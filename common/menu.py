"""
🌐 Vahid Menyu Sistemi (Unified Menu Engine) — common/menu.py

Bu modul həm 🤖 Telegram Bot (/menu), həm də 📱 Telegram Userbot (.menu / .bmenu)
üçün vahid idarəetmə panelini, statusları, düymələri, UI kod bloklarını və ekranları təmin edir.

Əsas prinsiplər:
  1) Vahid Vəziyyət (Single Source of Truth):
     - Bot statusu (istifadəçi sayı, qruplar, yükləmələr)
     - Userbot statusu (hesablar, aktiv işlər, qoşulma vəziyyəti)
     - Depo statusu (işləyir/dayanıb, növbə, skan)
     - Canlı yayım / Səsli asistant (qeydiyyat vəziyyəti, hədəflər)
     - Sistem və server (CPU, RAM, Disk, donma monitoru)
     - Audit & Təhlükəsizlik (gündəlik admin komandaları, Şəxsi Çat hesabatı)
  2) UI Kod Bloku Görünüşü:
     - Terminalsayağı, müasir və zərif monospace kartlar (<pre>...</pre>)
     - Səliqəli açar-dəyər sütunları və status nişanları
  3) İkiqat Rejim (Dual-mode rendering):
     - Inline Kart rejimi (HTML + InlineKeyboardMarkup): Botda /menu, Userbotda .menu / .bmenu
     - Mətn rejimi (Fastfetch / plain text): Userbotun inline düymə göndərə bilmədiyi çatlarda
  4) 100% Geriye Uyğunluq (Backward Compatibility):
     - menu_plugin.py, host.py, start_plugin.py, system.py və digər modulların gözlədiyi
       bütün funksiyalar (main_text, main_kb, _counts, _depo_line, vb.) buradan ixrac edilir.
"""
import contextlib
from datetime import datetime
import logging
import platform
import re
import threading
import time
from html import escape
from typing import Any, Dict, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.kv import next_of
from common.formatting import get_tz, progress_bar   # get_tz: BOT_TZ — statistika paneli ilə eyni "bu gün"

logger = logging.getLogger(__name__)

LINE = "━━━━━━━━━━━━━━━━━━"

SECTIONS: Dict[str, str] = {
    "main": "🏠 İdarə Paneli",
    "aud":  "👥 İstifadəçilər & Qruplar",
    "biz":  "💎 Premium & Maliyyə",
    "com":  "📣 Yayım & Bildirişlər",
    "mus":  "🎵 Musiqi & Media",
    "depo": "📦 Depo & Skan",
    "live": "🎙 Canlı Yayım & VC",
    "bots": "📱 Userbot & Audit",
    "sys":  "🖥 Sistem & Resurslar",
}

_GLOBAL_CONTEXT: Any = None
_COUNTS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_DL_CACHE: Dict[str, Any] = {"ts": 0.0, "day": 0, "data": None}


def set_global_context(ctx: Any) -> None:
    """Qlobal AppContext-i təyin et (menyu çağırışlarında fallback kimi istifadə olunur)."""
    global _GLOBAL_CONTEXT
    _GLOBAL_CONTEXT = ctx


def get_global_context() -> Any:
    return _GLOBAL_CONTEXT


def _resolve_ctx(ctx: Any = None) -> Any:
    return ctx or _GLOBAL_CONTEXT


def _dot(ok: bool) -> str:
    return "🟢" if ok else "🔴"


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "—"


def _module_ok(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _clean_html(s: str) -> str:
    """Mətndən HTML teqlərini təmizləyir (<pre> daxilində təmiz görünüş üçün)."""
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _short_num(n: int) -> str:
    """Rəqəmi yığcam formatla: 1200 → 1.2k, 1500000 → 1.5M."""
    if not isinstance(n, (int, float)):
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"menu:{data}" if not data.startswith(("menu:", "ubm:", "gmenu:", "umenu:", "hc:", "is:", "kb_btn:", "test_mp3:", "ui:")) else data)


def crumb(*path: str) -> str:
    """Pəncərənin başlığı + yolu: '<i>🏠 › 🖥 Sistem</i>\n📡 <b>Server</b>'."""
    if not path:
        return ""
    *parents, title = path
    icon, _, name = title.partition(" ")
    h_icon = icon + " " if icon and not icon[0].isalnum() else ""
    h_name = name or title
    head = f"{h_icon}<b>{h_name}</b>"
    if not parents:
        return head
    p_names = []
    for p in parents:
        _, _, pn = p.partition(" ")
        p_names.append(pn or p)
    trail = " › ".join(p_names)
    return f"<i>🏠 › {trail}</i>\n{head}"


UI_STYLES = ("box", "script", "quote")
UI_STYLE_LABELS = {
    "box": "📋 Kod bloku",
    "script": "📜 Script sitat",
    "quote": "💬 Sitat",
}
UI_STYLE_DESCS = {
    "box": "Üslub: monoşrift kod bloku [pre], qalın başlıq",
    "script": "Üslub: qatlanan monospace sitat [expandable code]",
    "quote": "Üslub: şaquli xətli monospace sitat [code quote]",
}

UI_BAR_STYLES = ("vert", "block", "solid")
UI_BAR_LABELS = {
    "vert": "▎ Xətti bar [| ]",
    "block": "▰ Blok bar [▰▱]",
    "solid": "■ Bütöv bar [■.]",
}
UI_BAR_DESCS = {
    "vert": "Bar: xətlər (|)",
    "block": "Bar: bloklar (▰)",
    "solid": "Bar: bütöv (■)",
}

