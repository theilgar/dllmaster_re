"""
bot.menus.handlers.input_router
Mərkəzi FSM parametrləri və mətn/media daxiletmələri üçün mesaj router-i.
"""

import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.core.group_panel import panel_view
from bot.core.welcome import (
    CAPTION_LIMIT,
    TEXT_LIMIT,
    get_media,
    get_text_template,
    set_media,
    set_text_template,
)
from bot.menus.navigation import nav_row
from bot.menus.state import (
    ab_inp,
    get_context,
    grp_inp,
    is_creator,
    panel_editor,
    perf_inp,
    prm,
    rec_inp,
    stp,
    tx,
    us,
    wm,
)
from bot.menus.views.audit import (
    _lg,
    _lgub,
    audit_apply_target,
    audit_open_topics,
    audit_settings_view,
    audit_target_view,
    audit_topic_view,
)
from bot.menus.views.base import (
    LINE,
    SECTIONS,
    crumb,
    display_name,
)
from bot.menus.views.groups import _USER_ADMIN_GROUPS_CACHE
from bot.menus.views.messages import TEXT_SLOTS, ms_panel, send_sample
from bot.menus.views.performance import PERF_PICK_CONFIG, perf_picker_view
from bot.menus.views.premium_stars import (
    MAX_STARS,
    MIN_STARS,
    buy_keyboard,
    duration_panel,
    parse_user_ref,
    set_price,
    stars_plan_panel,
)
from bot.menus.views.stats import fmt_until
from bot.menus.views.system_hub import audio_buttons_picker_view
from common import tiers
from common.database import (
    get_db,
    set_audio_button_enabled,
    set_auto_hide_duration,
)
from common.menu import _btn
from common.permissions import is_group_admin

logger = logging.getLogger(__name__)


# ── 💎 1. Premium istifadəçi daxil edilməsi ──
async def waiting_premium_user(message: Message) -> bool:
    return prm.get("stage") == "await_user" and is_creator(message.from_user.id)


async def premium_user_input(message: Message):
    ctx = get_context()
    kind, value = parse_user_ref(message.text)
    db = get_db()
    back_kb = InlineKeyboardMarkup(inline_keyboard=[nav_row("prm:0")])
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("premium_user_input message delete xətası: %r", _e)

    prm_edit_panel = panel_editor(ctx, prm, "Premium")

    if kind == "id":
        user_id, u = value, await asyncio.to_thread(db.get_user, value)
    elif kind == "username":
        u = await asyncio.to_thread(db.find_user_by_username, value)
        if not u:
            await prm_edit_panel(
                f"💎 <b>Premium ver</b>\n\n❌ <b>@{escape(value)}</b> tapılmadı.\n\n"
                "<i>Username ilə vermək üçün istifadəçi əvvəlcə botu /start etməlidir "
                "(Telegram botlara başqasının username-ini ID-yə çevirməyə icazə vermir).\n"
                "Başqa username və ya rəqəmli ID yazın:</i>",
                back_kb,
            )
            return
        user_id = u["user_id"]
    else:
        await prm_edit_panel(
            "💎 <b>Premium ver</b>\n\n❌ Düzgün format deyil.\n\n"
            "<i>Rəqəmli ID (məs. <code>123456789</code>) və ya username (məs. <code>@username</code>) yazın:</i>",
            back_kb,
        )
        return

    prm["stage"] = "choose"
    text, kb = await duration_panel(user_id, u)
    await prm_edit_panel(text, kb)


