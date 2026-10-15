"""Səsli asistan: sessiya faylı yazısı birləşdirilir, atomikdir və növbəni məhdudlaşdırır."""
import asyncio
import json
import types

import pytest

import bot.voice.engine as engine
from bot.voice.engine import Assistant


def _track(name):
    return types.SimpleNamespace(title=name, vid=name, url="u", duration=1, file_id=None, by="x", user_id=1,
                                 track_id=2)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(engine, "SESSIONS_FILE", str(tmp_path / "data" / "voice_sessions.json"))
    state = types.SimpleNamespace(current=_track("c"), queue=[_track(f"q{i}") for i in range(900)], title="T")
    obj = types.SimpleNamespace(chats={-1: state}, SESSIONS_QUEUE_CAP=500, _sess_pending=False)
    obj._snapshot_sessions = lambda: Assistant._snapshot_sessions(obj)
    obj._write_sessions = Assistant._write_sessions
    return obj


def test_snapshot_caps_queue(fake):
    data = Assistant._snapshot_sessions(fake)
    assert len(data["-1"]["queue"]) == 500 and data["-1"]["current"]["title"] == "c"


def test_now_writes_synchronously_and_atomically(fake, tmp_path):
    Assistant.save_active_sessions(fake, now=True)
    path = tmp_path / "data" / "voice_sessions.json"
    assert json.loads(path.read_text(encoding="utf-8"))["-1"]["title"] == "T"
    assert not (tmp_path / "data" / "voice_sessions.json.tmp").exists()


async def test_many_calls_are_coalesced(fake, tmp_path):
    for _ in range(50):
        Assistant.save_active_sessions(fake)
    assert fake._sess_pending is True
    await asyncio.sleep(1.4)
    assert fake._sess_pending is False
    assert (tmp_path / "data" / "voice_sessions.json").exists()


async def test_now_cancels_pending_write(fake, tmp_path):
    Assistant.save_active_sessions(fake)
    Assistant.save_active_sessions(fake, now=True)
    assert fake._sess_pending is False
