"""
🔑 Telegram hesabına bot daxilində giriş — userbot/core/tg_login.py

Həm 1️⃣ əsas userbot (userbot/core/accounts), həm 2️⃣ canlı yayım asistanı (voice_assistant_plugin) eyni axından
istifadə edir: telefon → kod → (varsa) 2FA parolu. Nəticə StringSession sətridir — plugin onu öz faylına yazır.

📷 QR ilə giriş (kod gəlmirsə): bot QR şəkli göndərir (+ terminala da çap edir) → hesabın açıq olduğu
telefonda Parametrlər → Cihazlar → «Masaüstü cihazı qoş» / Link Desktop Device ilə skan et. Kod lazım deyil.
QR 30 san.-dən bir yenilənir, 5 dəq. gözləyir. Tələb: pip install qrcode  (şəkil üçün pillow və ya pypng)

Təhlükəsizlik:
  • yalnız bot sahibi (creator) və yalnız şəxsi çatda;
  • nömrə / kod / parol mesajları dərhal silinir;
  • kod rəqəmlər arasında boşluqla yazılmalıdır — düz yazılmış kod Telegram tərəfindən "paylaşılıb" sayılıb
    etibarsız edilir;
  • gözləmə 10 dəq., sonra avtomatik bağlanır.
"""
import asyncio
import contextlib
import io
import logging
import os
import re
import time
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from userbot.core.userbot_api import tr

logger = logging.getLogger(__name__)

API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
TTL = 600
QR_WAIT = 300                   # QR giriş ən çox bu qədər gözləyir (san.)
_ACTIVE = {}                    # açar → LoginFlow (aktiv giriş)
_FLOWS = {}                     # açar → LoginFlow (hamısı — 📷 düyməsi üçün)


def qr_png(data: str):
    """QR şəkli (PNG bayt) və terminal üçün ASCII; qrcode yoxdursa (None, None)."""
    try:
        import qrcode
    except ImportError:
        return None, None
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    png = None
    try:
        buf = io.BytesIO()
        qr.make_image().save(buf)
        png = buf.getvalue()
    except Exception:
        try:
            from qrcode.image.pure import PyPNGImage
            buf = io.BytesIO()
            qr.make_image(image_factory=PyPNGImage).save(buf)
            png = buf.getvalue()
        except Exception as e:
            logger.warning(f"📷 QR şəkli yaradılmadı (pillow və ya pypng quraşdır): {e}")
    ascii_ = None
    with contextlib.suppress(Exception):
        out = io.StringIO()
        qr.print_ascii(out=out, invert=True)
        ascii_ = out.getvalue()
    return png, ascii_


def _register_qr_handler(context):
    """📷 düyməsi (tgl:<açar>:qr) — bütün LoginFlow-lar üçün bir dəfə qeydiyyat."""
    if getattr(context, "_tgl_handler", False):
        return
    context._tgl_handler = True
    from aiogram import F

    @context.dp.callback_query(F.data.startswith("tgl:"))
    async def tgl_cb(cb):
        if not context.creator_id or cb.from_user.id != context.creator_id:
            await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
            return
        _, key, act = (cb.data.split(":") + ["", ""])[:3]
        flow = _FLOWS.get(key)
        if flow is None or act != "qr":
            await cb.answer()
            return
        try:
            text, kb = await flow.begin_qr(cb.message.chat.id, cb.message.message_id)
        except Exception as e:
            await cb.answer(str(e)[:190], show_alert=True)
            return
        await cb.answer("📷 QR hazırlanır...")
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


def any_waiting(uid) -> bool:
    """music_plugin: giriş məlumatı mahnı axtarışı sayılmasın."""
    return any(f.waiting(uid) for f in list(_ACTIVE.values()))


