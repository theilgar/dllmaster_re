"""
🌐 Vahid Proxy / 🧅 Tor İdarəetmə Mühərriki — common/proxy.py

YouTube və şəbəkə trafiki üçün mərkəzi çıxış IP-si və rotasiya sistemi.
Həm 🤖 Bot (yükləmə, axtarış, YT Music), həm də 📱 Userbot (Telethon bağlantıları) tərəfindən istifadə olunur.
/menu → 🖥 Sistem → 🌐 Proxy / Tor ilə idarə olunur, settings cədvəlində saxlanır (px:*).

Rejimlər:
  off    — birbaşa (default)
  proxy  — öz proxy siyahın: http(s)://, socks5://, socks5h:// (user:pass@ ilə də). Rotasiya:
             sticky  — biri işlədikcə o; 403 / xəta olanda növbətisinə
             round   — hər yükləmədə növbəti (yük paylanır)
  tor    — 127.0.0.1:9050 SOCKS; ControlPort (9051) varsa 🔄 Yeni IP (NEWNYM), 403-də avtomatik
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

from common.bootstrap import port_open
from common.database import Settings

logger = logging.getLogger(__name__)

MODES = {"off": "❌ Birbaşa", "proxy": "🌐 Proxy", "tor": "🧅 Tor"}
SCOPES = {
    "user": "⬇️ İstifadəçi yükləmələri",
    "depo": "📦 Depo doldurucu",
    "search": "🔎 Axtarış / YT Music",
    "telegram": "📱 Telegram / Userbot",
}
SCOPE_DEFAULT = {"user": True, "depo": True, "search": False, "telegram": False}
ROTATE = {"sticky": "📌 Sabit (xətada növbəti)", "round": "🔁 Hər yükləmədə növbəti"}
BAD_FOR = 600                        # 403 / xəta verən proxy 10 dəq. kənarda qalır
_PROXY_RE = re.compile(r"^(https?|socks5h?|socks4a?)://(?:[^:@/\s]+(?::[^@/\s]*)?@)?[\w.\-]+:\d{2,5}/?$", re.I)

_state = {"idx": 0, "bad": {}, "newnym": 0.0, "stats": {}}

_S = Settings()
_s, _put = _S.get, _S.put


# ── ayarlar ──
def mode() -> str:
    m = _s("px:mode", "off")
    return m if m in MODES else "off"


def set_mode(m: str) -> None:
    _put("px:mode", m if m in MODES else "off")


def scope_on(scope: str) -> bool:
    v = _s(f"px:scope:{scope}")
    return SCOPE_DEFAULT.get(scope, True) if v is None else v == "1"


def toggle_scope(scope: str) -> bool:
    new = not scope_on(scope)
    _put(f"px:scope:{scope}", "1" if new else "0")
    return new


def rotate_mode() -> str:
    r = _s("px:rotate", "sticky")
    return r if r in ROTATE else "sticky"


def proxies() -> List[str]:
    try:
        v = json.loads(_s("px:list") or "[]")
        return [p for p in v if isinstance(p, str)]
    except ValueError:
        return []


def parse_proxies(text: str) -> Tuple[List[str], List[str]]:
    """Mətndən proxy-lər (hər sətirdə bir). host:port → http://; ip:port:user:pass formatı da qəbul olunur."""
    good, bad = [], []
    for raw in re.split(r"[\s,]+", text or ""):
        p = raw.strip()
        if not p:
            continue
        if "://" not in p:
            parts = p.split(":")
            if len(parts) == 4:                       # ip:port:user:pass
                p = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
            else:
                p = "http://" + p
        if p.lower().startswith("socks5://"):
            p = "socks5h://" + p[9:]                  # DNS də proxy-dən (sızma olmasın)
        (good if _PROXY_RE.match(p) else bad).append(p)
    return good, bad


