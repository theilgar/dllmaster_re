"""🌐 Uzaq server paneli: səhifələr panel qaydalarına uyğundur, hər düymə işləyir, mətn daxiletməsi, bildiriş düymələri."""
import time
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus  # noqa: F401  (dövrü importları qaydaya salır)
import bot.menus.handlers.remote_flow as rf
import bot.menus.state as st
import bot.menus.views.remote_server as V
from bot.core import remote
from common.database import Settings

ACCOUNTS = [
    {"id": "main", "kind": "main", "name": "Əsas hesab"},
    {"id": "a1b2c3d4", "kind": "browser", "name": "Google hesabı 6"},
    {"id": "e5f6a7b8", "kind": "browser", "name": "Google hesabı 6"},
    {"id": "f0f0f0f0", "kind": "file", "name": "Fayl hesabı"},
]


class FakeSettings(Settings):
    def __init__(self):
        super().__init__("remote:", empty_is_default=True)
        self.d = {}

    def get(self, key, default=None):
        v = self.d.get(key)
        return default if v is None or v == "" else v

    def put(self, key, value):
        self.d[key] = str(value)


@pytest.fixture(autouse=True)
def store(monkeypatch):
    monkeypatch.setattr(remote, "_S", FakeSettings())
    remote._gate.update(ev=None, decision=None, waiting=0, notified_at=0.0, msg=None)
    remote._down.update(until=0.0, error="", at=0.0)
    remote.last_check.update(at=0.0, ok=False, error="", info={})
    remote.stats.update(fallback=0, ip_retry=0)
    remote.PENDING.clear()
    remote._inflight.clear()
    st.perf_inp.update(stage=None, key=None, chat_id=None, panel_id=None)
    from bot.core import yt_accounts
    monkeypatch.setattr(yt_accounts, "accounts", lambda: list(ACCOUNTS))
    monkeypatch.setattr(yt_accounts, "get", lambda i: next((a for a in ACCOUNTS if a["id"] == i), None))
    monkeypatch.setattr(rf, "is_creator", lambda u: u == 1)
    yield


def configure(**kw):
    """Hazır server: ünvan, açıq, uğurlu yoxlama (RAM 1936 MB)."""
    remote._S.put("host", "admin@203.0.113.5")
    remote._S.put("on", "1")
    remote.last_check.update(at=time.time(), ok=True, error="",
                             info={"ram_mb": 1936, "free_mb": 50000, "yt_dlp": "2026.8.19", "js": ["node"], "ffmpeg": True})
    for k, v in kw.items():
        remote._S.put(k, v)


def labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def is_text(label: str) -> bool:
    """Mətnli düymə (panel qaydası: sətirdə ən çox 2); ikon / rəqəm düymələri (➖ ➕ 🔄) sayılmır."""
    return any(ch.isalpha() for ch in label)


def pages():
    """Hər səhifə bir neçə vəziyyətdə: (ad, (mətn, klaviatura))."""
    out = [("hub-boş", V.remote_view()), ("bağlantı-boş", V.remote_conn_view()), ("slotlar-boş", V.remote_slots_view()),
           ("hesablar", V.remote_accounts_view()), ("yükləmə", V.remote_mode_view("download")),
           ("upload", V.remote_mode_view("upload")), ("giriş-ünvan", V.remote_input_view("host")),
           ("giriş-port", V.remote_input_view("port")), ("giriş-açar", V.remote_input_view("key", "⚠️ xəta"))]
    configure(procs="16", download_mode="both", upload_mode="server")
    remote.stats.update(fallback=3, ip_retry=2)
    remote.PENDING["/tmp/x.m4a"] = ("dl/x.m4a", 10)
    out += [("hub-işləyir", V.remote_view("not")), ("bağlantı-hazır", V.remote_conn_view()),
            ("slotlar-çox", V.remote_slots_view()), ("yükləmə-ikisi", V.remote_mode_view("download", "✓ ok"))]
    remote._gate["waiting"] = 2
    remote._down.update(until=time.time() + 30, error="uzaq işçi başlamadı")
    out.append(("hub-gözləyir", V.remote_view()))
    remote._S.put("auto", "1")
    out.append(("hub-avto", V.remote_view()))
    return out