def write_secret(path: str, data: str):
    """Sessiya / açar faylını atomik (temp + os.replace) və yalnız sahibə oxunan (0600) yazır.
    Əvvəl mövcud 0644 fayl 0644 qalırdı, yazı yarıda kəsiləndə isə fayl boş qalırdı."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _code_where(t) -> str:
    """SentCode.type → kod harada? (Telethon tipi adına görə — köhnə/yeni versiyalarda işləsin)."""
    if t is None:
        return "naməlum"
    n = type(t).__name__
    ln = getattr(t, "length", None)
    tail = f" ({ln} rəqəm)" if ln else ""
    return {
        "SentCodeTypeApp": "📲 Telegram tətbiqinə — <b>digər cihazda</b>, «Telegram» (777000) çatına",
        "SentCodeTypeSms": "💬 SMS ilə",
        "SentCodeTypeCall": "📞 zəng ilə (kodu səsli deyəcək)",
        "SentCodeTypeFlashCall": "📞 flash-zəng (avtomatik)",
        "SentCodeTypeMissedCall": "📞 buraxılmış zəng — nömrənin son rəqəmləri kod",
        "SentCodeTypeEmailCode": "📧 hesabın <b>login e-poçtuna</b>",
        "SentCodeTypeSetUpEmailRequired": "📧 ❗ Telegram əvvəlcə login e-poçtu qurmağı tələb edir",
        "SentCodeTypeFragmentSms": "💎 Fragment SMS (fragment.com)",
        "SentCodeTypeFirebaseSms": "💬 Firebase SMS (yalnız rəsmi tətbiq)",
        "SentCodeTypeSmsWord": "💬 SMS (söz)",
        "SentCodeTypeSmsPhrase": "💬 SMS (ifadə)",
    }.get(n, n) + tail


class LoginFlow:
    """
    on_done(session_str, me) → (mətn, klaviatura) | qaytarılan mətn "❌" ilə başlayırsa sessiya saxlanmır
    cancel_cb — "❌ Ləğv et" düyməsinin callback_data-sı (plugin özü cancel() çağırır)
    """

    def __init__(self, key: str, context, title: str, hint: str, on_done, cancel_cb: str,
                 resend_cb: str = ""):
        self.key, self.ctx, self.title, self.hint = key, context, title, hint
        self.on_done, self.cancel_cb, self.resend_cb = on_done, cancel_cb, resend_cb
        self.st = None
        self._last = None
        _FLOWS[key] = self
        with contextlib.suppress(Exception):
            _register_qr_handler(context)

    # ── vəziyyət ──
    def waiting(self, uid) -> bool:
        st = self.st
        return bool(st) and uid == self.ctx.creator_id and time.time() < st["until"]

    def view(self, note: str = ""):
        step = (self.st or {}).get("step")
        text = f"🔑 <b>{self.title}</b>\n━━━━━━━━━━━━━━━━━━\n"
        if step == "phone":
            text += ("📱 Hesabın <b>telefon nömrəsini</b> göndər (beynəlxalq formatda):\n"
                     "<code>+994501234567</code>\n\n" + (f"<i>{self.hint}</i>\n" if self.hint else "")
                     + "<i>Mesajın dərhal silinir.</i>")
        elif step == "code":
            st = self.st
            text += f"📨 Kod göndərildi: {_code_where(st.get('type'))}\n"
            if st.get("next"):
                text += f"🔁 Yenidən göndərsən: {_code_where(st['next'])}\n"
            text += ("\n"
                     "⚠️ Kodu <b>rəqəmlər arasında boşluqla</b> yaz: <code>1 2 3 4 5</code>\n"
                     "<i>Düz yazılmış kodu Telegram \"paylaşılıb\" sayıb etibarsız edir.</i>")
        elif step == "qr":
            text += ("📷 <b>QR ilə giriş</b> — kod lazım deyil\n\n"
                     "1. Aşağıdakı QR şəklini <b>başqa ekranda</b> aç (kompüterdə Telegram Desktop / web, "
                     "və ya botun terminalına bax — QR orada da çap olunur)\n"
                     "2. Hesabın açıq olduğu telefonda: <b>Parametrlər → Cihazlar → Masaüstü cihazı qoş</b> "
                     "(Link Desktop Device)\n"
                     "3. QR-ı skan et — giriş avtomatik tamamlanır\n\n"
                     "<i>QR 30 san.-dən bir yenilənir, 5 dəq. gözləyir. 2FA varsa sonra parol soruşulacaq.</i>")
        elif step == "password":
            text += "🔐 Hesabda <b>iki addımlı doğrulama</b> var — parolu göndər.\n<i>Mesaj dərhal silinir.</i>"
        if note:
            text += f"\n\n{note}"
        row = [InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data=self.cancel_cb)]
        # Telegram başqa üsul təklif etmirsə (next_type yoxdur) 🔁 düyməsi göstərilmir —
        # basılsa SEND_CODE_UNAVAILABLE qaytarır və köhnə kodu da ləğv edə bilər
        if step == "code" and self.resend_cb and (self.st or {}).get("next") and not self.st.get("no_resend"):
            row.insert(0, InlineKeyboardButton(text=tr("🔁 Yenidən göndər"), callback_data=self.resend_cb))
        rows = [row]
        if step in ("phone", "code"):
            rows.insert(0, [InlineKeyboardButton(text=tr("📷 Kod gəlmir? QR ilə gir"), callback_data=f"tgl:{self.key}:qr")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def resend(self):
        """Kodu növbəti üsulla (SMS/zəng) yenidən istə."""
        st = self.st
        if not st or st.get("step") != "code" or not st.get("client"):
            return "⚠️ Aktiv kod addımı yoxdur"
        if not st.get("next") or st.get("no_resend"):
            return "ℹ️ Telegram başqa göndərmə üsulu vermir — artıq göndərilmiş kodu yaz"
        from telethon import functions
        from telethon.errors import FloodWaitError
        try:
            sent = await st["client"](functions.auth.ResendCodeRequest(st["phone"], st["hash"]))
        except FloodWaitError as e:
            return f"⏳ {e.seconds} san. gözlə"
        except Exception as e:
            if type(e).__name__ == "SendCodeUnavailableError" or "SEND_CODE_UNAVAILABLE" in str(e).upper():
                # bütün üsullar (tətbiq → SMS → zəng) işlənib: əvvəlki kod hələ keçərlidir
                st["no_resend"] = True
                st["until"] = time.time() + TTL
                await self._show(*self.view(
                    "ℹ️ <b>Telegram başqa göndərmə üsulu vermir</b> — bütün variantlar artıq işlənib.\n"
                    "• Əvvəl göndərilmiş kod hələ keçərlidir: «Telegram» (777000) çatına bax və onu "
                    "<b>boşluqla</b> yaz.\n"
                    "• Kod yoxdursa: <b>📷 QR ilə gir</b> (kod lazım deyil) və ya bir neçə saat gözlə "
                    "(tez-tez cəhd limiti uzadır)."))
                return "ℹ️ Yeni kod göndərilmədi — əvvəlki kodu yaz"
            return f"❌ {str(e)[:150]}"
        st.update(hash=sent.phone_code_hash, type=sent.type, next=getattr(sent, "next_type", None),
                  until=time.time() + TTL)
        logger.info(f"🔑 Kod yenidən göndərildi ({self.key}): {type(sent.type).__name__}")
        await self._show(*self.view("🔁 Yenidən göndərildi"))
        return ""

    async def begin(self, chat_id: int, msg_id: int):
        await self.cancel()
        if not API_ID or not API_HASH:
            raise RuntimeError("config.env: TELETHON_API_ID / TELETHON_API_HASH yoxdur")
        self.st = {"step": "phone", "until": time.time() + TTL, "chat": chat_id, "msg": msg_id}
        _ACTIVE[self.key] = self
        return self.view()

    async def cancel(self):
        st, self.st = self.st, None
        _ACTIVE.pop(self.key, None)
        if st:
            task = st.get("qr_task")
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
            await self._drop_qr_photo(st)
        if st and st.get("client"):
            with contextlib.suppress(Exception):
                await st["client"].disconnect()

    # ── 📷 QR giriş ──
    async def begin_qr(self, chat_id: int, msg_id: int):
        await self.cancel()
        if not API_ID or not API_HASH:
            raise RuntimeError("config.env: TELETHON_API_ID / TELETHON_API_HASH yoxdur")
        png, _ = qr_png("tg://login?token=test")
        if png is None:
            raise RuntimeError("QR üçün serverdə: pip install qrcode pillow  — sonra restart")
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), int(API_ID), API_HASH)
        await client.connect()
        qr = await client.qr_login()
        self.st = {"step": "qr", "until": time.time() + QR_WAIT, "chat": chat_id, "msg": msg_id, "client": client}
        self._last = self.st
        _ACTIVE[self.key] = self
        self.st["qr_task"] = asyncio.create_task(self._qr_loop(qr, self.st))
        logger.info(f"📷 QR giriş başladı ({self.key})")
        return self.view()

    async def _drop_qr_photo(self, st):
        mid = st.get("qr_msg")
        if mid:
            st["qr_msg"] = None
            with contextlib.suppress(Exception):
                await self.ctx.bot.delete_message(st["chat"], mid)

    async def _qr_loop(self, qr, st):
        from datetime import datetime, timezone
        from aiogram.types import BufferedInputFile, InputMediaPhoto
        from telethon.errors import SessionPasswordNeededError
        bot = self.ctx.bot
        deadline = time.time() + QR_WAIT
        caption = ("📷 <b>Bu QR-ı skan et</b>\nTelefonda: Parametrlər → Cihazlar → Masaüstü cihazı qoş\n"
                   "<i>Öz ekranını skan etmək olmur — QR-ı kompüterdə aç və ya terminala bax.</i>")
        try:
            while self.st is st and time.time() < deadline:
                png, ascii_ = await asyncio.to_thread(qr_png, qr.url)
                if ascii_:
                    print(f"\n📷 Userbot QR girişi ({self.key}) — telefonda Cihazlar → Masaüstü cihazı qoş:\n"
                          f"{ascii_}", flush=True)
                file = BufferedInputFile(png, filename="login_qr.png")
                if st.get("qr_msg"):
                    with contextlib.suppress(Exception):
                        await bot.edit_message_media(chat_id=st["chat"], message_id=st["qr_msg"],
                                                     media=InputMediaPhoto(media=file, caption=caption,
                                                                           parse_mode="HTML"))
                else:
                    sent = await bot.send_photo(st["chat"], file, caption=caption, parse_mode="HTML")
                    st["qr_msg"] = sent.message_id
                left = (qr.expires - datetime.now(timezone.utc)).total_seconds()
                try:
                    await qr.wait(timeout=max(5.0, min(left, deadline - time.time())))
                except asyncio.TimeoutError:
                    if time.time() >= deadline:
                        break
                    await qr.recreate()
                    continue
                except SessionPasswordNeededError:
                    await self._drop_qr_photo(st)
                    st.update(step="password", until=time.time() + TTL)
                    await self._show(*self.view("✅ QR skan olundu"))
                    return
                await self._drop_qr_photo(st)
                logger.info(f"📷 QR giriş uğurlu ({self.key})")
                await self._finish()
                return
            if self.st is st:
                await self.cancel()
                await self._show(*self._done_view("⌛ QR-ın vaxtı bitdi — 🔑 ilə yenidən başla"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"📷 QR giriş xətası ({self.key}): {e}", exc_info=True)
            if self.st is st:
                await self.cancel()
                await self._show(*self._done_view(f"❌ QR giriş alınmadı: <code>{escape(str(e)[:200])}</code>"))

    async def _show(self, text, kb):
        st = self.st or self._last
        with contextlib.suppress(Exception):
            await self.ctx.bot.edit_message_text(chat_id=st["chat"], message_id=st["msg"], text=text,
                                                 parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    # ── mesaj ──
    async def handle(self, message):
        st = self.st
        self._last = st
        text = (message.text or "").strip()
        with contextlib.suppress(Exception):
            await message.delete()
        from telethon.errors import (FloodWaitError, PasswordHashInvalidError, PhoneCodeExpiredError,
                                     PhoneCodeInvalidError, PhoneNumberBannedError, PhoneNumberInvalidError,
                                     SessionPasswordNeededError)
        if st["step"] == "qr":
            return await self._show(*self.view("ℹ️ QR gözlənilir — mətn lazım deyil"))
        st["until"] = time.time() + TTL
        logger.info(f"🔑 Giriş mesajı alındı ({self.key}) — addım: {st['step']}")
        try:
            if st["step"] == "phone":
                phone = re.sub(r"[^\d+]", "", text)
                if not re.fullmatch(r"\+?\d{8,15}", phone):
                    return await self._show(*self.view("⚠️ Nömrə düzgün deyil — <code>+994501234567</code>"))
                from telethon import TelegramClient
                from telethon.sessions import StringSession
                client = TelegramClient(StringSession(), int(API_ID), API_HASH)
                await client.connect()
                st["client"] = client
                sent = await client.send_code_request(phone)
                tname = type(sent.type).__name__
                logger.info(f"🔑 Kod istəndi ({self.key}): {tname}, next={type(getattr(sent, 'next_type', None)).__name__}")
                if tname == "SentCodeTypeSetUpEmailRequired":
                    await self.cancel()
                    return await self._show(*self._done_view(
                        "📧 Telegram bu giriş üçün kod göndərmədi — əvvəlcə hesaba <b>login e-poçtu</b> "
                        "qurmaq tələb olunur.\nRəsmi tətbiqdə: Parametrlər → Məxfilik → Login e-poçtu, "
                        "sonra 🔑 ilə yenidən başla."))
                st.update(step="code", phone=phone, hash=sent.phone_code_hash,
                          type=sent.type, next=getattr(sent, "next_type", None))
                return await self._show(*self.view())
            if st["step"] == "code":
                code = re.sub(r"\D", "", text)
                if not 4 <= len(code) <= 8:
                    return await self._show(*self.view("⚠️ Kod tapılmadı — rəqəmləri boşluqla yaz: "
                                                       "<code>1 2 3 4 5</code>"))
                logger.info(f"🔑 Kod yoxlanılır ({self.key}): {len(code)} rəqəm")
                try:
                    await st["client"].sign_in(st["phone"], code, phone_code_hash=st["hash"])
                except SessionPasswordNeededError:
                    st["step"] = "password"
                    return await self._show(*self.view())
                return await self._finish()
            if st["step"] == "password":
                await st["client"].sign_in(password=text)
                return await self._finish()
        except PhoneNumberInvalidError:
            await self._show(*self.view("❌ Nömrə səhvdir və ya Telegram-da yoxdur"))
        except PhoneNumberBannedError:
            await self.cancel()
            await self._show(*self._done_view("❌ Bu nömrə Telegram tərəfindən bloklanıb"))
        except PhoneCodeInvalidError:
            await self._show(*self.view("❌ Kod səhvdir — yenidən yaz (boşluqla)"))
        except PhoneCodeExpiredError:
            await self.cancel()
            await self._show(*self._done_view("⌛ Kodun vaxtı bitdi (və ya düz yazılıb \"paylaşılmış\" sayıldı) — "
                                              "🔑 ilə yenidən başla"))
        except PasswordHashInvalidError:
            await self._show(*self.view("❌ Parol səhvdir — yenidən göndər"))
        except FloodWaitError as e:
            await self.cancel()
            await self._show(*self._done_view(f"⏳ Telegram limiti: {e.seconds} san. sonra yenidən sına"))
        except Exception as e:
            logger.warning(f"🔑 Giriş xətası ({self.key}): {e}")
            await self._show(*self.view(f"❌ <code>{escape(str(e)[:200])}</code>"))

    def _done_view(self, note):
        return note, InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("⬅️ Geri"), callback_data=self.cancel_cb)]])

    async def _finish(self):
        st = self.st
        client = st["client"]
        session_str = client.session.save()
        me = await client.get_me()
        try:
            text, kb = await self.on_done(session_str, me, client)
        finally:
            st["client"] = None
            with contextlib.suppress(Exception):
                await client.disconnect()
            self.st = None
            _ACTIVE.pop(self.key, None)
        await self._show(text, kb)
