"""🔑 YouTube hesabları — yt-dlp üçün bir neçə hesab (cookies), rollar və blokda avtomatik keçid.

Hesab növləri:
  ⭐ main    — əsas: /menu → 🖥 Sistem → 🌐 Brauzer / cookies ayarı (köhnə davranış), həmişə var
  📄 file    — bota göndərilmiş cookies.txt (data/yt_accounts/<id>.txt, yalnız sahib oxuya bilər)
  🌐 browser — serverdəki brauzerin AYRI profili (məs. Firefox -P ilə yaradılıb, 2-ci hesaba girilib)

Rollar (hər hesab üçün ayrıca): 📦 depo — depo doldurucu · 🎵 user — istifadəçi yükləmələri / axtarış.
pick(lane) — bu zolaq üçün rolu olan, aktiv, bloklanmamış İLK hesab (siyahı sırası = prioritet).
YouTube hesabı rate-limit edəndə block(id) — həmin hesab COOLDOWN müddətinə kənara çəkilir və yükləmə
növbəti hesabla davam edir; heç biri qalmayanda cookiesiz üsullar sınanır (köhnə davranış).

Parolla giriş yoxdur: Google avtomatik girişi bloklayır, yt-dlp-nin YouTube OAuth-u da artıq işləmir.
"""
import json
import logging
import os
import shutil
import signal
import subprocess
import time
import uuid
from bot.core.webprofile import LOGIN_COOKIES, scan_cookie_jar
from common.database import Settings
from common.utilities import spawn
from common.kv import next_of

logger = logging.getLogger(__name__)

ACC_DIR = os.path.join("data", "yt_accounts")
PROFILES_DIR = os.path.join("data", "yt_profiles")      # 🖥 server girişi üçün ayrıca Chrome profilləri
LOGIN_URL = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"
CHROME_BINS = (("google-chrome-stable", "chrome"), ("google-chrome", "chrome"), ("chromium", "chromium"),
               ("chromium-browser", "chromium"), ("brave", "brave"), ("brave-browser", "brave"))
S_ACCOUNTS, S_MAIN = "yt:accounts", "yt:acc_main"
S_MODE, S_PIN = "yt:acc_mode", "yt:acc_pin_"          # rotasiya rejimi · ✋ manual rejimdə zolağın hesabı
MODES = {
    "auto": "⚡ Avtomatik",      # prioritet sırası; limitdə növbəti hesaba keçir
    "rr": "🔄 Növbəli",          # hər yükləmə növbəti hesabla (yük paylanır); bloklu hesab ötürülür
    "manual": "✋ Manual",       # zolağın hesabını özün seçirsən; limitdə keçid yoxdur — cookiesiz + xəbər
}
ROLES = {"depo": "📦 Depo", "user": "🎵 İstifadəçi"}
KINDS = {"main": "⭐ Əsas", "file": "📄 cookies.txt", "browser": "🌐 Brauzer profili"}
BLOCK_SECONDS = int(os.getenv("YT_COOKIE_BLOCK", "3600"))
MAX_COOKIE_FILE = 2 * 1024 * 1024

_blocks = {}          # hesab id → time.monotonic() bitmə anı
_stats = {}           # hesab id → {"ok": n, "fail": n, "last": ts, "reason": ""}
_rr = {"depo": 0, "user": 0}   # 🔄 növbəli rejimin sayğacı (zolaq üzrə)
notify_hook = None    # async fn(mətn) — bot sahibinə xəbər (menu_plugin təyin edir)
LAST_TEST = {}        # hesab id → son 🧪 test nəticəsi
_login = {}           # hesab id → server ekranında açılmış Chrome prosesi (🖥 giriş)


# ───────────────────────── saxlanma ─────────────────────────
_S = Settings(empty_is_default=True)
_get, _set = _S.get, _S.put


def _main() -> dict:
    try:
        m = json.loads(_get(S_MAIN, "{}"))
    except Exception:
        m = {}
    return {"id": "main", "kind": "main", "name": m.get("name") or "Əsas hesab",
            "roles": [r for r in m.get("roles", ["depo", "user"]) if r in ROLES], "on": m.get("on", True)}


def accounts() -> list:
    """Bütün hesablar — siyahı sırası prioritetdir (⭐ əsas həmişə birinci)."""
    try:
        extra = [a for a in json.loads(_get(S_ACCOUNTS, "[]")) if isinstance(a, dict) and a.get("id")]
    except Exception:
        extra = []
    return [_main()] + extra


def get(acc_id: str) -> dict | None:
    return next((a for a in accounts() if a["id"] == acc_id), None)


def _save(accs: list):
    main = next((a for a in accs if a["id"] == "main"), None)
    if main:
        _set(S_MAIN, json.dumps({"name": main["name"], "roles": main["roles"], "on": main["on"]}))
    _set(S_ACCOUNTS, json.dumps([a for a in accs if a["id"] != "main"], ensure_ascii=False))


