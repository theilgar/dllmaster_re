"""
bot.menus.handlers
Bütün menyu handler və router-lərinin qeydiyyat paketi.
"""

from aiogram import Router

from bot.menus.handlers.ads_flow import register_ads_handlers
from bot.menus.handlers.broadcast_flow import register_broadcast_handlers
from bot.menus.handlers.commands import router as commands_router
from bot.menus.handlers.group_pm_flow import router as group_pm_router
from bot.menus.handlers.input_router import register_input_handlers
from bot.menus.handlers.maint_flow import register_maint_handlers
from bot.menus.handlers.menu_router import register_menu_router
from bot.menus.handlers.spotify_flow import register_spotify_handlers
from bot.menus.handlers.tipmsg_flow import register_tipmsg_handlers
from bot.menus.handlers.youtube_flow import register_youtube_handlers


def register_all_menu_handlers(dp_or_router: Router, context=None):
    """Bütün menyu router və handler-lərini verilmiş Dispatcher və ya Router-ə qeydiyyatdan keçirir."""
    # 1. Giriş/Mətn vəziyyət handler-ləri (FSM / Waits)
    register_broadcast_handlers(dp_or_router)
    register_tipmsg_handlers(dp_or_router)
    register_maint_handlers(dp_or_router)
    register_ads_handlers(dp_or_router)
    register_youtube_handlers(dp_or_router)
    register_spotify_handlers(dp_or_router)
    register_input_handlers(dp_or_router)

    # 2. Komandalar (/menu, /groups, /allgroups, /rec)
    dp_or_router.include_router(commands_router)

    # 3. Qrup PM axını (umenu:*, pm_grp:*)
    dp_or_router.include_router(group_pm_router)

    # 4. Əsas menyu callbacks (hc:*, kb_btn:*, menu:*)
    register_menu_router(dp_or_router)


__all__ = [
    "commands_router",
    "group_pm_router",
    "register_all_menu_handlers",
    "register_broadcast_handlers",
    "register_input_handlers",
    "register_menu_router",
    "register_spotify_handlers",
    "register_youtube_handlers",
]
