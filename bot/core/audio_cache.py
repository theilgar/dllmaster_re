"""
📦 Mahnı depo kanalı və keş — bot/core/audio_cache.py

Hər yüklənən mahnı depo kanalına (default: @dllmasterdepo) göndərilir və onun Telegram file_id-si
bazada video_id ilə saxlanılır. Eyni mahnını kim istəsə, YouTube-dan yükləmədən dərhal göndərilir.

config.env:
  DEPO_CHANNEL=@dllmasterdepo     (və ya kanalın rəqəmli ID-si: -100...)
Bot kanalda "Mesaj göndərmə" icazəsi olan admin olmalıdır.
"""
import asyncio
import contextlib
import logging
import os
import threading
import time
import re
from html import escape

from aiogram.types import BufferedInputFile, FSInputFile

from bot.core import song_names as names
from common.database import get_db

logger = logging.getLogger(__name__)

_locks = {}
_depo = {"id": None, "checked": 0, "error": None}
_depo_yt = {"id": None, "checked": 0, "error": None}
RETRY_AFTER = 600          # kanal tapılmasa 10 dəq. sonra yenidən yoxla


DEFAULT_DEPO = "@dllmasterdepo"


def _setting(key: str):
    try:
        return get_db().get_setting(key)
    except Exception:
        return None


def depo_setting() -> str:
    """Depo kanalı (YouTube Music): /menu (depo:channel) → config.env DEPO_CHANNEL → default."""
    return (_setting("depo:channel") or os.getenv("DEPO_CHANNEL") or DEFAULT_DEPO).strip()


def depo_yt_setting() -> str:
    """YouTube mahnıları üçün xüsusi depo kanalı: /menu (depo:channel_yt) → DEPO_YT_CHANNEL → boş."""
    return (_setting("depo:channel_yt") or os.getenv("DEPO_YT_CHANNEL") or "").strip()


def set_depo_channel(ref: str, title: str = None, link: str = None):
    """/menu → 📦 Depo → 📢 Depo kanalı (əsas/YTM). Keşlənmiş kanal ID-si sıfırlanır."""
    db = get_db()
    db.set_setting("depo:channel", str(ref))
    db.set_setting("depo:title", title)
    db.set_setting("depo:link_auto", link)
    _depo.update(id=None, checked=0, error=None)


def set_depo_yt_channel(ref: str, title: str = None):
    """YouTube mahnıları üçün ayrıca depo kanalı təyin edir (boş ref kanalı ləğv edir)."""
    db = get_db()
    db.set_setting("depo:channel_yt", str(ref or ""))
    db.set_setting("depo:title_yt", title or "")
    _depo_yt.update(id=None, checked=0, error=None)


def depo_thread_setting() -> int | None:
    """Əsas (YT Music) depo üçün forum topic ID: /menu (depo:thread) → DEPO_THREAD → None."""
    val = _setting("depo:thread") or os.getenv("DEPO_THREAD")
    try:
        return int(val) if val else None
    except Exception:
        return None


def depo_yt_thread_setting() -> int | None:
    """YouTube deposu üçün forum topic ID: /menu (depo:thread_yt) → DEPO_YT_THREAD → None."""
    val = _setting("depo:thread_yt") or os.getenv("DEPO_YT_THREAD")
    try:
        return int(val) if val else None
    except Exception:
        return None


def set_depo_thread(thread_id: int | None):
    """Əsas depo üçün forum topic ID təyin edir (None/0 sıfırlayır)."""
    get_db().set_setting("depo:thread", str(thread_id) if thread_id else "")


def set_depo_yt_thread(thread_id: int | None):
    """YouTube deposu üçün forum topic ID təyin edir (None/0 sıfırlayır)."""
    get_db().set_setting("depo:thread_yt", str(thread_id) if thread_id else "")


def set_depo_link(link: str = None):
    """/start düyməsinin linki (boş — avtomatik: @username və ya dəvət linki)."""
    get_db().set_setting("depo:link", link or None)


