"""bot/core/depo_access.py — kanal tapma və botun hüququnu yoxlama: "Depo kanalı" səhifəsi və əlavə kanal EYNİ qaydanı işlədir."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import depo_access as A


def chat(kind="channel", username="depo2", cid=-1002):
    return types.SimpleNamespace(id=cid, type=kind, username=username, title="Depo 2")


def member(status, **kw):
    return types.SimpleNamespace(status=status, **kw)


@pytest.mark.parametrize("kind,m,ok,word", [
    ("channel", member("creator"), True, ""),
    ("channel", member("administrator", can_post_messages=True), True, ""),
    ("channel", member("administrator"), True, ""),                              # hüquq göstərilməyib — admin sayılır
    ("channel", member("administrator", can_post_messages=False), False, "Post messages"),
    ("channel", member("member"), False, "administrator"),
    ("channel", member("left"), False, "administrator"),
    ("supergroup", member("administrator"), True, ""),
    ("supergroup", member("member", can_send_messages=True), True, ""),
    ("supergroup", member("member", can_send_messages=False), False, "mesaj göndərmək hüququ yoxdur"),
    ("supergroup", member("restricted", can_send_messages=True, can_send_audios=True), True, ""),
    ("supergroup", member("restricted", can_send_messages=True, can_send_audios=False), False, "məhdudlaşdırılıb"),
    ("supergroup", member("kicked"), False, "status: kicked"),
])
def test_post_access_matrix(kind, m, ok, word):
    res, reason = A.post_access(chat(kind), m)
    assert res is ok and word in reason


def bot_for(status="administrator", **kw):
    bot = MagicMock()
    bot.get_chat = AsyncMock(return_value=chat())
    bot.get_me = AsyncMock(return_value=types.SimpleNamespace(id=777))
    bot.get_chat_member = AsyncMock(return_value=member(status, **kw))
    return bot


def run(coro):
    return asyncio.run(coro)


def test_inspect_returns_ref_and_title_or_a_reason():
    found, err = run(A.inspect(bot_for(), "@depo2"))
    assert not err and found.ref == "@depo2" and found.title == "Depo 2" and found.chat.id == -1002
    bot = bot_for()
    bot.get_chat = AsyncMock(return_value=chat(username=None))
    assert run(A.inspect(bot, -1002))[0].ref == "-1002"
    found, err = run(A.inspect(bot_for("member"), "@depo2"))
    assert found is None and "administrator" in err and "Mesaj göndərmək" in err
    bot = bot_for()
    bot.get_chat = AsyncMock(side_effect=RuntimeError("chat not found"))
    found, err = run(A.inspect(bot, "@nope"))
    assert found is None and "tapılmadı" in err and "chat not found" in err


def msg(text=None, forward_chat=None, thread=None):
    m = MagicMock()
    m.text = text
    m.message_thread_id = thread
    m.reply_to = None
    m.forward_origin = types.SimpleNamespace(chat=forward_chat) if forward_chat else None
    m.forward_from_chat = None
    return m


@pytest.mark.parametrize("text,target,thread", [
    ("@depo2", "@depo2", None),
    ("depo2", "@depo2", None),                                                    # @-sız ad da olar
    ("-1002", -1002, None),
    ("https://t.me/depo2", "@depo2", None),
    ("t.me/depo2/12", "@depo2", 12),                                             # mövzu linkdən
    ("https://t.me/c/1234567890/5", -1001234567890, 5),
])
def test_from_message_understands_every_form(text, target, thread):
    bot = bot_for()
    found, got_thread, err = run(A.from_message(bot, msg(text)))
    assert not err and got_thread == thread
    bot.get_chat.assert_awaited_with(target)


def test_forwarded_post_gives_the_channel_and_its_topic():
    bot = bot_for()
    found, thread, err = run(A.from_message(bot, msg(forward_chat=chat(cid=-1007), thread=22)))
    assert not err and thread == 22
    bot.get_chat.assert_awaited_with(-1007)


@pytest.mark.parametrize("text,word", [
    ("https://t.me/+AbCdEf", "Dəvət linki"),
    ("salam necəsən", "@username"),
    ("???", "@username"),
    ("", "@username"),
])
def test_from_message_rejects_junk_with_a_hint(text, word):
    bot = bot_for()
    found, thread, err = run(A.from_message(bot, msg(text)))
    assert found is None and word in err
    bot.get_chat.assert_not_awaited()