_UI_STYLE_OVERRIDE: str | None = None
_UI_BAR_OVERRIDE: str | None = None
_BLOCK_TAGS_RE = re.compile(
    r"<(?:pre|blockquote(?: [^>]*)?)>\s*(?:<code(?: [^>]*)?>)?([\s\S]*?)(?:</code>)?\s*</(?:pre|blockquote)>"
)


def get_ui_style() -> str:
    global _UI_STYLE_OVERRIDE
    if _UI_STYLE_OVERRIDE:
        return _UI_STYLE_OVERRIDE
    try:
        from common.database import get_db
        db = get_db()
        if db:
            val = db.get_setting("ui_style")
            if val in UI_STYLES:
                return val
    except Exception as _e:
        logger.debug("udulan xəta [common/menu.py:182]: %r", _e)
    return "box"


def set_ui_style(style: str) -> None:
    global _UI_STYLE_OVERRIDE
    _UI_STYLE_OVERRIDE = style
    try:
        from common.database import get_db
        db = get_db()
        if db:
            db.set_setting("ui_style", style)
    except Exception as _e:
        logger.debug("udulan xəta [common/menu.py:195]: %r", _e)


def get_ui_bar() -> str:
    global _UI_BAR_OVERRIDE
    if _UI_BAR_OVERRIDE:
        return _UI_BAR_OVERRIDE
    try:
        from common.database import get_db
        db = get_db()
        if db:
            val = db.get_setting("ui_bar")
            if val in UI_BAR_STYLES:
                return val
    except Exception as _e:
        logger.debug("udulan xəta [common/menu.py:210]: %r", _e)
    return "vert"


def set_ui_bar(bar_type: str) -> None:
    global _UI_BAR_OVERRIDE
    _UI_BAR_OVERRIDE = bar_type
    try:
        from common.database import get_db
        db = get_db()
        if db:
            db.set_setting("ui_bar", bar_type)
    except Exception as _e:
        logger.debug("udulan xəta [common/menu.py:223]: %r", _e)


def cycle_ui_bar() -> str:
    next_bar = next_of(UI_BAR_STYLES, get_ui_bar())
    set_ui_bar(next_bar)
    return next_bar


def wrap_ui_tags(style: str | None = None) -> tuple[str, str]:
    if style is None:
        style = get_ui_style()
    if style == "script":
        return "<blockquote expandable><code>", "</code></blockquote>"
    if style == "quote":
        return "<blockquote><code>", "</code></blockquote>"
    return "<pre>", "</pre>"


def wrap_ui_text(content: str, style: str | None = None) -> str:
    open_tag, close_tag = wrap_ui_tags(style)
    return f"{open_tag}{content}{close_tag}"


def adapt_ui_markup(text: str, style: str | None = None) -> str:
    tag_open, tag_close = wrap_ui_tags(style)

    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        # Strip any stray code/pre/blockquote tags inside inner to prevent tag mismatch
        inner = re.sub(r"</?(?:code|pre|blockquote(?: [^>]*)?)>", "", inner)
        return f"{tag_open}{inner}{tag_close}"

    return _BLOCK_TAGS_RE.sub(_repl, text)


def cycle_ui_style() -> str:
    next_style = next_of(UI_STYLES, get_ui_style())
    set_ui_style(next_style)
    return next_style


def ui_button_label(style: str | None = None) -> str:
    if style is None:
        style = get_ui_style()
    return f"📋 Görünüş: {UI_STYLE_LABELS.get(style, style)}"


def _disp_len(s: str) -> int:
    return len(s)


def _pad_col(s: str, width: int) -> str:
    return s.ljust(width)


def _bar_box(frac: float, width: int = 10, bar_type: str | None = None) -> str:
    try:
        frac = max(0.0, min(1.0, float(frac or 0)))
    except (ValueError, TypeError):
        frac = 0.0
    btype = bar_type or get_ui_bar()
    n = round(frac * width)
    if btype == "block":
        bar = "▰" * n + "▱" * (width - n)
    elif btype == "solid":
        bar = "■" * n + "." * (width - n)
    else:
        bar = "|" * n + " " * (width - n)
    return f"[{bar}]"


def format_table(rows: list, col_widths: tuple = (7, 16)) -> list:
    w1, w2 = col_widths
    return [f"{_pad_col(tag, w1)}{_pad_col(stat, w2)}{det}" for tag, stat, det in rows]


def render_card(head: str, lines: list, legend: str = "") -> str:
    head_lines = [h.strip() for h in (head or "").split("\n") if h.strip()]
    title = head_lines[0] if head_lines else ""
    extra_head = head_lines[1:] if len(head_lines) > 1 else []

    clean_lines = []
    if extra_head:
        for eh in extra_head:
            clean_lines.append(_clean_html(eh))
        if lines:
            clean_lines.append("──────────────────────────────────────")
    for l in lines:
        clean_lines.append(_clean_html(l))

    if legend:
        clean_leg = _clean_html(legend)
        if clean_leg:
            clean_lines.append("──────────────────────────────────────")
            for leg_line in clean_leg.splitlines():
                leg_line = leg_line.strip()
                if leg_line:
                    clean_lines.append(f"ℹ️ {leg_line}")

    content = escape(chr(10).join(clean_lines))
    block = wrap_ui_text(content)
    return f"{title}\n{block}" if title else block


# Bu önəklərdən biri ilə başlayan callback olduğu kimi qalır, qalanına "menu:" əlavə olunur.
MENU_CB_PREFIXES = ("menu:", "hc:", "kb_btn:", "ui:", "is:", "df:", "dc:", "hg:")


def menu_cb(data: str) -> str:
    """Naviqasiya düymələrinin callback-i: tanınan önəkli olduğu kimi, qısa ad ("sys") — "menu:sys"."""
    return data if data.startswith(MENU_CB_PREFIXES) else f"menu:{data}"


