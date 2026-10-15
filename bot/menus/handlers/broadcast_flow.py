"""
bot.menus.handlers.broadcast_flow
Kütləvi mesaj (Broadcast) axını: hədəf seçimi, önbaxış, göndərmə və dayandırma.
"""

import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.menus.navigation import nav_row
from bot.menus.views.home import main_kb, main_text
from bot.menus.state import (
    bc,
    bc_reset,
    delete_preview,
    get_context,
    is_creator,
    panel_editor,
    reset_inputs,
    safe_edit_cb,
)
from bot.menus.views.base import LINE, SECTIONS, TARGET_LABELS, crumb
from bot.menus.views.broadcast import bc_target_kb, send_one, target_counts
from common import tiers
from common.database import get_db
from common.menu import _btn
from common.utilities import spawn

logger = logging.getLogger(__name__)

BROADCAST_DELAY = 0.05  # ~20 mesaj/san (Telegram limiti ~30/san)


def get_bc_edit_panel():
    return panel_editor(get_context(), bc, "Broadcast")


async def run_broadcast():
    db = get_db()
    targets = await asyncio.to_thread(db.broadcast_targets, bc["target"])
    total, text = len(targets), bc["text"]
    res = {"ok": 0, "removed": 0, "failed": 0}
    started = last_edit = time.time()
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("⏹ Dayandır", "bc_stop")]])
    header = f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]}\n\n"
    edit_panel = get_bc_edit_panel()

    await edit_panel(header + f"⏳ Göndərilir... 0/{total}", stop_kb)
    done = 0
    ctx = get_context()
    bot = getattr(ctx, "bot", None)
    if not bot:
        logger.error("Broadcast: bot kontekstdə tapılmadı")
        return

    markup = bc.get("markup")                                    # ☕ bəxşiş mesajı: məbləğ / deep-link düymələri

    async def deliver(kind, chat_id):
        if markup is None:
            return await send_one(bot, db, kind, chat_id, text)
        from bot.plugins.compact_ui_plugin import styled
        with styled("user"):                         # 📋 Görünüş: ☕ / 💎 mesajı seçilmiş üslubda (kod bloku / sitat)
            return await send_one(bot, db, kind, chat_id, text, markup)

    for kind, chat_id in targets:
        if bc.get("stop"):
            break
        res[await deliver(kind, chat_id)] += 1
        done += 1
        if time.time() - last_edit > 3:
            last_edit = time.time()
            await edit_panel(
                header + f"⏳ Göndərilir... {done}/{total}\n"
                f"✅ {res['ok']} · 🚫 {res['removed']} · ❌ {res['failed']}",
                stop_kb,
            )
        await asyncio.sleep(BROADCAST_DELAY)

    took = int(time.time() - started)
    status = "⏹ <b>Dayandırıldı</b>" if bc.get("stop") else "✅ <b>Tamamlandı</b>"
    await edit_panel(
        header + f"{status} ({done}/{total}, {took} san.)\n\n"
        f"✅ Çatdı: <b>{res['ok']}</b>\n"
        f"🚫 Bloklayıb / çıxarıb: <b>{res['removed']}</b>\n"
        f"❌ Xəta: <b>{res['failed']}</b>",
        InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
    )
    logger.info("Broadcast bitdi (%s): %s, %s/%s", bc.get("target"), res, done, total)


async def broadcast_task():
    try:
        await run_broadcast()
    except Exception as e:
        logger.error("Broadcast xətası: %s", e, exc_info=True)
        edit_panel = get_bc_edit_panel()
        with contextlib.suppress(Exception):
            await edit_panel(
                f"❌ <b>Broadcast xətası:</b> <code>{escape(str(e))[:300]}</code>",
                InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
            )
    finally:
        bc_reset()


async def _mcb_bc(cb: CallbackQuery, parts: list, action: str):
    await cb.answer()
    await delete_preview()
    bc_reset()
    reset_inputs()
    counts = await asyncio.to_thread(target_counts, get_db())
    await safe_edit_cb(
        cb,
        crumb(SECTIONS["com"], "📢 Broadcast") + "\n" + LINE + "\nMesajı kimə göndərək?\n\n"
        f"👤 Şəxsi çatlarda alanlar: 👑 {'✅' if tiers.get('broadcast', 'creator') else '❌'}"
        f" · 💎 Premium {'✅' if tiers.get('broadcast', 'premium') else '❌'}"
        f" · 🆓 Free {'✅' if tiers.get('broadcast', 'free') else '❌'} "
        "<i>(💎 Premium → 🎛 İmkanlar)</i>\n\n"
        "<i>Qruplar və kanallar bot oraya əlavə ediləndə və ya orada yazışma olanda qeydə alınır.</i>",
        bc_target_kb(counts),
    )