def test_every_page_follows_panel_rules():
    for name, (text, kb) in pages():
        ds = datas(kb)
        assert len(ds) == len(set(ds)), f"{name}: təkrar callback"
        assert all(len(d.encode()) <= 64 for d in ds), f"{name}: callback 64 baytdan uzundur"
        assert len(text) <= 4000, name
        rows = kb.inline_keyboard
        nav = rows[-1]
        assert "menu:main" in [b.callback_data for b in nav] or name.startswith("giriş"), f"{name}: son sətir naviqasiyadır"
        for row in rows[:-1]:
            assert sum(is_text(b.text) for b in row) <= 2, f"{name}: sətirdə 2-dən çox mətnli düymə {[b.text for b in row]}"
            assert len(row) <= 5, name
        assert all(d.startswith("menu:rmt") or d in ("menu:main", "menu:cancel", "menu:perf") for d in ds), name


def test_hub_is_compact():
    """Əsas səhifə: ən çox 5 düymə sətri (+ qərar düymələri) — qalan ayarlar öz səhifələrində."""
    configure()
    _, kb = V.remote_view()
    assert len(kb.inline_keyboard) <= 5
    assert not any(d.endswith(("d+", "u+", "p+")) for d in datas(kb))


def test_hub_states():
    text, kb = V.remote_view()
    assert "Başlamaq üçün" in text and "🔌 Bağlantı" in " ".join(labels(kb))
    configure()
    text, kb = V.remote_view()
    assert "🟢 <b>İşləyir</b>" in text and "admin@203.0.113.5" in text and "RAM 1.9 GB" in text
    assert "🔁 Yenidən yoxla" not in labels(kb)
    remote._S.put("on", "0")
    text, _ = V.remote_view()
    assert "Söndürülüb" in text and "açanda işə düşəcək" in text
    remote._S.put("on", "1")
    remote._S.put("download_mode", "home")
    remote._S.put("upload_mode", "home")
    assert "server istifadə olunmur" in V.remote_view()[0]


def test_hub_when_server_down_offers_decision_buttons():
    configure()
    remote._gate["waiting"] = 4
    remote._down.update(until=time.time() + 30, error="ssh qoşulmadı")
    text, kb = V.remote_view()
    assert "🔴" in text and "4 iş server üçün gözləyir" in text
    assert datas(kb)[:3] == ["menu:rmt:fix:retry", "menu:rmt:fix:home", "menu:rmt:fix:auto"]
    assert kb.inline_keyboard[1][0].text == "🏠 Evə keç"


def test_upload_shows_home_when_download_is_home():
    configure(download_mode="home", upload_mode="server")
    text, _ = V.remote_view()
    assert "Upload: <b>🏠 Ev</b> <i>yükləmə evdə olduğundan</i>" in text
    assert "hazırda evdədir" in V.remote_mode_view("upload")[0]


def test_mode_pages_mark_current_choice():
    configure(download_mode="both")
    _, kb = V.remote_mode_view("download")
    by_data = {b.callback_data: b.text for row in kb.inline_keyboard for b in row}
    assert by_data["menu:rmt:dl:both"].startswith("✅")
    assert not by_data["menu:rmt:dl:home"].startswith("✅") and not by_data["menu:rmt:dl:server"].startswith("✅")
    assert {"menu:rmt:dl:home", "menu:rmt:dl:server", "menu:rmt:dl:both"} <= set(by_data)
    _, kb = V.remote_mode_view("upload")
    assert "menu:rmt:up:server" in datas(kb)


def test_slots_page_warns_and_recommends():
    configure(procs="16")
    text, kb = V.remote_slots_view()
    assert "⚠️" in text and "34" in text and "Tövsiyə: 📦 depo 4" in text
    assert "Ev" not in text and not any(d.startswith("menu:perf:pick") for d in datas(kb))    # ev slotları Performansdadır
    assert "menu:rmt:slots:rec" in datas(kb)
    remote._S.put("procs", "4")
    text, kb = V.remote_slots_view()
    assert "✅ Serverin yaddaşına uyğundur" in text and "menu:rmt:slots:rec" not in datas(kb)
    remote.last_check.update(info={})
    assert "RAM-ı bilinmir" in V.remote_slots_view()[0]


def test_conn_page_shows_setup_steps_until_first_success():
    text, _ = V.remote_conn_view()
    assert "Qurmaq üçün" in text and "daxil edilməyib" in text
    configure(key="/home/user/.ssh/dlm_vps")
    text, _ = V.remote_conn_view()
    assert "Qurmaq üçün" not in text and "uğurlu" in text and "yt-dlp 2026.8.19" in text and "dlm_vps" in text
    remote.last_check.update(info={"ram_mb": 1936, "js": []})
    assert "deno və ya node" in V.remote_conn_view()[0]
    remote.last_check.update(ok=False, error="ssh qoşulmadı")
    text, _ = V.remote_conn_view()
    assert "❌ ssh qoşulmadı" in text and "Əvvəlki uğurlu yoxlamadan: RAM 1.9 GB" in text
    assert "Qurmaq üçün" not in text                                  # server əvvəl qurulub, indi yalnız qoşulmur
    remote.last_check.update(info={})                                 # heç vaxt uğurla yoxlanmayıb
    text, _ = V.remote_conn_view()
    assert "❌ ssh qoşulmadı" in text and "Qurmaq üçün" in text and "Əvvəlki" not in text


