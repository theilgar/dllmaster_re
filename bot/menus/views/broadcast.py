"""
bot.menus.views.broadcast
Broadcast (kütləvi mesaj) hədəf seçimi, klaviaturalar və göndərmə köməkçiləri.
"""

import asyncio
import logging

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import TARGET_LABELS
from common.menu import _btn

logger = logging.getLogger(__name__)

_BOT_USERNAME = ""


def target_counts(db) -> dict[str, int]:
    uc, cc = db.user_counts(), db.chat_counts()
    c = {"users": uc["active"], "groups": cc["groups"], "channels": cc["channels"]}
    c["all"] = c["users"] + c["groups"] + c["channels"]
    return c


def bc_target_kb(counts: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(f"{TARGET_LABELS['users']} ({counts['users']})", "bc_t:users"),
                _btn(f"{TARGET_LABELS['groups']} ({counts['groups']})", "bc_t:groups"),
            ],
            [
                _btn(f"{TARGET_LABELS['channels']} ({counts['channels']})", "bc_t:channels"),
                _btn(f"{TARGET_LABELS['all']} ({counts['all']})", "bc_t:all"),
            ],
            nav_row("com"),
        ]
    )


def bc_preview_kb(target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🚀 İndi göndər", f"bc_s:{target}")],
            [_btn("✏️ Mətni dəyiş", f"bc_t:{target}"), _btn("✖️ Ləğv et", "bc_cancel")],
            nav_row("bc"),
        ]
    )


async def send_one(bot, db, kind: str, chat_id: int, text: str, reply_markup=None) -> str:
    """Bir çata göndərir → 'ok' | 'removed' (bloklayıb/çıxarılıb) | 'failed'."""
    for _ in range(4):
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup,
                                   disable_web_page_preview=True)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramMigrateToChat as e:
            await asyncio.to_thread(db.migrate_chat, chat_id, e.migrate_to_chat_id)
            chat_id = e.migrate_to_chat_id
        except TelegramForbiddenError:
            if kind == "user":
                await asyncio.to_thread(db.set_blocked, chat_id, True)
            else:
                await asyncio.to_thread(db.set_chat_active, chat_id, False)
            return "removed"
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if kind != "user" and ("chat not found" in msg or "not a member" in msg or "kicked" in msg):
                await asyncio.to_thread(db.set_chat_active, chat_id, False)
                return "removed"
            logger.warning("Broadcast xətası (%s): %s", chat_id, e)
            return "failed"
        except Exception as e:
            logger.warning("Broadcast xətası (%s): %s", chat_id, e)
            return "failed"
    return "failed"


async def get_bot_username(bot=None) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        b = bot or getattr(get_context(), "bot", None)
        if b:
            try:
                me = await b.me()
                _BOT_USERNAME = me.username or "dllmasterbot"
            except Exception:
                _BOT_USERNAME = "dllmasterbot"
        else:
            _BOT_USERNAME = "dllmasterbot"
    return _BOT_USERNAME


__all__ = [
    "bc_preview_kb",
    "bc_target_kb",
    "get_bot_username",
    "send_one",
    "target_counts",
]
