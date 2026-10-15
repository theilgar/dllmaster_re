"""
bot.menus.pages.userbot
Re-export from bot.menus.views.userbot for backwards compatibility.
"""
from bot.menus.views.userbot import (
    amsg_view,
    backup_view,
    filter_view,
    profile_view,
    userbot_hub_view,
)

__all__ = [
    "amsg_view",
    "backup_view",
    "filter_view",
    "profile_view",
    "userbot_hub_view",
]
