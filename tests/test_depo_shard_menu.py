"""📦 Depo kanalları menyusu: hub, kanal səhifəsi, əlavə et (yazı / yönləndirmə), köməkçilər, sil, panel qaydaları."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import audio_cache, depo_shards as D
from bot.depo import shard_menu as M
from tests.test_depo_shard_cmd import _bot, _chat, fresh_shards  # noqa: F401  (autouse fixture)
from tests.test_depo_tuning_flow import _click, _handler, _shown, env  # noqa: F401


@pytest.fixture(autouse=True)
def main_depo(monkeypatch):
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=-1001))


def _datas(env_, data):
    return _shown(_click(env_, data))[1]


def _text_in(env_, text, uid=1, forwarded_from=None):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.text = text
    msg.delete = AsyncMock()
    msg.answer = AsyncMock()
    msg.forward_origin = types.SimpleNamespace(chat=types.SimpleNamespace(id=forwarded_from)) if forwarded_from else None
    msg.forward_from_chat = None
    msg.message_thread_id = None
    msg.reply_to = None
    asyncio.run(_handler(env_, "shard_input")(msg))
    return msg


def test_helpers_page_has_the_channels_button(env):
    assert "dc:panel" in _datas(env, "df:hp")                                    # köməkçi səhifəsindən → Depo kanalı
    env.pool.set_scope("off")
    assert "dc:panel" in _datas(env, "df:hp")                                    # söndürülüb olsa da görünür


def test_hub_without_extra_channels_explains_and_offers_adding(env):
    text, datas = _shown(_click(env, "df:sh"))
    assert "Əlavə kanal yoxdur" in text and "df:sha" in datas and "dc:panel" in datas        # geri → Depo kanalı
    assert not any(d.startswith(("df:shc", "df:shx")) for d in datas)


def test_add_by_typing_a_username_then_hub_lists_it(env):
    _bot(env, {"@depo2": -1002})
    env.pool.verify_shard = AsyncMock(return_value=(0, 3))
    text, datas = _shown(_click(env, "df:sha"))
    assert "Kanal əlavə et" in text and "df:sh" in datas
    _text_in(env, "https://t.me/depo2/7")                                        # mövzu linkdən çıxarılır
    assert D.extras() == [{"ref": "@depo2", "thread": 7}]
    shown = env.ctx.bot.edit_message_text.await_args.kwargs
    assert "Kanal əlavə olundu" in shown["text"] and "0/3" in shown["text"]
    datas = [b.callback_data for r in shown["reply_markup"].inline_keyboard for b in r]
    assert "df:sh:0" in datas and "df:shc" in datas and "df:shx" in datas


def test_add_by_forwarding_a_channel_post(env):
    _bot(env, {-1002: -1002})
    env.pool.verify_shard = AsyncMock(return_value=(2, 3))
    _click(env, "df:sha")
    _text_in(env, None, forwarded_from=-1002)
    assert D.extras() == [{"ref": "-1002", "thread": None}]


def test_bad_input_keeps_waiting_and_other_users_are_ignored(env):
    _bot(env, {})
    _click(env, "df:sha")
    _text_in(env, "???")
    assert D.extras() == [] and "@username" in env.ctx.bot.edit_message_text.await_args.kwargs["text"]
    assert env.ctx.depo_waiting(1)                                             # düzgün yazana qədər gözləyir
    _text_in(env, "@salam")                                                    # ad kimi tanınır, amma kanal tapılmır
    assert D.extras() == [] and "tapılmadı" in env.ctx.bot.edit_message_text.await_args.kwargs["text"]
    assert env.ctx.depo_waiting(1)                                             # səhv yazılıbsa gözləmə davam edir
    assert not env.ctx.depo_waiting(999)                                       # gözləmə yalnız creator üçündür
    _text_in(env, "t.me/+AbCd")
    assert "Dəvət linki" in env.ctx.bot.edit_message_text.await_args.kwargs["text"] and D.extras() == []


def test_leaving_the_prompt_cancels_the_wait(env):
    _click(env, "df:sha")
    _click(env, "df:hp")
    ctx_wait = env.ctx.depo_waiting
    assert not ctx_wait(1)


def test_waiting_is_visible_to_the_music_search_guard(env):
    _click(env, "df:sha")
    assert env.ctx.depo_waiting(1)


def _with_channel(env):
    _bot(env, {"@b": -1002})
    env.pool.verify_shard = AsyncMock(return_value=(1, 3))
    D.add("@b")
    asyncio.run(D.resolve(env.ctx.bot, "@b"))


def test_channel_page_buttons(env):
    _with_channel(env)
    text, datas = _shown(_click(env, "df:sh:0"))
    assert "@b" in text and "df:shh:0:0" in datas and "df:shc:0" in datas and "df:shd:0" in datas and "df:sh" in datas
    assert all(len(d.encode()) <= 64 for d in datas)
    assert "Hub" not in text and "Kanal tapılmadı" not in _shown(_click(env, "df:sh:0"))[0]
    assert "Kanal tapılmadı" in _shown(_click(env, "df:sh:5"))[0]               # köhnə düymə → hub + qeyd


def test_add_helpers_button_runs_in_the_background_once(env, monkeypatch):
    _with_channel(env)
    env.pool.shard_add_helpers = AsyncMock(return_value={"added": 2, "already": 0, "failed": 0, "errors": [], "id": -1002})
    spawned = []
    monkeypatch.setattr(M, "spawn", lambda coro: spawned.append(coro))
    text, _ = _shown(_click(env, "df:shh:0:24"))
    assert "Userbot köməkçiləri əlavə edir" in text and len(spawned) == 1
    assert "Artıq gedir" in _shown(_click(env, "df:shh:0:24"))[0] and len(spawned) == 1
    env.ctx.bot.send_message = AsyncMock()
    asyncio.run(spawned[0])
    env.pool.shard_add_helpers.assert_awaited_with("@b", 24)
    assert "yeni: 2" in env.ctx.bot.send_message.await_args.args[1]
    _click(env, "df:shh:0:0")                                                    # bitəndən sonra yenidən işləyir
    assert len(spawned) == 2
    spawned[1].close()


def test_check_and_clear_buttons(env):
    _with_channel(env)
    D.pause(-1002, 900)
    assert "flood" in _shown(_click(env, "df:sh"))[0]
    assert "Yoxlandı" in _shown(_click(env, "df:shc"))[0] and "Yoxlandı" in _shown(_click(env, "df:shc:0"))[0]
    assert "ləğv olundu" in _shown(_click(env, "df:shx"))[0] and D.left(-1002) == 0
    assert env.pool.verify_shard.await_count >= 2


def test_remove_needs_confirmation(env):
    _with_channel(env)
    text, datas = _shown(_click(env, "df:shd:0"))
    assert "siyahıdan silinsin" in text and "df:shdy:0" in datas and "df:sh:0" in datas
    assert D.extras() != []
    assert "silindi" in _shown(_click(env, "df:shdy:0"))[0] and D.extras() == []
    assert "Əlavə kanal yoxdur" in _shown(_click(env, "df:shdy:0"))[0]           # təkrar basış zərərsizdir


# ── 🎚 siyasət və paylar ──
def _buttons(env_, data):
    cb = _click(env_, data)
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard]


def test_policy_toggle_appears_with_an_extra_channel_and_switches_to_manual_weights(env):
    assert not any(d == "df:shp" for r in _buttons(env, "df:sh") for _, d in r)         # əlavə kanal yoxdur
    _with_channel(env)
    rows = _buttons(env, "df:sh")
    assert ("Siyasət: 🤖 Avto", "df:shp") in [b for r in rows for b in r]
    assert not any(d.startswith("df:shw:") for r in rows for _, d in r)                  # avtoda pay düymələri yoxdur
    text, datas = _shown(_click(env, "df:shp"))
    assert D.policy() == "manual" and "Manual" in text and "df:shw:m" in datas and "df:shw:0" in datas
    rows = _buttons(env, "df:sh")
    assert ("Siyasət: ✋ Manual", "df:shp") in [b for r in rows for b in r]
    assert ("⚖️ əsas depo: 50%", "df:shw:m") in [b for r in rows for b in r]
    assert all(len(r) <= 2 for r in rows[:-1])
    _click(env, "df:shp")
    assert D.policy() == "auto"


def test_weight_page_steps_resets_and_shows_the_share(env):
    _with_channel(env)
    D.set_policy("manual")
    text, datas = _shown(_click(env, "df:shw:0"))
    assert "@b" in text and "50%" in text and "df:shs:0:+10" in datas and "df:shs:0:=100" in datas and "df:sh" in datas
    assert all(len(d.encode()) <= 64 for d in datas)
    text, _ = _shown(_click(env, "df:shs:0:-50"))
    assert D.weight("@b") == 50 and "33%" in text                                          # 100 : 50
    _click(env, "df:shs:m:+200")
    assert D.weight("main") == 300
    _click(env, "df:shs:0:=0")
    assert D.weight("@b") == 0
    _click(env, "df:shs:0:=100")
    assert D.weight("@b") == 100
    _click(env, "df:shs:0:evil")                                                          # yanlış dəyər zərərsizdir
    _click(env, "df:shs:9:+10")                                                           # olmayan kanal da
    assert D.weight("@b") == 100
    assert "Kanal tapılmadı" in _shown(_click(env, "df:shw:9"))[0]
