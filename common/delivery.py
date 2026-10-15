"""
🚚 Vahid Audio Çatdırılma Servisi — common/delivery.py

Bütün audio yükləmə modulları (music_plugin, links_plugin, depo, test_mp3_plugin) üçün
ortaq audio göndərmə, format/bitreyt idarəsi, flood wait qorunması və keş integrasiyası.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from html import escape
from typing import Any, Callable, Optional

from aiogram import types
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def is_audio_forbidden_error(e: Exception) -> bool:
    """Telegram qruplarında musiqi/media göndərmə icazəsi məhdudiyyətini yoxlayır."""
    msg = str(e).lower()
    if "not enough rights" in msg and any(w in msg for w in ("music", "audio", "media")):
        return True
    if any(k in msg for k in (
        "send_audios_forbidden",
        "send_media_forbidden",
        "chat_send_audios_forbidden",
        "chat_send_media_forbidden",
    )):
        return True
    return False


def sanitize_title(name: str) -> str:
    """Fayl sistemi və başlıqlar üçün təhlükəsiz ad."""
    if not name:
        return "track"
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return cleaned[:100] or "track"


def build_audio_filename(artist: str, track: str, base_path: str = "") -> str:
    """Artist və mahnı adına uyğun təmiz fayl adı yaradır."""
    ext = os.path.splitext(base_path)[1] if base_path else ".m4a"
    if not ext:
        ext = ".m4a"
    a = sanitize_title(artist) if artist and artist not in ("YouTube", "Naməlum") else ""
    t = sanitize_title(track)
    fname = f"{a} - {t}{ext}" if a else f"{t}{ext}"
    return fname


class AudioDeliveryService:
    """Vahid Audio Çatdırılma Servisi."""

    @staticmethod
    async def deliver(
        target: types.Message,
        res: dict,
        artist: str,
        track_name: str,
        duration: int = 0,
        caption: str = "",
        reply_markup: Any = None,
        user_id: Optional[int] = None,
        context: Any = None,
        cancel_event: Any = None,
        activity: Any = None,
        audio_cache: Any = None,
        hide_keyboard_callback: Optional[Callable[[int, int], None]] = None,
        max_flood_retries: int = 3,
    ) -> types.Message:
        """
        Audio faylını və ya keş file_id-ni Telegram vasitəsilə təhlükəsiz göndərir.
        
        Xüsusiyyətlər:
          1. Ləğvetmə yoxlanışı (cancel_event).
          2. /codec və MP3 seçimi yoxlanışı (context.test_mp3_hook).
          3. Telegram FloodWait gözləməsi və avtomatik təkrar cəhdlər.
          4. Keşlənmiş və ya yerli faylın (FSInputFile) göndərilməsi.
          5. Göndərilmiş faylın keş bazasında yadda saxlanması.
          6. İstəyə görə inline klaviaturanın sonradan gizlədilməsi.
        """
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Yükləmə istifadəçi tərəfindən dayandırıldı")

        chat_id = target.chat.id if hasattr(target, "chat") else None
        chat_type = getattr(target.chat, "type", "") if hasattr(target, "chat") else ""
        effective_user_id = user_id or getattr(getattr(target, "from_user", None), "id", None)
        target_bot = getattr(target, "bot", None) or getattr(context, "bot", None)

        # 1) /codec & MP3 hook yoxlanışı
        mp3_hook = getattr(context, "test_mp3_hook", None) if context else None
        is_mp3_active = bool(mp3_hook and mp3_hook.is_active_for(user_id=user_id, chat_id=chat_id))

        if activity and hasattr(activity, "stage"):
            activity.stage("SEND")

        last_error = None
        for flood_try in range(max_flood_retries):
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Yükləmə istifadəçi tərəfindən dayandırıldı")

            try:
                # ── MP3 Rejimi ──
                if is_mp3_active:
                    bitrate = mp3_hook.get_bitrate(chat_id=chat_id, user_id=user_id)
                    sent = await mp3_hook.send_mp3(
                        bot=target_bot,
                        target=target,
                        res=res,
                        artist=artist,
                        track_name=track_name,
                        duration=duration,
                        caption=caption,
                        reply_markup=reply_markup,
                        bitrate=bitrate,
                    )
                # ── File ID ilə Birbaşa Göndərmə ──
                elif res.get("file_id"):
                    sent = await target.answer_audio(
                        audio=res["file_id"],
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                # ── Yerli Fayl ilə Göndərmə ──
                elif res.get("path") and os.path.isfile(res["path"]):
                    local_path = res["path"]
                    fname = res.get("fname") or build_audio_filename(artist, track_name, local_path)
                    sent = await target.answer_audio(
                        audio=FSInputFile(local_path, filename=fname),
                        title=(track_name or "")[:64],
                        performer=(artist or "")[:64],
                        duration=duration or None,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                    # Keşə yaz
                    if audio_cache and hasattr(audio_cache, "save_from_message") and res.get("vid"):
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(
                                audio_cache.save_from_message,
                                res["vid"],
                                sent,
                                track_name,
                                artist,
                                duration,
                            )
                else:
                    raise FileNotFoundError("Göndəriləcək nə fayl yolu (path), nə də file_id mövcuddur")

                # Klaviaturanı avtomatik gizlət (əgər callback verilibsə)
                if hide_keyboard_callback and sent and hasattr(sent, "chat") and hasattr(sent, "message_id"):
                    with contextlib.suppress(Exception):
                        hide_keyboard_callback(sent.chat.id, sent.message_id)

                return sent

            except Exception as e:
                last_error = e
                # FloodWait idarəsi
                wait = getattr(e, "retry_after", None)
                if wait and flood_try < max_flood_retries - 1:
                    logger.warning(f"Telegram FloodWait: {wait} saniyə gözlənilir (cəhd {flood_try+1})")
                    if activity and hasattr(activity, "flood"):
                        activity.flood(wait)
                    await asyncio.sleep(float(wait) + 1.0)
                    if activity and hasattr(activity, "stage"):
                        activity.stage("SEND")
                    continue

                # ── Qrupda Musiqi Göndərmə İcazəsi Məhdudiyyəti (Fallback) ──
                if is_audio_forbidden_error(e) and chat_type in ("group", "supergroup", "channel") and target_bot:
                    logger.warning(
                        f"Qrupda ({chat_id}) musiqi göndərmə icazəsi yoxdur: {e}. "
                        f"Şəxsi çata ({effective_user_id}) yönləndirilmə cəhdi edilir..."
                    )
                    pm_sent = None
                    if effective_user_id:
                        try:
                            local_path = res.get("path")
                            fname = res.get("fname") or build_audio_filename(artist, track_name, local_path or "")
                            if res.get("file_id"):
                                pm_sent = await target_bot.send_audio(
                                    chat_id=effective_user_id,
                                    audio=res["file_id"],
                                    caption=caption,
                                    parse_mode="HTML",
                                    reply_markup=reply_markup,
                                )
                            elif local_path and os.path.isfile(local_path):
                                pm_sent = await target_bot.send_audio(
                                    chat_id=effective_user_id,
                                    audio=FSInputFile(local_path, filename=fname),
                                    title=(track_name or "")[:64],
                                    performer=(artist or "")[:64],
                                    duration=duration or None,
                                    caption=caption,
                                    parse_mode="HTML",
                                    reply_markup=reply_markup,
                                )

                            if pm_sent:
                                # Keşə yaz
                                if audio_cache and hasattr(audio_cache, "save_from_message") and res.get("vid"):
                                    with contextlib.suppress(Exception):
                                        await asyncio.to_thread(
                                            audio_cache.save_from_message,
                                            res["vid"],
                                            pm_sent,
                                            track_name,
                                            artist,
                                            duration,
                                        )
                                # Klaviaturanı avtomatik gizlət
                                if hide_keyboard_callback and hasattr(pm_sent, "chat") and hasattr(pm_sent, "message_id"):
                                    with contextlib.suppress(Exception):
                                        hide_keyboard_callback(pm_sent.chat.id, pm_sent.message_id)
                        except Exception as pme:
                            logger.warning(f"Şəxsi çata ({effective_user_id}) audio göndərilə bilmədi: {pme}")

                    # Bot istifadəçi adını al
                    bot_uname = "dllmasterbot"
                    try:
                        me = getattr(target_bot, "_me", None)
                        if not me and hasattr(target_bot, "get_me"):
                            me = await target_bot.get_me()
                        if me and getattr(me, "username", None):
                            bot_uname = me.username
                    except Exception as _e:
                        logger.debug("udulan xəta [common/delivery.py:236]: %r", _e)

                    track_label = f"{artist} - {track_name}" if artist and track_name else (track_name or artist or "Mahnı")

                    if pm_sent:
                        notice_text = (
                            f"⚠️ <b>Bu qrupda musiqi göndərmək icazəsi bağlıdır.</b>\n\n"
                            f"📩 <b>{escape(track_label)}</b> mahnısı şəxsi çatınıza göndərildi!"
                        )
                        kb_rows = [
                            [InlineKeyboardButton(text="📩 Mahnını aç (Şəxsi çat)", url=f"https://t.me/{bot_uname}")],
                            [InlineKeyboardButton(text="🛡 Qrup Admini üçün bələdçi", url=f"https://t.me/{bot_uname}?start=help_group")],
                        ]
                    else:
                        vid = res.get("vid", "")
                        start_url = f"https://t.me/{bot_uname}?start=dl_{vid}" if vid else f"https://t.me/{bot_uname}"
                        notice_text = (
                            f"⚠️ <b>Bu qrupda musiqi göndərmək icazəsi bağlıdır!</b>\n\n"
                            f"🎵 <b>{escape(track_label)}</b> mahnısını əldə etmək üçün botu şəxsi çatda başladın (Start) "
                            f"və ya qrup adminindən bota musiqi göndərmə icazəsi verməsini xahiş edin."
                        )
                        kb_rows = [
                            [InlineKeyboardButton(text="📩 Bota keç (Start)", url=start_url)],
                            [InlineKeyboardButton(text="🛡 Qrup Admini üçün bələdçi", url=f"https://t.me/{bot_uname}?start=help_group")],
                        ]

                    notice_msg = None
                    try:
                        from common.utilities import delete_after
                        notice_msg = await target_bot.send_message(
                            chat_id=chat_id,
                            text=notice_text,
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                        )
                        delete_after(notice_msg, 90.0)
                    except Exception as ge:
                        logger.warning(f"Qrupa icazə bildirişi göndərilə bilmədi ({chat_id}): {ge}")

                    if pm_sent:
                        return pm_sent
                    if notice_msg:
                        return notice_msg

                raise last_error

        if last_error:
            raise last_error
        raise RuntimeError("Audio göndərilmədi")
