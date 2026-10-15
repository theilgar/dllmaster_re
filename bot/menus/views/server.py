"""
bot.menus.views.server
Server resursları, monitorinq, speedtest və fastfetch pəncərələri.
"""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import SECTIONS, crumb
from common.diagnostics import server_snapshot
from common.formatting import fmt_duration_words
from common.menu import _bar_box, _btn, format_table, render_card




def srv_button_label() -> str:
    s = server_snapshot()
    parts = []
    if "cpu" in s:
        parts.append(f"CPU {s['cpu']:.0f}%")
    if "ram" in s:
        parts.append(f"RAM {s['ram']:.0f}%")
    if "disk" in s and s["disk"] >= 85:
        parts.append(f"💾 {s['disk']:.0f}%")
    warn = any(s.get(k, 0) >= 90 for k in ("cpu", "ram", "disk"))
    return "📡 Server" + (" · " + " · ".join(parts) if parts else "") + (" ⚠️" if warn else "")


def srv_hub_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = server_snapshot()
    gb = 1024**3
    l = s.get("load", (0.0, 0.0, 0.0))
    l1 = l[0] if isinstance(l, (list, tuple)) and len(l) > 0 else 0.0
    l5 = l[1] if isinstance(l, (list, tuple)) and len(l) > 1 else 0.0
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    rows = [
        (
            "CPU",
            f"{_bar_box(s.get('cpu', 0) / 100, 8)} {s.get('cpu', 0):3.0f}%",
            f"load {l1:.2f} {l5:.2f} · {s.get('cores', 1)} nüvə",
        ),
        (
            "RAM",
            f"{_bar_box(s.get('ram', 0) / 100, 8)} {s.get('ram', 0):3.0f}%",
            f"{s.get('ram_used', 0) / gb:.1f}/{s.get('ram_total', 0) / gb:.1f} GB"
            + (f" · swp {s['swap']:.0f}%" if "swap" in s else ""),
        ),
        (
            "DSK",
            f"{_bar_box(s.get('disk', 0) / 100, 8)} {s.get('disk', 0):3.0f}%",
            f"{s.get('disk_used', 0) / gb:.0f}/{s.get('disk_total', 0) / gb:.0f} GB",
        ),
        ("BOT", f"{s.get('bot_rss', 0) / 1048576:.0f} MB", f"işləyir {fmt_duration_words(s.get('bot_uptime', 0))}"),
        ("SYS", fmt_duration_words(s.get('uptime', 0)), "server onlayn"),
    ]
    card_lines = [
        f"Server: 🟢 onlayn · {now_str}",
        "──────────────────────────────────────",
        "BÖLMƏ  STAT/BAR        MƏLUMAT",
        "─────  ─────────────── ────────────────",
    ] + format_table(rows)
    if note:
        card_lines.append("──────────────────────────────────────")
        card_lines.append(f"ℹ️ {note}")

    head = crumb(SECTIONS["sys"], "📡 Server")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("🚀 Speedtest", "speed"),
                _btn("🖥 Fastfetch", "fetch"),
                InlineKeyboardButton(text="📈 htop", callback_data="htop:start"),
            ],
            nav_row("sys", refresh="srv"),
        ]
    )
    return render_card(head, card_lines), kb


def userbots_button_label() -> str:
    ctx = get_context()
    fn = getattr(ctx, "userbots_label", None)
    return fn() if callable(fn) else "📱 Userbotlar"


def va_button_label() -> str:
    ctx = get_context()
    va = getattr(ctx, "voice_assistant", None)
    if va is None:
        return "🎙 Canlı yayım · ❌"
    try:
        return va.label()
    except Exception:
        return "🎙 Canlı yayım"


__all__ = [
    "srv_button_label",
    "srv_hub_view",
    "userbots_button_label",
    "va_button_label",
]
