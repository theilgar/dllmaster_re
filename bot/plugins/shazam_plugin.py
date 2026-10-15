"""
🎵 Shazam Musiqi Tanıma Plugini — bot/plugins/shazam_plugin.py

İmkanlar:
  • /shazam, /find, /tani, /tanı — İstənilən səsli mesaja (voice), audioya, videoya və ya video nota
    reply (cavab) verərək mahnını 1-2 saniyə ərzində dəqiqliklə tapır.
  • Şəxsi çatda birbaşa göndərilən səs yazısı və ya dairəvi videodan da avtomatik musiqi tanıma.
  • Mahnı adı, ifaçı, albom, janr və yüksək keyfiyyətli üz qabığı (Cover Art).
  • İnteraktiv düymələr:
      - [📥 Bu Mahnını Yüklə] — Bot dərhal həmin mahnını axtarıb çatda təqdim edir
      - [🔎 İnline Axtar] — Çatda birbaşa inline axtarış rejimini açır
      - [📝 Mahnı Sözləri] — Mahnının sözləri (lyrics) varsa göstərir
      - [🔗 Shazam] — Rəsmi Shazam keçidi
"""

import contextlib
import hashlib
import logging
import os
import time
from collections import OrderedDict
from html import escape
from typing import Any, Tuple
from urllib.parse import quote

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.utilities import ui_styled

logger = logging.getLogger(__name__)

PRIORITY = 20  # audio_editor (25) və digərlərindən əvvəl yüklənir, /shazam komandası hər zaman işləyir

# Shazam instansiyası və keş
_SHAZAM_CLIENT = None
_SHAZAM_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_PENDING_MEDIA: "OrderedDict[Tuple[int, int], Tuple[Any, str]]" = OrderedDict()
CACHE_MAX = 300
TEMP_DIR = os.path.join("data", "shazam_tmp")


def _get_shazam():
    global _SHAZAM_CLIENT
    if _SHAZAM_CLIENT is None:
        from shazamio import Shazam
        _SHAZAM_CLIENT = Shazam()
    return _SHAZAM_CLIENT


def _remember_pending(chat_id: int, message_id: int, media, media_type: str):
    _PENDING_MEDIA[(chat_id, message_id)] = (media, media_type)
    while len(_PENDING_MEDIA) > 100:
        _PENDING_MEDIA.popitem(last=False)


def _get_pending(chat_id: int, message_id: int | None):
    if not message_id:
        return None
    return _PENDING_MEDIA.get((chat_id, message_id))


async def _extract_audio_clip(input_path: str, output_path: str, duration_sec: int = 25) -> bool:
    """ffmpeg ilə fayldan ilk ~25 saniyəlik MP3 parça (vaxt bitsə proses öldürülür — common.media)."""
    from common.media import run_ffmpeg
    rc, err = await run_ffmpeg("-i", input_path, "-ss", "0", "-t", str(duration_sec), "-vn",
                               "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", output_path, timeout=20)
    if rc != 0:
        logger.warning(f"ffmpeg audio kəsmə xətası: {err[:200]}")
    return os.path.exists(output_path) and os.path.getsize(output_path) > 1000


def _remember(key: str, item: dict):
    _SHAZAM_CACHE[key] = item
    _SHAZAM_CACHE.move_to_end(key)
    while len(_SHAZAM_CACHE) > CACHE_MAX:
        _SHAZAM_CACHE.popitem(last=False)


def _parse_track(track: dict) -> dict:
    title = track.get("title") or "Naməlum Mahnı"
    artist = track.get("subtitle") or "Naməlum İfaçı"
    item = {"title": title, "artist": artist, "query": f"{artist} - {title}",
            "genre": (track.get("genres") or {}).get("primary"), "album": None, "year": None, "lyrics": None}
    for s in track.get("sections", []):
        if s.get("type") == "SONG":
            for m in s.get("metadata", []):
                key = (m.get("title") or "").lower()
                if key == "album":
                    item["album"] = m.get("text")
                elif key in ("released", "year"):
                    item["year"] = m.get("text")
        elif s.get("type") == "LYRICS" and s.get("text"):
            item["lyrics"] = "\n".join(s["text"])
    images = track.get("images") or {}
    item["cover_url"] = images.get("coverarthq") or images.get("coverart") or images.get("background")
    item["shazam_url"] = track.get("url") or ("https://www.shazam.com/search/" + quote(f"{title} {artist}"))
    return item


