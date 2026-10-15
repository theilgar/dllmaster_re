"""
📱 Telethon userbot — vahid client sahibi (şəxsi hesab) + 📱 Userbot hesabları paneli.

Bu plugin client-i yaradır və qoşur (userbot/core/tg_session.py) — bütün proses üçün bir client:
  context.telethon_client — depo_filler_plugin, userbot/core/host, userbot/plugins/* hamısı bunu işlədir.

PRIORITY 10 — digər plugin-lərdən əvvəl yüklənir ki, onlar setup() zamanı client-i hazır görsün.

Hesablar (/menu → 🖥 Sistem → 📱 Userbotlar):
  1️⃣ Əsas userbot — TAM səlahiyyət: depo skanı və Telegram mənbələri, 🧬 depo indeksi / dublikat silmə,
     .menu və bütün userbot komandaları, /scan_chat, köməkçi işlər. Bot menyusundan 🔑 ilə qoşulur
     (data/userbot.string) və ya köhnə qayda ilə login.py (userbot.session).
  2️⃣ Canlı yayım asistanı — YALNIZ səs: qrup səsli yayımlarında /play ilə mahnı oxudur.
     Heç bir komanda qəbul etmir, depoya / bota toxunmur (voice_assistant_plugin).
  Eyni hesab iki rola qoşula bilməz.
"""
import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from userbot.core import tg_login, tg_session
from userbot.core.userbot_api import tr
from common.utilities import format_bytes
from bot.core import panel as ui
from bot.core.panel import show as panel_show

logger = logging.getLogger(__name__)
PRIORITY = 10


