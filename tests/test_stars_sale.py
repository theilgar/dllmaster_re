import time
from datetime import datetime, timezone, timedelta

import pytest

from bot.core import stars

TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 12, 20, 10, 0, tzinfo=TZ).timestamp()


@pytest.fixture
def store(monkeypatch):
    data = {}

    class DB:
        def get_setting(self, k, d=None): return data.get(k, d)
        def set_setting(self, k, v):
            data.pop(k, None) if v is None else data.__setitem__(k, v)

    monkeypatch.setattr(stars, "get_db", lambda: DB())
    return data


def test_parse_times():
    p = lambda t: stars.parse_sale_time(t, NOW, TZ)
    assert p("25.12.2026 18:00") == int(datetime(2026, 12, 25, 18, 0, tzinfo=TZ).timestamp())
    assert p("25.12 18:00") == p("25.12.2026 18:00")
    assert p("18:00") == int(datetime(2026, 12, 20, 18, 0, tzinfo=TZ).timestamp())
    assert p("09:00") == int(datetime(2026, 12, 21, 9, 0, tzinfo=TZ).timestamp())   # keçib → sabah
    assert p("+3g") == int(NOW + 3 * 86400) and p("+12s") == int(NOW + 12 * 3600)
    assert p("-") is None
    with pytest.raises(ValueError):
        p("salam")
    with pytest.raises(ValueError):
        p("31.02.2026 10:00")


def test_sale_window_and_prices(store):
    stars.set_price("30", 100)
    stars.set_price("life", 1)
    assert stars.effective_price("30") == 100 and not stars.sale_active()
    stars.set_sale("on", 1)
    stars.set_sale("pct", 30)
    stars.set_sale("start", int(NOW))
    stars.set_sale("end", int(NOW) + 3600)
    assert not stars.sale_active(NOW - 1)
    assert stars.sale_active(NOW + 10) and not stars.sale_active(NOW + 3600)
    store["stars_sale:start"] = "0"
    store["stars_sale:end"] = str(int(time.time()) + 60)
    assert stars.effective_price("30") == 70
    assert stars.effective_price("life") == 1                      # minimum 1⭐
    offers = {k: (p, b) for k, _, _, p, b in stars.plan_offers()}
    assert offers["30"] == (70, 100)
    kb = stars.buy_keyboard()
    assert "əvvəl 100" in kb.inline_keyboard[0][0].text and "70" in kb.inline_keyboard[0][0].text
    store["stars_sale:on"] = "0"
    assert stars.effective_price("30") == 100 and "əvvəl" not in stars.buy_keyboard().inline_keyboard[0][0].text
