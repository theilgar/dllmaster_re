"""
⚙️ bot/depo/engine.py - Depo doldurucu mühərriki (Filler) və növbə idarəçisi.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque


from bot.core import audio_cache, song_names as names
from bot.core.depo_helpers import UPLOAD_CTX
from bot.core.links import yt_list_url
from bot.depo.cleaner import (
    tg_channel_items,
    _toks,
)
from bot.depo.crawler import (
    COUNTRIES,
    COUNTRY_PRIO_TOP,
    COUNTRY_SEARCH_QUERIES,
    HAS_YTM,
    MAX_DURATION,
    YTM_LOW_WATER,
    YTM_OFFICIAL_CHART_COUNTRIES,
    YTM_PAUSE,
    YTM_SEEN_CAP,
    YTMusic,
    YtmCrawler,
    _chart_artists,
    _chart_tracks,
    _is_valid_chart_for,
    itunes_lookup,
    spotify_lookup,
    ytm_item,
)
from common.database import Settings, get_db
from common.utilities import SHUTDOWN

try:
    from telethon import utils as tl_utils
    from telethon.tl.types import DocumentAttributeAudio, InputMessagesFilterMusic
except ImportError:
    tl_utils = DocumentAttributeAudio = InputMessagesFilterMusic = None

logger = logging.getLogger(__name__)

DEFAULT_SOURCES = [
    {"kind": "sp_playlist", "id": "37i9dQZF1DXcBWIGoYBM5M", "label": "Today's Top Hits"},
    {"kind": "sp_playlist", "id": "37i9dQZEVXbMDoHDwVN2tF", "label": "Top 50 Global"},
]
DELAY_STEPS = [0, 1, 2, 3, 5, 8, 10, 15, 20, 25, 30, 45, 60, 90, 120, 180, 300]
DEFAULT_DELAY = 25
MAX_DELAY = 3600
OLD_SPEEDS = {"slow": 60, "normal": 25, "fast": 8}
DEFAULT_WORKERS = 1
MAX_WORKERS = 128
WORKER_STEPS = [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32,
                40, 48, 56, 64, 80, 96, 112, 128]
RETRY_BACKOFF = [5 * 60, 30 * 60, 2 * 3600, 6 * 3600, 24 * 3600]
NOTFOUND_BACKOFF = [3 * 86400, 7 * 86400]
_PERM_ERR = re.compile(
    r"video unavailable|private video|has been removed|copyright|not available in your country|"
    r"members[- ]only|join this channel|premieres in|live event will begin|this live event|"
    r"live stream recording is not available|"
    r"is not a valid url|unsupported url|no video formats|account associated .* terminated|"
    r"file is too big|request entity too large|too long", re.I)
SYNC_EVERY = 24 * 3600
NOVID_AUTO_MAX = 150
SYNC_AUTO_MAX = 0.3
STATE_FILE = os.getenv("DEPO_STATE_FILE", os.path.join("data", "depo_state.json"))
if not os.getenv("DEPO_STATE_FILE") and not os.path.exists(STATE_FILE) and os.path.exists("depo_state.json"):
    os.makedirs("data", exist_ok=True)
    os.replace("depo_state.json", STATE_FILE)
STATE_SAVE_EVERY = max(1.0, float(os.getenv("DEPO_STATE_EVERY", "2")))
TG_CHECKPOINT = 500
IDLE_SLEEP = 30 * 60
NOTFOUND_RETRY = 3 * 86400
STUCK_AFTER = 10 * 60
NO_PROGRESS_AFTER = 15 * 60
Q_PAGE = 8
_ACTIVE_PHASES = ("SRCH", "CHK", "DL", "SLOT", "UP")

_VID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")
_VID_TAG = re.compile(r"#v(\w{11})\b")


def _post_vid(m):
    for e in getattr(m, "entities", None) or []:
        mm = _VID_RE.search(getattr(e, "url", None) or "")
        if mm:
            return mm.group(1)
    text = getattr(m, "message", "") or ""
    mm = _VID_RE.search(text)
    if mm:
        return mm.group(1)
    mm = _VID_TAG.search(text)
    if mm and "_" not in mm.group(1):
        return mm.group(1)
    return None


def hkey(key) -> str:
    return hashlib.md5(str(key or "").encode("utf-8", "ignore")).hexdigest()[:10]


API_ID = os.getenv("TELETHON_API_ID")
API_HASH = os.getenv("TELETHON_API_HASH")
SESSION_FILE = os.getenv("TELETHON_SESSION_FILE", "userbot.session")   # tg_session.py ilə eyni
SOURCE_KIND_LABELS = {
    "sp_playlist": "Spotify playlist",
    "sp_album": "Spotify albom",
    "sp_artist": "Spotify ifaçı",
    "yt_list": "YouTube playlist",
    "tg_channel": "Telegram (veb)",
    "tg_chat": "Telegram (userbot)",
}


def raise_nofile(workers: int):
    """Açıq fayl / socket limitini (ulimit -n) qaldırır: 128 paralel yt-dlp + ffmpeg + Telegram bağlantıları
    default 1024-ə yaxınlaşır → "Too many open files". Yalnız soft limit, hard limitə qədər."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = max(4096, workers * 40)
        if hard != resource.RLIM_INFINITY:
            want = min(want, hard)
        if soft != resource.RLIM_INFINITY and soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            logger.info(f"📂 Açıq fayl limiti qaldırıldı: {soft} → {want} (hard: {hard})")
        elif hard != resource.RLIM_INFINITY and hard < workers * 20:
            logger.warning(f"⚠️ ulimit -n hard limiti {hard}-dir — {workers} işçi üçün az ola bilər "
                           f"(/etc/security/limits.conf və ya systemd LimitNOFILE=)")
    except Exception as e:
        logger.debug(f"ulimit dəyişmədi: {e}")


