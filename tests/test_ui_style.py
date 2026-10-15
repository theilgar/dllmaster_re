"""📋 Görünüş ayarı: zolaq və mətn üslubu bütün yerlərə tətbiq olunur."""
import pytest

import common.menu as menu


@pytest.mark.parametrize("bar,expected", [("vert", "|||||     "), ("block", "▰▰▰▰▰▱▱▱▱▱"), ("solid", "■■■■■.....")])
def test_bar_follows_setting(monkeypatch, bar, expected):
    monkeypatch.setattr(menu, "_UI_BAR_OVERRIDE", bar)
    assert menu._bar(0.5, 10) == expected
    assert menu._bar_box(0.5, 10) == f"[{expected}]"


def test_progress_bar_and_chart_follow_setting(monkeypatch):
    monkeypatch.setattr(menu, "_UI_BAR_OVERRIDE", "block")
    from bot.menus.views.base import bar_chart
    from bot.voice.models import progress_bar

    assert progress_bar(30, 60) == "▰▰▰▰▰▱▱▱▱▱"
    assert "▰" in bar_chart(["a"], [5], 4) and "█" not in bar_chart(["a"], [5], 4)


@pytest.mark.parametrize("style,tag", [("box", "<pre>"), ("script", "<blockquote expandable>"), ("quote", "<blockquote>")])
def test_text_style_wrapper(monkeypatch, style, tag):
    monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", style)
    assert menu.wrap_ui_text("x").startswith(tag)
