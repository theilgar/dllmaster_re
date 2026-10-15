import asyncio
from types import SimpleNamespace

from bot.core import latency


def setup_function(_):
    latency._stats.clear()


def test_key_of():
    assert latency.key_of(SimpleNamespace(data="menu:yt:src:both")) == "🖱 menu:yt"
    assert latency.key_of(SimpleNamespace(text="/music@bot abc")) == "💬 /music"
    assert latency.key_of(SimpleNamespace(text="salam")) == "💬 text"
    assert latency.key_of(SimpleNamespace()) is None


def test_middleware_records_and_snapshot():
    async def handler(event, data):
        await asyncio.sleep(0.01)
        return "ok"

    mw = latency.LatencyMiddleware()
    ev = SimpleNamespace(data="menu:perf:x")
    assert asyncio.run(mw(handler, ev, {})) == "ok"
    s = latency.snapshot()
    assert s["count"] == 1 and s["slow"][0][0] == "🖱 menu:perf"
    assert "orta" in latency.summary_lines()[0]


def test_long_jobs_excluded_from_summary():
    latency.record("🖱 choice_3", 40.0)
    assert latency.snapshot()["count"] == 0


def test_exception_still_recorded():
    async def bad(event, data):
        raise ValueError

    try:
        asyncio.run(latency.LatencyMiddleware()(bad, SimpleNamespace(data="a:b"), {}))
    except ValueError:
        pass
    assert latency.snapshot()["count"] == 1
