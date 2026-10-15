"""Botun öz chat_member hadisələri: yalnız heç bir hesab izləmədikdə canlı hədəfə göndərilir."""
import time
import types

import pytest

import bot.plugins.audit_plugin as ap


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text, kw))


class FakeClient:
    def __init__(self, connected=True):
        self._c = connected

    def is_connected(self):
        return self._c


def _user(uid, name, username=None):
    return types.SimpleNamespace(id=uid, full_name=name, username=username)


@pytest.fixture
def env(logger_mod, ub):
    logger_mod._recent_events.clear()
    ap._MEMBER_CACHE.clear()
    bot = FakeBot()
    ctx = types.SimpleNamespace(bot=bot, creator_id=42, telethon_client=None, voice_assistant=None)
    ub.context = ctx
    chat = types.SimpleNamespace(id=-1001, title="Qrup")
    return types.SimpleNamespace(lg=logger_mod, ub=ub, bot=bot, ctx=ctx, chat=chat,
                                 actor=_user(1, "Admin", "adm"), target=_user(2, "Üzv", "uzv"))


async def _push(e, action="ban", details=("Qrupdan banlandı",)):
    return await ap.push_live(e.ctx, e.chat, e.actor, e.target, action, list(details), time.time())


async def test_nothing_sent_when_audit_is_off(env):
    assert await _push(env) is False and not env.bot.sent


async def test_sends_to_group_target_with_topic_and_clickable_user(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.lg.set_group_target(env.ub, -1001, "kick", "-100777", "9")
    assert await _push(env) is True
    chat, text, kw = env.bot.sent[0]
    assert chat == -100777 and kw["message_thread_id"] == 9 and kw["parse_mode"] == "HTML"
    assert "Ban" in text and 'tg://user?id=2' in text and "@uzv" in text and "İcra edən" in text


async def test_falls_back_to_creator_when_no_target(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    assert await _push(env, "unban", ("Ban götürüldü",)) is True
    assert env.bot.sent[0][0] == 42 and "message_thread_id" not in env.bot.sent[0][2]


async def test_skipped_when_assistant_watches_group(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.ctx.voice_assistant = types.SimpleNamespace(
        slots=[types.SimpleNamespace(running=True, voice_enabled=True, client=object())])
    assert await _push(env) is False and not env.bot.sent


async def test_skipped_when_userbot_connected(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.ctx.telethon_client = FakeClient(True)
    assert await _push(env) is False


async def test_sent_when_userbot_disconnected(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.ctx.telethon_client = FakeClient(False)
    assert await _push(env) is True


async def test_duplicate_within_window_is_not_resent(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    assert await _push(env) is True
    assert await _push(env) is False and len(env.bot.sent) == 1


async def test_disabled_event_is_not_sent(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.lg.set_group_event_on(env.ub, -1001, "kick", False)
    assert await _push(env) is False


async def test_join_has_no_actor_row(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.actor = env.target
    assert await _push(env, "join", ()) is True
    assert "İcra edən" not in env.bot.sent[0][1] and "Üzv Qoşuldu" in env.bot.sent[0][1]


async def test_unknown_action_ignored(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    assert await _push(env, "title") is False


class MemberClient:
    """get_permissions('me') ilə üzvlüyü təyin edən saxta Telethon client."""

    def __init__(self, exc=None, connected=True):
        self.exc, self.calls, self._c = exc, 0, connected

    def is_connected(self):
        return self._c

    async def get_permissions(self, chat, who):
        self.calls += 1
        if self.exc:
            raise self.exc
        return object()


class UserNotParticipantError(Exception):
    pass


def _assistant_with(env, client):
    env.ctx.voice_assistant = types.SimpleNamespace(
        slots=[types.SimpleNamespace(running=True, voice_enabled=True, client=client)])


async def test_assistant_member_means_bot_stays_silent(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    _assistant_with(env, MemberClient())
    assert await _push(env) is False


async def test_bot_covers_group_when_assistant_is_not_a_member(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    _assistant_with(env, MemberClient(UserNotParticipantError()))
    assert await _push(env) is True and env.bot.sent


async def test_unknown_entity_counts_as_not_member(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    _assistant_with(env, MemberClient(ValueError("Could not find the input entity")))
    assert await _push(env) is True


async def test_transient_error_is_treated_as_watched(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    _assistant_with(env, MemberClient(TimeoutError("network")))
    assert await _push(env) is False                       # qeyri-müəyyən xəta → təkrar göndərmə


async def test_membership_is_cached(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    client = MemberClient()
    _assistant_with(env, client)
    await _push(env)
    env.lg._recent_events.clear()
    await _push(env)
    assert client.calls == 1


async def test_userbot_member_check_when_group_source_is_userbot(env):
    env.lg.add_group(env.ub, -1001, "Qrup")
    env.lg.set_group_source(env.ub, -1001, "userbot")
    env.ctx.telethon_client = MemberClient(UserNotParticipantError())
    assert await _push(env) is True                        # userbot qoşuludur, amma üzv deyil → bot göndərir
    env.lg._recent_events.clear()
    env.ctx.telethon_client = MemberClient()
    ap._MEMBER_CACHE.clear()
    assert await _push(env) is False
