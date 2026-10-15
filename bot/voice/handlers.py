"""
🎮 bot/voice/handlers.py - Səs asistanı komandaları, panellər və callback handler-ləri.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from bot.core import panel as ui, va_record
from bot.core.panel import show as panel_show
from bot.core.links import _YOUTUBE_VID_RE as _VID_RE
from bot.voice.calls_adapter import Calls, pytgcalls_version
from bot.voice.engine import Assistant
from bot.voice.models import (
    DM_CD_STEPS,
    DM_TEXT,
    MAX_DURATION,
    MAX_SLOTS,
    MAXPER_STEPS,
    MIX_STEPS,
    NotMember,
    QUALITY,
    S_DEPO,
    S_DM_CD,
    S_DM_ON,
    S_MAXPER,
    S_MIXAUTO,
    S_MIXN,
    S_NORM,
    S_ON,
    S_QUAL,
    S_SRC,
    S_THROTTLE,
    S_VOL,
    S_WHO,
    SLOT_ICONS,
    SOURCES,
    VOL_STEPS,
    WHO,
    ChatState,
    Slot,
    Track,
    _set,
    extra_session_path,
)
from common.i18n import _ as tr
from common.utilities import format_duration as fmt_dur, spawn, ui_styled
from userbot.core import tg_login
from common.kv import next_of

logger = logging.getLogger(__name__)

va = None

def setup(context):
    global va
    dp, bot = context.dp, context.bot
    va = Assistant(context)
    old = getattr(context, "voice_assistant", None)
    context.voice_assistant = va

    is_creator = context.is_creator


    # music_plugin-in ümumi mətn handler-i giriş məlumatını axtarış saymasın (hər iki hesabın girişi)
    context.va_waiting = tg_login.any_waiting

    _admin_cache = {}  # (chat_id, user_id) -> (is_admin, expires_at)

    async def can_control(chat_id, user_id) -> bool:
        if is_creator(user_id) or va.who == "all":
            return True
        now = time.time()
        cached = _admin_cache.get((chat_id, user_id))
        if cached and cached[1] > now:
            return cached[0]
        try:
            m = await bot.get_chat_member(chat_id, user_id)
            status = getattr(m, "status", None)
            is_adm = status in ("administrator", "creator") or str(getattr(status, "value", status)) in ("administrator", "creator")
            _admin_cache[(chat_id, user_id)] = (is_adm, now + 60.0)
            return is_adm
        except Exception:
            return False

    # ── menyu paneli ──
    def panel_view(note: str = ""):
        ver = pytgcalls_version()
        if not ver:
            state = "❌ py-tgcalls quraşdırılmayıb — <code>pip install py-tgcalls</code>"
        elif not va.has_session():
            state = "⚠️ Hesab qoşulmayıb — 🔑 düyməsi ilə qoş"
        elif not va.enabled:
            state = "🔴 Söndürülüb"
        elif va.running:
            state = "🟢 İşləyir"
        else:
            state = f"⚠️ İşləmir: {escape(va.error or va.status)}"
        me = va.me
        acc = (f"{escape(me.first_name or '')} (@{escape(me.username)})" if me and me.username
               else escape(me.first_name or "") if me else ("qoşulub (aktiv deyil)" if va.has_session() else "—"))
        lines = ["🎙 <b>2️⃣ Canlı yayım asistanı</b> — <i>yalnız səs</i>\n━━━━━━━━━━━━━━━━━━",
                 f"<b>Vəziyyət:</b> {state}",
                 f"👤 <b>Hesab:</b> {acc}"]

        is_active = bool(va.has_session() and va.enabled and va.running)

        if is_active:
            lines.extend([
                f"🔎 <b>Axtarış:</b> {SOURCES.get(va.source, va.source)}",
                f"📦 <b>Əvvəl depo (adla):</b> {'✅' if va.depo_first else '❌'}",
                f"🎛 <b>İdarə edə bilər:</b> {WHO[va.who]}",
                f"🧩 <b>py-tgcalls:</b> {ver or '—'}",
                f"🔀 <b>/mixplay:</b> {va.mix_n} mahnı · ♾ davam: {'✅' if va.mix_auto else '❌'}",
            ])
            # 👥 asistan hesabları (çoxlu yayım)
            acc_lines = [f"\n👥 <b>Asistan hesabları: {len(va.slots)}</b> · limit: "
                         + (f"{va.max_per} yayım/hesab" if va.max_per else "limitsiz")]
            for sl_ in va.slots:
                stt = ("🟢" if sl_.running else ("⚠️ " + escape(sl_.error[:60]) if sl_.error else "⚪️"))
                role = f"🔊 {va.load(sl_)} yayım" if sl_.voice_enabled else "📦 Yalnız Depo (qruplara və səsə girmir)"
                acc_lines.append(f"{sl_.name()} — {stt} · {role}")
            acc_lines.append("<i>📦 Yalnız Depo rejimində olan hesablar qruplara və yayımlara qətiyyən qoşulmur, arxa planda işləyir.</i>")
            lines.append("\n".join(acc_lines))
            lines.append(f"🎧 <b>Keyfiyyət:</b> {QUALITY[va.quality]} · 🔊 normallaşdırma: "
                         f"{'✅' if va.normalize else '❌'} · səviyyə {va.volume}%"
                         + (" ⚠️" if va.volume > 100 else "")
                         + f"\n⚙️ <b>Yayımda deponu yavaşlat:</b> {'✅' if va.throttle else '❌'}")
            dms = getattr(va, "dm_stats", None) or {}
            lines.append("💬 <b>Şəxsi mesajlara cavab:</b> " + (
                f"✅ botu başlat düyməsi ilə · eyni adama {va.dm_cd} saatda bir" if va.dm_on and va.dm_cd
                else "✅ hər mesaja" if va.dm_on else "❌ söndürülüb")
                + (f" · göndərilib: {dms.get('replied', 0)}" if dms.get("replied") else ""))
            lines.append("━━━━━━━━━━━━━━━━━━")
            active = [s for s in va.chats.values() if s.current or s.queue]
            if active:
                lines.append(f"🔊 <b>Aktiv yayımlar: {len(active)}</b>")
                for s in active[:8]:
                    cur = s.current.title[:40] if s.current else "—"
                    sl_ = va.bind.get(s.chat_id)
                    lines.append(f"• {sl_.icon + ' ' if sl_ and len(va.slots) > 1 else ''}"
                                 f"{escape(s.title or str(s.chat_id))[:30]} — {'⏸' if s.paused else '▶️'} "
                                 f"{escape(cur)}" + (f" <i>(+{len(s.queue)})</i>" if s.queue else ""))
            else:
                lines.append("🔇 <i>Aktiv yayım yoxdur</i>")
            lines.append("\n<i>Qrupda: /play mahnı adı · /mixplay · /pause · /resume · /skip · /stop · /queue · /clearqueue</i>")
        else:
            if va.has_session():
                lines.append("\n<i>Asistan söndürülüb. Tənzimləmələri görmək və aktivləşdirmək üçün 🟢 düyməsinə klikləyin.</i>")
            else:
                lines.append("\n<i>Qrup səsli yayımlarında /play ilə mahnı oxutmaq üçün asistan hesabını qoşun.</i>")

        if note:
            lines.append(f"\n{note}")

        rows = []
        if va.has_session():
            rows.append([ui.toggle("🎙 Asistan", is_active, "va:toggle")])
            if is_active:
                src_order = list(SOURCES.keys())
                next_src = next_of(src_order, va.source, "ytm")
                src_btn = InlineKeyboardButton(text=f"🔎 {SOURCES.get(va.source, SOURCES['ytm'])}", callback_data=f"va:src:{next_src}")
                next_q = "best" if va.quality == "depo" else "depo"
                q_label = "💎 Ən yaxşı" if va.quality == "best" else "⚡ Depo"
                q_btn = InlineKeyboardButton(text=f"🎧 {q_label}", callback_data=f"va:q:{next_q}")
                rows.append([src_btn, q_btn])
                rows.append([ui.toggle("📦 Depo əvvəl", va.depo_first, "va:depo", compact=True),
                             InlineKeyboardButton(text=WHO[va.who], callback_data="va:who")])
                rows.append([ui.btn(f"🔀 Mix: {va.mix_n} mahnı", "va:mixn"),
                             ui.toggle("♾ Davam", va.mix_auto, "va:mixauto", compact=True)])
                rows.append([ui.toggle("🔊 Norm", va.normalize, "va:norm", compact=True),
                             ui.btn(f"🔉 Səs: {va.volume}%", "va:vol"),
                             ui.btn(f"⚙️ Depo: {'🐢' if va.throttle else '🚀'}", "va:throttle")])
                rows.append([ui.toggle("💬 DM cavab", va.dm_on, "va:dm", compact=True),
                             ui.btn(f"⏱ {('hər dəfə' if not va.dm_cd else str(va.dm_cd) + ' saat')}", "va:dmcd")])
                acc_row = []
                if len(va.slots) < MAX_SLOTS:
                    acc_row.append(InlineKeyboardButton(text="➕ Asistan əlavə et", callback_data="va:add"))
                acc_row.append(InlineKeyboardButton(
                    text=f"📊 Limit: {va.max_per if va.max_per else '∞'}/hesab", callback_data="va:maxper"))
                rows.append(acc_row)
                for sl_ in va.slots[1:]:
                    v_title = "🎙 Səs: ✅" if sl_.voice_enabled else "📦 Yalnız Depo"
                    rows.append([
                        InlineKeyboardButton(text=f"{sl_.icon} {v_title}", callback_data=f"va:vtoggle:{sl_.idx}"),
                        InlineKeyboardButton(text=f"🚪 {sl_.icon} çıxar", callback_data=f"va:rm:{sl_.idx}")
                    ])
                if len(va.slots) == 1:
                    sl0 = va.slots[0]
                    v_title = "🎙 Səs: ✅" if sl0.voice_enabled else "📦 Yalnız Depo"
                    rows.append([
                        InlineKeyboardButton(text=f"{sl0.icon} {v_title}", callback_data="va:vtoggle:0")
                    ])
                rows.append([InlineKeyboardButton(text="🔄 Yenidən qoşul", callback_data="va:reconnect"),
                             InlineKeyboardButton(text="🔁 Hesabı dəyiş", callback_data="va:login")])
                rows.append([InlineKeyboardButton(text="🚪 Hesabdan çıx", callback_data="va:logout"),
                             ui.btn("⏺ Yayım qeydi", "vrc:0:list")])
                active = [s for s in va.chats.values() if s.current or s.queue]
                if active:
                    rows.append([InlineKeyboardButton(text="⏹ Bütün yayımları bitir", callback_data="va:stopall")])
        else:
            rows.append([InlineKeyboardButton(text="🔑 Hesab qoş", callback_data="va:login")])

        rows.append(ui.nav("menu:live", "Canlı yayım", refresh_data="va:panel"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    va.panel_view = panel_view

    login_target = {"idx": 0}

    async def on_login_done(session_str, me, client):
        main_me = getattr(context, "telethon_me", None)
        if main_me and main_me.id == me.id:
            with contextlib.suppress(Exception):
                await client.log_out()
            return panel_view("❌ <b>Bu hesab 1️⃣ əsas userbotdur.</b> Asistan üçün ayrıca hesab qoş — eyni sessiyanı "
                              "iki client açanda Telegram birini atır.")
        idx = login_target["idx"]
        dup = next((x for x in va.slots if x.idx != idx and x.me and x.me.id == me.id), None)
        if dup:
            with contextlib.suppress(Exception):
                await client.disconnect()
            return panel_view(f"❌ <b>Bu hesab artıq {dup.icon} asistan kimi qoşulub.</b> Başqa hesab seç.")
        if idx > 0:                                       # ➕ əlavə asistan — digər yayımlara toxunulmur
            sl_ = next((x for x in va.slots if x.idx == idx), None)
            if sl_ is None:
                sl_ = Slot(idx, extra_session_path(idx))
                va.slots.append(sl_)
                va.slots.sort(key=lambda x: x.idx)
            await va._close_slot(sl_)
            sl_.write_session(session_str)
            sl_.me = me
            with contextlib.suppress(Exception):
                await client.disconnect()
            err = await va._start_slot(sl_) if va.enabled else ""
            rejim = "📦 Yalnız Depo (qruplara və səsə girməyəcək)" if not sl_.voice_enabled else "🎙 Səsli yayım"
            return panel_view(f"✅ <b>{sl_.icon} userbot qoşuldu:</b> {escape(me.first_name or '')}"
                              + (f" (@{escape(me.username)})" if me.username else "")
                              + f"\n⚙️ <b>Rejim:</b> {rejim}"
                              + (f"\n⚠️ {escape(err)}" if err else "")
                              + "\n<i>İstədiyiniz vaxt menyudan rejimini dəyişə bilərsiniz.</i>")
        await va.stop()
        va._write_session(session_str)
        va.me = me
        _set(S_ON, "1")
        err = await va.start()
        return panel_view(f"✅ <b>2️⃣ Asistan qoşuldu:</b> {escape(me.first_name or '')}"
                          + (f" (@{escape(me.username)})" if me.username else "")
                          + (f"\n⚠️ {escape(err)}" if err else ""))

    flow = tg_login.LoginFlow(
        "assistant", context, "2️⃣ Canlı yayım asistanı hesabını qoş",
        "Ayrıca hesab olmalıdır (1️⃣ əsas userbot yox). Bu hesab yalnız səsli yayımda mahnı oxudacaq.",
        on_login_done, "va:logincancel")

    show = panel_show

    @dp.callback_query(F.data.startswith("va:"))
    async def va_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1] if len(parts) > 1 else "panel"
        note = ""
        if act == "toggle":
            if va.enabled:
                _set(S_ON, "0")
                await cb.answer("🔴 Söndürülür...")
                await va.stop(kb=await contact_kb())     # aktiv qruplarda "adminlə əlaqə" düyməsi
                note = "🔴 Asistan söndürüldü"
            else:
                _set(S_ON, "1")
                await cb.answer("🟢 Başladılır...")
                err = await va.start()
                note = f"⚠️ {escape(err)}" if err else "🟢 Asistan işləyir"
        elif act == "src" and len(parts) > 2 and parts[2] in SOURCES:
            _set(S_SRC, parts[2])
            await cb.answer(SOURCES[parts[2]])
        elif act == "depo":
            _set(S_DEPO, "0" if va.depo_first else "1")
            await cb.answer("📦 Depo əvvəl: " + ("✅" if va.depo_first else "❌"))
        elif act == "who":
            _set(S_WHO, "admins" if va.who == "all" else "all")
            await cb.answer(WHO[va.who])
        elif act == "mixn":
            nxt = next_of(MIX_STEPS, va.mix_n)
            _set(S_MIXN, nxt)
            await cb.answer(f"🔀 /mixplay: {nxt} mahnı (bir partiya)")
        elif act == "q" and len(parts) > 2 and parts[2] in QUALITY:
            _set(S_QUAL, parts[2])
            await cb.answer(QUALITY[parts[2]] + (" — növbəti mahnıdan" if va.chats else ""))
        elif act == "norm":
            _set(S_NORM, "0" if va.normalize else "1")
            await cb.answer("🔊 Normallaşdırma " + ("açıq — bütün mahnılar eyni səviyyədə" if va.normalize else "söndürüldü"))
        elif act == "vol":
            nxt = next_of(VOL_STEPS, va.volume)
            _set(S_VOL, nxt)
            for cid, stt in va.chats.items():
                if stt.current:
                    await va.calls_for(cid).volume(cid, nxt)
            await cb.answer(f"🔉 {nxt}%" + (" — 100%-dən yuxarı səs təhrif ola bilər" if nxt > 100 else ""),
                            show_alert=nxt > 100)
        elif act == "throttle":
            _set(S_THROTTLE, "0" if va.throttle else "1")
            va.apply_throttle()
            await cb.answer("⚙️ Yayımda depo prosesləri yarıya enir" if va.throttle else "⚙️ Depo tam sürətdə")
        elif act == "dm":
            _set(S_DM_ON, "0" if va.dm_on else "1")
            await cb.answer("💬 Şəxsi mesajlara avto-cavab " + ("açıldı" if va.dm_on else "söndürüldü"))
        elif act == "dmcd":
            nxt = next_of(DM_CD_STEPS, va.dm_cd)
            _set(S_DM_CD, nxt)
            await cb.answer("⏱ " + ("hər mesaja cavab" if not nxt else f"eyni adama {nxt} saatda bir"))
        elif act == "mixauto":
            _set(S_MIXAUTO, "0" if va.mix_auto else "1")
            await cb.answer("♾ Növbə bitəndə Mix davam edir" if va.mix_auto else "♾ Davam söndürüldü")
        elif act == "reconnect":
            await cb.answer("🔄 Yenidən qoşulur...")
            await va.stop()
            if va.enabled:
                err = await va.start()
                note = f"⚠️ {escape(err)}" if err else "✅ Yenidən qoşuldu"
            else:
                note = "ℹ️ Asistan söndürülüb — 🟢 ilə aç"
        elif act == "logout":
            await cb.answer()
            await show(cb, "🚪 <b>Asistan hesabından çıxılsın?</b>\n\nSessiya silinir, hesab Telegram-dan da "
                           "çıxış edir (Cihazlar siyahısından itir).",
                       InlineKeyboardMarkup(inline_keyboard=[ui.confirm("va:logoutok", "va:panel", "çıx")]))
            return
        elif act == "logoutok":
            await cb.answer("🚪 Çıxılır...")
            await va.logout()
            note = "🚪 Hesabdan çıxıldı"
        elif act == "add":
            used = {x.idx for x in va.slots}
            free_idx = next((i for i in range(1, MAX_SLOTS) if i not in used), None)
            if free_idx is None:
                await cb.answer(f"Ən çox {MAX_SLOTS} asistan", show_alert=True)
                return
            login_target["idx"] = free_idx
            try:
                text, kb = await flow.begin(cb.message.chat.id, cb.message.message_id)
            except Exception as e:
                await cb.answer(str(e)[:190], show_alert=True)
                return
            await cb.answer()
            await show(cb, f"➕ <b>{SLOT_ICONS[free_idx]} əlavə asistan</b>\n<i>Başqa (ayrıca) Telegram hesabı olmalıdır.</i>"
                           f"\n\n{text}", kb)
            return
        elif act == "maxper":
            nxt = next_of(MAXPER_STEPS, va.max_per)
            _set(S_MAXPER, nxt)
            await cb.answer("📊 " + (f"hər hesabda ən çox {nxt} yayım" if nxt else "limitsiz"))
        elif act == "vtoggle" and len(parts) > 2 and parts[2].isdigit():
            idx = int(parts[2])
            sl_ = next((x for x in va.slots if x.idx == idx), None)
            if not sl_:
                await cb.answer("Tapılmadı", show_alert=True)
                return
            sl_.voice_enabled = not sl_.voice_enabled
            if not sl_.voice_enabled:
                for cid in [c for c, b in list(va.bind.items()) if b is sl_]:
                    await va._end_chat(cid, announce=True, reason="bu hesab yalnız Depo rejiminə keçirildi")
                if sl_.calls:
                    with contextlib.suppress(Exception):
                        await sl_.calls.stop()
                    sl_.calls = None
                sl_.status = "işləyir (yalnız depo/işçi)"
                await cb.answer(f"📦 {sl_.icon} yalnız Depo rejiminə keçirildi — qruplara və səsə girməyəcək!", show_alert=True)
            else:
                if sl_.client and sl_.client.is_connected() and not sl_.calls and pytgcalls_version():
                    try:
                        sl_.calls = Calls(sl_.client, va._on_end, va._on_closed)
                        await sl_.calls.start()
                        va._register_dm(sl_)
                        va.rec.attach(sl_)
                    except Exception as _e:
                        logger.warning(f"vtoggle calls start xətası: {_e}")
                sl_.status = "işləyir"
                await cb.answer(f"🎙 {sl_.icon} səsli yayımlara qoşuldu!", show_alert=True)
            text, kb = va.panel_view()
            await show(cb, text, kb)
            return
        elif act == "rm" and len(parts) > 2 and parts[2].isdigit():
            idx = int(parts[2])
            sl_ = next((x for x in va.slots if x.idx == idx and idx > 0), None)
            if not sl_:
                await cb.answer("Tapılmadı", show_alert=True)
                return
            await cb.answer()
            await show(cb, f"🚪 <b>{escape(sl_.name())} asistanı çıxarılsın?</b>\n\n"
                           f"Onun apardığı {va.load(sl_)} yayım bitəcək, sessiya silinəcək.",
                       InlineKeyboardMarkup(inline_keyboard=[ui.confirm(f"va:rmok:{idx}", "va:panel", "çıxar")]))
            return
        elif act == "rmok" and len(parts) > 2 and parts[2].isdigit():
            idx = int(parts[2])
            sl_ = next((x for x in va.slots if x.idx == idx and idx > 0), None)
            if sl_:
                for cid in [c for c, b in va.bind.items() if b is sl_]:
                    await va._end_chat(cid, announce=True, reason="asistan hesabı çıxarıldı")
                with contextlib.suppress(Exception):
                    if sl_.client is not None:
                        await sl_.client.log_out()
                await va._close_slot(sl_)
                with contextlib.suppress(OSError):
                    os.remove(sl_.path)
                va.slots.remove(sl_)
                note = f"🚪 {sl_.icon} asistan çıxarıldı"
            await cb.answer("🚪")
        elif act == "stopall":
            for cid in list(va.chats):
                await va._end_chat(cid, announce=True, reason="bot sahibi dayandırdı")
            await cb.answer("⏹ Hamısı bitdi")
        elif act == "login":
            login_target["idx"] = 0
            try:
                text, kb = await flow.begin(cb.message.chat.id, cb.message.message_id)
            except Exception as e:
                await cb.answer(str(e)[:190], show_alert=True)
                return
            await cb.answer()
            await show(cb, text, kb)
            return
        elif act == "logincancel":
            await flow.cancel()
            await cb.answer("❌ Ləğv edildi")
        else:
            await cb.answer()
        await show(cb, *panel_view(note))

    # ── giriş: telefon → kod → 2FA (userbot/core/tg_login.py) ──
    @dp.message(F.chat.type == "private", F.text, F.from_user.func(lambda u: flow.waiting(u.id)))
    async def va_login_input(message: types.Message):
        await flow.handle(message)

    # ── qrup komandaları ──
    # ── 👤 adminlə əlaqə ──
    contact_cache = {}

    async def contact_url() -> str:
        v = (os.getenv("ADMIN_CONTACT") or "").strip()
        if v:
            return v if v.startswith(("http://", "https://", "tg://")) else f"https://t.me/{v.lstrip('@')}"
        if "url" not in contact_cache:
            url = f"tg://user?id={context.creator_id}"
            with contextlib.suppress(Exception):
                ch = await bot.get_chat(context.creator_id)
                if ch.username:
                    url = f"https://t.me/{ch.username}"
            contact_cache["url"] = url
        return contact_cache["url"]

    async def contact_kb():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="👤 Adminlə əlaqə", url=await contact_url())]])

    async def send_with_contact(chat_id, text, reply_to=None):
        """tg://user düyməsini məxfilik ayarı bloklasa — düyməsiz, mətndə link ilə."""
        try:
            return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=await contact_kb(),
                                          reply_to_message_id=reply_to, allow_sending_without_reply=True)
        except Exception:
            url = await contact_url()
            return await bot.send_message(chat_id, text + f'\n\n👤 <a href="{escape(url, quote=True)}">Admin</a>',
                                          parse_mode="HTML", reply_to_message_id=reply_to,
                                          allow_sending_without_reply=True)

    async def notify_disabled(chat_id, reply_to=None):
        await send_with_contact(
            chat_id, tr("🔴 <b>Canlı yayım asistanı hazırda söndürülüb.</b>\n\nSəsli yayımda mahnı oxutmaq üçün bot admini onu aktivləşdirməlidir — aşağıdakı düymə ilə əlaqə saxlayın.\n<i>Botun digər funksiyaları (/music, inline) işləyir.</i>"),
            reply_to)

    # ── 🔐 asistanı qrupa əlavə etmək üçün icazə ──
    join_asks = {}                       # chat_id → {"msg": id, "pending": [coroutine factory], "ts"}

    def asst_name(chat_id=None) -> str:
        me = va.slot_for(chat_id).me if chat_id is not None else va.me
        if not me:
            return tr("İfaçı")
        return f"@{me.username}" if me.username else escape(me.first_name or tr("İfaçı"))

    async def ask_join(chat, retry=None):
        """Qrupda icazə sorğusu (təkrar sorğu göndərmir — gözləyən əməliyyat əlavə olunur)."""
        ask = join_asks.get(chat.id)
        if ask and time.time() - ask["ts"] < 900:
            if retry:
                ask["pending"].append(retry)
            return False
        status = await va.member_status(chat.id)
        with contextlib.suppress(Exception):
            await va.assign(chat.id)                     # hansı hesab qoşulacaqsa onun adı göstərilsin
        warn_kicked = ("\n\n" + tr("⚠️ <i>Asistan əvvəllər bu qrupdan qadağan olunub — təsdiqdən sonra qadağa götürüləcək.</i>") if status == "kicked" else "")
        text = tr("🎙 <b>Canlı yayım asistanı</b> ({name}) bu qrupda deyil.\n\nSəsli yayımda mahnı oxutması üçün onu qrupa əlavə edimmi?\n<i>👮 Yalnız qrup adminləri təsdiq edə bilər. Asistan yalnız səs oxudur — mesaj yazmır, heç nəyi idarə etmir.</i>",
                  name=asst_name(chat.id)) + warn_kicked
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("✅ Əlavə et"), callback_data="vj:yes"),
            InlineKeyboardButton(text=tr("❌ Xeyr"), callback_data="vj:no")]])
        with ui_styled("voice"):
            msg = await bot.send_message(chat.id, text, parse_mode="HTML", reply_markup=kb)
        join_asks[chat.id] = {"msg": msg.message_id, "pending": [retry] if retry else [], "ts": time.time()}
        return True

    async def need_member(message: types.Message, retry) -> bool:
        """Asistan qrupdadırsa True; deyilsə icazə sorğusu göndərilir, əməliyyat təsdiqdən sonra təkrarlanır."""
        try:
            await va.ensure_member(message.chat.id)
            return True
        except NotMember:
            sent = await ask_join(message.chat, retry)
            if not sent:
                with contextlib.suppress(Exception):
                    with ui_styled("voice"):
                        await message.reply(tr("🔐 <b>Asistan təsdiqi</b>\nAsistanın qrupa əlavə olunması üçün admin təsdiqi gözlənilir — təsdiqdən sonra avtomatik davam edəcək."))
            return False

    @dp.callback_query(F.data.startswith("vj:"))
    async def vj_cb(cb: types.CallbackQuery):
        chat = cb.message.chat
        uid = cb.from_user.id
        is_admin = uid == context.creator_id
        if not is_admin:
            with contextlib.suppress(Exception):
                m = await bot.get_chat_member(chat.id, uid)
                is_admin = str(getattr(m.status, "value", m.status)) in ("administrator", "creator")
        if not is_admin:
            await cb.answer(tr("👮 Yalnız qrup adminləri təsdiq edə bilər"), show_alert=True)
            return
        ask = join_asks.get(chat.id)
        if cb.data == "vj:no":
            join_asks.pop(chat.id, None)
            await cb.answer(tr("❌ İmtina edildi"))
            with contextlib.suppress(Exception):
                await cb.message.edit_text(tr("❌ Asistan qrupa əlavə edilmədi. Lazım olsa /play ilə yenidən soruşulacaq."))
            return
        if not va.enabled:
            await cb.answer()
            join_asks.pop(chat.id, None)
            with contextlib.suppress(Exception):
                await cb.message.delete()
            await notify_disabled(chat.id)
            return
        if not va.running:
            err = await va.start()
            if err:
                await cb.answer(f"⚠️ Asistan işləmir: {err[:150]}", show_alert=True)
                return
        await cb.answer(tr("⏳ Qoşulur..."))
        with contextlib.suppress(Exception):
            await cb.message.edit_text(tr("⏳ <i>Asistan qrupa qoşulur...</i>"), parse_mode="HTML")
        try:
            await va.join(chat.id)
        except Exception as e:
            with contextlib.suppress(Exception):
                await cb.message.edit_text(f"⚠️ {e}", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=tr("🔄 Yenidən sına"), callback_data="vj:yes"),
                                      InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data="vj:no")]]))
            return
        join_asks.pop(chat.id, None)
        by = escape(cb.from_user.first_name or "admin")
        with contextlib.suppress(Exception):
            await cb.message.edit_text(
                tr("✅ <b>Asistan qrupa əlavə olundu</b> ({name}) — təsdiq: {by}\n\n🎙 İndi səsli yayımda: <code>/play mahnı adı</code> · <code>/mixplay</code>\n<i>💡 Asistanı \"Video chatları idarə etmək\" icazəli admin etsəniz, səsli yayımı özü başlada bilər.</i>",
                   name=asst_name(), by=by),
                parse_mode="HTML")
        for retry in (ask or {}).get("pending", []):     # gözləyən /play, /mixplay — indi davam
            with contextlib.suppress(Exception):
                await retry()

    # ── bot qrupa əlavə olunanda ──
    # Handler yox, outer middleware: hadisəni udmur, start_plugin-in salam handler-inə də ötürür.
    # Asistan işi fonda gedir ki, (sleep, va.start) qrup salamını gecikdirməsin.
    _bg_tasks = set()

    async def _membership_mw(handler, event, data):
        task = asyncio.create_task(on_bot_membership(event))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return await handler(event, data)

    dp.my_chat_member.outer_middleware(_membership_mw)

    async def on_bot_membership(upd: types.ChatMemberUpdated):
        try:
            await _on_bot_membership(upd)
        except Exception as e:
            logger.warning(f"🎙 Asistan qrupa qoşulma yoxlanışı xətası: {e}")

    async def _on_bot_membership(upd: types.ChatMemberUpdated):
        if upd.chat.type not in ("group", "supergroup"):
            return
        new = str(getattr(upd.new_chat_member.status, "value", upd.new_chat_member.status))
        old = str(getattr(upd.old_chat_member.status, "value", upd.old_chat_member.status))
        if new not in ("member", "administrator") or old in ("member", "administrator", "creator", "restricted"):
            return                                    # yalnız yeni əlavə olunma (yüksəltmə yox)
        if not va.enabled or not va.has_session():
            return                                    # söndürülübsə — /play yazılanda bildiriləcək
        if not va.running and await va.start():
            return
        await asyncio.sleep(2)                        # qrup yaradılırkən əlavə olunubsa — asistan da ola bilər
        if await va.member_status(upd.chat.id) != "member":
            with contextlib.suppress(Exception):
                await ask_join(upd.chat)

    async def guard(message: types.Message) -> bool:
        if message.chat.type not in ("group", "supergroup"):
            await message.reply(tr("🎙 Bu komanda qruplarda, səsli yayımda işləyir."))
            return False
        try:
            from bot.plugins.restart_plugin import is_restart_pending
            if is_restart_pending() and not is_creator(message.from_user.id):
                await message.reply(tr("⏳ <b>Bot restart üçün gözləmədədir.</b>\n"
                                       "Cari mahnılar və yükləmələr bitdikdən sonra bot yenidən başlayacaq (1-2 dəqiqə).\n"
                                       "Zəhmət olmasa bir az sonra yenidən cəhd edin."), parse_mode="HTML")
                return False
        except Exception as _e:
            logger.debug("udulan xəta [bot/voice/handlers.py:628]: %r", _e)
        if not va.enabled:
            await notify_disabled(message.chat.id, reply_to=message.message_id)
            return False
        if not va.running:
            err = await va.start()
            if err:
                await message.reply(tr("⚠️ Asistan işləmir: {err}", err=escape(err)), parse_mode="HTML")
                return False
        if not await can_control(message.chat.id, message.from_user.id):
            await message.reply(tr("👮 Yalnız qrup adminləri idarə edə bilər."))
            return False
        return True

    # ── 🧪 /vctest — qoşulma diaqnostikası (yalnız creator, qrupda) ──
    @dp.message(Command("vctest", ignore_mention=True))
    async def vctest_cmd(message: types.Message):
        if not (message.from_user and message.from_user.id == context.creator_id):
            return
        if message.chat.type not in ("group", "supergroup"):
            await message.reply("🧪 /vctest qrupda yazılır — səsli söhbət açıq olan qrupda.")
            return
        with ui_styled("voice"):
            status = await message.reply("🧪 <b>Səsli yayım diaqnostikası...</b>\n<i>~30 san.</i>", parse_mode="HTML")
            report = await va.vctest(message.chat.id)
            text = "\n".join(report["summary"])
            with contextlib.suppress(Exception):
                await status.edit_text(text[:4000], parse_mode="HTML", disable_web_page_preview=True)
        with contextlib.suppress(Exception):
            await message.answer_document(BufferedInputFile("\n".join(report["log"]).encode("utf-8", "replace"),
                                                            filename=f"vctest_{message.chat.id}.txt"),
                                          caption="🧪 Tam log — mənə göndər")

    @dp.message(Command("play", "p", "oynat", prefix="/!.", ignore_mention=True))
    async def play_cmd(message: types.Message, command: CommandObject):
        if not await guard(message):
            return
        if not await need_member(message, lambda: play_cmd(message, command)):
            return
        query = (command.args or "").strip()
        reply = message.reply_to_message
        by = message.from_user.first_name or ""
        with ui_styled("voice"):
            try:
                status = await message.reply(tr("🔎 <b>Axtarılır...</b>\n<i>Zəhmət olmasa gözləyin...</i>"), parse_mode="HTML")
            except Exception:
                status = await message.answer(tr("🔎 <b>Axtarılır...</b>\n<i>Zəhmət olmasa gözləyin...</i>"), parse_mode="HTML")
            async def _safe_status(txt: str, **kwargs):
                try:
                    with ui_styled("voice"):
                        await status.edit_text(txt, parse_mode="HTML", **kwargs)
                except Exception as _e:
                    logger.debug(f"Status mesajı yenilənmədi: {_e}")

            try:
                if not query and reply and (reply.audio or reply.voice):
                    a = reply.audio or reply.voice
                    title = (f"{a.performer} - {a.title}" if getattr(a, "performer", None) and getattr(a, "title", None)
                             else getattr(a, "title", None) or getattr(a, "file_name", None) or "Səs faylı")
                    t = Track(title, None, None, a.duration or 0, a.file_id, by, src="reply")
                elif not query and reply and (reply.text or reply.caption) and not (reply.text or reply.caption or "").startswith("/"):
                    reply_text = (reply.text or reply.caption or "").strip()
                    t = await va.resolve(reply_text, by)
                elif not query:
                    await _safe_status(tr("🎙 <b>İstifadə qaydası</b>\n<code>/play mahnı adı</code> və ya mahnıya reply edib <code>/play</code>"))
                    return
                else:
                    t = await va.resolve(query, by)
                if message.from_user:
                    t.user_id = message.from_user.id
                if t.duration and t.duration > MAX_DURATION:
                    await _safe_status(tr("⏭ <b>Mahnı çox uzundur</b>\n{dur} (maksimum {max_dur})", dur=fmt_dur(t.duration), max_dur=fmt_dur(MAX_DURATION)))
                    return
                if not t.file_id and t.url:
                    await _safe_status(
                        f"⏳ <b>{escape(t.title[:80])}</b>\n<i>" + tr("📦 Depoda yoxdur — depoya yüklənir...") + "</i>"
                    )
                    await va.ensure_depo(t)
                await _safe_status(
                    f"⏳ <b>{escape(t.title[:80])}</b>\n<i>"
                    + (tr("📦 depodan götürülür...") if t.file_id else tr("⬇️ yüklənir...")) + "</i>"
                )
                last_edit = [0.0]
                shown_label = [""]

                async def progress(label):                  # hər mərhələ dəyişikliyi görünür (eyni yazı təkrarlanmır)
                    if label == shown_label[0]:
                        return
                    now = time.monotonic()
                    if now - last_edit[0] < 1.5:            # flood nəzarəti (Telegram limitlərindən qorunma)
                        return
                    shown_label[0] = label
                    last_edit[0] = now
                    await _safe_status(f"⏳ <b>{escape(t.title[:80])}</b>\n<i>{label}</i>")

                kind, pos = await va.enqueue(message.chat.id, message.chat.title or "", t, progress)
                if kind == "playing":
                    with contextlib.suppress(Exception):
                        await status.delete()
                else:
                    del_btn = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🗑 Növbədən sil", callback_data=f"vq:del:{t.track_id}")
                    ]])
                    await _safe_status(
                        tr("📋 <b>Növbəyə əlavə olundu (#{pos})</b>\n{title}", pos=pos, title=escape(t.title[:80])),
                        reply_markup=del_btn,
                    )
                    st = va.chats.get(message.chat.id)
                    if st:
                        await va.refresh_np(st)
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if "groupcall" in low.replace("_", "") or "no active" in low or "not in a call" in low:
                    msg = tr("Səsli yayım başlamayıb — qrupda video chat başladın (və ya asistanı \"Manage video chats\" admini edin)")
                logger.warning(f"🎙 /play xətası ({message.chat.id}): {e}", exc_info=not isinstance(e, RuntimeError))
                with contextlib.suppress(Exception):
                    await status.edit_text(f"❌ <b>Xəta baş verdi</b>\n{escape(msg[:300])}", parse_mode="HTML")

    async def simple(message: types.Message, action: str):
        if not await guard(message):
            return
        chat_id = message.chat.id
        st = va.chats.get(chat_id)
        if action == "stop":
            if st or chat_id in va.chats or chat_id in va.bind:
                await va._end_chat(chat_id, announce=True, reason=tr("dayandırıldı"))
            else:
                with contextlib.suppress(Exception):
                    await va.calls_for(chat_id).leave(chat_id)
                await message.reply(tr("🔇 Hazırda heç nə oxunmur."))
            with contextlib.suppress(Exception):
                await message.delete()
            return
        if not st or (not st.current and not getattr(st, "active_track", None) and not st.queue):
            await message.reply(tr("🔇 Hazırda heç nə oxunmur."))
            return
        await do_action(st, action)
        with contextlib.suppress(Exception):
            await message.delete()

    async def do_action(st: ChatState, action: str) -> str:
        if action == "pause" and not st.paused:
            await va.calls_for(st.chat_id).pause(st.chat_id)
            st.pause()
            await va.refresh_np(st)
            return tr("⏸ Fasilə")
        if action == "resume" and st.paused:
            await va.calls_for(st.chat_id).resume(st.chat_id)
            st.resume()
            await va.refresh_np(st)
            return tr("▶️ Davam")
        if action == "toggle":
            return await do_action(st, "resume" if st.paused else "pause")
        if action == "skip":
            await va.next(st.chat_id, skipped=True)
            return tr("⏭ Növbəti")
        if action == "stop":
            await va._end_chat(st.chat_id, announce=True, reason=tr("dayandırıldı"))
            return tr("⏹ Dayandırıldı")
        if action == "radio":
            if st.radio:
                st.radio = None
                await va.refresh_np(st)
                return tr("🔀 Mix rejimi söndürüldü")
            if not st.current:
                return tr("🔇 Hazırda heç nə oxunmur")
            seed = st.current.vid
            if not seed:
                try:
                    res = await va.resolve(st.current.title, "")
                    if res and res.vid:
                        st.current.vid = res.vid
                        seed = res.vid
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/voice_assistant_plugin.py:2682]: %r", _e)
            if not seed:
                return tr("Bu mahnı üçün Mix tapılmadı")
            st.radio = {"seed": seed, "seen": {t.vid for t in [st.current, *st.queue] if t and t.vid}}
            n = await va.refill(st, force=True)
            await va.refresh_np(st)
            return tr("🔀 Mix: +{n} mahnı", n=n)
        if action in ("clear", "clear_queue", "clearqueue", "cqueue"):
            n = va.clear_queue(st.chat_id)
            await va.refresh_np(st)
            return tr("🗑 Növbə təmizləndi ({n} mahnı silindi)", n=n) if n else tr("📋 Növbə onsuz da boşdur")
        return ""

    # ── ⏺ /vrec — asistan qeydinin idarə paneli (bot/core/va_record.py) ──
    #   qrupda:      /vrec [start | stop | mark | auto | manual | status | music N]  → əməl + panel
    #   şəxsi çatda: /vrec  → qruplar siyahısı;  userbot: .vcrec (userbot/plugins/vcrec.py) → eyni panel
    async def vrec_open(chat_id: int, title: str, note: str = "", where: int = None, owner: bool = True):
        """Paneli göndər: 👁 ayarına görə botun şəxsi çatına (default) və ya qrupun özünə."""
        va_record.remember_group(chat_id, title)
        text, kb = await va.rec.panel(chat_id, title, note, owner=owner)
        target = where if where is not None else (context.creator_id if va_record.panel_dm() else chat_id)
        with ui_styled("admin"):
            await bot.send_message(target, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    async def vrec_command(chat_id: int, title: str, args: str, by: str = "", where: int = None,
                           owner: bool = True):
        """/vrec və .vcrec-in ortaq məntiqi: alt-komanda (varsa) → nəticə panelin ℹ️ sətrində.

        owner=False (qrup admini): yalnız öz qrupunun qeydini idarə edir — qlobal ayarlar bağlıdır."""
        words = (args or "").strip().lower().split()
        sub = words[0] if words else ""
        alias = {"başla": "start", "basla": "start", "dayan": "stop", "end": "stop", "nişan": "mark",
                 "nisan": "mark", "avto": "auto", "avtomatik": "auto", "əl": "manual"}
        sub = alias.get(sub, sub)
        note = ""
        try:
            if sub and not owner and sub not in va_record.GROUP_ACTS:
                note = "⛔ Bu ayar bütün qruplara aiddir — yalnız bot sahibi dəyişə bilər"
            elif sub in ("start", "stop", "mark", "auto", "manual"):
                note = await va.rec.action(chat_id, title, sub, by=by)
            elif sub in ("music", "musiqi") and len(words) > 1 and words[1].rstrip("%").isdigit():
                note = f"🎚 Musiqi səviyyəsi: {va_record.set_music_level(int(words[1].rstrip('%')))}%"
            elif sub in ("timer", "taymer"):
                if len(words) > 1 and words[1].isdigit():
                    val = va_record.set_timer_limit(int(words[1]))
                else:
                    val = va_record.set_timer_limit(va_record._cycle(va_record.TIMER_STEPS, va_record.timer_limit()))
                note = f"⏱ Taymer: {val} dəqiqə" if val else "⏱ Taymer: limitsiz"
            elif sub in ("silent", "sessiz", "səssiz"):
                if len(words) > 1 and words[1] in ("on", "off", "1", "0", "ac", "aç", "bagla", "bağla"):
                    va_record._put(va_record.S_SILENT, "1" if words[1] in ("on", "1", "ac", "aç") else "0")
                    on = va_record.silent_on()
                else:
                    on = va_record.toggle_silent()
                note = "🔕 Səssiz rejim aktiv" if on else "🔔 Səssiz rejim söndürüldü"
            elif sub in ("dest", "hedef", "hədəf", "target", "tgt"):
                if len(words) > 1:
                    w1 = words[1]
                    if w1 in ("target", "dm", "here", "qrup"):
                        d = "here" if w1 == "qrup" else w1
                        va_record._put(va_record.S_DEST, d)
                        dest_map = {"target": "🎯 .rec hədəfi", "dm": "💬 şəxsi çat", "here": "👥 bu qrup"}
                        note = f"📤 Hədəf: {dest_map.get(d, d)}"
                    else:
                        from bot.plugins.menu_plugin import audit_apply_target
                        st = "vc"
                        raw_target = " ".join((args or "").strip().split()[1:])
                        if w1 in ("vc", "tr", "spl") and len(words) > 2:
                            st = w1
                            raw_target = " ".join((args or "").strip().split()[2:])
                        note = await audit_apply_target(st, raw_target)
                else:
                    target = where if where is not None else (context.creator_id if va_record.panel_dm() else chat_id)
                    text, kb = va.rec.target_view(chat_id, "vc")
                    with ui_styled("admin"):
                        await bot.send_message(target, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                    return
            elif sub in ("keep", "saxla"):
                if len(words) > 1 and words[1].isdigit():
                    v = int(words[1])
                    va_record._put(va_record.S_KEEP, v)
                else:
                    v = va_record._cycle(va_record.KEEP_STEPS, va_record.keep_days())
                    va_record._put(va_record.S_KEEP, v)
                note = f"🗑 Qeydlər {v} gün saxlanılır" if v else "🗑 Qeydlər həmişə saxlanılır"
            elif sub in ("status", "?", "vəziyyət"):
                note = va.rec.status_text()
        except Exception as e:
            logger.warning(f"⏺ /vrec {sub} ({chat_id}): {e}")
            note = f"❌ {e}"
        await vrec_open(chat_id, title, note, where, owner=owner)

    context.vrec_command = vrec_command                   # userbot .vcrec bunu çağırır

    @dp.message(Command("vrec", "arec", "vcrec", prefix="/!.", ignore_mention=True))
    async def vrec_cmd(message: types.Message, command: CommandObject):
        if not message.from_user:
            return
        is_creator_user = context.is_creator(message.from_user.id)
        if message.chat.type in ("group", "supergroup"):
            from common.permissions import is_group_admin
            if not is_creator_user and not await is_group_admin(message.bot, message.chat.id, message.from_user.id):
                return
            with contextlib.suppress(Exception):
                await message.delete()                   # qrupda iz qalmasın (bot admin deyilsə qalır)
            where = None if is_creator_user else message.chat.id
            await vrec_command(message.chat.id, message.chat.title or str(message.chat.id), command.args or "",
                                by=message.from_user.full_name, where=where, owner=is_creator_user)
            return
        if not is_creator_user:
            return
        if (command.args or "").strip():
            await vrec_command(0, "Şəxsi", command.args or "", by=message.from_user.full_name, where=message.chat.id)
            return
        note = va.rec.status_text() if va.rec.sessions else ""
        text, kb = va.rec.groups_view(note)
        with ui_styled("admin"):
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("vrc:"))
    async def vrc_cb(cb: types.CallbackQuery):
        _, cid, act = (cb.data.split(":", 2) + ["", ""])[:3]
        try:
            chat_id = int(cid)
        except ValueError:
            await cb.answer()
            return
        is_creator_user = context.is_creator(cb.from_user.id)
        if not is_creator_user:
            if chat_id != 0:
                from common.permissions import is_group_admin
                if not await is_group_admin(cb.bot, chat_id, cb.from_user.id):
                    await cb.answer("👮 Bu paneli yalnız qrup adminləri idarə edə bilər!", show_alert=True)
                    return
            else:
                await cb.answer("⛔ Bu panel yalnız bot sahibi üçündür!", show_alert=True)
                return
            if act not in va_record.GROUP_ACTS:
                await cb.answer("⛔ Bu ayar bütün qruplara aiddir — yalnız bot sahibi dəyişə bilər", show_alert=True)
                return
        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        # 🎯 Qruplar menyusundan birbaşa aktiv qeydi dayandırma / nişanlama
        if act in ("gstop", "gmark"):
            title = (va.rec.sessions[chat_id].title if chat_id in va.rec.sessions else
                     va_record.auto_chats().get(chat_id) or va_record.known_groups().get(chat_id) or str(chat_id))
            real_act = "stop" if act == "gstop" else "mark"
            if real_act == "stop":
                with contextlib.suppress(Exception):
                    await cb.answer("⏳ Qeyd saxlanılır...")
            note = ""
            try:
                note = await va.rec.action(chat_id, title, real_act, by=cb.from_user.full_name)
            except Exception as e:
                note = f"❌ {e}"
            ans = note if note else None
            with contextlib.suppress(Exception):
                await cb.answer(ans[:190] if ans else None, show_alert=bool(note and note.startswith("❌")))
            text, kb = va.rec.groups_view(note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        # 🎯 Hədəf idarəetməsi (Tək rejim düyməsi, 1 fərdi hədəf, silmə düyməsi)
        if act == "dest":
            await cb.answer()
            text, kb = va.rec.target_view(chat_id, "vc")
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act == "t_mode_cycle":
            nxt = va_record.cycle_dest()
            mode_names = {
                "target": "🎯 Fərdi Hədəf",
                "dm": "💬 Şəxsi çat",
                "here": "👥 Bu çat",
            }
            note = f"📤 Rejim: {mode_names.get(nxt, nxt)}"
            with contextlib.suppress(Exception):
                await cb.answer(note)
            text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act.startswith("t_mode:"):
            mode = act.split(":", 1)[1]
            va_record._put(va_record.S_DEST, mode)
            mode_names = {"target": "🎯 Fərdi Hədəf", "dm": "💬 Şəxsi çat", "here": "👥 Bu çat"}
            note = f"📤 Aktiv rejim: {mode_names.get(mode, mode)}"
            with contextlib.suppress(Exception):
                await cb.answer(note)
            text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act == "tgt_del":
            from bot.plugins.menu_plugin import _lg, _lgub
            lg, ub = _lg(), _lgub()
            if ub and lg:
                for s_key in ("vc", "tr", "spl"):
                    lg.reset_stream_target(ub, s_key)
                    ub.db.kv_del("logger", f"{s_key}_target_chat")
                    ub.db.kv_del("logger", f"{s_key}_target_topic")
            note = "🗑 Fərdi hədəf silindi"
            with contextlib.suppress(Exception):
                await cb.answer(note)
            text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act in ("tgt_in", "tgt_in:vc"):
            from bot.plugins.menu_plugin import REC_INP
            REC_INP.update(stage="await_target", stream="vc", origin="vrc", vrc_cid=chat_id,
                           chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text = (
                "🎯 <b>Canlı yayım qeydi üçün hədəf təyin et</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                "Hədəfi bu çata yaz və göndər:\n"
                "• <code>@kanal</code> və ya <code>-1001234567890</code>\n"
                "• topic ilə: <code>-1001234567890 42</code> və ya <code>https://t.me/c/1234567890/42</code>\n"
                "• cari qrup üçün sadəcə topic: <code>42</code>\n"
                "• <code>me</code> — Saved Messages (Saxlanmış mesajlar)"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✖️ Ləğv et", callback_data=f"vrc:{chat_id}:dest")
            ]])
            await cb.answer()
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act in ("tgt_tp", "tgt_tp:vc"):
            from bot.plugins.menu_plugin import _lg, _lgub, audit_open_topics
            lg, ub = _lg(), _lgub()
            cur_chat, _, _ = lg.get_stream_target(ub, "vc") if ub and lg else (None, None, None)
            view = await audit_open_topics("vc", cur_chat)
            if view is None:
                text, kb = va.rec.target_view(chat_id, "vc", "⚠️ Forum deyil və ya topic-lər oxunmadı")
                with contextlib.suppress(Exception):
                    await cb.answer("⚠️ Forum deyil və ya topic-lər oxunmadı", show_alert=True)
            else:
                text, kb = va.rec.topic_view(chat_id, "vc")
                with contextlib.suppress(Exception):
                    await cb.answer("🧵 Topic-lər açıldı")
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act.startswith("tgt_tpset:"):
            parts = act.split(":")
            pick = parts[-1]
            from bot.plugins.menu_plugin import _lg, _lgub, _AU_TOPICS
            lg, ub = _lg(), _lgub()
            data = _AU_TOPICS.get("vc")
            if not data:
                text, kb = va.rec.target_view(chat_id, "vc", "⚠️ Topic siyahısı yoxdur")
                with contextlib.suppress(Exception):
                    await cb.answer("⚠️ Topic siyahısı yoxdur", show_alert=True)
            else:
                if pick == "g":
                    tid, tname = None, "General"
                else:
                    idx = int(pick) if pick.isdigit() else -1
                    tid, tname = data["topics"][idx] if 0 <= idx < len(data["topics"]) else (None, "General")
                lg.set_target(ub, "vc", data["chat"], tid, f"{data['title'][:20]} › {tname[:20]}")
                lg.reset_stream_target(ub, "tr")
                lg.reset_stream_target(ub, "spl")
                note = f"🧵 Topic seçildi: {data['title'][:30]} › {tname}"
                with contextlib.suppress(Exception):
                    await cb.answer(f"🧵 {tname}"[:190])
                text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act.startswith("tgt_pk:"):
            parts = act.split(":")
            idx_str = parts[-1]
            from bot.plugins.menu_plugin import _lg, _lgub, _au_candidates, audit_open_topics
            lg, ub = _lg(), _lgub()
            cands = _au_candidates(lg, ub)
            idx = int(idx_str) if idx_str.isdigit() else -1
            if 0 <= idx < len(cands):
                c, t, n = cands[idx]
                lg.set_target(ub, "vc", c, t, n)
                lg.reset_stream_target(ub, "tr")
                lg.reset_stream_target(ub, "spl")
                note = f"📌 Hədəf: {n}" + (f" · topic {t}" if t else "")
                if not t:
                    view = await audit_open_topics("vc", c, n)
                    if view is not None:
                        text, kb = va.rec.topic_view(chat_id, "vc", note + " — indi topic seç (və ya 💬 General)")
                        with contextlib.suppress(Exception):
                            await cb.answer(f"📌 {n}"[:190])
                        with contextlib.suppress(Exception):
                            with ui_styled("admin"):
                                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                        return
                with contextlib.suppress(Exception):
                    await cb.answer(note[:190])
                text, kb = va.rec.target_view(chat_id, "vc", note)
            else:
                with contextlib.suppress(Exception):
                    await cb.answer("⚠️ Namizəd tapılmadı", show_alert=True)
                text, kb = va.rec.target_view(chat_id, "vc", "⚠️ Namizəd tapılmadı")
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act in ("tgt_me", "tgt_me:vc"):
            from bot.plugins.menu_plugin import _lg, _lgub
            lg, ub = _lg(), _lgub()
            if ub and lg:
                lg.set_target(ub, "vc", "me")
                lg.reset_stream_target(ub, "tr")
                lg.reset_stream_target(ub, "spl")
            note = "💾 Saved Messages-ə göndəriləcək"
            with contextlib.suppress(Exception):
                await cb.answer(note)
            text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act in ("tgt_test", "tgt_test:vc"):
            from bot.plugins.menu_plugin import audit_test
            try:
                note = await audit_test("vc")
            except Exception as e:
                note = f"❌ {e}"
            clean_note = re.sub(r"<[^>]+>", "", note) if note else ""
            with contextlib.suppress(Exception):
                await cb.answer(clean_note[:190], show_alert=clean_note.startswith("❌"))
            text, kb = va.rec.target_view(chat_id, "vc", note)
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act.startswith("tgt_s:"):
            await cb.answer()
            text, kb = va.rec.target_view(chat_id, "vc")
            with contextlib.suppress(Exception):
                with ui_styled("admin"):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            return

        if act == "list" or (chat_id == 0 and act in ("music", "br", "timer", "silent", "keep", "tr", "split", "pdm", "refresh")):
            if not is_creator_user:
                from bot.core.group_panel import panel_view
                text, kb = await panel_view(cb.bot, chat_id or cb.message.chat.id, cb.from_user.id,
                                            cb.message.chat.title, private=cb.message.chat.type == "private")
                with contextlib.suppress(Exception):
                    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
                return
            note = ""
            if act not in ("list", "open", "refresh"):
                try:
                    note = await va.rec.action(0, "", act, by=cb.from_user.full_name)
                except Exception as e:
                    note = f"❌ {e}"
            ans = note if note else ("🔄 Yeniləndi" if act in ("list", "refresh") else None)
            with contextlib.suppress(Exception):
                await cb.answer(ans[:190] if ans else None, show_alert=bool(note and note.startswith("❌")))
            text, kb = va.rec.groups_view(note)
        else:
            title = (va.rec.sessions[chat_id].title if chat_id in va.rec.sessions else
                     va_record.auto_chats().get(chat_id) or va_record.known_groups().get(chat_id) or str(chat_id))
            note = ""
            if act not in ("open", "refresh"):
                if act in ("start", "stop"):
                    with contextlib.suppress(Exception):
                        await cb.answer("⏳ Gözləyin...", show_alert=False)
                    try:
                        note = await va.rec.action(chat_id, title, act, by=cb.from_user.full_name)
                    except Exception as e:
                        logger.warning(f"⏺ panel {act} ({chat_id}): {e}")
                        note = f"❌ {e}"
                else:
                    try:
                        note = await va.rec.action(chat_id, title, act, by=cb.from_user.full_name)
                    except Exception as e:
                        logger.warning(f"⏺ panel {act} ({chat_id}): {e}")
                        note = f"❌ {e}"
                    ans = note if note else None
                    with contextlib.suppress(Exception):
                        await cb.answer(ans[:190] if ans else None, show_alert=bool(note and note.startswith("❌")))
            else:
                ans = "🔄 Yeniləndi" if act == "refresh" else None
                with contextlib.suppress(Exception):
                    await cb.answer(ans)
            text, kb = await va.rec.panel(chat_id, title, note, from_menu=(chat_id < 0), owner=is_creator_user)
        with contextlib.suppress(Exception):
            with ui_styled("admin"):
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    # ⚡ avtomatik rejim: bot qrupda "səsli yayım başladı / bitdi" servis mesajını görür
    # (asistan hesabının öz hadisəsi də var — va_record.Recorder.attach; ikisindən hansı tez gəlsə)
    @dp.message(F.video_chat_started)
    async def on_vc_started(message: types.Message):
        await va.rec.on_call_started(message.chat.id, message.chat.title or str(message.chat.id))

    @dp.message(F.video_chat_ended)
    async def on_vc_ended(message: types.Message):
        await va.rec.on_call_ended(message.chat.id)

    @dp.message(Command("mixplay", "mp", "radio", prefix="/!.", ignore_mention=True))
    async def mixplay_cmd(message: types.Message, command: CommandObject):
        if not await guard(message):
            return
        if not await need_member(message, lambda: mixplay_cmd(message, command)):
            return
        query = (command.args or "").strip()
        reply = message.reply_to_message
        by = message.from_user.first_name or ""
        st = va.chats.get(message.chat.id)

        # 1. Canlı yayımda mahnı oxunmursa və ad/reply verilməyibsə → defolt mesaj və düymələr
        if not query and not (reply and reply.audio) and not (st and st.current):
            text = tr(
                "🔀 <b>Mixplay — Oxşar mahnılar</b>\n\n"
                "🔇 Hazırda canlı yayımda mahnı oxunmur.\n\n"
                "<b>İstifadə qaydası:</b>\n"
                "• <code>/mixplay mahnı adı</code> və ya link\n"
                "• Mahnıya reply edib <code>/mixplay</code>\n"
                "• Canlı yayımda mahnı oxunarkən sadəcə <code>/mixplay</code>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔀 /mixplay", switch_inline_query_current_chat="/mixplay "),
                InlineKeyboardButton(text="▶️ /play", switch_inline_query_current_chat="/play "),
            ]])
            with ui_styled("voice"):
                await message.reply(text, parse_mode="HTML", reply_markup=kb)
            return

        with ui_styled("voice"):
            status = await message.reply(tr("🔀 <b>Oxşar mahnılar axtarılır...</b>\n<i>Zəhmət olmasa gözləyin...</i>"), parse_mode="HTML")
            try:
                if query:
                    seed = await va.resolve(query, by)
                elif reply and reply.audio:
                    a = reply.audio
                    q = f"{a.performer} - {a.title}" if a.performer and a.title else (a.title or a.file_name or "")
                    m = _VID_RE.search(" ".join(filter(None, [reply.caption or "",
                                                             *(e.url or "" for e in (reply.caption_entities or []))])))
                    seed = await va.resolve(f"https://youtu.be/{m.group(1)}" if m else q, by)
                elif st and st.current:
                    seed = st.current                          # hazırda oxunanın Mix-i
                    if not seed.vid:
                        resolved = await va.resolve(seed.title, by)
                        seed.vid = resolved.vid
                else:
                    await status.edit_text(tr("🔀 <b>İstifadə qaydası</b>\n<code>/mixplay mahnı adı</code> və ya mahnı oxunarkən sadəcə <code>/mixplay</code>"), parse_mode="HTML")
                    return
                if not seed.vid:
                    raise RuntimeError("bu mahnının YouTube ID-si tapılmadı — Mix qurula bilmir")
                with contextlib.suppress(Exception):
                    await status.edit_text(f"🔀 <b>Mix: {escape(seed.title[:70])}</b>\n<i>"
                                           + (tr("📦 depodan götürülür...") if seed.file_id else tr("⬇️ hazırlanır...")) + "</i>",
                                           parse_mode="HTML")
                started, n = await va.start_mix(message.chat.id, message.chat.title or "", seed, by)
                auto_str = tr(" · ♾ bitəndə özü davam edəcək") if va.mix_auto else ""
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🔀 Mix: Aktiv ✅", callback_data="vp:radio"),
                        InlineKeyboardButton(text="📋 Növbə", callback_data="vp:refresh"),
                    ],
                    [
                        InlineKeyboardButton(text="🗑 Növbəni təmizlə", callback_data="vp:clear"),
                    ],
                ])
                await status.edit_text(tr("🔀 <b>Mix başladı:</b> {title}\n📋 +{n} oxşar mahnı növbəyə əlavə olundu{auto}",
                                          title=escape(seed.title[:70]), n=n, auto=auto_str),
                                       parse_mode="HTML", reply_markup=kb)
                spawn(_autodelete(status, 30))
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if "groupcall" in low.replace("_", "") or "no active" in low or "not in a call" in low:
                    msg = tr("Səsli yayım başlamayıb — qrupda video chat başladın")
                logger.warning(f"🎙 /mixplay xətası ({message.chat.id}): {e}")
                with contextlib.suppress(Exception):
                    await status.edit_text(f"❌ <b>Xəta baş verdi</b>\n{escape(msg[:300])}", parse_mode="HTML")

    async def _autodelete(msg, delay):
        await asyncio.sleep(delay)
        with contextlib.suppress(Exception):
            await msg.delete()

    @dp.message(Command("pause", prefix="/!.", ignore_mention=True))
    async def pause_cmd(message: types.Message):
        await simple(message, "pause")

    @dp.message(Command("resume", prefix="/!.", ignore_mention=True))
    async def resume_cmd(message: types.Message):
        await simple(message, "resume")

    @dp.message(Command("skip", "next", prefix="/!.", ignore_mention=True))
    async def skip_cmd(message: types.Message):
        await simple(message, "skip")

    @dp.message(Command("stop", "end", prefix="/!.", ignore_mention=True))
    async def stop_cmd(message: types.Message):
        if message.chat.type not in ("group", "supergroup"):
            return                                   # şəxsi çatda /stop başqa plugin-lərə qalsın
        await simple(message, "stop")

    @dp.message(Command("queue", prefix="/!.", ignore_mention=True))
    async def queue_cmd(message: types.Message):
        if message.chat.type not in ("group", "supergroup"):
            return
        st = va.chats.get(message.chat.id)
        if not st or (not st.current and not getattr(st, "active_track", None) and not st.queue):
            await message.reply(tr("🔇 Hazırda heç nə oxunmur — /play mahnı adı"))
            return
        if st.np_msg:
            with contextlib.suppress(Exception):
                await bot.delete_message(st.chat_id, st.np_msg)
            st.np_msg = None
        await va.announce(st)

    @dp.message(Command("clearqueue", "vclear", "cqueue", "clearq", "cq", "clear", prefix="/!.", ignore_mention=True))
    async def clear_queue_cmd(message: types.Message, command: CommandObject):
        if message.chat.type not in ("group", "supergroup"):
            return
        st = va.chats.get(message.chat.id)
        cmd_name = (command.command or "").lower()
        if cmd_name == "clear" and (not st or not st.queue):
            return
        if not await guard(message):
            return
        if not st or (not st.current and not getattr(st, "active_track", None) and not st.queue):
            await message.reply(tr("🔇 Hazırda heç nə oxunmur."))
            return
        n = va.clear_queue(message.chat.id)
        await va.refresh_np(st)
        if n > 0:
            rep = await message.reply(
                tr("🗑 <b>Növbə təmizləndi!</b>\n<i>{n} mahnı növbədən çıxarıldı.</i>", n=n),
                parse_mode="HTML"
            )
        else:
            rep = await message.reply(tr("📋 Növbə onsuz da boşdur."))
        spawn(_autodelete(rep, 10))
        with contextlib.suppress(Exception):
            await message.delete()

    @dp.message(Command("vreload", "reloadvc", "vcreload", "reload", prefix="/!.", ignore_mention=True))
    async def vreload_cmd(message: types.Message, command: CommandObject):
        if message.chat.type not in ("group", "supergroup"):
            return
        cmd_name = (command.command or "").lower()
        st = va.chats.get(message.chat.id)
        if cmd_name == "reload" and not st:
            return
        if not await guard(message):
            return
        if not st or (not st.current and not getattr(st, "active_track", None) and not st.queue):
            rep = await message.reply(tr("🔇 Hazırda aktiv səsli yayım yoxdur."))
            spawn(_autodelete(rep, 10))
            return
        with ui_styled("voice"):
            status = await message.reply(tr("🔄 <b>Səsli yayım bağlantısı yenilənir...</b>"), parse_mode="HTML")
        try:
            await va.reload_chat(message.chat.id)
            with ui_styled("voice"):
                await status.edit_text(tr("✅ <b>Səsli yayım yeniləndi!</b>\n<i>Axın bərpa olundu, növbə qorundu.</i>"), parse_mode="HTML")
            spawn(_autodelete(status, 10))
        except Exception as e:
            with ui_styled("voice"):
                await status.edit_text(f"❌ <b>Xəta:</b> {escape(str(e)[:200])}", parse_mode="HTML")
        with contextlib.suppress(Exception):
            await message.delete()

    @dp.callback_query(F.data.startswith("vp:"))
    async def vp_cb(cb: types.CallbackQuery):
        chat_id = cb.message.chat.id
        st = va.chats.get(chat_id)
        action = cb.data.split(":", 1)[1]

        if not await can_control(chat_id, cb.from_user.id):
            await cb.answer(tr("👮 Yalnız adminlər"), show_alert=True)
            return

        if action == "stop":
            await cb.answer(tr("⏹ Dayandırıldı"))
            if st or chat_id in va.chats or chat_id in va.bind:
                await va._end_chat(chat_id, announce=True, reason=tr("dayandırıldı"))
            else:
                with contextlib.suppress(Exception):
                    await va.calls_for(chat_id).leave(chat_id)
            return

        if not st or (not st.current and not getattr(st, "active_track", None) and not st.queue):
            await cb.answer(tr("Yayım artıq bitib"))
            return

        if action in ("clear", "clearqueue", "cqueue"):
            n = va.clear_queue(chat_id)
            if n > 0:
                await cb.answer(tr("🗑 Növbə təmizləndi ({n} mahnı silindi)", n=n), show_alert=True)
            else:
                await cb.answer(tr("📋 Növbə onsuz da boşdur"), show_alert=True)
            await va.refresh_np(st)
            return

        if action == "refresh":
            await cb.answer(tr("🔄 Yeniləndi"))
            await va.refresh_np(st)
            return

        if action == "skip":
            if getattr(st, "skipping", False):
                await cb.answer(tr("⏳ Növbəti mahnı hazırlanır..."))
                return
            st.skipping = True
            await cb.answer(tr("⏭ Növbəti"))
            try:
                await va.next(st.chat_id, skipped=True)
            finally:
                st.skipping = False
            return

        if action in ("pause", "resume", "toggle"):
            should_pause = not st.paused if action == "toggle" else (action == "pause")
            await cb.answer(tr("⏸ Fasilə") if should_pause else tr("▶️ Davam"))
            if should_pause and not st.paused:
                await va.calls_for(st.chat_id).pause(st.chat_id)
                st.pause()
            elif not should_pause and st.paused:
                await va.calls_for(st.chat_id).resume(st.chat_id)
                st.resume()
            await va.refresh_np(st)
            return

        if action == "radio":
            if st.radio:
                await cb.answer(tr("🔀 Mixplay söndürüldü"))
                st.radio = None
                await va.refresh_np(st)
                return
            await cb.answer(tr("🔀 Mixplay başladılır..."))
            try:
                await do_action(st, action)
            except Exception as e:
                logger.warning(f"🎙 Radio xətası: {e}")
            return

        try:
            await cb.answer()
            await do_action(st, action)
        except Exception as e:
            logger.warning(f"🎙 vp action xətası ({action}): {e}")

    @dp.callback_query(F.data.startswith("vr:"))
    async def vr_cb(cb: types.CallbackQuery):
        if not cb.message:
            await cb.answer()
            return
        chat_id = cb.message.chat.id
        parts = cb.data.split(":")
        if len(parts) < 3:
            return
        action = parts[1]
        try:
            target_chat_id = int(parts[2])
        except ValueError:
            target_chat_id = chat_id

        # icazə əməlin aid olduğu qrupa görə yoxlanır (düymənin olduğu çata görə yox)
        if not await can_control(target_chat_id, cb.from_user.id):
            await cb.answer(tr("👮 Yalnız adminlər"), show_alert=True)
            return

        recoveries = getattr(va, "_pending_recoveries", {})
        data = recoveries.pop(str(target_chat_id), None)

        if action == "dismiss":
            await cb.answer(tr("Ləğv edildi"))
            with contextlib.suppress(Exception):
                with ui_styled("voice"):
                    await cb.message.edit_text(
                        tr("❌ <b>Səsli yayımın bərpası ləğv edildi.</b>"),
                        parse_mode="HTML"
                    )
            spawn(_autodelete(cb.message, 10))
            return

        if action == "resume":
            if not data:
                await cb.answer(tr("Məlumat tapılmadı və ya vaxtı bitib"), show_alert=True)
                with contextlib.suppress(Exception):
                    await cb.message.delete()
                return

            await cb.answer(tr("Yayım bərpa olunur..."))
            with contextlib.suppress(Exception):
                with ui_styled("voice"):
                    await cb.message.edit_text(
                        tr("⏳ <b>Səsli yayım bərpa olunur...</b>"),
                        parse_mode="HTML"
                    )

            cur_data = data.get("current")
            queue_data = data.get("queue") or []

            cur_track = None
            if cur_data:
                cur_track = Track(
                    cur_data["title"],
                    cur_data.get("vid"),
                    cur_data.get("url"),
                    cur_data.get("duration") or 0,
                    cur_data.get("file_id"),
                    cur_data.get("by") or "Bərpa",
                    depo=bool(cur_data.get("file_id")),
                    src="depo" if cur_data.get("file_id") else "yt"
                )
            elif queue_data:
                first = queue_data.pop(0)
                cur_track = Track(
                    first["title"],
                    first.get("vid"),
                    first.get("url"),
                    first.get("duration") or 0,
                    first.get("file_id"),
                    first.get("by") or "Bərpa",
                    depo=bool(first.get("file_id")),
                    src="depo" if first.get("file_id") else "yt"
                )

            if cur_track:
                try:
                    await va.enqueue(chat_id, data.get("title") or (cb.message.chat.title or ""), cur_track)
                    st = va.chats.get(chat_id)
                    if st:
                        for q in queue_data:
                            qt = Track(
                                q["title"],
                                q.get("vid"),
                                q.get("url"),
                                q.get("duration") or 0,
                                q.get("file_id"),
                                q.get("by") or "Bərpa",
                                depo=bool(q.get("file_id")),
                                src="depo" if q.get("file_id") else "yt",
                                user_id=q.get("user_id"),
                                track_id=q.get("track_id"),
                            )
                            st.queue.append(qt)
                        if st.queue:
                            va.prefetch(st.queue[0])
                        va.save_active_sessions()
                        await va.refresh_np(st)
                    with contextlib.suppress(Exception):
                        with ui_styled("voice"):
                            await cb.message.edit_text(
                                tr("✅ <b>Səsli yayım və növbə uğurla bərpa olundu!</b>"),
                                parse_mode="HTML"
                            )
                    spawn(_autodelete(cb.message, 10))
                except Exception as e:
                    logger.warning(f"🎙 Bərpa xətası ({chat_id}): {e}")
                    with contextlib.suppress(Exception):
                        with ui_styled("voice"):
                            await cb.message.edit_text(
                                f"❌ <b>Bərpa xətası:</b> {escape(str(e)[:200])}",
                                parse_mode="HTML"
                            )

    @dp.callback_query(F.data.startswith("vq:del:"))
    async def vq_del_cb(cb: types.CallbackQuery):
        if not cb.message:
            await cb.answer()
            return
        chat_id = cb.message.chat.id
        parts = cb.data.split(":")
        if len(parts) < 3:
            await cb.answer()
            return
        track_id = parts[2]
        st = va.chats.get(chat_id)

        target_track = None
        if st and st.queue:
            for q in list(st.queue):
                if getattr(q, "track_id", None) == track_id:
                    target_track = q
                    break

        if not target_track:
            if st and st.current and getattr(st.current, "track_id", None) == track_id:
                await cb.answer(tr("▶️ Bu mahnı artıq oxunur — keçmək üçün /skip yazın"), show_alert=True)
            else:
                await cb.answer(tr("ℹ️ Mahnı artıq növbədə deyil"), show_alert=True)
            with contextlib.suppress(Exception):
                await cb.message.edit_reply_markup(reply_markup=None)
            return

        user_id = cb.from_user.id
        # Silmə icazəsi: mahnını əlavə edən şəxs VƏ YA admin/creator
        track_owner_id = getattr(target_track, "user_id", None)
        can_del = (track_owner_id is not None and user_id == track_owner_id) or await can_control(chat_id, user_id)
        if not can_del:
            await cb.answer(tr("👮 Bu mahnını yalnız əlavə edən şəxs və ya adminlər silə bilər"), show_alert=True)
            return

        try:
            st.queue.remove(target_track)
        except ValueError:
            pass

        va._drop_file(target_track)
        if st.queue:
            va.prefetch(st.queue[0])
        va.save_active_sessions()

        await cb.answer(tr("🗑 Mahnı növbədən silindi"))
        with contextlib.suppress(Exception):
            with ui_styled("voice"):
                await cb.message.edit_text(
                    tr("🗑 <b>Növbədən silindi:</b>\n{title}\n<i>👤 Silən: {by}</i>",
                       title=escape(target_track.title[:80]),
                       by=escape(cb.from_user.first_name or "istifadəçi")),
                    parse_mode="HTML",
                )
        await va.refresh_np(st)


    # ── 💬 asistanın şəxsi mesaj kartı (inline "va:start") — yalnız asistan hesabının özünə ──

    @dp.inline_query(F.query == "va:start")
    async def va_dm_card(q: types.InlineQuery):
        if q.from_user.id not in va.my_ids():
            await q.answer([], cache_time=0, is_personal=True)
            return
        me = await va._bot_info()
        link = f"https://t.me/{me.username}?start=va"
        await q.answer([InlineQueryResultArticle(
            id="va_start", title="▶️ Botu başlat", description=me.first_name or me.username,
            input_message_content=InputTextMessageContent(
                message_text=DM_TEXT.format(bot_name=escape(me.first_name or me.username)), parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=f"▶️ {me.first_name or 'Botu'} başlat", url=link)]]),
        )], cache_time=0, is_personal=True)

    # ── başlanğıc ──
    async def _boot():
        if old is not None:
            with contextlib.suppress(Exception):
                await old.stop()                         # plugin reload — köhnə client bağlansın
        if va.enabled and va.has_session():
            await va.start()
            spawn(va.check_restart_recovery())
        spawn(va.rec.recover())            # ⏺ restartdan yarımçıq qalan qeydlər

    try:
        spawn(_boot())
    except RuntimeError:
        dp.startup.register(_boot)

    async def _shutdown():
        # proses dayanır: gedən qeydlər diskdə saxlanır — növbəti başlanğıcda asistan bitirib göndərir
        await va.stop(for_restart=True)
    dp.shutdown.register(_shutdown)


async def teardown(context):
    va = getattr(context, "voice_assistant", None)
    if va is not None:
        await va.stop(for_restart=True)
