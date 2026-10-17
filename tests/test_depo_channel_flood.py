"""🌊 Depo kanalının ortaq flood-u: kanal dayanır, sürət azalmır, başqa kanal varsa yükləmə ona keçir, istifadəçi gözləmir."""
import asyncio
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendAudio

from bot.core import depo_shards as D
from bot.core.depo_helpers import UPLOAD_CTX, Helper, HelperPool
from tests.test_depo_shards import BOT, Store

MAIN, B = -1001, -1002


@pytest.fixture
def pool(monkeypatch):
    store = {}
    monkeypatch.setattr(HelperPool, "_get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(HelperPool, "_set", staticmethod(lambda k, v: store.__setitem__(k, str(v))))
    monkeypatch.setattr(HelperPool, "_init_table", lambda self: None)
    monkeypatch.setattr(D, "_cfg", Store())
    for name in ("_ids", "_primary", "_pause", "_sent", "_main_ok", "_bad", "_slot_at"):
        monkeypatch.setattr(D, name, {})
    monkeypatch.setattr(D, "_loaded", False)
    p = HelperPool(MagicMock())
    p._depo = {"id": MAIN, "ts": time.monotonic()}
    D._primary["ytm"] = {"id": MAIN, "thread": None}
    for i in range(5):
        h = Helper(f"{i}:tok", False)
        h.ok = True
        h.me = types.SimpleNamespace(id=i, username=f"b{i}", first_name=f"b{i}")
        h.bot = AsyncMock(side_effect=_flood(2000))
        p.helpers.append(h)
    return p


def _flood(n):
    return TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=n)


def _method(chat=MAIN):
    return SendAudio(chat_id=chat, audio="x")


def _with_shard(pool):
    """B əlavə kanaldır, bütün köməkçilər orada admindir."""
    D.add("@b")
    asyncio.run(D.resolve(BOT, "@b"))
    D.set_main_ok(B, True)
    for h in pool.helpers:
        h.shard_state[B] = "ok"


def _probes(p):
    return sum(h.bot.await_count for h in p.helpers)


def _filler(coro, timeout=0.3):
    token = UPLOAD_CTX.set((object(), 0))                     # doldurucu yükləməsi
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout))
    finally:
        UPLOAD_CTX.reset(token)


def test_when_every_helper_hits_its_quota_the_channel_pauses_and_the_rate_is_untouched(pool):
    with pytest.raises(asyncio.TimeoutError):                 # kanal doludur, başqa kanal yoxdur → gözləyir
        _filler(pool.upload(_method(), MagicMock(), AsyncMock()))
    assert _probes(pool) == 5                                 # hər köməkçi bir dəfə yoxlandı
    assert pool.stats["quota"] == 5 and pool.stats["channel_flood"] == 1
    assert pool.channel_cool_left() > 1900 and pool.chat_exhausted(MAIN)
    assert pool.rate == 18                                    # kvota sürəti azaltmır


def test_one_bots_quota_does_not_stop_the_channel_the_other_bots_keep_posting(pool):
    ok = MagicMock()
    pool.helpers[0].bot = AsyncMock(side_effect=_flood(2000))
    for h in pool.helpers[1:]:
        h.bot = AsyncMock(return_value=ok)
    pool.register_foreign = AsyncMock()
    _filler(pool.upload(_method(), MagicMock(), AsyncMock()), timeout=2)
    sent_to = [c.args[0].chat_id for h in pool.helpers[1:] for c in h.bot.await_args_list]
    assert sent_to == [MAIN] and pool.stats.get("rerouted", 0) == 0     # kanal dəyişmədi, dayanmadı
    assert pool.channel_cool_left(MAIN) == 0 and not pool.chat_exhausted(MAIN)
    assert pool.helpers[0].cool_chat[MAIN] > time.monotonic() + 1900 and pool.helpers[0].cool_until == 0
    assert pool.rate == 18


