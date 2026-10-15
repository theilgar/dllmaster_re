from bot.core import panel


def test_paginate_clamps():
    items = list(range(25))
    chunk, page, pages = panel.paginate(items, 99, 10)
    assert (chunk, page, pages) == ([20, 21, 22, 23, 24], 2, 3)
    assert panel.paginate([], 5, 10) == ([], 0, 1)
    assert panel.paginate(items, -3, 10)[1] == 0


def test_page_row():
    cb = lambda p: f"x:{p}"
    assert panel.page_row(0, 1, cb) is None
    r = panel.page_row(0, 3, cb)
    assert [b.callback_data for b in r] == ["x:0", "x:0", "x:1"]
    r = panel.page_row(0, 3, cb, wrap=True, center_cb="noop")
    assert [b.callback_data for b in r] == ["x:2", "noop", "x:1"]
    assert r[1].text == "1/3"


def test_btn_url():
    b = panel.btn("a", url="https://x.y")
    assert b.url == "https://x.y" and b.callback_data is None
    assert panel.btn("a", "d").callback_data == "d"