def set_depo_show(show: bool):
    get_db().set_setting("depo:show_start", "1" if show else "0")


def depo_public() -> dict:
    """/start-dakı 📦 Depo kanalı düyməsi üçün: {link, title, show, custom}."""
    ref = depo_setting()
    custom = _setting("depo:link")
    link = custom or _setting("depo:link_auto")
    if not link and ref.startswith("@"):
        link = f"https://t.me/{ref[1:]}"
    return {"link": link, "title": _setting("depo:title") or ref, "custom": bool(custom),
            "show": (_setting("depo:show_start") or "1") == "1"}


def lock_for(video_id: str) -> asyncio.Lock:
    """Eyni mahnını iki nəfər eyni anda istəyəndə ikinci birincini gözləsin, iki dəfə yüklənməsin."""
    lock = _locks.get(video_id)
    if lock is None:
        if len(_locks) > 2000:          # yaddaş böyüməsin
            for k in [k for k, l in _locks.items() if not l.locked()][:1000]:
                _locks.pop(k, None)
        lock = _locks[video_id] = asyncio.Lock()
    return lock


async def depo_chat_id(bot):
    """Depo kanalının rəqəmli ID-si (username-dən bir dəfə çevrilir); əlçatan deyilsə None."""
    if _depo["id"]:
        return _depo["id"]
    if time.time() - _depo["checked"] < RETRY_AFTER and _depo["checked"]:
        return None
    _depo["checked"] = time.time()
    ref = depo_setting()
    try:
        chat = await bot.get_chat(int(ref) if ref.lstrip("-").isdigit() else ref)
        _depo["id"], _depo["error"] = chat.id, None
        logger.info(f"📦 Depo kanalı: {getattr(chat, 'title', ref)} ({chat.id})")
        return chat.id
    except Exception as e:
        _depo["error"] = str(e)
        logger.warning(f"📦 Depo kanalı əlçatan deyil ({ref}): {e} — botu kanala admin kimi əlavə edin")
        return None


async def depo_yt_chat_id(bot):
    """YouTube mahnıları üçün ayrıca depo kanalının ID-si; təyin edilməyibsə və ya əlçatan deyilsə None."""
    ref = depo_yt_setting()
    if not ref:
        return None
    if _depo_yt["id"]:
        return _depo_yt["id"]
    if time.time() - _depo_yt["checked"] < RETRY_AFTER and _depo_yt["checked"]:
        return None
    _depo_yt["checked"] = time.time()
    try:
        chat = await bot.get_chat(int(ref) if ref.lstrip("-").isdigit() else ref)
        _depo_yt["id"], _depo_yt["error"] = chat.id, None
        logger.info(f"▶️ YouTube Depo kanalı: {getattr(chat, 'title', ref)} ({chat.id})")
        return chat.id
    except Exception as e:
        _depo_yt["error"] = str(e)
        logger.warning(f"▶️ YouTube Depo kanalı əlçatan deyil ({ref}): {e}")
        return None


async def depo_target_chat_id(bot, source: str = "ytm"):
    """Mənbəyə uyğun depo kanalının ID-si."""
    cid, _ = await depo_target_chat_info(bot, source=source)
    return cid


async def depo_target_chat_info(bot, source: str = "ytm") -> tuple[int | None, int | None]:
    """
    Mənbəyə uyğun (chat_id, message_thread_id) cütlüyü:
    - source == 'yt' olarsa və YouTube kanalı/topiki mövcuddursa → YouTube hədəfi
    - əks halda → əsas depo hədəfi
    """
    if source == "yt":
        yt_id = await depo_yt_chat_id(bot)
        if yt_id:
            return yt_id, depo_yt_thread_setting()
        yt_thread = depo_yt_thread_setting()
        if yt_thread:
            base_id = await depo_chat_id(bot)
            return base_id, yt_thread
    base_id = await depo_chat_id(bot)
    return base_id, depo_thread_setting()


