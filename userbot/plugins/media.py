"""🎨 media.py — Media, stikerlər və səs (TTS) alətləri.

Komandalar:
  • .sticker / .s          — şəkli, GIF-i və ya videonu stikerə çevirir və göndərir
  • .stickerpack            — istifadəçiyə aid stiker paketlərinin siyahısı və linkləri
  • .tts [mətn]             — mətni səs mesajına (voice note) çevirib göndərir
  • .ttsvoice [səs]         — standart TTS səsini dəyişir / dilləri göstərir
"""
import asyncio
import contextlib
import math
import os
import re
import shutil
import tempfile
from html import escape


from userbot.core.userbot_api import Out, ff, get_db, logger, safe_edit
from common.database import Settings

# ─────────────────────────────────────────────────────────────
# 1. Stiker Parametrləri və Köməkçiləri
# ─────────────────────────────────────────────────────────────
DEFAULT_EMOJI = "🎨"
PACK_MAX = 120
S_PACK = "sticker_pack:{uid}"                    # hazırkı paket nömrəsi
S_EMOJI = "sticker_emoji:{uid}"                  # default emoji (kartdan seçilir)
S_AUTO = "sticker_autopack:{uid}"                # paketə avtomatik əlavə (default: açıq)

SIDE = 512
VIDEO_MAX_SEC = 3                                # Telegram limiti (maks 3 san.)
MAX_PARTS = 20
MAX_SPEED = 20.0
VIDEO_MAX_BYTES = 256 * 1024


_S = Settings(empty_is_default=True)
_s = _S.get


_put = _S.put


def _is_image(msg) -> bool:
    if getattr(msg, "photo", None):
        return True
    doc = getattr(msg, "document", None)
    mime = (getattr(doc, "mime_type", "") or "") if doc else ""
    return mime.startswith("image/") and mime != "image/gif"


def _is_video(msg) -> bool:
    if getattr(msg, "gif", None) or getattr(msg, "video", None) or getattr(msg, "video_note", None):
        return True
    doc = getattr(msg, "document", None)
    mime = (getattr(doc, "mime_type", "") or "") if doc else ""
    return mime in ("image/gif", "video/webm", "video/mp4", "video/quicktime")


from common.media import (
    duration_of,
    duration_sec,
    media_dimensions as _dims,
    to_ogg,
    to_static_sticker as to_static,
    to_video_sticker as to_video,
)


def _pack_name(uid: int, n: int, bot_username: str) -> str:
    return f"u{uid}_dllm_{n}_by_{bot_username}"[:64]


def _pack_title(first_name: str, n: int) -> str:
    return (f"{first_name or 'Mənim'} stikerləri" + (f" #{n}" if n > 1 else ""))[:64]


async def add_to_pack(ub, me, path: str, video: bool, emoji: str) -> tuple:
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import FSInputFile, InputSticker
    from common.database import get_db

    bot = ub.bot
    bot_un = await ub.bot_username()
    key = S_PACK.format(uid=me.id)
    n = int(get_db().get_setting(key) or 1)
    sticker = InputSticker(sticker=FSInputFile(path), format="video" if video else "static",
                           emoji_list=[emoji])
    for _ in range(20):
        name = _pack_name(me.id, n, bot_un)
        try:
            st = await bot.get_sticker_set(name)
        except TelegramBadRequest as e:
            if "invalid" not in str(e).lower() and "not found" not in str(e).lower():
                raise
            st = None
        if st is None:
            await bot.create_new_sticker_set(
                user_id=me.id, name=name, title=_pack_title(me.first_name, n),
                stickers=[sticker])
            get_db().set_setting(key, str(n))
            return name, 1
        if len(st.stickers) < PACK_MAX:
            await bot.add_sticker_to_set(user_id=me.id, name=name, sticker=sticker)
            get_db().set_setting(key, str(n))
            return name, len(st.stickers) + 1
        n += 1
    raise RuntimeError("bütün paketlər doludur")


# ─────────────────────────────────────────────────────────────
# 2. TTS Parametrləri və Köməkçiləri
# ─────────────────────────────────────────────────────────────
MAX_LEN = 1500
VOICE_KEY = "userbot:tts_voice"

LANGS = {
    "az": ("az-AZ-BabekNeural", "az"),
    "ru": ("ru-RU-DmitryNeural", "ru"),
    "en": ("en-US-AriaNeural", "en"),
    "tr": ("tr-TR-AhmetNeural", "tr"),
    "de": ("de-DE-KillianNeural", "de"),
    "fr": ("fr-FR-HenriNeural", "fr"),
    "es": ("es-ES-AlvaroNeural", "es"),
    "ar": ("ar-SA-HamedNeural", "ar"),
    "fa": ("fa-IR-FaridNeural", "fa"),
}
DEFAULT_LANG = "az"
_LANG_PREFIX = re.compile(r"^\s*([a-z]{2})\s*:\s*", re.I)

