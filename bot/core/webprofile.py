"""
🌐 Web profil — yt-dlp üçün cookies və User-Agent idarəsi (/menu → 🖥 Sistem → 🌐 Brauzer / cookies).

Cookie mənbəyi (ayar: web:cookies):
  auto    — YOUTUBE_COOKIES_FILE varsa o, yoxdursa seçilmiş brauzer   (default, köhnə davranış)
  browser — həmişə seçilmiş brauzer
  file    — həmişə cookies.txt (YOUTUBE_COOKIES_FILE)
  none    — cookiesiz

Brauzerlər: Firefox, LibreWolf, Floorp, Zen, Waterfox (Firefox mühərriki — profil qovluğu ilə),
            Chrome, Chromium, Brave, Edge, Opera, Vivaldi, Whale, Safari (macOS).
Adi, Flatpak və Snap quraşdırmaları avtomatik tapılır; profil seçilməyibsə yt-dlp ən son istifadə olunanı götürür.

User-Agent (ayar: web:ua):
  ytdlp  — yt-dlp özü seçir (default; YouTube klientləri üçün ən etibarlısı)
  match  — cookies götürülən brauzerə uyğun UA (Firefox cookies → Firefox UA)
  random — siyahıdan təsadüfi masaüstü UA
"""
import glob
import json
import logging
import os
import random
import sys
import time
from common.database import Settings
from common.kv import next_of

logger = logging.getLogger(__name__)

H = os.path.expanduser("~")
XDG = os.getenv("XDG_CONFIG_HOME") or os.path.join(H, ".config")
APPDATA = os.getenv("APPDATA") or ""
LOCALAPPDATA = os.getenv("LOCALAPPDATA") or ""
MAC = os.path.join(H, "Library", "Application Support")


def _p(*parts):
    return os.path.join(*parts)