def nav_row(
    back: str = "main",
    refresh: str = "",
    to: str = "",
    extra: InlineKeyboardButton | None = None,
    *,
    include_home: bool = True,
    include_close: bool = True,
) -> list:
    """Standart vahid naviqasiya sətri:
    - 1-ci səviyyə (back == 'main'): [🔄 Yenilə] [🏠 Əsas Menyu] [✖️ Bağla]
    - 2+ səviyyə (back != 'main'): [🔄 Yenilə] [⬅️ Geri] [🏠 Menyu] [✖️ Bağla]
    """
    row = []
    if refresh:
        row.append(InlineKeyboardButton(text="🔄 Yenilə", callback_data=menu_cb(refresh)))
    if back == "main":
        row.append(InlineKeyboardButton(text="🏠 Əsas Menyu", callback_data="menu:main"))
    else:
        b_txt = f"⬅️ {to}" if to else "⬅️ Geri"
        row.append(InlineKeyboardButton(text=b_txt, callback_data=menu_cb("back" if back in ("auto", "back") else back)))
        if include_home:
            row.append(InlineKeyboardButton(text="🏠 Menyu", callback_data="menu:main"))
    if include_close:
        row.append(InlineKeyboardButton(text="✖️ Bağla", callback_data="menu:cancel"))
    if extra:
        row.append(extra)
    return row


def is_creator(user_id: int, ctx: Any = None) -> bool:
    from common.permissions import is_creator as _is_creator
    return _is_creator(user_id, ctx)


# ═════════════════════════════════════════════════════════════
# 📊 Canlı Monitor Formatlayıcısı (Depo Monitoru Üslubunda)
# ═════════════════════════════════════════════════════════════

def _bar(frac: float, width: int = 10) -> str:
    """Zolaq (mötərizəsiz): seçilmiş UI zolaq üslubunda (/ui → bar) — | / ▰▱ / ■."""
    return _bar_box(frac, width)[1:-1]


def format_ui_box(title: str, sections: list[Tuple[str, list[Any]]], max_width: int = 42) -> str:
    """Depo canlı monitoru (depotop) üslubunda təmiz terminal / monitor formatlayıcısı."""
    lines = []
    for sec_title, items in sections:
        clean_sec = _clean_html(sec_title).strip()
        if lines:
            lines.append("")
        lines.append(f"# {clean_sec}")
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str):
                k = _clean_html(str(item[0])).strip()
                v = _clean_html(str(item[1])).strip()
                lines.append(f"  {k:<16} {v}")
            else:
                s = _clean_html(str(item)).strip()
                lines.append(f"  {s}")
    code_content = escape("\n".join(lines))
    return f"{title}\n<pre>{code_content}</pre>"


# ═════════════════════════════════════════════════════════════
# 📊 Status və Statistika Məlumatları
# ═════════════════════════════════════════════════════════════

def _today_start() -> int:
    now = datetime.now(get_tz())
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _counts(force: bool = False) -> dict:
    now = time.time()
    if not force and _COUNTS_CACHE["data"] is not None and now - _COUNTS_CACHE["ts"] < 30:
        return _COUNTS_CACHE["data"]
    try:
        from common.database import get_db
        db = get_db()
        res = {
            "users": _safe(db.user_counts, {"total": 0, "active": 0, "blocked": 0}) if db else {"total": 0, "active": 0, "blocked": 0},
            "chats": _safe(db.chat_counts, {"groups": 0, "channels": 0}) if db else {"groups": 0, "channels": 0},
            "prem": _safe(db.premium_count, 0) if db else 0,
        }
    except Exception:
        res = {"users": {"total": 0, "active": 0, "blocked": 0}, "chats": {"groups": 0, "channels": 0}, "prem": 0}
    _COUNTS_CACHE["ts"] = now
    _COUNTS_CACHE["data"] = res
    return res


def _download_stats(day_start: int, now: int, force: bool = False) -> dict:
    if not force and _DL_CACHE["data"] is not None and (now - _DL_CACHE["ts"] < 30) and _DL_CACHE["day"] == day_start:
        return _DL_CACHE["data"]
    try:
        from common.database import get_db
        db = get_db()
        res = _safe(lambda: db.download_stats(day_start, now), {}) if db else {}
    except Exception:
        res = {}
    _DL_CACHE["ts"] = now
    _DL_CACHE["day"] = day_start
    _DL_CACHE["data"] = res
    return res


def _depo_line(ctx: Any = None) -> str:
    c = _resolve_ctx(ctx)
    filler = getattr(c, "depo_filler", None) if c else None
    if filler is None:
        return "📦 Depo: <i>plugin yüklənməyib</i>"
    try:
        on = filler.running
        stall = filler.stall_reason() if hasattr(filler, "stall_reason") else ""
        state = "⚠️ ilişib" if on and stall else ("🟢 işləyir" if on else "🔴 söndürülüb")
        return f"📦 Depo: {state} · 📋 {_short_num(len(filler.queue))} növbədə"
    except Exception:
        return "📦 Depo"


