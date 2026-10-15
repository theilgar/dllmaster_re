"""
bot.menus.handlers.settings_flow
Ayarlar, Premium, Su nişanı, Mətn şablonları və Speedtest axınları.
"""

import asyncio
from html import escape
import logging
import re

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.core.welcome import set_media, set_text_template
from bot.menus.navigation import nav_row
from bot.menus.views.music import mus_view
from bot.menus.state import (
    bc,
    bc_reset,
    delete_preview,
    get_context,
    prm,
    reset_inputs,
    safe_edit_cb,
    tx,
    wm,
)
from bot.menus.views.messages import (
    TEXT_SLOTS,
    media_slot,
    ms_list_panel,
    ms_panel,
    send_sample,
)
from bot.menus.views.premium_stars import premium_panel
from bot.menus.views.stats import fmt_until
from bot.menus.views.system_hub import biz_view, sub_kb, sys_kb, sys_text
from bot.menus.views.users_chats import user_label
from common import tiers
from common.database import (
    creator_caption_enabled,
    get_db,
    is_maintenance_mode,
    is_tip_prompt_enabled,
    set_creator_caption,
    set_maintenance_mode,
    set_tip_prompt_enabled,
)
from common.diagnostics import run_speedtest

logger = logging.getLogger(__name__)

speed_lock = asyncio.Lock()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    from bot.menus.keyboards import menu_btn
    return menu_btn(text, data)


async def _mcb_prm_g(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    try:
        user_id, days = int(parts[2]), int(parts[3])
    except (IndexError, ValueError):
        await cb.answer()
        return
    db = get_db()
    until = await asyncio.to_thread(db.grant_premium, user_id, days or None, cb.from_user.id)
    reset_inputs()
    u = await asyncio.to_thread(db.get_user, user_id)
    await cb.answer("💎 Premium verildi")

    notified = False
    try:
        await ctx.bot.send_message(
            user_id,
            f"🎉 <b>Sizə Premium verildi!</b>\n⏳ {fmt_until(until)}\n\n"
            f"✨ <b>Artıq istifadə edə biləcəyin imkanlar:</b>\n{tiers.perks_html()}\n\n"
            "<i>Statusunu istənilən vaxt /premium ilə görə bilərsən.</i>",
            parse_mode="HTML",
        )
        notified = True
    except Exception as e:
        logger.info("Premium bildirişi göndərilmədi (%s): %s", user_id, e)

    await safe_edit_cb(
        cb,
        "✅ <b>Premium verildi</b>\n\n"
        f"👤 {user_label(u, user_id)}\n"
        f"⏳ {fmt_until(until)}\n"
        f"🔔 Bildiriş: {'göndərildi' if notified else 'göndərilmədi (botu başlatmayıb və ya bloklayıb)'}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn("➕ Daha birinə", "prm_add")],
                nav_row("prm:0"),
            ]
        ),
    )


async def _mcb_prm_rm(cb: CallbackQuery, parts: list, action: str):
    try:
        user_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        await cb.answer()
        return
    await cb.answer()
    db = get_db()
    u = await asyncio.to_thread(db.get_user, user_id)
    cur = await asyncio.to_thread(db.get_premium, user_id)
    if not cur:
        text, kb = await premium_panel(page)
        await safe_edit_cb(cb, "ℹ️ <i>Bu istifadəçinin artıq premiumu yoxdur.</i>\n\n" + text, kb)
        return
    await safe_edit_cb(
        cb,
        f"⚠️ <b>Premium alınsın?</b>\n\n👤 {user_label(u, user_id)}\n⏳ {fmt_until(cur['until'])}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                panel.confirm(f"menu:prm_rmy:{user_id}:{page}", f"menu:prm:{page}", "premiumu al"),
            ]
        ),
    )


async def _mcb_wm_set(cb: CallbackQuery, parts: list, action: str):
    slot = parts[2] if len(parts) > 2 else ""
    if slot not in TEXT_SLOTS:
        await cb.answer()
        return
    await cb.answer()
    reset_inputs()
    wm.update(
        stage="await_media",
        slot=media_slot(slot),
        back=slot,
        chat_id=cb.message.chat.id,
        panel_id=cb.message.message_id,
    )
    shared = (
        "\n\n<i>ℹ️ Bu media salam mesajı ilə ortaqdır — orada da dəyişəcək.</i>"
        if media_slot(slot) != slot
        else ""
    )
    await safe_edit_cb(
        cb,
        f"🖼 <b>{escape(TEXT_SLOTS[slot]['label'])}</b>\n\n"
        "📤 Yeni <b>şəkil</b>, <b>GIF</b> və ya <b>video</b> göndərin.\n"
        "<i>Mesajın mətni onun altında caption kimi görünəcək.</i>" + shared,
        InlineKeyboardMarkup(inline_keyboard=[nav_row(f"ms:{slot}")]),
    )