# ── 💎 2. /premium əmri ──
async def premium_status(message: Message):
    uid = message.from_user.id
    if is_creator(uid):
        st = [(tiers.FEATURES[f][0], tiers.get(f, "creator")) for f in tiers.ORDER]
        text = (
            "👑 <b>Status: Creator</b>\n\n"
            + "\n".join(f"{'✅' if on else '❌'} {name}" for name, on in st)
            + "\n\n<i>/menu → 💎 Premium → 🎛 İmkanlar-dan dəyişilir</i>"
        )
    else:
        cur = await asyncio.to_thread(get_db().get_premium, uid)
        lifetime = bool(cur) and cur["until"] is None
        kb = await asyncio.to_thread(buy_keyboard, lifetime)
        if cur:
            text = (
                f"💎 <b>Status: Premium</b>\n⏳ {fmt_until(cur['until'])}\n\n"
                f"✨ <b>Sənin imkanların:</b>\n{tiers.perks_html()}"
                + ("\n\n⭐ <b>Uzatmaq üçün plan seç</b> — müddət üstünə əlavə olunur:" if kb else "")
            )
        else:
            text = (
                "🆓 <b>Status: Free</b>\n\n"
                f"💎 <b>Premium ilə əldə edəcəklərin:</b>\n{tiers.perks_html()}"
                + ("\n\n⭐ <b>Telegram Stars ilə premium al:</b>" if kb else "")
            )
        try:
            from bot.plugins.compact_ui_plugin import styled

            ctx_m = styled("user")
        except Exception:
            ctx_m = contextlib.nullcontext()
        with ctx_m:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return
    try:
        from bot.plugins.compact_ui_plugin import styled

        ctx_m = styled("admin")
    except Exception:
        ctx_m = contextlib.nullcontext()
    with ctx_m:
        await message.answer(text, parse_mode="HTML")


# ── 🖼 3. Media daxil edilməsi ──
async def waiting_media(message: Message) -> bool:
    return wm.get("stage") == "await_media" and is_creator(message.from_user.id)


async def welcome_media_input(message: Message):
    ctx = get_context()
    slot = wm.get("slot", "welcome")
    back = wm.get("back", "welcome")
    if message.animation:
        kind, file_id = "animation", message.animation.file_id
    elif message.video:
        kind, file_id = "video", message.video.file_id
    else:
        kind, file_id = "photo", message.photo[-1].file_id
    await asyncio.to_thread(set_media, slot, kind, file_id)
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("welcome_media_input delete message xətası: %r", _e)
    text, kb = ms_panel(back, "✅ <b>Yeni media saxlandı.</b> Yoxlamaq üçün 👁 Önizləmə bas.")
    wm_edit_panel = panel_editor(ctx, wm, "Media")
    await wm_edit_panel(text, kb)
    wm["stage"] = None
    logger.info("Media dəyişdi (%s): %s", slot, kind)


async def welcome_media_wrong(message: Message):
    if message.text and message.text.startswith("/"):
        return
    await message.reply(
        "🖼 Şəkil, GIF və ya video göndərin.\n"
        '<i>Şəkli "fayl kimi" yox, adi şəkil kimi göndərin.</i>',
        parse_mode="HTML",
    )


# ── 📝 4. Mətn şablonu daxil edilməsi ──
async def waiting_tx_text(message: Message) -> bool:
    return tx.get("stage") == "await_text" and is_creator(message.from_user.id)


async def tx_text_input(message: Message):
    ctx = get_context()
    slot = tx.get("slot")
    template = message.html_text.strip()
    if len(template) > TEXT_LIMIT - 200:
        await message.reply(
            f"❌ Mətn çox uzundur ({len(template)} simvol). Maksimum ~{TEXT_LIMIT - 200}."
        )
        return

    old = get_text_template(slot)
    await asyncio.to_thread(set_text_template, slot, template)
    try:
        rendered = await send_sample(
            message.chat.id,
            slot,
            message.from_user.first_name or "Dostum",
            message.from_user.id,
        )
    except Exception as e:
        await asyncio.to_thread(set_text_template, slot, old)
        await message.reply(
            f"❌ Bu mətn göndərilə bilmir, saxlanmadı:\n<code>{escape(str(e))[:300]}</code>",
            parse_mode="HTML",
        )
        return

    tx["stage"] = None
    note = "✅ <b>Yeni mətn saxlandı.</b> Yuxarıda istifadəçilərin görəcəyi formadır."
    if get_media(TEXT_SLOTS[slot]["media"])["type"] != "none" and len(rendered) > CAPTION_LIMIT:
        note += (
            f"\n⚠️ Mətn {len(rendered)} simvoldur — media ilə göndərmək üçün maksimum "
            f"{CAPTION_LIMIT}. Bu halda media olmadan, yalnız mətn gedəcək."
        )
    text, kb = ms_panel(slot, note)
    tx_edit_panel = panel_editor(ctx, tx, "Mesajlar")
    await tx_edit_panel(text, kb)
    logger.info("Mətn dəyişdi (%s): %s simvol", slot, len(template))


# ── 🔍 5. İstifadəçi axtarışı ──
async def waiting_user_query(message: Message) -> bool:
    return us.get("stage") == "await_query" and is_creator(message.from_user.id)


