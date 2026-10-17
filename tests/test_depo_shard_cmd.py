"""/depo shard ... əmrləri və audio_cache.depo_target_chat_info: əlavə kanal yoxdursa davranış dəyişmir."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from bot.core import audio_cache, depo_shards as D
from tests.test_depo_shards import BOT, Store
from tests.test_depo_tuning_flow import _handler, env  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fresh_shards(monkeypatch):
    monkeypatch.setattr(D, "_cfg", Store())
    for name in ("_ids", "_primary", "_pause", "_sent", "_main_ok", "_bad", "_slot_at"):
        monkeypatch.setattr(D, name, {})
    monkeypatch.setattr(D, "_loaded", False)
    monkeypatch.setattr(D, "capacity_fn", None)


def _say(env, args, uid=1):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()
    asyncio.run(_handler(env, "depo_cmd")(msg, CommandObject(command="depo", args=args)))
    return msg


def _text(msg):
    return msg.answer.await_args.args[0] if msg.answer.await_args else ""


def _chat(ref, cid):
    return types.SimpleNamespace(id=cid, has_protected_content=False, type="channel", title=f"T{abs(cid) % 100}",
                                 username=ref.lstrip("@") if isinstance(ref, str) else None)


def _bot(env, ids, status="administrator"):
    """ids: ref/id → chat id. Bot hər kanalda `status` statusundadır (administrator + Post messages)."""
    def get_chat(ref):
        if ref not in ids:
            raise RuntimeError("chat not found")
        return _chat(ref, ids[ref])
    env.ctx.bot.get_chat = AsyncMock(side_effect=get_chat)
    env.ctx.bot.get_me = AsyncMock(return_value=types.SimpleNamespace(id=777))
    env.ctx.bot.get_chat_member = AsyncMock(return_value=types.SimpleNamespace(status=status, can_post_messages=True))
    env.ctx.bot.id = 777
    env.pool.ctx = env.ctx                                          # hovuz və plugin eyni kontekstdədir
    return env.ctx.bot


def test_status_without_extra_channels(env):
    text = _text(_say(env, "shard"))
    assert "Əlavə kanal yoxdur" in text and "/depo shard add" in text


def test_only_the_creator_can_use_it(env):
    assert _say(env, "shard add @b", uid=999).answer.await_count == 0
    assert D.extras() == []


def test_add_unreachable_channel_is_refused(env):
    _bot(env, {})
    text = _text(_say(env, "shard add @nope"))
    assert "tapılmadı" in text and D.extras() == []


def test_add_registers_the_channel_and_reports_helpers(env, monkeypatch):
    _bot(env, {"@b": -1002})
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    env.pool.verify_shard = AsyncMock(return_value=(0, 5))
    env.pool.verify_main = AsyncMock(return_value=True)
    text = _text(_say(env, "shard add @b 12"))
    assert D.extras() == [{"ref": "@b", "thread": 12}]
    assert "Kanal əlavə olundu" in text and "0/5" in text and "/depo shard helpers @b" in text
    assert "Əsas bot bu kanalda admin deyil" not in text
    assert "Əlavə kanal yoxdur" not in text
    assert "artıq siyahıdadır" in _text(_say(env, "shard add @b"))


def test_add_refuses_the_main_depo_and_remove_works(env, monkeypatch):
    _bot(env, {"@main": -1001, "@b": -1002})
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    assert "artıq əsas depodur" in _text(_say(env, "shard add @main"))
    env.pool.verify_shard = AsyncMock(return_value=(1, 1))
    _say(env, "shard add @b")
    assert "silindi" in _text(_say(env, "shard rm @b")) and D.extras() == []
    assert "Belə kanal yoxdur" in _text(_say(env, "shard rm @b"))


def test_helpers_subcommand_runs_in_the_background_and_reports(env, monkeypatch):
    _bot(env, {"@b": -1002})
    env.pool.shard_add_helpers = AsyncMock(return_value={"added": 3, "already": 1, "failed": 1, "errors": ["@x: ❌ boom"],
                                                         "id": -1002})
    spawned = []
    monkeypatch.setattr("bot.depo.shard_cmd.spawn", lambda coro: spawned.append(coro))
    msg = _say(env, "shard helpers @b 4")
    assert "Userbot köməkçiləri əlavə edir" in _text(msg) and len(spawned) == 1
    asyncio.run(spawned[0])
    env.pool.shard_add_helpers.assert_awaited_with("@b", 4)
    done = msg.answer.await_args_list[-1].args[0]
    assert "yeni: 3" in done and "alınmadı: 1" in done and "boom" in done


def test_clear_and_unknown_subcommand(env):
    env.pool.note_channel_flood(1000, "x", -1001)
    assert D.left(-1001) > 900
    assert "ləğv olundu" in _text(_say(env, "shard clear")) and D.left(-1001) == 0
    assert "Naməlum əmr" in _text(_say(env, "shard wat"))


# ── audio_cache.depo_target_chat_info ──
def test_target_is_unchanged_without_extra_channels(monkeypatch):
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    monkeypatch.setattr(audio_cache, "depo_thread_setting", lambda: 5)
    assert asyncio.run(audio_cache.depo_target_chat_info(MagicMock(), "ytm")) == (-1001, 5)
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=None))
    assert asyncio.run(audio_cache.depo_target_chat_info(MagicMock(), "ytm")) == (None, 5)


def test_target_balances_between_channels_for_the_main_depo_only(monkeypatch):
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    monkeypatch.setattr(audio_cache, "depo_thread_setting", lambda: None)
    monkeypatch.setattr(audio_cache, "depo_yt_chat_id", AsyncMock(return_value=-1009))
    monkeypatch.setattr(audio_cache, "depo_yt_thread_setting", lambda: 3)
    D.add("@b")
    asyncio.run(D.resolve(BOT, "@b"))
    D.set_main_ok(-1002, True)
    D.pause(-1001, 1000)
    assert asyncio.run(audio_cache.depo_target_chat_info(BOT, "ytm"))[0] == -1002            # əsas flood-dadır
    assert asyncio.run(audio_cache.depo_target_chat_info(BOT, "yt")) == (-1009, 3)           # YouTube yuvası toxunulmaz


def test_add_is_refused_while_the_main_bot_is_not_admin_and_works_after(env, monkeypatch):
    _bot(env, {"@b": -1002}, status="member")                       # kanalda adi üzv — kifayət deyil
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    env.pool.verify_shard = AsyncMock(return_value=(3, 5))
    text = _text(_say(env, "shard add @b"))
    assert "administrator</b> deyil" in text and D.extras() == []
    _bot(env, {"@b": -1002})                                        # admin edildi
    assert "Kanal əlavə olundu" in _text(_say(env, "shard add @b")) and D.main_ok(-1002)


def test_check_reverifies_the_main_bots_admin_status(env, monkeypatch):
    _bot(env, {"@b": -1002})
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))
    env.pool.verify_shard = AsyncMock(return_value=(1, 1))
    _say(env, "shard add @b")
    D.set_main_ok(-1002, False)
    assert not D.usable(-1002)
    _say(env, "shard check")
    assert D.main_ok(-1002)


def test_forbidden_upload_to_an_extra_channel_does_not_reset_the_main_depo(monkeypatch, tmp_path):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x" * 10)
    D.add("@b")
    asyncio.run(D.resolve(BOT, "@b"))
    monkeypatch.setattr(audio_cache, "_depo", {"id": -1001, "checked": 0, "error": None})
    monkeypatch.setattr(audio_cache, "depo_target_chat_info", AsyncMock(return_value=(-1002, None)))
    bot = MagicMock()
    bot.send_audio = AsyncMock(side_effect=Exception("Telegram server says - Forbidden: bot is not a member of the channel chat"))
    res = asyncio.run(audio_cache.upload_to_depo(bot, str(f), "a.m4a", "t", "p", 10, "https://y/x", "vid1"))
    assert res is None
    assert audio_cache._depo["id"] == -1001 and audio_cache._depo["error"] is None       # əsas depo toxunulmaz
    assert D.is_bad(-1002)


def test_forbidden_upload_to_the_main_depo_still_resets_it(monkeypatch, tmp_path):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x" * 10)
    monkeypatch.setattr(audio_cache, "_depo", {"id": -1001, "checked": 0, "error": None})
    monkeypatch.setattr(audio_cache, "depo_target_chat_info", AsyncMock(return_value=(-1001, None)))
    bot = MagicMock()
    bot.send_audio = AsyncMock(side_effect=Exception("Forbidden: bot was kicked"))
    assert asyncio.run(audio_cache.upload_to_depo(bot, str(f), "a.m4a", "t", "p", 10, "https://y/x", "vid2")) is None
    assert audio_cache._depo["id"] is None and "kicked" in audio_cache._depo["error"]


def test_policy_and_weight_commands(env):
    _bot(env, {"@b": -1002})
    D.add("@b")
    assert "avto" in _text(_say(env, "shard"))
    assert "manual" in _text(_say(env, "shard policy manual")) and D.policy() == "manual"
    text = _text(_say(env, "shard weight @b 30"))
    assert D.weight("@b") == 30 and "pay: 30" in text
    _say(env, "shard weight main 200")
    assert D.weight("main") == 200
    assert "Belə kanal yoxdur" in _text(_say(env, "shard weight @nope 5"))
    _say(env, "shard policy auto")
    assert D.policy() == "auto"
