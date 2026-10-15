"""
🔗 Musiqi linkləri — bot/plugins/links_plugin.py
(Modullaşdırılmış entry-point; əsas məntiq bot/links/ paketindədir)
"""
from __future__ import annotations

from bot.links.handlers import setup
from bot.links.parsers import (
    pick_image,
    resolve_short,
    sp_item,
    spotify_embed,
    total_duration,
    track_key,
    vk_artist_data,
    vk_item,
    vk_playlist_data,
    vk_track_data,
    vk_video_data,
    vk_wall_data,
    youtube_oembed,
)

PRIORITY = 10

__all__ = [
    "setup",
    "pick_image",
    "sp_item",
    "track_key",
    "total_duration",
    "resolve_short",
    "youtube_oembed",
    "vk_item",
    "vk_track_data",
    "vk_playlist_data",
    "vk_artist_data",
    "vk_video_data",
    "vk_wall_data",
    "spotify_embed",
    "PRIORITY",
]
