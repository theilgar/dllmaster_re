"""
🛠 common/maintenance.py — Texniki iş rejimi: vahid vəziyyət, müddət (avto-bitmə), mətn və gözləyənlər.

Qapı (bütün istifadəçi əmrləri / düymələri / inline) — bot/plugins/maintenance_plugin.py.
Panel — /menu → 🛠 Bot idarəsi → 🛠 Texniki iş rejimi (bot/menus/views/maintenance.py).

Ayarlar (bazada):
  bot:maintenance_mode   "1" / "0"                      (köhnə açar — digər plugin-lər onu oxuyur)
  bot:maintenance_until  bitmə vaxtı (epoch, 0 = əl ilə)
  maint:duration         açanda tətbiq olunan müddət (san., 0 = müddətsiz)
  maint:text             istifadəçilərə göstərilən mətn ({eta} — təxmini bitmə sətri)
  maint:voice_stop       "1" — açanda aktiv səsli yayımlar dayandırılsın
  maint:depo_pause       "1" — açanda depo doldurucu pauza olsun, bitəndə davam etsin
  maint:notify           "1" — bitəndə cəhd edən istifadəçilərə xəbər ver
"""
from __future__ import annotations

import json
import logging
import time
from common.kv import next_of

logger = logging.getLogger(__name__)

K_ON, K_UNTIL = "bot:maintenance_mode", "bot:maintenance_until"
K_DURATION, K_TEXT = "maint:duration", "maint:text"
K_VOICE, K_DEPO, K_NOTIFY = "maint:voice_stop", "maint:depo_pause", "maint:notify"
K_MODE = "maint:mode"
K_STYLED = "maint:styled"
K_DELETE, K_NOTICES = "maint:delete", "maint:notice_msgs"
DELETE_MODES = ("0", "60", "600", "3600", "end")                 # silinmir / 1 dəq. / 10 dəq. / 1 saat / rejim bitəndə
DELETE_LABELS = {"0": "silinmir", "60": "1 dəq. sonra", "600": "10 dəq. sonra", "3600": "1 saat sonra",
                 "end": "rejim bitəndə"}
NOTICES_MAX = 500
K_STREAM_NOTICE, K_STREAM_CHATS = "maint:stream_notice", "maint:stream_chats"
MODES = ("block", "notice")
NOTICE_EVERY = 1800.0                       # xəbərdarlıq rejimi: eyni istifadəçiyə 30 dəq.-də bir dəfə
K_WAITERS, K_DEPO_WAS = "maint:waiters", "maint:depo_was_running"

DURATIONS = (0, 900, 1800, 3600, 7200, 21600, 43200)           # 0 = müddətsiz
WAITERS_MAX = 2000
CACHE_TTL = 3.0

DEFAULT_TEXT = ("🛠 <b>Texniki işlər gedir</b>\n\n"
                "Bot hazırda yenilənir, bir azdan yenidən işləyəcək.{eta}\n\n"
                "Səbrinizə görə təşəkkür edirik 💙")
NOTICE_TEXT = ("⚠️ <b>Texniki işlər gedir</b>\n\n"
               "Bot işləyir, amma qısa fasilələr və ya xətalar ola bilər.{eta}")
DONE_TEXT = "✅ <b>Texniki işlər başa çatdı</b>\n\nBot yenidən işləyir — davam edə bilərsiniz 🎵"

_cache: dict = {"t": 0.0, "v": None}
_waiters: set = set()
_waiters_saved = 0.0


def _db():
    from common.database import get_db
    return get_db()


def _get(key: str, default: str = "") -> str:
    try:
        v = _db().get_setting(key)
        return default if v is None else str(v)
    except Exception:
        return default


def _set(key: str, value) -> None:
    try:
        _db().set_setting(key, None if value is None else str(value))
    except Exception as e:
        logger.warning(f"Texniki iş ayarı saxlanmadı ({key}): {e}")
    _cache["t"] = 0.0


def duration_label(sec: int) -> str:
    if not sec:
        return "♾ müddətsiz"
    return f"{sec // 3600} saat" if sec >= 3600 and sec % 3600 == 0 else f"{sec // 60} dəq."


