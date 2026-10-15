"""
common.formatting
Vahid formatlama və mətn təmizləmə köməkçiləri (Bot və Userbot üçün ortaq modul).
"""

import html
import logging
import os
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def clean_title(title: str | None, max_len: int = 50) -> str:
    """Mətni Telegram HTML üçün təhlükəsiz hala salır və tələb olunan uzunluqda kəsir.
    
    Xüsusi simvollar (&, <, >) escape edilir ki, 'Can't parse entities' xətası baş verməsin.
    """
    if not title:
        return "Adsız"
    t = str(title).strip()
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return html.escape(t)


def truncate_text(text: str | None, max_len: int = 100, suffix: str = "…", normalize_spaces: bool = False) -> str:
    """Mətni verilmiş simvol sayına qədər qısaldır (normalize_spaces — boşluq/sətirləri birləşdirir)."""
    if not text:
        return ""
    s = " ".join(str(text).split()) if normalize_spaces else str(text).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)].rstrip() + suffix


def fmt_bytes(size_bytes: float, digits: int = 1, space: bool = True) -> str:
    """Bayt ölçüsü — botun vahid görünüşü: '512 B' · '2 KB' · '1.5 MB' · '3.2 GB'.
    B və KB tam ədəd, MB və yuxarısı digits onluqla. space=False — monoşrift cədvəllər üçün: '1.5MB'."""
    try:
        n = float(size_bytes or 0)
    except (ValueError, TypeError):
        n = 0.0
    n = max(n, 0.0)
    sp = " " if space else ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{sp}{unit}" if unit in ("B", "KB") else f"{n:.{digits}f}{sp}{unit}"
        n /= 1024
    return f"{n:.{digits}f}{sp}TB"


def fmt_duration(seconds: float, full: bool = False) -> str:
    """Saniyəni SS:DD və ya HH:SS:DD vaxt formatına çevirir."""
    try:
        sec = max(0, int(seconds))
    except (ValueError, TypeError):
        return "00:00"

    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)

    if h > 0 or full:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def ago(ts: float, now: float | None = None) -> str:
    """Nisbi vaxt: 'indicə' (1 dəq-dən az) · '5 dəq əvvəl' · '2 saat əvvəl' · '3 gün əvvəl' · '2 ay əvvəl'.
    Boş vaxt → 'heç vaxt'."""
    if not ts:
        return "heç vaxt"
    try:
        diff = max(0, int((now or time.time()) - float(ts)))
    except (ValueError, TypeError):
        return "naməlum"
    if diff < 60:
        return "indicə"
    if diff < 3600:
        return f"{diff // 60} dəq əvvəl"
    if diff < 86400:
        return f"{diff // 3600} saat əvvəl"
    if diff < 86400 * 30:
        return f"{diff // 86400} gün əvvəl"
    return f"{diff // (86400 * 30)} ay əvvəl"