def test_a_bot_out_of_quota_in_one_channel_still_works_in_the_other(pool):
    _with_shard(pool)
    pool.helpers[0].cool_chat[MAIN] = time.monotonic() + 3000
    h, wait = asyncio.run(pool._reserve(MAIN))
    assert h is not pool.helpers[0]                                  # əsas depoda başqa köməkçi seçilir
    h, wait = asyncio.run(pool._reserve(B))
    assert wait == 0 and pool.cool_left(pool.helpers[0], B) == 0 and pool.cool_left(pool.helpers[0], MAIN) > 2900


def test_when_the_channel_is_exhausted_new_uploads_go_to_the_other_channel_at_once(pool):
    _with_shard(pool)
    for h in pool.helpers:
        h.cool_chat[MAIN] = time.monotonic() + 2000
    assert pool.chat_exhausted(MAIN) and not pool.chat_exhausted(B)
    ok = MagicMock()
    for h in pool.helpers:
        h.bot = AsyncMock(return_value=ok)
    pool.register_foreign = AsyncMock()
    _filler(pool.upload(_method(), MagicMock(), AsyncMock()), timeout=2)
    sent_to = [c.args[0].chat_id for h in pool.helpers for c in h.bot.await_args_list]
    assert sent_to == [B] and pool.stats["rerouted"] == 1


def test_choose_avoids_an_exhausted_channel(pool):
    _with_shard(pool)
    D.capacity_fn = pool.shard_capacity
    D.exhausted_fn = pool.chat_exhausted
    for _ in range(30):
        D.note_sent(B)                                               # B daha yüklüdür
    assert asyncio.run(D.choose(BOT, "ytm", MAIN, None))[0] == MAIN
    for h in pool.helpers:
        h.cool_chat[MAIN] = time.monotonic() + 900
    assert asyncio.run(D.choose(BOT, "ytm", MAIN, None))[0] == B


def test_quota_cooldowns_survive_a_restart_and_clear_resets_them(pool):
    pool.helpers[0].cool_chat[MAIN] = time.monotonic() + 1200
    pool.helpers[1].cool_chat[B] = time.monotonic() + 600
    pool._save_cool(force=True)
    fresh = HelperPool(MagicMock())
    for src in pool.helpers:
        h = Helper(src.token, False)
        h.me = src.me
        fresh.helpers.append(h)
    fresh._load_cool()
    assert 1100 < fresh.cool_left(fresh.helpers[0], MAIN) <= 1200 and 500 < fresh.cool_left(fresh.helpers[1], B) <= 600
    assert fresh.cool_left(fresh.helpers[2], MAIN) == 0
    fresh.clear_channel_cool()
    assert all(not h.cool_chat for h in fresh.helpers)
    again = HelperPool(MagicMock())
    again.helpers = [Helper(h.token, False) for h in pool.helpers]
    for a, src in zip(again.helpers, pool.helpers):
        a.me = src.me
    again._load_cool()
    assert all(not h.cool_chat for h in again.helpers)               # təmizləmə də yadda qalır


def test_user_upload_goes_to_the_other_channel_or_the_main_bot(pool):
    pool.note_channel_flood(2000, "x", MAIN)
    make_request = AsyncMock(return_value="main-bot")
    assert UPLOAD_CTX.get() is None
    assert asyncio.run(pool.upload(_method(), MagicMock(), make_request)) == "main-bot"      # başqa kanal yoxdur
    assert _probes(pool) == 0 and pool.stats["fallback"] == 1
    _with_shard(pool)
    ok = MagicMock()
    for h in pool.helpers:
        h.bot = AsyncMock(return_value=ok)
    pool.register_foreign = AsyncMock()
    asyncio.run(pool.upload(_method(), MagicMock(), AsyncMock()))
    assert any(c.args[0].chat_id == B for h in pool.helpers for c in h.bot.await_args_list)


def test_a_channel_without_admin_helpers_gets_no_uploads_and_is_not_chosen(pool):
    D.add("@b")
    asyncio.run(D.resolve(BOT, "@b"))
    D.capacity_fn = pool.shard_capacity
    assert pool.shard_capacity(B) == 0 and pool.shard_capacity(MAIN) == 5
    assert asyncio.run(D.choose(BOT, "ytm", MAIN, None))[0] == MAIN
    h, _ = asyncio.run(pool._reserve(B))
    assert h is None                                          # B-də admin köməkçi yoxdur → əsas bota keçilir


