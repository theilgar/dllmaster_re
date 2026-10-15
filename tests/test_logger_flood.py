import asyncio

from telethon.errors import FloodWaitError

from userbot.plugins import logger as lg


class FakeClient:
    def __init__(self, fail_times):
        self.fail, self.sent = fail_times, []

    async def get_entity(self, x):
        return x

    async def send_message(self, entity, text, **kw):
        if self.fail > 0:
            self.fail -= 1
            raise FloodWaitError(request=None, capture=3)
        self.sent.append((entity, text, kw))
        return "ok"


def _setup(monkeypatch):
    monkeypatch.setattr(lg, "stream_on", lambda *a, **k: True)
    monkeypatch.setattr(lg, "get_stream_target", lambda *a, **k: (-100123, None, None))
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(lg.asyncio, "sleep", fake_sleep)
    lg._FLOOD_UNTIL.clear()
    lg._SEND_LOCKS.clear()
    return slept


def test_flood_waits_then_retries_same_target(monkeypatch):
    slept = _setup(monkeypatch)
    c = FakeClient(fail_times=1)
    assert asyncio.run(lg.send_to_target(None, c, text="x")) == "ok"
    assert len(c.sent) == 1 and c.sent[0][0] == -100123          # Saved Messages-ə yönləndirilmədi
    assert slept and slept[0] > 0


def test_second_flood_drops_message(monkeypatch):
    _setup(monkeypatch)
    c = FakeClient(fail_times=5)
    assert asyncio.run(lg.send_to_target(None, c, text="x")) is None
    assert c.sent == []                                          # "me"-ə atılmadı


def test_huge_flood_is_skipped_without_waiting(monkeypatch):
    slept = _setup(monkeypatch)
    import time
    lg._FLOOD_UNTIL["-100123"] = time.time() + lg.FLOOD_MAX_WAIT + 500
    c = FakeClient(fail_times=0)
    assert asyncio.run(lg.send_to_target(None, c, text="x")) is None
    assert not slept and not c.sent
