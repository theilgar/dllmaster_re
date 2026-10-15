"""🌐 Uzaq server: rejimlər, slotlar, bağlantı yoxlamaları, cookies süzgəci və serverlə real zəncir.

Real zəncir (DLM_REMOTE_TRANSPORT=local): ssh əvəzinə yerli bash, "server" = tmp qovluq, yt-dlp yerli HTTP serverdən
kiçik .m4a yükləyir. Telegram və ya real server lazım deyil.
"""
import asyncio
import functools
import hashlib
import http.server
import os
import sys
import threading
import time
from unittest.mock import AsyncMock

import pytest

import bot.menus  # noqa: F401
from bot.core import cpu_pool as cp
from bot.core import remote
from bot.core.depo_helpers import UPLOAD_CTX
from tests.test_remote_panel import FakeSettings, store  # noqa: F401  (autouse: saxta ayarlar + sıfırlanmış vəziyyət)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ───────────────────────── saf məntiq ─────────────────────────
def test_modes_and_legacy_flags():
    assert remote.dl_mode() == "server"                         # standart: köhnə «aç» bayrağı (1)
    remote._S.put("download", "0")
    assert remote.dl_mode() == "home"                           # köhnə «bağla» bayrağı
    remote.set_mode("download", "both")
    assert remote.dl_mode() == "both" and remote.up_mode() == "server"
    with pytest.raises(ValueError):
        remote.set_mode("download", "çox")
    with pytest.raises(ValueError):
        remote.set_mode("başqa", "home")


def test_claim_respects_mode_and_lane_limits():
    remote._S.put("procs", "2")
    remote._S.put("user_procs", "1")
    remote.set_mode("download", "server")
    assert all(remote.claim("download", "depo") for _ in range(5))      # «server»: həmişə (növbə serverdə)
    remote._inflight.clear()
    remote.set_mode("download", "both")
    assert [remote.claim("download", "depo") for _ in range(3)] == [True, True, False]    # depo slotu 2
    assert [remote.claim("download", "user") for _ in range(2)] == [True, False]          # istifadəçi slotu 1, ayrıdır
    remote.release("download", "depo")
    assert remote.claim("download", "depo") is True
    remote.set_mode("download", "home")
    assert remote.claim("download", "user") is False


def test_slots_are_clamped_and_advice_uses_server_ram():
    assert remote.set_slots("depo", 99) == 16 and remote.set_slots("depo", 0) == 1
    assert remote.set_slots("user", 99) == 4 and remote.set_slots("user", -3) == 1
    with pytest.raises(ValueError):
        remote.set_slots("başqa", 1)
    remote._S.put("procs", "16")
    remote._S.put("user_procs", "2")
    assert remote.worst_case_procs() == 34
    assert remote.slot_advice()["ram_mb"] is None and not remote.slot_advice()["warn"]
    remote.last_check.update(info={"ram_mb": 1936})
    adv = remote.slot_advice()
    assert adv["warn"] and adv["rec_depo"] == 4 and adv["need_mb"] == 3400
    remote._S.put("procs", "4")
    assert not remote.slot_advice()["warn"]
    remote.last_check.update(info={"ram_mb": 16000})
    remote._S.put("procs", "16")
    assert not remote.slot_advice()["warn"]