def update(acc_id: str, **fields) -> dict | None:
    accs = accounts()
    for a in accs:
        if a["id"] == acc_id:
            a.update(fields)
            _save(accs)
            return a
    return None


def toggle_role(acc_id: str, role: str) -> bool:
    a = get(acc_id)
    if not a or role not in ROLES:
        return False
    roles = [r for r in a["roles"] if r != role] if role in a["roles"] else a["roles"] + [role]
    update(acc_id, roles=roles)
    return role in roles


def toggle_on(acc_id: str) -> bool:
    a = get(acc_id)
    if not a:
        return False
    update(acc_id, on=not a.get("on", True))
    return not a.get("on", True)


def move_up(acc_id: str):
    """Prioriteti bir pillə qaldır (⭐ əsasdan yuxarı keçmir)."""
    accs = accounts()
    i = next((k for k, a in enumerate(accs) if a["id"] == acc_id), -1)
    if i > 1:
        accs[i - 1], accs[i] = accs[i], accs[i - 1]
        _save(accs)


def remove(acc_id: str) -> bool:
    if acc_id == "main":
        return False
    accs = accounts()
    acc = next((a for a in accs if a["id"] == acc_id), None)
    if not acc:
        return False
    if acc.get("kind") == "file" and acc.get("file"):
        try:
            os.remove(acc["file"])
        except OSError:
            pass
    if acc.get("managed") and acc.get("profile"):            # 🖥 server girişinin öz Chrome profili
        pid = _profile_pid(acc["profile"])
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        _stop_login(acc_id, timeout=10)
        shutil.rmtree(acc["profile"], ignore_errors=True)
    _save([a for a in accs if a["id"] != acc_id])
    _blocks.pop(acc_id, None)
    return True


