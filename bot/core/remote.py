"""
🌐 Uzaq serverlər — bot/core/remote.py

Bot (menyu, baza, ffmpeg, Shazam...) evdə qalır; yükləmə (yt-dlp) və depoya upload istəsəniz bir və ya bir neçə uzaq serverdə
görülür. Nəqliyyat — adi SSH (əlavə proqram / port yoxdur): hər serverdə yalnız bot/core/ytdl_worker.py işləyir və evdəki işçi
proseslə eyni protokolla danışır (cpu_pool.RemoteProcPool). Bir serverin bağlantısı, sağlamlığı, faylları —
bot/core/remote_host.py (class Server); bu fayl serverlər arası siyasətdir.

İki sual, hər biri üçün üç cavab (bütün serverlərə ortaq):
  ⬇️ Yükləmə / ⬆️ Upload harada getsin?   🏠 ev · 🌐 server · 🏠+🌐 ikisi (serverlər doludursa evdə)
  🛟 Server xətası olsa nə olsun?          ✋ Manual (standart): iş evə keçmir, gözləyir, creator-a düymələrlə bildiriş gedir
                                           🤖 Avto: iş dərhal evdə görülür

Paylama: yeni iş ən az yüklü SAĞLAM serverə gedir (yük = işləyən / slot). Server xəta versə və ya YouTube onun IP-sini rədd etsə
iş başqa serverə keçir; heç biri qalmasa yuxarıdakı Manual / Avto qaydası işləyir. Hər serverin öz slotları, öz qovluğu və öz
Firefox girişi var.

Necə işləyir:
  ⬇️ Yükləmə — yt-dlp serverdə işləyir. Nəticə:
       • ev fayla toxunacaqsa (ffmpeg, istifadəçiyə göndərmə) — fayl ssh ilə evə çəkilir;
       • 📦 depo zolağında hazır .m4a-dırsa — fayl həmin serverdə QALIR, evdə boş yer-tutan fayl yaranır (PENDING).
  ⬆️ Upload — PENDING fayl onu saxlayan serverdən birbaşa Telegram-a yüklənir (ev internetini işlətmir).
  Server üçün uyğun olmayan işlər (brauzer cookies-i, localhost proxy) həmişə evdə görülür — bu xəta deyil.

Bölmələr: ayarlar → serverlər reyestri → seçim (claim) → vəziyyət → monitor izi → cookies → yükləmə → upload →
xəta qapısı (Manual) → try_download / try_upload (cpu_pool və audio_cache bunları çağırır).
Ayarlar bazada (remote:*). Menyu: /menu → 🖥 Sistem → ⚙️ Performans → 🌐 Uzaq server (bot/menus/views/remote_server.py).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid

from bot.core import remote_host as rh
from bot.core.remote_host import (  # noqa: F401  (başqa modullar və testlər `remote.X` kimi istifadə edir)
    DENO_INSTALL,
    MAX_DEPO_SLOTS,
    MAX_USER_SLOTS,
    PROVIDER_CMD,
    PROVIDER_INSTALL,
    PULL_SESSIONS,
    SHORT_SESSIONS,
    SPAWN_PARALLEL,
    RemoteLocalOnly,
    RemoteRetryHome,
    RemoteUnavailable,
    RemoteUploadError,
    Server,
)
from common.database import Settings

logger = logging.getLogger(__name__)

_S = Settings("remote:", empty_is_default=True)
ROOT = rh.ROOT
_IP_HINTS = ("sign in to confirm", "not a bot", "http error 403", "http error 429", "too many requests",
             "this content isn't available, try again later",
             "failed to extract any player response")                 # datacenter IP-si (AWS və s.): YouTube heç bir client-ə cavab vermir
IP_BLOCK_AFTER = 6                    # bir serverdə ardıcıl bu qədər IP rəddindən sonra creator-a xəbərdarlıq gedir
_INFRA_HINTS = ("no module named", "command not found", "no such file or directory: 'yt", "permission denied",
                "cannot allocate memory", "no space left")


# ───────────────────────── ümumi ayarlar ─────────────────────────
MODES = ("home", "server", "both")
MODE_LABEL = {"home": "🏠 Ev", "server": "🌐 Server", "both": "🏠+🌐 İkisi"}
MAX_SERVERS = 8


def enabled() -> bool:
    return _S.flag("on")


def set_enabled(on: bool) -> bool:
    """Ana açar. Söndürüləndə hər şey evdə görülür — server üçün gözləyən işlər də dərhal evə buraxılır."""
    _S.put("on", "1" if on else "0")
    if on:
        for s in servers():
            s.mark_up()
    else:
        resolve_gate("home")
    return on


def mode(kind: str) -> str:
    """kind: 'download' | 'upload' → 'home' | 'server' | 'both'."""
    v = _S.get(kind + "_mode")
    if v in MODES:
        return v
    return "server" if _S.flag(kind, "1") else "home"          # köhnə aç/bağla ayarından


def set_mode(kind: str, value: str) -> str:
    if kind not in ("download", "upload") or value not in MODES:
        raise ValueError(f"{kind}/{value}")
    _S.put(kind + "_mode", value)
    return value


def dl_mode() -> str:
    return mode("download")


def up_mode() -> str:
    return mode("upload")


def auto_fallback() -> bool:
    """🤖 Avto: serverlər xəta verəndə iş dərhal evdə görülür. ✋ Manual (standart): iş gözləyir, creator-a bildiriş gedir."""
    return _S.flag("auto", "0")


def set_auto_fallback(on: bool) -> bool:
    _S.put("auto", "1" if on else "0")
    return on


# ───────────────────────── serverlər reyestri ─────────────────────────
_servers: dict = {}                                   # id → Server (icra vəziyyəti dəyişikliklərdə qalır)
_dropped: list = []                                   # silinmiş serverlər: hovuzları bağlanmalıdır
_cfg_cache = {"raw": None, "cfgs": []}


def _legacy_cfg():
    """Köhnə tək-server ayarlarından (remote:host, port, key, ...) bir server çıxarır — köçürmə."""
    if not (_S.get("host") or rh.local_transport()):
        return []
    cfg = rh.default_cfg("s1", "Server 1")
    cfg.update(host=_S.get("host", "") or "", port=_S.int("port", 22, lo=1, hi=65535), key=_S.get("key", "") or "",
               dir=_S.get("dir", rh.DEFAULT_DIR) or rh.DEFAULT_DIR, py=_S.get("py", "python3") or "python3",
               slots=_S.int("procs", 2, lo=1, hi=rh.MAX_DEPO_SLOTS), user_slots=_S.int("user_procs", 2, lo=1, hi=rh.MAX_USER_SLOTS),
               firefox=_S.flag("server_firefox", "0"))
    return [cfg]


def _cfg_list() -> list:
    raw = _S.get("servers")
    if raw is None:
        return _legacy_cfg()
    if raw == _cfg_cache["raw"]:
        return _cfg_cache["cfgs"]
    try:
        lst = json.loads(raw)
    except ValueError:
        lst = []
    cfgs = [c for c in lst if isinstance(c, dict) and c.get("id")] if isinstance(lst, list) else []
    _cfg_cache.update(raw=raw, cfgs=cfgs)
    return cfgs


def _save():
    raw = json.dumps([s.to_cfg() for s in _servers.values()], ensure_ascii=False)
    _S.put("servers", raw)
    _cfg_cache.update(raw=raw, cfgs=[s.to_cfg() for s in _servers.values()])


def servers() -> list:
    """Bütün serverlər (siyahı sırası ilə). Konfiqurasiya bazadan oxunur, icra vəziyyəti saxlanılır."""
    cfgs = _cfg_list()
    ids = [c["id"] for c in cfgs]
    for c in cfgs:
        s = _servers.get(c["id"])
        if s is None:
            _servers[c["id"]] = Server(c, persist=_save)
        else:
            s.apply(c)
    for sid in [i for i in _servers if i not in ids]:
        _dropped.append(_servers.pop(sid))
    return [_servers[i] for i in ids if i in _servers]


def get_server(sid):
    servers()
    return _servers.get(sid)


def usable_servers() -> list:
    return [s for s in servers() if s.usable()]


def configured() -> bool:
    return any(s.configured() and s.on for s in servers())


def add_server(name: str = ""):
    """Yeni (boş) server əlavə edir; ünvanı menyudan daxil edilir."""
    cur = servers()
    if len(cur) >= MAX_SERVERS:
        raise ValueError(f"Ən çox {MAX_SERVERS} server ola bilər")
    taken = {s.id for s in cur} | {s.id for s in _dropped}
    sid = next(i for i in (uuid.uuid4().hex[:4] for _ in range(100)) if i not in taken)
    _servers.update({s.id: s for s in cur})                    # köhnə tək-server (legacy) də siyahıya yazılsın
    _servers[sid] = Server(rh.default_cfg(sid, name or f"Server {len(cur) + 1}"), persist=_save)
    _save()
    return _servers[sid]


async def remove_server(sid: str) -> bool:
    s = get_server(sid)
    if s is None:
        return False
    _servers.pop(sid, None)
    _save()
    for pool in s.pools():
        await pool.shutdown()
    resolve_gate("retry")                                       # bu serveri gözləyən işlər başqasını/ev qaydasını yoxlasın
    return True


def _field(sid: str):
    s = get_server(sid)
    if s is None:
        raise ValueError("Server tapılmadı")
    return s


def set_conn(sid: str, name: str, value) -> str:
    """Bir serverin bağlantı ayarı (name / host / port / key / dir). Yoxlanılır; səhv dəyərdə ValueError."""
    s = _field(sid)
    v = rh.validate(name, value)
    setattr(s, {"dir": "dir"}.get(name, name), v)
    s.mark_up()
    _servers.update({x.id: x for x in servers()})
    _save()
    return str(v)


def set_slots(sid: str, lane: str, n: int) -> int:
    s = _field(sid)
    if lane == "depo":
        s.depo_slots = max(1, min(rh.MAX_DEPO_SLOTS, int(n)))
        v = s.depo_slots
    elif lane == "user":
        s.user_slots = max(1, min(rh.MAX_USER_SLOTS, int(n)))
        v = s.user_slots
    else:
        raise ValueError(lane)
    _servers.update({x.id: x for x in servers()})
    _save()
    return v


def set_server_on(sid: str, on: bool) -> bool:
    s = _field(sid)
    s.on = bool(on)
    if s.on:
        s.mark_up()
    _servers.update({x.id: x for x in servers()})
    _save()
    return s.on


def set_server_firefox(sid: str, on: bool) -> bool:
    """🦊 Cookies-i evdən göndərmək əvəzinə bu serverdə OXU: serverin öz Firefox profilindəki YouTube girişi.
    Datacenter IP-sində evdən köçürülən sessiya işləmir, serverin öz IP-sində açılmış giriş isə işləyir."""
    s = _field(sid)
    s.firefox = bool(on)
    _servers.update({x.id: x for x in servers()})
    _save()
    return s.firefox


def all_pools() -> list:
    """Bütün serverlərin (silinmişlər də) işçi hovuzları — bağlanma üçün."""
    return [p for s in (*servers(), *_dropped) for p in s.pools()]


# ───────────────────────── seçim: iş hansı serverə getsin ─────────────────────────
stats = {"fallback": 0, "ip_retry": 0}            # fallback — serverlər alınmadı, iş evdə görüldü; ip_retry — IP rədd


def server_selected(kind: str) -> bool:
    """Ayarlarda server seçilib və ən azı bir server açıqdır (serverlərin hazırda sağlam olub-olmamasından asılı olmayaraq)."""
    return enabled() and configured() and mode(kind) != "home"


def available() -> bool:
    return enabled() and bool(usable_servers())


def _load_ratio(s: Server, kind: str, lane: str) -> float:
    return s.load(kind, lane) / max(1, s.limit(lane))


def claim(kind: str, lane: str = "depo", exclude=()):
    """İş hansı serverdə görülsün → Server (yük artırılır) və ya None.
    'server' — ən az yüklü sağlam server (hamısı doludursa da: iş onun növbəsinə düşür);
    'ikisi' — yalnız boş yeri olan serverlər, yoxdursa None (iş evdə görülür).
    Server qaytarılıbsa çağıran finally-də release(srv, kind, lane) etməlidir."""
    m = mode(kind)
    if m == "home":
        return None
    cands = [s for s in usable_servers() if s.id not in exclude]
    if m == "both":
        cands = [s for s in cands if s.load(kind, lane) < s.limit(lane)]
    if not cands:
        return None
    srv = min(cands, key=lambda s: _load_ratio(s, kind, lane))
    srv.inflight[(kind, lane)] = srv.load(kind, lane) + 1
    return srv


def claim_on(srv: Server, kind: str, lane: str = "depo") -> bool:
    """Konkret serverdə yer ayır (upload: fayl hansı serverdədirsə oradan getməlidir)."""
    if srv.load(kind, lane) >= srv.limit(lane) and mode(kind) == "both":
        return False
    srv.inflight[(kind, lane)] = srv.load(kind, lane) + 1
    return True


def release(srv: Server, kind: str, lane: str = "depo"):
    k = (kind, lane)
    srv.inflight[k] = max(0, srv.inflight.get(k, 0) - 1)


# ───────────────────────── vəziyyət ─────────────────────────
def status() -> tuple[str, str]:
    """(ikon, qısa mətn) — menyunun ümumi vəziyyət sətri."""
    if not enabled():
        return "⚪", "Söndürülüb, hər şey evdə"
    srvs = [s for s in servers()]
    if not srvs:
        return "🟡", "Server əlavə edilməyib"
    if not any(s.configured() for s in srvs):
        return "🟡", "Ünvan daxil edilməyib"
    live = [s for s in srvs if s.on and s.configured()]
    if not live:
        return "⚪", "Bütün serverlər söndürülüb"
    good = [s for s in live if s.usable() and (not s.last_check["at"] or s.last_check["ok"])]
    if len(live) == 1:
        return live[0].status()
    if not [s for s in live if s.usable()]:
        return "🔴", f"Bütün serverlər dayanıb ({len(live)})"
    if len(good) == len(live):
        return "🟢", f"{len(live)} server işləyir"
    return "🟡", f"{len(good)}/{len(live)} server işləyir"


def check_is_stale(max_age: float = 300.0) -> bool:
    return any(s.check_is_stale(max_age) for s in servers() if s.on and s.configured())


def waiting_count() -> int:
    return _gate["waiting"]


def ip_blocked() -> bool:
    return any(s.ip["streak"] >= IP_BLOCK_AFTER for s in servers())


def server_view(s: Server) -> dict:
    icon, text = s.status()
    return {"id": s.id, "name": s.name, "host": s.host, "port": s.port, "key": s.key, "dir": s.dir, "on": s.on,
            "configured": s.configured(), "icon": icon, "status": text, "firefox": s.firefox,
            "depo_slots": s.depo_slots, "user_slots": s.user_slots, "running": s.running(),
            "load": {"depo": s.load("download", "depo"), "user": s.load("download", "user"), "up": s.load("upload", "depo")},
            "check": dict(s.last_check), "info": s.last_check.get("info") or {}, "advice": s.slot_advice(),
            "ip_streak": s.ip["streak"], "ip_blocked": s.ip["streak"] >= IP_BLOCK_AFTER}


def snapshot() -> dict:
    """Menyu üçün bütün vəziyyət bir yerdə (görünüşlər yalnız bunu formatlayır)."""
    icon, text = status()
    srvs = [server_view(s) for s in servers()]
    running = {k: sum(s["running"][k] for s in srvs) for k in ("depo", "user", "up")}
    return {
        "icon": icon, "status": text, "enabled": enabled(), "configured": configured(), "servers": srvs,
        "n_live": sum(1 for s in servers() if s.usable()), "dl": mode("download"), "up": mode("upload"), "auto": auto_fallback(),
        "waiting": _gate["waiting"], "ip_blocked": ip_blocked(), "pending_files": len(PENDING), "running": running,
        "done": sum(p.stats["done"] for p in all_pools()),
        "fallback": stats["fallback"], "ip_retry": stats["ip_retry"],
    }


# ───────────────────────── 📊 depo monitoru üçün iz ─────────────────────────
counts = {"dl_S": 0, "dl_E": 0, "up_S": 0, "up_E": 0}      # S — server, E — ev (cəhd / göndərmə sayı)


def mark(kind: str, where: str):
    """Depo doldurucunun cari işçisi üçün qeyd: kind 'dl'|'up', where 'S' (server) | 'E' (ev).
    Monitorda işçi sətrində 'YER' sütunu və ümumi say kimi görünür. Doldurucu konteksti yoxdursa — yalnız say."""
    counts[f"{kind}_{where}"] += 1
    try:
        from bot.core.depo_helpers import UPLOAD_CTX
        ctx = UPLOAD_CTX.get()
        if ctx:
            filler, idx = ctx
            st = filler.wstate.get(idx)
            if st is not None:
                st.setdefault("via", {})[kind] = where
    except Exception as e:
        logger.debug("remote.mark xətası: %r", e)


def via_text(st: dict) -> str:
    v = (st or {}).get("via") or {}
    return f"{v.get('dl', '-')}/{v.get('up', '-')}"


# ───────────────────────── 🍪 cookies ─────────────────────────
PENDING = {}                           # yerli yer-tutan fayl (abspath) → (uzaq yol, ölçü, server id): fayl hələ serverdədir
_sizes = {}                            # yer-tutanın əsl ölçüsü (göndərildikdən sonra da) — statistikada 0 bayt görünməsin
COOKIE_TTL = 30 * 60                   # ixrac olunmuş fayl bu qədər saniyə təzə sayılır
COOKIE_DIR = os.path.join(ROOT, "data", "remote_cookies")
_cookie_locks = {}


def _loopback(url: str) -> bool:
    return bool(re.search(r"//(?:[^@/]*@)?(?:localhost|127\.|\[?::1)", url or ""))


def acc_allowed(acc_id: str) -> bool:
    return _S.flag(f"acc_{acc_id}", "0")


def toggle_acc(acc_id: str) -> bool:
    return _S.toggle(f"acc_{acc_id}", "0")


def _account_for(spec) -> dict | None:
    """yt-dlp 'cookiesfrombrowser' dəyərinə uyğun YouTube hesabı (hesab açarını tapmaq üçün)."""
    from bot.core import yt_accounts
    for acc in yt_accounts.accounts():
        try:
            if tuple(yt_accounts.opts(acc).get("cookiesfrombrowser") or ()) == tuple(spec):
                return acc
        except Exception as e:
            logger.debug("hesab uyğunlaşdırması: %r", e)
    return None


def _youtube_only(jar):
    """Cookie qabından yalnız youtube / google olanlar (başqa saytların cookies-i serverə getmir) → (yeni qab, say)."""
    from yt_dlp.cookies import YoutubeDLCookieJar
    out, n = YoutubeDLCookieJar(), 0
    for c in jar:
        d = (c.domain or "").lower()
        if "youtube" in d or "google" in d:
            out.set_cookie(c)
            n += 1
    return out, n


def _export_cookies(spec, path: str) -> int:
    """Brauzer cookies-indən YouTube / Google olanları fayla yazır (rejim 600) → cookie sayı."""
    from yt_dlp import YoutubeDL
    with YoutubeDL({"cookiesfrombrowser": tuple(spec), "quiet": True, "no_warnings": True}) as ydl:
        out, n = _youtube_only(ydl.cookiejar)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path, ignore_discard=True, ignore_expires=True)
    os.chmod(path, 0o600)
    return n


async def _browser_cookie_file(spec) -> str:
    acc = _account_for(spec)
    if acc is None or not acc_allowed(acc["id"]):
        raise RemoteLocalOnly("hesab serverdə istifadə üçün açılmayıb" if acc else "hesab tapılmadı")
    path = os.path.join(COOKIE_DIR, f"{acc['id']}.txt")
    lock = _cookie_locks.setdefault(acc["id"], asyncio.Lock())
    async with lock:
        fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < COOKIE_TTL
        if not fresh:
            try:
                n = await asyncio.to_thread(_export_cookies, spec, path)
            except Exception as e:
                raise RemoteLocalOnly(f"cookies ixrac olunmadı: {type(e).__name__}: {str(e)[:100]}")
            if not n:
                raise RemoteLocalOnly("brauzerdə YouTube cookies-i tapılmadı")
            logger.info(f"🍪 {acc['name']}: {n} cookie server üçün ixrac olundu")
    return path


async def prepare_opts(srv: Server, opts: dict) -> tuple[dict, str]:
    """Yerli yt-dlp opsiyalarını bu serverə uyğunlaşdırır → (opsiyalar, yerli qovluq). Alınmasa RemoteLocalOnly."""
    o = dict(opts)
    if (o.get("cookiesfrombrowser") or o.get("cookiefile")) and srv.firefox:
        o.pop("cookiefile", None)
        o["cookiesfrombrowser"] = ("firefox",)             # serverdə oxunur — evdən heç bir cookie göndərilmir
    elif o.get("cookiesfrombrowser"):
        o["cookiefile"] = await _browser_cookie_file(o.pop("cookiesfrombrowser"))     # → fayl, aşağıda serverə göndərilir
    if o.get("proxy") and _loopback(str(o["proxy"])):
        raise RemoteLocalOnly("proxy evin localhost-undadır")
    tmpl = o.get("outtmpl")
    if not isinstance(tmpl, str):
        raise RemoteLocalOnly("outtmpl xüsusi formatdadır")
    local_dir = os.path.dirname(tmpl) or "."
    o["outtmpl"] = "dl/" + os.path.basename(tmpl)
    for k in ("ffmpeg_location", "cachedir", "paths", "download_archive"):
        o.pop(k, None)
    if isinstance(o.get("js_runtimes"), dict):                 # yerli yollar serverdə yoxdur; yt-dlp Node 20-ni dəstəkləmir → Deno
        o["js_runtimes"] = {"deno": {}}
    if str(o.get("format", "")).startswith("bestaudio"):
        o["_dlm_audio_extract"] = True                         # server: 360p mp4-dən səs itkisiz .m4a-ya çıxarılır (az trafik)
    cf = o.get("cookiefile")
    if cf:
        try:
            st = os.stat(cf)
        except OSError:
            raise RemoteLocalOnly("cookies faylı tapılmadı")
        cached = srv.cookie_cache.get(cf)
        if not cached or cached[:2] != (st.st_mtime, st.st_size):
            rel = "cookies/" + hashlib.sha1(cf.encode()).hexdigest()[:16] + ".txt"
            with open(cf, "rb") as f:
                await srv.push_bytes(f.read(), rel)
            cached = srv.cookie_cache[cf] = (st.st_mtime, st.st_size, rel)
        o["cookiefile"] = cached[2]
    return o, local_dir


# ───────────────────────── ⬇️ yükləmə ─────────────────────────
async def _mirror_progress(srv: Server, rel_prog: str, local_prog: str):
    """Serverdəki irəliləyiş faylını evdəki .prog-a köçürür → cpu_pool-un mövcud poller-i göstərir."""
    last = None
    while True:
        await asyncio.sleep(3.0)                              # çoxlu yükləmə: hər biri ortaq bağlantıya sorğu göndərir
        try:
            rc, out, _ = await srv.run(f"cat {rel_prog} 2>/dev/null", 10)
        except RemoteUnavailable:
            continue
        if rc or not out or out == last:
            continue
        last = out
        try:
            tmp = local_prog + ".t"
            with open(tmp, "wb") as f:
                f.write(out)
            os.replace(tmp, local_prog)
        except OSError:
            pass


async def _push_cancel(srv: Server, event, rel_cancel: str):
    while not event.is_set():
        await asyncio.sleep(0.3)
    await srv.touch(rel_cancel)


def _is_ip_rejection(msg: str) -> bool:
    m = (msg or "").lower()
    return any(h in m for h in _IP_HINTS)


def _is_infra(msg: str) -> bool:
    m = (msg or "").lower()
    return any(h in m for h in _INFRA_HINTS)


def via_tag(exc) -> str:
    """Xəta serverdə baş veribsə loq sətrinə əlavə olunan işarə (evdəki xətadan ayırd etmək üçün)."""
    name = getattr(exc, "via", None)
    return f" · 🌐 {name}" if name else ""


def _ip_rejected(srv: Server):
    """Bu serverin IP-si rədd edildi. Ardıcıl IP_BLOCK_AFTER dəfə olarsa creator-a düymələrlə bir dəfə xəbər verilir."""
    b = srv.ip
    b["streak"] += 1
    if b["streak"] == IP_BLOCK_AFTER and time.time() - b["notified_at"] > 1800:
        b["notified_at"] = time.time()
        asyncio.ensure_future(_notify_ip_block(srv))


async def download(srv: Server, args: tuple, timeout: float, cancel_event, local_cancel_path: str, lane: str) -> dict:
    """ytdl_download-un uzaq variantı (bu serverdə) → {"path", "info"}. Alınmasa RemoteUnavailable / RemoteRetryHome."""
    from bot.core import cpu_pool as cp
    url, opts = args[0], args[1]
    ropts, local_dir = await prepare_opts(srv, opts)
    pool = srv.pool("user" if lane == "user" else "depo")           # 👤 / 📦 ayrı slotlar
    rid = uuid.uuid4().hex[:10]
    rcancel = f"dl/.c_{rid}"
    mirror = asyncio.ensure_future(_mirror_progress(srv, rcancel + ".prog", local_cancel_path + ".prog")) \
        if local_cancel_path else None
    canceller = asyncio.ensure_future(_push_cancel(srv, cancel_event, rcancel)) if cancel_event is not None else None
    try:
        res = await pool.call("ytdl_download", (url, ropts, rcancel), timeout, cancel_event, None)
    except cp.WorkerCrashed as e:
        srv.mark_down(e)
        raise RemoteUnavailable(str(e))
    except cp.WorkerError as e:
        e.via = srv.name                                      # loqda «🌐 <server>» işarəsi: xəta evdən yox, serverdən gəlib
        if _is_infra(str(e)):
            srv.mark_down(e)
            raise RemoteUnavailable(str(e))
        if _is_ip_rejection(str(e)):
            stats["ip_retry"] += 1
            _ip_rejected(srv)
            rej = RemoteRetryHome(f"«{srv.name}» serverinin IP-si rədd edildi: {str(e)[:90]}")
            rej.original = e                                  # ✋ Manual rejimdə evə keçmir — əsl xəta yuxarı qalxır
            raise rej
        raise
    finally:
        for t in (mirror, canceller):
            if t:
                t.cancel()
        asyncio.ensure_future(srv.rm(rcancel, rcancel + ".prog", rcancel + ".prog.t"))
    srv.mark_up()
    srv.ip["streak"] = 0                                      # server YouTube-dan yüklədi — IP rədd zənciri kəsildi
    rpath = res["path"]
    lpath = os.path.join(local_dir, os.path.basename(rpath))
    size = await srv.remote_size(rpath)
    if size < 0:
        raise RemoteUnavailable(f"yüklənən fayl serverdə tapılmadı: {rpath}")
    if lane == "depo" and mode("download") != "home" and mode("upload") != "home" and rpath.lower().endswith(".m4a"):
        os.makedirs(local_dir, exist_ok=True)
        open(lpath, "wb").close()                          # yer-tutan: hələ serverdədir (upload oradan gedəcək)
        PENDING[os.path.abspath(lpath)] = (rpath, size, srv.id)
        _sizes[os.path.abspath(lpath)] = size
        while len(_sizes) > 2000:                          # yaddaş sızmasın: ən köhnələr atılır
            _sizes.pop(next(iter(_sizes)))
        return {"path": lpath, "info": res["info"]}
    for attempt in (1, 2):
        try:
            await srv.pull_file(rpath, lpath, size)
            break
        except RemoteUnavailable:
            if attempt == 2:
                raise
    await srv.rm(rpath)
    return {"path": lpath, "info": res["info"]}


# ───────────────────────── ⬆️ upload ─────────────────────────
def file_size(path) -> int:
    """Faylın ölçüsü; serverdə qalmış (yer-tutan) fayl üçün əsl ölçü. Fayl yoxdursa OSError (os.path.getsize kimi)."""
    n = os.path.getsize(path)
    return n or _sizes.get(os.path.abspath(path), 0)


def pending(path) -> bool:
    return isinstance(path, str) and os.path.abspath(path) in PENDING


def pending_server(path):
    """Fayl hansı serverdə qalıb → Server və ya None (server silinib / fayl serverdə deyil)."""
    ent = PENDING.get(os.path.abspath(path)) if isinstance(path, str) else None
    return get_server(ent[2]) if ent else None


async def materialize(path: str) -> bool:
    """Fayl hələ serverdədirsə — evə çəkir (yer-tutanın yerinə). Çəkilməsə yer-tutan silinir, False."""
    if not isinstance(path, str):
        return True
    ap = os.path.abspath(path)
    ent = PENDING.get(ap)
    if not ent:
        return True
    rpath, size, sid = ent
    srv = get_server(sid)
    try:
        if srv is None:
            raise RemoteUnavailable("fayl saxlanılan server silinib")
        await srv.pull_file(rpath, ap, size)
    except RemoteUnavailable as e:
        logger.warning(f"🌐 Fayl serverdən çəkilmədi: {e}")
        rh._silent_rm(ap)
        PENDING.pop(ap, None)
        return False
    PENDING.pop(ap, None)
    await srv.rm(rpath)
    return True


async def discard(path: str):
    """Fayl artıq lazım deyil (göndərildi / ləğv) — serverdəki nüsxə silinir. Evdəki yer-tutanı çağıran silir."""
    ent = PENDING.pop(os.path.abspath(path), None)
    srv = get_server(ent[2]) if ent else None
    if srv is not None:
        await srv.rm(ent[0])


async def upload_audio(srv: Server, path: str, token: str, chat_id: int, thread_id, filename: str, title: str,
                       performer: str, duration: int, caption: str, thumb: bytes = None):
    """Serverdəki faylı həmin serverdən Telegram-a yükləyir → aiogram Message. Telegram xətası — RemoteUploadError,
    server xətası — RemoteUnavailable (çağıran evdə davam edir)."""
    from aiogram.types import Message
    from bot.core import cpu_pool as cp
    rpath = PENDING[os.path.abspath(path)][0]
    try:
        d = await srv.pool("up").call("upload_audio", (token, chat_id, thread_id, rpath, filename, title, performer,
                                                       duration, caption, thumb), 900)
    except cp.WorkerCrashed as e:
        srv.mark_down(e)
        raise RemoteUnavailable(str(e))
    except cp.WorkerError as e:
        if _is_infra(str(e)) or "uzaq fayl tapılmadı" in str(e):
            srv.mark_down(e)
            raise RemoteUnavailable(str(e))
        raise RemoteUploadError(str(e))
    srv.mark_up()
    return Message.model_validate(d)


# ───────────────────────── ✋ Manual rejim: xəta qapısı ─────────────────────────
GATE_TIMEOUT = 900                     # gözləyən iş bu qədər saniyədən sonra xəta ilə bitir (depo təkrar növbəsinə düşür)
PROBE_EVERY = 20                       # serverlər bərpa olunubmu — bu intervalla yoxlanır
_gate = {"ev": None, "decision": None, "waiting": 0, "notified_at": 0.0, "msg": None}


def resolve_gate(decision: str):
    """Gözləyən işləri azad edir: 'retry' — yenidən serverə, 'home' — evdə işlət."""
    ev = _gate["ev"]
    if ev is not None and not ev.is_set():
        _gate["decision"] = decision
        ev.set()


async def check_all() -> list:
    """Bütün açıq serverləri paralel yoxlayır → [(Server, nəticə), ...]."""
    need = mode("upload") != "home"
    live = [s for s in servers() if s.on and s.configured()]
    res = await asyncio.gather(*(s.check(need) for s in live), return_exceptions=True)
    out = []
    for s, r in zip(live, res):
        out.append((s, r if isinstance(r, dict) else {"ok": False, "error": str(r), "info": {}}))
    return out


async def _send_to_creator(text: str, rows: list):
    """Creator-a düymələrlə bildiriş. rows: [[(mətn, callback), ...], ...]"""
    try:
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M
        from bot.menus.state import get_context
        ctx = get_context()
        kb = M(inline_keyboard=[[B(text=t, callback_data=d) for t, d in row] for row in rows])
        return await ctx.bot.send_message(ctx.creator_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.warning(f"🌐 Server bildirişi göndərilmədi: {type(e).__name__}: {e}")
        return None


async def _notify_outage(err: str):
    """Creator-a bildiriş + düymələr: 🔁 yenidən yoxla · 🏠 evə keç · 🤖 avto."""
    if time.time() - _gate["notified_at"] < 600:              # tez-tez dəyişən xətada spam olmasın
        return None
    _gate["notified_at"] = time.time()
    from html import escape
    live = [s for s in servers() if s.on and s.configured()]
    which = f"Bütün uzaq serverlər ({len(live)})" if len(live) > 1 else "Uzaq server"
    return await _send_to_creator(
        f"🔴 <b>{which} cavab vermir</b>\n<code>{escape(str(err)[:200])}</code>\n\n"
        f"⬇️ Yükləmə: <b>{MODE_LABEL[dl_mode()]}</b> · ⬆️ Upload: <b>{MODE_LABEL[up_mode()]}</b>\n"
        f"Xəta rejimi <b>✋ Manual</b> olduğundan iş evə keçmir, gözləyir.\n\nNə edək?",
        [[("🔁 Yenidən yoxla", "menu:rmt:fix:retry")],
         [("🏠 Evə keç", "menu:rmt:fix:home"), ("🤖 Avto", "menu:rmt:fix:auto")]])


async def _notify_ip_block(srv: Server):
    """Bu serverin IP-si YouTube tərəfindən ardıcıl rədd edilir — qərar creator-dadır."""
    from html import escape
    others = len(usable_servers()) - (1 if srv.usable() else 0)
    return await _send_to_creator(
        f"🟠 <b>«{escape(srv.name)}» serverinin IP-si YouTube tərəfindən rədd edilir</b>\n"
        f"Son <b>{srv.ip['streak']}</b> yükləmə ardıcıl alınmadı (<code>Failed to extract any player response</code>, "
        f"403 və s.). Bu hesab və ya cookies problemi deyil: datacenter IP-lərini (AWS və s.) YouTube bloklayır. "
        f"Serverin öz Firefox girişi (🍪 Hesablar / server səhifəsi) kömək edə bilər.\n\n"
        + (f"İşlər avtomatik digər serverlərə ({others}) yönləndirilir.\n\n" if others > 0 else "")
        + "🏠 <b>Evə keç</b> — yükləmə evdə gedir, serverlər dayanır\n"
        "🤖 <b>Avto</b> — hamısı rədd edəndə iş evdə təkrarlanır",
        [[("🏠 Evə keç", "menu:rmt:fix:home"), ("🤖 Avto", "menu:rmt:fix:auto")]])


async def _outage_loop(ev, err: str):
    """Bildiriş göndərir, sonra serverlər bərpa olunana (və ya qərar verilənə) qədər yoxlayır."""
    msg = await _notify_outage(err)
    deadline = time.monotonic() + 1800
    while not ev.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(PROBE_EVERY)
        if ev.is_set():
            return
        if any(r["ok"] for _, r in await check_all()):
            resolve_gate("retry")
            if msg is not None:
                try:
                    await msg.edit_text("✅ <b>Uzaq server bərpa olundu</b> — gözləyən işlər davam edir.", parse_mode="HTML")
                except Exception as e:
                    logger.debug("bildiriş yenilənmədi: %r", e)
            return


async def wait_decision(kind: str, err, cancel_event=None, timeout: float = None) -> str:
    """✋ Manual: serverlər alınmadı — iş burada gözləyir. → 'retry' (yenidən serverə) | 'home' (evdə işlət).
    Gözləmə bitərsə RemoteUnavailable; ⏹ basılarsa DownloadCancelled."""
    timeout = timeout or GATE_TIMEOUT
    g = _gate
    if g["ev"] is None or g["ev"].is_set():
        g["ev"], g["decision"] = asyncio.Event(), None
        asyncio.ensure_future(_outage_loop(g["ev"], str(err)))
    ev, t0 = g["ev"], time.monotonic()
    g["waiting"] += 1
    try:
        while True:
            try:
                await asyncio.wait_for(ev.wait(), 1.0)
                return g["decision"] or "retry"
            except asyncio.TimeoutError:
                if cancel_event is not None and cancel_event.is_set():
                    from yt_dlp.utils import DownloadCancelled
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                if time.monotonic() - t0 > timeout:
                    raise RemoteUnavailable(f"uzaq server əlçatmaz — {int(timeout)} san. gözlənildi: {str(err)[:80]}")
    finally:
        g["waiting"] = max(0, g["waiting"] - 1)


async def apply_fix(action: str) -> str:
    """Bildiriş və menyudakı qərar düymələrinin icrası → istifadəçiyə nəticə mətni (HTML)."""
    if action == "retry":
        results = await check_all()
        if any(r["ok"] for _, r in results):
            resolve_gate("retry")
            return "✅ Server cavab verdi — gözləyən işlər davam edir."
        from html import escape
        err = next((r["error"] for _, r in results if r.get("error")), "serverlər cavab vermir")
        return f"❌ Server hələ cavab vermir: {escape(err[:200])}"
    if action in ("home", "auto"):
        for s in servers():
            s.ip["streak"] = 0
        resolve_gate("home")
        if action == "home":
            set_mode("download", "home")
            set_mode("upload", "home")
            return "🏠 Yükləmə və upload evə keçirildi, gözləyən işlər evdə görülür. Geri qaytarmaq üçün aşağıdan seçin."
        set_auto_fallback(True)
        return "🤖 Avto açıldı: gözləyən işlər indi evdə görülür, bundan sonra serverlər xəta verəndə iş avtomatik evə keçir."
    return "?"


def _stopping() -> bool:
    from bot.core import cpu_pool as cp
    return bool(getattr(cp, "_STOPPING", False))


# ───────────────────────── try_download / try_upload ─────────────────────────
async def try_download(args: tuple, timeout: float, cancel_event, local_cancel_path: str, lane: str):
    """Yükləməni serverlərdən birində görməyə çalışır. → nəticə, və ya None (bu iş evdə görülməlidir).
    Server xəta versə / IP-si rədd edilsə iş başqa serverə keçir. Hamısı alınmasa — 🤖 Avto: dərhal None;
    ✋ Manual: xəta olarsa gözləyir (bildiriş + düymələr), IP rəddində əsl xəta yuxarı qalxır."""
    tried: set = set()
    last_rejection = None
    while True:
        if not server_selected("download"):
            return None
        healthy = usable_servers()
        if not healthy:                                       # bütün serverlər dayanıb
            if auto_fallback():
                stats["fallback"] += 1
                return None
            err = next((s.down_info()[1] for s in servers() if s.down_info()[1]), "") or "serverlər dayanıb"
            if await wait_decision("download", err, cancel_event) == "home":
                return None
            tried.clear()
            continue
        if all(s.id in tried for s in healthy):               # sağlam serverlərin hamısı bu işi rədd etdi (IP)
            if auto_fallback() or last_rejection is None:
                if last_rejection is not None:
                    logger.info(f"🌐 Bu yükləmə evdə: {last_rejection}")
                stats["fallback"] += 1
                return None
            raise getattr(last_rejection, "original", last_rejection)   # ✋ evə keçmir — əsl xəta olduğu kimi
        srv = claim("download", lane, exclude=tried)
        if srv is None:
            return None                                       # 🏠+🌐 ikisi: serverlər dolu → artıq iş evdə
        mark("dl", "S")
        try:
            return await download(srv, args, timeout, cancel_event, local_cancel_path, lane)
        except RemoteRetryHome as e:
            tried.add(srv.id)
            last_rejection = e
            logger.info(f"🌐 {e} — başqa server yoxlanılır" if len(healthy) > 1 else f"🌐 {e}")
        except RemoteLocalOnly as e:
            logger.debug(f"🌐 Bu yükləmə evdə: {e}")        # serverlər üçün uyğun deyil (brauzer cookies-i və s.)
            return None
        except RemoteUnavailable as e:
            if _stopping():
                raise asyncio.CancelledError("bot dayanır") from e
            tried.add(srv.id)
            if not srv.down_info()[0]:                        # pull / fayl xətası: server yenə də şübhəlidir
                srv.mark_down(e)
            logger.warning(f"🌐 «{srv.name}» yükləməni alınmadı: {e}")
        finally:
            release(srv, "download", lane)


async def try_upload(path: str, token: str, chat_id: int, thread_id, filename: str, title: str, performer: str,
                     duration: int, caption: str, thumb: bytes = None):
    """Serverdəki faylı onu saxlayan serverdən göndərməyə çalışır. → aiogram Message, və ya None (fayl evə çəkilib evdən
    getməlidir). Telegram xətası — RemoteUploadError."""
    while True:
        srv = pending_server(path)
        if srv is None or not server_selected("upload"):
            return None
        if not (srv.on and srv.configured()):                 # server istifadəçi tərəfindən söndürülüb — fayl evə çəkilsin
            return None
        if not srv.usable():
            if auto_fallback():
                stats["fallback"] += 1
                return None
            if await wait_decision("upload", srv.down_info()[1] or f"«{srv.name}» dayanıb") == "home":
                return None
            continue
        if not claim_on(srv, "upload"):
            return None
        try:
            msg = await upload_audio(srv, path, token, chat_id, thread_id, filename, title, performer, duration, caption, thumb)
            mark("up", "S")
            return msg
        except RemoteUnavailable as e:
            if auto_fallback():
                stats["fallback"] += 1
                logger.info(f"🌐 Uzaq upload alınmadı ({e}) — fayl evə çəkilir")
                return None
            if await wait_decision("upload", e) == "home":
                return None
        finally:
            release(srv, "upload")
