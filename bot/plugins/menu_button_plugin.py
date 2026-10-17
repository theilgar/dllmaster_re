"""
⌨️ Menyu düymələri — bot/plugins/menu_button_plugin.py   (məntiq: bot/core/menu_button.py)

İstifadəçi (şəxsi çat): /keyboard [off|short|full] (/klaviatura) və /menu → ⌨️ Klaviatura (düymə üç üslubu dövrə ilə dəyişir)
  • ⌨️ qısa — 📋 Menyu · 🔎 Axtar · 🎧 Format · 📚 Pleylistlər · 💎 Premium · 🌐 Dil: müvafiq komandanı işlədir
    (dp.feed_update ilə, ona görə bütün middleware-lər / texniki rejim / dil eyni qalır), düymə mesajını silir.
  • ⌨️ tam — əsas menyunun BÜTÜN düymələri (sahib: 8 bölmə + 🔄 Yenilə / ❓ Bələdçi / ✖️ Bağla; istifadəçi: öz menyusu).
    Düymə = həmin inline düymənin callback-i: plugin "gözləmə" mesajı göndərir, saxta CallbackQuery işlədir, handler həmin
    mesajı həqiqi panelə çevirir (core/menu_button.BridgeMiddleware saxta cavabları Telegram-a buraxmır). Bölmənin içində
    adi inline düymələr işləyir. Standart — bağlı.
Sahib: /menubutton (/komandalar) və /menu → 🖥 Sistem → ⌨️ Menyu düymələri
  • 📋 Telegram-ın komanda siyahısı ("Menu" düyməsi): vəziyyət, 🔄 Tətbiq et, rejim 🤖 Avto / ✋ Manual
  • öz klaviaturası, açan istifadəçi sayı

PRIORITY = 3: düymə mətni mahnı axtarışına və ya mətn gözləyən panellərə (sahibin kanal ID-si yazması və s.) düşməsin.
"""
import asyncio
import contextlib
import logging
import uuid
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity,
                           ReplyKeyboardRemove, Update)

from bot.core import menu_button as mb, panel as ui
from bot.core.panel import btn
from common import i18n
from common.i18n import _
from common.utilities import spawn

logger = logging.getLogger(__name__)

PRIORITY = 3