async def user_search_input(message: Message):
    ctx = get_context()
    query = message.text.strip()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("user_search_input delete message xətası: %r", _e)
    now = int(time.time())
    rows = await asyncio.to_thread(get_db().search_users, query, now)
    kb = [
        [
            _btn(
                f"{'💎 ' if u['premium'] else ''}{'🚫 ' if u['blocked'] else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
                f"user:{u['user_id']}:usearch_res",
            )
        ]
        for u in rows
    ]
    kb.append([_btn("🔍 Yenidən axtar", "usearch")])
    kb.append(nav_row("users:all:0"))
    us["last_query"] = query
    us["stage"] = None
    if rows:
        text = f"🔍 <b>\"{escape(query)}\"</b> üzrə <b>{len(rows)}</b> nəticə\n\n<i>Ətraflı məlumat üçün bas.</i>"
    else:
        text = f"🔍 <b>\"{escape(query)}\"</b> üzrə heç kim tapılmadı."
    try:
        await ctx.bot.edit_message_text(
            chat_id=us["chat_id"],
            message_id=us["panel_id"],
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )
    except Exception as e:
        logger.warning("Axtarış paneli yenilənmədi: %s", e)


# ── 🎛 6. Mahnı düymələri taymeri ──
async def waiting_ab_input(message: Message) -> bool:
    return ab_inp.get("stage") == "await_sec" and is_creator(message.from_user.id)


async def audio_buttons_timer_input(message: Message):
    ctx = get_context()
    raw = message.text.strip().lower()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("audio_buttons_timer_input delete message xətası: %r", _e)
    panel_args = dict(chat_id=ab_inp["chat_id"], message_id=ab_inp["panel_id"], parse_mode="HTML")

    note = ""
    if raw in ("-", "off", "bagli", "bağlı"):
        set_audio_button_enabled("mix", False)
        set_audio_button_enabled("plus", False)
        set_audio_button_enabled("hide", False)
        note = "🔴 Mahnı düymələri söndürüldü (Off)"
    elif raw in ("+", "sonsuz", "inf", "unlimited", "daimi"):
        set_auto_hide_duration(0)
        set_audio_button_enabled("mix", True)
        set_audio_button_enabled("plus", True)
        set_audio_button_enabled("hide", True)
        note = "✅ Taymer: Daimi (sonsuz — heç vaxt silinmir)"
    elif raw.isdigit():
        val = max(0, min(3600, int(raw)))
        set_auto_hide_duration(val)
        t_txt = "Daimi (silinmir)" if val == 0 else f"{val} saniyə"
        note = f"✅ Taymer: {t_txt}"
    else:
        text = (
            crumb(SECTIONS["mus"]) + " › 🎛 <b>Mahnı düymələri › Taymer</b>\n"
            + LINE + "\n"
            f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
            "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
            "• <b>Rəqəm</b> (məs: <code>20</code>)\n"
            "• <b>+</b> (sonsuz / Daimi)\n"
            "• <b>-</b> (off / bağlı)\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✖️ Ləğv et", callback_data="kb_btn:pick")]]
        )
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    ab_inp["stage"] = None
    text, kb = audio_buttons_picker_view(note)
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)


# ── ⚙️ 7. Performans dəyəri daxil edilməsi ──
async def waiting_perf_input(message: Message) -> bool:
    return perf_inp.get("stage") == "await_val" and is_creator(message.from_user.id)


async def perf_value_input(message: Message):
    ctx = get_context()
    from bot.core import cpu_pool as cp

    key = perf_inp.get("key")
    if isinstance(key, str) and key.startswith("rmt_"):          # 🌐 Uzaq server: ünvan / port / açar
        from bot.menus.handlers.remote_flow import on_text
        await on_text(message, key[4:])
        return
    cfg = PERF_PICK_CONFIG.get(key)
    if not cfg:
        perf_inp["stage"] = None
        return
    raw = message.text.strip().lower()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("perf_value_input delete message xətası: %r", _e)
    panel_args = dict(chat_id=perf_inp["chat_id"], message_id=perf_inp["panel_id"], parse_mode="HTML")
    unit = f" {cfg['unit']}" if cfg["unit"] else ""
    note = ""
    if raw in ("-", "min", "off"):
        v = cfg["setter"](cp, cfg["min"])
        note = f"✅ {cfg['title']}: {v}{unit} (minimum)"
    elif raw in ("+", "max", "sonsuz"):
        v = cfg["setter"](cp, cfg["max"])
        note = f"✅ {cfg['title']}: {v}{unit} (maksimum)"
    elif raw.isdigit():
        val = int(raw)
        v = cfg["setter"](cp, val)
        note = f"✅ {cfg['title']}: {v}{unit}"
    else:
        text = (
            crumb(SECTIONS["sys"]) + f" › ⚙️ <b>Performans › {cfg['title']}</b>\n"
            + LINE + "\n"
            f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
            "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
            f"• <b>Rəqəm</b> (min: <code>{cfg['min']}</code>, maks: <code>{cfg['max']}</code>)\n"
            f"• <b>+</b> (maksimum: {cfg['max']})\n"
            f"• <b>-</b> (minimum: {cfg['min']})\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[_btn("✖️ Ləğv et", f"perf:pick:{key}")]]
        )
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    perf_inp["stage"] = None
    text, kb = perf_picker_view(key, note)
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)


