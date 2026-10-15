"""
📦 bot/voice/models.py - Səs asistanı məlumat modelləri, sabitlər və parametrlər.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from collections import deque

from common.database import Settings
from common.utilities import progress_bar as _pbar
from userbot.core import tg_login

logger = logging.getLogger(__name__)


def _get_play_dir() -> str:
    from bot.core.youtube_handler import get_download_dir
    p = os.path.join(get_download_dir(), "voice_play")
    try:
        os.makedirs(p, exist_ok=True)
    except Exception as _e:
        logger.debug("udulan xəta [bot/voice/models.py:26]: %r", _e)
    return p


PLAY_DIR = _get_play_dir()
VOICE_CACHE_DIR = os.path.join("data", "voice_cache")
MAX_CACHE_BYTES = 1500 * 1024 * 1024  # 1.5 GB daimi səs keşi
SESSIONS_FILE = os.path.join("data", "voice_sessions.json")
MAX_DURATION = 30 * 60
IDLE_LEAVE = 90

SOURCES = {"ytm": "🎵 YouTube Music", "yt": "▶️ YouTube", "both": "🔀 Hər ikisi"}
WHO = {"all": "👥 Hamı", "admins": "👮 Yalnız adminlər"}
S_ON, S_SRC, S_DEPO, S_WHO = "va:on", "va:source", "va:depo_first", "va:who"
S_MIXN, S_MIXAUTO = "va:mix_n", "va:mix_auto"
S_QUAL, S_NORM, S_VOL, S_THROTTLE = "va:quality", "va:norm", "va:volume", "va:throttle"
S_DM_ON, S_DM_CD = "va:dm_on", "va:dm_cd"
DM_CD_STEPS = [0, 1, 6, 12, 24]
DM_TEXT = ("👋 Salam! Bu hesab <b>{bot_name}</b> botunun canlı yayım asistanıdır və mesajlara cavab vermir.\n\n"
           "🎵 Mahnı axtarmaq, yükləmək və qrupda canlı yayım üçün aşağıdakı düyməyə bas — bot dərhal açılacaq.")
QUALITY = {"depo": "⚡ Depo (sürətli)", "best": "💎 Ən yaxşı (Opus 251)"}
VOL_STEPS = [50, 75, 100, 125, 150]
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"
MIX_STEPS = [5, 10, 15, 20, 30, 50]
MIX_REFILL_AT = 2

# ⏱ /play mərhələlərinin vaxt limitləri (san.)
T_MEMBER = 25
T_BEST_DEPO = 15
T_BEST = 75
T_FETCH = 240
T_JOIN = 90
DIALOG_SCAN = 3000
T_OP = 12
T_NORM = 60
T_ROUTE = 45
T_YT = 150
S_MAXPER = "va:max_per"
MAXPER_STEPS = [0, 1, 2, 3, 5, 10]
MAX_SLOTS = 5
SLOT_ICONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

SESSION_PATH = os.getenv("ASSISTANT_SESSION_FILE") or os.path.join("data", "assistant.session")

_S = Settings(empty_is_default=True)
_get = _S.get
_set = _S.put

_BG = set()
REC = None


def progress_bar(elapsed: int, total: int, width: int = 10) -> str:
    return _pbar(elapsed, total=total, width=width)             # UI zolaq üslubuna uyğun (/ui → bar)


async def _spawn(coro):
    task = asyncio.ensure_future(coro)
    _BG.add(task)

    def done(t):
        _BG.discard(t)
        if not t.cancelled() and t.exception():
            logger.warning(f"🎙 hadisə işlənmədi: {t.exception()!r}")
    task.add_done_callback(done)


class _Deadline(asyncio.TimeoutError):
    """Voice assistant daxili limit xətası."""


def _flood_seconds(err) -> int:
    if err is None:
        return 0
    if "flood" in type(err).__name__.lower() and getattr(err, "seconds", None):
        return int(err.seconds)
    m = re.search(r"FLOOD_WAIT_(\d+)|wait of (\d+) seconds", str(err), re.I)
    return int(m.group(1) or m.group(2)) if m else 0


async def _deadline(aw, limit: float):
    task = asyncio.ensure_future(aw)
    done, _ = await asyncio.wait({task}, timeout=limit)
    if not done:
        task.cancel()
        task.add_done_callback(lambda f: f.cancelled() or f.exception())
        raise _Deadline()
    return task.result()


class Track:
    __slots__ = ("title", "vid", "url", "duration", "file_id", "by", "path", "prep", "depo", "src", "fmt", "user_id", "track_id")

    def __init__(self, title, vid=None, url=None, duration=0, file_id=None, by="", depo=False, src="", user_id=None, track_id=None):
        self.title, self.vid, self.url, self.duration = title, vid, url, int(duration or 0)
        self.file_id, self.by, self.depo, self.src = file_id, by, depo, src
        self.path, self.prep, self.fmt = None, None, ""
        self.user_id = user_id
        self.track_id = track_id or uuid.uuid4().hex[:8]


class ChatState:
    def __init__(self, chat_id, title=""):
        self.chat_id, self.title = chat_id, title
        self.queue = deque()
        self.current = None
        self.started = 0.0
        self.paused = False
        self.np_msg = None
        self.idle_task = None
        self.lock = asyncio.Lock()
        self.radio = None
        self.joining = 0.0
        self.refilling = False
        self.stopped = False
        self.play_task = None
        self.active_track = None
        self.skipping = False
        self.pause_time = 0.0
        self.total_paused = 0.0
        self.ticker_task = None

    def pause(self):
        if not self.paused:
            self.paused = True
            self.pause_time = time.time()
            if REC:
                REC.on_pause(self.chat_id)

    def resume(self):
        if self.paused:
            self.paused = False
            if self.pause_time:
                self.total_paused += time.time() - self.pause_time
                self.pause_time = 0.0
            if REC:
                REC.on_resume(self.chat_id)

    def elapsed(self) -> int:
        if not self.started:
            return 0
        now = self.pause_time if (self.paused and self.pause_time) else time.time()
        el = int(now - self.started - self.total_paused)
        if self.current and self.current.duration:
            el = min(max(el, 0), self.current.duration)
        return max(el, 0)


class Slot:
    """Bir asistan hesabı: öz Telethon client-i və öz py-tgcalls mühərriki."""

    def __init__(self, idx: int, path: str):
        self.idx, self.path = idx, path
        self.client = self.calls = self.me = None
        self.status, self.error = "söndürülüb", ""

    @property
    def icon(self) -> str:
        return SLOT_ICONS[self.idx] if self.idx < len(SLOT_ICONS) else f"#{self.idx + 1}"

    @property
    def voice_enabled(self) -> bool:
        return _get(f"va:slot_voice:{self.idx}", "1" if self.idx == 0 else "0") == "1"

    @voice_enabled.setter
    def voice_enabled(self, val: bool):
        _set(f"va:slot_voice:{self.idx}", "1" if val else "0")

    @property
    def running(self) -> bool:
        if self.client is None:
            return False
        try:
            if not self.client.is_connected():
                return False
        except Exception:
            return False
        if self.voice_enabled:
            return self.calls is not None
        return True

    def has_session(self) -> bool:
        return os.path.isfile(self.path) and os.path.getsize(self.path) > 10

    def read_session(self) -> str:
        with open(self.path, encoding="utf-8") as f:
            return f.read().strip()

    def write_session(self, s: str):
        tg_login.write_secret(self.path, s)

    def name(self) -> str:
        me = self.me
        if not me:
            return f"{self.icon} —"
        return f"{self.icon} @{me.username}" if me.username else f"{self.icon} {me.first_name or me.id}"


def extra_session_path(idx: int) -> str:
    return os.path.join(os.path.dirname(SESSION_PATH) or "data", f"assistant_{idx + 1}.session")


class NotMember(Exception):
    """Asistan qrupda deyil — qoşulmaq üçün admin icazəsi lazımdır."""
