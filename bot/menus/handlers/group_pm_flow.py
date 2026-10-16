"""
bot.menus.handlers.group_pm_flow
İstifadəçi şəxsi menyusu (umenu:*) və PM qrup ayarları (pm_grp:*) handler-ləri.
"""

import asyncio
import contextlib
import logging
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.menus.views.home import main_kb, main_text
from bot.menus.state import grp_inp, is_creator
from bot.menus.views.broadcast import get_bot_username
from bot.menus.views.base import CHAT_FILTERS, USER_FILTERS
from bot.menus.views.groups import user_menu_view
from bot.menus.views.stats import fmt_until
from common import tiers
from common.database import get_db
from common.i18n import _ as tr
from common.utilities import ui_styled

logger = logging.getLogger(__name__)
router = Router(name="group_pm_flow")

ALL_KINDS = {"all_menu": "menu", "all_acc": "acc", "all_mp3": "mp3", "all_vrec": "rec", "all_lm": "lm"}


def _all_next(kind: str, arg: str):
    from bot.core import va_record
    from bot.core.group_panel import (
        CYCLE_LINK_MODES,
        LINK_MODE_AUTO,
        MENU_ACCESS_ADMIN,
        MENU_ACCESS_ROLES,
        _cycle,
        active_groups,
        is_group_menu_enabled,
        is_group_mp3_active,
    )

    if kind == "acc":
        cur = get_db().get_setting("group_menu:access") or MENU_ACCESS_ADMIN
        return arg if arg in MENU_ACCESS_ROLES else _cycle(MENU_ACCESS_ROLES, cur)
    if kind == "lm":
        cur = get_db().get_setting("group_link:default_mode") or LINK_MODE_AUTO
        return arg if arg in CYCLE_LINK_MODES else _cycle(CYCLE_LINK_MODES, cur)
    if arg in ("on", "off"):
        return arg == "on"
    rows = active_groups()
    check = {
        "menu": lambda c: is_group_menu_enabled(c),
        "mp3": lambda c: is_group_mp3_active(c),
        "rec": lambda c: int(c) in va_record.auto_chats(),
    }[kind]
    return not any(check(r["chat_id"]) for r in rows)


