"""
📦 Depo doldurucu + 📱 Telethon userbot — bot/plugins/depo_filler_plugin.py
(Modullaşdırılmış entry-point; əsas məntiq bot/depo/ paketindədir)
"""
from __future__ import annotations

from bot.depo.cleaner import clean_song, parse_tg_target, tg_channel_items, tg_item, tl_message_item
from bot.depo.crawler import YtmCrawler, itunes_lookup, match_score, spotify_lookup, ytm_item
from bot.depo.engine import (
    Filler,
    delete_depo_tracks,
    ensure_thread_pool,
    raise_nofile,
    search_depo_for_deletion,
)
from bot.depo.handlers import setup
from bot.depo.monitor import (
    ProcMeter,
    bot_tz,
    get_top_interval,
    render_top,
    set_top_interval,
)
from bot.depo.scanner import TgUser

PRIORITY = 60

__all__ = [
    "setup",
    "clean_song",
    "parse_tg_target",
    "tg_item",
    "tl_message_item",
    "tg_channel_items",
    "match_score",
    "spotify_lookup",
    "itunes_lookup",
    "ytm_item",
    "YtmCrawler",
    "raise_nofile",
    "ensure_thread_pool",
    "search_depo_for_deletion",
    "delete_depo_tracks",
    "Filler",
    "get_top_interval",
    "set_top_interval",
    "ProcMeter",
    "render_top",
    "bot_tz",
    "TgUser",
    "PRIORITY",
]