def test_connection_values_are_validated(tmp_path):
    for bad in ("-oProxyCommand=evil", "a b@c", "x;rm -rf /", "$(id)@h"):
        with pytest.raises(ValueError):
            remote.set_conn("host", bad)
    assert remote.set_conn("host", "root@203.0.113.5") == "root@203.0.113.5"
    assert remote.set_conn("host", "") == "" and remote.host() == ""
    for bad in ("0", "70000", "abc", "-1", "22; ls"):
        with pytest.raises(ValueError):
            remote.set_conn("port", bad)
    assert remote.set_conn("port", "2222") == "2222" and remote.port() == 2222
    priv = tmp_path / "id_x"
    priv.write_text("PRIVATE")
    with pytest.raises(ValueError, match="açıq açardır"):
        remote.set_conn("key", str(priv) + ".pub")
    with pytest.raises(ValueError, match="tapılmadı"):
        remote.set_conn("key", str(tmp_path / "yoxdur"))
    assert remote.set_conn("key", str(priv)) == str(priv)
    for bad in ("../x", "-r", "a\nb"):
        with pytest.raises(ValueError):
            remote.set_conn("dir", bad)
    with pytest.raises(ValueError):
        remote.set_conn("py", "python")                         # yalnız host / port / key / dir dəyişir


def test_status_line_reflects_every_state():
    assert remote.status()[0] == "⚪"
    remote._S.put("on", "1")
    assert remote.status()[0] == "🟡" and "Ünvan" in remote.status()[1]
    remote._S.put("host", "a@b.c")
    assert remote.status() == ("🟢", "Aktiv, hələ yoxlanmayıb")
    remote.last_check.update(at=time.time(), ok=True)
    assert remote.status() == ("🟢", "İşləyir")
    remote.last_check.update(ok=False, error="yt-dlp serverdə yoxdur")
    assert remote.status() == ("🟡", "yt-dlp serverdə yoxdur")
    remote.mark_down("ssh qoşulmadı")
    icon, text = remote.status()
    assert icon == "🔴" and "ssh qoşulmadı" in text
    remote.mark_up()
    assert remote.status()[0] == "🟡"
    assert remote.check_is_stale() is False and remote.last_check.update(at=time.time() - 400) is None
    assert remote.check_is_stale() is True


def test_only_youtube_and_google_cookies_leave_the_house():
    from http.cookiejar import Cookie
    from yt_dlp.cookies import YoutubeDLCookieJar

    def c(domain, name):
        return Cookie(0, name, "v", None, False, domain, True, True, "/", True, True, None, False, None, None, {})

    jar = YoutubeDLCookieJar()
    for d, n in ((".youtube.com", "SID"), (".google.com", "NID"), ("accounts.google.com", "X"), ("music.youtube.com", "Y"),
                 (".facebook.com", "fb"), (".github.com", "gh"), ("bank.example", "secret"), (".googlevideo.com", "gv")):
        jar.set_cookie(c(d, n))
    out, n = remote._youtube_only(jar)
    domains = sorted(x.domain for x in out)
    assert n == 5 and domains == [".google.com", ".googlevideo.com", ".youtube.com", "accounts.google.com", "music.youtube.com"]
    assert not any("facebook" in d or "github" in d or "bank" in d for d in domains)


async def test_prepare_opts_adapts_paths_and_refuses_unsupported_cases(tmp_path, monkeypatch):
    pushed = []
    monkeypatch.setattr(remote, "push_bytes", AsyncMock(side_effect=lambda data, rel, *a: pushed.append((rel, data))))
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tvalue\n")
    base = {"format": "bestaudio", "outtmpl": str(tmp_path / "download" / "x.%(ext)s"), "ffmpeg_location": "/usr/bin",
            "js_runtimes": {"deno": {"path": "/home/user/.deno/bin/deno"}}, "cookiefile": str(jar), "proxy": "socks5h://1.2.3.4:9050"}
    o, local_dir = await remote.prepare_opts(base)
    assert o["outtmpl"] == "dl/x.%(ext)s" and local_dir == str(tmp_path / "download")
    assert "ffmpeg_location" not in o and o["js_runtimes"] == {"deno": {}}          # yerli yollar serverə getmir
    assert o["cookiefile"].startswith("cookies/") and pushed and b"SID" in pushed[0][1]
    pushed.clear()
    await remote.prepare_opts(base)
    assert not pushed                                                              # dəyişməyib: yenidən göndərilmir
    with pytest.raises(remote.RemoteLocalOnly):
        await remote.prepare_opts({**base, "proxy": "socks5h://127.0.0.1:9050"})   # evin localhost proxy-si
    with pytest.raises(remote.RemoteLocalOnly):
        await remote.prepare_opts({**base, "outtmpl": {"default": "x"}})
    monkeypatch.setattr(remote, "_account_for", lambda spec: None)                    # naməlum brauzer profili
    with pytest.raises(remote.RemoteLocalOnly, match="hesab tapılmadı"):
        await remote.prepare_opts({**base, "cookiefile": None, "cookiesfrombrowser": ("chrome", str(tmp_path), "BASICTEXT")})