def test_accounts_page_distinguishes_same_names():
    _, kb = V.remote_accounts_view()
    ls = labels(kb)
    assert any("Google hesabı 6 #a1b2" in t for t in ls) and any("Google hesabı 6 #e5f6" in t for t in ls)
    assert any(t.startswith("⭐ Əsas hesab") for t in ls)
    remote.toggle_acc("f0f0f0f0")
    assert any("Fayl hesabı · fayl 🟢" in t for t in labels(V.remote_accounts_view()[1]))


# ───────────────────────── düymələrin icrası ─────────────────────────
class Click:
    """menu:rmt:... düyməsinə basış: cavab (toast / alert) və göstərilən pəncərə."""

    def __init__(self, monkeypatch, user=1):
        self.shown = []
        self.user = user

        async def fake_edit(cb, text, kb=None):
            self.shown.append((text, kb))

        monkeypatch.setattr(rf, "safe_edit_cb", fake_edit)

    async def __call__(self, data: str):
        msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=7), message_id=9)
        cb = types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=self.user), message=msg, answer=AsyncMock())
        await rf._mcb_remote(cb, data.split(":"))
        self.cb = cb
        return cb

    @property
    def alert(self):
        a = self.cb.answer.await_args
        return a and a.kwargs.get("show_alert") and a.args[0]

    @property
    def toast(self):
        a = self.cb.answer.await_args
        return a and a.args and a.args[0]


@pytest.fixture
def click(monkeypatch):
    return Click(monkeypatch)


async def test_non_creator_cannot_change_anything(monkeypatch):
    c = Click(monkeypatch, user=999)
    configure()
    await c("menu:rmt:on")
    await c("menu:rmt:dl:home")
    assert remote.enabled() and remote.dl_mode() == "server" and not c.shown
    assert c.alert == "⛔ Yalnız bot sahibi"


async def test_master_switch_needs_address(click):
    await click("menu:rmt:on")
    assert "ünvanını daxil edin" in click.alert and not remote.enabled()
    configure()
    await click("menu:rmt:on")
    assert not remote.enabled() and "Söndürüldü" in click.toast
    await click("menu:rmt:on")
    assert remote.enabled() and "🌐 Uzaq server: 🟢 Aktiv" in labels(click.shown[-1][1])


async def test_mode_pickers_save_and_stay_on_page(click):
    configure()
    await click("menu:rmt:dl:both")
    assert remote.dl_mode() == "both" and click.toast == "✓ Yükləmə: 🏠+🌐 İkisi"
    assert any(t.startswith("✅ 🏠+🌐") for t in labels(click.shown[-1][1]))
    await click("menu:rmt:up:home")
    assert remote.up_mode() == "home" and "Upload" in click.toast
    await click("menu:rmt:up:nonsense")                       # yanlış dəyər: dəyişmir
    assert remote.up_mode() == "home"
    await click("menu:rmt:dl")                                # yalnız səhifəni açır
    assert remote.dl_mode() == "both" and "Mahnı harada yüklənsin" in click.shown[-1][0]


async def test_failure_policy_toggle_and_auto_releases_waiting_jobs(click):
    configure()
    remote._gate["ev"] = __import__("asyncio").Event()
    remote._gate["waiting"] = 1
    await click("menu:rmt:auto")
    assert remote.auto_fallback() and remote._gate["ev"].is_set() and remote._gate["decision"] == "home"
    assert "Avto" in click.toast
    await click("menu:rmt:auto")
    assert not remote.auto_fallback() and "Manual" in click.toast


async def test_slots_buttons_respect_bounds(click):
    configure(procs="16", user_procs="4")
    await click("menu:rmt:slots:d+")
    await click("menu:rmt:slots:u+")
    assert remote.depo_slots() == 16 and remote.user_slots() == 4            # yuxarı sərhəd
    await click("menu:rmt:slots:d-")
    assert remote.depo_slots() == 15 and "Depo: 15" in click.toast
    remote._S.put("procs", "1")
    await click("menu:rmt:slots:d-")
    assert remote.depo_slots() == 1                                          # aşağı sərhəd
    remote._S.put("procs", "16")
    remote._S.put("user_procs", "2")
    await click("menu:rmt:slots:rec")                                        # RAM 1936 və istifadəçi 2 → tövsiyə 4
    assert remote.depo_slots() == 4 and "Depo: 4" in click.toast
    await click("menu:rmt:slots:di")
    assert "doldurucunun" in click.alert


