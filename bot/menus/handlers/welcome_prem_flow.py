"""
bot.menus.handlers.welcome_prem_flow
Yeni istifadəçilər üçün Xoş Gəldin (Trial / Promo) Premium menyu callback idarəçisi.
"""
from __future__ import annotations

import logging
import time

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import safe_edit_cb, wprem_inp
from bot.menus.views.welcome_prem import welcome_prem_view
from common.database import (
    get_welcome_prem_days,
    get_welcome_prem_mode,
    get_welcome_prem_until,
    is_welcome_prem_enabled,
    set_welcome_prem_days,
    set_welcome_prem_enabled,
    set_welcome_prem_mode,
    set_welcome_prem_until,
)
from common.formatting import fmt_date
from common.i18n import _ as tr

logger = logging.getLogger(__name__)


async def handle_welcome_prem_callback(cb: CallbackQuery, parts: list[str], action: str):
    """Mərkəzi menyudan gələn wprem_* callback-lərini icra edir."""
    if action == "wprem_cfg":
        await cb.answer()
        wprem_inp["stage"] = None
        text, kb = welcome_prem_view()
        await safe_edit_cb(cb, text, kb)

    elif action == "wprem_toggle":
        cur = is_welcome_prem_enabled()
        new_val = not cur
        set_welcome_prem_enabled(new_val)
        note = tr("🟢 Xoş gəldin Premium-u aktivləşdirildi") if new_val else tr("🔴 Xoş gəldin Premium-u söndürüldü")
        await cb.answer(note)
        text, kb = welcome_prem_view(note)
        await safe_edit_cb(cb, text, kb)

    elif action == "wprem_mode":
        cur_mode = get_welcome_prem_mode()
        new_mode = "date" if cur_mode == "days" else "days"
        set_welcome_prem_mode(new_mode)
        note = tr("📅 Rejim dəyişdirildi: Konkret tarixədək") if new_mode == "date" else tr("⏱ Rejim dəyişdirildi: Sınaq günləri")
        await cb.answer(note)
        text, kb = welcome_prem_view(note)
        await safe_edit_cb(cb, text, kb)

    elif action == "wprem_days":
        days = next((int(p) for p in reversed(parts) if p.isdigit()), None)
        if days is not None:
            set_welcome_prem_days(days)
            note = tr("✅ Hər yeni istifadəçiyə {n} gün Premium təyin edildi", n=days)
            await cb.answer(note)
            text, kb = welcome_prem_view(note)
            await safe_edit_cb(cb, text, kb)

    elif action == "wprem_qdate":
        days = next((int(p) for p in reversed(parts) if p.isdigit()), None)
        if days is not None:
            target_ts = int(time.time() + days * 86400)
            set_welcome_prem_until(target_ts)
            dt_str = fmt_date(target_ts, "%d.%m.%Y %H:%M")
            note = tr("✅ Kampaniya tarixi təyin edildi: {d}", d=dt_str)
            await cb.answer(note)
            text, kb = welcome_prem_view(note)
            await safe_edit_cb(cb, text, kb)

    elif action == "wprem_clr_date":
        set_welcome_prem_until(None)
        note = tr("🚫 Kampaniya tarixi sıfırlandı")
        await cb.answer(note)
        text, kb = welcome_prem_view(note)
        await safe_edit_cb(cb, text, kb)

    elif action == "wprem_set_days":
        await cb.answer()
        wprem_inp.update(
            stage="await_wprem_days",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        cur_days = get_welcome_prem_days()
        text = (
            "🎉 <b>Yeni istifadəçi üçün Premium gününü təyin et</b>\n\n"
            f"Hazırkı sınaq: <b>{cur_days} gün</b>\n\n"
            "✍️ <b>Yeni istifadəçiyə neçə gün pulsuz Premium verilsin?</b>\n"
            "<i>Müsbət tam ədəd daxil edin (məsələn: <code>3</code>, <code>7</code>, <code>14</code>).</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_row("wprem_cfg")])
        await safe_edit_cb(cb, text, kb)

    elif action == "wprem_set_date":
        await cb.answer()
        wprem_inp.update(
            stage="await_wprem_date",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        cur_until = get_welcome_prem_until()
        cur_str = fmt_date(cur_until, "%d.%m.%Y %H:%M") if cur_until else "təyin olunmayıb"
        text = (
            "🎉 <b>Xoş gəldin kampaniyasının bitmə tarixini təyin et</b>\n\n"
            f"Hazırkı bitmə tarixi: <b>{cur_str}</b>\n\n"
            "✍️ <b>Bitmə vaxtını yazın:</b>\n"
            "Nümunələr:\n"
            "• <code>25.12.2026 18:00</code> (tam tarix)\n"
            "• <code>25.12 18:00</code> (bu il)\n"
            "• <code>18:00</code> (bu gün və ya sabah)\n"
            "• <code>+3g</code> (3 gün sonra), <code>+12s</code> (12 saat sonra)\n"
            "• <code>-</code> (tarixi ləğv etmək)"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_row("wprem_cfg")])
        await safe_edit_cb(cb, text, kb)
    else:
        return False
    return True
