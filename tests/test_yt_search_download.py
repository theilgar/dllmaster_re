import asyncio

import pytest

from bot.core import youtube_handler as yh, ytdl_worker


def test_file_too_large_marker_shared():
    assert yh.FILE_TOO_LARGE == ytdl_worker.FILE_TOO_LARGE
    assert yh.FILE_TOO_LARGE in yh.VIDEO_ERRORS


def test_ytm_relevant():
    res = [{"title": "Rəsul Gunəşli - Sevgi"}, {"title": "Başqa mahnı"}]
    assert yh.ytm_relevant("Resul Gunesli Sevgi", res)
    assert not yh.ytm_relevant("completely different words here", res)


def _run_search(monkeypatch, mode, ytm_items, explicit):
    m = yh.YoutubeManager()
    calls = {"yt": 0}

    async def ytm(q, limit, fast=False):
        return ytm_items

    async def yt(q, limit, fast=False):
        calls["yt"] += 1
        return [{"title": "yt", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "source": "yt"}]

    monkeypatch.setattr(m, "_ytm_search", ytm)
    monkeypatch.setattr(m, "_yt_search", yt)
    monkeypatch.setattr(yh, "search_mode", lambda: mode)
    monkeypatch.setattr(yh, "search_source", lambda: "ytm")
    res = asyncio.run(m.youtube_search("abc def ghi", 5, source="ytm" if explicit else None))
    return res, calls["yt"]


ITEMS = [{"title": "zzz qqq", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "source": "ytm"}]


def test_auto_irrelevant_falls_back_to_youtube(monkeypatch):
    res, yt_calls = _run_search(monkeypatch, "auto", ITEMS, explicit=False)
    assert yt_calls == 1 and res[0]["source"] == "yt"


def test_manual_or_explicit_keeps_ytm(monkeypatch):
    assert _run_search(monkeypatch, "manual", ITEMS, explicit=False)[1] == 0
    assert _run_search(monkeypatch, "auto", ITEMS, explicit=True)[1] == 0


def test_too_large_stops_after_first_attempt(monkeypatch, tmp_path):
    calls = []

    async def fake_dl(url, opts, cancel_event=None, **kw):
        calls.append(1)
        raise RuntimeError(f"{yh.FILE_TOO_LARGE} (200 MB)")

    async def nowait(*a, **k):
        return None

    monkeypatch.setattr(yh.cpu_pool, "ytdl_download", fake_dl)
    monkeypatch.setattr(yh, "_wait_cooldown", nowait)
    monkeypatch.setattr(yh, "get_download_dir", lambda: str(tmp_path))
    with pytest.raises(RuntimeError, match="limitindən"):
        asyncio.run(yh.download_as_m4a("https://www.youtube.com/watch?v=aaaaaaaaaaa", "x", "firefox"))
    assert len(calls) == 1


def test_rank_setting_defaults_and_fmt():
    from bot.core import inline_settings as IS
    assert IS.SPEC["rank"][1] == 1 and IS.SPEC["chat_rank"][1] == 1
    assert "rank" in IS.INLINE_ORDER and "chat_rank" in IS.CHAT_ORDER
    assert IS.fmt("rank").startswith(("🤖", "📦"))
