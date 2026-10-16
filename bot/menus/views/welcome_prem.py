"""
bot.menus.views.welcome_prem
Yeni istifadəçilər üçün Xoş Gəldin (Welcome Trial & Promo) Premium idarəetmə paneli.
"""
from __future__ import annotations

from datetime import datetime
import time

from aiogram.types import InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.views.base import SECTIONS, crumb
from common.database import (
    get_db,
    get_welcome_prem_days,
    get_welcome_prem_mode,
    get_welcome_prem_until,
    is_welcome_prem_active,
    is_welcome_prem_enabled,
)
from common.formatting import fmt_date, get_tz
from common.i18n import _ as tr
from common.menu import _btn


def welcome_prem_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """👑 Admin üçün Yeni İstifadəçi (Xoş Gəldin) Premium idarə paneli."""
    enabled = is_welcome_prem_enabled()
    mode = get_welcome_prem_mode()
    days = get_welcome_prem_days()
    until_ts = get_welcome_prem_until()
    now = int(time.time())

    db = get_db()
    stats = (
        db.get_welcome_trial_stats()
        if hasattr(db, "get_welcome_trial_stats")
        else {"total_trials": 0, "active_trials": 0}
    )

    status_icon = "🟢 Aktiv" if is_welcome_prem_active() else ("🔴 Deaktiv (Tarix keçib)" if enabled and mode == "date" else "🔴 Deaktiv")

    lines = [
        crumb(SECTIONS["biz"], "🎉 Xoş gəldin Premium-u"),
        "",
        f"⚙️ <b>Vəziyyət:</b> {status_icon}",
    ]

    if mode == "days":
        lines.append("🎛 <b>Rejim:</b> ⏱ <b>Sınaq müddəti (Günlə)</b>")
        lines.append(f"💎 <b>Yeni istifadəçiyə hədiyyə:</b> <b>{days} gün</b> Premium")
        lines.append("<i>Hər yeni üzv qeydiyyatdan keçdiyi andan etibarən təyin olunmuş gün qədər Premium qazanır.</i>")
    else:
        lines.append("🎛 <b>Rejim:</b> 📅 <b>Aksiya kampaniyası (Tarixədək)</b>")
        if until_ts:
            dt_str = fmt_date(until_ts, "%d.%m.%Y %H:%M")
            is_past = until_ts <= now
            state_str = " (⚠️ Vaxtı bitib)" if is_past else " (🟢 Qüvvədədir)"
            lines.append(f"⏳ <b>Kampaniyanın bitmə tarixi:</b> <b>{dt_str}</b>{state_str}")
        else:
            lines.append("⏳ <b>Kampaniyanın bitmə tarixi:</b> <i>Təyin olunmayıb</i>")
        lines.append("<i>Bu tarixədək bota ilk dəfə qoşulan bütün yeni istifadəçilərə həmin tarixədək Premium verilir.</i>")

    lines.extend([
        "",
        "📊 <b>Statistika:</b>",
        f"• Cəmi hədiyyə alanlar: <b>{stats['total_trials']}</b> nəfər",
        f"• Hazırda aktiv sınaqlar: <b>{stats['active_trials']}</b> nəfər",
        "",
        "🛡 <i>Yalnız bota ilk dəfə start verən yeni istifadəçilərə şamil olunur (təkrar istifadəçilər bloklanır).</i>",
    ])

    if note:
        lines.append(f"\nℹ️ <i>{note}</i>")

    toggle_txt = "🔴 Söndür" if enabled else "🟢 Aktivləşdir"
    mode_switch_txt = "🔄 Rejim: 📅 Tarixlə" if mode == "days" else "🔄 Rejim: ⏱ Günlə"

    rows = [
        [_btn(toggle_txt, "wprem_toggle"), _btn(mode_switch_txt, "wprem_mode")],
    ]

    if mode == "days":
        rows.append([_btn("✏️ Gün sayını yaz", "wprem_set_days")])
        rows.append([
            _btn(f"{'✅ ' if days == 1 else ''}1 gün", "wprem_days:1"),
            _btn(f"{'✅ ' if days == 3 else ''}3 gün", "wprem_days:3"),
            _btn(f"{'✅ ' if days == 7 else ''}7 gün", "wprem_days:7"),
            _btn(f"{'✅ ' if days == 14 else ''}14 gün", "wprem_days:14"),
            _btn(f"{'✅ ' if days == 30 else ''}30 gün", "wprem_days:30"),
        ])
    else:
        rows.append([_btn("✏️ Bitmə tarixini yaz", "wprem_set_date"), _btn("🚫 Tarixi sıfırla", "wprem_clr_date")])
        rows.append([
            _btn("+1 gün", "wprem_qdate:1"),
            _btn("+3 gün", "wprem_qdate:3"),
            _btn("+7 gün", "wprem_qdate:7"),
            _btn("+14 gün", "wprem_qdate:14"),
            _btn("+30 gün", "wprem_qdate:30"),
        ])

    rows.append(nav_row("biz"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
