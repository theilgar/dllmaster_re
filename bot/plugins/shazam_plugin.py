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

import asyncio
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

from common.i18n import _ as tr
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
    lines = [tr("🎧 <b>Musiqi tapıldı (Shazam)</b>") + (f" · <i>{escape(tr(item['stage']))}</i>" if item.get("stage_no", 1) > 1 else ""), "━━━━━━━━━━━━━━━━━━",
             tr("🎵 <b>Mahnı:</b> <b>{v}</b>", v=escape(item['title'])), tr("👤 <b>İfaçı:</b> <b>{v}</b>", v=escape(item['artist']))]
    for icon, label, key in (("💿", tr("Albom"), "album"), ("🏷", tr("Janr"), "genre"), ("📅", tr("Buraxılış ili"), "year")):
        if item.get(key):
            lines.append(f"{icon} <b>{label}:</b> {escape(str(item[key]))}")
    rows = [[InlineKeyboardButton(text=tr("📥 Yüklə"), callback_data=f"shz:dl:{cache_key}"),
             InlineKeyboardButton(text=tr("🔎 Inline axtar"), switch_inline_query_current_chat=item["query"])]]
    row2 = []
    if item.get("lyrics"):
        row2.append(InlineKeyboardButton(text=tr("📝 Sözlər"), callback_data=f"shz:lyr:{cache_key}"))
    if item.get("shazam_url"):
        row2.append(InlineKeyboardButton(text="🔗 Shazam", url=item["shazam_url"]))
    if row2:
        rows.append(row2)
    rows.append([InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# 🎯 Çətin mahnılar üçün çoxmərhələli tanıma: fərqli hissələr + səs emalı + sürətləndirilmiş / yavaşladılmış variantlar.
# (label, başlanğıc payı 0..1 | None=0, müddət san., ffmpeg -af). İlk uğurlu nəticədə dayanır.
ENHANCE = "highpass=f=80,lowpass=f=12000,dynaudnorm=f=150:g=15,loudnorm=I=-16:TP=-1.5:LRA=11"
STAGES = [
    ("başlanğıc", 0.0, 20, ""),
    ("orta hissə", 0.5, 20, ""),
    ("səs yaxşılaşdırıldı (başlanğıc)", 0.0, 25, ENHANCE),
    ("1/4 hissə", 0.25, 15, ""),
    ("səs yaxşılaşdırıldı (orta)", 0.5, 25, ENHANCE),
    ("3/4 hissə", 0.75, 15, ""),
    ("sürətləndirilmiş versiya (sped up)", 0.4, 20, "asetrate=44100*0.85,aresample=44100," + ENHANCE),
    ("yavaşladılmış versiya (slowed)", 0.4, 20, "asetrate=44100*1.15,aresample=44100," + ENHANCE),
]
STAGE_TIMEOUT = 18        # bir mərhələ (ffmpeg + Shazam) üçün
TOTAL_BUDGET = 75         # bütün mərhələlər üçün ümumi vaxt (san.)


async def _clip(input_path: str, out: str, start: float, dur: int, af: str) -> bool:
    from common.media import run_ffmpeg
    args = ["-ss", f"{max(0.0, start):.2f}", "-t", str(dur), "-i", input_path, "-vn", "-ac", "1", "-ar", "44100"]
    if af:
        args += ["-af", af]
    rc, err = await run_ffmpeg(*args, "-acodec", "libmp3lame", "-b:a", "128k", out, timeout=STAGE_TIMEOUT)
    if rc != 0:
        logger.debug(f"ffmpeg mərhələ xətası: {err[:160]}")
    return os.path.exists(out) and os.path.getsize(out) > 1000


def _track_key(track: dict) -> str:
    return f"{(track.get('subtitle') or '').strip().lower()}|{(track.get('title') or '').strip().lower()}"


async def _confirm(shazam, input_path: str, total: float, frac: float, dur: int, tag: str, item: dict, af: str = ""):
    """Səhv tanımanın qarşısı: FƏRQLİ hissədən ikinci parça götürülüb eyni mahnı çıxırmı?
    → (təsdiqləndi?, fərqli nəticə 'ifaçı - ad' və ya None). Fayl çox qısadırsa təsdiq mümkün deyil → (False, None)."""
    if not total or total < dur + 12:
        return False, None
    other = (frac + 0.45) % 1.0                              # tanındığı hissədən uzaq nöqtə
    start = min(max(0.0, total * other), max(0.0, total - 15))
    path = os.path.join(TEMP_DIR, f"rc_{tag}.mp3")
    try:
        if not await _clip(input_path, path, start, 15, af):          # eyni emal (süzgəc) ilə
            return False, None
        res = await asyncio.wait_for(shazam.recognize(path), STAGE_TIMEOUT)
        track = res.get("track") if isinstance(res, dict) else None
        if not track or not track.get("title"):
            return False, None
        same = _track_key(track) == f"{item['artist'].strip().lower()}|{item['title'].strip().lower()}"
        return same, (None if same else f"{track.get('subtitle', '')} - {track.get('title', '')}")
    except Exception as e:
        logger.debug(f"Shazam təsdiq parçası alınmadı: {e}")
        return False, None
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


async def recognize_file(input_path: str, on_stage=None, verify: bool = True) -> dict | None:
    """Yerli faylı (audio və ya video) Shazam ilə tanıyır — ÇOXMƏRHƏLİLİ.

    Mərhələlər (STAGES): başlanğıc → orta hissə → səs yaxşılaşdırma → 1/4 → 3/4 → sped-up / slowed variantlar.
    İlk uğurlu mərhələdə dayanır. on_stage(i, n, label) — irəliləyiş bildirişi (könüllü).
    Mahnı tapılsa lüğət qaytarır (item["stage"] — hansı mərhələdə tapıldı), tapılmasa None."""
    if not os.path.exists(input_path) or os.path.getsize(input_path) < 500:
        return None
    from common.media import duration_of
    os.makedirs(TEMP_DIR, exist_ok=True)
    try:
        total = await duration_of(input_path)
    except Exception:
        total = 0.0
    shazam = _get_shazam()
    t_begin = time.monotonic()
    tag = f"{os.getpid()}_{int(time.time() * 1000)}"
    tried = 0
    weak = None
    for i, (label, frac, dur, af) in enumerate(STAGES, 1):
        if time.monotonic() - t_begin > TOTAL_BUDGET:
            break
        if total and total < 8 and i > 3:
            break                                           # çox qısa fayl — yeni hissələr mənasızdır
        start = 0.0 if not total else min(max(0.0, total * frac), max(0.0, total - dur))
        if total and total <= dur + 2 and frac > 0 and not af:
            continue                                        # fayl elə bir parçadır — eyni hissəni təkrarlama
        clip_path = os.path.join(TEMP_DIR, f"rf_{tag}_{i}.mp3")
        try:
            if on_stage:
                with contextlib.suppress(Exception):
                    r = on_stage(i, len(STAGES), label)
                    if hasattr(r, "__await__"):
                        await r
            if not await _clip(input_path, clip_path, start, dur, af):
                continue
            tried += 1
            res = await asyncio.wait_for(shazam.recognize(clip_path), STAGE_TIMEOUT)
            track = res.get("track") if isinstance(res, dict) else None
            if track and track.get("title"):
                cache_key = hashlib.md5(f"{track.get('subtitle', '')}:{track.get('title', '')}".encode()).hexdigest()[:12]
                item = _parse_track(track)
                item["cache_key"] = cache_key
                item["stage"] = label
                item["stage_no"] = i
                if verify:
                    item["confirmed"], item["alt"] = await _confirm(shazam, input_path, total, frac, dur, tag, item, af)
                    if not item["confirmed"]:
                        can_confirm = bool(total) and total >= dur + 12
                        if af:
                            # səs emalı (gücləndirmə / sürət) səs-küyü mahnı kimi göstərə bilər — təsdiqsiz qəbul edilmir
                            logger.info(f"🎧 Shazam mərhələ {i} ({label}) təsdiqlənmədi, atıldı: "
                                        f"{item['artist']} - {item['title']}")
                            continue
                        if can_confirm:                      # adi parça, amma ikinci parça təsdiq etmədi → zəif namizəd
                            weak = weak or (i, label, item, cache_key)
                            continue
                _remember(cache_key, item)
                logger.info(f"🎧 Shazam tapdı ({i}/{len(STAGES)} mərhələ: {label})"
                            f"{'' if item.get('confirmed', True) else ' [təsdiqsiz]'}: {item['artist']} - {item['title']}")
                return item
        except asyncio.TimeoutError:
            logger.debug(f"Shazam mərhələ {i} vaxt aşdı")
        except Exception as e:
            logger.warning(f"Shazam mərhələ {i} xətası: {e}")
        finally:
            with contextlib.suppress(OSError):
                os.remove(clip_path)
    if weak:                                                 # təsdiqlənməyən, amma adi parçada tapılan namizəd
        i, label, item, cache_key = weak
        _remember(cache_key, item)
        logger.info(f"🎧 Shazam zəif namizəd ({i}/{len(STAGES)} mərhələ: {label}): {item['artist']} - {item['title']}")
        return item
    logger.info(f"🎧 Shazam tapmadı ({tried} mərhələ sınandı, {time.monotonic() - t_begin:.0f} san.)")
    return None


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
        status_msg = await message.reply(tr("🎧 <i>Səs dinlənilir və Shazam ilə axtarılır...</i>"), parse_mode="HTML")
    else:
        with contextlib.suppress(Exception):
            await status_msg.edit_text(tr("🎧 <i>Səs dinlənilir və Shazam ilə axtarılır...</i>"), parse_mode="HTML")

    os.makedirs(TEMP_DIR, exist_ok=True)
    temp_in = os.path.join(TEMP_DIR, f"in_{message.message_id}_{message.chat.id}")

    try:
        # Faylı teleqramdan yükləyirik
        await bot.download(target_media, destination=temp_in)
        if not os.path.exists(temp_in) or os.path.getsize(temp_in) < 500:
            await status_msg.edit_text(tr("⚠️ <i>Səs faylı yüklənə bilmədi və ya çox qısadır.</i>"), parse_mode="HTML")
            return

        async def _stage(i, n, label):
            if i > 1:
                with contextlib.suppress(Exception):
                    await status_msg.edit_text(
                        tr("🎧 <i>Çətin mahnıdır — mərhələ {i}/{n}: {label}...</i>", i=i, n=n, label=escape(tr(label))), parse_mode="HTML")

        item = await recognize_file(temp_in, on_stage=_stage)
        if not item or not item.get("title"):
            close_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
            ])
            await status_msg.edit_text(
                tr("😔 <b>Təəssüf ki, heç bir mahnı tapılmadı.</b>\n<i>Səs çox qısa ola bilər, ətraf küy çoxdur və ya bu musiqi Shazam bazasında yoxdur.</i>"),
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
        await status_msg.edit_text(tr("⚠️ <i>Musiqi axtarışı zamanı xəta baş verdi: {err}</i>", err=escape(str(e)[:100])), parse_mode="HTML")
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
        guide_text = tr(
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
            [InlineKeyboardButton(text=tr("🔎 Musiqi Axtar (İnline)"), switch_inline_query_current_chat="")],
            [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
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
            [InlineKeyboardButton(text=tr("🎧 Shazam ilə tanı"), callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                tr("🎤 <b>Səsli mesaj qəbul edildi.</b>\n<i>Bu səsdəki musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>"),
                parse_mode="HTML",
                reply_markup=kb,
            )

    @dp.message(F.chat.type == "private", F.video_note)
    async def pm_video_note_prompt(message: types.Message):
        _remember_pending(message.chat.id, message.message_id, message.video_note, "video_note")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("🎧 Shazam ilə tanı"), callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                tr("📹 <b>Video qeyd qəbul edildi.</b>\n<i>Bu videodakı musiqini Shazam ilə axtarmaq üçün aşağıdakı düyməyə vurun:</i>"),
                parse_mode="HTML",
                reply_markup=kb,
            )

    @dp.message(F.chat.type == "private", F.video)
    async def pm_video_prompt(message: types.Message):
        if message.caption and message.caption.strip().startswith("/"):
            return
        _remember_pending(message.chat.id, message.message_id, message.video, "video")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("🎧 Shazam ilə tanı"), callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                tr("🎬 <b>Video qəbul edildi.</b>\n<i>Videodakı arxa fon musiqisini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>"),
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
            [InlineKeyboardButton(text=tr("🎧 Shazam ilə tanı"), callback_data=f"shz:rec:{message.message_id}")],
            [InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]
        ])
        with ui_styled("user"):
            await message.reply(
                tr("📁 <b>Video fayl qəbul edildi.</b>\n<i>Fayldakı musiqini Shazam ilə tapmaq üçün aşağıdakı düyməyə vurun:</i>"),
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
                await cb.answer(tr("⚠️ Media faylı tapılmadı və ya köhnəlib."), show_alert=True)
                return

            await cb.answer(tr("🎧 Shazam ilə axtarılır..."))
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
                await cb.answer(tr("⚠️ Mahnı məlumatı tapılmadı və ya köhnəlib."), show_alert=True)
                return
            await cb.answer(tr("📥 Mahnı axtarılır və yüklənir..."))
            search_query = item.get("query") or f"{item.get('artist')} - {item.get('title')}"
            
            # Musiqi yükləmə sistemini işə salırıq
            handler = getattr(context, "handle_music_search", None)
            if handler:
                fake_msg = cb.message.model_copy(update={"from_user": cb.from_user})
                await handler(fake_msg, search_query, "shazam")
            else:
                await cb.message.reply(
                    tr("🎵 <b>{q}</b>\n\nYükləmək üçün əmrdən istifadə edin: <code>/music {q}</code>", q=escape(search_query)),
                    parse_mode="HTML"
                )
            return

        if action == "lyr":
            if not item or not item.get("lyrics"):
                await cb.answer(tr("📝 Bu mahnı üçün sözlər tapılmadı."), show_alert=True)
                return
            await cb.answer()
            lyrics = item["lyrics"]
            if len(lyrics) > 3500:                      # mesaj limiti 4096 (şəkil altı isə cəmi 1024 — ona görə ayrıca)
                lyrics = lyrics[:3500] + tr("\n\n...(davamı qısaldıldı)")
            text = f"📝 <b>{escape(item['artist'])} — {escape(item['title'])}</b>\n━━━━━━━━━━━━━━━━━━\n\n{escape(lyrics)}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="shz:close")]])
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
