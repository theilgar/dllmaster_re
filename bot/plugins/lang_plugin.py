"""
🌐 Dil seçimi — bot/plugins/lang_plugin.py

İstifadəçi:
  • İlk /start — əvvəl dil seçimi, salam mesajı seçilən dildə (start_plugin bunu çağırır)
  • Dil seçməmiş köhnə istifadəçi — bota ilk yazdığında BİR DƏFƏ seçim təklif olunur (mesajı da işlənir)
  • /lang (/language, /dil, /язык) və /start-dakı 🌐 düyməsi — istənilən vaxt dəyişmək
Admin: /menu → 🎨 Mesajlar → 🌐 Dillər
  • dillər, tərcümə faizi, fayl xətaları · default dil · avtomatik təyin
  • ➕ Dil faylı yüklə (.json) — yeni fayl = yeni dil, restart yoxdur
  • 📤 Şablon (tərcüməçi üçün boş fayl) · 📤 mövcud dilin faylı · 🗑 sil · 🔁 yenidən oxu
  • 📢 Seçim etməyənlərə dil sorğusu göndər
Fayl formatı: common/i18n.py başlığına bax.
"""
import asyncio
import contextlib
import json
import logging
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from common import i18n
from common.database import get_db
from common.i18n import _
from bot.core.input_wait import InputWait, chain_menu_text
from bot.core import panel as ui
from bot.core.panel import btn, show as panel_show
from common.utilities import spawn
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 15                      # music_plugin-dən əvvəl — /lang axtarış sayılmasın
WAIT_TTL = 600
ASK_KEY = "lang_asked:{}"




def lang_keyboard(uid, prefix: str = "set") -> InlineKeyboardMarkup:
    cur = i18n.get_lang(uid) if i18n.chosen(uid) else None
    items = [btn(("✅ " if code == cur else "") + label, f"lang:{prefix}:{code}") for code, label in i18n.LANGS.items()]
    return InlineKeyboardMarkup(inline_keyboard=[items[i:i + 2] for i in range(0, len(items), 2)])