async def test_accounts_toggle(click):
    await click("menu:rmt:acc:a1b2c3d4")
    assert remote.acc_allowed("a1b2c3d4") and not remote.acc_allowed("e5f6a7b8")
    assert "serverdə işləyəcək" in click.shown[-1][0]
    await click("menu:rmt:acc:a1b2c3d4")
    assert not remote.acc_allowed("a1b2c3d4") and "yalnız evdə" in click.shown[-1][0]
    await click("menu:rmt:acc:yoxdur")                                       # naməlum hesab: heç nə olmur
    assert "Serverdə açıq: <b>0</b>" in click.shown[-1][0]


async def test_decision_buttons(click, monkeypatch):
    configure()
    ev = remote._gate["ev"] = __import__("asyncio").Event()
    await click("menu:rmt:fix:home")
    assert remote.dl_mode() == "home" and remote.up_mode() == "home" and ev.is_set() and remote._gate["decision"] == "home"
    assert "evə keçirildi" in click.shown[-1][0]
    remote._S.put("download_mode", "server")
    stub = AsyncMock(return_value={"ok": False, "error": "ssh <qoşulmadı>", "info": {}})
    monkeypatch.setattr(remote, "check", stub)
    await click("menu:rmt:fix:retry")
    assert "hələ cavab vermir" in click.shown[-1][0] and "&lt;qoşulmadı&gt;" in click.shown[-1][0]    # HTML təhlükəsiz
    stub.return_value = {"ok": True, "error": "", "info": {}}
    await click("menu:rmt:fix:retry")
    assert "Server cavab verdi" in click.shown[-1][0]
    await click("menu:rmt:fix:auto")
    assert remote.auto_fallback() and "Avto açıldı" in click.shown[-1][0]
    await click("menu:rmt:fix:wtf")                                          # naməlum əmr: dəyişiklik yoxdur
    assert click.cb.answer.await_count


async def test_test_and_setup_buttons(click, monkeypatch):
    await click("menu:rmt:test")
    assert "ünvanını daxil edin" in click.alert
    configure()
    check = AsyncMock(side_effect=lambda: remote.last_check.update(at=time.time(), ok=True) or {"ok": True})
    monkeypatch.setattr(remote, "check", check)
    await click("menu:rmt:test")
    assert check.await_count == 1 and len(click.shown) == 2                   # əvvəl «Yoxlanılır», sonra nəticə
    assert "Yoxlanılır" in click.shown[0][0] and "uğurlu" in click.shown[1][0]
    monkeypatch.setattr(remote, "setup", AsyncMock(return_value=["✅ python3: Python 3.13", "❌ pip <xətası>"]))
    await click("menu:rmt:setup")
    assert "Server qurulur" in click.shown[-2][0] and "pip &lt;xətası&gt;" in click.shown[-1][0]
    assert not rf._busy["setup"] and not rf._busy["test"]                     # yenidən basmaq olar


async def test_text_input_flow_keeps_prompt_on_error_and_saves_on_success(click, monkeypatch):
    await click("menu:rmt:edit:host")
    assert st.perf_inp["stage"] == "await_val" and st.perf_inp["key"] == "rmt_host" and st.perf_inp["panel_id"] == 9
    assert datas(click.shown[-1][1]) == ["menu:rmt:conn"] and "istifadəçi@ip" in click.shown[-1][0]
    edits = []
    bot = types.SimpleNamespace(edit_message_text=AsyncMock(side_effect=lambda **kw: edits.append(kw)))
    monkeypatch.setattr(rf, "get_context", lambda: types.SimpleNamespace(bot=bot))

    def msg(text):
        return types.SimpleNamespace(text=text, delete=AsyncMock())

    await rf.on_text(msg("-oProxyCommand=evil"), "host")                       # təhlükəli dəyər rədd olunur
    assert remote.host() == "" and st.perf_inp["stage"] == "await_val"        # ekran açıq qalır
    assert "⚠️" in edits[-1]["text"] and edits[-1]["message_id"] == 9
    await rf.on_text(msg("admin@203.0.113.5"), "host")
    assert remote.host() == "admin@203.0.113.5" and st.perf_inp["stage"] is None
    assert "Yadda saxlanıldı" in edits[-1]["text"] and "Qurmaq üçün" in edits[-1]["text"]
    await rf.on_text(msg("-"), "host")                                        # «-» silir
    assert remote.host() == ""
    await click("menu:rmt:edit:key")
    await rf.on_text(msg("/home/user/.ssh/dlm_vps.pub"), "key")                # açıq açar rədd olunur
    assert "açıq açardır" in edits[-1]["text"] and remote.key() == ""
    await click("menu:rmt:edit:port")
    await rf.on_text(msg("99999"), "port")
    assert "1–65535" in edits[-1]["text"]
    await rf.on_text(msg("2222"), "port")
    assert remote.port() == 2222
    await click("menu:rmt")                                                    # başqa səhifəyə keçid mətn gözləməsini ləğv edir
    assert st.perf_inp["stage"] is None


