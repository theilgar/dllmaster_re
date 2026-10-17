"""📢 "Depo kanalı" səhifəsi: əsas / YouTube yuvaları + əlavə kanallar BİR yerdə; kanalı təyin etmək ortaq yoxlamanı işlədir."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher

from bot.core import audio_cache, depo_shards as D
from bot.core.depo_helpers import HelperPool
from bot.plugins import depo_channel_plugin as P
from tests.test_depo_shards import BOT, Store

MAIN, B = -1003987878159, -1003993387505


class FakeDB:
    def __init__(self):
        self.d = {}

    def get_setting(self, key, default=None):
        return self.d.get(key, default)

    def set_setting(self, key, value):
        self.d[key] = value


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(audio_cache, "get_db", lambda: db)
    monkeypatch.setattr(audio_cache, "_depo", {"id": MAIN, "checked": 0, "error": None})
    monkeypatch.setattr(audio_cache, "_depo_yt", {"id": None, "checked": 0, "error": None})
    monkeypatch.setattr(D, "_cfg", Store())
    for name in ("_ids", "_primary", "_pause", "_sent", "_main_ok", "_bad", "_slot_at"):
        monkeypatch.setattr(D, name, {})
    monkeypatch.setattr(D, "capacity_fn", None)
    store = {}
    monkeypatch.setattr(HelperPool, "_get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(HelperPool, "_set", staticmethod(lambda k, v: store.__setitem__(k, str(v))))
    monkeypatch.setattr(HelperPool, "_init_table", lambda self: None)
    ctx = MagicMock()
    ctx.dp = Dispatcher()
    ctx.bot = MagicMock()
    ctx.is_creator = lambda uid: uid == 1
    ctx.depo_helpers = HelperPool(ctx)
    ctx.depo_helpers.verify_all_shards = AsyncMock()
    P.setup(ctx)
    return types.SimpleNamespace(ctx=ctx, db=db, pool=ctx.depo_helpers)


def handler(env, name):
    for obs in env.ctx.dp.observers.values():
        for h in obs.handlers:
            if h.callback.__name__ == name:
                return h.callback
    raise AssertionError(name)


def click(env, data):
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message.chat.id, cb.message.message_id = 10, 20
    cb.message.edit_text = AsyncMock()
    asyncio.run(handler(env, "dc_cb")(cb))
    return cb


def shown(cb):
    call = cb.message.edit_text.await_args
    kb = call.kwargs["reply_markup"]
    return call.args[0], [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard]


def flat(rows):
    return [b for r in rows for b in r]


def tg_chat(cid, username, kind="channel"):
    return types.SimpleNamespace(id=cid, type=kind, username=username, title=f"T {username}", has_protected_content=False)


def say(env, text=None, forward=None, thread=None, uid=1):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.text = text
    msg.message_thread_id = thread
    msg.reply_to = None
    msg.forward_origin = types.SimpleNamespace(chat=forward) if forward else None
    msg.forward_from_chat = None
    msg.delete = AsyncMock()
    msg.reply = AsyncMock()
    msg.answer = AsyncMock(return_value=types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=5))
    asyncio.run(handler(env, "on_input")(msg))
    return msg


def bot_admin(env, chats, status="administrator"):
    env.ctx.bot.get_chat = AsyncMock(side_effect=lambda r: chats[r])
    env.ctx.bot.get_me = AsyncMock(return_value=types.SimpleNamespace(id=777))
    env.ctx.bot.get_chat_member = AsyncMock(return_value=types.SimpleNamespace(status=status, can_post_messages=True))
    env.ctx.bot.edit_message_text = AsyncMock()
    env.ctx.bot.export_chat_invite_link = AsyncMock(return_value="https://t.me/+x")


# ── görünüş ──
def test_page_without_extra_channels_points_to_adding_one(env):
    text, rows = shown(click(env, "dc:panel"))
    assert "Əlavə kanallar" in text and "yoxdur" in text and "yükü bölmək" in text and "tutumu artırır" in text
    datas = [d for _, d in flat(rows)]
    assert "df:sha" in datas and "df:sh" in datas and "dc:check" in datas and "dc:chat" in datas and "dc:chat_yt" in datas
    assert rows[-1][-1][1] == "dc:close" and "menu:depo" in [d for _, d in rows[-1]]            # naviqasiya sonuncudur
    assert all(len(d.encode()) <= 64 for d in datas)


def test_page_lists_the_extra_channels_with_state_policy_and_count(env):
    D.add("@depo2")
    D._ids["@depo2"] = {"id": B, "ts": 0}
    D.set_main_ok(B, True)
    asyncio.run(D.choose(BOT, "ytm", MAIN, None))
    D.set_policy("manual")
    D.set_weight("@depo2", 300)
    D.pause(MAIN, 600)
    text, rows = shown(click(env, "dc:panel"))
    assert "@depo2" in text and "🌊" in text and "✋ manual" in text and "75%" in text
    assert ("📦 Kanallar: 2", "df:sh") in flat(rows) and ("➕ Əlavə kanal", "df:sha") in flat(rows)


def test_check_also_verifies_extra_channels(env, monkeypatch):
    env.ctx.bot.get_chat = AsyncMock(return_value=tg_chat(MAIN, "dllmasterdepo"))
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=MAIN))
    D.add("@depo2")
    D._ids["@depo2"] = {"id": B, "ts": 0}
    D.set_main_ok(B, True)
    D.capacity_fn = lambda cid: 3
    cb = click(env, "dc:check")
    env.pool.verify_all_shards.assert_awaited_once()
    assert "əlavə kanal hazır" in cb.answer.await_args.args[0] and "1/1" in cb.answer.await_args.args[0]


# ── kanalı təyin etmək: ortaq yoxlama ──
def test_setting_the_main_channel_by_forwarding_a_post(env):
    bot_admin(env, {B: tg_chat(B, "depo2")})
    click(env, "dc:chat")
    say(env, forward=tg_chat(B, "depo2"), thread=9)
    assert env.db.d["depo:channel"] == "@depo2" and env.db.d["depo:thread"] == "9"
    assert env.db.d["depo:link_auto"] == "https://t.me/depo2"


def test_setting_the_youtube_channel_by_username(env):
    bot_admin(env, {"@ytdepo": tg_chat(-1004000000001, "ytdepo")})
    click(env, "dc:chat_yt")
    say(env, "@ytdepo")
    assert env.db.d["depo:channel_yt"] == "@ytdepo" and env.db.d["depo:title_yt"] == "T ytdepo"


def test_a_channel_where_the_bot_is_not_admin_is_refused_with_the_reason(env):
    bot_admin(env, {"@depo2": tg_chat(B, "depo2")}, status="member")
    click(env, "dc:chat")
    msg = say(env, "@depo2")
    assert "depo:channel" not in env.db.d
    assert "administrator" in msg.reply.await_args.args[0]


def test_invite_links_and_junk_get_a_hint_and_waiting_continues(env):
    click(env, "dc:chat")
    assert "Dəvət linki" in say(env, "https://t.me/+AbCd").reply.await_args.args[0]
    assert "@username" in say(env, "salam necəsən").reply.await_args.args[0]
    assert env.ctx.menu_waiting_text(1)                                           # hələ gözləyir


def test_other_buttons_still_work(env):
    click(env, "dc:show")
    assert env.db.d["depo:show_start"] == "0"
    click(env, "dc:clear_yt")
    assert env.db.d["depo:channel_yt"] == ""
    click(env, "dc:link")
    assert "/start düyməsinin linki" in shown(click(env, "dc:link"))[0]
    say(env, "https://t.me/depo2")
    assert env.db.d["depo:link"] == "https://t.me/depo2"
