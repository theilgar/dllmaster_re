"""
📢 Depo kanalı ayarları — bot/plugins/depo_channel_plugin.py (yalnız creator)

/menu → 📦 Depo → 📢 Depo kanalı
  ✏️ Kanalı dəyiş   — @username, -100... ID, t.me linki və ya kanaldan yönləndirilmiş mesaj.
                      Bot kanalda "mesaj göndərmək" icazəsi olan admin olmalıdır — yoxlanılır.
  🔗 Linki dəyiş    — /start düyməsinin açacağı link (məs. gizli kanal üçün t.me/+... dəvət linki).
                      Boş qalsa avtomatik: @username → t.me/username, gizli kanalda bot dəvət linki yaradır.
  👁 /start-da      — 📦 Depo kanalı düyməsini /start mesajında göstər / gizlət
  🔄 Yoxla          — kanalın əlçatan olub-olmadığını yenidən yoxla

Seçim settings cədvəlində saxlanır (depo:channel) və config.env-dəki DEPO_CHANNEL-dən üstündür.
Kanal dəyişəndə əvvəlki mahnılar keşdə qalır (Telegram file_id kanaldan asılı deyil) — yeni mahnılar yeni kanala gedir.
"""
import contextlib
import logging
import re
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup

from bot.core import audio_cache
from bot.core.input_wait import InputWait, chain_menu_text
from bot.core import panel as ui
from bot.core.panel import btn

logger = logging.getLogger(__name__)

PRIORITY = 73                         # menu (50), logs (70), backup (72)-dən sonra — menu_waiting_text zənciri
WAIT_TTL = 600




