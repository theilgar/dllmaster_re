"""Unit tests for playlist "🎙 Canlı yayımda oxut" button in groups with voice assistant."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher
from bot.links import handlers as H
from bot.voice.models import ChatState


class FakeChat:
    def __init__(self, chat_id: int, chat_type: str = "group", title: str = "Test Group"):
        self.id = chat_id
        self.type = chat_type
        self.title = title


class FakeUser:
    def __init__(self, user_id: int, first_name: str = "User"):
        self.id = user_id
        self.first_name = first_name


class FakeMessage:
    def __init__(self, chat: FakeChat, message_id: int = 100, user: FakeUser = None):
        self.chat = chat
        self.message_id = message_id
        self.from_user = user or FakeUser(10, "Tester")
        self.edited_text = None
        self.reply_markup = None

    async def edit_text(self, text, parse_mode=None, disable_web_page_preview=None, reply_markup=None):
        self.edited_text = text
        self.reply_markup = reply_markup
        return self


class FakeCallback:
    def __init__(self, chat: FakeChat, message: FakeMessage, user: FakeUser, data: str):
        self.message = message
        self.from_user = user
        self.data = data
        self.bot = AsyncMock()
        self.answered = False
        self.answer_text = None
        self.alert = False

    async def answer(self, text=None, show_alert=False):
        self.answered = True
        self.answer_text = text
        self.alert = show_alert


def create_env(chat_type="group", with_va=True, va_in_chat=True, va_running=True):
    dp = Dispatcher()
    bot = AsyncMock()
    context = types.SimpleNamespace(
        dp=dp,
        bot=bot,
        creator_id=999,
        spotify=None,
        playlist_import=None,
    )

    if with_va:
        va = types.SimpleNamespace(
            enabled=True,
            has_session=lambda: True,
            running=va_running,
            start=AsyncMock(return_value=""),
            member_status=AsyncMock(return_value="member" if va_in_chat else "left"),
            chats={-1001: ChatState(-1001, "Test Group")} if va_in_chat else {},
            who="all",
            enqueue=AsyncMock(return_value=("playing", 0)),
            state=lambda cid, title="": ChatState(cid, title),
            prefetch=MagicMock(),
            save_active_sessions=MagicMock(),
            refresh_np=AsyncMock(),
        )
        context.voice_assistant = va
    else:
        context.voice_assistant = None

    H.setup(context)
    return context


@pytest.mark.asyncio
async def test_show_list_card_private_chat_no_vplay():
    """In private chat, vplay button should NOT be shown."""
    ctx = create_env(chat_type="private", with_va=True, va_in_chat=True)
    status_msg = FakeMessage(FakeChat(10, "private"), message_id=1)
    orig_msg = FakeMessage(FakeChat(10, "private"), message_id=2, user=FakeUser(10))
    items = [{"title": "Song 1", "url": "https://www.youtube.com/watch?v=11111111111", "raw_duration": 180}]

    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "My Playlist", items, "playlist")

    assert status_msg.reply_markup is not None
    callbacks = [btn.callback_data for row in status_msg.reply_markup.inline_keyboard for btn in row]
    assert "lk:go" in callbacks
    assert "lk:vplay" not in callbacks


@pytest.mark.asyncio
async def test_show_list_card_group_no_assistant_in_group():
    """In group chat without assistant in the group, vplay button should NOT be shown."""
    ctx = create_env(chat_type="group", with_va=True, va_in_chat=False)
    status_msg = FakeMessage(FakeChat(-1002, "supergroup"), message_id=1)
    orig_msg = FakeMessage(FakeChat(-1002, "supergroup"), message_id=2, user=FakeUser(10))
    items = [{"title": "Song 1", "url": "https://www.youtube.com/watch?v=11111111111", "raw_duration": 180}]

    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "My Playlist", items, "playlist")

    assert status_msg.reply_markup is not None
    callbacks = [btn.callback_data for row in status_msg.reply_markup.inline_keyboard for btn in row]
    assert "lk:go" in callbacks
    assert "lk:vplay" not in callbacks


@pytest.mark.asyncio
async def test_show_list_card_group_with_assistant_has_vplay_button_below_go():
    """In group chat with assistant, vplay button must be present directly below lk:go."""
    ctx = create_env(chat_type="group", with_va=True, va_in_chat=True)
    status_msg = FakeMessage(FakeChat(-1001, "supergroup"), message_id=1)
    orig_msg = FakeMessage(FakeChat(-1001, "supergroup"), message_id=2, user=FakeUser(10))
    items = [{"title": "Song 1", "url": "https://www.youtube.com/watch?v=11111111111", "raw_duration": 180}]

    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "My Playlist", items, "playlist")

    assert status_msg.reply_markup is not None
    rows = status_msg.reply_markup.inline_keyboard
    assert len(rows) >= 2
    assert rows[0][0].callback_data == "lk:go"
    assert rows[1][0].callback_data == "lk:vplay"
    assert "Canlı yayımda oxut" in rows[1][0].text


@pytest.mark.asyncio
async def test_vplay_callback_enqueues_playlist_and_updates_card():
    """Clicking lk:vplay enqueues tracks to voice assistant and edits the message."""
    ctx = create_env(chat_type="group", with_va=True, va_in_chat=True)
    chat = FakeChat(-1001, "supergroup")
    status_msg = FakeMessage(chat, message_id=50)
    user = FakeUser(10, "Ahmad")
    orig_msg = FakeMessage(chat, message_id=51, user=user)

    items = [
        {"title": "Track 1", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "raw_duration": 120},
        {"title": "Track 2", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "raw_duration": 200},
    ]

    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "Hits", items, "playlist")

    cb = FakeCallback(chat, status_msg, user, "lk:vplay")
    await ctx.links_callback(cb)

    assert cb.answered is True
    # Allow background spawn task to run
    await asyncio.sleep(0.05)

    assert ctx.voice_assistant.enqueue.called
    assert "mahnı canlı yayıma əlavə edildi" in (status_msg.edited_text or "")


@pytest.mark.asyncio
async def test_vplay_callback_blocked_for_unauthorized_user():
    """Another user who is neither sender, creator nor admin cannot trigger vplay."""
    ctx = create_env(chat_type="group", with_va=True, va_in_chat=True)
    chat = FakeChat(-1001, "supergroup")
    status_msg = FakeMessage(chat, message_id=60)
    sender = FakeUser(10, "Sender")
    other_user = FakeUser(55, "Stranger")
    orig_msg = FakeMessage(chat, message_id=61, user=sender)

    items = [{"title": "Track 1", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "raw_duration": 120}]
    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "Hits", items, "playlist")

    cb = FakeCallback(chat, status_msg, other_user, "lk:vplay")
    # Member is regular member, not admin
    cb.bot.get_chat_member = AsyncMock(return_value=types.SimpleNamespace(status="member"))

    await ctx.links_callback(cb)
    assert cb.answered is True
    assert "Yalnız linki göndərən" in (cb.answer_text or "")
    assert not ctx.voice_assistant.enqueue.called


@pytest.mark.asyncio
async def test_vplay_callback_handles_group_call_error():
    """If group call is not started, error is caught and shown to the user."""
    ctx = create_env(chat_type="group", with_va=True, va_in_chat=True)
    ctx.voice_assistant.enqueue = AsyncMock(side_effect=RuntimeError("GroupCall not in a call"))

    chat = FakeChat(-1001, "supergroup")
    status_msg = FakeMessage(chat, message_id=70)
    user = FakeUser(10, "Ahmad")
    orig_msg = FakeMessage(chat, message_id=71, user=user)

    items = [{"title": "Track 1", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "raw_duration": 120}]
    await ctx.links_show_list_card(status_msg, orig_msg, "YouTube", "Hits", items, "playlist")

    cb = FakeCallback(chat, status_msg, user, "lk:vplay")
    await ctx.links_callback(cb)

    await asyncio.sleep(0.05)
    assert "Səsli yayım başlamayıb" in (status_msg.edited_text or "")