def depo_status() -> dict:
    return {
        "ref": depo_setting(),
        "id": _depo["id"],
        "error": _depo["error"],
        "thread": depo_thread_setting(),
        "ref_yt": depo_yt_setting(),
        "id_yt": _depo_yt["id"],
        "error_yt": _depo_yt["error"],
        "thread_yt": depo_yt_thread_setting(),
        "title_yt": _setting("depo:title_yt") or "",
    }


# ───────────────── 🧠 L1 In-Memory Audio LRU Keşi ─────────────────
from collections import OrderedDict
_L1_CACHE = OrderedDict()
_L1_MAX_SIZE = 3000
_L1_STATS = {"hits": 0, "misses": 0}
_L1_ENABLED_OVERRIDE: bool | None = None


def is_l1_cache_enabled() -> bool:
    """L1 RAM keşi aktivdirmi (susmaya görə 1/aktivdir)."""
    try:
        from common.utilities import is_accel_auto_enabled
        if is_accel_auto_enabled():
            return True
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/audio_cache.py:221]: %r", _e)
    if _L1_ENABLED_OVERRIDE is not None:
        return _L1_ENABLED_OVERRIDE
    try:
        val = _setting("opt:l1_cache")
        if val is not None:
            return val != "0"
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/audio_cache.py:229]: %r", _e)
    return True


def set_l1_cache_enabled(enabled: bool):
    """L1 RAM keşini açır və ya bağlayır."""
    global _L1_ENABLED_OVERRIDE
    _L1_ENABLED_OVERRIDE = enabled
    try:
        get_db().set_setting("opt:l1_cache", "1" if enabled else "0")
    except Exception as e:
        logger.warning(f"L1 cache ayarı bazaya saxlanmadı (yaddaşda tətbiq edildi): {e}")
    if not enabled:
        clear_l1_cache()


def clear_l1_cache():
    """L1 RAM keşini tamamilə təmizləyir."""
    _L1_CACHE.clear()
    _L1_STATS["hits"] = 0
    _L1_STATS["misses"] = 0


def l1_cache_stats() -> dict:
    """L1 RAM keşinin statistikası (ölçü, hitlər, hit-rate)."""
    total = _L1_STATS["hits"] + _L1_STATS["misses"]
    rate = (_L1_STATS["hits"] / total * 100) if total > 0 else 0.0
    return {
        "enabled": is_l1_cache_enabled(),
        "size": len(_L1_CACHE),
        "max": _L1_MAX_SIZE,
        "hits": _L1_STATS["hits"],
        "misses": _L1_STATS["misses"],
        "hit_rate": rate,
    }


def get_cached(video_id: str):
    if not video_id:
        return None

    enabled = is_l1_cache_enabled()
    if enabled and video_id in _L1_CACHE:
        _L1_STATS["hits"] += 1
        _L1_CACHE.move_to_end(video_id)
        return _L1_CACHE[video_id]

    try:
        row = get_db().cache_get(video_id)
    except Exception as e:
        logger.warning(f"Keş oxunmadı: {e}")
        return None

    if enabled:
        _L1_STATS["misses"] += 1
        if row:
            _L1_CACHE[video_id] = row
            _L1_CACHE.move_to_end(video_id)
            if len(_L1_CACHE) > _L1_MAX_SIZE:
                _L1_CACHE.popitem(last=False)
    return row


def refetch_once(video_id: str) -> bool:
    """Artisti "YouTube" olan köhnə keşi yenidən yükləmək olar? Hər video üçün yalnız bir dəfə True.
    Əvvəl hər dəfə yenidən yüklənirdi — artist yenə tapılmayanda eyni mahnı depoya təkrar-təkrar düşürdü."""
    if not video_id:
        return False
    try:
        db = get_db()
        with db.sql() as cur:
            cur.execute("INSERT INTO cache_refetch (video_id, ts) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (video_id, int(time.time())))
            return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"cache_refetch: {e}")
        return False


_PENDING_HITS = {}
_LAST_HITS_FLUSH = time.time()


