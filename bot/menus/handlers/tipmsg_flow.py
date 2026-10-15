"""
bot.menus.handlers.tipmsg_flow
☕ Bəxşiş mesajı axını: menyu, adama / qrupa göndərmə (ID / @username / kart), önizləmə və broadcast.

Broadcast mövcud 📢 mexanizmini işlədir (bc vəziyyəti, bc_send / bc_stop düymələri, hesabat paneli) —
yalnız mətn + düymələr (bc["kb"] = "tip") və hədəf («free» / «users») fərqlidir.
"""
import asyncio
import contextlib
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.menus.navigation import nav_row
from bot.menus.state import bc, bc_reset, delete_preview, get_context, is_creator, panel_editor, reset_inputs, safe_edit_cb, tp
from bot.menus.views.base import TARGET_LABELS
from bot.menus.views.broadcast import send_one
from bot.menus.views.premium_stars import parse_user_ref
from bot.menus.views.tipmsg import ask_group_view, ask_user_view, bot_username, result_text, tip_menu_view
from common.database import get_db
from common.menu import _btn

logger = logging.getLogger(__name__)


async def _payload(group: bool = False):
    from bot.plugins.start_plugin import tip_message
    return tip_message(await bot_username(), group=group)


async def _send_to(chat_id: int, group: bool = False) -> str:
    from bot.plugins.compact_ui_plugin import raw
    text, kb = await _payload(group)
    with raw():                                   # 📋 Görünüş: istifadəçiyə / qrupa gedən mesaj kod blokuna çevrilməsin
        return await send_one(get_context().bot, get_db(), "group" if group else "user", chat_id, text, kb)


def _who(uid: int, u: dict | None) -> str:
    name = (u or {}).get("first_name") or (f"@{u['username']}" if (u or {}).get("username") else "")
    return f'<a href="tg://user?id={uid}">{escape(name or str(uid))}</a> (<code>{uid}</code>)'


async def _mcb_tipmsg(cb: CallbackQuery, parts: list, action: str):
    """menu:tipmsg[:to | :prev | :bc:<free|users> | :u:<id>[:<geri>]]"""
    sub = parts[2] if len(parts) > 2 else ""
    ctx = get_context()

    if sub == "":
        await cb.answer()
        tp["stage"] = None
        if bc.get("stage") in ("await_text", "confirm"):
            await delete_preview()
            bc_reset()
        await safe_edit_cb(cb, *await tip_menu_view())
        return

    if sub == "to":
        await cb.answer()
        reset_inputs()
        tp.update(stage="await_user", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await safe_edit_cb(cb, *ask_user_view())
        return

    if sub == "g":
        await cb.answer()
        reset_inputs()
        tp.update(stage="await_group", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await safe_edit_cb(cb, *ask_group_view())
        return

    if sub == "c" and len(parts) > 3 and parts[3].lstrip("-").isdigit():   # qrup kartından: dərhal göndər
        chat_id = int(parts[3])
        back = ":".join(parts[4:]) or "chats:all:0"
        status = await _send_to(chat_id, group=True)
        row = await asyncio.to_thread(get_db().get_chat, chat_id)
        note = result_text(status, f"<b>{escape((row or {}).get('title') or str(chat_id))}</b>", group=True)
        await cb.answer("☕ Göndərildi" if status == "ok" else note.replace("<b>", "").replace("</b>", "")[:190])
        from bot.menus.views.users_chats import chat_card
        text, kb = await chat_card(chat_id, back, live=False)
        await safe_edit_cb(cb, f"{text}\n\n{note}", kb)
        return

    if sub == "prev":                                         # creator-a eyni mesajı göndər (düymələr işləyir, özünə ödəniş olmur)
        await cb.answer()
        text, kb = await _payload()
        with contextlib.suppress(Exception):
            await ctx.bot.send_message(cb.from_user.id, text, parse_mode="HTML", reply_markup=kb,
                                       disable_web_page_preview=True)
        return

    if sub == "u" and len(parts) > 3 and parts[3].isdigit():   # istifadəçi kartından: dərhal göndər
        uid = int(parts[3])
        back = ":".join(parts[4:]) or "users:all:0"          # geri ünvanında ":" ola bilər
        status = await _send_to(uid)
        u = await asyncio.to_thread(get_db().get_user, uid)
        note = result_text(status, _who(uid, u))
        await cb.answer(note.replace("<code>", "").replace("</code>", "")[:190] if status != "ok" else "☕ Göndərildi")
        from bot.menus.views.users_chats import user_card
        text, kb = await user_card(uid, back)
        await safe_edit_cb(cb, f"{text}\n\n{note}", kb)
        return

    if sub == "bc" and len(parts) > 3 and parts[3] in ("free", "users", "groups"):
        target = parts[3]
        targets = await asyncio.to_thread(get_db().broadcast_targets, target)
        if not targets:
            await cb.answer("Bu kateqoriyada heç kim yoxdur", show_alert=True)
            return
        await cb.answer()
        await delete_preview()
        reset_inputs()
        bc_reset()
        text, kb = await _payload(group=(target == "groups"))
        bc.update(stage="confirm", target=target, text=text, markup=kb, chat_id=cb.message.chat.id,
                  panel_id=cb.message.message_id, preview_id=None)
        try:
            preview = await cb.message.answer(
                text, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [_btn(f"✅ Göndər ({len(targets)})", "bc_send")],
                    [_btn("✖️ Ləğv et", "bc_cancel")],
                ]),
            )
        except Exception as e:
            bc_reset()
            await safe_edit_cb(cb, *await tip_menu_view(f"❌ Önizləmə göndərilmədi: <code>{escape(str(e))[:200]}</code>"))
            return
        bc["preview_id"] = preview.message_id
        await safe_edit_cb(
            cb,
            f"☕ <b>Bəxşiş mesajı</b> — {TARGET_LABELS[target]} (<b>{len(targets)}</b>)\n\n"
            "👇 <i>Aşağıdakı mesaj məhz belə göndəriləcək"
            + (" (qrupda ⭐ düyməsi botun şəxsi çatına aparır)." if target == "groups"
               else " (istifadəçilər ⭐ məbləğ düymələrini də görəcək).") + "</i>",
            InlineKeyboardMarkup(inline_keyboard=[nav_row("tipmsg")]),
        )
        return

    await cb.answer()