async def _mcb_bc_t(cb: CallbackQuery, parts: list, action: str):
    target = parts[2] if len(parts) > 2 else ""
    if target not in TARGET_LABELS:
        await cb.answer()
        return
    count = len(await asyncio.to_thread(get_db().broadcast_targets, target))
    if count == 0:
        await cb.answer("Bu kateqoriyada heç kim yoxdur", show_alert=True)
        return
    await cb.answer()
    bc_reset()
    bc.update(
        stage="await_text",
        target=target,
        chat_id=cb.message.chat.id,
        panel_id=cb.message.message_id,
        preview_id=None,
    )
    await safe_edit_cb(
        cb,
        f"📢 <b>Broadcast</b> — {TARGET_LABELS[target]} (<b>{count}</b>)\n\n"
        "✍️ <b>Göndəriləcək mesajı yazın.</b>\n"
        "<i>Qalın, kursiv, link kimi formatlar saxlanılır.</i>",
        InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
    )


async def _mcb_bc_edit(cb: CallbackQuery):
    if bc.get("stage") != "confirm":
        await cb.answer("Sessiya bitib", show_alert=True)
        return
    await cb.answer()
    await delete_preview()
    bc["stage"] = "await_text"
    edit_panel = get_bc_edit_panel()
    await edit_panel(
        f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]}\n\n✍️ <b>Yeni mesajı yazın.</b>",
        InlineKeyboardMarkup(inline_keyboard=[nav_row("bc")]),
    )


async def _mcb_bc_cancel(cb: CallbackQuery):
    await cb.answer("Ləğv edildi")
    if bc.get("stage"):
        await delete_preview()
        if bc.get("panel_id"):
            edit_panel = get_bc_edit_panel()
            await edit_panel(main_text(), main_kb())
    bc_reset()


async def _mcb_bc_send(cb: CallbackQuery):
    if bc.get("stage") != "confirm" or cb.message.message_id != bc.get("preview_id"):
        await cb.answer("Sessiya bitib", show_alert=True)
        return
    await cb.answer("Göndərilir...")
    bc["stage"] = "sending"
    bc["stop"] = False
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception as _e:
        logger.debug("Broadcast edit_reply_markup xətası: %r", _e)
    bc["preview_id"] = None
    spawn(broadcast_task())


async def _mcb_bc_stop(cb: CallbackQuery):
    if bc.get("stage") == "sending":
        bc["stop"] = True
        await cb.answer("Dayandırılır...")
    else:
        await cb.answer()


async def waiting_broadcast_text(message: Message) -> bool:
    return bc.get("stage") == "await_text" and is_creator(message.from_user.id)


async def broadcast_text(message: Message):
    text = message.html_text  # qalın/kursiv/link formatı saxlanılır
    count = len(await asyncio.to_thread(get_db().broadcast_targets, bc["target"]))
    bc["text"] = text
    bc["stage"] = "confirm"
    await delete_preview()
    try:
        preview = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_btn(f"✅ Göndər ({count})", "bc_send")],
                    [_btn("✏️ Yenidən yaz", "bc_edit"), _btn("✖️ Ləğv et", "bc_cancel")],
                ]
            ),
        )
    except Exception as e:
        bc["stage"] = "await_text"
        await message.reply(
            f"❌ Mesaj göndərilə bilmir: <code>{escape(str(e))[:300]}</code>\nYenidən yazın:",
            parse_mode="HTML",
        )
        return
    bc["preview_id"] = preview.message_id
    edit_panel = get_bc_edit_panel()
    await edit_panel(
        f"📢 <b>Broadcast</b> — {TARGET_LABELS[bc['target']]} (<b>{count}</b>)\n\n"
        "👇 <i>Aşağıda önizləmə var. Göndərilən mesaj məhz belə görünəcək.</i>"
    )


async def broadcast_not_text(message: Message):
    await message.reply("✍️ Yalnız mətn göndərilə bilər. Mesajı mətn kimi yazın.")


def register_broadcast_handlers(router: Router):
    """Broadcast üçün mesaj filterlərini router-ə bağlayır."""
    router.message.register(
        broadcast_text,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_broadcast_text,
    )
    router.message.register(
        broadcast_not_text,
        F.chat.type == "private",
        ~F.text,
        waiting_broadcast_text,
    )
