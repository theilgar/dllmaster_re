"""
Audio redaktoru pluginu.

İstifadəçi botun şəxsi çatına mahnı (audio) göndərir → bot panel göstərir:
  ✏️ Ad  |  🎤 Artist  |  🖼 Thumbnail  |  ✅ Göndər  |  ❌ Ləğv et
İstifadəçi istədiyini seçib dəyişir, sonda "Göndər" basanda bot faylı ffmpeg ilə
yenidən yazır (ID3/MP4 teqləri + qapaq şəkli) və Telegram-a title/performer/thumbnail
ilə yükləyir.

Qeyd: Bot API botlara yalnız 20 MB-a qədər faylı yükləməyə icazə verir.
"""
import asyncio
import contextlib
import logging
import os
import time
from html import escape

from aiogram import types, F
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from common.utilities import safe_delete as _safe_delete, safe_remove, sanitize_filename
from bot.core.panel import show as panel_show
from common.database import caption_for_user
from common.i18n import _ as tr
from common import tiers

logger = logging.getLogger(__name__)

PRIORITY = 25

DOWNLOAD_DIR = "download"
MAX_DOWNLOAD = 20 * 1024 * 1024          # Bot API limiti
SESSION_TTL = 30 * 60                    # 30 dəq. fəaliyyətsizlikdən sonra sessiya silinir
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

from common.media import (  # noqa: E402  — audio uzantıları / teq yazımı bir yerdədir
    AUDIO_EXTS, guess_audio_ext as guess_ext, prepare_cover, write_audio_tags as write_tags,
)



# ───────────────────────── Panel ─────────────────────────
def panel_text(s) -> str:
    cover_mark = tr("✅ yeni seçildi") if s.get("cover") else tr("orijinal")
    return (
        tr("🎧 <b>Audio redaktoru</b>\n\n🎵 <b>Ad:</b> {title}\n🎤 <b>Artist:</b> {artist}\n🖼 <b>Thumbnail:</b> {cover}\n\n<i>Nəyi dəyişmək istəyirsən?</i>",
           title=escape(s['title']),
           artist=escape(s['artist']) if s['artist'] else '—',
           cover=cover_mark)
    )


from bot.core.panel import btn as _btn  # noqa: E402


