"""
bot.menus.views
Bütün menyu səhifə və pəncərə vizual şablonlarının vahid eksport paketi.
"""

from bot.menus.views.audit import (
    _AU_TOPICS,
    _au_btn,
    _au_candidates,
    _au_cb,
    _au_dest,
    _lg,
    _lgub,
    audit_apply_target,
    audit_fetch_topics,
    audit_open_topics,
    audit_settings_view,
    audit_target_view,
    audit_test,
    audit_topic_view,
)
from bot.menus.views.base import (
    BOT_STATUS,
    CHAT_FILTERS,
    CHAT_TYPES,
    CHATS_PAGE,
    LINE,
    PAGE_SIZE,
    PREMIUM_DURATIONS,
    PREMIUM_PAGE_SIZE,
    SECTIONS,
    SOURCE_LABELS,
    TARGET_LABELS,
    TRACK_THROTTLE,
    USER_FILTERS,
    USERS_PAGE,
    ago,
    bar_chart,
    crumb,
    display_name,
    fmt_date,
    fmt_dt,
    pct,
    sparkline,
    stats_tabs,
    trend,
    tz_offset_seconds,
)
from bot.menus.views.broadcast import (
    bc_preview_kb,
    bc_target_kb,
    get_bot_username,
    send_one,
    target_counts,
)
from bot.menus.views.dev import (
    dev_button_label,
    dev_hub_view,
    git_summary,
    health_label,
    health_view,
    run_updates,
    upd_button_label,
    upd_view,
)
from bot.menus.views.groups import (
    _USER_ADMIN_GROUPS_CACHE,
    all_groups_settings_view,
    get_user_admin_groups,
    group_menu_view,
    group_settings_view,
    groups_view,
    user_groups_view,
    user_menu_view,
)
from bot.menus.views.home import (
    home_view,
    main_kb,
    main_text,
)
from bot.menus.views.invites import (
    invite_top_view,
    invite_view,
    user_invite_data,
    user_invite_view,
)
from bot.menus.views.welcome_prem import welcome_prem_view
from bot.menus.views.messages import (
    CAPTION_LIMIT,
    MS_WHERE,
    TEXT_LIMIT,
    TEXT_SLOTS,
    media_slot,
    ms_list_panel,
    ms_panel,
    send_sample,
    short_media,
)
from bot.menus.views.music import (
    mus_view,
    shazam_info_view,
    tags_info_view,
)
from bot.menus.views.performance import (
    PERF_INFO,
    perf_button_label,
    perf_cores_view,
    perf_hub_view,
    perf_picker_view,
)
from bot.menus.views.premium_stars import (
    MAX_STARS,
    MIN_STARS,
    PLAN_MAP,
    PLANS,
    buy_keyboard,
    duration_panel,
    get_price,
    parse_user_ref,
    payment_card,
    payments_view,
    per_month,
    plan_line,
    premium_panel,
    set_price,
    short_name,
    stars_panel,
    stars_plan_panel,
)
from bot.menus.views.server import (
    srv_button_label,
    srv_hub_view,
    userbots_button_label,
    va_button_label,
)
from bot.menus.views.spotify import (
    _mask_secret,
    _sp_setting,
    _spotify_button_label,
    spotify_panel,
    start_oauth_listener,
    stop_oauth_listener,
)
from bot.menus.views.stats import (
    fmt_until,
    stats_view,
    user_label,
)
from bot.menus.views.system_hub import (
    HELP_TEXT,
    UserTrackerMiddleware,
    accel_view,
    arch_button_label,
    arch_view,
    aud_view,
    audio_buttons_picker_view,
    audio_buttons_view,
    biz_view,
    bots_view,
    com_view,
    depo_row,
    depo_view,
    fetch_kb,
    fetch_sessions,
    help_view,
    live_view,
    mod_cmds_view,
    settings_view,
    start_buttons_view,
    start_fetch_live,
    stop_fetch_on,
    sub_kb,
    sys_kb,
    sys_text,
    ub_no_inline_list_view,
)
from bot.menus.views.userbot import (
    amsg_view,
    backup_view,
    filter_view,
    profile_view,
    userbot_hub_view,
)
from bot.menus.views.users_chats import (
    chat_card,
    chat_live,
    user_card,
    users_list_kb,
    users_view,
)
from bot.menus.views.youtube_accs import (
    _yta_browser_profiles,
    _yta_label,
    dl_format_view,
    search_source_view,
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


import types as _types

# Fasad: yuxarıdakı bütün adlar (modullar xaric) açıq API-dir — siyahı əl ilə yazılmır.
__all__ = [_n for _n, _v in list(globals().items()) if not _n.startswith("__") and not isinstance(_v, _types.ModuleType)]