# ── 🎙 8. REC hədəf daxil edilməsi ──
async def waiting_rec_input(message: Message) -> bool:
    return rec_inp.get("stage") == "await_target" and is_creator(message.from_user.id)


async def rec_target_input(message: Message):
    ctx = get_context()
    raw = message.text.strip()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("rec_target_input delete message xətası: %r", _e)

    panel_args = dict(chat_id=rec_inp["chat_id"], message_id=rec_inp["panel_id"], parse_mode="HTML")
    rec_inp["stage"] = None
    au_stream = rec_inp.pop("stream", None)
    gcid = rec_inp.pop("gcid", None)
    origin = rec_inp.pop("origin", None)
    vrc_cid = rec_inp.pop("vrc_cid", 0)

    if origin == "vrc":
        from bot.plugins import voice_assistant_plugin

        va_obj = getattr(voice_assistant_plugin, "va", None) or getattr(ctx, "voice_assistant", None)
        s = "vc"
        lgm, ubm = _lg(), _lgub()
        try:
            note = await audit_apply_target(s, raw)
            if ubm and lgm:
                lgm.reset_stream_target(ubm, "tr")
                lgm.reset_stream_target(ubm, "spl")
        except Exception as e:
            note = f"❌ {escape(str(e)[:150])}"
        if va_obj and hasattr(va_obj, "rec"):
            text, kb = va_obj.rec.target_view(vrc_cid, s, note)
            chat, topic, _ = (
                lgm.get_stream_target(ubm, s) if lgm and ubm else (None, None, None)
            )
            if note.startswith("✅") and chat and not topic:
                if await audit_open_topics(s, chat) is not None:
                    text, kb = va_obj.rec.topic_view(
                        vrc_cid, s, note + " — indi topic seç (və ya 💬 General)"
                    )
        else:
            text, kb = "❌ Canlı yayım asistanı aktiv deyil", None
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    if au_stream and gcid == "add":                  # ➕ qrup əlavə et
        from bot.menus.views.audit import audit_groups_view, audit_gview

        lgm, ubm = _lg(), _lgub()
        from bot.menus.views.audit import tg_client

        client = tg_client()
        try:
            chat, _topic, err = await lgm.resolve_target(client, raw, 0)
            if err or not chat or chat == "me" or not str(chat).startswith("-"):
                text, kb = audit_groups_view(0, f"❌ Qrup tapılmadı: {escape(err or 'qrup / kanal ID-si olmalıdır')}")
            else:
                title = ""
                with contextlib.suppress(Exception):
                    ent = await client.get_entity(int(chat))
                    title = getattr(ent, "title", "") or ""
                lgm.add_group(ubm, int(chat), title)
                text, kb = audit_gview(int(chat), "✅ Qrup auditə əlavə edildi")
        except Exception as e:
            text, kb = audit_groups_view(0, f"❌ {escape(str(e)[:120])}")
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    if au_stream and gcid is not None:               # 🏘 qrup üzrə hədəf
        from bot.menus.views.audit import audit_gtarget_view, audit_gtopic_view, audit_open_gtopics

        try:
            note = await audit_apply_target(au_stream, raw, gcid)
        except Exception as e:
            note = f"❌ {escape(str(e)[:150])}"
        text, kb = audit_gtarget_view(gcid, au_stream, note)
        lgm, ubm = _lg(), _lgub()
        chat, topic, _ = lgm.get_stream_target(ubm, au_stream, gcid)
        if note.startswith("✅") and chat and not topic:
            if await audit_open_gtopics(gcid, au_stream, chat) is not None:
                text, kb = audit_gtopic_view(gcid, au_stream, note + " — indi topic seç (və ya 💬 General)")
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    if au_stream:
        try:
            note = await audit_apply_target(au_stream, raw)
        except Exception as e:
            note = f"❌ {escape(str(e)[:150])}"
        text, kb = audit_target_view(au_stream, note)
        lgm, ubm = _lg(), _lgub()
        chat, topic, _ = lgm.get_stream_target(ubm, au_stream)
        if note.startswith("✅") and chat and not topic:
            if await audit_open_topics(au_stream, chat) is not None:
                text, kb = audit_topic_view(
                    au_stream, note + " — indi topic seç (və ya 💬 General)"
                )
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    text, kb = audit_settings_view(
        "⚠️ Hədəf seçimi vaxtı keçib — yenidən 🎯 Hədəf düyməsinə bas"
    )
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)