def setup(context):
    dp, bot = context.dp, context.bot
    is_creator = context.is_creator

    def invite_on() -> bool:
        try:
            from common.database import is_invite_enabled
            return bool(is_invite_enabled())
        except Exception:
            return False

    async def full_markup(uid, user=None):
        """Tam üslub üçün inline əsas menyu: sahib — admin menyusu, qalanlar — şəxsi menyu."""
        from bot.menus.state import get_context
        if is_creator(uid):
            from common.menu import main_kb
            return main_kb(get_context())
        from bot.menus.views.groups import user_menu_view
        user = user or types.User(id=uid, is_bot=False, first_name="")
        return (await user_menu_view(bot, user))[1]

    async def attach_keyboard(chat_id, uid, text=None, user=None):
        """Klaviaturanı bu söhbətə qoşur (Telegram üçün reply_markup bir mesajla gəlməlidir)."""
        if mb.kb_style(uid) == "full":
            kb, mapping = mb.full_keyboard(await full_markup(uid, user), i18n.current())
            await asyncio.to_thread(mb.store_map, uid, mapping)
            text = text or _("⌨️ Tam menyu klaviaturası aktivdir — bütün bölmələr düymələrlə açılır. Söndürmək: /keyboard off")
        else:
            kb = mb.keyboard(i18n.current(), invite_on())
            text = text or _("⌨️ Klaviatura aktivdir. Söndürmək: /keyboard")
        await bot.send_message(chat_id, text, reply_markup=kb)

    async def set_style(chat_id, uid, style: str, user=None):
        await asyncio.to_thread(mb.set_kb_style, uid, style)
        if style == "off":
            await bot.send_message(chat_id, _("⌨️ Klaviatura söndürüldü"), reply_markup=ReplyKeyboardRemove())
        else:
            await attach_keyboard(chat_id, uid, user=user)

    async def refresh_keyboard(chat_id, uid):
        """Dil dəyişəndə düymə yazıları da yeni dildə olsun (lang_plugin çağırır)."""
        if await asyncio.to_thread(mb.kb_on, uid):
            with contextlib.suppress(Exception):
                await attach_keyboard(chat_id, uid, _("⌨️ Klaviatura yeniləndi"))

    context.menu_kb_refresh = refresh_keyboard

    # saxta callback-lər üçün (tam üslub) — sessiya middleware-i bir dəfə qoşulur
    bridge_mw = getattr(context, "_kb_bridge_mw", None)
    if bridge_mw is None:
        bridge_mw = context._kb_bridge_mw = mb.BridgeMiddleware()
        with contextlib.suppress(Exception):
            bot.session.middleware(bridge_mw)

    # ── istifadəçi: /keyboard ──
    @dp.message(Command("keyboard", "klaviatura"))
    async def keyboard_cmd(message: types.Message, command: CommandObject):
        if message.chat.type != "private" or not message.from_user:
            await message.reply(_("⌨️ Klaviatura yalnız şəxsi çatda işləyir"))
            return
        uid = message.from_user.id
        arg = (command.args or "").strip().lower()
        cur = await asyncio.to_thread(mb.kb_style, uid)
        named = {"on": "short", "aç": "short", "ac": "short", "short": "short", "qısa": "short", "qisa": "short",
                 "full": "full", "tam": "full", "off": "off", "bağla": "off", "bagla": "off"}
        style = named.get(arg) or mb.next_style(cur)             # arqumentsiz — off → qısa → tam → off
        await set_style(message.chat.id, uid, style, message.from_user)

    # ── düymə mətnini tanı (hər dildə) ──
    async def label_filter(message: types.Message):
        if message.chat.type != "private" or not message.from_user or not message.text:
            return False
        text, uid = message.text.strip(), message.from_user.id
        if mb.kb_style(uid) == "full":                    # tam üslub: bu istifadəçinin son klaviaturasındakı düymələr
            spec = mb.load_map(uid).get(text)
            if spec:
                return {"kb_spec": spec}
        action = mb.label_map().get(text)
        return {"kb_action": action} if action else False

    async def run_command(message: types.Message, cmd: str):
        fake = message.model_copy(update={"text": cmd, "entities": [
            MessageEntity(type="bot_command", offset=0, length=len(cmd))]}).as_(bot)
        await dp.feed_update(bot, Update(update_id=10 ** 12 + message.message_id, message=fake))

    async def run_callback(message: types.Message, data: str):
        """Tam üslub: inline düymənin callback-ini həmin istifadəçi adından işlət. "⏳" mesajı handler-in redaktə etdiyi
        panelə çevrilir; handler ona toxunmayıbsa (alert / yeni mesaj göndərib) — silinir."""
        chat_id = message.chat.id
        ph = await bot.send_message(chat_id, "⏳")
        cq_id = f"{mb.BRIDGE_PREFIX}{uuid.uuid4().hex[:12]}"
        bridge_mw.register(cq_id, chat_id)
        cq = CallbackQuery(id=cq_id, from_user=message.from_user, chat_instance="kb-bridge", message=ph,
                           data=data).as_(bot)
        try:
            await dp.feed_update(bot, Update(update_id=10 ** 12 + ph.message_id, callback_query=cq))
        except Exception as e:
            logger.warning(f"⌨️ Tam klaviatura: '{data}' işlənmədi: {e}")
        if not bridge_mw.was_touched(chat_id, ph.message_id):
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id, ph.message_id)

    async def run_spec(message: types.Message, spec: dict):
        if "cb" in spec:
            await run_callback(message, spec["cb"])
        elif "url" in spec:
            await message.answer("🔗", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=message.text.strip()[:60], url=spec["url"])]]))
        else:
            kw = {"switch_inline_query": spec["sw"]} if "sw" in spec else {"switch_inline_query_current_chat": spec.get("swc", "")}
            await message.answer(_("🔎 Inline axtarış"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=message.text.strip()[:60], **kw)]]))

    @dp.message(F.text, label_filter)
    async def on_label(message: types.Message, kb_action: str = None, kb_spec: dict = None):
        uid = message.from_user.id
        if kb_spec is None and not await asyncio.to_thread(mb.kb_on, uid):
            # köhnə klaviatura qalıb (söndürülüb) — gizlət, amma əməli yenə də yerinə yetir
            with contextlib.suppress(Exception):
                await message.answer(_("⌨️ Klaviatura bağlıdır — /keyboard ilə yenidən aça bilərsiniz"),
                                     reply_markup=ReplyKeyboardRemove())
        if kb_spec is not None:
            await run_spec(message, kb_spec)
        elif kb_action == "search":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=_("🔎 Inline axtarış"), switch_inline_query_current_chat="")]])
            await message.answer(_("🔎 Mahnı adını yazın, YouTube/Spotify linki göndərin və ya inline axtarışdan istifadə edin."),
                                 reply_markup=kb)
        else:
            await run_command(message, mb.COMMAND_OF[kb_action])
        with contextlib.suppress(Exception):
            await message.delete()                    # düymə mətni çatda yığılmasın (cavab artıq göndərilib)

    # ── sahib paneli ──
    async def panel(uid: int, note: str = ""):
        names = await mb.telegram_state(bot)
        creator_id = getattr(context, "creator_id", 0) or 0
        auto = mb.cmds_mode() == "auto"
        same = names is not None and mb.in_sync(names, creator_id)
        lines = [
            "<i>🏠 › 🖥 Sistem</i>", "⌨️ <b>Menyu düymələri</b>", "━━━━━━━━━━━━━━━━━━",
            "📋 <b>Komanda siyahısı</b> <i>(yazı xəttinin solundakı Menu düyməsi)</i>",
            "   Telegram-da: " + (f"<b>{len(names)}</b> komanda" if names is not None else "<i>oxunmadı</i>"),
            f"   Bizim siyahı: <b>{len(mb.USER_COMMANDS)}</b> istifadəçi + {len(mb.CREATOR_COMMANDS)} admin komandası · "
            f"dillər: {', '.join([i18n.DEFAULT, *mb.lang_codes()])}",
            "   Vəziyyət: " + ("✅ eynidir" if same else "⚠️ fərqlidir — 🔄 Tətbiq et" if names is not None else "❓"),
            "   Rejim: " + ("🤖 <b>Avto</b> — açılışda fərq olsa özü yenilənir" if auto
                            else "✋ <b>Manual</b> — BotFather-dəki siyahıya toxunmur, yalnız 🔄 Tətbiq et ilə"),
            "",
            "⌨️ <b>Klaviatura</b> <i>(həmişə görünən qısayol düymələri, şəxsi çatda)</i>",
            f"   Açan istifadəçi: <b>{sum((users := await asyncio.to_thread(mb.kb_users)).values())}</b> "
            f"(qısa {users['short']} · tam {users['full']}) · sənin klaviaturan: "
            f"{mb.STYLE_LABEL[await asyncio.to_thread(mb.kb_style, uid)]}",
            "   🔴 bağlı (standart) → ⌨️ qısa (6 qısayol) → ⌨️ tam (əsas menyunun bütün düymələri: 8 bölmə + Yenilə / "
            "Bələdçi / Bağla; hər biri həqiqi paneli açır)",
            "<i>İstifadəçilər /keyboard və ya /menu → ⌨️ Klaviatura ilə özləri seçir.</i>"]
        if note:
            lines.append(f"\n{note}")
        rows = [[btn("🔄 Tətbiq et", "mbtn:apply"), ui.mode("Rejim", auto, "mbtn:mode")],
                [btn("👤 İstifadəçi menyusu", "mbtn:um")],
                [btn(f"⌨️ Mənim klaviaturam: {mb.STYLE_LABEL[await asyncio.to_thread(mb.kb_style, uid)]}",
                     "mbtn:kb:panel")],
                ui.nav("menu:sys", "Sistem", close_data="mbtn:close", refresh_data="mbtn:panel")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def um_panel(note: str = ""):
        lines = ["<i>🏠 › 🖥 Sistem › ⌨️ Menyu düymələri</i>", "👤 <b>İstifadəçi menyusu</b> <i>(şəxsi çat, /menu)</i>",
                 "━━━━━━━━━━━━━━━━━━", "Adi istifadəçilərin gördüyü menyuda hansı düymələr olsun:"]
        lines += [f"• {mb.USER_MENU[k]} — {ui.state(mb.um_on(k))}" for k in mb.USER_MENU]
        lines.append("<i>✖️ Bağla həmişə qalır. 🎁 Dəvət düyməsi dəvət sistemi söndürülübsə də görünmür. "
                     "Dəyişiklik dərhal qüvvəyə minir (açıq menyular yenilənəndə).</i>")
        if note:
            lines.append(f"\n{note}")
        rows = ui.grid([ui.toggle(mb.USER_MENU[k], mb.um_on(k), f"mbtn:umt:{k}", compact=True) for k in mb.USER_MENU], 2)
        rows.append([btn("♻️ Hamısını göstər", "mbtn:umall")])
        rows.append(ui.nav("mbtn:panel", "Menyu düymələri", close_data="mbtn:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(Command("menubutton", "komandalar"))
    async def menubutton_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id) or message.chat.type != "private":
            return
        text, kb = await panel(message.from_user.id)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def edit(cb: types.CallbackQuery, text, kb):
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"menubutton paneli yenilənmədi: {e}")

    @dp.callback_query(F.data.startswith("mbtn:"))
    async def mbtn_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act, uid = parts[1], cb.from_user.id
        if act == "kb":                                   # istifadəçi də, sahib də (öz klaviaturası): off → qısa → tam → off
            if cb.message.chat.type != "private":
                await cb.answer(_("⌨️ Klaviatura yalnız şəxsi çatda işləyir"), show_alert=True)
                return
            style = mb.next_style(await asyncio.to_thread(mb.kb_style, uid))
            await cb.answer(mb.STYLE_LABEL[style])
            await set_style(cb.message.chat.id, uid, style, cb.from_user)
            if len(parts) > 2 and parts[2] == "menu":      # istifadəçi menyusunu yenilə (düymənin vəziyyəti)
                from bot.menus.views.groups import user_menu_view
                with contextlib.suppress(Exception):
                    await edit(cb, *await user_menu_view(bot, cb.from_user))
            elif len(parts) > 2 and parts[2] == "panel" and is_creator(uid):
                await edit(cb, *await panel(uid))
            return
        if not is_creator(uid):
            await cb.answer("👑 Yalnız sahib", show_alert=True)
            return
        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        note = ""
        if act in ("um", "umt", "umall"):
            if act == "umt" and len(parts) > 2 and parts[2] in mb.USER_MENU:
                mb.set_um(parts[2], not mb.um_on(parts[2]))
            elif act == "umall":
                for k in mb.USER_MENU:
                    mb.set_um(k, True)
            await cb.answer()
            await edit(cb, *um_panel())
            return
        if act == "apply":
            try:
                res = await mb.apply_commands(bot, getattr(context, "creator_id", 0) or 0)
                note = (f"✅ Komanda siyahısı tətbiq olundu: {res['commands']} + {res['creator']} admin · "
                        f"dillər: {', '.join(res['langs'])}")
                await cb.answer("✅ Tətbiq olundu")
            except Exception as e:
                note = f"❌ <code>{escape(str(e))[:200]}</code>"
                await cb.answer("❌ Alınmadı", show_alert=True)
        elif act == "mode":
            mb.set_cmds_mode("manual" if mb.cmds_mode() == "auto" else "auto")
            await cb.answer("🤖 Avto" if mb.cmds_mode() == "auto" else "✋ Manual")
        else:
            await cb.answer()
        await edit(cb, *await panel(uid, note))

    # ── avto rejim: açılışda fərq varsa siyahını yenilə ──
    async def auto_sync():
        await asyncio.sleep(20)
        try:
            creator_id = getattr(context, "creator_id", 0) or 0
            if mb.cmds_mode() != "auto":
                return
            names = await mb.telegram_state(bot)
            if names is not None and not mb.in_sync(names, creator_id):
                res = await mb.apply_commands(bot, creator_id)
                logger.info(f"⌨️ Komanda siyahısı avtomatik yeniləndi ({res['commands']}+{res['creator']}, "
                            f"dillər: {', '.join(res['langs'])})")
        except Exception as e:
            logger.warning(f"⌨️ Komanda siyahısı yenilənmədi: {e}")

    with contextlib.suppress(RuntimeError):
        spawn(auto_sync())
    logger.info("✅ Menyu düymələri plugin-i yükləndi (/keyboard · /menubutton)")