# key → label, emoji, yt-dlp mühərriki, ailə, profil kök qovluqları
BROWSERS = {
    "firefox": {"label": "Firefox", "emoji": "🦊", "engine": "firefox", "family": "firefox", "roots": [
        _p(H, ".mozilla", "firefox"), _p(XDG, "mozilla", "firefox"),
        _p(H, ".var", "app", "org.mozilla.firefox", ".mozilla", "firefox"),
        _p(H, ".var", "app", "org.mozilla.firefox", "config", "mozilla", "firefox"),
        _p(H, "snap", "firefox", "common", ".mozilla", "firefox"),
        _p(MAC, "Firefox", "Profiles"), _p(APPDATA, "Mozilla", "Firefox", "Profiles")]},
    "librewolf": {"label": "LibreWolf", "emoji": "🐺", "engine": "firefox", "family": "firefox", "roots": [
        _p(H, ".librewolf"), _p(XDG, "librewolf", "librewolf"),
        _p(H, ".var", "app", "io.gitlab.librewolf-community", ".librewolf"),
        _p(MAC, "librewolf", "Profiles"), _p(APPDATA, "librewolf", "Profiles")]},
    "floorp": {"label": "Floorp", "emoji": "🌀", "engine": "firefox", "family": "firefox", "roots": [
        _p(H, ".floorp"), _p(H, ".var", "app", "one.ablaze.floorp", ".floorp"),
        _p(MAC, "Floorp", "Profiles"), _p(APPDATA, "Floorp", "Profiles")]},
    "zen": {"label": "Zen", "emoji": "🧘", "engine": "firefox", "family": "firefox", "roots": [
        _p(H, ".zen"), _p(H, ".var", "app", "app.zen_browser.zen", ".zen"),
        _p(MAC, "zen", "Profiles"), _p(APPDATA, "zen", "Profiles")]},
    "waterfox": {"label": "Waterfox", "emoji": "💧", "engine": "firefox", "family": "firefox", "roots": [
        _p(H, ".waterfox"), _p(MAC, "Waterfox", "Profiles"), _p(APPDATA, "Waterfox", "Profiles")]},
    "chrome": {"label": "Chrome", "emoji": "🟡", "engine": "chrome", "family": "chromium", "roots": [
        _p(XDG, "google-chrome"), _p(H, ".var", "app", "com.google.Chrome", "config", "google-chrome"),
        _p(MAC, "Google", "Chrome"), _p(LOCALAPPDATA, "Google", "Chrome", "User Data")]},
    "chromium": {"label": "Chromium", "emoji": "🔵", "engine": "chromium", "family": "chromium", "roots": [
        _p(XDG, "chromium"), _p(H, ".var", "app", "org.chromium.Chromium", "config", "chromium"),
        _p(H, "snap", "chromium", "common", "chromium"),
        _p(MAC, "Chromium"), _p(LOCALAPPDATA, "Chromium", "User Data")]},
    "brave": {"label": "Brave", "emoji": "🦁", "engine": "brave", "family": "chromium", "roots": [
        _p(XDG, "BraveSoftware", "Brave-Browser"),
        _p(H, ".var", "app", "com.brave.Browser", "config", "BraveSoftware", "Brave-Browser"),
        _p(H, "snap", "brave", "current", ".config", "BraveSoftware", "Brave-Browser"),
        _p(MAC, "BraveSoftware", "Brave-Browser"), _p(LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data")]},
    "edge": {"label": "Edge", "emoji": "🌊", "engine": "edge", "family": "chromium", "roots": [
        _p(XDG, "microsoft-edge"), _p(H, ".var", "app", "com.microsoft.Edge", "config", "microsoft-edge"),
        _p(MAC, "Microsoft Edge"), _p(LOCALAPPDATA, "Microsoft", "Edge", "User Data")]},
    "opera": {"label": "Opera", "emoji": "⭕", "engine": "opera", "family": "chromium", "roots": [
        _p(XDG, "opera"), _p(H, ".var", "app", "com.opera.Opera", "config", "opera"),
        _p(MAC, "com.operasoftware.Opera"), _p(APPDATA, "Opera Software", "Opera Stable")]},
    "vivaldi": {"label": "Vivaldi", "emoji": "🎻", "engine": "vivaldi", "family": "chromium", "roots": [
        _p(XDG, "vivaldi"), _p(H, ".var", "app", "com.vivaldi.Vivaldi", "config", "vivaldi"),
        _p(MAC, "Vivaldi"), _p(LOCALAPPDATA, "Vivaldi", "User Data")]},
    "whale": {"label": "Whale", "emoji": "🐋", "engine": "whale", "family": "chromium", "roots": [
        _p(XDG, "naver-whale"), _p(MAC, "Naver", "Whale"), _p(LOCALAPPDATA, "Naver", "Naver Whale", "User Data")]},
    "safari": {"label": "Safari", "emoji": "🧭", "engine": "safari", "family": "safari", "roots": [
        _p(H, "Library", "Containers", "com.apple.Safari", "Data", "Library", "Cookies"),
        _p(H, "Library", "Cookies")]},
}

COOKIE_SOURCES = {
    "auto": "🔄 Avto",
    "browser": "🌐 Brauzer",
    "file": "📄 cookies.txt",
    "none": "🚫 Cookiesiz",
}
UA_MODES = {
    "ytdlp": "⚙️ yt-dlp özü",
    "match": "🎯 Brauzerə uyğun",
    "random": "🎲 Təsadüfi",
}
# Chromium ailəsinin Linux-da cookies şifrəsini saxladığı yer (yt-dlp adları); "" = avto
KEYRINGS = ["", "BASICTEXT", "GNOMEKEYRING", "KWALLET5", "KWALLET6", "KWALLET"]

S_COOKIES, S_BROWSER, S_PROFILE, S_KEYRING, S_UA = "web:cookies", "web:browser", "web:profile", "web:keyring", "web:ua"
S_BMODE = "web:browser_mode"      # auto — ən son istifadə olunan brauzer + profil · manual — əl ilə seçilmiş
BROWSER_MODES = {"auto": "🤖 Avto", "manual": "✋ Manual"}
S_KRMODE = "web:keyring_mode"     # auto — işləyən keyring sınaqla tapılır · manual — əl ilə seçilmiş

USER_AGENTS = {
    "firefox": [
        "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0",
    ],
    "chrome": [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    ],
    "edge": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
    ],
    "opera": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 OPR/123.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 OPR/123.0.0.0",
    ],
    "safari": [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
    ],
}
UA_GROUP = {"firefox": "firefox", "chrome": "chrome", "chromium": "chrome", "brave": "chrome", "vivaldi": "chrome",
            "whale": "chrome", "edge": "edge", "opera": "opera", "safari": "safari"}
ALL_USER_AGENTS = [ua for lst in USER_AGENTS.values() for ua in lst]

LAST_TEST = {}                 # son 🧪 test nəticəsi (menyuda göstərilir)


# ───────────────────────── ayarlar ─────────────────────────
_S = Settings(empty_is_default=True)
_get, _set = _S.get, _S.put


def cookies_file() -> str:
    """YOUTUBE_COOKIES_FILE yolu ('' — təyin olunmayıb)."""
    return os.path.expanduser(os.getenv("YOUTUBE_COOKIES_FILE") or "")


def browser_mode() -> str:
    m = _get(S_BMODE, "auto")
    return m if m in BROWSER_MODES else "auto"


def set_browser_mode(mode: str):
    if mode in BROWSER_MODES:
        _set(S_BMODE, mode)


def keyring_mode() -> str:
    m = _get(S_KRMODE, "auto")
    return m if m in BROWSER_MODES else "auto"


def set_keyring_mode(mode: str):
    if mode in BROWSER_MODES:
        _set(S_KRMODE, mode)


def set_keyring(kr: str):
    """✋ Manual keyring seçimi ("" — yt-dlp özü)."""
    kr = (kr or "").upper()
    if kr in KEYRINGS:
        _set(S_KEYRING, kr)
        _set(S_KRMODE, "manual")


def auto_browser() -> str | None:
    """Serverdə ən son istifadə olunan brauzer (profillərinin ən təzə cookie faylına görə)."""
    best, best_t = None, -1.0
    for key, profs in detect().items():
        if profs and profs[0].get("mtime", 0) > best_t:
            best, best_t = key, profs[0].get("mtime", 0)
    return best


# 🔐 Avto keyring: bot terminaldan / screen-dən qalxanda iş masası mühiti (XDG_CURRENT_DESKTOP) olmur və
# yt-dlp-nin öz "avto" keyring seçimi KDE-də Chrome cookie-lərini aça bilmir (0 cookie). 🤖 avto rejimdə
# variantlar sınanır, girişli cookie verən yadda saxlanır. Sınaq bloklayır — yalnız test / yoxlamada (thread-də).
KEYRING_PROBE = ("KWALLET6", "KWALLET5", "KWALLET", "GNOMEKEYRING", "BASICTEXT", "")
_auto_kr = {"key": None, "kr": None}


def probe_keyring(browser: str, profile: str = "") -> str | None:
    """Chromium ailəsi üçün işləyən keyring-i tap (ən çox YouTube girişli cookie) və yadda saxla."""
    from yt_dlp.cookies import extract_cookies_from_browser
    engine = BROWSERS[browser]["engine"]
    best, best_n = None, -1
    for kr in KEYRING_PROBE:
        try:
            _, yt, logged = scan_cookie_jar(extract_cookies_from_browser(engine, profile or None, keyring=kr or None))
        except Exception:
            continue
        score = yt + (1000 if logged else 0)
        if score > best_n and yt:
            best, best_n = kr, score
        if logged:
            break                                     # ilk girişli variant kifayətdir
    _auto_kr.update(key=(browser, profile or ""), kr=best)
    if best is not None:
        logger.info(f"🔐 Avto keyring: {BROWSERS[browser]['label']} → {best or 'avto'}")
    return best


def auto_keyring(browser: str, profile: str = "") -> str | None:
    """Yadda saxlanmış avto keyring (sınaq aparılmayıbsa None)."""
    return _auto_kr["kr"] if _auto_kr["key"] == (browser, profile or "") else None


def current() -> dict:
    manual = browser_mode() == "manual"
    saved = (_get(S_BROWSER) or os.getenv("YOUTUBE_BROWSER") or "firefox").lower()
    browser = saved if manual else (auto_browser() or saved)
    if browser not in BROWSERS:
        browser = "firefox"
    src = _get(S_COOKIES, "auto")
    ua = _get(S_UA, "ytdlp")
    kr = (_get(S_KEYRING) or "").upper()
    profile = (_get(S_PROFILE) or "") if manual else ""
    kr_auto = keyring_mode() == "auto"
    if kr_auto and BROWSERS[browser]["family"] == "chromium":
        akr = auto_keyring(browser, profile)
        if akr is not None:
            kr = akr                                   # 🤖 avto — sınaqla tapılmış keyring
    return {
        "cookies": src if src in COOKIE_SOURCES else "auto",
        "browser": browser,
        "profile": profile,
        "mode": "manual" if manual else "auto",
        "kr_mode": "auto" if kr_auto else "manual",
        "keyring": kr if kr in KEYRINGS else "",
        "ua": ua if ua in UA_MODES else "ytdlp",
    }


def effective_source(cfg=None) -> str:
    """auto-nu açır: 'file' | 'browser' | 'none'."""
    cfg = cfg or current()
    if cfg["cookies"] == "auto":
        f = cookies_file()
        return "file" if f and os.path.isfile(f) else "browser"
    return cfg["cookies"]


def set_source(value: str):
    if value in COOKIE_SOURCES:
        _set(S_COOKIES, value)


def set_browser(key: str):
    if key in BROWSERS and key != current()["browser"]:
        _set(S_BROWSER, key)
        _set(S_PROFILE, "")                          # profil başqa brauzerə aiddir
        if BROWSERS[key]["family"] != "chromium":
            _set(S_KEYRING, "")


def set_profile(path: str):
    _set(S_PROFILE, path or "")


def cycle_keyring() -> str:
    cur = current()["keyring"]
    nxt = next_of(KEYRINGS, cur)
    _set(S_KEYRING, nxt)
    return nxt


def set_ua_mode(mode: str):
    if mode in UA_MODES:
        _set(S_UA, mode)


def cycle_ua_mode() -> str:
    keys = list(UA_MODES)
    nxt = next_of(keys, current()["ua"])
    _set(S_UA, nxt)
    return nxt


# ───────────────────────── brauzer / profil aşkarlanması ─────────────────────────
_detect_cache = {"ts": 0.0, "data": None}


def _firefox_profiles(root: str) -> list:
    out = []
    cands = [root] + [d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)]
    for d in cands:
        ck = os.path.join(d, "cookies.sqlite")
        if os.path.isfile(ck):
            name = os.path.basename(d)
            out.append({"name": name.split(".", 1)[1] if "." in name else name, "path": d, "cookies": ck})
    return out


