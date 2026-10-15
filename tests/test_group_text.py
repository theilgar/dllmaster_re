"""🔎 Qrupda «/music» yazmadan mətnlə axtarış: filtr məntiqi, qrup ayarı, panel."""
import inspect
import types
from unittest.mock import AsyncMock

import pytest

from bot.core import group_panel as gp
from bot.core import group_text as gt


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    gt._last.clear()
    store = {}
    monkeypatch.setattr(gp, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(gp, "set_setting", lambda k, v: store.__setitem__(k, str(v)))
    return store


def msg(text="Eminem Lose Yourself", chat_id=-100, user_id=7, **kw):
    base = dict(text=text, chat=types.SimpleNamespace(id=chat_id, type="supergroup"),
                from_user=types.SimpleNamespace(id=user_id, is_bot=False), entities=None, forward_origin=None,
                via_bot=None, sender_chat=None, reply_to_message=None, message_thread_id=None, is_topic_message=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# ── mətnin axtarışa oxşarlığı ──
@pytest.mark.parametrize("text", ["Eminem", "Lose Yourself", "ab", "Nərgiz - Sevgilim", "50 cent in da club", "Xəzər 2"])
def test_accepts_song_like_text(text):
    assert gt.looks_like_query(text)


@pytest.mark.parametrize("text", ["", "   ", "a", "😀😀😀", "...", "?!", "x" * 101, "/music abc", ".play x", "!radio"])
def test_rejects_non_queries(text):
    assert not gt.looks_like_query(text)


# ── qərar ──
def test_plain_text_in_enabled_group_is_a_search():
    assert gt.is_group_search(msg(), True) is True


def test_disabled_group_never_searches():
    assert gt.is_group_search(msg(), False) is False


@pytest.mark.parametrize("override", [
    dict(from_user=types.SimpleNamespace(id=1, is_bot=True)),                      # bot mesajı
    dict(from_user=None),
    dict(forward_origin=object()),                                                  # forward
    dict(via_bot=object()),                                                         # inline nəticə
    dict(sender_chat=object()),                                                     # kanal / anonim admin
    dict(reply_to_message=types.SimpleNamespace(message_id=5)),                     # söhbət (reply)
    dict(text="salam"[:1]),                                                         # çox qısa
])
def test_conversation_like_messages_are_ignored(override):
    assert gt.is_group_search(msg(**override), True) is False


@pytest.mark.parametrize("etype", ["mention", "text_mention", "url", "text_link", "email", "phone_number", "bot_command"])
def test_messages_with_directed_entities_are_ignored(etype):
    assert gt.is_group_search(msg(entities=[types.SimpleNamespace(type=etype)]), True) is False


def test_formatting_entities_do_not_block():
    assert gt.is_group_search(msg(entities=[types.SimpleNamespace(type="bold")]), True) is True


def test_forum_topic_header_reply_is_not_a_conversation():
    m = msg(reply_to_message=types.SimpleNamespace(message_id=42), message_thread_id=42, is_topic_message=True)
    assert gt.is_group_search(m, True) is True
    m = msg(reply_to_message=types.SimpleNamespace(message_id=43), message_thread_id=42, is_topic_message=True)
    assert gt.is_group_search(m, True) is False                                    # topic içində başqasına cavab


def test_per_user_per_group_cooldown():
    assert gt.is_group_search(msg(), True, now=100.0)
    assert not gt.is_group_search(msg(), True, now=105.0)                           # 8 san.-dən tez
    assert gt.is_group_search(msg(user_id=8), True, now=105.0)                      # başqa istifadəçi
    assert gt.is_group_search(msg(chat_id=-200), True, now=105.0)                   # başqa qrup
    assert gt.is_group_search(msg(), True, now=109.0)


def test_ignored_messages_do_not_consume_the_cooldown():
    assert not gt.is_group_search(msg(text="😀😀"), True, now=100.0)
    assert gt.is_group_search(msg(), True, now=100.5)


# ── qrup ayarı ──
def test_setting_is_off_by_default_and_per_group(clean):
    assert gp.is_group_text_search(-1) is False
    gp.set_group_text_search(-1, True)
    assert gp.is_group_text_search(-1) and not gp.is_group_text_search(-2)
    gp.set_group_text_search(-1, False)
    assert not gp.is_group_text_search(-1)


def test_panel_has_the_toggle_and_summary_line():
    assert "txt" in gp.SETTINGS and gp.SETTINGS["txt"][2] is False                 # sahib-yalnız deyil (admin dəyişə bilər)
    btn = gp._setting_btn("txt", -100)
    assert btn.callback_data == "grp:txt:-100" and "Mətnlə axtarış" in btn.text


async def test_panel_text_shows_state(monkeypatch):
    gp.set_group_text_search(-100, True)
    bot = types.SimpleNamespace(get_chat=AsyncMock(return_value=types.SimpleNamespace(title="Test")))
    monkeypatch.setattr(gp, "is_creator", lambda uid: True)
    text, kb = await gp.panel_view(bot, -100, 1, "Test")
    assert "Mətnlə axtarış (/music-süz)" in text
    assert any(b.callback_data == "grp:txt:-100" for row in kb.inline_keyboard for b in row)


async def test_toggle_warns_when_bot_cannot_read_messages(monkeypatch):
    me = types.SimpleNamespace(id=9, can_read_all_group_messages=False)
    member = types.SimpleNamespace(status="member")
    gp._CTX = types.SimpleNamespace(bot=types.SimpleNamespace(me=AsyncMock(return_value=me),
                                                               get_chat_member=AsyncMock(return_value=member)))
    note = await gp._t_txt(-100, "T")
    assert "🟢 açıq" in note and "Bot admin deyil" in note and gp.is_group_text_search(-100)
    note = await gp._t_txt(-100, "T")
    assert "söndürüldü" in note and not gp.is_group_text_search(-100)


async def test_toggle_has_no_warning_for_admin_bot_or_privacy_off():
    me = types.SimpleNamespace(id=9, can_read_all_group_messages=True)
    gp._CTX = types.SimpleNamespace(bot=types.SimpleNamespace(me=AsyncMock(return_value=me)))
    assert "⚠️" not in await gp._t_txt(-100, "T")
    gp.set_group_text_search(-100, False)
    me.can_read_all_group_messages = False
    gp._CTX.bot.get_chat_member = AsyncMock(return_value=types.SimpleNamespace(status="administrator"))
    assert "⚠️" not in await gp._t_txt(-100, "T")


async def test_toggle_survives_bot_api_errors():
    gp._CTX = types.SimpleNamespace(bot=types.SimpleNamespace(me=AsyncMock(side_effect=RuntimeError("network"))))
    note = await gp._t_txt(-100, "T")
    assert "🟢 açıq" in note and gp.is_group_text_search(-100)


# ── music_plugin-ə qoşulma ──
def test_music_plugin_registers_the_group_text_handler():
    import bot.plugins.music_plugin as mp
    src = inspect.getsource(mp)
    assert "handle_group_text_music_request" in src
    assert 'F.chat.type.in_({"group", "supergroup"})' in src and "~F.text.func(is_music_link)" in src
    assert "is_group_text_search" in src and "waiting_any" in src
