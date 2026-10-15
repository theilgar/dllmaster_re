"""
🎛 Audio Kodek və Format Plugini — bot/plugins/test_mp3_plugin.py
(Vahid /codec sistemi, M4A standart axın və MP3 bitreyt tənzimləmələri)
"""
import asyncio
import contextlib
import logging
import os
import time
from html import escape

from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from bot.core import cpu_pool
from bot.core import panel as ui
from bot.core.group_panel import (
    DEFAULT_BITRATE, VALID_BITRATES,
    get_group_bitrate, get_group_link_mode, is_group_mp3_active, set_group_bitrate,
    set_group_link_mode, set_group_mp3_active,
)
from common.database import creator_id, get_setting, is_creator, set_setting
from common.permissions import is_group_admin
from common.utilities import sanitize_filename, ui_styled

PRIORITY = 20  # music_plugin (50)-dən əvvəl yüklənir
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "download"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ───────────────────────── Audio Kodek və Bitreyt Saxlanması ─────────────────────────
def get_user_codec(uid: int = None) -> str:
    """İstifadəçinin seçdiyi format: 'm4a' (standart/defolt) və ya 'mp3'."""
    if not uid:
        return "m4a"
    val = get_setting(f"user_codec:{uid}")
    if val in ("mp3", "m4a"):
        return val
    return "m4a"


def set_user_codec(uid: int, codec: str):
    """İstifadəçinin audio formatını yadda saxlayır ('m4a' və ya 'mp3')."""
    val = "mp3" if str(codec).lower() == "mp3" else "m4a"
    set_setting(f"user_codec:{uid}", val)


def get_user_bitrate(uid: int = None) -> int:
    """İstifadəçinin seçdiyi MP3 bitreyti (standart 320 kbps)."""
    val = get_setting(f"test_mp3:u:{uid}:bitrate") if uid else None
    val = val or get_setting("test_mp3:bitrate", str(DEFAULT_BITRATE))
    try:
        b = int(val)
        return b if b in VALID_BITRATES else DEFAULT_BITRATE
    except (ValueError, TypeError):
        return DEFAULT_BITRATE


def set_user_bitrate(bitrate: int, uid: int = None):
    """İstifadəçinin MP3 bitreytini yadda saxlayır."""
    key = f"test_mp3:u:{uid}:bitrate" if uid else "test_mp3:bitrate"
    set_setting(key, str(bitrate))


def get_creator_bitrate() -> int:
    cid = creator_id()
    return get_user_bitrate(cid) if cid else DEFAULT_BITRATE


def set_creator_bitrate(bitrate: int):
    cid = creator_id()
    if cid:
        set_user_bitrate(bitrate, cid)


# ───────────────────────── Geriye Uyğunluq Köməkçiləri ─────────────────────────
def is_plugin_enabled() -> bool:
    return True


def is_allowed_user(user_id: int) -> bool:
    return True


def is_user_mp3_active(uid: int = None) -> bool:
    return get_user_codec(uid) == "mp3"


def set_user_mp3_active(active: bool, uid: int = None):
    set_user_codec(uid, "mp3" if active else "m4a")


def is_creator_mp3_active() -> bool:
    cid = creator_id()
    return get_user_codec(cid) == "mp3" if cid else False


def set_creator_mp3_active(active: bool):
    cid = creator_id()
    if cid:
        set_user_codec(cid, "mp3" if active else "m4a")


def get_allowed_testers() -> list[tuple[int, str]]:
    return []


def get_allowed_user_ids() -> list[int]:
    return []


def add_allowed_user(user_id: int, user_title: str = ""):
    pass


def remove_allowed_user(user_id: int) -> str | None:
    return None


def clear_allowed_users():
    pass


# ───────────────────────── FFmpeg MP3 Konvertasiyası ─────────────────────────
async def convert_to_mp3(src_path: str, dst_path: str, bitrate: int = 320,
                         title: str = None, performer: str = None) -> bool:
    """Faylı ffmpeg ilə istifadəçinin seçdiyi bitreytlə MP3 formatına çevirir."""
    cmd = [
        *cpu_pool.ffmpeg_prefix(), "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vn", "-map", "0:a:0",
        "-c:a", "libmp3lame",
        "-b:a", f"{bitrate}k",
    ]
    if title:
        cmd.extend(["-metadata", f"title={title}"])
    if performer:
        cmd.extend(["-metadata", f"artist={performer}"])
    cmd.append(dst_path)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("FFmpeg MP3 çevirmə vaxtı bitdi (180s timeout)")
            return False
        if proc.returncode != 0:
            logger.error(f"FFmpeg MP3 konvertasiya xətası ({proc.returncode}): {stderr.decode(errors='ignore')}")
            return False
        return os.path.isfile(dst_path) and os.path.getsize(dst_path) > 0
    except Exception as e:
        logger.error(f"FFmpeg MP3 çevirmə istisnası: {e}", exc_info=True)
        return False