def setup(context):
    from common.database import get_db
    ub1_enabled = get_db().get_setting("userbot1:enabled", "1") == "1"
    context.userbot1_enabled = ub1_enabled

    client = None
    if ub1_enabled:
        client = tg_session.make_client(context)
        if client is not None:
            context.telethon_task = asyncio.create_task(tg_session.connect(context))
    else:
        context.telethon_client = None
        logger.info("📱 Userbot 1 ayarlarda söndürülüb — avtomatik qoşulmadı.")

    dp, bot = context.dp, context.bot
    context.va_waiting = tg_login.any_waiting          # music_plugin: giriş mətni axtarış sayılmasın

    is_creator = context.is_creator

    # ── idarəetmə funksiyaları (yandır / söndür) ──
    def who(me) -> str:
        if not me:
            return "—"
        name = escape(" ".join(x for x in (me.first_name, me.last_name) if x) or "")
        return f"{name}" + (f" (@{escape(me.username)})" if me.username else "") + f" · <code>{me.id}</code>"

    def is_ub1_active() -> bool:
        c = getattr(context, "telethon_client", None)
        if c is not None:
            try:
                if c.is_connected():
                    return True
            except Exception as _e:
                logger.debug("udulan xəta [userbot/core/accounts.py:62]: %r", _e)
        return bool(getattr(context, "userbot1_enabled", True) and c is not None)

    async def stop_ub1() -> str:
        get_db().set_setting("userbot1:enabled", "0")
        context.userbot1_enabled = False
        client = getattr(context, "telethon_client", None)
        task = getattr(context, "telethon_task", None)
        if task and not task.done():
            task.cancel()
            context.telethon_task = None
        if client is not None:
            with contextlib.suppress(Exception):
                if client.is_connected():
                    await client.disconnect()
        context.telethon_client = None
        tg_session._rebind_userbot(context, None)
        logger.info("🛑 1️⃣ Əsas userbot dayandırıldı.")
        return "🛑 1️⃣ Əsas userbot söndürüldü"

    async def start_ub1() -> str:
        get_db().set_setting("userbot1:enabled", "1")
        context.userbot1_enabled = True
        me = await tg_session.reconnect(context)
        if me:
            logger.info(f"🟢 1️⃣ Əsas userbot yandırıldı: {me.first_name}")
            return f"🟢 1️⃣ Əsas userbot yandırıldı: {who(me)}"
        return "⚠️ 1️⃣ Əsas userbot yandırıldı, amma qoşulma alınmadı — loglara baxın"

    async def toggle_ub1() -> str:
        if is_ub1_active():
            return await stop_ub1()
        else:
            return await start_ub1()

    context.is_ub1_active = is_ub1_active
    context.userbot1_toggle = toggle_ub1

    def is_ub2_active() -> bool:
        va = getattr(context, "voice_assistant", None)
        return bool(va and va.enabled and va.running)

    async def stop_ub2() -> str:
        va = getattr(context, "voice_assistant", None)
        if not va:
            return "⚠️ 2️⃣ Asistan yüklənməyib"
        from bot.plugins.voice_assistant_plugin import _set, S_ON
        _set(S_ON, "0")
        await va.stop()
        return "🔴 2️⃣ UB2 (Asistan) söndürüldü"

    async def start_ub2() -> str:
        va = getattr(context, "voice_assistant", None)
        if not va:
            return "⚠️ 2️⃣ Asistan yüklənməyib"
        from bot.plugins.voice_assistant_plugin import _set, S_ON
        _set(S_ON, "1")
        err = await va.start()
        return f"⚠️ {escape(err)}" if err else "🟢 2️⃣ UB2 (Asistan) işləyir"

    async def toggle_ub2() -> str:
        if is_ub2_active():
            return await stop_ub2()
        else:
            return await start_ub2()

    context.is_ub2_active = is_ub2_active
    context.userbot2_toggle = toggle_ub2

    # ── vəziyyət ──
    def main_status() -> tuple:
        c = getattr(context, "telethon_client", None)
        me = getattr(context, "telethon_me", None)
        if not getattr(context, "userbot1_enabled", True) and (c is None or not c.is_connected()):
            return "🔴", "söndürülüb (əl ilə dayandırılıb)", me
        if not tg_session.API_ID or not tg_session.API_HASH:
            return "❌", "TELETHON_API_ID / TELETHON_API_HASH yoxdur", None
        if getattr(context, "telethon_unauthorized", False) and not me:
            return "❌", "sessiya etibarsız idi (çıxılıb) — 🔑 ilə yenidən qoş", None
        if c is None:
            return ("⚠️", "hesab qoşulmayıb", None) if not tg_session.session_source() \
                else ("⚠️", "client yaradılmadı (telethon?)", None)
        if c.is_connected() and me:
            return "🟢", "qoşulub", me
        return "🟡", "qoşulur / bağlantı yoxdur", me

    def va_status() -> tuple:
        va = getattr(context, "voice_assistant", None)
        if va is None:
            return "❌", "voice_assistant_plugin yüklənməyib", None
        if not va.has_session():
            return "⚠️", "hesab qoşulmayıb", None
        if not va.enabled:
            return "🔴", "söndürülüb", va.me
        if va.running:
            n = sum(1 for s in va.chats.values() if s.current)
            return "🟢", "işləyir" + (f" · {n} yayım" if n else ""), va.me
        return "⚠️", va.error or va.status, va.me

    def label() -> str:
        a, b = main_status()[0], va_status()[0]
        return f"📱 Userbotlar · 1️⃣{a} 2️⃣{b}"

    context.userbots_label = label

    def hub_view(note: str = ""):
        i1, s1, me1 = main_status()
        i2, s2, me2 = va_status()
        lines = [
            "📱 <b>Userbot hesabları</b>\n━━━━━━━━━━━━━━━━━━",
            f"1️⃣ <b>Userbot 1 (Əsas):</b> {i1} {escape(s1)}" + (f" · {who(me1)}" if me1 else ""),
            f"2️⃣ <b>Userbot 2 (Asistan):</b> {i2} {escape(s2)}" + (f" · {who(me2)}" if me2 else ""),
        ]
        if me1 and me2 and me1.id == me2.id:
            lines.append("\n⚠️ <b>Hər iki rol eyni hesabdadır!</b> Asistanı başqa hesabla qoş.")
        lines.append("\n<i>Tənzimləmək üçün istədiyiniz userbotu seçin:</i>")
        if note:
            lines.append(f"\n{note}")

        b1_icon = "🟢" if is_ub1_active() else ("🔴" if tg_session.session_source() else "⚪")
        va = getattr(context, "voice_assistant", None)
        b2_icon = "🟢" if is_ub2_active() else ("🔴" if (va and va.has_session()) else "⚪")

        rows = [
            [InlineKeyboardButton(text=tr(f"1️⃣ Userbot 1 (Əsas) · {b1_icon}"), callback_data="ubacc:ub1")],
            [InlineKeyboardButton(text=tr(f"2️⃣ Userbot 2 (Asistan) · {b2_icon}"), callback_data="ubacc:ub2")],
            [InlineKeyboardButton(text=tr("🗄 Userbot bazası"), callback_data="ubacc:db")],
            ui.nav("menu:bots", "Userbot və audit", refresh_data="ubacc:panel"),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def ub1_view(note: str = ""):
        i1, s1, me1 = main_status()
        active = is_ub1_active()
        has_session = bool(tg_session.session_source())
        src = {"string": "🔑 bot menyusu ilə (data/userbot.string)",
               "file": f"📄 login.py ({escape(tg_session.SESSION_FILE)})"}.get(tg_session.session_source(), "—")
        mode = getattr(context, "telethon_session_mode", "")
        lines = [
            "1️⃣ <b>Userbot 1 (Əsas)</b>\n━━━━━━━━━━━━━━━━━━",
            f"Vəziyyət: {i1} <b>{escape(s1)}</b>",
            f"Hesab: 👤 {who(me1)}",
            f"Sessiya: 🔐 {src}" + (" · yaddaşda" if mode == "memory" else ""),
            "",
            "<i>📦 Depo skanı və Telegram mənbələri · 🧬 depo indeksi · .menu və userbot komandaları · /scan_chat</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = []
        if has_session:
            rows.append([ui.toggle("1️⃣ UB1", active, "ubacc:stop1" if active else "ubacc:start1", compact=True)])
            if active:
                rows.append([
                    InlineKeyboardButton(text=tr("🔄 Yenidən qoşul"), callback_data="ubacc:reconnect1"),
                    InlineKeyboardButton(text=tr("🔁 Hesabı dəyiş"), callback_data="ubacc:login")
                ])
                rows.append([
                    InlineKeyboardButton(text=tr("🚪 Hesabdan çıx"), callback_data="ubacc:logout1")
                ])
        else:
            rows.append([InlineKeyboardButton(text=tr("🔑 1️⃣ Əsas userbotu qoş"), callback_data="ubacc:login")])

        rows.append(ui.nav("ubacc:panel", "Userbotlar", refresh_data="ubacc:ub1"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def ub2_view(note: str = ""):
        i2, s2, me2 = va_status()
        active = is_ub2_active()
        va = getattr(context, "voice_assistant", None)
        has_session = bool(va and va.has_session())
        lines = [
            "2️⃣ <b>Userbot 2 (Köməkçi / Asistan)</b>\n━━━━━━━━━━━━━━━━━━",
            f"Vəziyyət: {i2} <b>{escape(s2)}</b>",
            f"Hesab: 👤 {who(me2)}",
            "",
            "<i>⚡ Depo sinxronizasiyası və paralel skan işçisi · 🎙 İstəyə görə qruplarda səsli yayım (və ya yalnız Depo rejimi).</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = []
        if has_session:
            rows.append([ui.toggle("2️⃣ UB2", active, "ubacc:stop2" if active else "ubacc:start2", compact=True)])
            if active:
                rows.append([
                    InlineKeyboardButton(text=tr("🔄 Yenidən qoşul"), callback_data="ubacc:reconnect2"),
                    InlineKeyboardButton(text=tr("🔁 Hesabı dəyiş"), callback_data="va:login")
                ])
                rows.append([
                    InlineKeyboardButton(text=tr("🎙 Canlı Yayım Paneli"), callback_data="va:panel"),
                    InlineKeyboardButton(text=tr("🚪 Hesabdan çıx"), callback_data="ubacc:logout2")
                ])
        else:
            rows.append([InlineKeyboardButton(text=tr("🔑 2️⃣ Asistanı qoş (UB2)"), callback_data="va:login")])

        rows.append(ui.nav("ubacc:panel", "Userbotlar", refresh_data="ubacc:ub2"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    context.userbots_hub_view = hub_view

    # ── 🗄 userbot bazası ──
    NS_NAMES = {"filters": "🎯 Filtrlər", "settings": "⚙️ Ayarlar (köhnə userbot:*)", "meta": "🏷 Xidməti"}


    def db_view(note: str = ""):
        try:
            from userbot.core.userbot_db import get_udb
            st = get_udb().stats()
        except Exception as e:
            return (f"❌ Userbot bazası açılmadı: <code>{escape(str(e)[:200])}</code>",
                    InlineKeyboardMarkup(inline_keyboard=[ui.nav("ubacc:panel", "Hesablar")]))
        lines = ["🗄 <b>Userbot bazası</b>\n━━━━━━━━━━━━━━━━━━",
                 f"📁 <code>{escape(st['path'])}</code>",
                 f"💾 Ölçü: <b>{format_bytes(st['size'], space=True)}</b> <i>(botun əsas bazasından ayrıdır)</i>"]
        if st["records"]:
            lines.append("\n📑 <b>Qeydlər</b>")
            for ns, (n, chats) in sorted(st["records"].items()):
                lines.append(f"• {escape(NS_NAMES.get(ns, ns))}: <b>{n}</b> · {chats} çat")
        if st["kv"]:
            lines.append("\n🔑 <b>Açar / dəyər</b>")
            for ns, n in sorted(st["kv"].items()):
                lines.append(f"• {escape(NS_NAMES.get(ns, ns))}: {n}")
        if st["log"]:
            lines.append("\n📜 <b>Jurnal</b>")
            for ns, n in sorted(st["log"].items()):
                lines.append(f"• {escape(ns)}: {n}")
        lines.append("\n<i>Userbot pluginləri: <code>ub.db.kv_* / rec_* / log_*</code> — userbot/core/userbot_db.py</i>")
        if note:
            lines.append(f"\n{note}")
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("💾 Yedəyi göndər"), callback_data="ubacc:dbbak"),
             InlineKeyboardButton(text=tr("🧹 Sıxlaşdır"), callback_data="ubacc:dbvac")],
            ui.nav("ubacc:panel", "Hesablar", refresh_data="ubacc:db")])

    # ── 🔑 1️⃣ giriş ──
    async def on_done(session_str, me, client):
        va = getattr(context, "voice_assistant", None)
        if va is not None and va.me and va.me.id == me.id:
            with contextlib.suppress(Exception):
                await client.log_out()
            return ub1_view("❌ <b>Bu hesab artıq 2️⃣ canlı yayım asistanıdır.</b> Əsas userbot üçün başqa "
                            "hesab qoş.")
        old = getattr(context, "telethon_me", None)
        new_me = await tg_session.swap(context, session_str)
        if not new_me:
            return ub1_view("⚠️ Sessiya saxlandı, amma qoşulma alınmadı — 🔄 ilə yenidən sına")
        note = f"✅ <b>1️⃣ Əsas userbot qoşuldu:</b> {who(new_me)}"
        if old and old.id != new_me.id:
            note += f"\n<i>Əvvəlki hesab ({escape(old.first_name or '')}) əvəz olundu.</i>"
        return ub1_view(note)

    # userbot/core/tg_login.py köhnədirsə (resend_cb yoxdur) — plugin yenə yüklənsin, sadəcə 🔁 düyməsi olmasın
    import inspect
    extra = {"resend_cb": "ubacc:resend"} \
        if "resend_cb" in inspect.signature(tg_login.LoginFlow.__init__).parameters else {}
    if not extra:
        logger.warning("🔑 userbot/core/tg_login.py köhnədir — 🔁 Yenidən göndər söndürülüb (yeni faylı qoy və restart et)")
    flow = tg_login.LoginFlow(
        "main", context, "1️⃣ Əsas userbot hesabını qoş",
        "Bu hesab tam səlahiyyətli olacaq: depo skanı, Telegram mənbələri, .menu komandaları.",
        on_done, "ubacc:cancel", **extra)

    show = panel_show

    @dp.callback_query(F.data.startswith("ubacc:"))
    async def ubacc_cb(cb: types.CallbackQuery):
        try:
            await _ubacc_cb(cb)
        except Exception as e:
            logger.error(f"📱 Userbotlar paneli xətası: {e}", exc_info=True)
            with contextlib.suppress(Exception):
                await cb.answer(f"❌ {str(e)[:180]}", show_alert=True)

    async def _ubacc_cb(cb: types.CallbackQuery):
        if not is_creator(cb.from_user.id):
            await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
            return
        act = cb.data.split(":", 1)[1]
        note = ""
        if act == "panel":
            await cb.answer()
            await show(cb, *hub_view())
            return
        if act == "ub1":
            await cb.answer()
            await show(cb, *ub1_view())
            return
        if act == "ub2":
            await cb.answer()
            await show(cb, *ub2_view())
            return
        if act in ("login", "login1"):
            try:
                text, kb = await flow.begin(cb.message.chat.id, cb.message.message_id)
            except Exception as e:
                await cb.answer(str(e)[:190], show_alert=True)
                return
            await cb.answer()
            await show(cb, text, kb)
            return
        if act == "resend":
            if not hasattr(flow, "resend"):
                await cb.answer("⚠️ userbot/core/tg_login.py köhnədir", show_alert=True)
                return
            err = await flow.resend()
            await cb.answer(err or "🔁 Göndərildi", show_alert=bool(err))
            return
        if act in ("db", "dbbak", "dbvac"):
            from userbot.core.userbot_db import get_udb
            note = ""
            if act == "dbbak":
                await cb.answer("💾 Hazırlanır...")
                import os
                import tempfile
                from aiogram.types import FSInputFile
                path = os.path.join(tempfile.gettempdir(), f"userbot_{time.strftime('%Y%m%d_%H%M')}.db")
                try:
                    await asyncio.to_thread(get_udb().backup, path)
                    await bot.send_document(cb.message.chat.id, FSInputFile(path),
                                            caption="🗄 Userbot bazasının yedəyi (SQLite)")
                    note = "✅ Yedək göndərildi"
                except Exception as e:
                    note = f"❌ Yedək alınmadı: <code>{escape(str(e)[:150])}</code>"
                finally:
                    with contextlib.suppress(OSError):
                        os.remove(path)
            elif act == "dbvac":
                await cb.answer("🧹 Sıxlaşdırılır...")
                freed = await asyncio.to_thread(get_udb().vacuum)
                note = f"🧹 Sıxlaşdırıldı — {format_bytes(max(0, freed), space=True)} boşaldı"
            else:
                await cb.answer()
            await show(cb, *db_view(note))
            return
        if act in ("toggle1", "stop1", "start1"):
            if act == "toggle1":
                await cb.answer()
                note = await toggle_ub1()
            elif act == "stop1":
                await cb.answer("🛑 Söndürülür...")
                note = await stop_ub1()
            elif act == "start1":
                await cb.answer("🟢 Qoşulur...")
                note = await start_ub1()
            await show(cb, *ub1_view(note))
            return
        if act in ("reconnect", "reconnect1"):
            await cb.answer("🔄 Qoşulur...")
            me = await tg_session.reconnect(context)
            note = f"✅ Qoşuldu: {who(me)}" if me else "⚠️ Qoşulma alınmadı — logda səbəbə bax"
            await show(cb, *ub1_view(note))
            return
        if act in ("logout", "logout1"):
            await cb.answer()
            await show(cb, "🚪 <b>1️⃣ Əsas userbotdan çıxılsın?</b>\n\nTelegram-da sessiya bağlanır, depo skanı, "
                           "Telegram mənbələri və userbot komandaları hesab yenidən qoşulana qədər işləməyəcək.",
                       InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text=tr("✅ Bəli, çıx"), callback_data="ubacc:logoutok"),
                           InlineKeyboardButton(text=tr("↩️ Xeyr"), callback_data="ubacc:ub1")]]))
            return
        if act == "logoutok":
            await cb.answer("🚪 Çıxılır...")
            err = await tg_session.logout(context)
            note = "🚪 1️⃣ Hesabdan çıxıldı" + (f" <i>({escape(err)})</i>" if err else "")
            await show(cb, *ub1_view(note))
            return
        if act in ("toggle2", "stop2", "start2"):
            if act == "toggle2":
                await cb.answer()
                note = await toggle_ub2()
            elif act == "stop2":
                await cb.answer("🛑 Söndürülür...")
                note = await stop_ub2()
            elif act == "start2":
                await cb.answer("🟢 Qoşulur...")
                note = await start_ub2()
            await show(cb, *ub2_view(note))
            return
        if act == "reconnect2":
            await cb.answer("🔄 Qoşulur...")
            va = getattr(context, "voice_assistant", None)
            if va:
                await va.stop()
                if va.enabled:
                    err = await va.start()
                    note = f"⚠️ {escape(err)}" if err else "✅ 2️⃣ UB2 (Asistan) yenidən qoşuldu"
                else:
                    note = "ℹ️ 2️⃣ UB2 söndürülüb — 🟢 ilə aç"
            else:
                note = "⚠️ Asistan yüklənməyib"
            await show(cb, *ub2_view(note))
            return
        if act == "logout2":
            await cb.answer()
            await show(cb, "🚪 <b>2️⃣ Asistan hesabından (UB2) çıxılsın?</b>\n\nSessiya silinir, qrup səsli yayımlarında "
                           "mahnı oxutmaq üçün hesab yenidən qoşulana qədər işləməyəcək.",
                       InlineKeyboardMarkup(inline_keyboard=[[
                           InlineKeyboardButton(text=tr("✅ Bəli, çıx"), callback_data="ubacc:logout2ok"),
                           InlineKeyboardButton(text=tr("↩️ Xeyr"), callback_data="ubacc:ub2")]]))
            return
        if act == "logout2ok":
            await cb.answer("🚪 Çıxılır...")
            va = getattr(context, "voice_assistant", None)
            if va:
                await va.logout()
                note = "🚪 2️⃣ Asistan hesabından çıxıldı"
            else:
                note = "⚠️ Asistan yüklənməyib"
            await show(cb, *ub2_view(note))
            return
        if act == "cancel":
            await flow.cancel()
            await cb.answer("❌ Ləğv edildi")
            await show(cb, *ub1_view("❌ Giriş ləğv edildi"))
            return
        await cb.answer()
        await show(cb, *hub_view(note))

    @dp.message(F.chat.type == "private", F.text, F.from_user.func(lambda u: flow.waiting(u.id)))
    async def ubacc_login_input(message: types.Message):
        await flow.handle(message)


async def teardown(context):
    task = getattr(context, "telethon_task", None)
    if task and not task.done():
        task.cancel()
    client = getattr(context, "telethon_client", None)
    if client is not None:
        if client.is_connected():
            try:
                await asyncio.wait_for(client.disconnect(), 5)
                logger.info("🛑 Telethon userbot bağlantısı kəsildi.")
            except Exception as e:                       # asılı disconnect dayandırmanı saxlamasın
                logger.warning(f"🛑 Telethon disconnect 5 san.-də bitmədi: {e!r}")
        context.telethon_client = None          # reload-da yeni client yaradılsın, handler-lər ona köçsün