def mark_hit(video_id: str):
    """Mahnı hit sayını artırır (RAM-da buferlənir və vaxtaşırı toplu tranzaksiya ilə yazılır)."""
    if not video_id:
        return
    _PENDING_HITS[video_id] = _PENDING_HITS.get(video_id, 0) + 1
    now = time.time()
    global _LAST_HITS_FLUSH
    if len(_PENDING_HITS) >= 20 or (now - _LAST_HITS_FLUSH > 15 and _PENDING_HITS):
        _flush_pending_hits()


def _flush_pending_hits():
    global _LAST_HITS_FLUSH
    _LAST_HITS_FLUSH = time.time()
    if not _PENDING_HITS:
        return
    batch = list(_PENDING_HITS.items())
    _PENDING_HITS.clear()

    def _worker():
        try:
            db = get_db()
            with db.sql() as cur:
                now_ts = int(time.time())
                for vid, cnt in batch:
                    cur.execute(
                        "UPDATE audio_cache SET hits = hits + %s, last_hit = %s WHERE video_id = %s",
                        (cnt, now_ts, vid),
                    )
        except Exception as e:
            logger.debug(f"Hits batch flush xətası: {e}")

    threading.Thread(target=_worker, daemon=True, name="audio-hit-flush").start()


def invalidate(video_id: str):
    _L1_CACHE.pop(video_id, None)
    try:
        get_db().cache_delete(video_id)
        mark_dirty()
        logger.info(f"📦 Keşdən silindi (file_id etibarsızdır): {video_id}")
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/audio_cache.py:148]: %r", _e)


def save_from_message(video_id: str, msg, title=None, performer=None, duration=None, yt_title=None):
    """Göndərilmiş audio mesajından file_id-ni keşə yazır."""
    audio = getattr(msg, "audio", None)
    if not video_id or not audio:
        return None
    try:
        get_db().cache_put(
            video_id, audio.file_id, audio.file_unique_id, title or audio.title, performer or audio.performer,
            duration or audio.duration, audio.file_size, msg.chat.id, msg.message_id, yt_title=yt_title,
        )
        mark_dirty()
        if is_l1_cache_enabled():
            _L1_CACHE[video_id] = {
                "video_id": video_id,
                "file_id": audio.file_id,
                "unique_id": audio.file_unique_id,
                "title": title or audio.title,
                "performer": performer or audio.performer,
                "duration": duration or audio.duration,
                "size": audio.file_size,
                "chat_id": msg.chat.id,
                "message_id": msg.message_id,
                "yt_title": yt_title,
            }
            _L1_CACHE.move_to_end(video_id)
            if len(_L1_CACHE) > _L1_MAX_SIZE:
                _L1_CACHE.popitem(last=False)
    except Exception as e:
        logger.warning(f"Keşə yazılmadı: {e}")
    return audio.file_id


# ───────────── 🔎 Depo indeksi: adla axtarış və dublikatların qarşısı ─────────────
_index = {"rows": [], "built": 0, "dirty": True}
_build_lock = threading.Lock()
_tok_memo = {}                # (ad, ifaçı) → hazır söz dəstləri — yenidən qurmada regex təkrarlanmır


def mark_dirty():
    _index["dirty"] = True


def search_depo(query: str, limit: int = 5) -> list:
    """Sorğuya uyğun depo mahnıları (PostgreSQL pg_trgm GIN indeksi ilə ultra-sürətli)."""
    q = re.sub(r'["\'«»“”„‟`]', ' ', query or '').strip()
    q = re.sub(r'\s+', ' ', q).strip(" -:;,.")
    if not q:
        return []
    try:
        return get_db().cache_search(q, limit=limit)
    except Exception as e:
        logger.warning(f"Depo axtarışı xətası: {e}")
        return []


def find_same(artist: str, track: str, duration: int = 0):
    """Eyni mahnı (ad + artist, müddət ±8 san.) depoda artıq varsa onun sətri."""
    if not artist or not track:
        return None
    try:
        return get_db().cache_find_same(artist, track, duration)
    except Exception as e:
        logger.warning(f"Depo dublikat yoxlaması xətası: {e}")
        return None