# ── ⭐ 9. Stars qiymət daxil edilməsi ──
async def waiting_price(message: Message) -> bool:
    return stp.get("stage") == "await_price" and is_creator(message.from_user.id)


async def stars_price_input(message: Message):
    ctx = get_context()
    key = stp.get("plan")
    raw = message.text.strip().replace("⭐", "").replace(" ", "").lower()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("stars_price_input delete message xətası: %r", _e)
    panel_args = dict(chat_id=stp["chat_id"], message_id=stp["panel_id"], parse_mode="HTML")
    if raw in ("-", "off", "bagli", "bağlı", "0"):
        await asyncio.to_thread(set_price, key, None)
        stp["stage"] = None
        text, kb = stars_plan_panel(key, "🚫 Satışdan çıxarıldı (off)")
        try:
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        except Exception as e:
            logger.warning("Ulduz paneli yenilənmədi: %s", e)
        logger.info("⭐ Qiymət söndürüldü (off): %s", key)
        return

    if raw in ("+", "max"):
        raw = str(MAX_STARS)

    if not raw.isdigit() or not (MIN_STARS <= int(raw) <= MAX_STARS):
        text, kb = stars_plan_panel(
            key,
            f"❌ <b>{escape(message.text[:20])}</b> düzgün deyil. "
            f"{MIN_STARS}–{MAX_STARS} arası rəqəm, yaxud satışdan çıxarmaq üçün '-' yazın:",
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_plan:{key}")])
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return
    await asyncio.to_thread(set_price, key, int(raw))
    stp["stage"] = None
    text, kb = stars_plan_panel(key, f"✅ Qiymət saxlandı: <b>{int(raw)}⭐</b>")
    try:
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
    except Exception as e:
        logger.warning("Ulduz paneli yenilənmədi: %s", e)
    logger.info("⭐ Qiymət dəyişdi: %s = %s", key, raw)


# ── 🏷 Endirim vaxtı daxil edilməsi ──
async def waiting_sale_time(message: Message) -> bool:
    return str(stp.get("stage") or "").startswith("await_sale_") and is_creator(message.from_user.id)


async def sale_time_input(message: Message):
    from bot.core.stars import parse_sale_time, set_sale
    from bot.menus.views.premium_stars import sale_panel
    ctx = get_context()
    which = stp["stage"].replace("await_sale_", "")
    with contextlib.suppress(Exception):
        await message.delete()
    panel_args = dict(chat_id=stp["chat_id"], message_id=stp["panel_id"], parse_mode="HTML")
    try:
        ts = parse_sale_time(message.text)
    except ValueError:
        text, kb = sale_panel(f"❌ <b>{escape(message.text[:30])}</b> başa düşülmədi. "
                              "Nümunə: <code>25.12.2026 18:00</code>, <code>18:00</code>, <code>+3g</code> və ya <code>-</code>")
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return
    await asyncio.to_thread(set_sale, which, ts)
    stp["stage"] = None
    note = "✅ Saxlanıldı" + ("" if ts else " — məhdudiyyət yoxdur")
    if which == "end" and ts:
        s = sale_settings_snapshot()
        if s["start"] and ts <= s["start"]:
            note += "\n⚠️ Bitmə vaxtı başlamadan əvvəldir!"
    text, kb = sale_panel(note)
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)


def sale_settings_snapshot():
    from bot.core.stars import sale_settings
    return sale_settings()


