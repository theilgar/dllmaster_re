"""cpu_pool: 🤖 Avto thread / nice / rejim."""
import pytest

from bot.core import cpu_pool as cp


@pytest.fixture
def store(monkeypatch):
    data = {}
    monkeypatch.setattr(cp, "_get", lambda k, d=None: data.get(k) if data.get(k) not in (None, "") else d)
    monkeypatch.setattr(cp, "_set", lambda k, v: data.__setitem__(k, str(v)))
    monkeypatch.setattr(cp, "_int", lambda k, d, lo, hi: int(data.get(k) or d))
    monkeypatch.setattr(cp, "turbo_on", lambda: False)
    monkeypatch.setattr(cp, "apply_threads", lambda force=False: None)
    monkeypatch.setattr(cp.MON, "event", lambda t: None)
    cp._AUTO.update(threads=None, nice=None, idle=0, calm=0)
    yield data
    cp._AUTO.update(threads=None, nice=None, idle=0, calm=0)


def _wd(monkeypatch, lag):
    monkeypatch.setattr(cp.WATCHDOG, "snapshot", lambda: {"lag_avg": lag, "now_frozen": False})


def _pool(monkeypatch, queued=0, active=0):
    monkeypatch.setattr(cp, "thread_pool_stats", lambda: {"queued": queued, "active": active})


def test_manual_by_default(store):
    assert not cp.threads_auto() and not cp.nice_auto()
    store[cp.S_THREADS], store[cp.S_NICE] = "100", "7"
    assert cp.threads() == 100 and cp.nice() == 7


def test_threads_grow_on_queue_and_shrink_on_lag(store, monkeypatch):
    cp.set_threads_auto(True)
    base = cp.base_threads()
    assert cp.threads() == base
    _wd(monkeypatch, 0.0)
    _pool(monkeypatch, queued=20, active=base)
    cp._auto_tune()
    grown = cp.threads()
    assert grown > base or base >= min(cp.THREAD_STEPS[-1], max(128, cp.CORES * 16))
    _wd(monkeypatch, 1.0)                                     # bot ləngiyir → kiçilir
    cp._auto_tune()
    assert cp.threads() <= grown and cp.threads() >= base


def test_threads_shrink_after_idle(store, monkeypatch):
    cp.set_threads_auto(True)
    cp._AUTO["threads"] = cp.base_threads() + 16
    _wd(monkeypatch, 0.0)
    _pool(monkeypatch, queued=0, active=0)
    for _ in range(20):
        cp._auto_tune()
    assert cp.threads() == cp.base_threads() + 8


def test_nice_rises_on_lag_and_returns(store, monkeypatch):
    cp.set_nice_auto(True)
    assert cp.nice() == cp.AUTO_NICE_BASE
    monkeypatch.setattr(cp, "_renice_workers", lambda n: None)
    _wd(monkeypatch, 1.0)
    cp._auto_tune()
    cp._auto_tune()
    assert cp.nice() == cp.AUTO_NICE_BASE + 4
    _wd(monkeypatch, 0.0)
    for _ in range(10):
        cp._auto_tune()
    assert cp.nice() == cp.AUTO_NICE_BASE + 3


def test_nice_capped(store, monkeypatch):
    cp.set_nice_auto(True)
    monkeypatch.setattr(cp, "_renice_workers", lambda n: None)
    _wd(monkeypatch, 5.0)
    for _ in range(20):
        cp._auto_tune()
    assert cp.nice() == cp.AUTO_NICE_MAX


def test_turbo_overrides_auto(store, monkeypatch):
    cp.set_threads_auto(True)
    monkeypatch.setattr(cp, "turbo_on", lambda: True)
    store[cp.S_THREADS] = "256"
    assert not cp.threads_auto() and cp.threads() == 256


def test_default_mode_depends_on_ram(monkeypatch):
    monkeypatch.setattr(cp, "CORES", 4)
    monkeypatch.setattr(cp, "_RAM_TOTAL_MB", [512])
    assert cp.default_mode() == "thread"
    monkeypatch.setattr(cp, "_RAM_TOTAL_MB", [4096])
    assert cp.default_mode() == "process"
    monkeypatch.setattr(cp, "CORES", 1)
    assert cp.default_mode() == "thread"


def test_perf_buttons_use_handled_callbacks():
    from bot.menus.views.performance import perf_hub_view
    _, kb = perf_hub_view()
    data = [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]
    assert not any(d.endswith(":tog") for d in data)
    for pat in ("menu:perf:asm:", "menu:perf:tauto:", "menu:perf:nauto:", "menu:perf:mode:"):
        assert any(d.startswith(pat) for d in data), pat
