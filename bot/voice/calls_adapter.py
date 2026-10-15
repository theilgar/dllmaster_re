"""
📞 bot/voice/calls_adapter.py - py-tgcalls 1.x və 2.x üçün vahid abstraksiya adapteri.
"""
from __future__ import annotations

import contextlib
import logging
from collections import deque

from bot.voice.models import REC, T_OP, _deadline, _spawn

logger = logging.getLogger(__name__)


class Calls:
    """py-tgcalls adapteri (1.x və 2.x)."""

    def __init__(self, client, on_end, on_closed):
        try:
            from pytgcalls.mtproto.telethon_client import TelethonClient
            if not getattr(TelethonClient, "_patched_entity_err", False):
                _orig_on_update = TelethonClient.on_update
                async def _safe_on_update(tc_self, update):
                    try:
                        return await _orig_on_update(tc_self, update)
                    except (KeyError, ValueError):
                        pass
                TelethonClient.on_update = _safe_on_update
                TelethonClient._patched_entity_err = True
        except Exception as _e:
            logger.debug("udulan xəta [bot/voice/calls_adapter.py:30]: %r", _e)

        from pytgcalls import PyTgCalls
        self.app = PyTgCalls(client)
        self.v2 = hasattr(self.app, "play")
        self._on_end = lambda chat_id: _spawn(on_end(chat_id))
        self._on_closed = lambda chat_id, status: _spawn(on_closed(chat_id, status))
        self._register()

    def _register(self):
        if self.v2:
            @self.app.on_update()
            async def _upd(_, update):
                name = type(update).__name__
                chat_id = getattr(update, "chat_id", None)
                if chat_id is None:
                    return
                if name == "StreamFrames":
                    if REC:
                        REC.on_frames(chat_id, update)
                    return
                if name == "StreamEnded":
                    st = str(getattr(update, "stream_type", "audio")).lower()
                    if REC and REC.recent_toggle(chat_id):
                        return
                    if "video" not in st:
                        await self._on_end(chat_id)
                elif name == "ChatUpdate":
                    status = str(getattr(update, "status", "")).upper()
                    if any(x in status for x in ("KICKED", "LEFT", "CLOSED", "DISCARDED")):
                        await self._on_closed(chat_id, status)
        else:
            @self.app.on_stream_end()
            async def _end(_, update):
                await self._on_end(update.chat_id)

            for ev in ("on_kicked", "on_closed_voice_chat", "on_left"):
                deco = getattr(self.app, ev, None)
                if deco:
                    def make(evname):
                        async def _closed(_, chat_id):
                            await self._on_closed(chat_id, evname)
                        return _closed
                    deco()(make(ev))

    async def start(self):
        await self.app.start()

    async def play(self, chat_id: int, path: str):
        if self.v2:
            await self.app.play(chat_id, self._stream(path))
            return
        try:
            from pytgcalls.types import AudioPiped
        except ImportError:
            from pytgcalls.types.input_stream import AudioPiped
        try:
            await self.app.change_stream(chat_id, AudioPiped(path))
        except Exception:
            await self.app.join_group_call(chat_id, AudioPiped(path))

    @staticmethod
    def _stream(path: str):
        from pytgcalls.types import MediaStream
        kw = {}
        try:
            from pytgcalls.types import AudioQuality
            kw["audio_parameters"] = AudioQuality.HIGH
        except ImportError:
            pass
        flags = getattr(getattr(MediaStream, "Flags", None), "IGNORE", None) or getattr(MediaStream, "IGNORE", None)
        if flags is not None:
            kw["video_flags"] = flags
        for attempt in (kw, {k: v for k, v in kw.items() if k != "video_flags"}, {}):
            try:
                return MediaStream(path, **attempt)
            except TypeError:
                continue
        return MediaStream(path)

    async def volume(self, chat_id, value: int):
        fn = getattr(self.app, "change_volume_call", None)
        if fn is not None:
            with contextlib.suppress(Exception):
                await _deadline(fn(chat_id, int(value)), T_OP)

    async def pause(self, chat_id):
        await _deadline(self.app.pause(chat_id) if self.v2 else self.app.pause_stream(chat_id), T_OP)

    async def resume(self, chat_id):
        await _deadline(self.app.resume(chat_id) if self.v2 else self.app.resume_stream(chat_id), T_OP)

    async def leave(self, chat_id):
        with contextlib.suppress(Exception):
            await _deadline(self.app.leave_call(chat_id) if self.v2 else self.app.leave_group_call(chat_id), T_OP)


def pytgcalls_version() -> str:
    try:
        import pytgcalls
        v = getattr(pytgcalls, "__version__", "?")
    except Exception:
        return ""
    try:
        from importlib import metadata
        v += f" · ntgcalls {metadata.version('ntgcalls')}"
    except Exception as _e:
        logger.debug("pytgcalls versiyası oxunmadı: %r", _e)
    return v


class _LibLog(logging.Handler):
    NAMES = ("pytgcalls", "ntgcalls")

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = deque(maxlen=25)
        self._old = {}

    def emit(self, record):
        with contextlib.suppress(Exception):
            self.lines.append(f"{record.name} {record.levelname}: {record.getMessage()[:200]}")

    def __enter__(self):
        for n in self.NAMES:
            lg = logging.getLogger(n)
            self._old[n] = lg.level
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self)
        return self

    def __exit__(self, *exc):
        for n in self.NAMES:
            lg = logging.getLogger(n)
            lg.removeHandler(self)
            lg.setLevel(self._old.get(n, logging.NOTSET))
        return False