def _result_view(item: dict, cache_key: str) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🎧 <b>Musiqi tapıldı (Shazam)</b>", "━━━━━━━━━━━━━━━━━━",
             f"🎵 <b>Mahnı:</b> <b>{escape(item['title'])}</b>", f"👤 <b>İfaçı:</b> <b>{escape(item['artist'])}</b>"]
    for icon, label, key in (("💿", "Albom", "album"), ("🏷", "Janr", "genre"), ("📅", "Buraxılış ili", "year")):
        if item.get(key):
            lines.append(f"{icon} <b>{label}:</b> {escape(str(item[key]))}")
    rows = [[InlineKeyboardButton(text="📥 Yüklə", callback_data=f"shz:dl:{cache_key}"),
             InlineKeyboardButton(text="🔎 Inline axtar", switch_inline_query_current_chat=item["query"])]]
    row2 = []
    if item.get("lyrics"):
        row2.append(InlineKeyboardButton(text="📝 Sözlər", callback_data=f"shz:lyr:{cache_key}"))
    if item.get("shazam_url"):
        row2.append(InlineKeyboardButton(text="🔗 Shazam", url=item["shazam_url"]))
    if row2:
        rows.append(row2)
    rows.append([InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def recognize_file(input_path: str) -> dict | None:
    """Yerli faylı (audio və ya video) Shazam ilə tanıyır.

    Mahnı tapılsa _parse_track məlumatlarını lüğət şəklində qaytarır,
    tapılmasa None qaytarır.
    """
    if not os.path.exists(input_path) or os.path.getsize(input_path) < 500:
        return None

    os.makedirs(TEMP_DIR, exist_ok=True)
    clip_path = os.path.join(TEMP_DIR, f"rf_clip_{os.getpid()}_{int(time.time() * 1000)}.mp3")
    target = input_path
    clip_created = False
    try:
        ok = await _extract_audio_clip(input_path, clip_path, duration_sec=25)
        if ok:
            target = clip_path
            clip_created = True

        shazam = _get_shazam()
        res = await shazam.recognize(target)
        track = res.get("track") if isinstance(res, dict) else None
        if not track or not track.get("title"):
            return None

        cache_key = hashlib.md5(f"{track.get('subtitle', '')}:{track.get('title', '')}".encode()).hexdigest()[:12]
        item = _parse_track(track)
        item["cache_key"] = cache_key
        _remember(cache_key, item)
        return item
    except Exception as e:
        logger.error(f"Shazam recognize_file xətası: {e}", exc_info=True)
        return None
    finally:
        if clip_created and os.path.exists(clip_path):
            with contextlib.suppress(Exception):
                os.remove(clip_path)


async def _process_recognition(
    message: types.Message,
    target_media,
    media_type: str,
    context,
    status_msg: types.Message | None = None,
):
    """Verilmiş media obyektini yükləyir və Shazam ilə tanıyır."""
    bot = context.bot
    if status_msg is None:
        status_msg = await message.reply("🎧 <i>Səs dinlənilir və Shazam ilə axtarılır...</i>", parse_mode="HTML")
    else:
        with contextlib.suppress(Exception):
            await status_msg.edit_text("🎧 <i>Səs dinlənilir və Shazam ilə axtarılır...</i>", parse_mode="HTML")

    os.makedirs(TEMP_DIR, exist_ok=True)
    temp_in = os.path.join(TEMP_DIR, f"in_{message.message_id}_{message.chat.id}")

    try:
        # Faylı teleqramdan yükləyirik
        await bot.download(target_media, destination=temp_in)
        if not os.path.exists(temp_in) or os.path.getsize(temp_in) < 500:
            await status_msg.edit_text("⚠️ <i>Səs faylı yüklənə bilmədi və ya çox qısadır.</i>", parse_mode="HTML")
            return

        item = await recognize_file(temp_in)
        if not item or not item.get("title"):
            close_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
            ])
            await status_msg.edit_text(
                "😔 <b>Təəssüf ki, heç bir mahnı tapılmadı.</b>\n"
                "<i>Səs çox qısa ola bilər, ətraf küy çoxdur və ya bu musiqi Shazam bazasında yoxdur.</i>",
                parse_mode="HTML",
                reply_markup=close_kb,
            )
            return

        cache_key = item.get("cache_key") or hashlib.md5(f"{item.get('artist', '')}:{item.get('title', '')}".encode()).hexdigest()[:12]
        _remember(cache_key, item)
        text, kb = _result_view(item, cache_key)
        cover_url = item.get("cover_url")

        sent = False
        if cover_url:
            try:
                await message.reply_photo(photo=cover_url, caption=text, parse_mode="HTML", reply_markup=kb)
                sent = True
            except Exception as e:
                logger.debug(f"Shazam coverart göndərilmədi, mətnlə göndərilir: {e}")

        if not sent:
            try:
                await status_msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await message.reply(text, parse_mode="HTML", reply_markup=kb)
        else:
            with contextlib.suppress(Exception):
                await status_msg.delete()

    except Exception as e:
        logger.error(f"Shazam tanıma prosesi xətası: {e}", exc_info=True)
        await status_msg.edit_text(f"⚠️ <i>Musiqi axtarışı zamanı xəta baş verdi: {escape(str(e)[:100])}</i>", parse_mode="HTML")
    finally:
        if os.path.exists(temp_in):
            with contextlib.suppress(Exception):
                os.remove(temp_in)


