"""
bot.menus.handlers.invites_flow
Dəvət (invite / referral) sistemi menyu callback idarəçisi.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import get_context, inv_inp, safe_edit_cb
from bot.menus.views.invites import invite_top_view, invite_view
from common.database import (
    get_invite_reward_days,
    is_invite_enabled,
    set_invite_enabled,
    set_invite_reward_days,
)
from common.i18n import _ as tr

logger = logging.getLogger(__name__)


async def handle_invite_callback(cb: CallbackQuery, parts: list[str], action: str):
    """Mərkəzi menyudan gələn inv_* callback-lərini icra edir."""
    if action == "inv_cfg":
        await cb.answer()
        inv_inp["stage"] = None
        text, kb = invite_view()
        await safe_edit_cb(cb, text, kb)

    elif action == "inv_toggle":
        cur = is_invite_enabled()
        new_val = not cur
        set_invite_enabled(new_val)
        note = tr("🟢 Dəvət sistemi aktivləşdirildi") if new_val else tr("🔴 Dəvət sistemi söndürüldü")
        await cb.answer(note)
        text, kb = invite_view(note)
        await safe_edit_cb(cb, text, kb)

    elif action == "inv_days":
        days = next((int(p) for p in reversed(parts) if p.isdigit()), None)
        if days is not None:
            set_invite_reward_days(days)
            note = tr("✅ Hər dəvət üçün {n} gün Premium təyin edildi", n=days)
            await cb.answer(note)
            text, kb = invite_view(note)
            await safe_edit_cb(cb, text, kb)

    elif action == "inv_set_days":
        await cb.answer()
        inv_inp.update(
            stage="await_invite_days",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        cur_days = get_invite_reward_days()
        text = (
            "🎁 <b>Dəvət üçün Premium gününü təyin et</b>\n\n"
            f"Hazırkı mükafat: <b>{cur_days} gün</b>\n\n"
            "✍️ <b>Hər 1 dəvət üçün istifadəçiyə neçə gün Premium verilsin?</b>\n"
            "<i>Müsbət tam ədəd daxil edin (məsələn: <code>5</code>, <code>10</code>, <code>30</code>).</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_row("inv_cfg")])
        await safe_edit_cb(cb, text, kb)

    elif action == "inv_top":
        await cb.answer()
        page = next((int(p) for p in reversed(parts) if p.isdigit()), 0)
        text, kb = invite_top_view(page)
        await safe_edit_cb(cb, text, kb)
