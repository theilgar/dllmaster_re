"""🎛 Köməkçi tənzimləmələri: /depo → Köməkçi botlar → Tənzimləmələr düymələri və mətn daxiletməsi (handlers.setup)."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher

import bot.depo.handlers as H
from bot.core import depo_helpers, depo_tuning as T


@pytest.fixture
def env(monkeypatch):
    store = {}
    monkeypatch.setattr(depo_helpers.HelperPool, "_get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(depo_helpers.HelperPool, "_set", staticmethod(lambda k, v: store.__setitem__(k, str(v))))
    monkeypatch.setattr(depo_helpers.HelperPool, "_init_table", lambda self: None)
    pool = depo_helpers.HelperPool(MagicMock())
    monkeypatch.setattr(H, "Filler", MagicMock())
    monkeypatch.setattr(H, "TgUser", MagicMock())
    monkeypatch.setattr(H.depo_helpers, "install", lambda ctx: pool)
    monkeypatch.setattr(H, "spawn", lambda coro: coro.close())
    ctx = MagicMock()
    ctx.dp = Dispatcher()
    ctx.bot = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.creator_id = 1
    ctx.is_creator = lambda uid: uid == 1
    H.setup(ctx)
    return types.SimpleNamespace(ctx=ctx, pool=pool, store=store)


def _handler(env, name):
    for obs in env.ctx.dp.observers.values():
        for h in obs.handlers:
            if h.callback.__name__ == name:
                return h.callback
    raise AssertionError(name)


def _click(env, data):
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message.chat.id, cb.message.message_id = 10, 20
    cb.message.edit_text = AsyncMock()
    asyncio.run(_handler(env, "depo_callback")(cb))
    return cb


def _shown(cb):
    """Redaktə olunan mesajın (mətn, düymə callback-ləri)."""
    call = cb.message.edit_text.await_args
    datas = [b.callback_data for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    return call.args[0], datas


def test_hub_opens_and_every_button_opens_its_picker(env):
    text, datas = _shown(_click(env, "df:tn"))
    assert "Tənzimləmələr" in text and len(text) < 4096
    for key in T.TUNABLES:
        assert f"df:tn:{key}" in datas
        ptext, pdatas = _shown(_click(env, f"df:tn:{key}"))
        assert T.TUNABLES[key].label in ptext and f"df:tm:{key}:manual" in pdatas


def test_mode_toggle_then_steppers_then_back_to_auto(env):
    cb = _click(env, "df:tm:group_interval:manual")
    _, datas = _shown(cb)
    assert env.pool.tune_mode("group_interval") == "manual" and "df:ts:group_interval:+0.2" in datas
    _click(env, "df:ts:group_interval:+0.2")
    _click(env, "df:ts:group_interval:+1")
    assert env.pool.tune("group_interval") == 4.4
    _click(env, "df:tm:group_interval:auto")
    assert env.pool.tune_mode("group_interval") == "auto" and env.pool.tune("group_interval") == 3.2


def test_every_stepper_button_of_every_setting_works(env):
    for key, t in T.TUNABLES.items():
        _click(env, f"df:tm:{key}:manual")
        for d in t.steps:
            _click(env, f"df:ts:{key}:{d:+g}")
        assert t.lo <= env.pool.tune(key) <= t.hi


def test_reset_and_all_auto_buttons(env):
    env.pool.note_flood("rate")
    _, datas = _shown(_click(env, "df:tn:rate"))
    assert "df:tr:rate" in datas
    _click(env, "df:tr:rate")
    assert env.pool.rate == 18
    env.pool.set_rate(5)
    _click(env, "df:tna")
    assert env.pool.tune_mode("rate") == "auto"


def test_old_rate_picker_buttons_still_work(env):
    assert "Sürət limiti" in _shown(_click(env, "df:pick:hpr"))[0]
    _click(env, "df:vstep:hpr:+5")
    assert env.pool.rate == 23 and env.pool.tune_mode("rate") == "manual"


def test_unknown_key_and_bad_step_do_not_crash(env):
    assert "Tənzimləmələr" in _shown(_click(env, "df:tn:nope"))[0]
    _click(env, "df:ts:rate:abc")


def test_typed_value_sets_manual(env):
    cb = _click(env, "df:tv:sink_interval")
    assert "Çevirmə aralığı" in _shown(cb)[0]
    msg = MagicMock()
    msg.text = "2,5"
    msg.from_user.id = 1
    msg.delete = AsyncMock()
    asyncio.run(_handler(env, "helper_rate_input")(msg))
    assert env.pool.sink_interval() == 2.5 and env.pool.tune_mode("sink_interval") == "manual"
    env.ctx.bot.edit_message_text.assert_awaited()


def test_turbo_button_toggles_profile_and_mentions_kept_manual_settings(env):
    _click(env, "df:tm:poll_every:manual")
    text, datas = _shown(_click(env, "df:tpf"))
    assert env.pool.turbo and env.pool.rate == 20 and "df:tpf" in datas
    assert "Köməkçi turbo aktivdir" in text and "1 manual ayara toxunulmadı" in text
    text, _ = _shown(_click(env, "df:tpf"))
    assert not env.pool.turbo and env.pool.rate == 18 and "Balanslı" in text


def test_guide_button_opens_guide_and_back_returns_to_hub(env):
    text, datas = _shown(_click(env, "df:tng"))
    assert "Bələdçi" in text and "df:tn" in datas
    assert "Tənzimləmələr" in _shown(_click(env, "df:tn"))[0]


def test_stacks_command_writes_the_report_and_replies_with_a_summary(env, monkeypatch, tmp_path):
    from aiogram.filters import CommandObject
    from bot.core import stall_watch
    monkeypatch.setattr(stall_watch, "_ROOT", str(tmp_path))
    msg = MagicMock()
    msg.from_user.id = 1
    msg.answer = AsyncMock()

    async def go():
        await _handler(env, "depo_cmd")(msg, CommandObject(command="depo", args="stacks"))
    asyncio.run(go())
    text = msg.answer.await_args.args[0]
    assert "<pre>" in text and "stall_" in text
    assert list((tmp_path / "bot_logs").glob("stall_*.txt"))