@router.callback_query(F.data.startswith("umenu:"))
async def user_menu_callback(cb: CallbackQuery):
    act = cb.data.split(":", 1)[1]

    if act == "close":
        await cb.answer()
        with contextlib.suppress(Exception):
            await cb.message.delete()
        return

    if act == "main":
        await cb.answer()
        if is_creator(cb.from_user.id):
            await cb.message.edit_text(main_text(), parse_mode="HTML", reply_markup=main_kb())
        else:
            text, kb = await user_menu_view(cb.bot, cb.from_user)
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if act in ("invite", "ref"):
        await cb.answer()
        bot_uname = await get_bot_username(cb.bot)
        from bot.menus.views.invites import user_invite_view

        text, kb = await user_invite_view(cb.from_user, bot_uname)
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if act == "mp3_info":
        await cb.answer()
        text = (
            "🎧 <b>Audio Formatı & MP3 Keyfiyyət Rejimi</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Bot bütün mahnıları ən yüksək keyfiyyətlə və təmiz metadata ilə təmin edir:\n\n"
            "• <b>Standart Rejim:</b> Orijinal audio fayllar (m4a/mp3) — teqlər və örtük şəkli ilə.\n"
            "• <b>MP3 Rejimi:</b> 320 kbps Studio, 256, 192 və 128 kbps formatında fərdi seçim.\n\n"
            "<i>Şəxsi çatda və ya qruplarda istədiyiniz formatı menyudan tənzimləyə bilərsiniz.</i>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎛 Formatı Dəyiş (/codec)", callback_data="codec:panel")],
                [InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main"), InlineKeyboardButton(text="✖️ Bağla", callback_data="umenu:close")],
            ]
        )
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if act == "help":
        await cb.answer()
        bot_uname = await get_bot_username(cb.bot)
        text = (
            "❓ <b>DLLMaster — İstifadə Bələdçisi</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🎵 <b>Musiqi Yükləmək:</b>\n"
            "• Çata sadəcə mahnının və ya ifaçının adını yazın.\n"
            "• YouTube, Spotify və ya Apple Music linklərini birbaşa göndərin.\n\n"
            f"⚡ <b>İnline Axtarış:</b>\n"
            f"• İstənilən çatda <code>@{bot_uname} Mahnı adı</code> yazaraq dərhal tapıb paylaşın.\n\n"
            "🔍 <b>Musiqi Tanıma (Shazam):</b>\n"
            "• Çatdakı səs yazısına və ya videoya reply edib <code>/shazam</code> yazın.\n\n"
            "✏️ <b>Audio Redaktoru:</b>\n"
            "• Bota audio fayl göndərin — adını, ifaçısını və albom üz qabığını dəyişin.\n\n"
            "💎 <b>Premium Üstünlükləri:</b>\n"
            "• <code>/premium</code> — Reklamsız, yüksək sürətli və limitsiz yükləmə."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main")],
                [InlineKeyboardButton(text="✖️ Bağla", callback_data="umenu:close")],
            ]
        )
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if act == "premium":
        await cb.answer()
        uid = cb.from_user.id
        db = get_db()
        cur = await asyncio.to_thread(db.get_premium, uid)
        lifetime = bool(cur) and cur["until"] is None
        from bot.core.stars import buy_keyboard

        buy_kb = await asyncio.to_thread(buy_keyboard, lifetime)
        rows = list(buy_kb.inline_keyboard) if buy_kb else []
        rows.append([InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main")])
        kb_with_back = InlineKeyboardMarkup(inline_keyboard=rows)
        if cur:
            text = (
                f"💎 <b>Status: Premium</b>\n⏳ {fmt_until(cur['until'])}\n\n"
                f"✨ <b>Sənin imkanların:</b>\n{tiers.perks_html()}"
                + ("\n\n⭐ <b>Uzatmaq üçün plan seç</b> — müddət üstünə əlavə olunur:" if buy_kb else "")
            )
        else:
            text = (
                f"🆓 <b>Status: Free</b>\n\n💎 <b>Premium ilə əldə edəcəklərin:</b>\n{tiers.perks_html()}"
                + ("\n\n⭐ <b>Telegram Stars ilə premium al:</b>" if buy_kb else "")
            )
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb_with_back)
        return

    if act in ("groups", "groups_refresh"):
        await cb.answer()
        from bot.menus.views.groups import _USER_ADMIN_GROUPS_CACHE, user_groups_view

        if act == "groups_refresh":
            _USER_ADMIN_GROUPS_CACHE.pop(cb.from_user.id, None)
        text, kb = await user_groups_view(cb.bot, cb.from_user.id, page=0)
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return


@router.callback_query(F.data.startswith("pm_grp:"))
async def pm_group_callback(cb: CallbackQuery):
    parts = cb.data.split(":")
    act = parts[1] if len(parts) > 1 else ""

    if act == "input_id":
        await cb.answer()
        grp_inp[cb.from_user.id] = {
            "panel_id": cb.message.message_id,
            "chat_id": cb.message.chat.id,
            "until": time.time() + 300,
        }
        await cb.message.edit_text(
            "✍️ <b>Qrup ID və ya @istifadəçi adı göndərin</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Məsələn: <code>-1001234567890</code> və ya <code>@qrupunuz</code>\n\n"
            "<i>Bot həmin qrupda olmalı, siz isə admin olmalısınız.</i>",
            parse_mode="HTML",
            reply_markup=panel.kb([panel.cancel("umenu:groups")]),
        )
        return

    if act == "page":
        await cb.answer()
        from bot.menus.views.groups import user_groups_view

        page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0
        text, kb = await user_groups_view(cb.bot, cb.from_user.id, page=page)
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    if not is_creator(cb.from_user.id):
        await cb.answer("👑 Bu əməliyyat yalnız bot sahibi üçündür!", show_alert=True)
        return

    from bot.core.group_panel import all_groups_set
    from bot.menus.views.groups import all_groups_settings_view

    if act in ALL_KINDS:
        kind = ALL_KINDS[act]
        val = await asyncio.to_thread(_all_next, kind, parts[2] if len(parts) > 2 else "toggle")
        count = await asyncio.to_thread(all_groups_set, kind, val)
        await cb.answer(f"✅ {count} qrupa tətbiq edildi", show_alert=True)
    else:
        await cb.answer()
    text, kb = await all_groups_settings_view(cb.bot, cb.from_user.id)
    with ui_styled("admin"):
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    from bot.menus.keyboards import menu_btn
    return menu_btn(text, data)


async def _mcb_chat_leave(cb: CallbackQuery, parts: list, action: str):
    from html import escape
    from bot.menus.state import safe_edit_cb

    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    await cb.answer()
    back = ":".join(parts[3:]) or "grp:groups:0"
    row = await asyncio.to_thread(get_db().get_chat, chat_id)
    title = escape((row or {}).get("title") or str(chat_id))
    await safe_edit_cb(
        cb,
        f"⚠️ <b>Bot bu çatdan çıxsın?</b>\n\n💬 <b>{title}</b>\n🆔 <code>{chat_id}</code>\n\n"
        "<i>Bot yenidən yalnız kimsə onu əlavə edəndə qayıda bilər.</i>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                panel.confirm(
                    f"menu:chat_leavey:{chat_id}:{back}",
                    f"menu:chat:{chat_id}:{back}",
                    "çatdan çıx",
                ),
            ]
        ),
    )


