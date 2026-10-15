"""
bot.menus.pages.home
Əsas menyu (Home) paneli və ümumi sistem görünüşü.
"""

from typing import Any

from aiogram.types import InlineKeyboardMarkup

from common.menu import main_kb, main_text


def home_view(ctx: Any = None, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Əsas menyu mətnini və klaviaturasını qaytarır."""
    text = main_text(ctx)
    if note:
        text += f"\n\nℹ️ {note}"
    kb = main_kb(ctx)
    return text, kb


__all__ = ["home_view", "main_kb", "main_text"]