def setup(context):
    dp = context.dp
    context.shazam_recognize_file = recognize_file

    # ── /shazam, /find, /tani, /tanı əmri ──
    @dp.message(Command("shazam", "find", "tani", "tanı"))
    async def shazam_command(message: types.Message):
        # 1. Reply yoxlanışı
        rep = message.reply_to_message
        target = None
        mtype = None

        if rep:
            if rep.voice:
                target, mtype = rep.voice, "voice"
            elif rep.audio:
                target, mtype = rep.audio, "audio"
            elif rep.video_note:
                target, mtype = rep.video_note, "video_note"
            elif rep.video:
                target, mtype = rep.video, "video"
            elif rep.document and (
                (rep.document.mime_type or "").startswith(("audio/", "video/"))
                or (rep.document.file_name or "").lower().endswith(
                    (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".mp4", ".mov", ".mkv", ".webm")
                )
            ):
                target, mtype = rep.document, "document"

        # 2. Mesajın özündə media varsa (məs. faylın caption-ında /shazam)
        if not target:
            if message.voice:
                target, mtype = message.voice, "voice"
            elif message.audio:
                target, mtype = message.audio, "audio"
            elif message.video_note:
                target, mtype = message.video_note, "video_note"
            elif message.video:
                target, mtype = message.video, "video"
            elif message.document and (
                (message.document.mime_type or "").startswith(("audio/", "video/"))
                or (message.document.file_name or "").lower().endswith(
                    (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".mp4", ".mov", ".mkv", ".webm")
                )
            ):
                target, mtype = message.document, "document"

        # Media tapıldısa dərhal tanı
        if target:
            with ui_styled("user"):
                await _process_recognition(message, target, mtype, context)
            return

        # Əgər reply və media yoxdursa bələdçi göstər
        guide_text = (
            "🔍 <b>Shazam — Musiqi Tanıma Bələdçisi</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Mahnının adını tapmaq üçün 3 asan yol:\n\n"
            "1️⃣ <b>Mesaja Cavab (Reply):</b>\n"
            "   • Çatdakı səs yazısına, audioya və ya videoya reply edib <code>/shazam</code> yazın.\n\n"
            "2️⃣ <b>Birbaşa Səs / Video Göndər:</b>\n"
            "   • Şəxsi çatda bota səsli mesaj, video və ya dairəvi video göndərin və <b>[🎧 Shazam ilə tanı]</b> vurun.\n\n"
            "3️⃣ <b>Faylla Birlikdə:</b>\n"
            "   • Video və ya audio göndərərkən izahat yerinə <code>/shazam</code> yazın.\n\n"
            "<i>⚡ Bot bir neçə saniyə ərzində mahnını tapıb adını və yükləmə linkini təqdim edəcək!</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Musiqi Axtar (İnline)", switch_inline_query_current_chat="")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(guide_text, parse_mode="HTML", reply_markup=kb)

    # ── Şəxsi çatda birbaşa səs, video və ya video note göndərildikdə düymə təqdimatı ──
    @dp.message(F.chat.type == "private", F.voice)
    async def pm_voice_prompt(message: types.Message):
        if message.caption and message.caption.strip().startswith("/"):
            return
        _remember_pending(message.chat.id, message.message_id, message.voice, "voice")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Shazam ilə tanı", callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                "🎤 <b>Səsli mesaj qəbul edildi.</b>\n"
                "<i>Bu səsdəki musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )

    @dp.message(F.chat.type == "private", F.video_note)
    async def pm_video_note_prompt(message: types.Message):
        _remember_pending(message.chat.id, message.message_id, message.video_note, "video_note")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Shazam ilə tanı", callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                "📹 <b>Video qeyd qəbul edildi.</b>\n"
                "<i>Bu videodakı musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )

    @dp.message(F.chat.type == "private", F.video)
    async def pm_video_prompt(message: types.Message):
        if message.caption and message.caption.strip().startswith("/"):
            return
        _remember_pending(message.chat.id, message.message_id, message.video, "video")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Shazam ilə tanı", callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                "🎬 <b>Video qəbul edildi.</b>\n"
                "<i>Videodakı arxa fon musiqisini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )

    def _is_video_doc(m: types.Message) -> bool:
        doc = m.document
        if not doc:
            return False
        mime = (doc.mime_type or "").lower()
        if mime.startswith("video/"):
            return True
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        return ext in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}

    @dp.message(F.chat.type == "private", _is_video_doc)
    async def pm_video_doc_prompt(message: types.Message):
        if message.caption and message.caption.strip().startswith("/"):
            return
        _remember_pending(message.chat.id, message.message_id, message.document, "document")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Shazam ilə tanı", callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                "📁 <b>Video fayl qəbul edildi.</b>\n"
                "<i>Fayldakı musiqini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )

    # ── Callback işləyiciləri (shz:*) ──
    @dp.callback_query(F.data.startswith("shz:"))
    async def shazam_callback(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        if action == "rec":
            try:
                target_mid = int(parts[2])
            except (IndexError, ValueError):
                target_mid = None

            pending = _get_pending(cb.message.chat.id, target_mid)
            target_media = None
            mtype = None
            if pending:
                target_media, mtype = pending
            elif cb.message.reply_to_message:
                rep = cb.message.reply_to_message
                if rep.voice:
                    target_media, mtype = rep.voice, "voice"
                elif rep.video:
                    target_media, mtype = rep.video, "video"
                elif rep.video_note:
                    target_media, mtype = rep.video_note, "video_note"
                elif rep.audio:
                    target_media, mtype = rep.audio, "audio"
                elif rep.document:
                    target_media, mtype = rep.document, "document"

            if not target_media:
                await cb.answer("⚠️ Media faylı tapılmadı və ya köhnəlib.", show_alert=True)
                return

            await cb.answer("🎧 Shazam ilə axtarılır...")
            with ui_styled("user"):
                await _process_recognition(
                    cb.message.reply_to_message or cb.message,
                    target_media,
                    mtype or "media",
                    context,
                    status_msg=cb.message,
                )
            return

        cache_key = parts[2] if len(parts) > 2 else ""
        item = _SHAZAM_CACHE.get(cache_key)

        if action == "dl":
            if not item:
                await cb.answer("⚠️ Mahnı məlumatı tapılmadı və ya köhnəlib.", show_alert=True)
                return
            await cb.answer("📥 Mahnı axtarılır və yüklənir...")
            search_query = item.get("query") or f"{item.get('artist')} - {item.get('title')}"
            
            # Musiqi yükləmə sistemini işə salırıq
            handler = getattr(context, "handle_music_search", None)
            if handler:
                fake_msg = cb.message.model_copy(update={"from_user": cb.from_user})
                await handler(fake_msg, search_query, "shazam")
            else:
                await cb.message.reply(
                    f"🎵 <b>{escape(search_query)}</b>\n\n"
                    f"Yükləmək üçün əmrdən istifadə edin: <code>/music {escape(search_query)}</code>",
                    parse_mode="HTML"
                )
            return

        if action == "lyr":
            if not item or not item.get("lyrics"):
                await cb.answer("📝 Bu mahnı üçün sözlər tapılmadı.", show_alert=True)
                return
            await cb.answer()
            lyrics = item["lyrics"]
            if len(lyrics) > 3500:                      # mesaj limiti 4096 (şəkil altı isə cəmi 1024 — ona görə ayrıca)
                lyrics = lyrics[:3500] + "\n\n...(davamı qısaldıldı)"
            text = f"📝 <b>{escape(item['artist'])} — {escape(item['title'])}</b>\n━━━━━━━━━━━━━━━━━━\n\n{escape(lyrics)}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Bağla", callback_data="shz:close")]])
            with ui_styled("user"):
                await cb.message.reply(text, parse_mode="HTML", reply_markup=kb)
            return

        if action == "back" and item:                    # köhnə mesajlardakı "Geri" düyməsi
            await cb.answer()
            text, kb = _result_view(item, cache_key)
            with contextlib.suppress(Exception), ui_styled("user"):
                if cb.message.caption is not None:
                    await cb.message.edit_caption(caption=text, parse_mode="HTML", reply_markup=kb)
                else:
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        await cb.answer()

    logger.info("🎵 Shazam musiqi tanıma plugini yükləndi.")