def _chromium_profiles(root: str) -> list:
    names = {}
    local_state = os.path.join(root, "Local State")
    if os.path.isfile(local_state):          # Flatpak və s. boş qovluqlarda Local State olmur — bu xəta deyil, ad sadəcə qovluq adıdır
        try:
            with open(local_state, encoding="utf-8") as f:
                info = json.load(f).get("profile", {}).get("info_cache", {}) or {}
            names = {k: (v or {}).get("name") or k for k, v in info.items()}
        except (OSError, ValueError, AttributeError) as _e:
            logger.debug("Chromium 'Local State' oxunmadı (%s): %r", local_state, _e)
    out = []
    cands = [root] + [d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)]
    for d in cands:
        for ck in (os.path.join(d, "Network", "Cookies"), os.path.join(d, "Cookies")):
            if os.path.isfile(ck):
                base = os.path.basename(d)
                label = names.get(base) or base
                out.append({"name": label if label == base else f"{label} ({base})", "path": d, "cookies": ck})
                break
    return out


def _safari_profiles(root: str) -> list:
    ck = os.path.join(root, "Cookies.binarycookies")
    return [{"name": "default", "path": "", "cookies": ck}] if os.path.isfile(ck) else []


def profiles(key: str) -> list:
    """Brauzerin serverdə tapılan profilləri — ən son istifadə olunan birinci."""
    b = BROWSERS.get(key)
    if not b or (b["family"] == "safari" and sys.platform != "darwin"):
        return []
    fn = {"firefox": _firefox_profiles, "chromium": _chromium_profiles, "safari": _safari_profiles}[b["family"]]
    found, seen = [], set()
    for root in b["roots"]:
        if not root or not os.path.isdir(root):
            continue
        for p in fn(root):
            real = os.path.realpath(p["cookies"])
            if real in seen:
                continue
            seen.add(real)
            try:
                p["mtime"] = os.path.getmtime(p["cookies"])
            except OSError:
                p["mtime"] = 0
            found.append(p)
    found.sort(key=lambda p: p["mtime"], reverse=True)
    return found


