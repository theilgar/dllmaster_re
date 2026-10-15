"""
bot.menus.pages.music
Re-export from bot.menus.views.music for backwards compatibility.
"""
from bot.menus.views.music import (
    mus_view,
    shazam_info_view,
    tags_info_view,
)

__all__ = [
    "mus_view",
    "shazam_info_view",
    "tags_info_view",
]