# ───────────────────────── cookies.txt ─────────────────────────
def parse_cookie_file(data: bytes) -> dict:
    """Netscape cookies.txt yoxlaması → {"yt": n, "logged_in": bool, "total": n}. Uyğun deyilsə ValueError."""
    if len(data) > MAX_COOKIE_FILE:
        raise ValueError("fayl çox böyükdür (cookies.txt adətən 100 KB-dan kiçik olur)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("mətn faylı deyil")
    total = yt = 0
    logged_in = False
    now = time.time()
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        total += 1
        domain, _, _, _, expires, name, _ = parts
        if "youtube.com" in domain or "google.com" in domain:
            yt += "youtube.com" in domain
            exp = int(expires) if expires.strip().isdigit() else 0
            if name in LOGIN_COOKIES and (not exp or exp > now):
                logged_in = True
    if not total:
        raise ValueError("Netscape formatında cookie tapılmadı — «Get cookies.txt LOCALLY» uzantısı ilə çıxar")
    if not yt:
        raise ValueError("youtube.com cookie-si yoxdur — faylı youtube.com açıq olan tabda çıxar")
    return {"total": total, "yt": yt, "logged_in": logged_in}


def add_file(data: bytes, name: str = "", replace_id: str = "") -> tuple[dict, dict]:
    """Yeni hesab (və ya replace_id-in faylını yenilə) → (hesab, yoxlama). Uyğun deyilsə ValueError."""
    info = parse_cookie_file(data)
    if not info["logged_in"]:
        raise ValueError("girişli cookie (SID / SAPISID) yoxdur — brauzerdə həmin hesaba girib yenidən çıxar")
    os.makedirs(ACC_DIR, exist_ok=True)
    os.chmod(ACC_DIR, 0o700)
    acc = get(replace_id) if replace_id else None
    acc_id = acc["id"] if acc and acc["kind"] == "file" else uuid.uuid4().hex[:8]
    path = os.path.abspath(os.path.join(ACC_DIR, f"{acc_id}.txt"))   # yt-dlp işçi prosesləri üçün tam yol
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    if acc and acc["kind"] == "file":
        acc = update(acc_id, file=path, on=True)
    else:
        n = len(accounts())
        acc = {"id": acc_id, "kind": "file", "name": (name or f"Hesab {n + 1}")[:40], "file": path,
               "roles": ["depo"], "on": True, "added": int(time.time())}
        _save(accounts() + [acc])
    _blocks.pop(acc_id, None)
    LAST_TEST.pop(acc_id, None)
    logger.info(f"🔑 YouTube hesabı (cookies.txt) əlavə olundu / yeniləndi: {acc['name']} · {info['yt']} YouTube cookie")
    return acc, info


def add_browser(browser: str, profile: str, name: str = "", keyring: str = "") -> dict:
    from bot.core import webprofile as wp
    if browser not in wp.BROWSERS:
        raise ValueError("naməlum brauzer")
    n = len(accounts())
    acc = {"id": uuid.uuid4().hex[:8], "kind": "browser", "name": (name or f"Hesab {n + 1}")[:40],
           "browser": browser, "profile": profile or "", "keyring": keyring or "",
           "roles": ["depo"], "on": True, "added": int(time.time())}
    _save(accounts() + [acc])
    logger.info(f"🔑 YouTube hesabı (brauzer profili) əlavə olundu: {acc['name']} · {browser}")
    return acc


# ───────────────────────── seçim və blok ─────────────────────────
def block_left(acc_id: str) -> int:
    return max(0, int(_blocks.get(acc_id, 0) - time.monotonic()))


def block(acc_id: str, reason: str = "", seconds: int = BLOCK_SECONDS):
    if not acc_id:
        return
    if not block_left(acc_id):
        a = get(acc_id)
        logger.warning(f"🔑 YouTube hesabı bloklandı: {a['name'] if a else acc_id} ({reason}) — "
                       f"{seconds // 60} dəq. kənarda" + ("" if mode() == "manual" else ", növbəti hesaba keçilir"))
        if a and notify_hook is not None:
            _blocks[acc_id] = time.monotonic() + seconds          # xəbər mətnində yeni vəziyyət görünsün
            _notify_block(a, reason, seconds)
    _blocks[acc_id] = max(_blocks.get(acc_id, 0), time.monotonic() + seconds)
    st = _stats.setdefault(acc_id, {"ok": 0, "fail": 0, "last": 0, "reason": ""})
    st["reason"] = reason


def unblock(acc_id: str = None):
    if acc_id:
        _blocks.pop(acc_id, None)
    else:
        _blocks.clear()


def usable(acc: dict) -> bool:
    if acc["kind"] == "file":
        return bool(acc.get("file")) and os.path.isfile(acc["file"])
    if acc["kind"] == "main":
        from bot.core import webprofile as wp
        return wp.effective_source() != "none"
    return True


def role_of(lane: str) -> str:
    return "depo" if lane == "depo" else "user"


def mode() -> str:
    m = _get(S_MODE, "auto")
    return m if m in MODES else "auto"


def cycle_mode() -> str:
    keys = list(MODES)
    nxt = next_of(keys, mode())
    _set(S_MODE, nxt)
    return nxt


def pinned(lane: str) -> dict | None:
    """✋ Manual: zolaq üçün seçilmiş hesab (seçilməyibsə — rolu olan ilk hesab)."""
    role = role_of(lane)
    a = get(_get(S_PIN + role, "") or "")
    if a and role in a["roles"]:
        return a
    return next((x for x in accounts() if role in x["roles"] and x.get("on", True)), None)


def set_pin(acc_id: str, role: str):
    """Hesabı zolağın ✋ manual hesabı et (rolu yoxdursa əlavə olunur)."""
    a = get(acc_id)
    if not a or role not in ROLES:
        return
    if role not in a["roles"]:
        update(acc_id, roles=a["roles"] + [role])
    if not a.get("on", True):
        update(acc_id, on=True)
    _set(S_PIN + role, acc_id)


def test_failed(acc_id: str) -> bool:
    """Son 🧪 test uğursuz olubmu (girişsiz / cookie oxunmur) — belə hesab rotasiyaya düşmür."""
    t = LAST_TEST.get(acc_id)
    return bool(t) and not t.get("ok")


def _ready(a: dict, role: str, exclude=()) -> bool:
    return (a.get("on", True) and role in a["roles"] and a["id"] not in exclude
            and not block_left(a["id"]) and usable(a) and not test_failed(a["id"]))


def pick(lane: str = "download", exclude=(), advance: bool = False) -> dict | None:
    """Bu zolaq üçün işlək hesab (yoxdursa None — cookiesiz). Rejimə görə:
      ⚡ auto   — rolu olan, aktiv, bloklanmamış İLK hesab (siyahı sırası = prioritet)
      🔄 rr     — hazır hesablar arasında növbə ilə; advance=True (yükləmənin başlanğıcı) növbəni irəlilədir
      ✋ manual — yalnız seçilmiş hesab; bloklu / istisnadırsa None (başqasına keçmir)"""
    role = role_of(lane)
    m = mode()
    if m == "manual":
        a = pinned(lane)
        return a if a and _ready(a, role, exclude) else None
    ready = [a for a in accounts() if _ready(a, role, exclude)]
    if not ready:
        return None
    if m == "rr":
        if advance:
            _rr[role] += 1
        return ready[_rr[role] % len(ready)]
    return ready[0]


def _notify_block(acc: dict, reason: str, seconds: int):
    """Limit xəbəri sahibə: hansı hesab, nə qədər, indi kim işləyir."""
    from html import escape
    lines = [f"🔑 <b>YouTube hesabı limit aldı:</b> {escape(acc['name'])}",
             f"Səbəb: {escape(reason or 'rate-limit')} · ⏸ {seconds // 60} dəq. kənarda"]
    for lane, icon in (("depo", "📦"), ("download", "🎵")):
        if role_of(lane) in acc["roles"]:
            nxt = pick(lane)
            lines.append(f"{icon} indi: " + (escape(nxt["name"]) if nxt else "<b>cookiesiz</b>"
                                             + (" — ✋ manual rejim, başqa hesab seç" if mode() == "manual" else
                                                " — işlək hesab qalmadı")))
    try:
        spawn(notify_hook("\n".join(lines)))
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/yt_accounts.py:336]: %r", _e)