# ── mətn daxiletməsi: ID / @username ──
async def waiting_tip_user(message: Message) -> bool:
    return tp.get("stage") == "await_user" and bool(message.from_user) and is_creator(message.from_user.id)


async def tip_user_input(message: Message):
    ctx = get_context()
    kind, value = parse_user_ref(message.text)
    db = get_db()
    with contextlib.suppress(Exception):
        await message.delete()
    edit = panel_editor(ctx, tp, "Bəxşiş mesajı")

    u = None
    if kind == "id":
        uid, u = value, await asyncio.to_thread(db.get_user, value)
    elif kind == "username":
        u = await asyncio.to_thread(db.find_user_by_username, value)
        if not u:
            await edit(*ask_user_view(f"❌ <b>@{escape(value)}</b> tapılmadı — istifadəçi botu /start etməyib. "
                                      "Başqa username və ya rəqəmli ID yaz:"))
            return
        uid = u["user_id"]
    else:
        await edit(*ask_user_view("❌ Düzgün format deyil. ID (məs. <code>123456789</code>) və ya @username yaz:"))
        return

    if is_creator(uid):
        await edit(*ask_user_view("🙂 Özünə göndərmək üçün <b>👁 Mənə önizləmə</b> düyməsini işlət. Başqa ID yaz:"))
        return
    status = await _send_to(uid)
    tp["stage"] = None
    await edit(*await tip_menu_view(result_text(status, _who(uid, u))))


# ── mətn daxiletməsi: qrup ID / @username ──
async def waiting_tip_group(message: Message) -> bool:
    return tp.get("stage") == "await_group" and bool(message.from_user) and is_creator(message.from_user.id)


async def tip_group_input(message: Message):
    import re
    ctx = get_context()
    raw_ref = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", (message.text or "").strip(), flags=re.I).strip("/ ")
    with contextlib.suppress(Exception):
        await message.delete()
    edit = panel_editor(ctx, tp, "Bəxşiş mesajı")
    if re.fullmatch(r"-?\d{5,20}", raw_ref):
        ref = int(raw_ref)
    elif re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", raw_ref):
        ref = "@" + raw_ref.lstrip("@")
    else:
        await edit(*ask_group_view("❌ Düzgün format deyil. ID (məs. <code>-1001234567890</code>) və ya @username yaz:"))
        return
    try:
        chat = await ctx.bot.get_chat(ref)
    except Exception as e:
        await edit(*ask_group_view(f"❌ Qrup tapılmadı / bot orada deyil: <code>{escape(str(e))[:120]}</code>"))
        return
    if str(getattr(chat, "type", "")).endswith("private") or chat.id > 0:
        await edit(*ask_group_view("❌ Bu qrup deyil. İstifadəçiyə göndərmək üçün 👤 düyməsini işlət."))
        return
    status = await _send_to(chat.id, group=True)
    tp["stage"] = None
    await edit(*await tip_menu_view(result_text(status, f"<b>{escape(chat.title or str(chat.id))}</b>", group=True)))


async def tip_user_not_text(message: Message):
    await message.reply("✍️ ID və ya @username mətn kimi yazılmalıdır.")


def register_tipmsg_handlers(router: Router):
    router.message.register(tip_user_input, F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_tip_user)
    router.message.register(tip_group_input, F.chat.type == "private", F.text, ~F.text.startswith("/"),
                            waiting_tip_group)
    router.message.register(tip_user_not_text, F.chat.type == "private", ~F.text, waiting_tip_user)
    router.message.register(tip_user_not_text, F.chat.type == "private", ~F.text, waiting_tip_group)