def alias(video_id: str, row: dict, yt_title=None):
    """Bu video_id-ni depodakı mövcud posta bağlayır (təkrar yükləmə olmadan)."""
    try:
        get_db().cache_put(video_id, row["file_id"], row.get("unique_id"), row.get("title"), row.get("performer"),
                           row.get("duration"), row.get("size"), row.get("chat_id"), row.get("message_id"),
                           yt_title=yt_title)
        mark_dirty()
        logger.info(f"📦 Dublikat qarşısı: {video_id} → depodakı '{row.get('performer')} - {row.get('title')}'")
    except Exception as e:
        logger.warning(f"Alias yazılmadı: {e}")


def cached_ids(video_ids) -> set:
    if not video_ids:
        return set()
    vids = {v for v in video_ids if v}
    found = set()
    if is_l1_cache_enabled():
        for vid in vids:
            if vid in _L1_CACHE:
                found.add(vid)
        missing = vids - found
    else:
        missing = vids

    if missing:
        try:
            db_found = get_db().cache_cached_ids(list(missing))
            found.update(db_found)
        except Exception as _e:
            logger.debug("udulan xəta [bot/core/audio_cache.py:453]: %r", _e)
    return found


async def upload_to_depo(bot, file_path, filename: str, title: str, performer: str,
                         duration: int, url: str, video_id: str, thumb: bytes = None, yt_title: str = None,
                         source: str = "ytm"):
    """Faylı (yol və ya bytes) mənbəyə uyğun depo kanalına yükləyir, keşə yazır və file_id qaytarır. Alınmasa None."""
    chat_id, thread_id = await depo_target_chat_info(bot, source=source)
    if not chat_id:
        return None
    if not isinstance(file_path, (bytes, bytearray)) and not os.path.exists(file_path):
        logger.debug(f"📦 Depoya yükləmək üçün fayl tapılmadı: {file_path}")
        return None
    title = names.clean_track(title or "") if title else title           # tarix, (Official Video) və s.
    performer = names.clean_artist(performer or "") if performer and performer != "YouTube" else performer
    tag = "#v" + (video_id or "").replace("-", "_")
    is_yt = source == "yt"
    src_icon = "▶️" if is_yt else "🎵"
    src_label = "YouTube" if is_yt else "YouTube Music"
    caption = (
        f"{src_icon} <b>{escape(performer or '')}{' — ' if performer else ''}{escape(title or '')}</b>\n"
        f"🔗 <a href=\"{escape(url, quote=True)}\">{src_label}</a> · {tag}"
    )
    async def _send_and_save():
        for attempt in range(2):
            try:
                send_kw = {
                    "chat_id": chat_id,
                    "audio": (BufferedInputFile(file_path, filename=filename) if isinstance(file_path, (bytes, bytearray))
                              else FSInputFile(file_path, filename=filename)),
                    "title": (title or "")[:64] or None,
                    "performer": (performer or "")[:64] or None,
                    "duration": int(duration or 0) or None,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "disable_notification": True,
                    "thumbnail": BufferedInputFile(thumb, filename="cover.jpg") if thumb else None,
                }
                if thread_id:
                    send_kw["message_thread_id"] = thread_id
                msg = await bot.send_audio(**send_kw)
                # keşə yazma göndərmə ilə bir yerdədir: restart / ləğv arada olsa belə post bazaya düşür
                return await asyncio.to_thread(save_from_message, video_id, msg, title, performer, duration,
                                               yt_title=yt_title)
            except Exception as e:
                err_str = str(e).lower()
                if (isinstance(e, OSError) and getattr(e, "errno", None) == 2) or "errno 2" in err_str:
                    logger.debug(f"📦 Depoya göndərmə zamanı fayl tapılmadı/silindi: {e}")
                    return None
                retry_after = getattr(e, "retry_after", None)
                if retry_after is None and "flood" in str(e).lower():
                    m = re.search(r"retry in (\d+)", str(e).lower())
                    if m:
                        retry_after = int(m.group(1))
                if attempt == 0 and retry_after and retry_after <= 10:
                    logger.info(f"📦 Depo FloodWait ({retry_after} san.): gözlənilir və təkrar cəhd olunur...")
                    await asyncio.sleep(retry_after + 0.5)
                    continue
                raise

    try:
        # 🛡 shield: işçi ləğv olunsa (restart, ⏹) göndərmə yarımçıq qalmır — yoxsa post depoda olur,
        # bazada olmur və növbəti dəfə eyni mahnı yenidən yüklənirdi
        inner = asyncio.ensure_future(_send_and_save())
        def _on_done(f):
            if f.cancelled():
                return
            exc = f.exception()
            if not exc:
                return
            err_msg = str(exc).lower()
            if "errno 2" in err_msg or (isinstance(exc, OSError) and getattr(exc, "errno", None) == 2):
                logger.debug(f"📦 Depo yazısı (fayl silindi/tapılmadı): {exc}")
            else:
                logger.warning(f"📦 Depo yazısı (fon): {exc}")

        inner.add_done_callback(_on_done)
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if "chat not found" in str(e).lower() or "not enough rights" in str(e).lower() \
                or "forbidden" in str(e).lower():
            _depo["id"], _depo["error"], _depo["checked"] = None, str(e), time.time()
        return None


