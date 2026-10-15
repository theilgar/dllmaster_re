"""
bot.menus.handlers.commands
Bot üçün /menu, /groups, /rec və /allgroups komanda handler-ləri.
"""

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.menus.state import (
    bc,
    bc_reset,
    delete_preview,
    get_context,
    is_creator,
    reset_inputs,
)
from bot.menus.views.groups import user_menu_view
from bot.menus.views.home import main_kb, main_text
from common.permissions import is_group_admin, is_group_owner
from common.utilities import ui_styled

logger = logging.getLogger(__name__)
router = Router(name="menu_commands")


@router.message(Command("menu"))
async def menu_command(message: Message):
    uid = message.from_user.id
    ctx = get_context()

    # 1) Qrup: menyu söndürülübsə yalnız adminlərə xəbər
    if message.chat.type in ("group", "supergroup"):
        from bot.core.group_panel import (
            MENU_ACCESS_ALL,
            MENU_ACCESS_LABELS,
            MENU_ACCESS_OWNER,
            get_group_menu_access,
            is_group_menu_enabled,
            panel_view,
            user_view,
        )

        chat_id, title = message.chat.id, message.chat.title or "Qrup"
        is_adm = await is_group_admin(message.bot, chat_id, uid)
        access = get_group_menu_access(chat_id)
        allowed = await is_group_owner(message.bot, chat_id, uid) if access == MENU_ACCESS_OWNER else is_adm
        if not is_group_menu_enabled(chat_id) and not is_adm:
            await message.reply("🔒 <i>Bu qrupda menyu adminlər tərəfindən söndürülüb.</i>", parse_mode="HTML")
            return
        if allowed:
            text, kb = await panel_view(message.bot, chat_id, uid, title)
        elif access == MENU_ACCESS_ALL:
            text, kb = await user_view(message.bot, chat_id, title)
        else:
            who = MENU_ACCESS_LABELS[access].split(" ", 1)[1].lower()
            await message.reply(f"👮 <i>Bu qrupda menyudan {who} istifadə edə bilər.</i>", parse_mode="HTML")
            return
        with ui_styled("admin"):
            await message.reply(text, parse_mode="HTML", reply_markup=kb)
        return

    # 2) Şəxsi çat (PM) - Creator
    if is_creator(uid):
        if bc.get("stage") and bc.get("stage") != "sending":
            await delete_preview(ctx)
            bc_reset()
        reset_inputs()
        await message.answer(await asyncio.to_thread(main_text, ctx), parse_mode="HTML", reply_markup=main_kb(ctx))
        return

    # 3) Şəxsi çat (PM) - Sadə İstifadəçi / Tester
    text, kb = await user_menu_view(message.bot, message.from_user)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("groups", "qruplar", "group"))
async def groups_command(message: Message):
    uid = message.from_user.id
    if message.chat.type in ("group", "supergroup"):
        if await is_group_admin(message.bot, message.chat.id, uid):
            from bot.core.group_panel import panel_view

            text, kb = await panel_view(message.bot, message.chat.id, uid, message.chat.title)
            with ui_styled("admin"):
                await message.reply(text, parse_mode="HTML", reply_markup=kb)
        return
    from bot.menus.views.groups import user_groups_view

    text, kb = await user_groups_view(message.bot, uid, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("allgroups", "butunqruplar"))
async def all_groups_command(message: Message):
    uid = message.from_user.id
    if not is_creator(uid):
        return
    from bot.menus.views.groups import all_groups_settings_view

    text, kb = await all_groups_settings_view(message.bot, uid)
    with ui_styled("admin"):
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("rec"))
async def rec_command(message: Message):
    cid = message.chat.id
    uid = message.from_user.id
    is_creator_user = is_creator(uid)
    ctx = get_context()
    if cid < 0:
        if not is_creator_user and not await is_group_admin(message.bot, cid, uid):
            return
        va = getattr(ctx, "voice_assistant", None)
        if va and getattr(va, "rec", None):
            text, kb = await va.rec.panel(cid, message.chat.title or str(cid), from_menu=True, owner=is_creator_user)
            await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return
    elif is_creator_user:
        va = getattr(ctx, "voice_assistant", None)
        if va and getattr(va, "rec", None):
            text, kb = va.rec.groups_view()
            await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return
        from bot.menus.views.system_hub import depo_view

        text, kb = depo_view(ctx)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


__all__ = ["router"]