try:
    import edge_tts
    HAS_EDGE = True
except ImportError:
    HAS_EDGE = False
try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False


def _edge_voice_lang(voice: str) -> str:
    return (voice.split("-", 1)[0] or DEFAULT_LANG).lower()


async def synth(text: str, voice: str, out_mp3: str):
    if HAS_EDGE:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out_mp3)
        return
    if HAS_GTTS:
        lang = _edge_voice_lang(voice)
        if lang not in {l[1] for l in LANGS.values()}:
            lang = "en"
        await asyncio.to_thread(lambda: gTTS(text=text, lang=lang).save(out_mp3))
        return
    raise RuntimeError("no-engine")




def _attrs(as_voice, dur, text):
    try:
        from telethon.tl.types import DocumentAttributeAudio
    except Exception:
        return None
    if as_voice:
        return [DocumentAttributeAudio(duration=dur or 0, voice=True)]
    title = (text[:40] + "…") if len(text) > 40 else text
    return [DocumentAttributeAudio(duration=dur or 0, voice=False, title=title, performer="TTS")]


# ─────────────────────────────────────────────────────────────
# Əsas Qeydiyyat (register)
# ─────────────────────────────────────────────────────────────
def register(ub):
    # ═════════════════════════════════════════════════════════
    # A) Stiker Komandaları
    # ═════════════════════════════════════════════════════════
    async def make(event, reply, src: str, tmp: str, emoji: str, no_pack: bool, mode: str, dur: float):
        from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeSticker, InputStickerSetShortName
        client = event.client
        me = await client.get_me()
        video = _is_video(reply) or (getattr(reply, "sticker", None) and str(src).endswith((".webm", ".tgs")))

        if not video:
            dst = os.path.join(tmp, "s.webp")
            await to_static(src, dst)
            w, h = await _dims(dst)
            attrs = [DocumentAttributeFilename("sticker.webp"),
                     DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetShortName(""))]
            pname, cnt, perr = None, 0, ""
            if not no_pack:
                try:
                    pname, cnt = await add_to_pack(ub, me, dst, video=False, emoji=emoji)
                except Exception as e:
                    perr = str(e)
            await client.send_file(event.chat_id, dst, attributes=attrs, reply_to=reply.id)
            await finish_note(event, pname, cnt, perr)
            return

        if mode == "split":
            n = min(MAX_PARTS, math.ceil(dur / VIDEO_MAX_SEC))
            perr_first = ""
            for i in range(n):
                dst = os.path.join(tmp, f"s_{i}.webm")
                await to_video(src, dst, start=i * VIDEO_MAX_SEC, speed=1.0)
                w, h = await _dims(dst)
                attrs = [DocumentAttributeFilename("sticker.webm"),
                         DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetShortName(""))]
                pname, cnt = None, 0
                if not no_pack:
                    try:
                        pname, cnt = await add_to_pack(ub, me, dst, video=True, emoji=emoji)
                    except Exception as e:
                        if not perr_first:
                            perr_first = str(e)
                await client.send_file(event.chat_id, dst, attributes=attrs, reply_to=reply.id)
                await asyncio.sleep(0.3)
            await finish_note(event, pname, cnt, perr_first, count=n)
            return

        dst = os.path.join(tmp, "s.webm")
        speed = max(1.0, dur / VIDEO_MAX_SEC) if mode == "fast" else 1.0
        if speed > MAX_SPEED:
            speed = MAX_SPEED
        await to_video(src, dst, start=0.0, speed=speed)
        w, h = await _dims(dst)
        attrs = [DocumentAttributeFilename("sticker.webm"),
                 DocumentAttributeSticker(alt=emoji, stickerset=InputStickerSetShortName(""))]
        pname, cnt, perr = None, 0, ""
        if not no_pack:
            try:
                pname, cnt = await add_to_pack(ub, me, dst, video=True, emoji=emoji)
            except Exception as e:
                perr = str(e)
        await client.send_file(event.chat_id, dst, attributes=attrs, reply_to=reply.id)
        note = f"⚡ {speed:.1f}× sürətləndirildi" if speed > 1.05 else ""
        await finish_note(event, pname, cnt, perr, extra=note)

    async def finish_note(event, pname: str, cnt: int, perr: str, count: int = 1, extra: str = ""):
        lines = []
        if count > 1:
            lines.append(f"✂️ {count} video stiker göndərildi")
        if extra:
            lines.append(extra)
        if pname:
            lines.append(f"📦 <a href=\"https://t.me/addstickers/{pname}\">Paketə</a> əlavə olundu (#{cnt})")
        elif perr:
            lines.append(f"⚠️ Paketə düşmədi: <code>{escape(perr[:120])}</code>")
        if lines:
            await safe_edit(event, " · ".join(lines))
            await asyncio.sleep(4)
        with contextlib.suppress(Exception):
            await event.delete()

    @ub.command("stickerpack", pattern=r"^\.stickerpack$",
                help=("stiker paketlərim", "<code>.sticker</code> ilə yaradılan paketlərin linkləri."), category="acc")
    async def on_pack(event):
        me = await event.client.get_me()
        bot_un = await ub.bot_username()
        last = int(get_db().get_setting(S_PACK.format(uid=me.id)) or 0)
        if not last:
            await safe_edit(event, "📦 Hələ paket yoxdur — şəkilə reply edib <code>.sticker</code> yaz")
            return
        links = [f"• <a href=\"https://t.me/addstickers/{_pack_name(me.id, n, bot_un)}\">"
                 f"{_pack_title(me.first_name, n)}</a>" for n in range(1, last + 1)]
        await safe_edit(event, "📦 <b>Stiker paketlərin:</b>\n" + "\n".join(links))

    @ub.command("sticker", pattern=r"^\.(?:sticker|s)(?:\s+(\S+))?(?:\s+(\S+))?$",
                help=("şəkli / GIF-i stikerə çevir",
                      "Reply etdiyin mediadan stiker düzəldir və elə həmin çata göndərir.\n\n"
                      "<b>İstifadə:</b>\n• şəkilə / fotoya <b>reply</b> → <code>.sticker</code>\n"
                      "• emoji ilə: <code>.sticker 😎</code> · qısa: <code>.s</code>\n"
                      "• GIF / qısa video → <b>video stiker</b> (≤3 san., səssiz)\n"
                      "• paketlər: <code>.stickerpack</code>"),
                category="acc")
    async def on_sticker(event):
        arg = (event.pattern_match.group(1) or "").strip()
        mode = "ask"
        for word, m in (("fast", "fast"), ("sürət", "fast"), ("split", "split"), ("böl", "split"),
                        ("first", "first"), ("ilk", "first")):
            if arg.lower() == word or (event.pattern_match.group(2) or "").lower() == word:
                mode = m
                arg = "" if arg.lower() == word else arg
        me_id = (await event.client.get_me()).id
        auto = _s(S_AUTO.format(uid=me_id), "1") == "1"
        no_pack = arg == "-" or event.pattern_match.group(2) == "-" or not auto
        emoji = (arg if arg and arg != "-" else _s(S_EMOJI.format(uid=me_id), DEFAULT_EMOJI))[:8]
        reply = await event.get_reply_message() if event.is_reply else None
        if not reply:
            await safe_edit(event, "🎨 <b>İstifadə:</b> Şəkil, GIF və ya videoya reply edib <code>.sticker</code> yazın.")
            return
        if not (_is_image(reply) or _is_video(reply) or getattr(reply, "sticker", None)):
            await safe_edit(event, "🎨 <b>Bu mesajda şəkil / GIF / video yoxdur</b> — media fayla reply edin.")
            return
        if not shutil.which("ffmpeg") and _is_video(reply):
            await safe_edit(event, "❌ Video stiker üçün ffmpeg lazımdır")
            return
        await safe_edit(event, "🎨 <i>Stikerə çevrilir...</i>")
        tmp = tempfile.mkdtemp(prefix="sticker_")
        try:
            src = await event.client.download_media(reply, file=os.path.join(tmp, "src"))
            if not src:
                raise RuntimeError("media endirilmədi")
            video = _is_video(reply) or (getattr(reply, "sticker", None) and str(src).endswith((".webm", ".tgs")))
            if str(src).endswith(".tgs"):
                raise RuntimeError("animasiyalı (.tgs) stiker artıq stikerdir — çevirmək lazım deyil")
            if not video:
                await make(event, reply, src, tmp, emoji, no_pack, "static", 0)
                return
            dur = await duration_of(src)
            await make(event, reply, src, tmp, emoji, no_pack, "first" if mode == "ask" else mode, dur)
        except Exception as e:
            logger.info(f"🎨 .sticker xətası: {e}")
            await safe_edit(event, f"❌ Stiker alınmadı: <code>{str(e)[:200]}</code>")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ═════════════════════════════════════════════════════════
    # B) TTS (Text to Speech) Komandaları
    # ═════════════════════════════════════════════════════════
    def default_voice() -> str:
        try:
            v = get_db().get_setting(VOICE_KEY)
        except Exception:
            v = None
        return v or LANGS[DEFAULT_LANG][0]

    @ub.command("tts", pattern=r"^\.tts(?:\s+([\s\S]+))?$",
                help=("mətni səsə çevir",
                      "Mətni səs mesajı (voice note) kimi göndərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.tts Salam dünya</code>\n"
                      "• <code>.tts ru: Привет</code> · <code>.tts en: Hello</code> (dil: az/ru/en/tr/de/fr/es/ar/fa)\n"
                      "• başqa mesaja <b>reply</b> → <code>.tts</code>\n\n"
                      "Standart səsi dəyişmək: <code>.ttsvoice</code>"))
    async def on_tts(event):
        arg = (event.pattern_match.group(1) or "").strip()
        text = arg
        if not text and event.is_reply:
            reply = await event.get_reply_message()
            text = (getattr(reply, "message", None) or getattr(reply, "raw_text", None) or "").strip() if reply else ""
        if not text:
            await safe_edit(event, "ℹ️ <b>.tts &lt;mətn&gt;</b> yaz və ya bir mesaja reply et.")
            return
        if not (HAS_EDGE or HAS_GTTS):
            await safe_edit(event, "❌ TTS mühərriki quraşdırılmayıb (pip install edge-tts).")
            return
        voice = default_voice()
        m = _LANG_PREFIX.match(text)
        if m and m.group(1).lower() in LANGS:
            voice = LANGS[m.group(1).lower()][0]
            text = text[m.end():].strip()
        if not text:
            await safe_edit(event, "ℹ️ Dil kodundan sonra mətn yaz: <code>.tts az: Salam</code>")
            return
        if len(text) > MAX_LEN:
            text = text[:MAX_LEN]
        await safe_edit(event, "🗣 <i>Səsə çevrilir...</i>")

        tmp = tempfile.mkdtemp(prefix="tts_")
        mp3, ogg = os.path.join(tmp, "v.mp3"), os.path.join(tmp, "v.ogg")
        try:
            try:
                await synth(text, voice, mp3)
            except Exception as e:
                logger.info(f".tts sintez xətası ({voice}): {e}")
                await safe_edit(event, f"❌ Səs yaradılmadı: <code>{str(e)[:150]}</code>")
                return
            if not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
                await safe_edit(event, "❌ Boş səs faylı alındı.")
                return
            as_voice = await to_ogg(mp3, ogg)
            send_path = ogg if as_voice else mp3
            dur = await duration_sec(send_path)
            reply_to = event.reply_to_msg_id or None
            try:
                await event.client.send_file(
                    event.chat_id, send_path, voice_note=as_voice,
                    reply_to=reply_to, attributes=_attrs(as_voice, dur, text))
            except Exception as e:
                logger.info(f".tts göndərmə xətası: {e}")
                await safe_edit(event, f"❌ Göndərilmədi: <code>{str(e)[:150]}</code>")
                return
            with contextlib.suppress(Exception):
                await event.delete()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @ub.command("ttsvoice", pattern=r"^\.ttsvoice(?:\s+(\S+))?$",
                help=("TTS standart səsi",
                      "Standart TTS səsini göstərir / dəyişir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.ttsvoice</code> — cari səs və dillər\n"
                      "• <code>.ttsvoice az-AZ-BanuNeural</code> — səs adı ilə dəyiş"))
    async def on_ttsvoice(event):
        name = (event.pattern_match.group(1) or "").strip()

        def save(voice):
            get_db().set_setting(VOICE_KEY, voice)

        def card(note=None):
            engine = "edge-tts" if HAS_EDGE else ("gTTS" if HAS_GTTS else "yoxdur ✗")
            body = [("Engine", engine), ("Səs", default_voice()),
                    ("ffmpeg", "● var (voice note)" if shutil.which("ffmpeg") else "○ yoxdur (adi audio)"),
                    "# Dillər", ("Prefiks", " ".join(f"{k}:" for k in LANGS))]
            if note:
                body.append(("Qeyd", note))
            return ff(ub.title("tts"), body, footer="standart səsi düymə ilə seç")

        if name:
            if not re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2,4}-\w+Neural", name):
                await ub.out(event, card("✗ ad belə olmalıdır: az-AZ-BanuNeural"))
                return
            try:
                save(name)
            except Exception as e:
                await ub.out(event, card(f"✗ saxlanmadı: {str(e)[:80]}"))
                return
            await ub.out(event, card("✓ yadda saxlandı" + ("" if HAS_EDGE else " (edge-tts yoxdur!)")))
            return

        btns = []
        out = Out(ub, event)
        for code, (voice, _g) in LANGS.items():
            btns.append(out.btn(code, f"v_{code}"))

            async def pick(o, cb, voice=voice):
                try:
                    save(voice)
                    await o.update(card(f"✓ {voice}"))
                except Exception as e:
                    await o.update(card(f"✗ {str(e)[:80]}"))
            out.on(f"v_{code}")(pick)
        await out.open(card(), [btns[i:i + 5] for i in range(0, len(btns), 5)])