async def _mcb_chat_leavey(cb: CallbackQuery, parts: list, action: str):
    from bot.menus.state import get_context, safe_edit_cb
    from bot.menus.views.groups import groups_view

    ctx = get_context()
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        await cb.answer()
        return
    back = ":".join(parts[3:]) or "grp:groups:0"
    try:
        await ctx.bot.leave_chat(chat_id)
        note = "✅ Bot çatdan çıxdı"
    except Exception as e:
        note = f"Çıxmaq alınmadı: {str(e)[:120]}"
        logger.warning("leave_chat xətası (%s): %s", chat_id, e)
    await cb.answer(note)
    b = back.split(":")
    if b[0] == "grp" and len(b) >= 2 and b[1] in dict(CHAT_FILTERS):
        from bot.menus.views.users_chats import chats_view

        page = int(b[2]) if len(b) > 2 and b[2].isdigit() else 0
        await safe_edit_cb(cb, *(await chats_view(b[1], page)))
    else:
        await safe_edit_cb(cb, *groups_view())


async def _mcb_usearch_res(cb: CallbackQuery, parts: list, action: str):
    from html import escape
    from bot.menus.navigation import nav_row
    from bot.menus.state import safe_edit_cb, us
    from bot.menus.views.base import display_name
    from bot.menus.views.users_chats import users_view

    await cb.answer()
    query = us.get("last_query")
    if not query:
        text, kb = await users_view("all", 0)
        await safe_edit_cb(cb, text, kb)
        return
    rows = await asyncio.to_thread(get_db().search_users, query, int(time.time()))
    kb = [
        [
            _btn(
                f"{'💎 ' if u['premium'] else ''}{'🚫 ' if u['blocked'] else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
                f"user:{u['user_id']}:usearch_res",
            )
        ]
        for u in rows
    ]
    kb.append([_btn("🔍 Yenidən axtar", "usearch")])
    kb.append(nav_row("users:all:0"))
    await safe_edit_cb(
        cb,
        f"🔍 <b>\"{escape(query)}\"</b> üzrə <b>{len(rows)}</b> nəticə",
        InlineKeyboardMarkup(inline_keyboard=kb),
    )


