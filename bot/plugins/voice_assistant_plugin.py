"""
🎙 Canlı yayım asistanı — bot/plugins/voice_assistant_plugin.py
(Modullaşdırılmış entry-point; əsas mühərrik bot/voice/ paketindədir)
"""
from __future__ import annotations

from bot.voice.calls_adapter import Calls, pytgcalls_version
from bot.voice.engine import Assistant
from bot.voice.handlers import setup as _setup
from bot.voice.models import (
    DM_CD_STEPS,
    DM_TEXT,
    IDLE_LEAVE,
    LOUDNORM,
    MAX_CACHE_BYTES,
    MAX_DURATION,
    MAX_SLOTS,
    MAXPER_STEPS,
    MIX_REFILL_AT,
    MIX_STEPS,
    PLAY_DIR,
    QUALITY,
    S_DEPO,
    S_DM_CD,
    S_DM_ON,
    S_MAXPER,
    S_MIXAUTO,
    S_MIXN,
    S_NORM,
    S_ON,
    S_QUAL,
    S_SRC,
    S_THROTTLE,
    S_VOL,
    S_WHO,
    SESSIONS_FILE,
    SLOT_ICONS,
    SOURCES,
    VOICE_CACHE_DIR,
    VOL_STEPS,
    WHO,
    ChatState,
    NotMember,
    Slot,
    Track,
    _get,
    _set,
    extra_session_path,
    progress_bar,
)

va = None


def setup(context):
    global va
    _setup(context)
    va = getattr(context, "voice_assistant", None)


__all__ = [
    "setup",
    "va",
    "Assistant",
    "Calls",
    "Track",
    "ChatState",
    "Slot",
    "NotMember",
    "progress_bar",
    "pytgcalls_version",
    "extra_session_path",
    "_get",
    "_set",
    "S_ON",
    "S_SRC",
    "S_DEPO",
    "S_WHO",
    "S_MIXN",
    "S_MIXAUTO",
    "S_QUAL",
    "S_NORM",
    "S_VOL",
    "S_THROTTLE",
    "S_DM_ON",
    "S_DM_CD",
    "DM_CD_STEPS",
    "DM_TEXT",
    "QUALITY",
    "VOL_STEPS",
    "LOUDNORM",
    "MIX_STEPS",
    "MIX_REFILL_AT",
    "PLAY_DIR",
    "VOICE_CACHE_DIR",
    "MAX_CACHE_BYTES",
    "SESSIONS_FILE",
    "MAX_DURATION",
    "IDLE_LEAVE",
    "SOURCES",
    "WHO",
    "MAX_SLOTS",
    "SLOT_ICONS",
    "S_MAXPER",
    "MAXPER_STEPS",
]