async def recover_bad_file(context, video_id: str) -> str:
    """
    Keşdəki file_id işləmədi. Əvvəl yenidən yükləmə əvəzinə depodakı postu sink çatına forward edib
    botun yeni file_id-sini alırıq (köməkçi bot file_id-si, çevrilməmiş indeks postu və s.).
    'retry'    — keş düzəldi, eyni postdan göndər
    'reupload' — post depoda yoxdur, keş silindi → yenidən yüklənəcək
    Müvəqqəti xətada (flood) RuntimeError — dublikat yaratmamaq üçün yenidən yükləmirik.
    """
    c = get_cached(video_id)
    pool = getattr(context, "depo_helpers", None)
    if c and c.get("chat_id") and c.get("message_id") and pool is not None:
        try:
            audio = await pool._forward_audio(context.bot, c["chat_id"], c["message_id"], None)
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "message_id_invalid" in err or "can't be forwarded" in err:
                logger.info(f"📦 Depo postu yoxdur ({c['chat_id']}/{c['message_id']}) — yenidən yüklənəcək")
                await asyncio.to_thread(_drop_post, c["chat_id"], c["message_id"])
                return "reupload"
            raise RuntimeError(f"Depo müvəqqəti əlçatan deyil, bir az sonra yenidən yoxla ({str(e)[:80]})")
        new = {"file_id": audio.file_id, "unique_id": audio.file_unique_id, "title": c.get("title"),
               "performer": c.get("performer"), "duration": c.get("duration"), "size": audio.file_size,
               "chat_id": c["chat_id"], "message_id": c["message_id"]}
        await asyncio.to_thread(get_db().cache_repoint, c["chat_id"], c["message_id"], new)
        with contextlib.suppress(Exception):
            pool.foreign.pop(c["file_id"], None)
            pool.converted[c["file_id"]] = audio.file_id
            await asyncio.to_thread(pool._save_converted, c["file_id"], audio.file_id)
        mark_dirty()
        logger.info(f"📦 file_id depo postundan bərpa olundu: {video_id}")
        return "retry"
    invalidate(video_id)
    return "reupload"


def _drop_post(chat_id, message_id):
    """Silinmiş depo postuna bağlı bütün keş sətirləri (aliaslar da)."""
    db = get_db()
    with db.sql() as cur:
        cur.execute("DELETE FROM audio_cache WHERE chat_id=%s AND message_id=%s", (chat_id, message_id))
    mark_dirty()


def is_bad_file_id(err: Exception) -> bool:
    e = str(err).lower()
    return any(k in e for k in ("wrong file identifier", "file reference", "wrong remote file",
                                "file_id", "failed to get http url content"))
