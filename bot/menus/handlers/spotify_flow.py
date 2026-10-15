"""
bot.menus.handlers.spotify_flow
Spotify API parametrləri, OAuth autentifikasiyası və açarların daxil edilməsi axını.
"""

import asyncio
import logging
import os
from html import escape

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.menus.state import (
    get_context,
    is_creator,
    reset_inputs,
    safe_edit_cb,
    sp_input,
)
from bot.menus.views.base import LINE, SECTIONS, crumb
from bot.menus.views.spotify import (
    spotify_panel,
    start_oauth_listener,
    stop_oauth_listener,
)
from common.database import get_db
from common.menu import _btn
from common.utilities import update_config_env

logger = logging.getLogger(__name__)


async def _mcb_sp(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    sub = parts[2] if len(parts) > 2 else ""
    if not sub or sub == "panel":
        await stop_oauth_listener()
        reset_inputs()
        await cb.answer()
        await safe_edit_cb(cb, *spotify_panel())
    elif sub == "oauth":
        sp = getattr(ctx, "spotify", None)
        if not sp or not sp.client_id or not sp.client_secret:
            await cb.answer("Əvvəlcə Client ID və Client Secret təyin edin!", show_alert=True)
            return
        await cb.answer()
        reset_inputs()
        sp_input["stage"] = "await_oauth"
        sp_input["chat_id"] = cb.message.chat.id
        sp_input["panel_id"] = cb.message.message_id
        auth_url = sp.get_authorize_url()
        sp_input["auth_url"] = auth_url

        await start_oauth_listener(ctx, cb.message.chat.id, cb.message.message_id, sp_input)

        prompt = (
            crumb(SECTIONS["mus"], "🟢 Spotify API", "🔑 OAuth Giriş") + "\n" + LINE + "\n"
            "🔑 <b>Spotify Hesabı ilə Giriş (OAuth)</b>\n\n"
            "Spotify hesabınızı (Premium) qoşaraq playlistlərdəki <b>bütün mahnıları (100-dən çox)</b> "
            "və şəxsi playlistləri birbaşa yükləyə bilərsiniz.\n\n"
            "<b>Necə daxil olmaq olar?</b>\n"
            "1️⃣ <b>Brauzerdə açın:</b>\n"
            'Aşağıdakı <b>"🔗 Giriş Et (Brauzerdə aç)"</b> düyməsinə toxunub Spotify hesabınızla daxil olun və icazə verin.\n'
            '<i>(Serverdəsinizsə "🖥 Server brauzerində aç" düyməsini də sıxa bilərsiniz).</i>\n\n'
            "2️⃣ <b>Təsdiqləyin:</b>\n"
            "• Əgər bot ilə eyni server/kompüterdəsinizsə, brauzer avtomatik təsdiqlənəcək.\n"
            "• Əgər telefonunuzda və ya başqa brauzerdə açmısınızsa:\n"
            "Təsdiqdən sonra açılan səhifənin ünvanını (<code>http://127.0.0.1:8888/callback?code=...</code>) "
            "və ya həmin linki <b>kopyalayıb bura göndərin</b>.\n\n"
            "ℹ️ <i>Redirect URI: <code>http://127.0.0.1:8888/callback</code></i>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Brauzerdə giriş et", url=auth_url)],
                [_btn("🖥 Server brauzerində aç", "sp:open_srv")],
                [_btn("✖️ Ləğv et", "sp:cancel")],
            ]
        )
        await safe_edit_cb(cb, prompt, kb)
    elif sub == "open_srv":
        auth_url = sp_input.get("auth_url")
        sp = getattr(ctx, "spotify", None)
        if not auth_url and sp:
            auth_url = sp.get_authorize_url()
        if not auth_url:
            await cb.answer("Giriş linki tapılmadı", show_alert=True)
            return
        try:
            import webbrowser

            webbrowser.open(auth_url)
            await cb.answer("🖥 Server brauzerində açıldı! Daxil olub icazə verin.", show_alert=True)
        except Exception as e:
            await cb.answer(f"Server brauzeri açıla bilmədi: {e}", show_alert=True)
    elif sub == "logout":
        sp = getattr(ctx, "spotify", None)
        if sp:
            await asyncio.to_thread(sp.logout_oauth)  # Spotify-a yenidən qoşulur (şəbəkə)
        await cb.answer("✅ Spotify hesabından çıxış edildi.", show_alert=True)
        await safe_edit_cb(
            cb,
            *spotify_panel(
                "✅ <b>İstifadəçi hesabından çıxış edildi.</b> Tətbiq rejiminə qayıdıldı."
            ),
        )
    elif sub == "set_id":
        await stop_oauth_listener()
        await cb.answer()
        reset_inputs()
        sp_input["stage"] = "await_id"
        sp_input["chat_id"] = cb.message.chat.id
        sp_input["panel_id"] = cb.message.message_id
        prompt = (
            crumb(SECTIONS["mus"], "🟢 Spotify API", "✏️ Client ID") + "\n" + LINE + "\n"
            "✏️ Yeni <b>Spotify Client ID</b> göndərin:\n\n"
            "<i>(developer.spotify.com › Dashboard › App › Settings › Client ID)</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "sp:cancel")]])
        await safe_edit_cb(cb, prompt, kb)
    elif sub == "set_sec":
        await stop_oauth_listener()
        await cb.answer()
        reset_inputs()
        sp_input["stage"] = "await_sec"
        sp_input["chat_id"] = cb.message.chat.id
        sp_input["panel_id"] = cb.message.message_id
        prompt = (
            crumb(SECTIONS["mus"], "🟢 Spotify API", "✏️ Client Secret") + "\n" + LINE + "\n"
            "✏️ Yeni <b>Spotify Client Secret</b> göndərin:\n\n"
            "<i>(developer.spotify.com › Dashboard › App › Settings › View client secret)</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "sp:cancel")]])
        await safe_edit_cb(cb, prompt, kb)
    elif sub == "cancel":
        await stop_oauth_listener()
        await cb.answer("Ləğv edildi")
        reset_inputs()
        await safe_edit_cb(cb, *spotify_panel())
    elif sub == "test":
        await cb.answer("🧪 Yoxlanılır...")
        sp = getattr(ctx, "spotify", None)
        if not sp:
            await safe_edit_cb(cb, *spotify_panel("❌ Spotify modulu yüklənməyib."))
        else:
            ok, msg = await asyncio.to_thread(sp.test_connection)
            if ok:
                await cb.answer("✅ Spotify API aktivdir və uğurla işləyir!", show_alert=True)
                await safe_edit_cb(cb, *spotify_panel(f"✅ <b>Bağlantı uğurludur:</b> {msg}"))
            else:
                await cb.answer(f"❌ Xəta: {msg[:100]}", show_alert=True)
                await safe_edit_cb(
                    cb,
                    *spotify_panel(
                        f"❌ <b>Bağlantı xətası:</b> <code>{escape(msg[:200])}</code>"
                    ),
                )


async def waiting_sp_text(message: Message) -> bool:
    return sp_input.get("stage") in ("await_id", "await_sec", "await_oauth") and is_creator(
        message.from_user.id
    )


async def spotify_credential_input(message: Message):
    ctx = get_context()
    val = message.text.strip()
    stage = sp_input.get("stage")
    panel = dict(chat_id=sp_input["chat_id"], message_id=sp_input["panel_id"], parse_mode="HTML")
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("spotify_credential_input mesaj silinmə xətası: %r", _e)

    db = get_db()
    sp = getattr(ctx, "spotify", None)

    if stage == "await_oauth":
        if not sp:
            return
        ok, msg = await asyncio.to_thread(sp.complete_oauth, val)
        await stop_oauth_listener()
        sp_input["stage"] = None
        note = (
            f"✅ <b>Spotify hesabı uğurla qoşuldu!</b>\n{escape(str(msg))}"
            if ok
            else f"❌ <b>Giriş xətası:</b> {escape(str(msg))}"
        )
        text, kb = spotify_panel(note)
        try:
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel)
        except Exception as e:
            logger.warning("Spotify panel yenilənmədi: %s", e)
        logger.info("🟢 Spotify OAuth input nəticəsi: %s (%s)", ok, msg)
        return

    if not val or len(val) < 8 or " " in val or "\n" in val:
        prompt = (
            crumb(SECTIONS["mus"], "🟢 Spotify API", "Xəta") + "\n" + LINE + "\n"
            "❌ <b>Daxil edilən dəyər düzgün formatda deyil!</b>\n\n"
            "Açar boşluqsuz, hərf və rəqəmlərdən ibarət olmalıdır.\n"
            "Yenidən göndərin və ya ləğv edin:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "sp:cancel")]])
        try:
            await ctx.bot.edit_message_text(text=prompt, reply_markup=kb, **panel)
        except Exception as e:
            logger.warning("Spotify panel yenilənmədi: %s", e)
        return

    if stage == "await_id":
        update_config_env("SPOTIFY_CLIENT_ID", val)
        os.environ["SPOTIFY_CLIENT_ID"] = val
        await asyncio.to_thread(db.set_setting, "spotify:client_id", val)
        sec = (
            os.getenv("SPOTIFY_CLIENT_SECRET")
            or (await asyncio.to_thread(db.get_setting, "spotify:client_secret"))
            or ""
        )
        ok, msg = False, ""
        if sp:
            ok, msg = await asyncio.to_thread(sp.reload, val, sec)
        sp_input["stage"] = None
        note = "✅ <b>Client ID saxlandı!</b>" + (f" ({msg})" if ok else f"\n⚠️ Xəbərdarlıq: {msg}")
        text, kb = spotify_panel(note)
        try:
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel)
        except Exception as e:
            logger.warning("Spotify panel yenilənmədi: %s", e)
        logger.info("🟢 Spotify Client ID yeniləndi.")
    elif stage == "await_sec":
        update_config_env("SPOTIFY_CLIENT_SECRET", val)
        os.environ["SPOTIFY_CLIENT_SECRET"] = val
        await asyncio.to_thread(db.set_setting, "spotify:client_secret", val)
        cid = (
            os.getenv("SPOTIFY_CLIENT_ID")
            or (await asyncio.to_thread(db.get_setting, "spotify:client_id"))
            or ""
        )
        ok, msg = False, ""
        if sp:
            ok, msg = await asyncio.to_thread(sp.reload, cid, val)
        sp_input["stage"] = None
        note = "✅ <b>Client Secret saxlandı!</b>" + (
            f" ({msg})" if ok else f"\n⚠️ Xəbərdarlıq: {msg}"
        )
        text, kb = spotify_panel(note)
        try:
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel)
        except Exception as e:
            logger.warning("Spotify panel yenilənmədi: %s", e)
        logger.info("🟢 Spotify Client Secret yeniləndi.")


def register_spotify_handlers(router: Router):
    router.message.register(
        spotify_credential_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_sp_text,
    )
