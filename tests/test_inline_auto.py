import pytest

from bot.core import inline_settings as IS


@pytest.fixture
def store(monkeypatch):
    data = {}

    class DB:
        def get_setting(self, k, d=None): return data.get(k, d)
        def set_setting(self, k, v):
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v

    monkeypatch.setattr(IS, "get_db", lambda: DB())
    stats = {"inline": {"queries": 0, "cache_hits": 0, "errors": 0, "timeouts": 0, "answered": 0, "total": 0.0},
             "chat": {}, "mix": {}}
    IS.set_stats_provider(lambda: stats)
    yield data, stats
    IS.set_stats_provider(None)


def test_manual_by_default_and_auto_uses_defaults_without_samples(store):
    data, _ = store
    assert not IS.is_auto("limit")
    IS.set_auto("limit", True)
    assert IS.get("limit") == IS.SPEC["limit"][1] and IS.fmt("limit").endswith("🤖")


def test_auto_reacts_to_slow_and_fast(store):
    data, stats = store
    IS.set_all_auto(True)
    stats["inline"].update(queries=20, answered=20, total=20 * 4.0, cache_hits=0)   # yavaş
    assert IS.get("limit") == 8 and IS.get("debounce") == 0.5
    stats["inline"].update(total=20 * 0.5)                                          # sürətli
    assert IS.get("limit") == 15 and IS.get("debounce") == 0.2
    stats["inline"].update(cache_hits=15)
    assert IS.get("ttl") == 60
    stats["inline"].update(timeouts=10)                                             # tez-tez vaxt aşımı
    assert IS.get("timeout") == 20 and IS.get("ytwait") == 4


def test_manual_change_switches_off_auto(store):
    IS.set_auto("limit", True)
    IS.put("limit", 30)
    assert not IS.is_auto("limit") and IS.get("limit") == 30


def test_toggle_and_reset(store):
    IS.set_auto("rank", True)
    assert IS.get("rank") == 1
    IS.toggle("rank")
    assert not IS.is_auto("rank") and IS.get("rank") == 0
    IS.set_all_auto(True)
    IS.reset()
    assert not any(IS.is_auto(n) for n in IS.SPEC)


def test_auto_values_are_valid_steps(store):
    _, stats = store
    stats["inline"].update(queries=50, answered=50, total=300.0, cache_hits=30, errors=15)
    stats["chat"].update(queries=50, answered=50, total=300.0)
    stats["mix"].update(queries=50, answered=50, total=300.0, cache_hits=30)
    for name in IS.SPEC:
        IS.set_auto(name, True)
        assert IS.get(name) in IS.SPEC[name][2], name