def _snapshot() -> dict:
    now = time.monotonic()
    if _cache["v"] is not None and now - _cache["t"] < CACHE_TTL:
        return _cache["v"]
    try:
        until = float(_get(K_UNTIL, "0") or 0)
    except ValueError:
        until = 0.0
    try:
        dur = int(_get(K_DURATION, "0") or 0)
    except ValueError:
        dur = 0
    v = {"on": _get(K_ON) == "1", "until": until, "duration": dur if dur in DURATIONS else 0,
         "text": _get(K_TEXT), "voice": _get(K_VOICE) == "1", "depo": _get(K_DEPO) == "1",
         "notify": _get(K_NOTIFY, "1") != "0",
         "stream_notice": _get(K_STREAM_NOTICE, "1") != "0",
         "styled": _get(K_STYLED, "1") != "0",
         "delete": _get(K_DELETE, "0") if _get(K_DELETE, "0") in DELETE_MODES else "0",
         "mode": _get(K_MODE, "block") if _get(K_MODE, "block") in MODES else "block"}
    _cache.update(t=now, v=v)
    return v


def settings() -> dict:
    return dict(_snapshot())


def is_active() -> bool:
    """Rejim açıqdır VƏ müddəti bitməyib (bitibsə plugin-in izləyicisi söndürüb xəbər verir)."""
    s = _snapshot()
    return bool(s["on"]) and (not s["until"] or s["until"] > time.time())


def mode() -> str:
    return _snapshot()["mode"]


def is_blocking() -> bool:
    """Rejim açıqdır VƏ istifadəçiləri bağlayır (xəbərdarlıq rejimində — yox, hamı istifadə edə bilir)."""
    return is_active() and _snapshot()["mode"] == "block"


def toggle_mode() -> str:
    cur = _snapshot()["mode"]
    nxt = next_of(MODES, cur)
    _set(K_MODE, nxt)
    return nxt


def is_due() -> bool:
    """Açıqdır, amma müddəti bitib — söndürmək və xəbər vermək lazımdır."""
    s = _snapshot()
    return bool(s["on"]) and bool(s["until"]) and s["until"] <= time.time()


def remaining() -> int | None:
    """Bitməyə qalan saniyə (müddətsizdirsə None)."""
    s = _snapshot()
    if not s["on"] or not s["until"]:
        return None
    return max(0, int(s["until"] - time.time()))


def eta_line() -> str:
    left = remaining()
    if not left:
        return ""
    return f"\n⏱ Təxmini bitmə: <b>~{left // 3600} saat {left % 3600 // 60} dəq.</b>" if left >= 3600 \
        else f"\n⏱ Təxmini bitmə: <b>~{max(1, left // 60)} dəq.</b>"


def render_text(custom: str | None = None) -> str:
    """İstifadəçilərə göstərilən mətn: creator-un mətni (və ya defolt); {eta} dəyişəni təxmini bitmə ilə əvəz olunur."""
    default = NOTICE_TEXT if _snapshot()["mode"] == "notice" else DEFAULT_TEXT
    text = (custom if custom is not None else _snapshot()["text"]) or default
    eta = eta_line()
    return text.replace("{eta}", eta) if "{eta}" in text else text + eta


def message(kind: str = "notice", custom: str | None = None) -> str:
    """İSTİFADƏÇİYƏ / QRUPA gedən hazır mətn. kind: "notice" (bağlama / xəbərdarlıq) | "done" (rejim bitdi).
    Açıqdırsa (maint:styled) 📋 Görünüş üslubuna uyğun formatlanır — başlıq qalın, qalanı kod bloku / sitat
    (bot/plugins/compact_ui_plugin.boxify: /ui-dakı seçimlə eyni). Alınmasa və ya çox uzundursa — adi HTML."""
    raw = DONE_TEXT if kind == "done" else render_text(custom)
    if not _snapshot()["styled"]:
        return raw
    try:
        from bot.plugins.compact_ui_plugin import boxify
        from common.menu import get_ui_style
        return boxify(raw, True, style=get_ui_style()) or raw
    except Exception as e:
        logger.debug("Texniki iş mesajı formatlanmadı: %r", e)
        return raw