async def test_wrong_account_never_exports_browser_cookies(monkeypatch, tmp_path):
    """Hesab serverdə açılmayıbsa brauzer cookies-i oxunmur belə."""
    calls = []
    monkeypatch.setattr(remote, "_export_cookies", lambda spec, path: calls.append(spec) or 3)
    monkeypatch.setattr(remote, "_account_for", lambda spec: {"id": "a1b2c3d4", "name": "G3"})
    with pytest.raises(remote.RemoteLocalOnly, match="açılmayıb"):
        await remote._browser_cookie_file(("chrome",))
    assert not calls
    remote.toggle_acc("a1b2c3d4")
    monkeypatch.setattr(remote, "COOKIE_DIR", str(tmp_path))
    path = await remote._browser_cookie_file(("chrome",))
    assert calls == [("chrome",)] and path.endswith("a1b2c3d4.txt")


# ───────────────────────── serverlə real zəncir ─────────────────────────
PAYLOAD = os.urandom(200_000)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/deny.m4a":
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/slow.m4a":                      # depo tapşırığı uzun çəksin (zolaq ayrılığı sınağı)
            time.sleep(0.8)
            self.path = "/s.m4a"
        try:
            super().do_GET()
        except (ConnectionResetError, BrokenPipeError):
            pass


@pytest.fixture
async def srv(tmp_path, monkeypatch):
    www = tmp_path / "www"
    www.mkdir()
    (www / "s.m4a").write_bytes(PAYLOAD)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(www)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("DLM_REMOTE_TRANSPORT", "local")
    monkeypatch.setenv("DLM_REMOTE_DIR", str(tmp_path / "srv"))
    monkeypatch.setattr(remote, "PROBE_EVERY", 0.2)
    monkeypatch.setattr(cp.RemoteProcPool, "SPAWN_BACKOFF", 0)          # sınıq serverdə təkrar cəhd gözləməsi testi yubatmasın
    monkeypatch.setattr(remote, "_notify_outage", AsyncMock(return_value=None))
    monkeypatch.setattr(remote, "_synced", {"hash": None})
    monkeypatch.setattr(remote, "_sync_lock", None)
    remote._S.put("on", "1")
    remote._S.put("py", sys.executable)
    remote._S.put("procs", "2")
    remote._S.put("user_procs", "2")
    cp._STOPPING = False
    for pool in (*cp.REMOTE_LANES.values(), cp.REMOTE_UP):                    # hovuz statistikası testlər arasında yığılmasın
        pool.stats.update(done=0, err=0, crash=0, fallback=0, spawned=0, secs=0.0)
    holder = types_ns(url=f"http://127.0.0.1:{httpd.server_port}/s.m4a", deny=f"http://127.0.0.1:{httpd.server_port}/deny.m4a",
                      slow=f"http://127.0.0.1:{httpd.server_port}/slow.m4a",
                      out=lambda n: {"format": "best", "outtmpl": str(tmp_path / "download" / f"{n}.%(ext)s"), "quiet": True},
                      srv_dir=tmp_path / "srv")
    await remote.ensure_synced(force=True)
    yield holder
    resolve = remote.resolve_gate
    resolve("home")
    await cp.shutdown_pools()
    httpd.shutdown()


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def break_server():
    remote._S.put("py", "/nonexistent/python")
    remote._down["until"] = 0.0


