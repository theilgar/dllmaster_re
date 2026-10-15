"""
🔗 bot/links/ - Musiqi Linkləri və Axın Yükləmə Paketi.
"""
from bot.links.parsers import (
    pick_image,
    sp_item,
    track_key,
    total_duration,
    resolve_short,
    youtube_oembed,
    vk_item,
    spotify_embed,
)
from bot.links.handlers import setup

__all__ = [
    "pick_image",
    "sp_item",
    "track_key",
    "total_duration",
    "resolve_short",
    "youtube_oembed",
    "vk_item",
    "spotify_embed",
    "setup",
]
