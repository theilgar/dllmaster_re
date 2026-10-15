"""
bot.menus.handlers.menu_router
Mərkəzi Menyu callback query (menu:*, hc:*, kb_btn:*) marşrutlaşdırıcısı.
"""

import asyncio
import contextlib
import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot.menus.handlers.audit_flow import _mcb_au
from bot.menus.handlers.ads_flow import _mcb_ads
from bot.menus.handlers.maint_flow import _mcb_maint
from bot.menus.handlers.tipmsg_flow import _mcb_tipmsg
from bot.menus.handlers.broadcast_flow import (
    _mcb_bc,
    _mcb_bc_cancel,
    _mcb_bc_edit,
    _mcb_bc_send,
    _mcb_bc_stop,
    _mcb_bc_t,
)
from bot.menus.handlers.group_pm_flow import handle_chat_users_action
from bot.menus.handlers.performance_flow import _mcb_perf
from bot.menus.handlers.settings_flow import handle_settings_action
from bot.menus.handlers.spotify_flow import _mcb_sp
from bot.menus.handlers.stars_flow import _mcb_st_p, _mcb_st_plan
from bot.menus.handlers.system_flow import handle_system_action
from bot.menus.handlers.youtube_flow import (
    _mcb_dlf,
    _mcb_srch,
    _mcb_web,
    _mcb_yt,
    _mcb_yta,
)
from bot.menus.views.music import mus_view, shazam_info_view, tags_info_view
from bot.menus.views.userbot import amsg_view, backup_view, filter_view, profile_view
from bot.menus.state import (
    ab_inp,
    bc,
    bc_reset,
    delete_preview,
    get_context,
    rec_inp,
    reset_inputs,
    safe_edit_cb,
)
from bot.menus.views.audit import audit_settings_view
from bot.menus.views.base import (
    LINE,
    SECTIONS,
    crumb,
)
from bot.menus.views.dev import health_view
from bot.menus.views.groups import user_groups_view
from bot.menus.views.home import main_kb, main_text
from bot.menus.views.premium_stars import (
    payments_view,
    stars_panel,
)
from bot.menus.views.server import srv_hub_view
from bot.menus.views.stats import stats_view
from bot.menus.views.system_hub import (
    arch_view,
    aud_view,
    audio_buttons_picker_view,
    audio_buttons_view,
    biz_view,
    bots_view,
    com_view,
    depo_view,
    fetch_sessions,
    help_view,
    live_view,
    mod_cmds_view,
    start_fetch_live,
    stop_fetch_on,
    ub_no_inline_list_view,
)
from common.database import (
    is_userbot_inline_disabled,
    set_userbot_inline_disabled,
)
from common.utilities import ui_styled

logger = logging.getLogger(__name__)


# ── 🩺 Health callback ──
async def health_cb(cb: CallbackQuery):
    ctx = get_context()
    doctor = getattr(ctx, "doctor", None)
    if doctor is None:
        try:
            from common import doctor
        except Exception:
            doctor = None
    if doctor is None:
        await cb.answer("❌ doctor yüklənmədi", show_alert=True)
        return
    act = cb.data.split(":", 1)[1]
    note = ""
    if act == "fix":
        await cb.answer("🔧 Quraşdırılır...")
        await safe_edit_cb(
            cb, "🩺 <b>Sağlamlıq</b>\n\n⏳ <i>Çatışmayan paketlər quraşdırılır...</i>"
        )
        try:
            note = escape(await doctor.install_missing())
        except Exception as e:
            note = f"❌ <code>{escape(str(e))[:200]}</code>"
        ctx._health_cache = await asyncio.to_thread(doctor.run_all)
    else:
        await cb.answer(
            "🔄 Yoxlanılır..."
            if act == "refresh" or not getattr(ctx, "_health_cache", None)
            else None
        )
        ctx._health_cache = await asyncio.to_thread(doctor.run_all)
    await safe_edit_cb(cb, *health_view(note))