def fix_server():
    remote._S.put("py", sys.executable)


async def until(cond, timeout=15.0):
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "gözlənilən vəziyyət alınmadı"
        await asyncio.sleep(0.05)


def read(res) -> bytes:
    with open(res["path"], "rb") as f:
        return f.read()


async def test_download_runs_on_server_and_file_comes_home(srv):
    res = await cp.ytdl_download(srv.url, srv.out("a"))
    assert sha(read(res)) == sha(PAYLOAD)
    assert cp.REMOTE_LANES["user"].stats["done"] == 1 and cp.REMOTE_LANES["depo"].stats["done"] == 0
    left = [f for f in os.listdir(srv.srv_dir / "dl") if not f.startswith(".")]
    assert left == []                                                # serverdə qalıq yoxdur
    assert remote.counts["dl_S"] >= 1


async def test_depo_file_stays_on_server_until_needed(srv):
    tok = UPLOAD_CTX.set((object(), 0))
    try:
        res = await cp.ytdl_download(srv.url, srv.out("b"))
    finally:
        UPLOAD_CTX.reset(tok)
    assert remote.pending(res["path"]) and os.path.getsize(res["path"]) == 0       # evdə yalnız yer-tutan
    assert remote.file_size(res["path"]) == len(PAYLOAD)                           # amma statistika əsl ölçünü görür
    await remote.discard(res["path"])                                              # göndərildikdən sonra da
    assert remote.file_size(res["path"]) == len(PAYLOAD) and not remote.pending(res["path"])
    tok = UPLOAD_CTX.set((object(), 0))
    try:
        res = await cp.ytdl_download(srv.url, srv.out("b2"))
    finally:
        UPLOAD_CTX.reset(tok)
    assert await remote.materialize(res["path"])
    assert sha(read(res)) == sha(PAYLOAD) and not remote.pending(res["path"])


async def test_user_lane_is_not_blocked_by_depo_queue(srv):
    remote._S.put("procs", "1")
    await cp.ytdl_download(srv.url, srv.out("warm"))                       # istifadəçi işçisi hazır olsun
    tok = UPLOAD_CTX.set((object(), 0))
    depo = [asyncio.ensure_future(cp.ytdl_download(srv.slow, srv.out(f"d{i}"))) for i in range(2)]
    UPLOAD_CTX.reset(tok)
    await asyncio.sleep(0.4)
    t0 = time.monotonic()
    await cp.ytdl_download(srv.url, srv.out("u"))
    took = time.monotonic() - t0
    assert sum(1 for t in depo if not t.done()) == 2, "depo hələ gedirdi — istifadəçi onu gözləməməlidir"
    assert took < 2.0, f"istifadəçi {took:.1f} san. gözlədi"
    await asyncio.gather(*depo)
    assert cp.REMOTE_LANES["user"].stats["done"] == 2 and cp.REMOTE_LANES["depo"].stats["done"] == 2


async def test_both_mode_sends_only_free_slots_to_server(srv):
    remote.set_mode("download", "both")
    remote._S.put("user_procs", "1")
    served = []
    orig = remote.download

    async def spy(*a, **k):
        served.append(1)
        return await orig(*a, **k)

    remote.download = spy
    try:
        rs = await asyncio.gather(*[cp.ytdl_download(srv.url, srv.out(f"m{i}")) for i in range(3)])
    finally:
        remote.download = orig
    assert all(sha(read(r)) == sha(PAYLOAD) for r in rs)
    assert len(served) == 1 and not any(remote._inflight.values())


