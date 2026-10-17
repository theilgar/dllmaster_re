"""
bot.menus
Telegram Bot üçün modullaşdırılmış menyu paketi.
"""

import asyncio
import contextlib
import logging
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from bot.menus.handlers import (
    commands_router,
    group_pm_router,
    register_all_menu_handlers,
    register_broadcast_handlers,
    register_input_handlers,
    register_menu_router,
    register_spotify_handlers,
    register_youtube_handlers,
)
from bot.menus.keyboards import (
    btn,
    choice_row,
    grid,
    make_markup,
    menu_btn,
    mode_btn,
    toggle_btn,
)
from bot.menus.navigation import (
    back_button,
    clear_nav,
    current_nav,
    nav_row,
    pop_nav,
    push_nav,
    refresh_button,
)
from bot.menus.views import (
    amsg_view,
    backup_view,
    filter_view,
    mus_view,
    profile_view,
    settings_view,
    shazam_info_view,
    tags_info_view,
    userbot_hub_view,
)
from bot.menus.pagination import Pagination, paginate_list
from bot.menus.state import (
    REC_INP,
    TEXT_WAITS,
    ab_inp,
    bc,
    bc_reset,
    delete_preview,
    get_context,
    grp_inp,
    is_creator,
    menu_waiting_text,
    panel_editor,
    perf_inp,
    prm,
    rec_inp,
    reset_inputs,
    safe_edit_cb,
    set_context,
    sp_input,
    stp,
    tx,
    us,
    wm,
    yt_inp,
    yta_inp,
)
from common.permissions import is_group_owner
from bot.menus.views import (
    _AU_TOPICS,
    _USER_ADMIN_GROUPS_CACHE,
    BOT_STATUS,
    CAPTION_LIMIT,
    CHAT_FILTERS,
    CHAT_TYPES,
    CHATS_PAGE,
    HELP_TEXT,
    LINE,
    MAX_STARS,
    MIN_STARS,
    MS_WHERE,
    PAGE_SIZE,
    PERF_INFO,
    PLAN_MAP,
    PLANS,
    PREMIUM_DURATIONS,
    PREMIUM_PAGE_SIZE,
    SECTIONS,
    SOURCE_LABELS,
    TARGET_LABELS,
    TEXT_LIMIT,
    TEXT_SLOTS,
    TRACK_THROTTLE,
    USER_FILTERS,
    USERS_PAGE,
    UserTrackerMiddleware,
    _au_btn,
    _au_candidates,
    _au_cb,
    _au_dest,
    _lg,
    _lgub,
    _mask_secret,
    _sp_setting,
    _spotify_button_label,
    _yta_browser_profiles,
    _yta_label,
    accel_view,
    ago,
    all_groups_settings_view,
    arch_button_label,
    arch_view,
    aud_view,
    audio_buttons_picker_view,
    audio_buttons_view,
    audit_apply_target,
    audit_fetch_topics,
    audit_open_topics,
    audit_settings_view,
    audit_target_view,
    audit_test,
    audit_topic_view,
    bar_chart,
    bc_preview_kb,
    bc_target_kb,
    biz_view,
    bots_view,
    buy_keyboard,
    chat_card,
    chat_live,
    com_view,
    crumb,
    depo_row,
    depo_view,
    dev_button_label,
    dev_hub_view,
    display_name,
    dl_format_view,
    duration_panel,
    fetch_kb,
    fetch_sessions,
    fmt_date,
    fmt_dt,
    fmt_until,
    get_bot_username,
    get_price,
    get_user_admin_groups,
    git_summary,
    group_menu_view,
    group_settings_view,
    groups_view,
    health_label,
    health_view,
    help_view,
    home_view,
    live_view,
    main_kb,
    main_text,
    media_slot,
    mod_cmds_view,
    ms_list_panel,
    ms_panel,
    parse_user_ref,
    payment_card,
    payments_view,
    pct,
    per_month,
    perf_button_label,
    perf_cores_view,
    perf_hub_view,
    perf_picker_view,
    plan_line,
    premium_panel,
    run_updates,
    search_source_view,
    send_one,
    send_sample,
    set_price,
    short_media,
    short_name,
    sparkline,
    spotify_panel,
    srv_button_label,
    srv_hub_view,
    stars_panel,
    stars_plan_panel,
    start_buttons_view,
    start_fetch_live,
    start_oauth_listener,
    stats_tabs,
    stats_view,
    stop_fetch_on,
    stop_oauth_listener,
    sub_kb,
    sys_kb,
    sys_text,
    target_counts,
    trend,
    tz_offset_seconds,
    ub_no_inline_list_view,
    upd_button_label,
    upd_view,
    user_card,
    user_groups_view,
    user_label,
    user_menu_view,
    userbots_button_label,
    users_list_kb,
    users_view,
    va_button_label,
    web_profile_view,
    web_profiles_view,
    yt_button_label,
    yt_hub_view,
    yt_picker_view,
    yta_acc_view,
    yta_browser_view,
    yta_help_view,
    yta_list_view,
    yta_login_view,
    yta_own_device_view,
    yta_server_login_view,
)

