"""GitHub-a göndərmə axınında commit mesajı sorğusu / cavabı kimi əlavə mesajlar silinir (köhnə düymələr qalmır)."""
from unittest.mock import AsyncMock

from bot.updater.handlers import delete_messages


async def test_delete_messages_skips_empty_and_duplicates_and_swallows_errors():
    deleted = []

    async def fake_delete(chat_id, mid):
        if mid == 13:
            raise RuntimeError("message to delete not found")
        deleted.append((chat_id, mid))

    bot = AsyncMock()
    bot.delete_message = fake_delete
    n = await delete_messages(bot, 7, [11, None, 12, 11, 0, 13])
    assert n == 2 and deleted == [(7, 11), (7, 12)]


async def test_delete_messages_accepts_none():
    bot = AsyncMock()
    assert await delete_messages(bot, 7, None) == 0 and not bot.delete_message.await_count
