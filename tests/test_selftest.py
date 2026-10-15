import asyncio
from types import SimpleNamespace as NS

from bot.core import selftest
from bot.menus.views.maintenance import tests_view
from bot.plugins import maintenance_plugin as mp


def test_failures_are_reported_not_raised(monkeypatch):
    async def boom(ctx):
        raise RuntimeError("HTTP Error 403: Forbidden")

    async def ok(ctx):
        return True, "yaxşı"

    monkeypatch.setitem(selftest._FN, "download", boom)
    monkeypatch.setitem(selftest._FN, "ffmpeg", ok)
    res = asyncio.run(selftest.run(NS(), ["ffmpeg", "download"]))
    assert [r["ok"] for r in res] == [True, False]
    assert "403" in res[1]["detail"]
    text = selftest.render(res)
    assert "❌" in text and "1 problem" in text


def test_timeout_is_a_failure(monkeypatch):
    async def slow(ctx):
        raise asyncio.TimeoutError()

    monkeypatch.setitem(selftest._FN, "extract", slow)
    r = asyncio.run(selftest.run_one(NS(), "extract"))
    assert not r["ok"] and r["detail"] == "vaxt bitdi"


def test_progress_called_before_each_test(monkeypatch):
    async def ok(ctx):
        return True, "x"

    for k in ("db", "ffmpeg"):
        monkeypatch.setitem(selftest._FN, k, ok)
    seen = []
    asyncio.run(selftest.run(NS(), ["db", "ffmpeg"], lambda done, cur: seen.append((len(done), cur))))
    assert seen == [(0, "db"), (1, "ffmpeg")]


def test_gate_test_requires_report_button_allowed_in_maintenance():
    assert any("rp:go".startswith(p) for p in mp.ALLOWED_CB_PREFIXES)
    r = asyncio.run(selftest.run_one(NS(), "gate"))
    assert r["ok"]


def test_tests_view_has_all_buttons():
    _, kb = tests_view()
    data = [b.callback_data for r in kb.inline_keyboard for b in r]
    assert "menu:maint:tr:all" in data and "menu:maint:tr:sim" in data
    for k in selftest.ORDER:
        assert f"menu:maint:tr:{k}" in data