# ───────────────────────── Panel Görünüşü (/codec) ─────────────────────────
def codec_panel_view(user_id: int, chat_id: int = None, is_group: bool = False, from_menu: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """İstifadəçi və ya qrup üçün audio formatı & bitreyt seçimi paneli."""
    if is_group and chat_id and chat_id < 0:
        cur_codec = "mp3" if is_group_mp3_active(chat_id) else "m4a"
        cur_br = get_group_bitrate(chat_id)
        title = "👥 Qrup Audio Formatı & Kodek Seçimi"
        prefix = f"codec:g:{chat_id}"
    else:
        cur_codec = get_user_codec(user_id)
        cur_br = get_creator_bitrate() if is_creator(user_id) else get_user_bitrate(user_id)
        title = "🎧 Audio Formatı & Kodek Seçimi"
        prefix = "codec:u"

    codec_desc = {
        "m4a": "🎵 M4A (Standart / Sürətli)",
        "mp3": f"🎧 MP3 ({cur_br} kbps)",
    }.get(cur_codec, cur_codec.upper())

    quality_desc = {
        320: "320 kbps (Studio Keyfiyyəti · Ən yüksək)",
        256: "256 kbps (Yüksək keyfiyyət)",
        192: "192 kbps (Balanslı / Standart)",
        128: "128 kbps (Orta / Qənaətcil həcm)",
        64:  "64 kbps (Minimal həcm)",
    }.get(cur_br, f"{cur_br} kbps")

    text = (
        f"🎛 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• Hazırkı format: <b>{codec_desc}</b>\n"
        f"• Səs keyfiyyəti: <b>{quality_desc}</b>\n\n"
        "📌 <b>Formatlar haqqında məlumat:</b>\n"
        "• <b>🎵 M4A (Standart):</b> Ən sürətli yükləmə. Orijinal YouTube audio axınıdır, "
        "serverdə təkrar emal aparılmır və dərhal göndərilir.\n"
        "• <b>🎧 MP3:</b> Mahnı serverdə seçilmiş bitreytlə (64–320 kbps) MP3 formatına çevrilir və ID3 teqləri vurulur.\n\n"
        "<i>İstədiyiniz formatı və ya MP3 bitreytini aşağıdan seçə bilərsiniz:</i>"
    )

    codec_btn = (ui.btn("Format: 🎵 M4A (Standart)  ›  🎧 MP3", f"{prefix}:set:mp3") if cur_codec != "mp3"
                 else ui.btn("Format: 🎧 MP3  ›  🎵 M4A (Standart)", f"{prefix}:set:m4a"))

    rows = [
        [codec_btn],
    ]

    if cur_codec == "mp3":
        def b_btn(val):
            return ui.choice(f"{val} kbps", val == cur_br, f"{prefix}:br:{val}")

        rows.append([b_btn(320), b_btn(256)])
        rows.append([b_btn(192), b_btn(128), b_btn(64)])

    nav_row = []
    if from_menu:
        nav_row.append(InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main"))
    nav_row.append(InlineKeyboardButton(text="✖️ Bağla", callback_data=f"{prefix}:close"))
    rows.append(nav_row)

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_view() -> tuple[str, InlineKeyboardMarkup]:
    """Geriye uyğunluq: Creator üçün kodek paneli."""
    cid = creator_id() or 0
    return codec_panel_view(cid, from_menu=True)


def user_panel_view(user_id: int, from_menu: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """Geriye uyğunluq: İstifadəçi üçün kodek paneli."""
    return codec_panel_view(user_id, from_menu=from_menu)


# ───────────────────────── Musiqi Hook-u (Mp3TestHook / CodecHook) ─────────────────────────
class CodecHook:
    """Musiqi yükləmələri və inline çatdırılma üçün hook."""
    def __init__(self, context):
        self.context = context
        self._inline_mp3_cache: dict[tuple[str, int], str] = {}

    def get_group_link_mode(self, chat_id: int) -> str:
        return get_group_link_mode(chat_id)

    def set_group_link_mode(self, chat_id: int, mode: str):
        set_group_link_mode(chat_id, mode)

    async def is_group_admin(self, bot, chat_id: int, user_id: int) -> bool:
        return await is_group_admin(bot, chat_id, user_id)

    def is_active_for(self, user_id: int = None, chat_id: int = None) -> bool:
        if not is_plugin_enabled():
            return False

        # 1) Qrup konteksti:
        if chat_id and chat_id < 0:
            return is_group_mp3_active(chat_id)

        # 2) Şəxsi çat və ya İnline:
        if user_id:
            return get_user_codec(user_id) == "mp3"

        return False

    def get_bitrate(self, chat_id: int = None, user_id: int = None) -> int:
        if chat_id and chat_id < 0 and is_group_mp3_active(chat_id):
            return get_group_bitrate(chat_id)
        if user_id and is_creator(user_id):
            return get_creator_bitrate()
        return get_user_bitrate(user_id)

    async def send_mp3(self, bot, target, res: dict, artist: str, track_name: str,
                       duration: int, caption: str, reply_markup=None, bitrate: int = None):
        if not bitrate:
            chat_id = getattr(target, "chat_id", None) or (target.chat.id if hasattr(target, "chat") else None)
            user_id = getattr(target, "from_user", None) and target.from_user.id
            bitrate = self.get_bitrate(chat_id=chat_id, user_id=user_id)
        safe_name = sanitize_filename(f"{artist} - {track_name}" if artist else track_name)
        tag = int(time.time() * 1000)
        mp3_out = os.path.join(DOWNLOAD_DIR, f"conv_{tag}_{safe_name}_{bitrate}k.mp3")
        tmp_src = None
        ok = False

        try:
            local_path = res.get("path")
            if local_path and os.path.isfile(local_path):
                ok = await convert_to_mp3(local_path, mp3_out, bitrate=bitrate, title=track_name, performer=artist)
            elif res.get("file_id"):
                tg_file = await bot.get_file(res["file_id"])
                tmp_src = os.path.join(DOWNLOAD_DIR, f"tmp_{tag}_{safe_name}.m4a")
                await bot.download_file(tg_file.file_path, tmp_src)
                ok = await convert_to_mp3(tmp_src, mp3_out, bitrate=bitrate, title=track_name, performer=artist)

            if ok and os.path.isfile(mp3_out):
                return await target.answer_audio(
                    audio=FSInputFile(mp3_out, filename=f"{safe_name}.mp3"),
                    title=track_name[:64],
                    performer=artist[:64],
                    duration=duration or None,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            else:
                logger.warning("MP3 konvertasiyası baş tutmadı, standart çatdırılma istifadə olunur")
                if res.get("file_id"):
                    return await target.answer_audio(
                        audio=res["file_id"], caption=caption, parse_mode="HTML", reply_markup=reply_markup,
                    )
                elif local_path and os.path.isfile(local_path):
                    return await target.answer_audio(
                        audio=FSInputFile(local_path, filename=res.get("fname")),
                        title=track_name[:64], performer=artist[:64], duration=duration or None,
                        caption=caption, parse_mode="HTML", reply_markup=reply_markup,
                    )
        finally:
            for p in (mp3_out, tmp_src):
                if p and os.path.isfile(p):
                    with contextlib.suppress(OSError):
                        os.remove(p)

    async def prepare_mp3_file_id(self, bot, res: dict, artist: str, track_name: str,
                                  duration: int, upload_chat_id: int, user_id: int = None) -> tuple[str | None, int]:
        bitrate = self.get_bitrate(user_id=user_id)
        vid = res.get("vid") or ""
        cache_key = (vid, bitrate) if vid else None

        if cache_key and cache_key in self._inline_mp3_cache:
            return self._inline_mp3_cache[cache_key], bitrate

        safe_name = sanitize_filename(f"{artist} - {track_name}" if artist else track_name)
        tag = int(time.time() * 1000)
        mp3_out = os.path.join(DOWNLOAD_DIR, f"inl_{tag}_{safe_name}_{bitrate}k.mp3")
        tmp_src = None

        try:
            local_path = res.get("path")
            if local_path and os.path.isfile(local_path):
                ok = await convert_to_mp3(local_path, mp3_out, bitrate=bitrate, title=track_name, performer=artist)
            elif res.get("file_id"):
                tg_file = await bot.get_file(res["file_id"])
                tmp_src = os.path.join(DOWNLOAD_DIR, f"tmp_inl_{tag}_{safe_name}.m4a")
                await bot.download_file(tg_file.file_path, tmp_src)
                ok = await convert_to_mp3(tmp_src, mp3_out, bitrate=bitrate, title=track_name, performer=artist)
            else:
                return None, bitrate

            if ok and os.path.isfile(mp3_out):
                for target_chat in (upload_chat_id, user_id):
                    if not target_chat:
                        continue
                    try:
                        msg = await bot.send_audio(
                            chat_id=target_chat,
                            audio=FSInputFile(mp3_out, filename=f"{safe_name}.mp3"),
                            title=(track_name or "")[:64] or None,
                            performer=(artist or "")[:64] or None,
                            duration=duration or None,
                            caption=None,
                            disable_notification=True,
                        )
                        if msg and msg.audio:
                            fid = msg.audio.file_id
                            with contextlib.suppress(Exception):
                                await bot.delete_message(target_chat, msg.message_id)
                            if cache_key:
                                self._inline_mp3_cache[cache_key] = fid
                                if len(self._inline_mp3_cache) > 1000:
                                    self._inline_mp3_cache.pop(next(iter(self._inline_mp3_cache)))
                            return fid, bitrate
                    except Exception as upload_err:
                        logger.warning(f"Inline MP3 müvəqqəti yükləmə ({target_chat}) xətası: {upload_err}")
            return None, bitrate
        except Exception as e:
            logger.error(f"prepare_mp3_file_id xətası: {e}", exc_info=True)
            return None, bitrate
        finally:
            for p in (mp3_out, tmp_src):
                if p and os.path.isfile(p):
                    with contextlib.suppress(OSError):
                        os.remove(p)

Mp3TestHook = CodecHook


# ───────────────────────── Setup (Aiogram Qeydiyyatı) ─────────────────────────
def setup(context):
    dp = context.dp
    context.test_mp3_hook = CodecHook(context)
    context.codec_hook = context.test_mp3_hook

    async def _safe_edit_cb(cb: CallbackQuery, text: str, kb=None):
        try:
            if cb.message:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            elif cb.inline_message_id:
                await cb.bot.edit_message_text(text, inline_message_id=cb.inline_message_id, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.debug(f"codec edit xətası: {e}")

    # ───────────────────────── /codec Komandası ─────────────────────────
    @dp.message(Command("codec", "format", "audioformat"))
    async def codec_command_handler(message: Message, command: CommandObject):
        uid = message.from_user.id
        is_grp = message.chat.type != "private"
        cid = message.chat.id

        if is_grp:
            is_adm = await is_group_admin(message.bot, cid, uid)
            if not is_adm:
                with ui_styled("admin"):
                    await message.reply(
                        "👮 <b>Bu parametr yalnız qrup adminləri üçün əlçatandır.</b>\n"
                        "<i>Şəxsi çatınızda öz audio formatınızı dəyişmək üçün bota şəxsidə <code>/codec</code> yazın.</i>",
                        parse_mode="HTML"
                    )
                return

        query = (command.args or "").strip().lower()

        if query in ("m4a", "aac", "default", "standart", "standart_m4a", "0"):
            if is_grp:
                set_group_mp3_active(cid, False)
                text, kb = codec_panel_view(user_id=uid, chat_id=cid, is_group=True)
                await message.reply("✅ <b>Qrup üçün audio formatı M4A (Standart) təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            else:
                set_user_codec(uid, "m4a")
                text, kb = codec_panel_view(user_id=uid)
                await message.answer("✅ <b>Audio formatınız M4A (Standart) təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            return

        elif query in ("mp3", "1", "lame"):
            if is_grp:
                set_group_mp3_active(cid, True)
                text, kb = codec_panel_view(user_id=uid, chat_id=cid, is_group=True)
                await message.reply("✅ <b>Qrup üçün audio formatı MP3 təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            else:
                set_user_codec(uid, "mp3")
                text, kb = codec_panel_view(user_id=uid)
                await message.answer("✅ <b>Audio formatınız MP3 təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            return

        elif query.isdigit() and int(query) in VALID_BITRATES:
            br = int(query)
            if is_grp:
                set_group_bitrate(cid, br)
                set_group_mp3_active(cid, True)
                text, kb = codec_panel_view(user_id=uid, chat_id=cid, is_group=True)
                await message.reply(f"✅ <b>Qrup üçün audio formatı MP3 ({br} kbps) təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            else:
                if is_creator(uid):
                    set_creator_bitrate(br)
                else:
                    set_user_bitrate(br, uid)
                set_user_codec(uid, "mp3")
                text, kb = codec_panel_view(user_id=uid)
                await message.answer(f"✅ <b>Audio formatınız MP3 ({br} kbps) təyin edildi!</b>\n\n" + text, parse_mode="HTML", reply_markup=kb)
            return

        # Parametrsiz /codec: seçim paneli
        if is_grp:
            text, kb = codec_panel_view(user_id=uid, chat_id=cid, is_group=True)
            await message.reply(text, parse_mode="HTML", reply_markup=kb)
        else:
            text, kb = codec_panel_view(user_id=uid)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    # ───────────────────────── /mp3 Komandası (Ləğv edilib → /codec-ə yönləndirilir) ─────────────────────────
    @dp.message(Command("mp3"))
    async def legacy_mp3_command(message: Message, command: CommandObject = None):
        uid = message.from_user.id
        is_grp = message.chat.type != "private"
        cid = message.chat.id

        text, kb = codec_panel_view(user_id=uid, chat_id=cid, is_group=is_grp)
        notice = (
            "💡 <b>Qeyd:</b> <code>/mp3</code> funksiyası ləğv edilmişdir. "
            "Audio formatı və bitreyt tənzimləmələri artıq vahid <b><code>/codec</code></b> sisteminə keçirilib.\n\n"
        )
        if is_grp:
            await message.reply(notice + text, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(notice + text, parse_mode="HTML", reply_markup=kb)

    # ───────────────────────── Audio Formatı Düymələri (codec:*) ─────────────────────────
    @dp.callback_query(F.data.startswith("codec:"))
    async def codec_callback_handler(cb: CallbackQuery):
        uid = cb.from_user.id
        parts = cb.data.split(":")

        has_menu = False
        if cb.message and cb.message.reply_markup:
            has_menu = any(
                "umenu:main" in (b.callback_data or "")
                for row in cb.message.reply_markup.inline_keyboard
                for b in row
            )

        # 1) Qrup formatı: codec:g:<chat_id>:<act>[:<val>]
        if len(parts) >= 3 and parts[1] == "g":
            try:
                chat_id = int(parts[2])
            except ValueError:
                await cb.answer("Xətalı çat ID-si", show_alert=True)
                return

            is_adm = await is_group_admin(cb.bot, chat_id, uid)
            if not is_adm:
                await cb.answer("👮 Bu parametri yalnız qrup adminləri dəyişə bilər.", show_alert=True)
                return

            act = parts[3] if len(parts) > 3 else "panel"

            if act == "set":
                codec = parts[4] if len(parts) > 4 else "m4a"
                if codec == "mp3":
                    set_group_mp3_active(chat_id, True)
                    await cb.answer("🎧 Qrup formatı: MP3 seçildi")
                else:
                    set_group_mp3_active(chat_id, False)
                    await cb.answer("🎵 Qrup formatı: M4A (Standart) seçildi")
                text, kb = codec_panel_view(user_id=uid, chat_id=chat_id, is_group=True, from_menu=has_menu)
                await _safe_edit_cb(cb, text, kb)
                return

            elif act == "br":
                try:
                    br = int(parts[4])
                except (IndexError, ValueError):
                    br = None
                if br in VALID_BITRATES:
                    set_group_bitrate(chat_id, br)
                    set_group_mp3_active(chat_id, True)
                    await cb.answer(f"✅ Qrup bitreyti: {br} kbps seçildi")
                else:
                    await cb.answer("⚠️ Yanlış bitreyt", show_alert=True)
                text, kb = codec_panel_view(user_id=uid, chat_id=chat_id, is_group=True, from_menu=has_menu)
                await _safe_edit_cb(cb, text, kb)
                return

            elif act == "close":
                await cb.answer()
                with contextlib.suppress(Exception):
                    await cb.message.delete()
                return

            # panel / naməlum alt-əmr → qrup panelini göstər (istifadəçi budağına düşməsin)
            await cb.answer()
            text, kb = codec_panel_view(user_id=uid, chat_id=chat_id, is_group=True, from_menu=has_menu)
            await _safe_edit_cb(cb, text, kb)
            return

        # 2) İstifadəçi şəxsi formatı: codec:panel və ya codec:u:<act>[:<val>]
        if len(parts) > 1 and parts[1] == "panel":
            text, kb = codec_panel_view(user_id=uid, from_menu=True)
            await _safe_edit_cb(cb, text, kb)
            await cb.answer()
            return

        act = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "panel")

        if act == "set":
            codec = parts[3] if len(parts) > 3 else "m4a"
            if codec == "mp3":
                set_user_codec(uid, "mp3")
                await cb.answer("🎧 Format MP3 olaraq seçildi")
            else:
                set_user_codec(uid, "m4a")
                await cb.answer("🎵 Format M4A (Standart) olaraq seçildi")
            text, kb = codec_panel_view(user_id=uid, from_menu=has_menu)
            await _safe_edit_cb(cb, text, kb)
            return

        elif act == "br":
            try:
                br = int(parts[3])
                if br in VALID_BITRATES:
                    if is_creator(uid):
                        set_creator_bitrate(br)
                    else:
                        set_user_bitrate(br, uid)
                    set_user_codec(uid, "mp3")
                    await cb.answer(f"✅ Bitreyt: {br} kbps seçildi")
            except (IndexError, ValueError):
                pass
            text, kb = codec_panel_view(user_id=uid, from_menu=has_menu)
            await _safe_edit_cb(cb, text, kb)
            return

        elif act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

    # ───────────────────────── Geriye Uyğunluq Düymələri (test_mp3:*, mp3_u:*) ─────────────────────────
    @dp.callback_query(F.data.startswith(("test_mp3:", "mp3_u:")))
    async def legacy_callback_handler(cb: CallbackQuery):
        uid = cb.from_user.id
        parts = cb.data.split(":")
        act = parts[1] if len(parts) > 1 else ""

        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        text, kb = codec_panel_view(user_id=uid, from_menu=True)
        await _safe_edit_cb(cb, text, kb)
        await cb.answer()

    # ───────────────────────── PM Audio Fayl Konvertasiyası ─────────────────────────
    def is_mp3_auto_convert(message: Message) -> bool:
        if not message.from_user:
            return False
        return get_user_codec(message.from_user.id) == "mp3"

    @dp.message(F.chat.type == "private", F.audio, is_mp3_auto_convert)
    async def handle_user_audio_file(message: Message):
        uid = message.from_user.id
        audio = message.audio
        if not audio:
            return

        bitrate = get_creator_bitrate() if is_creator(uid) else get_user_bitrate(uid)
        title = audio.title or "Audio"
        performer = audio.performer or ""
        safe_name = sanitize_filename(f"{performer} - {title}" if performer else title)
        tag = int(time.time() * 1000)

        st = await message.reply(
            f"⏳ <b>Mahnı {bitrate} kbps MP3 formatına çevrilir...</b>",
            parse_mode="HTML",
        )
        tmp_in = os.path.join(DOWNLOAD_DIR, f"in_{tag}_{audio.file_id[:8]}.audio")
        mp3_out = os.path.join(DOWNLOAD_DIR, f"conv_{tag}_{safe_name}_{bitrate}k.mp3")

        try:
            tg_f = await message.bot.get_file(audio.file_id)
            await message.bot.download_file(tg_f.file_path, tmp_in)
            ok = await convert_to_mp3(tmp_in, mp3_out, bitrate=bitrate, title=title, performer=performer)
            if ok and os.path.isfile(mp3_out):
                caption = f"🎵 <b>{escape(title)}</b>" + (f" — {escape(performer)}" if performer else "")
                await message.answer_audio(
                    audio=FSInputFile(mp3_out, filename=f"{safe_name}.mp3"),
                    title=title[:64],
                    performer=performer[:64],
                    duration=audio.duration or None,
                    caption=caption,
                    parse_mode="HTML",
                )
                await st.delete()
            else:
                await st.edit_text("❌ MP3 konvertasiyası alınmadı.")
        except Exception as e:
            logger.error(f"Fayl konvertasiya xətası: {e}")
            await st.edit_text(f"❌ Xəta baş verdi: <code>{escape(str(e)[:200])}</code>", parse_mode="HTML")
        finally:
            for p in (tmp_in, mp3_out):
                if p and os.path.isfile(p):
                    with contextlib.suppress(OSError):
                        os.remove(p)

    logger.info("🎛 Audio Kodek plugini uğurla yükləndi.")