async def test_manual_mode_waits_instead_of_going_home_then_obeys_home_button(srv):
    break_server()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("w")))
    await until(lambda: remote.waiting_count() == 1)
    await asyncio.sleep(0.3)
    assert not t.done() and not os.path.exists(srv.out("w")["outtmpl"].replace("%(ext)s", "m4a")), "gözləyən iş evdə yüklənməməlidir"
    assert remote.status()[0] == "🔴"
    await remote.apply_fix("home")
    res = await asyncio.wait_for(t, 20)
    assert sha(read(res)) == sha(PAYLOAD) and remote.dl_mode() == "home" and remote.up_mode() == "home"
    assert remote.waiting_count() == 0


async def test_manual_mode_resumes_on_server_when_it_recovers(srv):
    break_server()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("r")))
    await until(lambda: remote.waiting_count() == 1)
    fix_server()                                                    # server özü düzəldi — 20 san.-lik yoxlama (testdə 0.2)
    res = await asyncio.wait_for(t, 20)
    assert sha(read(res)) == sha(PAYLOAD) and remote.dl_mode() == "server"
    assert remote.stats["fallback"] == 0 and cp.REMOTE_LANES["user"].stats["done"] == 1     # ev istifadə olunmadı


async def test_retry_button_only_releases_when_server_really_answers(srv):
    break_server()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("c")))
    await until(lambda: remote.waiting_count() == 1)
    assert "hələ cavab vermir" in await remote.apply_fix("retry") and not t.done()
    fix_server()
    assert "Server cavab verdi" in await remote.apply_fix("retry")
    assert sha(read(await asyncio.wait_for(t, 20))) == sha(PAYLOAD)


async def test_auto_button_releases_waiting_jobs_to_home(srv):
    break_server()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("x")))
    await until(lambda: remote.waiting_count() == 1)
    assert "Avto açıldı" in await remote.apply_fix("auto") and remote.auto_fallback()
    assert sha(read(await asyncio.wait_for(t, 20))) == sha(PAYLOAD)


async def test_auto_mode_goes_home_immediately_without_notification(srv):
    remote.set_auto_fallback(True)
    break_server()
    res = await asyncio.wait_for(cp.ytdl_download(srv.url, srv.out("e")), 20)
    assert sha(read(res)) == sha(PAYLOAD) and remote.waiting_count() == 0
    assert remote.stats["fallback"] >= 1 and not remote._notify_outage.await_count


async def test_waiting_job_ends_with_error_after_timeout(srv, monkeypatch):
    monkeypatch.setattr(remote, "GATE_TIMEOUT", 1.5)
    break_server()
    with pytest.raises(remote.RemoteUnavailable, match="san. gözlənildi"):
        await cp.ytdl_download(srv.url, srv.out("t"))
    assert remote.waiting_count() == 0


async def test_stop_button_cancels_a_waiting_job(srv):
    from yt_dlp.utils import DownloadCancelled
    break_server()
    ev = threading.Event()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("s"), cancel_event=ev))
    await until(lambda: remote.waiting_count() == 1)
    ev.set()
    with pytest.raises(DownloadCancelled):
        await asyncio.wait_for(t, 10)
    assert remote.waiting_count() == 0


async def test_ip_rejection_manual_keeps_error_auto_retries_home(srv):
    with pytest.raises(Exception, match="403"):
        await cp.ytdl_download(srv.deny, srv.out("h1"))
    assert remote.stats["ip_retry"] == 1 and remote.stats["fallback"] == 0       # Manual: evə keçmədi
    assert remote.down_info()[0] == 0                                           # server «dayanıb» sayılmadı
    remote.set_auto_fallback(True)
    with pytest.raises(Exception, match="403"):                                  # Avto: ev də eyni 403-ü alır
        await cp.ytdl_download(srv.deny, srv.out("h2"))
    assert remote.stats["ip_retry"] == 2
    assert cp.REMOTE_LANES["user"].stats["err"] == 2