def setup(context):
    dp, bot = context.dp, context.bot
    state = InputWait(WAIT_TTL, bcast=None)

    is_creator = context.is_creator


    waiting = state.waiting

    chain_menu_text(context, lambda uid: waiting() and is_creator(uid))

    async def send_picker(chat_id, uid, first: bool):
        """first=True — ilk dəfə: başlıq bütün dillərdə (istifadəçi hələ heç birini seçməyib)."""
        await bot.send_message(chat_id, i18n.picker_title() if first else _("🌐 Dil seçin:"),
                               reply_markup=lang_keyboard(uid, "first" if first else "set"))

    async def ask_first(message: types.Message) -> bool:
        """start_plugin: dil seçilməyibsə seçimi göstər və True qaytar (salam mesajı seçimdən sonra)."""
        u = message.from_user
        if not u or i18n.chosen(u.id):
            return False
        await asyncio.to_thread(get_db().set_setting, ASK_KEY.format(u.id), "1")   # hər yeni /start — loop-u tutmasın
        await send_picker(message.chat.id, u.id, first=True)
        return True

    context.lang_ask_first = ask_first

    # ── köhnə istifadəçilər: ilk mesajda bir dəfə ──
    async def old_user_mw(handler, event: types.Message, data):
        u = event.from_user
        if (u and not u.is_bot and event.chat.type == "private" and not i18n.chosen(u.id)
                and not get_db().get_setting(ASK_KEY.format(u.id))
                and not (event.text or "").startswith(("/start", "/lang", "/language", "/dil"))):
            await asyncio.to_thread(get_db().set_setting, ASK_KEY.format(u.id), "1")
            with contextlib.suppress(Exception):
                await send_picker(event.chat.id, u.id, first=True)
        return await handler(event, data)

    dp.message.outer_middleware(old_user_mw)

    @dp.message(Command("lang", "language", "dil", "язык"))
    async def lang_cmd(message: types.Message):
        if message.from_user:
            await send_picker(message.chat.id, message.from_user.id, first=False)

    @dp.callback_query(F.data.startswith("lang:"))
    async def lang_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        if parts[1] == "menu":
            await cb.answer()
            await send_picker(cb.message.chat.id, cb.from_user.id, first=False)
            return
        if parts[1] in ("set", "first") and len(parts) > 2 and parts[2] in i18n.LANGS:
            code = parts[2]
            i18n.set_lang(cb.from_user.id, code)
            await cb.answer(_("✅ Dil dəyişdirildi: {name}", code, name=i18n.LANGS[code]))
            with contextlib.suppress(Exception):
                await cb.message.delete()
            show = getattr(context, "show_start", None)
            if show and cb.message.chat.type == "private":
                tok = i18n.use(code)                  # salam mesajı YENİ dildə
                try:
                    await show(cb.message.chat.id, cb.from_user)
                except Exception as e:
                    logger.debug(f"start göstərilmədi: {e}")
                finally:
                    i18n.reset(tok)
            return
        await cb.answer()

    # ── admin paneli ──
    def unchosen_users() -> list:
        try:
            ids = [r[0] for r in get_db().sql_all("SELECT user_id FROM users WHERE blocked=0")]
        except Exception:
            return []
        return [u for u in ids if not i18n.chosen(u)]

    def panel(note: str = ""):
        st = i18n.stats()
        uc = len(unchosen_users())
        lines = ["<i>🏠 › 🎨 Mesajlar</i>", "🌐 <b>Dillər</b>", "━━━━━━━━━━━━━━━━━━"]
        for code, label in i18n.LANGS.items():
            r = i18n.REPORT.get(code, {})
            src = "əsas" if code == "az" else (f"📁 {r.get('file')}" if r.get("file") else "daxili")
            warn = f" · ⚠️ {len(r.get('skipped', []))} sətir atlandı" if r.get("skipped") else ""
            lines.append(f"{label} — <b>{i18n.coverage(code)}%</b> · {src} · seçən: {st.get(code, 0)}{warn}")
        for c, r in i18n.REPORT.items():
            if r.get("error"):
                lines.append(f"❌ <code>{escape(str(r.get('file')))}</code>: {escape(r['error'])}")
        lines += ["━━━━━━━━━━━━━━━━━━",
                  f"Default: <b>{i18n.LANGS.get(i18n.default_lang())}</b> · avtomatik təyin: "
                  f"<b>{'✅' if i18n.auto_detect() else '❌'}</b>",
                  f"🙋 Dil seçməyən istifadəçi: <b>{uc}</b> <i>(ilk mesajlarında bir dəfə soruşulur)</i>",
                  "",
                  "<i>📁 Yeni dil: 📤 Şablon faylını götür → tərcümə et → ➕ ilə geri göndər. "
                  "Boş qalan sətirlər Azərbaycanca göstərilir. Admin paneli Azərbaycanca qalır.</i>"]
        if state["bcast"]:
            b = state["bcast"]
            lines.append(f"\n📢 Sorğu: {b['sent']}/{b['total']} göndərildi · ❌ {b['fail']}"
                         + (" · ✅ bitdi" if b.get("done") else " · ⏳ gedir"))
        if note:
            lines.append(f"\n{note}")
        dl, lkeys = i18n.default_lang(), list(i18n.LANGS)
        if 1 < len(lkeys) <= 4:                                     # az/az + 1-3 dil: tək dövr düyməsi
            nl = next_of(lkeys, dl)
            lang_row = [btn(f"Defolt: {i18n.LANGS.get(dl, dl)}  ›  {i18n.LANGS[nl]}", f"ln:def:{nl}")]
        else:                                                       # çox dil — birbaşa seçim qalır
            lang_row = [ui.choice(c, dl == c, f"ln:def:{c}") for c in i18n.LANGS]
        rows = [lang_row,
                [ui.toggle("🔄 Avto-təyin", i18n.auto_detect(), "ln:auto", compact=True),
                 btn("🔁 Faylları yenidən oxu", "ln:reload")],
                [btn("➕ Dil faylı yüklə", "ln:add"), btn("📤 Şablon", "ln:tpl")],
                [btn(f"📤 {c}", f"ln:exp:{c}") for c in i18n.LANGS if c != "az"][:6]]
        removable = [c for c, r in i18n.REPORT.items() if r.get("file") and c not in i18n.BUILTIN_NAMES]
        if removable:
            rows.append([btn(f"🗑 {c}", f"ln:del:{c}") for c in removable][:6])
        if uc and not (state["bcast"] and not state["bcast"].get("done")):
            rows.append([btn(f"📢 Seçməyənlərə sorğu ({uc})", "ln:ask")])
        rows.append(ui.nav("menu:ms", "Şablonlar"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


    show = panel_show

    async def send_json(chat_id, data: dict, filename: str, caption: str):
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        await bot.send_document(chat_id, BufferedInputFile(raw, filename=filename), caption=caption, parse_mode="HTML")

    async def broadcast(chat_id, msg_id):
        users = unchosen_users()
        b = state["bcast"] = {"total": len(users), "sent": 0, "fail": 0, "done": False}
        for i, uid in enumerate(users):
            try:
                await bot.send_message(uid, i18n.picker_title(), reply_markup=lang_keyboard(uid, "first"))
                await asyncio.to_thread(get_db().set_setting, ASK_KEY.format(uid), "1")
                b["sent"] += 1
            except Exception as e:
                b["fail"] += 1
                if "retry after" in str(e).lower():
                    await asyncio.sleep(5)
            await asyncio.sleep(0.05)                 # ~20/san — Telegram limiti
            if i % 50 == 0:
                with contextlib.suppress(Exception):
                    text, kb = panel()
                    await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, parse_mode="HTML",
                                                reply_markup=kb)
        b["done"] = True
        logger.info(f"🌐 Dil sorğusu: {b['sent']}/{b['total']} göndərildi, {b['fail']} alınmadı")
        with contextlib.suppress(Exception):
            text, kb = panel()
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("ln:"))
    async def ln_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act, arg = parts[1], (parts[2] if len(parts) > 2 else "")
        note = ""
        if act == "def" and arg in i18n.LANGS:
            get_db().set_setting("i18n:default", arg)
            await cb.answer("✅")
        elif act == "auto":
            get_db().set_setting("i18n:auto", "0" if i18n.auto_detect() else "1")
            await cb.answer("🔄")
        elif act == "reload":
            rep = await asyncio.to_thread(i18n.reload)
            await cb.answer(f"🔁 {len(i18n.LANGS)} dil")
            errs = [f"{c}: {r['error']}" for c, r in rep.items() if r.get("error")]
            note = "🔁 Yenidən oxundu" + (("\n❌ " + "\n❌ ".join(escape(e) for e in errs)) if errs else "")
        elif act == "add":
            state.start("file")
            await cb.answer()
            await show(cb, "➕ <b>Dil faylı</b>\n\n<code>.json</code> faylını göndər (məs. <code>de.json</code>).\n"
                           "<i>Eyni kodlu dil artıq varsa, yenisi ilə əvəz olunur.</i>",
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "ln:panel")]]))
            return
        elif act == "tpl":
            await cb.answer("📤")
            try:
                from bot.plugins.start_plugin import DEFAULT_TEXTS
            except Exception:
                DEFAULT_TEXTS = {}
            await send_json(cb.message.chat.id, i18n.export_template(DEFAULT_TEXTS), "lang_template.json",
                            "📤 <b>Tərcümə şablonu</b>\n"
                            f"{len(i18n.source_keys())} mətn + {len(DEFAULT_TEXTS)} bələdçi.\n"
                            "1) <code>_meta.code</code> və <code>name</code>-i yaz (məs. de, 🇩🇪 Deutsch)\n"
                            "2) <code>strings</code> və <code>templates</code> dəyərlərini tərcümə et\n"
                            "3) <code>{yer_tutucular}</code>-a toxunma\n"
                            "4) faylı ➕ Dil faylı yüklə ilə göndər")
            return
        elif act == "exp" and arg in i18n.LANGS:
            await cb.answer("📤")
            await send_json(cb.message.chat.id, i18n.export_lang(arg), f"{arg}.json",
                            f"📤 {escape(i18n.LANGS[arg])} — {i18n.coverage(arg)}%\n"
                            "<i>Düzəldib ➕ ilə geri göndərə bilərsən.</i>")
            return
        elif act == "del" and arg:
            ok = await asyncio.to_thread(i18n.remove, arg)
            await cb.answer("🗑" if ok else "Tapılmadı")
            note = f"🗑 {escape(arg)} silindi" if ok else ""
        elif act == "ask":
            if state["bcast"] and not state["bcast"].get("done"):
                await cb.answer("⏳ Artıq gedir", show_alert=True)
                return
            await cb.answer("📢 Göndərilir...")
            spawn(broadcast(cb.message.chat.id, cb.message.message_id))
            await asyncio.sleep(0.2)
        else:
            state.clear()
            await cb.answer()
        await show(cb, *panel(note))

    async def is_waiting_file(message: types.Message) -> bool:
        return (bool(message.from_user) and is_creator(message.from_user.id) and waiting()
                and bool(message.document))

    @dp.message(F.chat.type == "private", is_waiting_file)
    async def on_file(message: types.Message):
        doc = message.document
        if not (doc.file_name or "").lower().endswith(".json"):
            await message.reply("❌ <code>.json</code> faylı göndər", parse_mode="HTML")
            return
        if (doc.file_size or 0) > 2 * 1024 * 1024:
            await message.reply("❌ Fayl çox böyükdür (2 MB-dan çox)")
            return
        try:
            buf = await bot.download(doc)
            code, rep = await asyncio.to_thread(i18n.install, buf.read(), doc.file_name)
        except Exception as e:
            await message.reply(f"❌ Fayl qəbul edilmədi: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            return
        state.clear()
        skipped = rep.get("skipped") or []
        note = (f"✅ <b>{escape(i18n.LANGS.get(code, code))}</b> əlavə olundu — {i18n.coverage(code)}% "
                f"({rep.get('strings', 0)} mətn, {rep.get('templates', 0)} bələdçi)\n"
                "<i>Artıq /lang siyahısındadır, restart lazım deyil.</i>"
                + ("\n⚠️ Atlanan sətirlər:\n" + "\n".join(f"• {escape(x)}" for x in skipped[:8]) if skipped else ""))
        logger.info(f"🌐 Dil faylı əlavə olundu: {code} ({i18n.coverage(code)}%)")
        text, kb = panel(note)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    logger.info("✅ Dil dəstəyi yükləndi: " + ", ".join(f"{c} {i18n.coverage(c)}%" for c in i18n.LANGS))