def ensure_thread_pool(workers: int):
    """asyncio.to_thread hovuzunu işçi sayına görə böyüdür.

    Default hovuz min(32, CPU+4) thread-dir; çox işçi (hər biri yt-dlp / Spotify / baza üçün thread tutur)
    onu doldurub istifadəçilərin öz yükləmələrini də gözlədərdi. Hovuz yalnız böyüyür, kiçilmir.
    Yükləmə zamanı işçi ~1 thread tutur (yt-dlp), qısa əməliyyatlar üçün ehtiyat + istifadəçilər üçün 32."""
    if workers > 32:
        raise_nofile(workers)
    need = min(512, max(32, workers * 2 + 32))
    from bot.core import cpu_pool

    cpu_pool._MIN_POOL = max(cpu_pool._MIN_POOL, need)   # ⚙️ Performans paneli bundan aşağı kiçiltməsin
    cur = cpu_pool.set_pool_size(need, grow_only=True)
    if cur is not None:
        logger.info(f"🧵 Thread hovuzu böyüdüldü: {cur} → {need} ({workers} işçi üçün)")


def search_depo_for_deletion(query: str, limit: int = 1000) -> list:
    """Depodan və keş bazasından silmək üçün açar sözlərlə mahnıları axtarır."""
    words = [w.strip() for w in query.strip().split() if w.strip()]
    if not words:
        return []
    conds = [
        "(COALESCE(performer, '') || ' ' || COALESCE(title, '') || ' ' || COALESCE(yt_title, '')) ILIKE %s"
        for _ in words
    ]
    where_clause = " AND ".join(conds)
    sql = f"""
        SELECT video_id, title, performer, yt_title, chat_id, message_id
        FROM audio_cache
        WHERE {where_clause}
        ORDER BY message_id DESC
        LIMIT %s
    """
    params = [f"%{w}%" for w in words] + [limit]
    db = get_db()
    rows = db.sql_all(sql, params)
    return [
        {
            "video_id": r[0],
            "title": r[1],
            "performer": r[2],
            "yt_title": r[3],
            "chat_id": r[4],
            "message_id": r[5],
        }
        for r in rows
    ]