def opts(acc: dict | None) -> dict:
    """yt-dlp cookie opsiyaları bu hesab üçün (None — cookiesiz)."""
    if not acc:
        return {}
    from bot.core import webprofile as wp
    if acc["kind"] == "main":
        return wp.cookie_opts()
    if acc["kind"] == "file":
        return {"cookiefile": acc["file"]}
    cfg = {"browser": acc["browser"], "profile": acc.get("profile") or "", "keyring": acc.get("keyring") or ""}
    return {"cookiesfrombrowser": wp.browser_spec(cfg)}


def report(acc_id: str, ok: bool):
    if not acc_id:
        return
    st = _stats.setdefault(acc_id, {"ok": 0, "fail": 0, "last": 0, "reason": ""})
    st["ok" if ok else "fail"] += 1
    st["last"] = time.time()


def stats(acc_id: str) -> dict:
    return _stats.get(acc_id, {"ok": 0, "fail": 0, "last": 0, "reason": ""})


def describe(acc: dict) -> str:
    if acc["kind"] == "main":
        from bot.core import webprofile as wp
        return wp.describe()
    if acc["kind"] == "file":
        return "📄 cookies.txt" + ("" if usable(acc) else " — ⚠️ fayl yoxdur")
    from bot.core import webprofile as wp
    b = wp.BROWSERS.get(acc["browser"], {"emoji": "🌐", "label": acc["browser"]})
    prof = os.path.basename((acc.get("profile") or "").rstrip("/")) or "avto"
    return f"{b['emoji']} {b['label']} · {prof}"


def status(acc: dict) -> str:
    if acc.get("login") == "pending":
        return "⏳ giriş gözlənilir"
    if not acc.get("on", True):
        return "⚫ söndürülüb"
    if not usable(acc):
        return "⚠️ işləmir"
    if test_failed(acc["id"]):
        return "⚠️ girişsiz — rotasiyada deyil"
    left = block_left(acc["id"])
    if left:
        return f"⏸ bloklu {left // 60 + 1} dəq."
    return "🟢 hazır"


def summary_line() -> str:
    """Panellər üçün bir sətir: hansı zolaq hansı hesabla gedir."""
    d, u = pick("depo"), pick("download")
    n = len(accounts())
    rr = " (növbə ilə)" if mode() == "rr" else ""
    return (f"{MODES[mode()]} · 📦 {d['name'] if d else '— cookiesiz'}{rr} · 🎵 {u['name'] if u else '— cookiesiz'}{rr}"
            + (f" · 🔑 {n} hesab" if n > 1 else ""))


# ───────────────────────── 🧪 test ─────────────────────────
def test(acc_id: str) -> dict:
    """Cookie-ləri həqiqətən oxuyur (bloklayır — asyncio.to_thread ilə çağır)."""
    acc = get(acc_id)
    res = {"ok": False, "yt": 0, "logged_in": False, "error": None, "ts": time.time()}
    try:
        if not acc:
            raise ValueError("hesab tapılmadı")
        if acc["kind"] == "main":
            from bot.core import webprofile as wp
            r = wp.test_cookies()
            res.update(ok=r["ok"], yt=r["yt"], logged_in=r["logged_in"], error=r["error"])
        elif acc["kind"] == "file":
            if not usable(acc):
                raise FileNotFoundError("cookies faylı yoxdur — yenisini göndər")
            with open(acc["file"], "rb") as f:
                info = parse_cookie_file(f.read())
            res.update(ok=info["logged_in"], yt=info["yt"], logged_in=info["logged_in"],
                       error=None if info["logged_in"] else "girişli cookie yoxdur (müddəti bitib?)")
        else:
            from yt_dlp.cookies import extract_cookies_from_browser
            spec = opts(acc)["cookiesfrombrowser"]
            jar = extract_cookies_from_browser(spec[0], spec[1] if len(spec) > 1 else None,
                                               keyring=spec[2] if len(spec) > 2 else None)
            res["total"], res["yt"], res["logged_in"] = scan_cookie_jar(jar)
            res["ok"] = res["logged_in"]
            if not res["yt"]:
                res["error"] = "bu profildə youtube.com cookie-si yoxdur — profildə youtube.com açıb hesaba gir"
            elif not res["logged_in"]:
                res["error"] = "profildə YouTube hesabına giriş edilməyib"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    LAST_TEST[acc_id] = res
    return res