def _userbot_line(ctx: Any = None) -> str:
    c = _resolve_ctx(ctx)
    if c and not getattr(c, "userbot_enabled", True):
        return "📱 Userbot: <i>söndürülüb (yalnız bot rejimi)</i>"

    fn1 = getattr(c, "is_ub1_active", None) if c else None
    ub1_on = fn1() if fn1 is not None else False
    c1 = getattr(c, "telethon_client", None) if c else None
    me1 = getattr(c, "telethon_me", None) if c else None
    if fn1 is None and c1:
        try:
            ub1_on = bool(getattr(c, "userbot1_enabled", True) and c1.is_connected())
        except Exception:
            ub1_on = False
    name1 = escape(getattr(me1, "first_name", "") or "") if me1 else ""
    s1 = f"1️⃣ {'🟢' if ub1_on else '🔴'} {name1}" if me1 else "1️⃣ ⚪ qoşulmayıb"

    fn2 = getattr(c, "is_ub2_active", None) if c else None
    va = getattr(c, "voice_assistant", None) if c else None
    if va and hasattr(va, "has_session") and va.has_session():
        ub2_on = fn2() if fn2 is not None else bool(va.enabled and va.running)
        me2 = getattr(va, "me", None)
        name2 = escape(getattr(me2, "first_name", "") or "") if me2 else "Asistan"
        s2 = f"2️⃣ {'🟢' if ub2_on else '🔴'} {name2}"
    else:
        s2 = "2️⃣ ⚪ qoşulmayıb"

    return f"📱 Userbot: {s1} · {s2}"


def _server_line() -> str:
    try:
        from common.diagnostics import server_snapshot
        s = server_snapshot()
    except Exception:
        s = {}
    parts = [f"CPU {s['cpu']:.0f}%"] if "cpu" in s else []
    if "ram" in s:
        parts.append(f"RAM {s['ram']:.0f}%")
    if "disk" in s:
        parts.append(f"Disk {s['disk']:.0f}%")
    warn = any(s.get(k, 0) >= 90 for k in ("cpu", "ram", "disk"))
    return "🖥 Server: " + (" · ".join(parts) or "—") + (" ⚠️" if warn else "")


def _alerts(ctx: Any = None) -> list:
    out = []
    c = _resolve_ctx(ctx)
    filler = getattr(c, "depo_filler", None) if c else None
    if filler is not None:
        stall = _safe(lambda: filler.stall_reason() if hasattr(filler, "stall_reason") else "", "")
        if stall:
            out.append(f"📦 Depo ilişib: {escape(str(stall))[:60]} → 📦 Depo")
        hp = getattr(filler, "helpers", None)
        if hp and hasattr(hp, "helpers"):
            bad = len(hp.helpers) - len(hp.active())
            if bad:
                out.append(f"🤖 {bad} köməkçi bot problemlidir → 📦 Depo")
    with contextlib.suppress(Exception):
        from bot.core import cpu_pool as cp
        if hasattr(cp, "WATCHDOG") and cp.WATCHDOG.enabled and cp.WATCHDOG.snapshot().get("freezes_hour"):
            out.append(f"🧊 Son 1 saatda {cp.WATCHDOG.snapshot()['freezes_hour']} donma → 🖥 Sistem")
    return out


# ═════════════════════════════════════════════════════════════
# 🏷 Dinamik Düymə Etiketləri
# ═════════════════════════════════════════════════════════════

def _mus_label(ctx: Any = None) -> str:
    return SECTIONS["mus"]


def _depo_label(ctx: Any = None) -> str:
    c = _resolve_ctx(ctx)
    filler = getattr(c, "depo_filler", None) if c else None
    if filler is None:
        return SECTIONS["depo"]
    parts = ["🟢" if _safe(lambda: filler.running, False) else "🔴"]
    hp = getattr(filler, "helpers", None)
    if hp and hasattr(hp, "helpers"):
        bad = len(hp.helpers) - len(hp.active())
        parts.append(f"🤖{len(hp.active())}/{len(hp.helpers)}" + (" ⚠️" if bad else ""))
    return SECTIONS["depo"] + " · " + " ".join(parts)


def _live_label(ctx: Any = None) -> str:
    c = _resolve_ctx(ctx)
    va = getattr(c, "voice_assistant", None) if c else None
    if va is None:
        return SECTIONS["live"] + " · ❌"
    n_rec = len(getattr(getattr(va, "rec", None), "sessions", {}) or {})
    return SECTIONS["live"] + (f" · 🔴 {n_rec} qeyd" if n_rec else "")


def _bots_label(ctx: Any = None) -> str:
    c = _resolve_ctx(ctx)
    parts = []
    if c and getattr(c, "userbot_enabled", True):
        fn = getattr(c, "is_ub1_active", None)
        if fn is not None:
            ub1_on = fn()
        else:
            client = getattr(c, "telethon_client", None)
            try:
                ub1_on = bool(getattr(c, "userbot1_enabled", True) and client and client.is_connected())
            except Exception:
                ub1_on = False
        parts.append("📱" + ("🟢" if ub1_on else "🔴"))

        va = getattr(c, "voice_assistant", None)
        if va and hasattr(va, "has_session") and va.has_session():
            fn2 = getattr(c, "is_ub2_active", None)
            ub2_on = fn2() if fn2 is not None else bool(va.enabled and va.running)
            parts.append("🎙" + ("🟢" if ub2_on else "🔴"))
    else:
        parts.append("⚪")
    return SECTIONS["bots"] + (" · " + " ".join(parts) if parts else "")


def _sys_label(ctx: Any = None) -> str:
    warn = False
    with contextlib.suppress(Exception):
        from common.diagnostics import server_snapshot
        s = server_snapshot()
        warn = any(s.get(k, 0) >= 90 for k in ("cpu", "ram", "disk"))
    return SECTIONS["sys"] + (" · ⚠️" if warn else "")


# ═════════════════════════════════════════════════════════════
# 🖼 Vahid Menyu Görünüşləri (Dashboard Generator)
# ═════════════════════════════════════════════════════════════