async def test_upload_waits_for_decision_then_goes_home(srv):
    tok = UPLOAD_CTX.set((object(), 0))
    try:
        res = await cp.ytdl_download(srv.url, srv.out("u1"))
    finally:
        UPLOAD_CTX.reset(tok)
    assert remote.pending(res["path"])
    break_server()
    t = asyncio.ensure_future(remote.try_upload(res["path"], "1:x", 1, None, "a.m4a", "t", "p", 1, "c"))
    await until(lambda: remote.waiting_count() == 1)
    assert not t.done()
    await remote.apply_fix("home")
    assert await asyncio.wait_for(t, 10) is None                                 # fayl evdən göndəriləcək
    fix_server()
    remote.set_mode("download", "server")
    remote.set_mode("upload", "server")
    assert await remote.materialize(res["path"]) and sha(read(res)) == sha(PAYLOAD)


async def test_upload_telegram_error_is_not_mistaken_for_server_failure(srv):
    tok = UPLOAD_CTX.set((object(), 0))
    try:
        res = await cp.ytdl_download(srv.url, srv.out("u2"))
    finally:
        UPLOAD_CTX.reset(tok)
    with pytest.raises(remote.RemoteUploadError):                                # saxta token → Telegram «Unauthorized»
        await remote.try_upload(res["path"], "1:bad", 1, None, "a.m4a", "t", "p", 1, "c")
    assert remote.down_info()[0] == 0 and remote.waiting_count() == 0
    await remote.discard(res["path"])


async def test_parallel_worker_sync_does_not_race(srv):
    """16 işçi eyni anda açılanda ytdl_worker.py yazısı bir-birinin müvəqqəti faylını götürməməlidir."""
    rs = await asyncio.gather(*[remote.ensure_synced(force=(i % 2 == 0)) for i in range(32)], return_exceptions=True)
    assert not [r for r in rs if isinstance(r, Exception)]
    rs = await asyncio.gather(*[remote.push_bytes(b"x" * 5000 + str(i).encode(), "cookies/c.txt") for i in range(32)],
                              return_exceptions=True)
    assert not [r for r in rs if isinstance(r, Exception)]
    assert not [f for f in os.listdir(srv.srv_dir / "cookies") if f.endswith(".part")]


async def test_master_switch_off_means_home_and_on_brings_the_rules_back(srv):
    remote.set_enabled(False)
    res = await cp.ytdl_download(srv.url, srv.out("off"))
    assert sha(read(res)) == sha(PAYLOAD)
    assert cp.REMOTE_LANES["user"].stats["done"] == 0 and not cp.REMOTE_LANES["user"].workers      # serverə toxunulmadı
    assert not remote.server_selected("download") and not remote.server_selected("upload")
    remote.set_enabled(True)                                          # açılan kimi qaydalar işə düşür: restart lazım deyil
    res = await cp.ytdl_download(srv.url, srv.out("on"))
    assert sha(read(res)) == sha(PAYLOAD) and cp.REMOTE_LANES["user"].stats["done"] == 1
    remote.set_mode("download", "home")                               # rejim «ev» — açar açıq olsa da ev
    await cp.ytdl_download(srv.url, srv.out("home"))
    assert cp.REMOTE_LANES["user"].stats["done"] == 1
    remote.set_mode("download", "server")
    remote.set_enabled(False)
    tok = UPLOAD_CTX.set((object(), 0))                               # depo: açar söndürülübsə fayl serverdə qalmır
    try:
        res = await cp.ytdl_download(srv.url, srv.out("depo-off"))
    finally:
        UPLOAD_CTX.reset(tok)
    assert not remote.pending(res["path"]) and sha(read(res)) == sha(PAYLOAD)


async def test_turning_the_master_switch_off_releases_jobs_waiting_for_the_server(srv):
    break_server()
    t = asyncio.ensure_future(cp.ytdl_download(srv.url, srv.out("w2")))
    await until(lambda: remote.waiting_count() == 1)
    remote.set_enabled(False)                                         # 15 dəq. gözləmə əvəzinə dərhal evdə
    res = await asyncio.wait_for(t, 20)
    assert sha(read(res)) == sha(PAYLOAD) and remote.waiting_count() == 0