def detect(force: bool = False) -> dict:
    """{key: [profillər]} — 30 san. keşlənir."""
    if not force and _detect_cache["data"] is not None and time.time() - _detect_cache["ts"] < 30:
        return _detect_cache["data"]
    data = {k: profiles(k) for k in BROWSERS}
    _detect_cache.update(ts=time.time(), data=data)
    return data


def profile_label(cfg=None) -> str:
    cfg = cfg or current()
    if not cfg["profile"]:
        return "avto (ən son istifadə olunan)"
    for p in detect().get(cfg["browser"], []):
        if p["path"] == cfg["profile"]:
            return p["name"]
    return os.path.basename(cfg["profile"].rstrip("/")) or cfg["profile"]


# ───────────────────────── yt-dlp üçün ─────────────────────────
def browser_spec(cfg=None) -> tuple:
    """yt-dlp 'cookiesfrombrowser': (mühərrik, profil, keyring)."""
    cfg = cfg or current()
    b = BROWSERS[cfg["browser"]]
    profile = cfg["profile"] or None
    if profile is None and cfg["browser"] != b["engine"]:
        # Firefox fork-u: yt-dlp onun qovluğunu tanımır → ən son profili açıq-aydın veririk
        found = detect().get(cfg["browser"]) or []
        profile = found[0]["path"] if found else None
    keyring = cfg["keyring"] if b["family"] == "chromium" and cfg["keyring"] else None
    spec = (b["engine"], profile, keyring)
    while spec and spec[-1] is None and len(spec) > 1:
        spec = spec[:-1]
    return spec


