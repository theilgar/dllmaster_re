"""
bot.menus.pages
Menyu səhifələrinin vahid eksport paketi.
"""

from bot.menus.pages.depo import depo_view
from bot.menus.pages.groups import groups_view
from bot.menus.pages.home import home_view, main_kb, main_text
from bot.menus.pages.music import mus_view, shazam_info_view, tags_info_view
from bot.menus.pages.settings import settings_view
from bot.menus.pages.stats import stats_view
from bot.menus.pages.userbot import (
    amsg_view,
    backup_view,
    filter_view,
    profile_view,
    userbot_hub_view,
)

__all__ = [
    "amsg_view",
    "backup_view",
    "depo_view",
    "filter_view",
    "groups_view",
    "home_view",
    "main_kb",
    "main_text",
    "mus_view",
    "profile_view",
    "settings_view",
    "shazam_info_view",
    "stats_view",
    "tags_info_view",
    "userbot_hub_view",
]
