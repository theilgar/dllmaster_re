"""
bot.menus.views.base
Menyu pəncərələri üçün ümumi etiketlər, breadcrumb, formatlama və vizual köməkçilər.
"""

from datetime import UTC, datetime
from html import escape

from aiogram.types import InlineKeyboardButton

from bot.core import panel
from common.formatting import ago, format_ts, sparkline
from common.menu import LINE, SECTIONS, crumb
from common.utilities import get_tz_with_name

PAGE_SIZE = 10
PREMIUM_PAGE_SIZE = 8
PREMIUM_DURATIONS = [("7 gün", 7), ("30 gün", 30), ("90 gün", 90), ("1 il", 365), ("♾ Ömürlük", 0)]
TRACK_THROTTLE = 60
BROADCAST_DELAY = 0.05

TARGET_LABELS = {
    "users": "👤 Şəxsi",
    "groups": "👥 Qruplar",
    "channels": "📣 Kanallar",
    "all": "🌐 Hamısı",
    "free": "🆓 Free istifadəçilər",
}

SOURCE_LABELS = {
    "music": "🔎 Axtarış (/music)",
    "inline": "⚡ İnline",
    "mix": "🔀 /mix",
    "batch": "⬇️ Toplu yükləmə",
    "spotify": "🟢 Spotify",
    "playlist": "📃 Playlist",
    "youtube": "▶️ YouTube",
    "editor": "✏️ Redaktor",
}

USER_FILTERS = [
    ("all", "👥 Hamısı"),
    ("active", "🟢 Aktiv"),
    ("new", "🆕 Yeni"),
    ("top", "🏆 Ən çox"),
    ("prem", "💎 Premium"),
    ("blocked", "🚫 Bloklayan"),
]

USERS_PAGE = 8
CHATS_PAGE = 8
CHAT_FILTERS = [("groups", "👥 Qruplar"), ("channels", "📣 Kanallar"), ("left", "🚪 Çıxılmış")]
CHAT_TYPES = {"group": "👥 Qrup", "supergroup": "👥 Superqrup", "channel": "📣 Kanal"}
BOT_STATUS = {
    "creator": "👑 Sahib",
    "administrator": "🛡 Admin",
    "member": "👤 Üzv",
    "restricted": "🔇 Məhdud",
    "left": "🚪 Çıxıb",
    "kicked": "⛔ Çıxarılıb",
}


def stats_tabs(view: str) -> list[InlineKeyboardButton]:
    """Statistika pəncərələri arasında tablar (📊 / 📈 / 🏆)."""
    def t(key: str, label: str):
        return panel.choice(label, view == key, f"menu:{key}")

    return [t("stats", "📊 Ümumi"), t("stats_chart", "📈 Qrafik"), t("stats_top", "🏆 Top")]


def tz_offset_seconds() -> int:
    """Konfiqurasiya edilmiş saat qurşağının UTC ofsetini saniyə ilə qaytarır."""
    now_utc = datetime.now(UTC)
    target_dt = now_utc.astimezone(get_tz_with_name()[0])
    return int(target_dt.utcoffset().total_seconds())


def fmt_dt(ts: int) -> str:
    """Zaman damğasını 'GG.AA SS:DD' formatına çevirir."""
    return format_ts(ts, "%d.%m %H:%M", empty="-")




def trend(cur: int, prev: int) -> str:
    """İki qiymət arasında artım/azalma faizini göstərir."""
    if prev == 0:
        return " (+100%)" if cur > 0 else ""
    diff = cur - prev
    pct_val = int(diff / prev * 100)
    if pct_val > 0:
        return f" (🟢 +{pct_val}%)"
    if pct_val < 0:
        return f" (🔴 {pct_val}%)"
    return " (⚪ 0%)"


def pct(part: int, whole: int) -> str:
    """Faiz nisbətini qaytarır."""
    return f"{int(part / whole * 100)}%" if whole else "0%"


def bar_chart(labels: list[str], values: list[int], width: int = 12) -> str:
    """Mətn formatında şaquli/üfüqi zolaqlı qrafik qurur."""
    m = max(values) if values and max(values) > 0 else 1
    lines = []
    for lbl, v in zip(labels, values):
        bar_len = int(v / m * width)
        from common.formatting import progress_bar
        bar_str = progress_bar(bar_len, width, width)           # UI zolaq üslubuna uyğun (/ui → bar)
        lines.append(f"<code>{lbl:2s}</code> {bar_str} <b>{v}</b>")
    return "\n".join(lines)


def display_name(u: dict, user_id: int | None = None) -> str:
    """İstifadəçi üçün təmiz görünüş adını qaytarır."""
    name = (u or {}).get("first_name") or (u or {}).get("username")
    if name:
        return escape(name)
    return str(user_id or (u or {}).get("user_id") or "Adsız")


def fmt_date(ts: int) -> str:
    """Tarixi 'GG.AA.İİİİ' formatında qaytarır."""
    return format_ts(ts, "%d.%m.%Y", empty="-")


__all__ = [
    "BOT_STATUS",
    "BROADCAST_DELAY",
    "CHATS_PAGE",
    "CHAT_FILTERS",
    "CHAT_TYPES",
    "LINE",
    "PAGE_SIZE",
    "PREMIUM_DURATIONS",
    "PREMIUM_PAGE_SIZE",
    "SECTIONS",
    "SOURCE_LABELS",
    "TARGET_LABELS",
    "TRACK_THROTTLE",
    "USERS_PAGE",
    "USER_FILTERS",
    "ago",
    "bar_chart",
    "crumb",
    "display_name",
    "fmt_date",
    "fmt_dt",
    "pct",
    "sparkline",
    "stats_tabs",
    "trend",
    "tz_offset_seconds",
]