def cookie_opts() -> dict:
    """yt-dlp opsiyaları: cookiefile / cookiesfrombrowser / heç nə."""
    cfg = current()
    src = effective_source(cfg)
    if src == "file":
        f = cookies_file()
        if f and os.path.isfile(f):
            return {"cookiefile": f}
        logger.warning("cookies.txt rejimi seçilib, amma YOUTUBE_COOKIES_FILE tapılmadı — brauzerə keçilir")
        return {"cookiesfrombrowser": browser_spec(cfg)}
    if src == "none":
        return {}
    return {"cookiesfrombrowser": browser_spec(cfg)}


def get_random_user_agent(mode: str = None) -> str:
    """UA rejiminə görə: match → brauzerə uyğun, digər halda istənilən masaüstü UA."""
    cfg = current()
    mode = mode or cfg["ua"]
    if mode == "match":
        pool = USER_AGENTS.get(UA_GROUP.get(BROWSERS[cfg["browser"]]["engine"], "chrome"), ALL_USER_AGENTS)
    else:
        pool = ALL_USER_AGENTS
    return random.choice(pool)


def ua_opts() -> dict:
    """yt-dlp üçün User-Agent ('ytdlp' rejimində boş — yt-dlp hər klient üçün özü seçir)."""
    mode = current()["ua"]
    if mode == "ytdlp":
        return {}
    return {"http_headers": {"User-Agent": get_random_user_agent(mode)}}


def describe(cfg=None) -> str:
    cfg = cfg or current()
    src = effective_source(cfg)
    if src == "file":
        return f"📄 cookies.txt ({os.path.basename(cookies_file())})"
    if src == "none":
        return "🚫 cookiesiz"
    b = BROWSERS[cfg["browser"]]
    return f"{b['emoji']} {b['label']} · {profile_label(cfg)}"


# ───────────────────────── 🧪 test ─────────────────────────
LOGIN_COOKIES = {"SAPISID", "__Secure-3PAPISID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO", "SID"}


def scan_cookie_jar(jar) -> tuple[int, int, bool]:
    """→ (cəmi cookie, youtube.com cookie, girişli?) — webprofile və 🔑 yt_accounts testləri üçün."""
    total = yt = 0
    logged_in = False
    now = time.time()
    for c in jar:
        total += 1
        if "youtube.com" in (c.domain or ""):
            yt += 1
            if c.name in LOGIN_COOKIES and (not c.expires or c.expires > now):
                logged_in = True
    return total, yt, logged_in


def test_cookies() -> dict:
    """Cookies-i həqiqətən oxuyur (bloklayır — asyncio.to_thread ilə çağır)."""
    t0 = time.monotonic()
    cfg = current()
    if cfg["kr_mode"] == "auto" and BROWSERS[cfg["browser"]]["family"] == "chromium" \
            and effective_source(cfg) == "browser":
        probe_keyring(cfg["browser"], cfg["profile"])  # 🔐 avto keyring-i (yenidən) tap
        cfg = current()
    src = effective_source(cfg)
    res = {"source": src, "label": describe(cfg), "ok": False, "total": 0, "yt": 0, "logged_in": False,
           "error": None, "ts": time.time()}
    try:
        if src == "none":
            res["ok"] = True
        else:
            if src == "file":
                from yt_dlp.cookies import YoutubeDLCookieJar
                f = cookies_file()
                if not (f and os.path.isfile(f)):
                    raise FileNotFoundError("YOUTUBE_COOKIES_FILE tapılmadı")
                jar = YoutubeDLCookieJar(f)
                jar.load(ignore_discard=True, ignore_expires=True)
            else:
                from yt_dlp.cookies import extract_cookies_from_browser
                spec = browser_spec(cfg)
                jar = extract_cookies_from_browser(spec[0], spec[1] if len(spec) > 1 else None,
                                                   keyring=spec[2] if len(spec) > 2 else None)
            res["total"], res["yt"], res["logged_in"] = scan_cookie_jar(jar)
            res["ok"] = res["yt"] > 0
            if not res["ok"]:
                res["error"] = "YouTube cookie-si yoxdur — bu brauzerdə/profildə youtube.com açılmayıb"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    res["secs"] = time.monotonic() - t0
    LAST_TEST.clear()
    LAST_TEST.update(res)
    logger.info(f"🧪 Cookie testi: {res['label']} → yt={res['yt']} login={res['logged_in']} err={res['error']}")
    return res