def test_dead_error_on_an_extra_channel_only_marks_that_channel(pool):
    _with_shard(pool)
    D.pause(MAIN, 2000)
    for h in pool.helpers:
        h.bot = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: not enough rights"))
    make_request = AsyncMock(return_value="main-bot")
    assert asyncio.run(pool.upload(_method(), MagicMock(), make_request)) == "main-bot"
    assert all(h.ok for h in pool.helpers)                    # əsas depoda hələ də işləyirlər
    assert all(h.shard_state[B] == "bad" for h in pool.helpers if h.bot.await_count)


def test_dead_error_on_the_main_depo_still_disables_the_helper(pool):
    pool.helpers[0].bot = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="chat not found"))
    for h in pool.helpers[1:]:
        h.bot = AsyncMock(return_value=MagicMock())
    pool.register_foreign = AsyncMock()
    asyncio.run(pool.upload(_method(), MagicMock(), AsyncMock()))
    assert pool.helpers[0].ok is False and "chat not found" in pool.helpers[0].status


def test_short_flood_cools_one_helper_and_lowers_the_rate(pool):
    ok = MagicMock()
    for h in pool.helpers[1:]:
        h.bot = AsyncMock(return_value=ok)
    pool.helpers[0].bot = AsyncMock(side_effect=_flood(20))
    pool.register_foreign = AsyncMock()
    asyncio.run(pool.upload(_method(), MagicMock(), AsyncMock()))
    assert pool.channel_cool_left() == 0 and pool.rate == 14
    assert [bool(h.cool_chat.get(MAIN)) for h in pool.helpers] == [True, False, False, False, False]
    assert pool.helpers[0].cool_until == 0                           # yalnız bu kanal üçün


def test_same_flood_reported_by_other_helpers_is_counted_once(pool):
    pool.note_channel_flood(2000, "a", MAIN)
    pool.note_channel_flood(1999, "b", MAIN)
    pool.note_channel_flood(1997, "c", MAIN)
    assert pool.stats["channel_flood"] == 1


def test_flood_below_the_threshold_setting_is_not_a_channel_flood(pool):
    pool.set_tune("long_flood", 600)                          # manual: 500 san. hələ "uzun" sayılmır
    for h in pool.helpers:
        h.bot = AsyncMock(side_effect=_flood(500))
    with pytest.raises(asyncio.TimeoutError):
        _filler(pool.upload(_method(), MagicMock(), AsyncMock()))
    assert pool.channel_cool_left() == 0 and pool.rate == 14


def test_pause_survives_restart_and_clear_resets_it(pool, monkeypatch):
    pool.note_channel_flood(1000, "a", MAIN)
    monkeypatch.setattr(D, "_pause", {})
    monkeypatch.setattr(D, "_loaded", False)
    assert pool.channel_cool_left(MAIN) > 900
    pool.clear_channel_cool()
    assert pool.channel_cool_left(MAIN) == 0


def test_reserve_never_hands_out_a_slot_before_the_channel_pause_ends(pool):
    pool.note_channel_flood(300, "a", MAIN)
    h, wait = asyncio.run(pool._reserve(MAIN))
    assert h is not None and wait > 250


def test_is_depo_accepts_extra_channels(pool):
    _with_shard(pool)
    bot = MagicMock()
    assert asyncio.run(pool.is_depo(B, bot)) and asyncio.run(pool.is_depo(MAIN, bot))
    assert not asyncio.run(pool.is_depo(-999, bot))


def test_verify_shard_counts_only_admins_who_can_post(pool):
    _with_shard(pool)
    statuses = ["creator", "administrator", "member", "left", "administrator"]
    for h, st in zip(pool.helpers, statuses):
        can_post = False if h is pool.helpers[4] else True
        h.bot = MagicMock()
        h.bot.get_chat_member = AsyncMock(return_value=types.SimpleNamespace(status=st, can_post_messages=can_post))
    assert asyncio.run(pool.verify_shard(B)) == (2, 5)
    assert [h.shard_state[B] for h in pool.helpers] == ["ok", "ok", "bad", "bad", "bad"]