def enable(duration: int | None = None) -> float:
    """Rejimi açır. duration (san.) verilməsə saxlanmış müddət işlənir (0 — müddətsiz). Bitmə vaxtını qaytarır."""
    s = _snapshot()
    dur = s["duration"] if duration is None else int(duration)
    until = time.time() + dur if dur else 0
    _set(K_UNTIL, int(until) if until else 0)
    _set(K_ON, "1")
    clear_waiters()
    return until


def disable() -> list:
    """Rejimi söndürür; bu müddətdə cəhd edənlərin siyahısını qaytarır (xəbər vermək üçün)."""
    _set(K_ON, "0")
    _set(K_UNTIL, 0)
    return pop_waiters()


def set_option(key: str, value) -> None:
    _set(key, value)


def cycle_duration() -> int:
    cur = _snapshot()["duration"]
    nxt = next_of(DURATIONS, cur)
    _set(K_DURATION, nxt)
    return nxt


def toggle_option(key: str, default_on: bool = False) -> bool:
    cur = _get(key, "1" if default_on else "0") == "1"
    _set(key, "0" if cur else "1")
    return not cur


# ── gözləyənlər (rejim zamanı cəhd edən istifadəçilər) ──
def add_waiter(uid: int) -> None:
    global _waiters_saved
    if len(_waiters) >= WAITERS_MAX or uid in _waiters:
        return
    _waiters.add(int(uid))
    if time.monotonic() - _waiters_saved > 30:                    # restartdan sonra da itməsin — seyrək yazılır
        _waiters_saved = time.monotonic()
        _set(K_WAITERS, json.dumps(sorted(_waiters)))


def clear_waiters() -> None:
    _waiters.clear()
    _set(K_WAITERS, "[]")


def pop_waiters() -> list:
    saved = []
    try:
        saved = [int(x) for x in json.loads(_get(K_WAITERS, "[]") or "[]")]
    except (ValueError, TypeError):
        pass
    out = sorted(set(saved) | _waiters)
    clear_waiters()
    return out


def waiters_count() -> int:
    return len(_waiters)


# ── bildiriş göndərilən yayım qrupları (bitəndə onlara da «bitdi» getsin) ──
def save_stream_chats(chat_ids: list) -> None:
    _set(K_STREAM_CHATS, json.dumps([int(c) for c in chat_ids]))


def pop_stream_chats() -> list:
    try:
        out = [int(c) for c in json.loads(_get(K_STREAM_CHATS, "[]") or "[]")]
    except (ValueError, TypeError):
        out = []
    _set(K_STREAM_CHATS, "[]")
    return out


# ── göndərilən bildirişlərin silinməsi ──
_notices: list = []                       # [(chat_id, message_id)] — yalnız «rejim bitəndə sil» üçün izlənir
_notices_saved = 0.0


def delete_mode() -> str:
    return _snapshot()["delete"]


def cycle_delete() -> str:
    cur = _snapshot()["delete"]
    nxt = next_of(DELETE_MODES, cur, "0")
    _set(K_DELETE, nxt)
    return nxt


def track_notice(chat_id: int, message_id: int) -> None:
    global _notices_saved
    if len(_notices) >= NOTICES_MAX:
        return
    _notices.append((int(chat_id), int(message_id)))
    if time.monotonic() - _notices_saved > 30:                  # restartdan sonra da silinə bilsin — seyrək yazılır
        _notices_saved = time.monotonic()
        _set(K_NOTICES, json.dumps(_notices))


def notices_count() -> int:
    return len(_notices)


def pop_notices() -> list:
    saved = []
    try:
        saved = [(int(c), int(m)) for c, m in json.loads(_get(K_NOTICES, "[]") or "[]")]
    except (ValueError, TypeError):
        pass
    out = list(dict.fromkeys(saved + list(_notices)))
    _notices.clear()
    _set(K_NOTICES, "[]")
    return out