# ── 🎛 Mahnı düymələri callback ──
async def audio_buttons_callback(cb: CallbackQuery):
    if not cb.message:
        await cb.answer()
        return

    parts = cb.data.split(":")
    act = parts[1]

    from common.database import (
        cycle_auto_hide_duration,
        get_auto_hide_duration,
        is_audio_button_enabled,
        reset_audio_buttons,
        set_audio_button_enabled,
        step_auto_hide_duration,
    )

    if act == "panel":
        ab_inp["stage"] = None
        await cb.answer()
        await safe_edit_cb(cb, *audio_buttons_view())
        return

    if act in ("pick", "inp"):
        ab_inp["stage"] = None
        await cb.answer()
        await safe_edit_cb(cb, *audio_buttons_picker_view())
        return

    if act == "vstep":
        delta = int(parts[2]) if len(parts) > 2 else 1
        new_v = step_auto_hide_duration(delta)
        t_txt = "Daimi (silinmir)" if new_v == 0 else f"{new_v} saniyə"
        await cb.answer(f"⏱ {t_txt}")
        await safe_edit_cb(cb, *audio_buttons_picker_view(f"✓ Taymer: {t_txt}"))
        return

    if act == "vinp":
        await cb.answer()
        ab_inp.update(
            stage="await_sec",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        sec = get_auto_hide_duration()
        t_txt = "Daimi (silinmir)" if sec == 0 else f"{sec} saniyə"
        text = (
            crumb(SECTIONS["mus"]) + " › 🎛 <b>Mahnı düymələri › Taymer</b>\n"
            + LINE + "\n"
            f"Hazırkı gizlənmə vaxtı: <b>{t_txt}</b>\n\n"
            "✍️ <b>Gizlənmə vaxtını daxil edin:</b>\n"
            "• <b>Rəqəm</b> yazın (məs: <code>20</code>) — həmin saniyə təyin edilsin\n"
            "• <b>+</b> yazın — <b>sonsuz (Daimi)</b> olsun (silinmir)\n"
            "• <b>-</b> yazın — <b>off (bağlı)</b> olsun (düymələr gizlədilsin)\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✖️ Ləğv et", callback_data="kb_btn:pick")]]
        )
        await safe_edit_cb(cb, text, kb)
        return

    if act == "t":
        btn_name = parts[2] if len(parts) > 2 else ""
        if btn_name in ("mix", "plus", "hide"):
            cur = is_audio_button_enabled(btn_name)
            new_state = not cur
            set_audio_button_enabled(btn_name, new_state)
            lbl = {"mix": "🔀 Mix", "plus": "➕ Playlist", "hide": "🙈 Gizlət"}.get(
                btn_name, btn_name
            )
            st = "AÇILDI (göstərilir)" if new_state else "BAĞLANDI (gizlədildi)"
            await cb.answer(f"{lbl} düyməsi {st}")
            await safe_edit_cb(cb, *audio_buttons_view())
        else:
            await cb.answer()
        return

    if act == "cycle":
        new_sec = cycle_auto_hide_duration(1)
        t_txt = "Daimi (silinmir)" if new_sec == 0 else f"{new_sec} saniyə"
        await cb.answer(f"⏱ Gizlənmə müddəti: {t_txt}")
        await safe_edit_cb(cb, *audio_buttons_view())
        return

    if act == "step":
        delta = int(parts[2]) if len(parts) > 2 else 5
        new_sec = step_auto_hide_duration(delta)
        t_txt = "Daimi (silinmir)" if new_sec == 0 else f"{new_sec} saniyə"
        await cb.answer(f"⏱ Gizlənmə müddəti: {t_txt}")
        await safe_edit_cb(cb, *audio_buttons_view())
        return

    if act == "reset":
        reset_audio_buttons()
        await cb.answer(
            "♻️ Düymə ayarları ilkin vəziyyətə qaytarıldı (hamısı açıq, 15 san)"
        )
        await safe_edit_cb(cb, *audio_buttons_view())
        return

    await cb.answer()