async def test_every_button_on_every_page_is_handled(click, monkeypatch):
    """Bütün səhifələrdəki hər menu:rmt düyməsinə basılır — xəta yoxdur, cavab (pəncərə və ya alert) var."""
    monkeypatch.setattr(remote, "check", AsyncMock(return_value={"ok": True, "error": "", "info": {}}))
    monkeypatch.setattr(remote, "setup", AsyncMock(return_value=["✅ hazır"]))
    seen = set()
    for name, (text, kb) in pages():
        for d in datas(kb):
            if not d.startswith("menu:rmt") or d in seen:
                continue
            seen.add(d)
            configure(procs="8")
            n_before = len(click.shown)
            await click(d)
            assert len(click.shown) > n_before or click.cb.answer.await_count, f"{name}: {d} cavabsız qaldı"
    assert len(seen) > 30
    st.perf_inp["stage"] = None


async def test_old_perf_rmt_callbacks_still_work(monkeypatch):
    """Köhnə mesajlardakı menu:perf:rmt:* düymələri yeni panelə yönləndirilir."""
    from bot.menus.handlers import performance_flow as pf
    shown = []

    async def fake_edit(cb, text, kb=None):
        shown.append(text)

    monkeypatch.setattr(rf, "safe_edit_cb", fake_edit)
    cb = types.SimpleNamespace(data="menu:perf:rmt:fix:home", from_user=types.SimpleNamespace(id=1),
                               message=types.SimpleNamespace(chat=types.SimpleNamespace(id=7), message_id=9), answer=AsyncMock())
    configure()
    await pf._mcb_perf(cb, cb.data.split(":"), "perf")
    assert remote.dl_mode() == "home" and "evə keçirildi" in shown[-1]


async def test_outage_notification_has_the_three_decision_buttons(monkeypatch):
    sent = []
    bot = types.SimpleNamespace(send_message=AsyncMock(side_effect=lambda *a, **kw: sent.append((a, kw))))
    monkeypatch.setattr(st, "get_context", lambda: types.SimpleNamespace(bot=bot, creator_id=1))
    configure()
    await remote._notify_outage("ssh <xətası>")
    (args, kw) = sent[0]
    assert args[0] == 1 and "&lt;xətası&gt;" in args[1] and "Manual" in args[1]
    cbs = datas(kw["reply_markup"])
    assert cbs == ["menu:rmt:fix:retry", "menu:rmt:fix:home", "menu:rmt:fix:auto"]
    await remote._notify_outage("yenə")                                        # 10 dəq. ərzində təkrar bildiriş yoxdur
    assert len(sent) == 1


def test_perf_hub_links_to_panel():
    from bot.menus.views import performance as P
    assert P._remote_icon() in ("⚪", "🟡", "🟢", "🔴")


async def test_menu_router_dispatches_rmt_callbacks(monkeypatch):
    """Əsl menu_callback: menu:rmt... düymələri uzaq server panelinə çatır, performans hub-ı isə ona keçid verir."""
    from bot.menus.handlers import menu_router as mr
    shown = []

    async def fake_edit(cb, text, kb=None):
        shown.append((text, kb))

    monkeypatch.setattr(rf, "safe_edit_cb", fake_edit)
    monkeypatch.setattr(mr, "stop_fetch_on", lambda m: None)
    configure()
    for data, needle in (("menu:rmt", "Uzaq server"), ("menu:rmt:dl", "Mahnı harada yüklənsin"),
                         ("menu:rmt:slots", "Slotlar"), ("menu:rmt:conn", "Bağlantı"), ("menu:rmt:acc", "Hesablar")):
        cb = types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=1), inline_message_id=None,
                                   message=types.SimpleNamespace(chat=types.SimpleNamespace(id=7), message_id=9),
                                   answer=AsyncMock())
        await mr.menu_callback(cb)
        assert needle in shown[-1][0], data
    assert len(shown) == 5