def fmt_date(ts: float, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Zaman damğasını formatlaşdırılmış tarix sətrinə çevirir."""
    try:
        dt = datetime.fromtimestamp(float(ts), get_tz())
        return dt.strftime(fmt)
    except Exception:
        return "naməlum"


def render_box(title: str, rows: Iterable[tuple[str, Any]], width: int = 32) -> str:
    """Monospace kart görünüşü (Unicode çərçivə) yaradır."""
    clean_t = str(title).strip()
    header_pad = max(0, width - len(clean_t) - 4)
    lines = [f"┌─ {clean_t} " + "─" * header_pad + "┐"]

    for label, val in rows:
        lbl_s = str(label).strip()
        val_s = str(val).strip()
        row_content = f"{lbl_s}: {val_s}"
        padding = max(1, width - len(row_content) - 2)
        lines.append(f"│ {lbl_s}:{' ' * padding}{val_s} │")

    lines.append("└" + "─" * (width) + "┘")
    return "\n".join(lines)


_DUR_UNITS = ((86400, "gün"), (3600, "saat"), (60, "dəq"), (1, "san"))


def fmt_duration_words(seconds: float | int) -> str:
    """Müddət sözlə — ən böyük iki qonşu vahid: '1 gün 3 saat' · '2 saat 15 dəq' · '1 dəq 30 san' · '45 san'.
    İkinci vahid sıfırdırsa yazılmır ('3 saat'); 0 → '0 san'."""
    sec = max(0, int(seconds or 0))
    for i, (size, name) in enumerate(_DUR_UNITS):
        if sec >= size or size == 1:
            big, rest = divmod(sec, size)
            out = f"{big} {name}"
            if i + 1 < len(_DUR_UNITS):
                small_size, small_name = _DUR_UNITS[i + 1]
                small = rest // small_size
                if small:
                    out += f" {small} {small_name}"
            return out
    return "0 san"


def sparkline(values: Iterable[float | int], empty: str = "▁") -> str:
    """Ədədlər ardıcıllığı üçün mini ASCII sparkline xətti qurur."""
    vals = list(values) if values else []
    if not vals:
        return ""
    ticks = " ▂▃▄▅▆▇█"
    mn, mx = min(vals), max(vals)
    if mn == mx:
        return ticks[3] * len(vals)
    span = mx - mn
    return "".join(ticks[min(len(ticks) - 1, int((v - mn) / span * (len(ticks) - 1)))] for v in vals)


def fmt_rate(nbytes: float, sec: float, compact: bool = False) -> str:
    """Sürət: '1.5 MB/s' / '300 KB/s'. compact=True — monoşrift cədvəllər üçün ASCII: '1.5M/s', '-'."""
    if not nbytes or sec <= 0:
        return "-" if compact else "—"
    r = nbytes / sec
    mb, kb = ("M/s", "K/s") if compact else (" MB/s", " KB/s")
    return f"{r / 1048576:.1f}{mb}" if r >= 1048576 else f"{r / 1024:.0f}{kb}"


# ───────────────────────── 🕒 Saat qurşağı ─────────────────────────
def get_tz(name: str = None):
    """Botun saat qurşağını qaytarır (default BOT_TZ və ya Asia/Baku, ehtiyat UTC+4)."""
    tz_name = name or os.getenv("BOT_TZ", "Asia/Baku")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        from datetime import timedelta, timezone
        return timezone(timedelta(hours=4))


def get_tz_name() -> str:
    return os.getenv("BOT_TZ", "Asia/Baku")


def get_tz_with_name() -> tuple:
    tz_name = get_tz_name()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name), tz_name
    except Exception:
        from datetime import timedelta, timezone
        return timezone(timedelta(hours=4)), "UTC+4"


# ───────────────────────── ⏱ Müddət və vaxt ─────────────────────────
def format_duration(seconds, is_ms: bool = False) -> str:
    try:
        total = int(seconds or 0)
        if is_ms:
            total //= 1000
        return fmt_duration(total)
    except (TypeError, ValueError) as e:
        logger.error(f"Vaxt formatlama xətası: {e}")
        return "00:00"


def format_until(until: float | int | None, tz=None) -> str:
    """Premium bitmə vaxtını formatlayır: None -> '♾ Ömürlük', əks halda '01.01.2026-dək'."""
    if until is None:
        return "♾ Ömürlük"
    tz = tz or get_tz()
    return datetime.fromtimestamp(int(until), tz).strftime("%d.%m.%Y") + "-dək"


def format_uptime(seconds: float | int, full: bool = False) -> str:
    """Saniyələri '2 gün 07:33:20' (full=True) və ya '2 gün 07:33' formatına salır."""
    sec = int(max(0, seconds or 0))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    day_str = f"{d} gün " if d else ""
    if full:
        return f"{day_str}{h:02d}:{m:02d}:{s:02d}"
    return f"{day_str}{h:02d}:{m:02d}"


fmt_uptime = format_uptime


def fmt_span(sec: float | int) -> str:
    """Qısa müddət formatı: 2g 7s 33d (kartlar və fastfetch üçün)."""
    sec = int(sec or 0)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = ([f"{d}g"] if d else []) + ([f"{h}s"] if h or d else []) + [f"{m}d"]
    return " ".join(parts) or "0d"


def format_clock(sec) -> str:
    """Müddət saat kimi: 75 → '1:15', 3725 → '1:02:05' (yayım / qeyd müddətləri)."""
    sec = max(0, int(sec or 0))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def format_ts(ts, fmt: str = "%d.%m %H:%M", empty: str = "—") -> str:
    """Unix vaxtı → botun saat qurşağında mətn; boşdursa empty."""
    return datetime.fromtimestamp(int(ts), get_tz()).strftime(fmt) if ts else empty


# ───────────────────────── 📦 Ölçü ─────────────────────────
def format_bytes(n: float | int, digits: int = 1, space: bool = True) -> str:
    """Köhnə ad — fmt_bytes ilə eyni (vahid görünüş)."""
    return fmt_bytes(n, digits, space)


human = format_bytes


def human_bytes_short(n: float | int) -> str:
    """Kompakt bayt formatı (terminal monitoru üçün): 45M, 2.4G, 512K."""
    n = float(n or 0)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit in ("G", "T") else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


# ───────────────────────── 📊 Zolaq və qrafik ─────────────────────────
def progress_bar(val: float, total: float | None = None, width: int = 10,
                 fill: str = "█", empty: str = "░", styled: bool | None = None) -> str:
    """Faizə (0..100) və ya nisbətə (val/total) uyğun dolma zolağı qaytarır.
    styled — UI zolaq üslubuna (/menu → 🎨) uyğunlaş; default: yalnız fill/empty dəyişdirilməyibsə."""
    if total is not None:
        if total <= 0:
            return empty * width
        ratio = min(max(float(val or 0) / float(total), 0.0), 1.0)
    else:
        ratio = min(max(float(val or 0) / 100.0, 0.0), 1.0)
    filled = int(round(ratio * width))
    if styled is None:
        styled = fill == "█" and empty == "░"
    if styled:
        try:
            from common.menu import get_ui_bar
            btype = get_ui_bar()
        except Exception:
            btype = "vert"
        if btype == "vert":
            return "|" * filled + " " * (width - filled)
        elif btype == "block":
            return "▰" * filled + "▱" * (width - filled)
        elif btype == "solid":
            return "■" * filled + "." * (width - filled)
    return (fill * filled) + (empty * (width - filled))


bar = progress_bar


def vertical_bar(frac: float, width: int = 10) -> str:
    """Loading bar (cari UI bar üslubuna uyğun: vert, block və ya solid)."""
    try:
        from common.menu import get_ui_bar
        btype = get_ui_bar()
    except Exception:
        btype = "vert"
    try:
        frac = max(0.0, min(1.0, float(frac or 0)))
    except (ValueError, TypeError):
        frac = 0.0
    n = round(frac * width)
    if btype == "block":
        return "▰" * n + "▱" * (width - n)
    elif btype == "solid":
        return "■" * n + "." * (width - n)
    return "|" * n + " " * (width - n)


def sparkline_zero(values, empty: str = "▁") -> str:
    """Sparkline 0-dan maksimuma (▁▂▃▄▅▆▇█); sparkline() isə min–max aralığına görə çəkir."""
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    try:
        top = max(values)
    except (TypeError, ValueError):
        top = 0
    if not top or top <= 0:
        return empty * len(values)
    return "".join(
        blocks[min(len(blocks) - 1, round(v / top * (len(blocks) - 1)))] if v else empty
        for v in values
    )


__all__ = [
    "ago",
    "clean_title",
    "fmt_bytes",
    "fmt_date",
    "fmt_duration",
    "fmt_duration_words",
    "fmt_rate",
    "render_box",
    "sparkline",
    "truncate_text",
    "get_tz",
    "get_tz_name",
    "get_tz_with_name",
    "format_duration",
    "format_until",
    "format_uptime",
    "fmt_uptime",
    "fmt_span",
    "format_clock",
    "format_ts",
    "format_bytes",
    "human",
    "human_bytes_short",
    "progress_bar",
    "bar",
    "vertical_bar",
    "sparkline_zero",
]