# ───────────────────────── 🖥 server cihazında Google girişi ─────────────────────────
# Serverin ekranında (KDE / GNOME sessiyası) ayrıca, boş Chrome profili açılır → Google giriş səhifəsi.
# Sahib orada (ekran qarşısında və ya uzaqdan masaüstü ilə) özü daxil olur — parol bota heç vaxt gəlmir.
# Pəncərə bağlananda cookie-lər diskə yazılır, ✅ «Girişi bitirdim» profili yoxlayır və hesabı aktiv edir.
# --password-store=basic: cookie-lər KWallet / GNOME keyring-ə bağlanmır → yt-dlp «BASICTEXT» ilə oxuyur.
def chrome_bin() -> tuple | None:
    for exe, key in CHROME_BINS:
        path = shutil.which(exe)
        if path:
            return path, key
    return None


def _gui_env() -> tuple[dict, list]:
    """Masaüstü sessiyasına qoşulmaq üçün mühit və Chrome bayraqları (Wayland varsa — Wayland)."""
    env = dict(os.environ)
    run = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = run
    wl = env.get("WAYLAND_DISPLAY") or next((n for n in ("wayland-0", "wayland-1")
                                              if os.path.exists(os.path.join(run, n))), "")
    if wl:
        env["WAYLAND_DISPLAY"] = wl
        return env, ["--ozone-platform=wayland"]
    if not env.get("DISPLAY") and os.path.exists("/tmp/.X11-unix/X0"):
        env["DISPLAY"] = ":0"
    if not env.get("DISPLAY"):
        raise RuntimeError("serverdə qrafik sessiya tapılmadı (Wayland / X) — 📱 öz cihazınla giriş et")
    return env, []


def login_running(acc_id: str) -> bool:
    proc = _login.get(acc_id)
    return proc is not None and proc.poll() is None


def _stop_login(acc_id: str, timeout: float = 30):
    """Chrome-u nəzakətlə bağla ki, cookie-lər diskə yazılsın.
    SIGTERM YALNIZ əsas brauzer prosesinə: o, şəbəkə prosesinə cookie-ləri saxlamağı deyib qaydasında çıxır.
    Əvvəl bütün qrupa (killpg) göndərilirdi → cookie-ləri yazan şəbəkə prosesi dərhal ölürdü və girişdən
    sonrakı heç bir cookie diskə düşmürdü («cookies tapılmadı»). Qrup yalnız vaxt bitəndə öldürülür."""
    proc = _login.pop(acc_id, None)
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/yt_accounts.py:484]: %r", _e)


def start_server_login(name: str = "") -> dict:
    """Serverin ekranında Google giriş pəncərəsi aç → gözləyən (⏳) hesab. Alınmasa RuntimeError."""
    found = chrome_bin()
    if not found:
        raise RuntimeError("serverdə Chrome / Chromium / Brave tapılmadı")
    exe, key = found
    env, flags = _gui_env()
    acc_id = uuid.uuid4().hex[:8]
    prof = os.path.abspath(os.path.join(PROFILES_DIR, acc_id))
    os.makedirs(prof, mode=0o700, exist_ok=True)
    os.chmod(PROFILES_DIR, 0o700)
    cmd = [exe, f"--user-data-dir={prof}", "--password-store=basic", "--no-first-run",
           "--no-default-browser-check", "--disable-sync", "--disk-cache-size=1048576", *flags,
           "--new-window", LOGIN_URL]
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(2)
    if proc.poll() is not None:
        shutil.rmtree(prof, ignore_errors=True)
        raise RuntimeError(f"brauzer açılmadı (kod {proc.returncode}) — qrafik sessiyaya qoşulmaq olmadı")
    _login[acc_id] = proc
    n = len(accounts())
    acc = {"id": acc_id, "kind": "browser", "name": (name or f"Google hesabı {n + 1}")[:40],
           "browser": key, "profile": prof, "keyring": "BASICTEXT", "managed": True, "login": "pending",
           "activated": False, "roles": ["depo"], "on": False, "added": int(time.time())}
    _save(accounts() + [acc])
    logger.info(f"🔑 Server girişi: {exe} açıldı (profil {acc_id}) — sahibin Google-a daxil olması gözlənilir")
    return acc


# Girişdən sonra profildə yalnız cookie-lər lazımdır — Chrome-un keşləri profil başına ~110 MB tuturdu
# (cookie faylı isə ~40 KB). Bunlar silinəndə Chrome növbəti açılışda özü yenidən yaradır.
SLIM_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache", "Service Worker",
             "IndexedDB", "blob_storage", "Shared Dictionary", "GrShaderCache", "ShaderCache",
             "GraphiteDawnCache", "component_crx_cache", "extensions_crx_cache", "optimization_guide_model_store",
             "Safe Browsing", "OnDeviceHeadSuggestModel", "screen_ai", "Crashpad")