def test_a_worker_sleeping_through_a_flood_wakes_up_when_another_channel_becomes_usable(pool, monkeypatch):
    pool.note_channel_flood(900, "x", MAIN)                   # başqa kanal yoxdur → doldurucu gözləyir
    ok = MagicMock()
    for h in pool.helpers:
        h.bot = AsyncMock(return_value=ok)
    pool.register_foreign = AsyncMock()
    D.add("@b")
    asyncio.run(D.resolve(BOT, "@b"))                         # kanal siyahıdadır, amma hələ yararlı deyil
    real_sleep = asyncio.sleep
    ticks = []

    async def fast_sleep(n):                                  # 15 san.-lik addımlar real vaxt almasın
        ticks.append(n)
        if len(ticks) == 2:                                   # gözləmə zamanı əsas bot və köməkçilər admin oldu
            D.set_main_ok(B, True)
            for h in pool.helpers:
                h.shard_state[B] = "ok"
        await real_sleep(0)
    monkeypatch.setattr("bot.core.depo_helpers.asyncio.sleep", fast_sleep)
    _filler(pool.upload(_method(), MagicMock(), AsyncMock()), timeout=2)
    assert ticks[:2] == [15.0, 15.0] and len(ticks) < 10     # 900 san.-nin hamısı gözlənilmədi
    sent_to = [c.args[0].chat_id for h in pool.helpers for c in h.bot.await_args_list]
    assert sent_to == [B]


def test_normal_pacing_wait_is_not_cut_short(pool, monkeypatch):
    slept = []

    async def fast_sleep(n):
        slept.append(n)
    monkeypatch.setattr("bot.core.depo_helpers.asyncio.sleep", fast_sleep)
    assert asyncio.run(pool._wait_slot(40, MAIN)) is False and slept == [15.0, 15.0, 10.0]


def test_a_burst_of_workers_is_spread_out_by_the_channel_rate(pool):
    token = UPLOAD_CTX.set((object(), 0))
    try:
        pool.set_tune("chat_rate", 60)                         # saniyədə 1
        starts = [asyncio.run(pool._reserve(MAIN))[1] for _ in range(6)]
    finally:
        UPLOAD_CTX.reset(token)
    gaps = [round(b - a, 1) for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.9 for g in gaps)                         # 5 köməkçi eyni anda boş olsa belə, 1 san. aralıq


def test_user_uploads_do_not_queue_behind_the_filler_burst(pool):
    token = UPLOAD_CTX.set((object(), 0))
    try:
        pool.set_tune("chat_rate", 6)                          # çox yavaş: 10 san. aralıq
        for _ in range(5):
            asyncio.run(pool._reserve(MAIN))
    finally:
        UPLOAD_CTX.reset(token)
    assert UPLOAD_CTX.get() is None
    _, wait = asyncio.run(pool._reserve(MAIN))
    assert wait < 5                                            # istifadəçi doldurucunun növbəsini gözləmir


def test_channel_rate_is_off_by_default_and_floods_never_change_it(pool):
    assert pool.tune("chat_rate") == 0 and pool.tune_mode("chat_rate") == "auto"
    pool.note_channel_flood(900, "x", MAIN)
    pool.note_helper_flood(pool.helpers[0], MAIN, 900, long=True)
    assert pool.tune("chat_rate") == 0
    pool.set_tune("chat_rate", 45)
    assert pool.tune("chat_rate") == 45


def test_with_the_rate_off_a_burst_is_not_spaced_out(pool):
    token = UPLOAD_CTX.set((object(), 0))
    try:
        waits = [asyncio.run(pool._reserve(MAIN))[1] for _ in range(5)]
    finally:
        UPLOAD_CTX.reset(token)
    assert waits == [0.0] * 5                                       # 5 köməkçi — hamısı dərhal (hər birinin öz tempi var)