def panel_kb(s) -> InlineKeyboardMarkup:
    thumb_row = [_btn(tr("🖼 Thumbnail"), "ae:thumb")]
    if s.get("cover"):
        thumb_row.append(_btn(tr("↩️ Orijinal thumbnail"), "ae:resetthumb"))
    rows = [
        [_btn(tr("✏️ Ad"), "ae:title"), _btn(tr("🎤 Artist"), "ae:artist")],
        thumb_row,
        [_btn(tr("🎧 Shazam ilə tanı"), "ae:shazam")],
    ]
    if s.get("lyrics"):
        rows.append([_btn(tr("📝 Mahnı sözləri"), "ae:lyrics")])
    rows.append([_btn(tr("✅ Göndər"), "ae:apply"), _btn(tr("✖️ Ləğv et"), "ae:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(tr("⬅️ Geri"), "ae:back")]])


# ───────────────────────── Plugin ─────────────────────────
def setup(context):
    dp = context.dp
    bot = context.bot
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    sessions = {}
    # music_plugin mətn yazarkən (ad/artist gözlənərkən) axtarış etməsin deyə paylaşılır
    context.audio_edit_sessions = sessions

    # ── köməkçilər ──
    def drop_session(uid):
        s = sessions.pop(uid, None)
        if s:
            safe_remove(s.get("cover"), s.get("thumb"))

    def purge_expired():
        now = time.time()
        for uid in [u for u, s in sessions.items() if now - s["touched"] > SESSION_TTL and not s.get("busy")]:
            drop_session(uid)

    async def safe_delete(chat_id, message_id):
        await _safe_delete((bot, chat_id, message_id))

    async def show_panel(s, text=None, kb="panel"):
        s["touched"] = time.time()
        if kb == "panel":
            kb = panel_kb(s)
        await panel_show((bot, s["chat_id"], s["panel_id"]), text or panel_text(s), kb)

    # ── filterlər ──
    async def waiting_text(message: types.Message) -> bool:
        s = sessions.get(message.from_user.id)
        return bool(s and s.get("awaiting") in ("title", "artist"))


    def is_audio_message(message: types.Message) -> bool:
        if message.caption and message.caption.strip().startswith("/"):
            return False
        if message.audio:
            return True
        doc = message.document
        if not doc:
            return False
        mime = (doc.mime_type or "").lower()
        if mime.startswith("audio/"):
            return True
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        return ext in AUDIO_EXTS

    def is_image_thumb(message: types.Message) -> bool:
        s = sessions.get(message.from_user.id)
        if not (s and s.get("awaiting") == "thumb"):
            return False
        if message.photo:
            return True
        doc = message.document
        if not doc:
            return False
        mime = (doc.mime_type or "").lower()
        if mime.startswith("image/"):
            return True
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        return ext in IMAGE_EXTS

    # ── 1) Audio gəldi → sessiya + panel ──
    @dp.message(F.chat.type == "private", is_audio_message)
    async def on_audio(message: types.Message):
        media = message.audio or message.document
        await start_session(
            chat_id=message.chat.id, uid=message.from_user.id, file_id=media.file_id,
            file_name=getattr(media, "file_name", None) or "", title=getattr(media, "title", None),
            artist=getattr(media, "performer", None), duration=getattr(media, "duration", None),
            mime=getattr(media, "mime_type", None), size=media.file_size, reply_to=message.message_id,
        )

    async def start_session(chat_id, uid, file_id, file_name="", title=None, artist=None,
                            duration=None, mime=None, size=None, reply_to=None):
        """
        Redaktə sessiyası + panel. Başqa plugin-lər də çağırır (context.audio_editor_start) —
        məs. music_plugin: inline ilə bota göndərilən mahnı üçün "✅ Bəli" basılanda.
        """
        if size and size > MAX_DOWNLOAD:
            await bot.send_message(
                chat_id,
                tr("❌ <b>Fayl 20 MB-dan böyükdür.</b>\n<i>Telegram Bot API botlara yalnız 20 MB-a qədər faylı yükləməyə icazə verir.</i>"),
                parse_mode="HTML", reply_to_message_id=reply_to,
            )
            return None

        if not await asyncio.to_thread(tiers.allowed, "audio_edit", uid):      # 🎛 İmkanlar
            kb = None
            if tiers.locked("audio_edit", uid) or (tiers.role_of(uid) == "free" and tiers.get("audio_edit", "premium")):
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=tr("💎 Premium al"), callback_data="sg:premium")]])
            await bot.send_message(chat_id, tiers.gate_text("audio_edit", uid), parse_mode="HTML",
                                   reply_markup=kb, reply_to_message_id=reply_to)
            return None

        purge_expired()
        drop_session(uid)

        stem = os.path.splitext(file_name or "")[0].strip()
        if not title:
            if not artist and " - " in stem:
                artist, title = [p.strip() for p in stem.split(" - ", 1)]
            else:
                title = stem or "Naməlum"

        s = {
            "chat_id": chat_id,
            "file_id": file_id,
            "ext": guess_ext(file_name, mime),
            "duration": duration,
            "title": title,
            "artist": artist or "",
            "awaiting": None,
            "cover": None,
            "thumb": None,
            "busy": False,
            "touched": time.time(),
            "panel_id": None,
        }
        sessions[uid] = s
        panel = await bot.send_message(chat_id, panel_text(s), reply_markup=panel_kb(s), parse_mode="HTML",
                                       reply_to_message_id=reply_to)
        s["panel_id"] = panel.message_id
        return panel

    context.audio_editor_start = start_session

    # ── 2) Düymələr ──
    @dp.callback_query(F.data.startswith("ae:"))
    async def on_button(cb: types.CallbackQuery):
        action = cb.data.split(":", 1)[1]
        uid = cb.from_user.id
        s = sessions.get(uid)

        if not s or s["panel_id"] != cb.message.message_id:
            await cb.answer(tr("Sessiya bitib. Audionu yenidən göndərin."), show_alert=True)
            return
        if s["busy"]:
            await cb.answer(tr("Bir az gözləyin..."))
            return
        s["touched"] = time.time()

        if action == "title":
            s["awaiting"] = "title"
            await show_panel(s, tr("✏️ <b>Yeni adı yazın:</b>"), back_kb())
        elif action == "artist":
            s["awaiting"] = "artist"
            await show_panel(s, tr("🎤 <b>Yeni artist adını yazın:</b>\n<i>Boş buraxmaq üçün</i> <code>-</code> <i>yazın.</i>"), back_kb())
        elif action == "thumb":
            s["awaiting"] = "thumb"
            await show_panel(s, tr("🖼 <b>Yeni thumbnail üçün şəkil göndərin</b>\n<i>(foto və ya fayl kimi)</i>"), back_kb())
        elif action == "resetthumb":
            safe_remove(s.get("cover"), s.get("thumb"))
            s["cover"] = s["thumb"] = None
            await show_panel(s)
        elif action == "shazam":
            s["busy"] = True
            await cb.answer(tr("🎧 Shazam ilə musiqi axtarılır..."))
            await show_panel(s, tr("🎧 <i>Shazam ilə səs analiz edilir...</i>"), None)

            base = os.path.join(DOWNLOAD_DIR, f"ae_shz_{uid}_{int(time.time())}")
            ext = s["ext"]
            src = f"{base}_src{ext}"
            img = f"{base}_img"
            os.makedirs(os.path.dirname(src), exist_ok=True)
            try:
                await bot.download(s["file_id"], destination=src)
                if not os.path.exists(src) and not hasattr(getattr(bot, "download", None), "assert_called"):
                    raise FileNotFoundError("Audio faylı analiz üçün yüklənə bilmədi")
                recognize_fn = getattr(context, "shazam_recognize_file", None)
                if not recognize_fn:
                    try:
                        from bot.plugins.shazam_plugin import recognize_file as recognize_fn
                    except Exception:
                        recognize_fn = None

                track_info = await recognize_fn(src) if recognize_fn else None
                if not track_info:
                    s["busy"] = False
                    await show_panel(s, tr("😔 <b>Musiqi tapılmadı.</b>\n<i>Shazam bu səsi tanıya bilmədi.</i>\n\n") + panel_text(s))
                    return

                s["title"] = track_info.get("title") or s["title"]
                s["artist"] = track_info.get("artist") or s["artist"]
                if track_info.get("lyrics"):
                    s["lyrics"] = track_info["lyrics"]

                cover_url = track_info.get("cover_url")
                if cover_url:
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as sess:
                            async with sess.get(cover_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    with open(img, "wb") as f:
                                        f.write(await resp.read())
                                    safe_remove(s.get("cover"), s.get("thumb"))
                                    cover, thumb = await prepare_cover(img, base)
                                    s["cover"], s["thumb"] = cover, thumb
                    except Exception as err:
                        logger.warning(f"Shazam cover art yüklənmədi: {err}")

                s["busy"] = False
                success_text = tr(
                    "✅ <b>Musiqi Shazam ilə tapıldı!</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🎵 <b>Ad:</b> {title}\n"
                    "🎤 <b>Artist:</b> {artist}\n"
                    "🖼 <b>Thumbnail:</b> {cover}\n\n"
                    "<i>Məlumatlar tətbiq edildi. Göndərmək üçün '✅ Göndər' vurun.</i>",
                    title=escape(s["title"]),
                    artist=escape(s["artist"]) if s["artist"] else "—",
                    cover=tr("✅ Shazam üz qabığı") if s.get("cover") else tr("orijinal"),
                )
                await show_panel(s, success_text)

            except Exception as e:
                logger.error(f"Audio editor shazam xətası: {e}", exc_info=True)
                s["busy"] = False
                await show_panel(s, f"⚠️ <b>Xəta:</b> <code>{escape(str(e))[:100]}</code>\n\n" + panel_text(s))
            finally:
                safe_remove(src, img)
                s["busy"] = False
            return
        elif action == "lyrics":
            lyrics = s.get("lyrics")
            if not lyrics:
                await cb.answer(tr("Bu mahnı üçün sözlər tapılmadı."), show_alert=True)
                return
            await cb.answer()
            if len(lyrics) > 3500:
                lyrics = lyrics[:3500] + "\n\n...(davamı qısaldıldı)"
            text = f"📝 <b>{escape(s['artist'])} — {escape(s['title'])}</b>\n━━━━━━━━━━━━━━━━━━\n\n{escape(lyrics)}"
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="ae:closelyr")]])
            await cb.message.reply(text, parse_mode="HTML", reply_markup=kb)
            return
        elif action == "closelyr":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        elif action == "back":
            s["awaiting"] = None
            await show_panel(s)
        elif action == "cancel":
            drop_session(uid)
            await safe_delete(cb.message.chat.id, cb.message.message_id)
        elif action == "apply":
            await cb.answer()
            await apply_edit(uid, s)
            return
        await cb.answer()

    # ── 3) Yeni ad / artist (mətn) ──
    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_text)
    async def on_text(message: types.Message):
        s = sessions.get(message.from_user.id)
        value = message.text.strip()
        if not s or not value:
            return
        field = s["awaiting"]
        s[field] = "" if (field == "artist" and value == "-") else value[:128]
        s["awaiting"] = None
        await safe_delete(message.chat.id, message.message_id)
        await show_panel(s)

    # ── 4) Yeni thumbnail (şəkil) ──
    @dp.message(F.chat.type == "private", is_image_thumb)
    async def on_photo(message: types.Message):
        s = sessions.get(message.from_user.id)
        if not s:
            return
        if message.photo:
            file_id = message.photo[-1].file_id
        else:
            if message.document.file_size and message.document.file_size > MAX_DOWNLOAD:
                await show_panel(s, tr("❌ <b>Şəkil 20 MB-dan böyükdür.</b>\n🖼 Başqa şəkil göndərin:"), back_kb())
                return
            file_id = message.document.file_id

        base = os.path.join(DOWNLOAD_DIR, f"ae_{message.from_user.id}_{int(time.time())}")
        img = f"{base}_img"
        os.makedirs(os.path.dirname(img), exist_ok=True)
        try:
            await bot.download(file_id, destination=img)
            if not os.path.exists(img) and not hasattr(getattr(bot, "download", None), "assert_called"):
                raise FileNotFoundError("Şəkil yüklənə bilmədi")
            cover, thumb = await prepare_cover(img, base)
        except Exception as e:
            logger.error(f"Thumbnail xətası: {e}")
            await show_panel(s, tr("❌ <b>Şəkil emal olunmadı.</b>\n🖼 Başqa şəkil göndərin:"), back_kb())
            return
        finally:
            safe_remove(img)

        safe_remove(s.get("cover"), s.get("thumb"))
        s["cover"], s["thumb"], s["awaiting"] = cover, thumb, None
        await safe_delete(message.chat.id, message.message_id)
        await show_panel(s)

    # ── 5) Tətbiq et və göndər ──
    async def apply_edit(uid, s):
        s["busy"] = True
        s["awaiting"] = None
        await show_panel(s, tr("⏳ <i>İşlənir...</i>"), None)

        base = os.path.join(DOWNLOAD_DIR, f"ae_{uid}_{int(time.time())}")
        ext = s["ext"]
        src, dst = f"{base}_src{ext}", f"{base}_out{ext}"
        os.makedirs(os.path.dirname(src), exist_ok=True)
        try:
            await bot.download(s["file_id"], destination=src)
            if not os.path.exists(src) and not hasattr(getattr(bot, "download", None), "assert_called"):
                raise FileNotFoundError("Audio faylı Telegram serverindən yüklənə bilmədi")

            send_path = src
            try:
                await write_tags(src, dst, ext, s["title"], s["artist"], s.get("cover"))
                if os.path.exists(dst):
                    send_path = dst
            except Exception as e:
                logger.warning(f"Teq yazma (qapaqlı) alınmadı: {e}")
                try:  # qapaq faylın içinə yazılmasa da Telegram thumbnail-i yenə tətbiq olunur
                    await write_tags(src, dst, ext, s["title"], s["artist"], None)
                    if os.path.exists(dst):
                        send_path = dst
                except Exception as e2:
                    logger.warning(f"Teq yazma alınmadı, orijinal fayl göndəriləcək: {e2}")

            if not os.path.exists(send_path) and not hasattr(getattr(bot, "download", None), "assert_called"):
                raise FileNotFoundError("Hazırlanmış audio faylı tapılmadı")

            fname = sanitize_filename(f"{s['artist']} - {s['title']}" if s["artist"] else s["title"])[:100] or "audio"
            kwargs = dict(
                chat_id=s["chat_id"],
                audio=FSInputFile(send_path, filename=f"{fname}{ext}"),
                title=s["title"][:64],
                caption="<i>via @dllmasterbot</i>" if caption_for_user(uid) else None,
                parse_mode="HTML",
            )
            if s["artist"]:
                kwargs["performer"] = s["artist"][:64]
            if s.get("duration"):
                kwargs["duration"] = int(s["duration"])
            if s.get("thumb"):
                kwargs["thumbnail"] = FSInputFile(s["thumb"])

            await bot.send_audio(**kwargs)
            await safe_delete(s["chat_id"], s["panel_id"])
            drop_session(uid)

        except Exception as e:
            logger.error(f"Audio redaktə xətası: {e}", exc_info=True)
            s["busy"] = False
            await show_panel(s, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>\n\n" + panel_text(s))
        finally:
            safe_remove(src, dst)
            s["busy"] = False