def slim_profile(prof: str) -> int:
    """Bağlı Chrome profilinin keşlərini sil → azad olan bayt. Açıq profilə toxunmur."""
    if not prof or _profile_pid(prof):
        return 0
    freed = 0
    for base in (prof, os.path.join(prof, "Default")):
        for name in SLIM_DIRS:
            d = os.path.join(base, name)
            if os.path.isdir(d):
                for root, _, files in os.walk(d):
                    for f in files:
                        try:
                            freed += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                shutil.rmtree(d, ignore_errors=True)
    return freed


def _profile_pid(prof: str) -> int:
    """Profili açıq saxlayan Chrome prosesinin PID-i (SingletonLock → «host-pid»); yoxdursa 0.
    Kilid faylı Chrome çıxandan sonra da qala bilər — ona görə prosesin həqiqətən yaşadığı yoxlanılır."""
    try:
        target = os.readlink(os.path.join(prof, "SingletonLock"))
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError, IndexError):
        return 0


def login_open(acc_id: str) -> bool:
    """Server girişinin pəncərəsi (Chrome) hələ açıqdır?"""
    acc = get(acc_id) or {}
    return login_running(acc_id) or bool(acc.get("profile") and _profile_pid(acc["profile"]))


def finish_server_login(acc_id: str, force: bool = False) -> dict:
    """Profili yoxla; girişlidirsə hesabı aktiv et (bloklayır — to_thread ilə).
    Pəncərə hələ açıqdırsa — Chrome-u öldürmürük (cookie-lər yarımçıq yazılıb itə bilər), sahibdən onu
    bağlamağı istəyirik: {"open": True}. force=True — yumşaq SIGTERM və 60 san.-yə qədər qaydasında çıxış."""
    acc = get(acc_id) or {}
    prof = acc.get("profile") or ""
    if login_open(acc_id):
        if not force:
            return {"ok": False, "open": True, "yt": 0, "logged_in": False,
                    "error": "pəncərə hələ açıqdır — serverin ekranında onu bağla, sonra yenidən ✅"}
        pid = _profile_pid(prof) if prof else 0
        proc = _login.pop(acc_id, None)
        target = pid or (proc.pid if proc is not None and proc.poll() is None else 0)
        if target:
            try:
                os.kill(target, signal.SIGTERM)          # yalnız əsas proses — qrupa YOX
            except OSError:
                pass
        for _ in range(60):
            if not login_open(acc_id):
                break
            time.sleep(1)
        else:
            return {"ok": False, "open": True, "yt": 0, "logged_in": False,
                    "error": "Chrome 60 san. ərzində bağlanmadı — pəncərəni ekranda özün bağla"}
    _login.pop(acc_id, None)
    time.sleep(1)
    res = test(acc_id)
    if res["ok"]:
        update(acc_id, on=True, login="ok", activated=True)
        freed = slim_profile(prof)
        logger.info(f"🔑 Server girişi tamamlandı: {acc.get('name')} · {res['yt']} YouTube cookie"
                    + (f" · 🧹 keş {freed // 1048576} MB silindi" if freed > 1048576 else ""))
    else:
        logger.warning(f"🔑 Server girişi alınmadı: {acc.get('name')} ({acc_id}) — {res['error']}")
    return res


def can_server_login(acc: dict) -> bool:
    """Bu hesab üçün serverin ekranında Google giriş pəncərəsi açıla bilərmi?"""
    if not acc or acc.get("kind") != "browser":
        return False
    if acc.get("managed"):
        return True
    prof = acc.get("profile") or ""
    if prof and (os.path.abspath(prof).startswith(os.path.abspath(PROFILES_DIR)) or os.path.exists(prof)):
        return True
    return False


def cancel_server_login(acc_id: str) -> bool:
    """Server girişini ləğv et. Əgər hesab əvvəlcədən aktiv idisə — silinmir, yeni yaradılmışdırsa — silinir."""
    acc = get(acc_id)
    if not acc:
        return False
    _stop_login(acc_id, timeout=10)
    if acc.get("activated") or acc.get("login") in ("ok", "failed"):
        update(acc_id, login="failed" if test_failed(acc_id) else "ok")
        return False
    remove(acc_id)
    return True


def reopen_server_login(acc_id: str):
    """Giriş alınmayıbsa və ya sessiya bitibsə — eyni profildə pəncərəni yenidən aç."""
    acc = get(acc_id)
    if not acc or not can_server_login(acc):
        raise RuntimeError("bu hesab server brauzer girişi ilə yaradılmayıb")
    if login_open(acc_id):
        return
    found = chrome_bin()
    if not found:
        raise RuntimeError("serverdə Chrome / Chromium / Brave tapılmadı")
    env, flags = _gui_env()
    prof = acc.get("profile")
    if not prof:
        prof = os.path.abspath(os.path.join(PROFILES_DIR, acc_id))
    os.makedirs(prof, mode=0o700, exist_ok=True)
    cmd = [found[0], f"--user-data-dir={prof}", "--password-store=basic", "--no-first-run",
           "--no-default-browser-check", "--disable-sync", "--disk-cache-size=1048576", *flags,
           "--new-window", LOGIN_URL]
    _login[acc_id] = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
    update(acc_id, login="pending", on=False, managed=True, profile=prof)
    LAST_TEST.pop(acc_id, None)