def add_proxies(lst: List[str]) -> int:
    cur = proxies()
    new = [p for p in lst if p not in cur]
    _put("px:list", json.dumps(cur + new))
    return len(new)


def remove_proxy(i: int) -> None:
    cur = proxies()
    if 0 <= i < len(cur):
        cur.pop(i)
        _put("px:list", json.dumps(cur))


def clear_proxies() -> None:
    _put("px:list", "[]")
    _state.update(idx=0, bad={})


def mask(p: str) -> str:
    """Parol göstərilməsin: socks5h://user:****@host:port"""
    return re.sub(r"(://[^:@/]+:)[^@/]+@", r"\1****@", p)


# ── Tor ──
def tor_proxy() -> str:
    return _s("tor:proxy", os.getenv("TOR_PROXY", "socks5h://127.0.0.1:9050"))


def tor_ctrl() -> Tuple[str, int]:
    return _s("tor:ctrl_host", "127.0.0.1"), int(_s("tor:ctrl_port", os.getenv("TOR_CONTROL_PORT", "9051")) or 9051)


def tor_auto() -> bool:
    return _s("tor:auto", "1") == "1"


def tor_running() -> bool:
    m = re.match(r"\w+://(?:[^@]+@)?([\w.\-]+):(\d+)", tor_proxy())
    return bool(m) and port_open(m.group(1), int(m.group(2)))


def _cookie_paths() -> List[str]:
    return [_s("tor:cookie") or "", "/run/tor/control.authcookie", "/var/run/tor/control.authcookie",
            "/var/lib/tor/control_auth_cookie", os.path.expanduser("~/.tor/control_auth_cookie")]


def tor_newnym() -> str:
    """Tor-dan yeni çıxış IP-si (NEWNYM). Qaytarır: "" uğurlu, yoxdursa xəta mətni. Bloklayır — to_thread ilə."""
    wait = 10 - (time.time() - _state["newnym"])          # Tor NEWNYM-i ~10 san.-də bir qəbul edir
    if wait > 0:
        time.sleep(wait)
    host, port = tor_ctrl()
    try:
        with socket.create_connection((host, port), timeout=5) as s:
            f = s.makefile("rwb")

            def cmd(line: str) -> str:
                f.write((line + "\r\n").encode())
                f.flush()
                return f.readline().decode(errors="ignore").strip()

            pw = _s("tor:password") or os.getenv("TOR_CONTROL_PASSWORD", "")
            auth = None
            if pw:
                auth = cmd(f'AUTHENTICATE "{pw}"')
            else:
                for path in _cookie_paths():
                    if path and os.path.isfile(path):
                        try:
                            with open(path, "rb") as cf:
                                auth = cmd("AUTHENTICATE " + cf.read().hex())
                            break
                        except OSError:
                            continue
                if auth is None:
                    auth = cmd("AUTHENTICATE")
            if not auth.startswith("250"):
                return f"ControlPort girişi alınmadı ({auth[:60]}) — torrc: CookieAuthentication 1 və ya parol"
            r = cmd("SIGNAL NEWNYM")
            if not r.startswith("250"):
                return f"NEWNYM rədd edildi: {r[:60]}"
            _state["newnym"] = time.time()
            return ""
    except OSError as e:
        return f"ControlPort ({host}:{port}) açıq deyil: {e}"


# ── seçim ──
def _scope_of(kind: str) -> str:
    return {"download": "user", "user": "user", "depo": "depo", "search": "search", "telegram": "telegram"}.get(kind, "user")


def current(kind: str = "download") -> Optional[str]:
    """Bu iş üçün proxy URL-i (və ya None — birbaşa)."""
    m = mode()
    if m == "off" or not scope_on(_scope_of(kind)):
        return None
    if m == "tor":
        return tor_proxy()
    lst = proxies()
    if not lst:
        return None
    now = time.time()
    ok = [p for p in lst if _state["bad"].get(p, 0) < now] or lst      # hamısı "pis"dirsə — yenə sına
    if rotate_mode() == "round":
        _state["idx"] = (_state["idx"] + 1) % len(ok)
    return ok[_state["idx"] % len(ok)]