async def _mcb_tx_set(cb: CallbackQuery, parts: list, action: str):
    slot = parts[2] if len(parts) > 2 else ""
    if slot not in TEXT_SLOTS:
        await cb.answer()
        return
    await cb.answer()
    reset_inputs()
    tx.update(
        stage="await_text",
        slot=slot,
        chat_id=cb.message.chat.id,
        panel_id=cb.message.message_id,
    )
    vars_line = ", ".join(f"<code>{escape(k)}</code>" for k in TEXT_SLOTS[slot]["vars"])
    await safe_edit_cb(
        cb,
        f"📝 <b>{escape(TEXT_SLOTS[slot]['label'])}</b>\n\n"
        "✍️ <b>Yeni mətni yazın.</b>\n"
        "Qalın, kursiv, link, <code>kod</code> kimi formatlar saxlanılır.\n\n"
        f"🔤 İstifadə edə biləcəyin dəyişənlər: {vars_line}\n\n"
        "<i>💡 Köhnə mətni əsas götürmək üçün əvvəlcə 📋 Hazırkı mətn ilə onu al, kopyala və dəyiş.</i>",
        InlineKeyboardMarkup(inline_keyboard=[nav_row(f"ms:{slot}")]),
    )


async def _mcb_speed(cb: CallbackQuery, parts: list, action: str):
    if speed_lock.locked():
        await cb.answer("Speedtest artıq işləyir...")
        return
    await cb.answer()
    async with speed_lock:
        await safe_edit_cb(
            cb, "🚀 <b>Speedtest</b>\n\n⏳ <i>Ölçülür... (30–60 saniyə çəkə bilər)</i>"
        )
        try:
            r = await run_speedtest()
            text = (
                "🚀 <b>Speedtest</b>\n\n"
                f"📥 Download: <b>{r['down']:.2f} Mbps</b>\n"
                f"📤 Upload: <b>{r['up']:.2f} Mbps</b>\n"
                f"🏓 Ping: <b>{r['ping']:.1f} ms</b>\n\n"
                f"🌐 Server: {escape(r['server'])}\n"
                f"🏢 ISP: {escape(str(r['isp']))}"
            )
        except Exception as e:
            logger.error("Speedtest xətası: %s", e)
            text = f"🚀 <b>Speedtest</b>\n\n❌ <code>{escape(str(e))[:400]}</code>"
        await safe_edit_cb(cb, text, sub_kb("speed"))


async def _mcb_sbtn(cb: CallbackQuery, parts: list, action: str):
    """🔘 /start düymələri: menu:sbtn · menu:sbtn_tog:<açar> · menu:sbtn_reset · menu:sbtn_prev."""
    from bot.menus.views.system_hub import start_buttons_view
    from common.database import (
        START_BUTTON_META,
        is_start_button_enabled,
        reset_start_buttons,
        set_start_button_enabled,
    )

    note = ""
    if action == "sbtn_tog":
        key = parts[2] if len(parts) > 2 else ""
        if key in START_BUTTON_META:
            on = not is_start_button_enabled(key)
            await asyncio.to_thread(set_start_button_enabled, key, on)
            note = f"{START_BUTTON_META[key]['label']}: {'🟢 aktiv' if on else '🔴 gizlədildi'}"
    elif action == "sbtn_reset":
        await asyncio.to_thread(reset_start_buttons)
        note = "♻️ Bütün start düymələri aktiv edildi"
    elif action == "sbtn_prev":
        from bot.plugins import start_plugin

        me = await cb.bot.me()
        user = cb.from_user
        text = start_plugin.build_private_text(user.first_name or "", me.username, user.id)
        await cb.answer()
        await cb.bot.send_message(
            user.id, text, parse_mode="HTML",
            reply_markup=start_plugin.private_keyboard(me.username, user.id),
        )
        return
    await cb.answer(note or None)
    await safe_edit_cb(cb, *start_buttons_view(note))


