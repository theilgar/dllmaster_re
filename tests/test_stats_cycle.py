"""Statistika: tab sırası əvəzinə tək dövr düyməsi."""
import time

import pytest

from bot.menus.views import stats as S


class FakeDB:
    def stats_overview(self, day_start, now, tz_offset):
        return {
            "downloads": {"today": 1, "week": 2, "prev_week": 1, "month": 3, "total": 4},
            "dl_users": {"today": 1, "week": 1, "month": 1},
            "users": {"total": 10, "blocked": 1, "new_today": 1, "new_week": 2, "active_today": 3, "active_week": 4},
            "chats": {"groups": 1, "channels": 0}, "premium_count": 1, "group_downloads": 1,
            "sources": {"ytm": 2}, "daily_dl": [1, 2, 3], "daily_new": [1, 0, 1],
            "chart_start": int(time.time()) - 3 * 86400, "hours": [1] * 24,
            "top_songs": [{"title": "x", "n": 3, "users": 2}],
            "top_users": [{"user_id": 1, "n": 3, "first_name": "A", "username": "a"}],
        }


@pytest.mark.parametrize("view,nxt", [("stats", "stats_chart"), ("stats_chart", "stats_top"), ("stats_top", "stats")])
async def test_single_button_cycles_through_views(view, nxt):
    text, kb = await S.stats_view(view, FakeDB())
    first = kb.inline_keyboard[0]
    assert len(first) == 1 and first[0].callback_data == f"menu:{nxt}"
    assert kb.inline_keyboard[1][0].callback_data == f"menu:{view}"        # 🔄 cari ekranı yeniləyir


async def test_each_view_has_its_own_content():
    t1, _ = await S.stats_view("stats", FakeDB())
    t2, _ = await S.stats_view("stats_chart", FakeDB())
    t3, _ = await S.stats_view("stats_top", FakeDB())
    assert "Yükləmələr" in t1 and "Qrafik" in t2 and "Top" in t3