def is_bad(p: str) -> bool:
    """Proxy hazırda xəta səbəbilə kənardadırmı (BAD_FOR müddəti)."""
    return _state["bad"].get(p, 0) > time.time()


def ytdlp_opts(kind: str = "download") -> Dict[str, str]:
    p = current(kind)
    return {"proxy": p} if p else {}


def telethon_proxy() -> Optional[Dict[str, Any]]:
    """Telethon TelegramClient üçün proxy konfiqurasiyası qaytarır (SOCKS5/HTTP/Tor)."""
    p = current("telegram")                      # yalnız 📱 Telegram əhatəsi — YouTube proxy-si Telethon-a keçmir
    if not p:
        return None
    m = re.match(r"^(socks5h?|socks4a?|https?|socks5)://(?:([^:@]+)(?::([^@]+))?@)?([\w.\-]+):(\d+)$", p, re.I)
    if not m:
        return None
    scheme, user, pwd, host, port = m.groups()
    scheme = scheme.lower()
    ptype = "socks5" if "socks5" in scheme else ("socks4" if "socks4" in scheme else "http")
    conf: Dict[str, Any] = {
        "proxy_type": ptype,
        "addr": host,
        "port": int(port),
    }
    if user:
        conf["username"] = user
    if pwd:
        conf["password"] = pwd
    if "socks5h" in scheme or "tor" in p:
        conf["rdns"] = True
    return conf


def report(p: Optional[str], ok: bool, blocked: bool = False) -> None:
    """Nəticə: uğursuz / 403 proxy kənara qoyulur, sabit rejimdə növbətisinə keçilir."""
    if not p:
        return
    st = _state["stats"].setdefault(p, {"ok": 0, "fail": 0, "blocked": 0})
    if ok:
        st["ok"] += 1
        return
    st["fail"] += 1
    if blocked:
        st["blocked"] += 1
    if mode() == "proxy":
        _state["bad"][p] = time.time() + BAD_FOR
        _state["idx"] += 1


def on_blocked() -> str:
    """Bütün cəhdlər 403 — Tor-da yeni IP, proxy-də növbəti. Qaytarır: görülən iş (boş — heç nə)."""
    m = mode()
    if m == "tor" and tor_auto():
        err = tor_newnym()
        return "🧅 Tor yeni IP aldı" if not err else ""
    if m == "proxy" and len(proxies()) > 1:
        return "🌐 növbəti proxy"
    return ""


def stats() -> Dict[str, Any]:
    return _state["stats"]


# ── sınaq (bloklayır — to_thread ilə) ──
def probe(p: Optional[str], timeout: int = 15) -> Dict[str, Any]:
    """Proxy-dən çıxış IP-si və YouTube-a gecikmə. yt-dlp-nin öz şəbəkə qatı ilə (SOCKS dəstəkli)."""
    from yt_dlp import YoutubeDL
    out: Dict[str, Any] = {"ok": False, "ip": None, "ms": None, "yt": None, "error": ""}
    opts = {"quiet": True, "no_warnings": True, "socket_timeout": timeout}
    if p:
        opts["proxy"] = p
    try:
        with YoutubeDL(opts) as ydl:
            t0 = time.monotonic()
            r = ydl.urlopen("https://api.ipify.org?format=json")
            out["ip"] = json.loads(r.read().decode()).get("ip")
            out["ms"] = int((time.monotonic() - t0) * 1000)
            t1 = time.monotonic()
            r = ydl.urlopen("https://www.youtube.com/generate_204")
            out["yt"] = int((time.monotonic() - t1) * 1000) if getattr(r, "status", 204) in (200, 204) else None
            out["ok"] = True
    except Exception as e:
        out["error"] = str(e).split("\n")[0][:150]
    return out