async def _mcb_csv(cb: CallbackQuery, parts: list, action: str):
    import csv
    import io
    from aiogram.types import BufferedInputFile
    from bot.menus.views.base import fmt_dt

    await cb.answer("Hazırlanır...")
    now = int(time.time())
    users = await asyncio.to_thread(get_db().users_export, now)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "user_id",
            "username",
            "first_name",
            "last_name",
            "first_seen",
            "last_seen",
            "blocked",
            "premium",
            "premium_until",
            "downloads_total",
            "downloads_30d",
            "last_download",
        ]
    )
    for u in users:
        w.writerow(
            [
                u["user_id"],
                u["username"] or "",
                u["first_name"] or "",
                u["last_name"] or "",
                fmt_dt(u["first_seen"]),
                fmt_dt(u["last_seen"]),
                u["blocked"],
                int(bool(u["premium"])),
                (
                    "ömürlük"
                    if u["premium"] and u["premium_until"] is None
                    else fmt_dt(u["premium_until"])
                    if u["premium"]
                    else ""
                ),
                u["dl_total"],
                u["dl_30d"],
                fmt_dt(u["last_download"]) if u["last_download"] else "",
            ]
        )
    await cb.message.answer_document(
        BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="users.csv"),
        caption=f"👥 {len(users)} istifadəçi",
    )


async def handle_chat_users_action(cb: CallbackQuery, parts: list[str], action: str) -> bool:
    """İstifadəçi və çatların idarə edilməsi hərəkətlərini icra edir."""
    from bot.menus.navigation import nav_row
    from bot.menus.state import reset_inputs, safe_edit_cb, us
    from bot.menus.views.premium_stars import duration_panel
    from bot.menus.views.users_chats import chat_card, user_card, users_view

    if action == "users":
        await cb.answer()
        reset_inputs()
        flt, page = "all", 0
        if len(parts) > 2 and parts[2].isdigit():
            page = int(parts[2])
        elif len(parts) > 2:
            flt = parts[2] if parts[2] in dict(USER_FILTERS) else "all"
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        text, kb = await users_view(flt, page)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "user":
        await cb.answer()
        try:
            uid = int(parts[2])
        except (IndexError, ValueError):
            return True
        back = ":".join(parts[3:]) or "users:all:0"
        text, kb = await user_card(uid, back)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "grp":
        await cb.answer()
        reset_inputs()
        flt = parts[2] if len(parts) > 2 and parts[2] in dict(CHAT_FILTERS) else "groups"
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from bot.menus.views.users_chats import chats_view

        text, kb = await chats_view(flt, page)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "chat":
        try:
            chat_id = int(parts[2])
        except (IndexError, ValueError):
            await cb.answer()
            return True
        await cb.answer("Yoxlanılır...")
        back = ":".join(parts[3:]) or "grp:groups:0"
        text, kb = await chat_card(chat_id, back)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "chat_leave":
        await _mcb_chat_leave(cb, parts, action)
        return True

    if action == "chat_leavey":
        await _mcb_chat_leavey(cb, parts, action)
        return True

    if action == "usearch_res":
        await _mcb_usearch_res(cb, parts, action)
        return True

    if action == "prm_gv":
        await cb.answer()
        try:
            uid = int(parts[2])
        except (IndexError, ValueError):
            return True
        card_back = ":".join(parts[3:]) or "users:all:0"
        u = await asyncio.to_thread(get_db().get_user, uid)
        text, kb = await duration_panel(uid, u, back=f"user:{uid}:{card_back}")
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "usearch":
        await cb.answer()
        reset_inputs()
        us.update(
            stage="await_query",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        await safe_edit_cb(
            cb,
            "🔍 <b>İstifadəçi axtarışı</b>\n\n"
            "ID, <code>@username</code> və ya adın bir hissəsini yazın:",
            InlineKeyboardMarkup(inline_keyboard=[nav_row("users:all:0")]),
        )
        return True

    if action == "csv":
        await _mcb_csv(cb, parts, action)
        return True

    return False


@router.callback_query(F.data.startswith("uinv:"))
async def user_invite_callback(cb: CallbackQuery):
    act = cb.data.split(":", 1)[1]
    if act.startswith("refresh"):
        back_cb = "sg:back" if ":start" in act else "umenu:home"
        await cb.answer(tr("Yeniləndi"))
        bot_uname = await get_bot_username(cb.bot)
        from bot.menus.views.invites import user_invite_view

        text, kb = await user_invite_view(cb.from_user, bot_uname, back_cb=back_cb)
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


__all__ = ["handle_chat_users_action", "router"]