# ── 👥 10. Qrup ID / Link daxil edilməsi ──
async def waiting_grp_input(message: Message) -> bool:
    st = grp_inp.get(message.from_user.id)
    if st and st.get("until", 0) < time.time():
        grp_inp.pop(message.from_user.id, None)
        return False
    return st is not None


async def group_id_text_input(message: Message):
    ctx = get_context()
    st = grp_inp.pop(message.from_user.id, None)
    raw = message.text.strip()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("udulan xəta [bot/menus/handlers/input_router.py:578]: %r", _e)

    panel_kwargs = {"chat_id": st["chat_id"], "message_id": st["panel_id"]} if st else {}

    target = None
    if raw.startswith("-100") or (raw.startswith("-") and raw[1:].isdigit()) or raw.isdigit():
        try:
            target = int(raw)
        except ValueError:
            pass
    else:
        target = raw if raw.startswith("@") else f"@{raw}"

    if not target:
        text = "❌ <b>Düzgün qrup ID və ya @istifadəçi adı daxil edilmədi!</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Yenidən cəhd et", callback_data="pm_grp:input_id")],
                [InlineKeyboardButton(text="⬅️ Qruplar", callback_data="umenu:groups")],
            ]
        )
        if panel_kwargs:
            with contextlib.suppress(Exception):
                await ctx.bot.edit_message_text(
                    text=text, reply_markup=kb, parse_mode="HTML", **panel_kwargs
                )
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    try:
        chat = await ctx.bot.get_chat(target)
    except Exception as e:
        text = (
            f"❌ <b>Qrup tapılmadı!</b>\n\n"
            f"Səbəb: <i>{escape(str(e)[:150])}</i>\n\n"
            "• Botun həmin qrupa əlavə edildiyindən əmin olun.\n"
            "• Qrup gizlidirsə (private supergroup), link əvəzinə rəqəmsal ID (məs: <code>-100...</code>) yazın."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Yenidən cəhd et", callback_data="pm_grp:input_id")],
                [InlineKeyboardButton(text="⬅️ Qruplar", callback_data="umenu:groups")],
            ]
        )
        if panel_kwargs:
            with contextlib.suppress(Exception):
                await ctx.bot.edit_message_text(
                    text=text, reply_markup=kb, parse_mode="HTML", **panel_kwargs
                )
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    cid = chat.id
    uid = message.from_user.id
    is_adm = await is_group_admin(ctx.bot, cid, uid)
    if not is_adm and not is_creator(uid):
        text = (
            f"👮 <b>İcazə yoxdur!</b>\n\n"
            f"Siz <b>{escape(chat.title or str(cid))}</b> qrupunda admin deyilsiniz."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Qruplar", callback_data="umenu:groups")]
            ]
        )
        if panel_kwargs:
            with contextlib.suppress(Exception):
                await ctx.bot.edit_message_text(
                    text=text, reply_markup=kb, parse_mode="HTML", **panel_kwargs
                )
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
        return

    get_db().upsert_chat(cid, chat.type, chat.title, getattr(chat, "username", None), True)
    _USER_ADMIN_GROUPS_CACHE.pop(uid, None)

    text, kb = await panel_view(ctx.bot, cid, uid, chat.title, private=True)
    if panel_kwargs:
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(
                text=text, reply_markup=kb, parse_mode="HTML", **panel_kwargs
            )
            return
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def register_input_handlers(router: Router):
    router.message.register(
        premium_user_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_premium_user,
    )
    router.message.register(
        premium_status,
        Command("premium"),
        F.chat.type == "private",
    )
    router.message.register(
        welcome_media_input,
        F.chat.type == "private",
        F.photo | F.animation | F.video,
        waiting_media,
    )
    router.message.register(
        welcome_media_wrong,
        F.chat.type == "private",
        ~F.photo,
        ~F.animation,
        ~F.video,
        waiting_media,
    )
    router.message.register(
        tx_text_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_tx_text,
    )
    router.message.register(
        user_search_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_user_query,
    )
    router.message.register(
        audio_buttons_timer_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_ab_input,
    )
    router.message.register(
        perf_value_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_perf_input,
    )
    router.message.register(
        rec_target_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_rec_input,
    )
    router.message.register(
        sale_time_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_sale_time,
    )
    router.message.register(
        stars_price_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_price,
    )
    router.message.register(
        group_id_text_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_grp_input,
    )