async def test_file_left_on_the_server_still_comes_home_after_the_master_switch_is_turned_off(srv):
    tok = UPLOAD_CTX.set((object(), 0))
    try:
        res = await cp.ytdl_download(srv.url, srv.out("left"))
    finally:
        UPLOAD_CTX.reset(tok)
    assert remote.pending(res["path"])
    remote.set_enabled(False)
    assert await remote.try_upload(res["path"], "1:x", 1, None, "a.m4a", "t", "p", 1, "c") is None      # serverdən göndərilmir
    assert await remote.materialize(res["path"]) and sha(read(res)) == sha(PAYLOAD)                  # fayl evə çəkilir, itmir


# ───────────────────────── SSH sessiya limiti (sshd MaxSessions=10) ─────────────────────────
def test_short_commands_share_one_connection_but_workers_get_their_own(monkeypatch):
    monkeypatch.delenv("DLM_REMOTE_TRANSPORT", raising=False)
    remote._S.put("host", "admin@203.0.113.5")
    short = remote._argv("echo ok")
    assert "ControlMaster=auto" in short and any(a.startswith("ControlPath=/") for a in short)
    worker = remote.worker_argv(0)
    assert "ControlMaster=no" in worker and "ControlPath=none" in worker and "ControlMaster=auto" not in worker
    assert worker[-2] == "admin@203.0.113.5" and worker[-3] == "--"             # host seçim kimi oxunmur
    assert remote.SHORT_SESSIONS + remote.PULL_SESSIONS < 10                    # ortaq bağlantı sshd limitindən aşağı


async def test_concurrent_ssh_sessions_never_exceed_the_limit(monkeypatch):
    live = {"now": 0, "max": 0}

    class FakeProc:
        returncode = 0

        async def communicate(self, data=None):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            await asyncio.sleep(0.02)
            live["now"] -= 1
            return b"ok", b""

    async def fake_exec(*a, **k):
        return FakeProc()

    monkeypatch.setattr(remote.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("DLM_REMOTE_TRANSPORT", "local")
    rs = await asyncio.gather(*[remote.run("echo ok", 5) for _ in range(40)])
    assert all(r[0] == 0 for r in rs)
    assert 1 < live["max"] <= remote.SHORT_SESSIONS                              # 40 sorğu — eyni anda ən çox 4


async def test_worker_connections_open_in_small_groups_and_retry_on_failure(monkeypatch):
    monkeypatch.setattr(cp.RemoteProcPool, "SPAWN_BACKOFF", 0)
    live = {"now": 0, "max": 0}
    tries = {"n": 0}

    class Pool(cp.RemoteProcPool):
        async def _spawn_once(self, remote_mod):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            try:
                await asyncio.sleep(0.02)
                tries["n"] += 1
                if tries["n"] <= 2:                                              # ilk iki cəhd: kex_exchange_identification
                    raise cp.WorkerCrashed("kex_exchange_identification: Connection closed by remote host")
                return "worker"
            finally:
                live["now"] -= 1

    pool = Pool("t", "t", 4)
    assert await pool._spawn() == "worker" and tries["n"] == 3                  # 3-cü cəhd uğurlu
    tries["n"] = 0
    rs = await asyncio.gather(*[Pool("t", "t", 4)._spawn() for _ in range(12)], return_exceptions=True)
    assert live["max"] <= remote.SPAWN_PARALLEL
    assert all(r == "worker" or isinstance(r, cp.WorkerCrashed) for r in rs)

    class Dead(cp.RemoteProcPool):
        async def _spawn_once(self, remote_mod):
            raise cp.WorkerCrashed("ssh qoşulmadı")

    with pytest.raises(cp.WorkerCrashed, match="ssh qoşulmadı"):                 # 3 cəhddən sonra xəta yuxarı qalxır
        await Dead("d", "d", 1)._spawn()