logger = logging.getLogger(__name__)


def setup(context: Any):
    """Menyu plugininin başladılması və Aiogram router-lərinin qeydiyyatı."""
    set_context(context)
    try:
        from common.menu import set_global_context

        set_global_context(context)
    except Exception as _e:
        logger.debug("udulan xəta [bot/menus/__init__.py:246]: %r", _e)

    try:
        from bot.core import cpu_pool

        cpu_pool.init(context)  # ⚙️ thread hovuzu + 🫀 donma monitoru
    except Exception as e:
        logger.warning(f"⚙️ cpu_pool başladılmadı: {e}")

    dp = context.dp
    context.menu_main_view = lambda: (main_text(), main_kb())  # userbot .bmenu üçün
    context.rec_inp = REC_INP

    # 🔔 Yeniləmə yoxlaması: başlanğıcda + hər 6 saatdan bir
    try:
        from common import updates as upd

        if upd is not None:
            async def notify_creator(text: str):
                if not context.creator_id:
                    return
                with contextlib.suppress(Exception):
                    await context.bot.send_message(
                        context.creator_id,
                        text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[menu_btn("🔔 Ətraflı", "upd:main")]]),
                    )

            def start_update_loop():
                old = getattr(context, "update_task", None)
                if old and not old.done():
                    return
                context.update_task = asyncio.create_task(upd.update_loop(notify=notify_creator))

            try:
                asyncio.get_running_loop()
                start_update_loop()
            except RuntimeError:
                async def _upd_on_startup(**_):
                    start_update_loop()

                dp.startup.register(_upd_on_startup)
    except Exception as e:
        logger.warning(f"🔔 Yeniləmə monitoru başladılmadı: {e}")

    # 🔑 YouTube hesabı rotasiya bildirişi və yoxlama dövrü
    async def _yta_notify(text: str):
        with contextlib.suppress(Exception):
            await context.bot.send_message(
                context.creator_id,
                text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[menu_btn("🔑 YouTube hesabları", "yta")]]),
            )

    with contextlib.suppress(Exception):
        from bot.core import yt_accounts as _ya

        _ya.notify_hook = _yta_notify
        _ya.start_health_loop()

    # ⌨️ Menyu mətn gözləmə zəncirinə qoşul
    try:
        from bot.core.input_wait import chain_menu_text

        chain_menu_text(context, menu_waiting_text)
    except Exception as e:
        logger.warning(f"chain_menu_text qoşulmadı: {e}")

    # Verilənlər bazasını inisializasiya et
    try:
        from common.database import get_db

        get_db()
    except Exception as _e:
        logger.debug("udulan xəta [bot/menus/__init__.py:322]: %r", _e)

    # 👥 İstifadəçi aktivlik middleware-i
    tracker = UserTrackerMiddleware()
    dp.message.outer_middleware(tracker)
    dp.callback_query.outer_middleware(tracker)
    dp.my_chat_member.outer_middleware(tracker)
    dp.channel_post.outer_middleware(tracker)

    # 🎛 Bütün menyu router və handler-lərini qeydiyyatdan keçir
    register_all_menu_handlers(dp, context)
    logger.info("✅ Menu modulları və handler-ləri uğurla yükləndi")


import types as _types

# Fasad: yuxarıdakı bütün adlar (modullar xaric) açıq API-dir — siyahı əl ilə yazılmır.
__all__ = [_n for _n, _v in list(globals().items()) if not _n.startswith("__") and not isinstance(_v, _types.ModuleType)]