def setup(context):
    dp, bot = context.dp, context.bot
    state = InputWait(WAIT_TTL)                                  # await: "chat" | "link"

    is_creator = context.is_creator


    waiting = state.waiting

    chain_menu_text(context, lambda uid: waiting() and is_creator(uid))   # music_plugin bunu axtarış saymasın

    def label() -> str:
        st = audio_cache.depo_status()
        icon = "✅" if st.get("id") else ("⚠️" if st.get("error") else "⏳")
        return f"📢 Depo kanalı · {st.get('ref')} {icon}"

    context.depo_channel_label = label

    # ── görünüş ──
    def panel(note: str = ""):
        st = audio_cache.depo_status()
        pub = audio_cache.depo_public()
        thread_ytm = st.get("thread")
        thread_yt = st.get("thread_yt")

        if st.get("id"):
            status = f"✅ əlçatandır · <code>{st['id']}</code>"
        elif st.get("error"):
            status = f"❌ {escape(st['error'][:150])}"
        else:
            status = "⏳ hələ yoxlanmayıb — 🔄 Yoxla"

        ref_yt = st.get("ref_yt")
        if ref_yt:
            if st.get("id_yt"):
                status_yt = f"✅ əlçatandır · <code>{st['id_yt']}</code>"
            elif st.get("error_yt"):
                status_yt = f"❌ {escape(st['error_yt'][:150])}"
            else:
                status_yt = "⏳ hələ yoxlanmayıb — 🔄 Yoxla"
            title_yt = st.get("title_yt") or ref_yt
            yt_th_str = f" › 🧵 Mövzu #{thread_yt}" if thread_yt else ""
            yt_line = f"▶️ <b>YouTube Deposu:</b> {escape(str(title_yt))} (<code>{escape(ref_yt)}</code>){yt_th_str}\n   🔌 <b>Vəziyyət:</b> {status_yt}"
        elif thread_yt:
            yt_line = f"▶️ <b>YouTube Deposu:</b> Əsas depoda › 🧵 <b>Mövzu #{thread_yt}</b>"
        else:
            yt_line = "▶️ <b>YouTube Deposu:</b> <i>Təyin olunmayıb (əsas depoya yönlənir)</i>"

        link = escape(pub["link"]) if pub["link"] else "<i>yoxdur — 🔗 Linki dəyiş</i>"
        ytm_th_str = f" › 🧵 Mövzu #{thread_ytm}" if thread_ytm else ""
        lines = [
            "<i>🏠 › 📦 Depo</i>",
            "📢 <b>Depo kanalları və mövzular</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"🎵 <b>YT Music Deposu:</b> {escape(str(pub['title']))} (<code>{escape(st['ref'])}</code>){ytm_th_str}",
            f"   🔌 <b>Vəziyyət:</b> {status}",
            yt_line,
            "━━━━━━━━━━━━━━━━━━",
            f"🔗 <b>/start linki:</b> {link}" + (" <i>(əl ilə)</i>" if pub["custom"] else ""),
            "👁 <b>/start düyməsi:</b> " + ("✅ göstərilir" if pub["show"] and pub["link"]
                                           else "❌ gizli" if not pub["show"] else "⚠️ link yoxdur"),
            "━━━━━━━━━━━━━━━━━━",
            "<i>🎵 YT Music mahnıları əsas depoya, ▶️ YouTube videoları/mahnıları YouTube deposuna yüklənir. Forum qruplarında hər ikisi üçün ayrı mövzu (topik) təyin edə bilərsiniz.</i>\n<i>💡 Qrupda/mövzuda birbaşa: /setdepo (YT Music) və /setdepoyt (YouTube)</i>",
        ]
        if note:
            lines.append(f"\n{note}")

        th_ytm_lbl = f"🧵 Topik #{thread_ytm}" if thread_ytm else "🧵 Topik"
        th_yt_lbl = f"🧵 Topik #{thread_yt}" if thread_yt else "🧵 Topik"

        yt_row = [btn("▶️ YouTube kanalı", "dc:chat_yt"), btn(th_yt_lbl, "dc:thread_yt")]
        if ref_yt or thread_yt:
            yt_row.append(btn("🗑 Sıfırla", "dc:clear_yt"))

        rows = [
            [btn("✏️ YT Music kanalı", "dc:chat"), btn(th_ytm_lbl, "dc:thread_ytm")],
            yt_row,
            [btn("🩺 Yoxla", "dc:check"), btn("🔗 Linki dəyiş", "dc:link")] + ([btn("♻️ Avto link", "dc:autolink")] if pub["custom"] else []),
            [ui.toggle("👁 /start-da göstər", pub["show"], "dc:show")],
            ui.nav("menu:depo", "Depo", close_data="dc:close"),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    show = state.show                 # 🧩 InputWait: yenilə + panel kimi yadda saxla

    async def to_panel(message, text, kb):
        await state.to_panel(bot, message, text, kb)

    def reset_helpers():
        pool = getattr(context, "depo_helpers", None)
        with contextlib.suppress(Exception):
            pool._depo["ts"] = 0                        # köməkçi botlar da yeni kanalı götürsün

    # ── düymələr ──
    @dp.callback_query(F.data.startswith("dc:"))
    async def dc_cb(cb: types.CallbackQuery):
        act = cb.data.split(":", 1)[1]
        note = ""
        if act == "close":
            state.clear()
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        if act == "thread_ytm":
            state.start("thread_ytm")
            await cb.answer()
            await show(cb, ("<i>🏠 › 📦 Depo › 📢 Depo kanalı</i>\n🧵 <b>YT Music Deposu üçün Mövzu (Topik)</b>\n\n"
                            "Mövzunu təyin etmək üçün:\n"
                            "• Topik linkini göndər (məs. <code>https://t.me/qrup/5</code>)\n"
                            "• Və ya rəqəmli ID göndər (məs. <code>5</code>)\n"
                            "• Və ya həmin mövzudan istənilən mesajı <b>yönləndir</b>\n\n"
                            "<i>Ləğv etmək üçün <code>0</code> və ya <code>sil</code> göndər.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "dc:panel")]]))
            return
        if act == "thread_yt":
            state.start("thread_yt")
            await cb.answer()
            await show(cb, ("<i>🏠 › 📦 Depo › 📢 Depo kanalı</i>\n🧵 <b>YouTube Deposu üçün Mövzu (Topik)</b>\n\n"
                            "YouTube mahnılarının düşəcəyi mövzunu təyin etmək üçün:\n"
                            "• Topik linkini göndər (məs. <code>https://t.me/qrup/12</code>)\n"
                            "• Və ya rəqəmli ID göndər (məs. <code>12</code>)\n"
                            "• Və ya həmin mövzudan istənilən mesajı <b>yönləndir</b>\n\n"
                            "<i>Ləğv etmək üçün <code>0</code> və ya <code>sil</code> göndər.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "dc:panel")]]))
            return
        if act == "chat":
            state.start("chat")
            await cb.answer()
            await show(cb, ("<i>🏠 › 📦 Depo › 📢 Depo kanalı</i>\n✏️ <b>Yeni YT Music depo kanalı</b>\n\n"
                            "YouTube Music-dən yüklənən mahnıların düşəcəyi kanalı göndər:\n• <code>@kanal</code>\n• <code>-1001234567890</code>\n"
                            "• <code>https://t.me/kanal</code> və ya mövzu linki <code>https://t.me/kanal/5</code>\n• və ya kanaldan/mövzudan istənilən mesajı <b>yönləndir</b>\n\n"
                            "<i>Əvvəl botu həmin kanala \"mesaj göndərmək\" icazəsi ilə admin et.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "dc:panel")]]))
            return
        if act == "chat_yt":
            state.start("chat_yt")
            await cb.answer()
            await show(cb, ("<i>🏠 › 📦 Depo › 📢 Depo kanalı</i>\n▶️ <b>Yeni YouTube depo kanalı</b>\n\n"
                            "YouTube-dan yüklənən mahnıların düşəcəyi ayrıca kanalı göndər:\n• <code>@kanal</code>\n• <code>-1001234567890</code>\n"
                            "• <code>https://t.me/kanal</code> və ya mövzu linki <code>https://t.me/kanal/12</code>\n• və ya kanaldan/mövzudan istənilən mesajı <b>yönləndir</b>\n\n"
                            "<i>Əvvəl botu həmin kanala \"mesaj göndərmək\" icazəsi ilə admin et.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "dc:panel")]]))
            return
        if act == "clear_yt":
            audio_cache.set_depo_yt_channel("")
            audio_cache.set_depo_yt_thread(None)
            await cb.answer("✅ YouTube parametrləri sıfırlandı")
            note = "✅ YouTube kanalı və mövzusu sıfırlandı — YouTube mahnıları da əsas depoya yönləndiriləcək."
        elif act == "link":
            state.start("link")
            await cb.answer()
            await show(cb, ("<i>🏠 › 📦 Depo › 📢 Depo kanalı</i>\n🔗 <b>/start düyməsinin linki</b>\n\n"
                            "Link göndər: <code>https://t.me/kanal</code> və ya gizli kanal üçün "
                            "<code>https://t.me/+AbCd...</code>\n\n"
                            "<i>İstifadəçilər /start-dakı 📦 düyməsi ilə bu linkə keçəcək.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "dc:panel")]]))
            return
        if act == "autolink":
            audio_cache.set_depo_link(None)
            await cb.answer("♻️ Avtomatik link")
        elif act == "show":
            audio_cache.set_depo_show(not audio_cache.depo_public()["show"])
            await cb.answer("👁 " + ("göstərilir" if audio_cache.depo_public()["show"] else "gizlədildi"))
        elif act == "check":
            audio_cache._depo.update(checked=0)
            audio_cache._depo_yt.update(checked=0)
            cid = await audio_cache.depo_chat_id(bot)
            cid_yt = await audio_cache.depo_yt_chat_id(bot) if audio_cache.depo_yt_setting() else None
            msg = "✅ YT Music əlçatandır" if cid else "❌ YT Music əlçatan deyil"
            if audio_cache.depo_yt_setting():
                msg += " · " + ("✅ YouTube əlçatandır" if cid_yt else "❌ YouTube əlçatan deyil")
            await cb.answer(msg, show_alert=not cid or (bool(audio_cache.depo_yt_setting()) and not cid_yt))
        else:                                            # panel
            state.clear()
            await cb.answer()
        await show(cb, *panel(note))

    # ── mətn / yönləndirmə gözləməsi ──
    @dp.message(F.chat.type == "private", state.for_creator(is_creator))
    async def on_input(message: types.Message):
        kind = state["await"]
        text = (message.text or "").strip()

        if kind in ("thread_ytm", "thread_yt"):
            from common.destinations import extract_topic_id, parse_target
            fwd_th = extract_topic_id(message)
            th_id = None
            parsed_chat = None
            if fwd_th:
                th_id = fwd_th
            elif text.lower() in ("0", "sil", "legv", "ləğv", "yoxdur", "none", "hec", "heç", "-"):
                th_id = 0
            else:
                clean_num = re.sub(r"^(?:topic|topik|mövzu|movzu|thread|t|\s|#)+", "", text, flags=re.I).strip()
                if clean_num.isdigit():
                    th_id = int(clean_num)
                else:
                    # Linkdən və ya mətndən topic çıxar (məs. https://t.me/qrup/5 və ya https://t.me/c/12345/5)
                    parsed_chat, parsed_th = parse_target(text)
                    if parsed_th:
                        th_id = parsed_th
                    else:
                        m_th = re.search(r"/(?:c/\d+|[\w_]+)/(\d+)", text)
                        if m_th:
                            th_id = int(m_th.group(1))

            if th_id is None:
                await message.reply(
                    "❌ Mövzu (topik) tapılmadı.\n\n"
                    "Zəhmət olmasa:\n"
                    "• Topik linki göndər (məs. <code>https://t.me/qrup/5</code>)\n"
                    "• Rəqəmli ID göndər (məs. <code>5</code> və ya <code>#5</code>)\n"
                    "• Və ya həmin topikdən bir mesajı <b>yönləndir</b>.\n\n"
                    "<i>Ləğv etmək üçün <code>0</code> yaz.</i>",
                    parse_mode="HTML"
                )
                return

            real_id = th_id if th_id > 0 else None
            extra_chat_note = ""
            if parsed_chat and real_id:
                try:
                    pchat = await bot.get_chat(parsed_chat)
                    pref = f"@{pchat.username}" if getattr(pchat, "username", None) else str(pchat.id)
                    ptitle = getattr(pchat, "title", None) or pref
                    if kind == "thread_yt" and not audio_cache.depo_yt_setting():
                        audio_cache.set_depo_yt_channel(pref, ptitle)
                        await audio_cache.depo_yt_chat_id(bot)
                        extra_chat_note = f"\n▶️ YouTube kanalı: <b>{escape(ptitle)}</b>"
                    elif kind == "thread_ytm" and not audio_cache.depo_setting():
                        audio_cache.set_depo_channel(pref, ptitle)
                        reset_helpers()
                        await audio_cache.depo_chat_id(bot)
                        extra_chat_note = f"\n🎵 YT Music kanalı: <b>{escape(ptitle)}</b>"
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/depo_channel_plugin.py:272]: %r", _e)

            if kind == "thread_ytm":
                audio_cache.set_depo_thread(real_id)
                note = f"✅ YT Music deposu üçün mövzu: <b>#{real_id}</b>{extra_chat_note}" if real_id else "✅ YT Music deposu üçün mövzu ləğv edildi."
            else:
                audio_cache.set_depo_yt_thread(real_id)
                note = f"✅ YouTube deposu üçün mövzu: <b>#{real_id}</b>{extra_chat_note}" if real_id else "✅ YouTube deposu üçün mövzu ləğv edildi."
            state.clear()
            with contextlib.suppress(Exception):
                await message.delete()
            await to_panel(message, *panel(note))
            return

        if kind == "link":
            if not (text.startswith("https://t.me/") or text.startswith("t.me/")):
                await message.reply("❌ <code>https://t.me/...</code> formatında link göndər.", parse_mode="HTML")
                return
            audio_cache.set_depo_link(text if text.startswith("https") else "https://" + text)
            state.clear()
            with contextlib.suppress(Exception):
                await message.delete()
            await to_panel(message, *panel("✅ Link yeniləndi"))
            return

        # kind in ("chat", "chat_yt")
        if kind in ("chat", "chat_yt"):
            from common.destinations import extract_topic_id, parse_target
            fwd_thread = extract_topic_id(message)
            parsed_thread = None
            origin = getattr(message, "forward_origin", None)
            fchat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None) or getattr(message, "forward_from_chat", None)
            target = None
            if fchat:
                target = fchat.id
            elif text.lstrip("-").isdigit():
                target = int(text)
            elif text.startswith("@") or "t.me/" in text:
                target, parsed_thread = parse_target(text)
                if target is None:
                    await message.reply("❌ Dəvət linki (t.me/+...) ilə kanalı tapa bilmirəm — "
                                        "kanaldan bir mesajı <b>yönləndir</b> və ya -100... ID göndər.", parse_mode="HTML")
                    return
            if target is None:
                await message.reply("❌ @username, -100... ID, t.me linki göndər və ya kanaldan mesaj yönləndir.")
                return
            try:
                target_chat_id = int(target) if str(target).lstrip("-").isdigit() else target
                chat = await bot.get_chat(target_chat_id)
                bot_user = await bot.get_me()
                member = await bot.get_chat_member(chat.id, bot_user.id)
            except Exception as e:
                await message.reply(f"❌ Kanal/qrup tapılmadı və ya bot orada deyil: <code>{escape(str(e))[:200]}</code>\n"
                                    "<i>Botu əvvəlcə həmin kanala/qrupa əlavə edin.</i>",
                                    parse_mode="HTML")
                return

            ctype = getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", ""))
            status = getattr(member, "status", "")
            status = getattr(status, "value", status)

            can_post = False
            err_reason = ""

            if status == "creator":
                can_post = True
            elif ctype == "channel":
                if status == "administrator":
                    post_right = getattr(member, "can_post_messages", None)
                    if post_right is False:
                        err_reason = "Botun kanalda <b>\"Mesaj göndərmək\"</b> (Post messages) icazəsi yoxdur."
                    else:
                        can_post = True
                else:
                    err_reason = "Bot bu kanalda <b>administrator</b> deyil."
            else:
                # Qrup / Supergroup / Forum
                if status == "administrator":
                    can_post = True
                elif status == "member":
                    can_post = getattr(member, "can_send_messages", True) is not False
                    if not can_post:
                        err_reason = "Botun bu qrupda mesaj göndərmək hüququ yoxdur."
                elif status == "restricted":
                    can_post = bool(getattr(member, "can_send_messages", False) and getattr(member, "can_send_audios", True))
                    if not can_post:
                        err_reason = "Botun bu qrupda mesaj/audio göndərmək hüququ məhdudlaşdırılıb."
                else:
                    err_reason = f"Bot bu qrupda deyil və ya uyğun statusda deyil (status: {status})."

            if not can_post:
                msg_txt = f"❌ {err_reason}\n\n" if err_reason else "❌ Bot bu çatda mesaj göndərə bilmir.\n\n"
                if ctype == "channel":
                    msg_txt += "Kanal ayarlarına daxil olun, botu <b>Administrator</b> edin və <b>\"Mesaj göndərmək\"</b> icazəsi verin."
                else:
                    msg_txt += "Botu qrupa əlavə edin və mesaj göndərmə icazəsi verin (tövsiyə olunur: admin edin)."
                await message.reply(msg_txt, parse_mode="HTML")
                return

            detected_thread = parsed_thread or fwd_thread
            th_note = f" (Mövzu #{detected_thread})" if detected_thread else ""
            ref = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
            ch_title = getattr(chat, "title", None) or ref
            if kind == "chat_yt":
                audio_cache.set_depo_yt_channel(ref, ch_title)
                if detected_thread:
                    audio_cache.set_depo_yt_thread(detected_thread)
                await audio_cache.depo_yt_chat_id(bot)
                state.clear()
                with contextlib.suppress(Exception):
                    await message.delete()
                logger.info(f"▶️ YouTube Depo kanalı dəyişdi → {ref}{th_note} ({chat.id})")
                await to_panel(message, *panel(f"✅ YouTube Depo kanalı: <b>{escape(ch_title)}</b>{th_note}"))
                return

            link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else None
            if not link:
                with contextlib.suppress(Exception):
                    link = await bot.export_chat_invite_link(chat.id)     # gizli kanal: dəvət linki
            audio_cache.set_depo_channel(ref, ch_title, link)
            if detected_thread:
                audio_cache.set_depo_thread(detected_thread)
            reset_helpers()
            await audio_cache.depo_chat_id(bot)
            state.clear()
            with contextlib.suppress(Exception):
                await message.delete()
            logger.info(f"📢 YT Music Depo kanalı dəyişdi → {ref}{th_note} ({chat.id})")
            await to_panel(message, *panel(f"✅ YT Music Depo kanalı: <b>{escape(ch_title)}</b>{th_note}"
                                           + ("" if link else "\n⚠️ Link alınmadı — 🔗 Linki dəyiş ilə əl ilə ver")))
            return

    # ── /setdepo · /setdepoyt — qrupda / kanalda / mövzuda birbaşa təyin etmək ──
    @dp.message(Command("setdepo", "setdepoyt"))
    async def set_depo_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
        arg = ((message.text or "").split(maxsplit=1)[1:] or [""])[0].strip().lower()
        chat = message.chat
        thread = message.message_thread_id if getattr(message, "is_topic_message", False) or getattr(message, "message_thread_id", None) else None
        title = chat.title or chat.full_name or str(chat.id)
        ref = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)

        if cmd == "setdepoyt":
            if arg in ("off", "söndür", "sil", "0"):
                audio_cache.set_depo_yt_channel("")
                audio_cache.set_depo_yt_thread(None)
                await message.reply("🗑 YouTube depo kanalı və mövzusu sıfırlandı.")
                return
            audio_cache.set_depo_yt_channel(ref, title)
            if thread:
                audio_cache.set_depo_yt_thread(thread)
            await audio_cache.depo_yt_chat_id(bot)
            th_str = f" (Mövzu #{thread})" if thread else ""
            await message.reply(f"✅ <b>YouTube Deposu təyin edildi:</b> {escape(title)}{th_str}\n"
                                f"<i>Sıfırlamaq: /setdepoyt off</i>", parse_mode="HTML")
            logger.info(f"▶️ YouTube Depo təyin edildi: {ref}{th_str} ({chat.id})")
        else:
            if arg in ("off", "söndür", "sil", "0"):
                audio_cache.set_depo_channel("")
                audio_cache.set_depo_thread(None)
                await message.reply("🗑 YT Music depo kanalı və mövzusu sıfırlandı.")
                return
            link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else None
            if not link:
                with contextlib.suppress(Exception):
                    link = await bot.export_chat_invite_link(chat.id)
            audio_cache.set_depo_channel(ref, title, link)
            if thread:
                audio_cache.set_depo_thread(thread)
            reset_helpers()
            await audio_cache.depo_chat_id(bot)
            th_str = f" (Mövzu #{thread})" if thread else ""
            await message.reply(f"✅ <b>YT Music Deposu təyin edildi:</b> {escape(title)}{th_str}\n"
                                f"<i>Sıfırlamaq: /setdepo off</i>", parse_mode="HTML")
            logger.info(f"📢 YT Music Depo təyin edildi: {ref}{th_str} ({chat.id})")

    logger.info(f"✅ Depo kanalı plugin-i yükləndi — {audio_cache.depo_setting()}")