async def handle_settings_action(cb: CallbackQuery, parts: list[str], action: str) -> bool:
    """Settings, Premium, Watermark, Text və Speedtest hərəkətlərini idarə edir."""
    if action in ("sbtn", "sbtn_tog", "sbtn_reset", "sbtn_prev"):
        await _mcb_sbtn(cb, parts, action)
        return True

    if action == "prm":
        await cb.answer()
        if bc.get("stage") == "await_text":
            await delete_preview()
            bc_reset()
        reset_inputs()
        text, kb = await premium_panel(int(parts[2]) if len(parts) > 2 else 0)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "prm_add":
        await cb.answer()
        if bc.get("stage") == "await_text":
            await delete_preview()
            bc_reset()
        prm.clear()
        prm.update(
            stage="await_user",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        await safe_edit_cb(
            cb,
            "💎 <b>Premium ver</b>\n\n"
            "✍️ İstifadəçinin <b>ID</b>-sini və ya <b>@username</b>-ini yazın:\n"
            "<i>məs. <code>123456789</code> və ya <code>@username</code></i>",
            InlineKeyboardMarkup(inline_keyboard=[nav_row("prm:0")]),
        )
        return True

    if action == "prm_g":
        await _mcb_prm_g(cb, parts, action)
        return True

    if action == "prm_rm":
        await _mcb_prm_rm(cb, parts, action)
        return True

    if action == "prm_rmy":
        try:
            user_id = int(parts[2])
            page = int(parts[3]) if len(parts) > 3 else 0
        except (IndexError, ValueError):
            await cb.answer()
            return True
        removed = await asyncio.to_thread(get_db().revoke_premium, user_id)
        await cb.answer("Premium alındı" if removed else "Artıq premium deyil")
        text, kb = await premium_panel(page)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "cap":
        enabled = not creator_caption_enabled()
        await asyncio.to_thread(set_creator_caption, enabled)
        await cb.answer(
            (
                '✅ Caption açıldı: sənə göndərilən mahnıların altında "via | song.link" yazılacaq'
                if enabled
                else "❌ Caption bağlandı: sənə göndərilən mahnılar yazısız olacaq"
            )
            + "\n\n🆓 Free: həmişə açıq · 💎 Premium: həmişə bağlı",
            show_alert=True,
        )
        await safe_edit_cb(cb, *mus_view())
        return True

    if action in ("ms", "wm", "tx"):
        await cb.answer()
        if bc.get("stage") == "await_text":
            await delete_preview()
            bc_reset()
        reset_inputs()
        slot = parts[2] if len(parts) > 2 else None
        text, kb = ms_panel(slot) if slot in TEXT_SLOTS else ms_list_panel()
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "wm_set":
        await _mcb_wm_set(cb, parts, action)
        return True

    if action in ("wm_def", "wm_none"):
        slot = parts[2] if len(parts) > 2 else ""
        if slot not in TEXT_SLOTS:
            await cb.answer()
            return True
        kind = "default" if action == "wm_def" else "none"
        await asyncio.to_thread(set_media, media_slot(slot), kind)
        reset_inputs()
        await cb.answer("✅ Yadda saxlandı")
        text, kb = ms_panel(slot)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "tx_set":
        await _mcb_tx_set(cb, parts, action)
        return True

    if action == "tx_show":
        slot = parts[2] if len(parts) > 2 else ""
        if slot not in TEXT_SLOTS:
            await cb.answer()
            return True
        await cb.answer()
        import bot.plugins.start_plugin as sp

        template = await asyncio.to_thread(sp.text_template, slot)
        try:
            await cb.message.answer(template, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            await cb.message.answer(
                f"❌ Göstərilmədi: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML"
            )
        return True

    if action == "tx_prev":
        slot = parts[2] if len(parts) > 2 else ""
        if slot not in TEXT_SLOTS:
            await cb.answer()
            return True
        await cb.answer("Önizləmə göndərilir...")
        try:
            await send_sample(
                cb.message.chat.id, slot, cb.from_user.first_name or "Dostum", cb.from_user.id
            )
        except Exception as e:
            await cb.message.answer(
                f"❌ Önizləmə alınmadı: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML"
            )
        return True

    if action == "tx_def":
        slot = parts[2] if len(parts) > 2 else ""
        if slot not in TEXT_SLOTS:
            await cb.answer()
            return True
        await asyncio.to_thread(set_text_template, slot, None)
        reset_inputs()
        await cb.answer("↩️ Default mətnə qaytarıldı")
        text, kb = ms_panel(slot)
        await safe_edit_cb(cb, text, kb)
        return True

    if action == "toggle_maint":                              # köhnə düymə — indi 🛠 panelinin açarı ilə eyni məntiq
        from bot.menus.handlers.maint_flow import apply_toggle

        note = await apply_toggle(get_context())
        await cb.answer(re.sub(r"<[^>]+>", "", note)[:190], show_alert=True)
        from bot.menus.views.maintenance import maint_view

        await safe_edit_cb(cb, *maint_view(note))
        return True

    if action == "toggle_tip":
        new_state = not is_tip_prompt_enabled()
        await asyncio.to_thread(set_tip_prompt_enabled, new_state)
        await cb.answer(
            "☕ Bəxşiş təklifi: AKTİV EDİLDİ\n(Yükləmədən sonra 1 dəq. görünəcək)"
            if new_state
            else "🔴 Bəxşiş təklifi: DEAKTİV EDİLDİ",
            show_alert=True,
        )
        await safe_edit_cb(cb, *biz_view())
        return True

    if action == "speed":
        await _mcb_speed(cb, parts, action)
        return True

    return False