# ───────────────────────── 🔘 hesabın düymələri (brauzerdə aç) ─────────────────────────
# Hər brauzer-profilli hesabın səhifəsində sahibin adlandırdığı düymələr: basanda həmin hesabın Chrome profili serverin
# ekranında açılır (məs. YouTube / Gmail — hesabın poçtuna baxmaq üçün). Link siyahısı hesabda saxlanır (`links`).
LINK_PRESETS = {
    "yt": ("▶️ YouTube", "https://www.youtube.com/"),
    "gm": ("📧 Gmail", "https://mail.google.com/"),
    "ga": ("👤 Google hesabı", "https://myaccount.google.com/"),
    "ym": ("🎵 YT Music", "https://music.youtube.com/"),
}
DEFAULT_LINKS = [{"name": LINK_PRESETS["yt"][0], "url": LINK_PRESETS["yt"][1]}]   # ayrıca saxlanmayıbsa
MAX_LINKS = 6
EXE_NAMES = {   # webprofile brauzer açarı → serverdə icra faylı (sıra ilə)
    "chrome": ("google-chrome-stable", "google-chrome"), "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave", "brave-browser"), "edge": ("microsoft-edge-stable", "microsoft-edge"),
    "opera": ("opera",), "vivaldi": ("vivaldi-stable", "vivaldi"), "whale": ("naver-whale-stable", "whale"),
    "firefox": ("firefox",), "librewolf": ("librewolf",), "floorp": ("floorp",), "zen": ("zen-browser", "zen"),
    "waterfox": ("waterfox",),
}


def links(acc: dict | None) -> list:
    """[{name, url}] — saxlanmayıbsa standart 1 düymə (▶️ YouTube)."""
    saved = (acc or {}).get("links")
    if not isinstance(saved, list):
        return [dict(x) for x in DEFAULT_LINKS]
    return [x for x in saved if isinstance(x, dict) and x.get("name") and x.get("url")][:MAX_LINKS]


def normalize_url(url: str) -> str:
    """Yalnız http(s) linki: 'mail.google.com' → 'https://mail.google.com'. Yararsızdırsa ValueError."""
    u = (url or "").strip()
    if not u or any(c.isspace() for c in u) or len(u) > 300:
        raise ValueError("link boşluqsuz olmalıdır (məs. mail.google.com)")
    if "://" not in u:
        u = "https://" + u
    if not u.lower().startswith(("http://", "https://")) or "." not in u.split("://", 1)[1].split("/", 1)[0]:
        raise ValueError("yalnız http / https linki (məs. https://mail.google.com)")
    return u


def clean_link_name(name: str) -> str:
    return " ".join((name or "").split())[:24]


def add_link(acc_id: str, name: str, url: str) -> dict:
    acc = get(acc_id)
    if not acc:
        raise ValueError("hesab tapılmadı")
    items = links(acc)
    if len(items) >= MAX_LINKS:
        raise ValueError(f"ən çox {MAX_LINKS} düymə ola bilər — birini sil")
    name, url = clean_link_name(name), normalize_url(url)
    if not name:
        raise ValueError("düymənin adı boşdur")
    items.append({"name": name, "url": url})
    update(acc_id, links=items)
    return items[-1]


def rename_link(acc_id: str, idx: int, name: str) -> bool:
    items = links(get(acc_id))
    name = clean_link_name(name)
    if not name or not 0 <= idx < len(items):
        return False
    items[idx]["name"] = name
    return bool(update(acc_id, links=items))


def remove_link(acc_id: str, idx: int) -> bool:
    items = links(get(acc_id))
    if not 0 <= idx < len(items):
        return False
    items.pop(idx)
    return bool(update(acc_id, links=items))


def can_open(acc: dict | None) -> bool:
    """Bu hesabın brauzer profili serverdə varmı (düymələr yalnız belə hesablarda çıxır)."""
    if not acc or acc.get("kind") != "browser" or not acc.get("profile"):
        return False
    return bool(acc.get("managed")) or os.path.exists(acc["profile"])


def _find_exe(key: str) -> str | None:
    for name in EXE_NAMES.get(key, ()):
        path = shutil.which(name)
        if path:
            return path
    return None


