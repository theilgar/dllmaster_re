"""bot/core/depo_tuning.py — köməkçi ayarları: 🤖 Avto (flood-a görə uyğunlaşır) və ✋ Manual."""
import pytest

from bot.core import depo_helpers, depo_tuning as T


@pytest.fixture
def pool(monkeypatch):
    store, clock = {}, [1000.0]
    monkeypatch.setattr(depo_helpers.HelperPool, "_get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(depo_helpers.HelperPool, "_set", staticmethod(lambda k, v: store.__setitem__(k, str(v))))
    monkeypatch.setattr(depo_helpers.HelperPool, "_init_table", lambda self: None)
    monkeypatch.setattr(T.time, "monotonic", lambda: clock[0])
    for var in ("DEPO_SINK_INTERVAL", "DEPO_SINK_MAX_WAIT", "DEPO_WARM_INTERVAL"):
        monkeypatch.delenv(var, raising=False)
    p = depo_helpers.HelperPool(object())
    p.store, p.clock = store, clock
    return p


def test_defaults_are_auto_with_standard_values(pool):
    assert pool.rate == 18 and pool.tune("group_interval") == 3.2
    assert all(pool.tune_mode(k) == "auto" for k in T.TUNABLES)


def test_old_stored_rate_keeps_working_as_manual(pool):
    pool.store["depo_helpers:rate"] = "12"                    # menyudan əvvəl yazılmış dəyər
    assert pool.tune_mode("rate") == "manual" and pool.rate == 12
    pool.store["depo_helpers:rate"] = "18"                    # standartla eyni — avto
    assert pool.tune_mode("rate") == "auto"


def test_manual_value_is_clipped_and_sticks(pool):
    assert pool.set_rate(500) == 60 and pool.set_rate(0) == 1
    assert pool.set_tune("group_interval", 3.456) == 3.5
    assert pool.tune_mode("group_interval") == "manual"
    pool.note_flood("group_interval")
    assert pool.tune("group_interval") == 3.5                 # manual flood-a baxmır


def test_step_tune_has_no_float_noise(pool):
    pool.set_tune("group_interval", 3.2)
    assert pool.step_tune("group_interval", 0.2) == 3.4
    assert pool.step_tune("sink_interval", -0.1) == 0.9


def test_auto_rate_drops_on_flood_and_recovers_when_calm(pool):
    pool.note_flood("rate")
    assert pool.rate == 14 and pool.store["depo_helpers:auto:rate"] == "14"
    pool.clock[0] += T.RECOVER_AFTER * 2 + 1
    assert pool.rate == 16
    pool.clock[0] += T.RECOVER_AFTER * 10
    assert pool.rate == 18


def test_many_helpers_flooding_at_once_is_one_step(pool):
    for _ in range(24):
        pool.note_flood("rate")
    assert pool.rate == 14


def test_flood_never_goes_past_the_limit(pool):
    for _ in range(12):
        pool.clock[0] += T.FLOOD_DEBOUNCE + 1
        pool.note_flood("rate")
        pool.note_flood("group_interval")
    assert pool.rate == 6 and pool.tune("group_interval") == 8.0


def test_interval_grows_on_flood_and_returns_to_base(pool):
    pool.note_flood("sink_interval")
    assert pool.sink_interval() == 1.5
    pool.clock[0] += T.RECOVER_AFTER * 10
    assert pool.sink_interval() == 1.0


def test_learned_value_survives_restart(pool):
    pool.note_flood("rate")
    again = depo_helpers.HelperPool(object())
    assert again.rate == 14


def test_derived_settings_follow_sink_interval(pool):
    assert pool.tune("sink_max_wait") == 20 and pool.tune("warm_interval") == 3
    pool.set_tune("sink_interval", 4)
    assert pool.tune("sink_max_wait") == 60 and pool.tune("warm_interval") == 12
    assert pool.tune_mode("sink_max_wait") == "auto"


def test_warm_zero_means_off_and_env_is_the_auto_base(pool, monkeypatch):
    pool.set_tune("warm_interval", 0)
    assert pool.tune("warm_interval") == 0
    monkeypatch.setenv("DEPO_SINK_INTERVAL", "2.5")
    pool.set_tune_mode("sink_interval", "auto")
    assert pool.sink_interval() == 2.5


def test_switch_to_manual_starts_from_current_value(pool):
    pool.note_flood("rate")                                   # avto 14-dədir
    pool.set_tune_mode("rate", "manual")
    assert pool.rate == 14 and pool.tune_mode("rate") == "manual"
    pool.set_tune_mode("rate", "auto")
    assert pool.tune_mode("rate") == "auto"


def test_reset_and_all_auto(pool):
    pool.note_flood("rate")
    pool.reset_tune("rate")
    assert pool.rate == 18
    pool.set_rate(9)
    pool.all_auto()
    assert pool.tune_mode("rate") == "auto" and pool.rate == 18


def test_turbo_raises_auto_bases_and_balanced_restores_them(pool):
    assert not pool.turbo
    pool.set_profile("turbo")
    assert pool.turbo and pool.rate == 20 and pool.tune("group_interval") == 3.0
    assert pool.sink_interval() == 0.7 and pool.tune("poll_every") == 90
    assert pool.tune("sink_max_wait") == 10 and pool.tune("warm_interval") == 2.1      # sink aralığından hesablanır
    pool.set_profile("balanced")
    assert pool.rate == 18 and pool.tune("group_interval") == 3.2 and pool.tune("sink_max_wait") == 20


def test_turbo_leaves_manual_settings_and_env_alone(pool, monkeypatch):
    pool.set_tune("group_interval", 5)
    monkeypatch.setenv("DEPO_SINK_INTERVAL", "2.0")
    pool.set_profile("turbo")
    assert pool.tune("group_interval") == 5 and pool.manual_keys() == ["group_interval"]
    assert pool.sink_interval() == 2.0 and pool.rate == 20


def test_turbo_still_backs_off_on_flood_and_returns_to_turbo_base(pool):
    pool.set_profile("turbo")
    pool.note_flood("rate")
    assert pool.rate == 15
    pool.clock[0] += T.RECOVER_AFTER * 20
    assert pool.rate == 20                                    # balanslı 18-ə yox, turbo bazasına qayıdır


def test_profile_change_drops_learned_values_and_survives_restart(pool):
    pool.note_flood("rate")
    pool.set_profile("turbo")
    assert pool.rate == 20
    assert depo_helpers.HelperPool(object()).turbo
    pool.toggle_turbo()
    assert not pool.turbo and pool.rate == 18


def test_turbo_values_stay_inside_auto_bounds():
    for t in T.TUNABLES.values():
        if t.turbo is None:
            continue
        assert t.lo <= t.turbo <= t.hi
        if t.safe < 0:
            assert t.limit <= t.turbo
        elif t.safe > 0:
            assert t.turbo <= t.limit


def test_turbo_keeps_rate_above_its_own_floor(pool):
    pool.set_profile("turbo")
    for _ in range(10):
        pool.clock[0] += T.FLOOD_DEBOUNCE + 1
        pool.note_flood("rate")
    assert pool.rate == 12                                    # balanslıda 6-ya düşürdü
    pool.set_profile("balanced")
    for _ in range(10):
        pool.clock[0] += T.FLOOD_DEBOUNCE + 1
        pool.note_flood("rate")
    assert pool.rate == 6


def test_a_rate_learned_as_6_is_lifted_to_the_turbo_floor_after_restart(pool):
    pool.store["depo_helpers:profile"] = "turbo"
    pool.store["depo_helpers:auto:rate"] = "6"
    assert depo_helpers.HelperPool(object()).rate == 12


def test_long_flood_threshold_is_a_plain_auto_manual_setting(pool):
    assert pool.tune("long_flood") == 60 and pool.tune_mode("long_flood") == "auto"
    pool.note_flood("long_flood")                             # uyğunlaşmır
    pool.set_profile("turbo")
    assert pool.tune("long_flood") == 60 and "depo_helpers:auto:long_flood" not in pool.store
    assert pool.set_tune("long_flood", 120) == 120 and pool.tune("long_flood") == 120


def test_chat_rate_is_off_by_default_manual_only_and_not_adaptive(pool):
    assert pool.tune("chat_rate") == 0
    pool.set_profile("turbo")
    assert pool.tune("chat_rate") == 0
    pool.note_flood("chat_rate")                               # uyğunlaşmır
    assert pool.tune("chat_rate") == 0 and "depo_helpers:auto:chat_rate" not in pool.store
    assert pool.set_tune("chat_rate", 80) == 80 and pool.tune("chat_rate") == 80
    pool.set_tune_mode("chat_rate", "auto")
    assert pool.tune("chat_rate") == 0