async def delete_depo_tracks(bot, tracks: list, filler) -> dict:
    """Mahnıları Telegram depo kanalından, audio_cache bazasından silir və depo_removed-ə salır."""
    if not tracks:
        return {"total": 0, "unique_vids": 0, "tg_deleted": 0, "db_deleted": 0}

    # 1. Telegram kanallarından mesajları sil
    by_chat = {}
    for t in tracks:
        cid, mid = t.get("chat_id"), t.get("message_id")
        if cid and mid:
            by_chat.setdefault(cid, set()).add(mid)

    tg_deleted = 0
    for cid, mids in by_chat.items():
        mid_list = list(mids)
        for i in range(0, len(mid_list), 100):
            chunk = mid_list[i:i + 100]
            try:
                await bot.delete_messages(chat_id=cid, message_ids=chunk)
                tg_deleted += len(chunk)
            except Exception as e:
                logger.warning(f"Telegram delete_messages xətası ({cid}): {e}")
                for mid in chunk:
                    try:
                        await bot.delete_message(chat_id=cid, message_id=mid)
                        tg_deleted += 1
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:1036]: %r", _e)
            await asyncio.sleep(0.05)

    # 2. Baza təmizliyi və qara siyahıya (depo_removed) salınması
    vids = list({t["video_id"] for t in tracks if t.get("video_id")})
    now = int(time.time())
    removed_entries = []
    for t in tracks:
        vid = t.get("video_id")
        if vid:
            p = t.get("performer") or ""
            ti = t.get("title") or t.get("yt_title") or vid
            name = f"{p} - {ti}" if p and p not in ti else ti
            removed_entries.append((vid, name, now))

    db_deleted = 0

    def _db_work():
        nonlocal db_deleted
        db = get_db()
        with db.sql() as cur:
            if vids:
                db_deleted = cur.execute("DELETE FROM audio_cache WHERE video_id = ANY(%s)", (vids,)).rowcount
                # depo_foreign-də video_id sütunu yoxdur — köməkçi file_id-lər depo postuna (chat_id, message_id) bağlıdır
                for cid, mids in by_chat.items():
                    cur.execute("DELETE FROM depo_foreign WHERE chat_id = %s AND message_id = ANY(%s)",
                                (cid, list(mids)))
                if removed_entries:
                    cur.executemany(
                        """INSERT INTO depo_removed (video_id, title, ts) VALUES (%s, %s, %s)
                           ON CONFLICT (video_id) DO UPDATE SET title = EXCLUDED.title, ts = EXCLUDED.ts""",
                        removed_entries,
                    )

    await asyncio.to_thread(_db_work)

    # 3. audio_cache keşini yenilə
    audio_cache.mark_dirty()

    # 4. Doldurucunun aktiv növbələrindən və keşlərindən sil
    if vids:
        filler.removed.update(vids)
        vid_set = set(vids)
        filler.queue = deque(x for x in filler.queue if x.get("vid") not in vid_set and x.get("video_id") not in vid_set)
        filler.country_queue = deque(
            x for x in filler.country_queue if x.get("vid") not in vid_set and x.get("video_id") not in vid_set
        )
        filler.ytm_queue = deque(
            x for x in filler.ytm_queue if x.get("vid") not in vid_set and x.get("video_id") not in vid_set
        )
        filler.seen.difference_update(f"https://www.youtube.com/watch?v={v}" for v in vids)
        filler.ytm_seen.difference_update(vids)

    # 5. Köməkçi botların keşindən çıxar
    pool = getattr(filler.ctx, "depo_helpers", None)
    if pool is not None:
        for cid, mids in by_chat.items():
            gone = set(mids)
            for fid, (fcid, fmid, _) in list(pool.foreign.items()):
                if fcid == cid and fmid in gone:
                    pool.foreign.pop(fid, None)

    return {
        "total": len(tracks),
        "unique_vids": len(vids),
        "tg_deleted": tg_deleted,
        "db_deleted": db_deleted,
    }


# ───────────────────────── 📦 Doldurucu ─────────────────────────


__all__ = [n for n in list(globals()) if not (n.startswith('__') and n.endswith('__'))]