def main_text(ctx: Any = None, force: bool = False) -> str:
    """Vahid Menyu Mətni (Depo Canlı Monitoru üslubunda həm Bot, həm Userbot üçün)."""
    now = int(time.time())
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    counts = _counts(force=force)
    dl = _download_stats(_today_start(), now, force=force)

    # Server snapshot
    try:
        from common.diagnostics import server_snapshot
        s = server_snapshot()
        cpu_val = s.get('cpu', 0)
        ram_val = s.get('ram', 0)
        disk_val = s.get('disk', 0)
        l = s.get('load')
        load_val = " ".join(f"{x:.2f}" for x in l) if isinstance(l, (list, tuple)) else str(l or '—')
        uptime_val = s.get('uptime_str', '—')
    except Exception:
        cpu_val = ram_val = disk_val = 0
        load_val = uptime_val = '—'

    u = counts.get('users', {})
    ch = counts.get('chats', {})
    prem = counts.get('prem', 0)

    u_tot = _short_num(u.get('total', 0))
    u_act = _short_num(u.get('active', 0))
    u_blk = _short_num(u.get('blocked', 0))

    c_grp = _short_num(ch.get('groups', 0))
    c_chn = _short_num(ch.get('channels', 0))

    dl_today = _short_num(dl.get('today', 0))
    dl_week = _short_num(dl.get('week', 0))
    dl_total = _short_num(dl.get('total', 0))

    c = _resolve_ctx(ctx)
    filler = getattr(c, "depo_filler", None) if c else None
    if filler:
        on_depo = getattr(filler, "running", False)
        q_len = len(getattr(filler, "queue", []))
        depo_st = f"🟢 işləyir ({q_len} növbə)" if on_depo else f"🔴 dayanıb ({q_len} növbə)"
    else:
        depo_st = "⚪ boş"

    ub = getattr(c, "userbot", None) if c else None
    ub_on = ub is not None and getattr(ub, "client", None) is not None and ub.client.is_connected()
    ub_st = "🟢 1️⃣ UB1 qoşulub" if ub_on else "⚪ UB1 sönülü"

    va = getattr(c, "voice_assistant", None) if c else None
    va_rec = len(getattr(getattr(va, "rec", None), "sessions", {}) or {}) if va else 0
    va_st = f"🔴 {va_rec} qeyd" if va_rec else "⚪ boş"

    head = f"🛠 <b>dllmaster panel</b> · 🟢 onlayn · <b>v2.4</b>\n<i>{now_str} · 🔄 canlı monitor</i>"

    lines = [
        f"Sys [{_bar(cpu_val / 100, 10)}] cpu {cpu_val:.0f}%  ram {ram_val:.0f}%  dsk {disk_val:.0f}%",
        f"Dwn [{_bar(dl.get('today', 0) / max(dl.get('week', 1), 1), 10)}] gün {dl_today}  7g {dl_week}  cəmi {dl_total}",
        f"Usr cəmi {u_tot}  akt {u_act}  blok {u_blk}  prem {prem}",
        f"Grp qrup {c_grp}  kanal {c_chn}  aktiv 🟢",
        f"Dep {depo_st}",
        f"Ubt {ub_st}  yayım {va_st}",
        f"Up  {uptime_val}  load {load_val}",
    ]

    extra_lines = []
    try:
        from common import maintenance
        if maintenance.is_active():
            extra_lines.append("⚠️ <b>DİQQƏT: Bot hazırda Texniki İş Rejimindədir (Maintenance)!</b>"
                               + ("" if maintenance.is_blocking() else " <i>(xəbərdarlıq — hamı istifadə edir)</i>"))
    except Exception as _e:
        logger.debug("udulan xəta [common/menu.py:660]: %r", _e)

    alerts = _alerts(ctx)
    if alerts:
        extra_lines.append(f"⚠️ <b>Diqqət ({len(alerts)}):</b>")
        extra_lines.extend([f"• {a}" for a in alerts[:5]])

    legend = "<i>Sys sistem · Dwn yükləmələr · Usr istifadəçilər · Grp qruplar · Dep depo · Ubt userbot</i>"
    body = f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"
    if extra_lines:
        body += "\n\n" + "\n".join(extra_lines)
    return body