# ── 👑 /menu alt handlerləri ──
async def _mcb_fetch(cb: CallbackQuery, parts: list, action: str):
    key = (cb.message.chat.id, cb.message.message_id)
    sess = fetch_sessions.get(key)
    if action == "fetch_pause" and sess:
        sess["paused"] = not sess["paused"]
        if not sess["paused"]:
            sess["until"] = max(sess["until"], time.time() + 60)
        await cb.answer("⏸ Fasilə" if sess["paused"] else "▶️ Davam")
        return
    if action == "fetch_stop" and sess:
        sess["stop"] = True
        await cb.answer("⏹ Dayandırıldı")
        return
    await cb.answer()
    await safe_edit_cb(cb, "🖥 <b>Fastfetch</b>\n\n⏳ <i>Yoxlanılır...</i>")
    start_fetch_live(key, cb.message)



# ── 🌐 Mərkəzi Menu Callback ──
async def menu_callback(cb: CallbackQuery):
    if not cb.message and not cb.inline_message_id:
        await cb.answer()
        return

    parts = cb.data.split(":")
    action = parts[1]
    if action not in ("fetch", "fetch_pause", "fetch_stop"):
        if cb.message:
            stop_fetch_on(cb.message)

    if action.startswith("bc") and bc.get("stage") == "sending" and action != "bc_stop":
        await cb.answer("Broadcast hələ gedir...", show_alert=True)
        return

    ctx = get_context()

    if action == "noop":
        await cb.answer()
        return

    simple = {
        "bc": lambda: _mcb_bc(cb, parts, action),
        "bc_t": lambda: _mcb_bc_t(cb, parts, action),
        "bc_edit": lambda: _mcb_bc_edit(cb),
        "bc_cancel": lambda: _mcb_bc_cancel(cb),
        "bc_send": lambda: _mcb_bc_send(cb),
        "bc_s": lambda: _mcb_bc_send(cb),
        "bc_stop": lambda: _mcb_bc_stop(cb),
        "tipmsg": lambda: _mcb_tipmsg(cb, parts, action),
        "maint": lambda: _mcb_maint(cb, parts, action),
        "ads": lambda: _mcb_ads(cb, parts, action),
    }
    if action in simple:
        await simple[action]()
        return

    if action == "main":
        await cb.answer()
        if bc.get("stage") and bc.get("stage") != "sending":
            await delete_preview()
            bc_reset()
        reset_inputs()
        from bot.menus.navigation import clear_nav
        clear_nav(cb.from_user.id)
        await safe_edit_cb(cb, await asyncio.to_thread(main_text, force=True), main_kb())   # COUNT sorğuları loop-da olmasın
        return

    if action == "back":
        await cb.answer()
        from bot.menus.navigation import pop_nav
        target = pop_nav(cb.from_user.id)
        if target == "main":
            reset_inputs()
            await safe_edit_cb(cb, await asyncio.to_thread(main_text, force=True), main_kb())
            return
        action = target
        parts = ["menu", target]

    if action not in ("close", "cancel", "noop"):
        from bot.menus.navigation import push_nav
        push_nav(cb.from_user.id, action)

    if action in ("groups", "grp_mgmt"):
        await cb.answer()
        text, kb = await user_groups_view(cb.bot, cb.from_user.id, page=0)
        await safe_edit_cb(cb, text, kb)
        return

    if await handle_settings_action(cb, parts, action):
        return

    if await handle_system_action(cb, parts, action):
        return

    if await handle_chat_users_action(cb, parts, action):
        return

    if action in ("close", "cancel"):
        await cb.answer()
        reset_inputs()
        try:
            await cb.message.delete()
        except Exception:
            await safe_edit_cb(cb, "✖️ Bağlandı. Yenidən açmaq üçün /menu")
        return

    elif action in (
        "aud",
        "biz",
        "mus",
        "depo",
        "live",
        "help",
        "com",
        "bots",
        "mod_cmds",
        "shazam_info",
        "tags_info",
        "ub_no_inline_list",
        "ub_amsg",
        "ub_filter",
        "ub_profile",
        "ub_backup",
        "srv",
        "arch",
    ):
        await cb.answer()
        if bc.get("stage") and bc.get("stage") != "sending":
            await delete_preview()
            bc_reset()
        reset_inputs()
        view = {
            "aud": aud_view,
            "biz": biz_view,
            "mus": mus_view,
            "depo": depo_view,
            "live": live_view,
            "help": help_view,
            "com": com_view,
            "bots": bots_view,
            "mod_cmds": mod_cmds_view,
            "shazam_info": shazam_info_view,
            "tags_info": tags_info_view,
            "ub_no_inline_list": ub_no_inline_list_view,
            "ub_amsg": amsg_view,
            "ub_filter": filter_view,
            "ub_profile": profile_view,
            "ub_backup": backup_view,
            "srv": srv_hub_view,
            "arch": arch_view,
        }[action]
        await safe_edit_cb(cb, *view())

    elif action == "ub_no_inline_toggle" and len(parts) > 2:
        try:
            target_cid = int(parts[2])
        except ValueError:
            await cb.answer()
            return
        cur_dis = is_userbot_inline_disabled(target_cid)
        new_dis = not cur_dis
        set_userbot_inline_disabled(target_cid, new_dis)
        note = (
            f"🟢 Çat ({target_cid}) üçün inline aktiv edildi"
            if not new_dis
            else f"🔴 Çat ({target_cid}) üçün inline söndürüldü"
        )
        await cb.answer(note)
        await safe_edit_cb(cb, *ub_no_inline_list_view(note))
        return

    elif action in ("health", "hc"):
        await cb.answer()
        text, kb = health_view(back="main")
        await safe_edit_cb(cb, text, kb)

    elif action in ("vcrec", "vrec", "rec"):
        va = getattr(ctx, "voice_assistant", None)
        if va and getattr(va, "rec", None):
            if hasattr(va.rec, "sessions") and len(va.rec.sessions) == 1:
                cid_act, s_act = next(iter(va.rec.sessions.items()))
                text, kb = await va.rec.panel(cid_act, s_act.title)
            else:
                text, kb = va.rec.groups_view()
            await safe_edit_cb(cb, text, kb)
            return
        await safe_edit_cb(cb, *live_view())
        return

    elif action in ("audit_hub", "audit") or action.startswith("rec_"):
        rec_inp["stage"] = None
        with contextlib.suppress(Exception):
            await cb.answer()
        await safe_edit_cb(cb, *(live_view() if action.startswith("rec_") else audit_settings_view()))
        return

    elif action == "au":
        await _mcb_au(cb, parts, action)

    elif action == "perf":
        await _mcb_perf(cb, parts, action)

    elif action == "sp":
        await _mcb_sp(cb, parts, action)

    elif action == "yt":
        await _mcb_yt(cb, parts, action)

    elif action in ("dlf", "dlf_direct", "dlf_ffmpeg"):
        await _mcb_dlf(cb, parts, action)

    elif action == "srch":
        await _mcb_srch(cb, parts, action)

    elif action == "yta":
        await _mcb_yta(cb, parts, action)

    elif action == "web":
        await _mcb_web(cb, parts, action)

    elif action in ("fetch", "fetch_pause", "fetch_stop"):
        await _mcb_fetch(cb, parts, action)

    elif action in ("stats", "stats_chart", "stats_top"):
        await cb.answer()
        text, kb = await stats_view(action)
        await safe_edit_cb(cb, text, kb)

    elif action == "st":
        await cb.answer()
        reset_inputs()
        text, kb = await stars_panel()
        await safe_edit_cb(cb, text, kb)

    elif action in ("st_plan", "st_off", "st_set", "st_step"):
        await _mcb_st_plan(cb, parts, action)

    elif action == "st_pay":
        await cb.answer()
        text, kb = await payments_view(
            int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        )
        await safe_edit_cb(cb, text, kb)

    elif action in ("st_p", "st_ref", "st_refy"):
        await _mcb_st_p(cb, parts, action)

    else:
        await cb.answer()


def register_menu_router(router: Router):
    router.callback_query.register(health_cb, F.data.startswith("hc:"))
    router.callback_query.register(audio_buttons_callback, F.data.startswith("kb_btn:"))
    router.callback_query.register(menu_callback, F.data.startswith("menu:"))