def browser_cmd(acc: dict, url: str) -> tuple[list, dict]:
    """Hesabın profilini açan əmr + mühit. Chromium ailəsi: --user-data-dir (öz profilimiz) və ya
    --user-data-dir + --profile-directory (brauzerin adi profili); Firefox ailəsi: --profile. RuntimeError."""
    from bot.core import webprofile as wp
    key, prof = acc.get("browser") or "", os.path.abspath(acc.get("profile") or "")
    spec = wp.BROWSERS.get(key) or {}
    env, flags = _gui_env()
    if spec.get("family") == "firefox":
        exe = _find_exe(key)
        if not exe:
            raise RuntimeError(f"serverdə {spec.get('label', key)} tapılmadı")
        return [exe, "--profile", prof, "--new-window", url], env
    exe = _find_exe(key) or (chrome_bin() or (None,))[0]
    if not exe:
        raise RuntimeError("serverdə Chrome / Chromium / Brave tapılmadı")
    own = acc.get("managed") or prof.startswith(os.path.abspath(PROFILES_DIR))
    dirs = [f"--user-data-dir={prof}"] if own else [f"--user-data-dir={os.path.dirname(prof)}",
                                                    f"--profile-directory={os.path.basename(prof)}"]
    return [exe, *dirs, "--password-store=basic", "--no-first-run", "--no-default-browser-check", *flags,
            "--new-window", url], env


def open_url(acc_id: str, url: str) -> str:
    """Serverin ekranında hesabın profilini həmin linklə aç (bloklayır — to_thread ilə). Alınmasa RuntimeError.
    Profil artıq açıqdırsa Chrome yeni pəncərəni mövcud prosesdə açıb dərhal çıxır (kod 0) — bu da uğurdur."""
    acc = get(acc_id)
    if not can_open(acc):
        raise RuntimeError("bu hesabın brauzer profili serverdə yoxdur (cookies.txt / əsas hesab)")
    url = normalize_url(url)
    cmd, env = browser_cmd(acc, url)
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        code = proc.wait(2)
    except subprocess.TimeoutExpired:
        code = None                                  # hələ işləyir — pəncərə açılıb
    if code not in (None, 0):
        raise RuntimeError(f"brauzer açılmadı (kod {code}) — profil başqa brauzerdə açıq ola bilər")
    logger.info(f"🔘 {acc['name']}: brauzerdə açıldı → {url}")
    return url


# ───────────────────────── 🩺 avtomatik yoxlama ─────────────────────────
# Test nəticəsi yaddaşdadır (restartda itir) — başlanğıcdan bir az sonra və hər HEALTH_EVERY san.
# bütün hesablar yoxlanılır; girişini itirən hesab rotasiyadan çıxır, sahibə xəbər gedir.
HEALTH_EVERY = int(os.getenv("YT_ACC_HEALTH_EVERY", str(30 * 60)))
_health_task = None


async def health_check(notify: bool = True) -> dict:
    """Bütün hesabları paralel yoxla → {id: nəticə}. Vəziyyəti dəyişən hesablar barədə xəbər ver."""
    import asyncio
    from html import escape
    accs = [a for a in accounts() if a.get("login") != "pending"]
    before = {a["id"]: test_failed(a["id"]) for a in accs}
    results = await asyncio.gather(*(asyncio.to_thread(test, a["id"]) for a in accs))
    lost = [a for a, r in zip(accs, results) if not r["ok"] and not before[a["id"]]]
    back = [a for a, r in zip(accs, results) if r["ok"] and before[a["id"]]]
    for a in lost:
        logger.warning(f"🔑 YouTube hesabı girişsizdir — rotasiyadan çıxarıldı: {a['name']} "
                       f"({LAST_TEST[a['id']].get('error')})")
    for a in back:
        logger.info(f"🔑 YouTube hesabı yenidən işləyir — rotasiyaya qayıtdı: {a['name']}")
    if notify and notify_hook is not None and (lost or back):
        lines = []
        if lost:
            lines.append("⚠️ <b>YouTube hesabı girişsizdir</b> — rotasiyadan çıxarıldı:")
            lines += [f"• {escape(a['name'])}: {escape(str(LAST_TEST[a['id']].get('error'))[:120])}" for a in lost]
        if back:
            lines.append("✅ <b>Yenidən işləyir</b> — rotasiyaya qayıtdı:")
            lines += [f"• {escape(a['name'])}" for a in back]
        try:
            await notify_hook("\n".join(lines))
        except Exception as e:
            logger.debug(f"hesab xəbəri getmədi: {e}")
    return {a["id"]: r for a, r in zip(accs, results)}


def start_health_loop(first_delay: float = 10):
    """Bot başlayanda bir dəfə çağırılır (menu_plugin) — arxa planda dövri yoxlama."""
    global _health_task
    import asyncio
    if _health_task is not None and not _health_task.done():
        return

    async def loop():
        await asyncio.sleep(first_delay)
        while True:
            try:
                await health_check(notify=True)
            except Exception as e:
                logger.warning(f"🔑 hesab yoxlaması alınmadı: {e}")
            await asyncio.sleep(HEALTH_EVERY)

    from common.utilities import spawn
    _health_task = spawn(loop())