def main_kb(ctx: Any = None, prefix: str = "menu") -> InlineKeyboardMarkup:
    """Vahid Menyu Klaviaturası — yenidən tərtib edilmiş simmetrik 2x2 düzülüş."""
    rows = [
        [_btn(SECTIONS["aud"], f"{prefix}:aud"), _btn(SECTIONS["mus"], f"{prefix}:mus")],
        [_btn(_depo_label(ctx), f"{prefix}:depo"), _btn(_live_label(ctx), f"{prefix}:live")],
        [_btn(SECTIONS["biz"], f"{prefix}:biz"), _btn(SECTIONS["com"], f"{prefix}:com")],
        [_btn(_bots_label(ctx), f"{prefix}:bots"), _btn(_sys_label(ctx), f"{prefix}:sys")],
        [_btn("🔄 Yenilə", f"{prefix}:main"), _btn("❓ Bələdçi", f"{prefix}:help"), _btn("✖️ Bağla", f"{prefix}:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ═════════════════════════════════════════════════════════════
# 📋 Bölmələr Üzrə Canlı Monitor İcmalları (Depo Monitoru Üslubunda)
# ═════════════════════════════════════════════════════════════

def sys_overview_text(ctx: Any = None) -> str:
    """🖥 Sistem & Resurslar bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    try:
        from common.diagnostics import server_snapshot, project_storage_snapshot
        srv = server_snapshot()
        stor = project_storage_snapshot()
        cpu_val = srv.get('cpu', 0)
        ram_val = srv.get('ram', 0)
        disk_val = srv.get('disk', 0)
        rss = stor.get('proc_rss_str', '—')
        tot_sz = stor.get('total_size_str', '—')
        files = stor.get('total_files', 0)
        l = srv.get('load')
        load = " ".join(f"{x:.2f}" for x in l) if isinstance(l, (list, tuple)) else str(l or '—')
        uptime = srv.get('uptime_str', '—')
    except Exception:
        cpu_val = ram_val = disk_val = 0
        rss = tot_sz = load = uptime = '—'
        files = 0

    try:
        from common import maintenance
        maint = ("🛠 aktiv" if maintenance.is_blocking() else "⚠️ xəbərdarlıq") if maintenance.is_active() else "🟢 normal"
    except Exception:
        maint = "🟢 normal"

    head = f"🖥 <b>sistem monitoru</b> · 🟢 onlayn · <b>{load}</b>\n<i>{now_str} · {uptime}</i>"
    lines = [
        f"Cpu [{_bar(cpu_val / 100, 10)}] {cpu_val:.0f}%  load {load}",
        f"Ram [{_bar(ram_val / 100, 10)}] {ram_val:.0f}%  bot {rss}",
        f"Dsk [{_bar(disk_val / 100, 10)}] {disk_val:.0f}%  layihə {tot_sz} ({files} f.)",
        f"Mod rejim {maint}  nəzarətçi 🟢 aktiv",
        f"Pro python {platform.python_version()}  telethon 1.42",
        f"Thr aktiv {threading.active_count()}  hovuz 🟢 normal",
    ]
    legend = "<i>Cpu prosessor · Ram operativ yaddaş · Dsk disk sahəsi · Mod iş rejimi · Pro mühit</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def music_overview_text(ctx: Any = None) -> str:
    """🎵 Musiqi & Media bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    try:
        from common.database import get_auto_hide_duration, is_audio_button_enabled, creator_caption_enabled
        btn_sec = get_auto_hide_duration()
        timer_txt = "daimi" if btn_sec == 0 else f"{btn_sec}s"
        mix_s = "ok" if is_audio_button_enabled("mix") else "off"
        plus_s = "ok" if is_audio_button_enabled("plus") else "off"
        hide_s = "ok" if is_audio_button_enabled("hide") else "off"
        cap_s = "açıq" if creator_caption_enabled() else "bağlı"
    except Exception:
        timer_txt, mix_s, plus_s, hide_s, cap_s = "daimi", "ok", "ok", "ok", "bağlı"

    head = f"🎵 <b>musiqi monitoru</b> · 🟢 aktiv · <b>media mühərriki</b>\n<i>{now_str} · audio xidmətləri</i>"
    lines = [
        "Src YouTube 🟢  Spotify 🟢  Shazam 🟢",
        f"Btn mix {mix_s}  playlist {plus_s}  gizlət {hide_s}  taymer {timer_txt}",
        f"Cap imza {cap_s}  keyfiyyət 320k MP3",
        "Ply canlı yayım /play 🟢  səsli pleyer hazır",
        "Tag mutagen/ffmpeg teq redaktoru /edit 🟢",
    ]
    legend = "<i>Src mənbələr · Btn mahnı düymələri · Cap audio caption · Ply yayım · Tag teqlər</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def depo_overview_text(ctx: Any = None) -> str:
    """📦 Depo & Skan bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _resolve_ctx(ctx)
    filler = getattr(c, "depo_filler", None) if c else None
    if filler:
        on = getattr(filler, "running", False)
        q_len = len(getattr(filler, "queue", []))
        st = "🟢 işləyir" if on else "🔴 dayanıb"
        hp = getattr(filler, "helpers", None)
        if hp and hasattr(hp, "helpers"):
            hp_str = f"{len(hp.active())}/{len(hp.helpers)} aktiv"
            rate_str = f"{hp.rate}/dəq"
        else:
            hp_str = rate_str = "—"
        st_data = getattr(filler, "stats", {})
        done_cnt = st_data.get("done", 0)
        skip_cnt = st_data.get("cached", 0)
        err_cnt = st_data.get("failed", 0)
    else:
        st, q_len, hp_str, rate_str = "🔴 söndürülüb", 0, "—", "—"
        done_cnt = skip_cnt = err_cnt = 0

    head = f"📦 <b>depo xülasəsi</b> · {st} · <b>{q_len} növbə</b>\n<i>{now_str} · arxiv doldurucusu</i>"
    lines = [
        f"Wrk vəziyyət {st}  növbədə {q_len} mahnı",
        f"Tot yükləndi {done_cnt}  keş {skip_cnt}  xəta {err_cnt}",
        f"Hlp köməkçilər {hp_str}  temp {rate_str}",
        "Mod kanal depolama 🟢  sinxronizasiya aktiv",
    ]
    legend = "<i>Wrk işçi statusu · Tot ümumi statistika · Hlp köməkçi botlar · Mod depo arxivi</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def live_overview_text(ctx: Any = None) -> str:
    """🎙 Canlı Yayım & VC bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _resolve_ctx(ctx)
    va = getattr(c, "voice_assistant", None) if c else None
    va_str = "🟢 aktiv" if (va and getattr(va, "running", False)) else "🔴 deaktiv"
    n_rec = len(getattr(getattr(va, "rec", None), "sessions", {}) or {}) if va else 0
    rec_str = f"🔴 {n_rec} aktiv" if n_rec else "⚪ qeyd getmir"

    head = f"🎙 <b>canlı yayım monitoru</b> · {va_str} · <b>VC asistan</b>\n<i>{now_str} · səsli söhbət</i>"
    lines = [
        f"Ast asistan {va_str}  səsli söhbət qoşulması hazır",
        f"Rec yayım qeydi {rec_str} (vrec)",
        "Cmd /play · /vrec · /join · /leave · /volume",
        "Dst hədəf Saved Messages və ya Audit kanalı",
    ]
    legend = "<i>Ast asistan vəziyyəti · Rec yayım qeydiyyatı · Cmd komandalar · Dst qeyd ünvanı</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def bots_overview_text(ctx: Any = None) -> str:
    """📱 Userbot & Audit bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _resolve_ctx(ctx)
    ub = getattr(c, "userbot", None) if c else None
    ub1_on = ub is not None and getattr(ub, "client", None) is not None and ub.client.is_connected()
    ub1_str = "🟢 aktiv" if ub1_on else "⚪ sönülü"

    va = getattr(c, "voice_assistant", None) if c else None
    ub2_on = va is not None and getattr(va, "running", False)
    ub2_str = "🟢 aktiv" if ub2_on else "⚪ sönülü"

    try:
        from common.database import get_userbot_inline_disabled_chats
        dis_chats = len(get_userbot_inline_disabled_chats())
    except Exception:
        dis_chats = 0

    head = f"📱 <b>userbot & audit monitoru</b> · 🟢 aktiv · <b>telethon</b>\n<i>{now_str} · sessiyalar və loqlar</i>"
    lines = [
        f"Ub1 1️⃣ əsas hesab (userbot) {ub1_str}",
        f"Ub2 2️⃣ asistan hesabı (vc)  {ub2_str}",
        "Aud qrup auditi 🟢 aktiv  gündəlik HTML hesabat 23:59",
        f"Inl inline söndürülmüş çatlar: {dis_chats} ədəd (.inline)",
        "Cmd .ban · .mute · .purge · .pin · .info · .storage",
    ]
    legend = "<i>Ub1 əsas userbot · Ub2 asistan · Aud qrup auditi · Inl inline filtri · Cmd əmrlər</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def aud_overview_text(ctx: Any = None) -> str:
    """👥 İstifadəçilər & Qruplar bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _counts()
    u, ch = c["users"], c["chats"]
    u_tot = u.get("total", 0)
    u_act = u.get("active", 0)
    u_blk = u.get("blocked", 0)
    c_grp = ch.get("groups", 0)
    c_chn = ch.get("channels", 0)
    prm = c.get("prem", 0)

    act_ratio = (u_act / max(u_tot, 1)) if u_tot else 0

    head = f"👥 <b>auditoriya monitoru</b> · 🟢 aktiv · <b>{_short_num(u_tot)} üzv</b>\n<i>{now_str} · icma statistikası</i>"
    lines = [
        f"Usr [{_bar(act_ratio, 10)}] cəmi {u_tot}  akt {u_act}  blok {u_blk}",
        f"Grp qruplar {c_grp}  kanallar {c_chn}  ümumi {c_grp + c_chn}",
        f"Prm premium üzvlər {prm}  stars abunəliyi",
        "Opt axtarış · CSV ixrac · filtrasiya · qrup ayarları",
    ]
    legend = "<i>Usr istifadəçi nisbəti · Grp çat sayı · Prm premium · Opt idarəetmə</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def biz_overview_text(ctx: Any = None) -> str:
    """💎 Premium & Maliyyə bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _counts()
    from common.database import is_tip_prompt_enabled
    tip_s = "açıq" if is_tip_prompt_enabled() else "bağlı"
    prem = c.get("prem", 0)

    head = f"💎 <b>premium & maliyyə</b> · 🟢 aktiv · <b>Stars</b>\n<i>{now_str} · abunəlik və tariflər</i>"
    lines = [
        f"Prm aktiv premium istifadəçilər: {prem} nəfər",
        "Pay Telegram Stars (⭐) rəsmi ödəniş mühərriki",
        f"Tip yükləmədən sonra bəxşiş təklifi: {tip_s}",
        "Pln 1 ay · 3 ay · 6 ay · 12 ay · ömürlük VIP",
        "Ben limitsiz sürət · təmiz audio · caption nəzarəti",
    ]
    legend = "<i>Prm premium status · Pay ödəniş · Tip bəxşiş · Pln planlar · Ben üstünlüklər</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


def com_overview_text(ctx: Any = None) -> str:
    """📣 Yayım & Bildirişlər bölməsinin canlı monitor paneli."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    c = _counts()
    u_act = c["users"]["active"]
    c_grp = c["chats"]["groups"]
    c_chn = c["chats"].get("channels", 0)

    head = f"📣 <b>yayım monitoru</b> · 🟢 hazır · <b>broadcast</b>\n<i>{now_str} · kütləvi çatdırılma</i>"
    lines = [
        f"Aud istifadəçilər {_short_num(u_act)}  qruplar {_short_num(c_grp)}  kanallar {_short_num(c_chn)}",
        f"Tot cəmi çatdırılma hədəfi: {_short_num(u_act + c_grp + c_chn)} çat",
        "Mod mətn · şəkil · audio · video · inline düymələr",
        "Tpl /start salamlaşma və bələdçi şablonları aktiv",
    ]
    legend = "<i>Aud hədəf auditoriya · Tot cəmi əhatə · Mod media növləri · Tpl şablonlar</i>"
    return f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"


# ═════════════════════════════════════════════════════════════
# 🗂 Layihə Yaddaşı & Keş Görünüşü
# ═════════════════════════════════════════════════════════════

def storage_view(note: str = "") -> Tuple[str, InlineKeyboardMarkup]:
    """🗂 Layihə Yaddaşı & Fayllar paneli (Depo Canlı Monitoru üslubunda)."""
    now_str = datetime.now(get_tz()).strftime("%d.%m.%Y %H:%M:%S")
    from common.diagnostics import project_storage_snapshot

    s = project_storage_snapshot()
    ram_pct = s.get("ram_pct", 0)
    disk_pct = s.get("disk_pct", 0)

    head = f"🗂 <b>yaddaş monitoru</b> · 🟢 normal · <b>{s['total_size_str']}</b>\n<i>{now_str} · {s['total_files']} fayl</i>"

    lines = [
        f"Mem [{_bar(ram_pct / 100, 10)}] bot {s['proc_rss_str']}  sis {s['ram_used_str']}/{s['ram_total_str']} ({ram_pct:.0f}%)",
        f"Dsk [{_bar(disk_pct / 100, 10)}] dsk {s['disk_used_str']}/{s['disk_total_str']} ({disk_pct:.0f}%)  layihə {s['total_size_str']}",
        "",
        "# BÖLMƏ            ÖLÇÜ     FAYL",
    ]
    for cat in s["categories"]:
        lines.append(f"  {cat['label'][:15]:<16} {cat['size_str']:>8} {cat['count']:>6}")

    lines.append("")
    lines.append("# ƏN BÖYÜK PLUGİNLƏR   ÖLÇÜ")
    for i, p in enumerate(s["top_plugins"][:5], 1):
        lines.append(f"{i:>2} {p['name'][:20]:<20} {p['size_str']:>6}")

    legend = "<i>Mem yaddaş · Dsk disk sahəsi · # kateqoriyalar və TOP böyük fayllar</i>"
    box = f"{head}\n<pre>{escape(chr(10).join(lines))}</pre>\n{legend}"
    if note:
        box += f"\n\nℹ️ {note}"

    rows = [
        [InlineKeyboardButton(text="🔄 Yenilə", callback_data="menu:storage_refresh"),
         InlineKeyboardButton(text="🧹 Keşi Təmizlə", callback_data="menu:storage_clean")],
        nav_row("sys"),
    ]
    return box, InlineKeyboardMarkup(inline_keyboard=rows)


# ═════════════════════════════════════════════════════════════
# 📱 Userbot Vahid Menyu Kartı & Fastfetch
# ═════════════════════════════════════════════════════════════

def unified_userbot_view(ub: Any, tok: str) -> Tuple[str, InlineKeyboardMarkup]:
    """Userbot üçün Vahid Menyu kartı və inline düymələri."""
    text = main_text(getattr(ub, "context", None))

    def bt(text: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"ubm:{tok}:{data}"[:64])

    rows = [
        [bt("👥 İstifadəçilər & Qruplar", "aud"), bt("🎵 Musiqi & Media", "mus")],
        [bt("📦 Depo", "depo"), bt("🎙 Canlı Yayım", "live")],
        [bt("💎 Premium", "biz"), bt("📣 Yayım", "com")],
        [bt("📱 Səs & Audit", "logger"), bt("🖥 Sistem", "sys")],
        [bt("🔄 Yenilə", "main"), bt("❓ Help", "help"), bt("✖️ Bağla", "close")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_unified_fastfetch_text(ub_or_ctx: Any) -> str:
    """Userbot üçün mətn-rejimli Fastfetch formatlı Vahid Menyu paneli.
    İnline düymə qadağan olan çatlarda avtomatik bu görünüş istifadə edilir."""
    from userbot.core.userbot_api import ff, tr

    ub = getattr(ub_or_ctx, "userbot", None) or ub_or_ctx
    c = _resolve_ctx(getattr(ub, "context", None))

    client = getattr(ub, "client", None)
    on = client is not None and client.is_connected()

    body = [
        (tr("Userbot"), tr("● qoşulub") if on else tr("○ qoşulmayıb")),
    ]

    filler = getattr(c, "depo_filler", None) if c else None
    if filler is not None:
        try:
            body.append((tr("Depo"), f"{tr('● işləyir') if filler.running else tr('○ dayanıb')} · {tr('növbə')} {len(filler.queue)}"))
        except Exception as _e:
            logger.debug("udulan xəta [common/menu.py:987]: %r", _e)

    va = getattr(c, "voice_assistant", None) if c else None
    if va is not None:
        try:
            n_rec = len(getattr(getattr(va, "rec", None), "sessions", {}) or {})
            body.append((tr("Canlı"), f"{'🔴 aktiv' if n_rec else '⚪ boş'} · {n_rec} qeyd"))
        except Exception as _e:
            logger.debug("udulan xəta [common/menu.py:995]: %r", _e)

    body.append((tr("İşlər"), f"{ub.active_tasks() if hasattr(ub, 'active_tasks') else 0} {tr('aktiv')}"))
    body.append((tr("Komanda"), f"{len(getattr(ub, 'commands', []))} · {tr('bölmə')} {len(getattr(ub, 'sections', {}))}"))

    # Əsas komandaların siyahısı
    body.append("# 🚀 Əsas Komandalar")
    body.append((".menu / .bmenu", "Vahid admin paneli"))
    body.append((".alive / .ping", "Status və gecikmə yoxlanışı"))
    body.append((".ff / .speedtest", "Sistem və şəbəkə sürəti"))
    body.append((".storage", "Layihə və pluginlərin yaddaşı"))
    body.append((".vcrec", "Səsli söhbət və yayım qeydiyyatı"))
    body.append((".purge / .clear", "Mesajların təmizlənməsi"))
    body.append((".filter / .automsg", "Açar söz və avto-cavab idarəsi"))
    body.append((".inline [on|off]", "Qrupda inline rejimi idarəsi"))

    title_str = getattr(ub, "title", lambda x: "menu")("menu")
    return ff(title_str, body, footer=tr("tam interaktiv panel üçün bot çatında /menu yazın"))


# ═════════════════════════════════════════════════════════════
# 🔁 Uyğunluq və Əlaqə Köməkçiləri
# ═════════════════════════════════════════════════════════════

sys_text = sys_overview_text
perf_overview_text = sys_overview_text
