"""
📣 common/group_ads.py — Canlı yayım userbotunun qrup reklamı: ayarlar, seçilmiş qruplar, gözləmə tarixçəsi.

Reklam mətni YALNIZ asistan hesabının üzv olduğu və seçilmiş (opt-in) qruplara göndərilir — şəxslərə şəxsi mesaj
YOXDUR. Hesabı Telegram məhdudiyyətindən qorumaq üçün sərt hədlər var: qruplar arası ən azı 20 san. (təsadüfi
sapma ilə), bir qrupa təkrar göndərməyə gözləmə, bir dövrdə ən çox 60 qrup.

Ayarlar (bazada, "ads:" önəkli):
  ads:text      reklam mətni (HTML; {bot} — @istifadəçi_adı, {link} — t.me linki)
  ads:groups    seçilmiş qrupların ID siyahısı (JSON)
  ads:delay     qruplar arası fasilə (san., ±30% sapma ilə)
  ads:cooldown  bir qrupa təkrar göndərmə arası (saat)
  ads:max       bir dövrdə maksimum qrup sayı
  ads:slot      "all" və ya asistan hesabının nömrəsi
  ads:last      {qrup_id: son_göndərmə_vaxtı}
  ads:report    son dövrün qısa hesabatı (JSON)
"""
from __future__ import annotations

import json
import logging
import random
import time
from common.kv import next_of

logger = logging.getLogger(__name__)

K_TEXT, K_GROUPS, K_DELAY, K_COOL = "ads:text", "ads:groups", "ads:delay", "ads:cooldown"
K_MAX, K_SLOT, K_LAST, K_REPORT = "ads:max", "ads:slot", "ads:last", "ads:report"

DELAYS = (30, 60, 90, 180, 300)               # san.
COOLDOWNS = (6, 12, 24, 72)                   # saat
MAXES = (10, 20, 40, 60)
MIN_DELAY = 20
HARD_MAX = 60
TEXT_LIMIT = 1500

DEFAULT_TEXT = ("🎵 <b>{bot}</b> — musiqi yüklə, axtar və dinlə!\n\n"
                "🔎 Mahnının adını yaz, link göndər və ya inline axtar.\n"
                "🎙 Qruplarda səsli yayım və playlist dəstəyi.\n\n"
                "👉 {link}")


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
        logger.warning(f"Reklam ayarı saxlanmadı ({key}): {e}")


def _int_choice(key: str, choices: tuple, default: int) -> int:
    try:
        v = int(_get(key, str(default)))
    except ValueError:
        return default
    return v if v in choices else default


def settings() -> dict:
    return {
        "text": _get(K_TEXT),
        "delay": _int_choice(K_DELAY, DELAYS, 90),
        "cooldown": _int_choice(K_COOL, COOLDOWNS, 24),
        "max": _int_choice(K_MAX, MAXES, 20),
        "slot": _get(K_SLOT, "all"),
    }


def cycle(key: str, choices: tuple, default: int) -> int:
    cur = _int_choice(key, choices, default)
    nxt = next_of(choices, cur)
    _set(key, nxt)
    return nxt


def cycle_slot(slot_indexes: list) -> str:
    """all → 0 → 1 → ... → all (yalnız mövcud asistan nömrələri)."""
    order = ["all"] + [str(i) for i in slot_indexes]
    cur = _get(K_SLOT, "all")
    nxt = next_of(order, cur, "all")
    _set(K_SLOT, nxt)
    return nxt


# ── mətn ──
def render(text: str | None, bot_username: str) -> str:
    t = text or _get(K_TEXT) or DEFAULT_TEXT
    user = (bot_username or "").lstrip("@")
    return t.replace("{bot}", f"@{user}" if user else "bot").replace("{link}", f"https://t.me/{user}" if user else "")


# ── seçilmiş qruplar ──
def selected() -> set:
    try:
        return {int(x) for x in json.loads(_get(K_GROUPS, "[]") or "[]")}
    except (ValueError, TypeError):
        return set()


def set_selected(ids) -> None:
    _set(K_GROUPS, json.dumps(sorted({int(i) for i in ids})))


def toggle_group(chat_id: int) -> bool:
    cur = selected()
    if int(chat_id) in cur:
        cur.discard(int(chat_id))
        on = False
    else:
        cur.add(int(chat_id))
        on = True
    set_selected(cur)
    return on


# ── tarixçə: bir qrupa tez-tez göndərməmək ──
def last_sent() -> dict:
    try:
        return {int(k): float(v) for k, v in json.loads(_get(K_LAST, "{}") or "{}").items()}
    except (ValueError, TypeError):
        return {}


def mark_sent(chat_id: int, ts: float | None = None) -> None:
    d = last_sent()
    d[int(chat_id)] = ts or time.time()
    cutoff = time.time() - 30 * 86400                                 # 30 gündən köhnə qeydlər atılır
    _set(K_LAST, json.dumps({str(k): v for k, v in d.items() if v > cutoff}))


def on_cooldown(chat_id: int, cooldown_h: int, now: float | None = None) -> bool:
    ts = last_sent().get(int(chat_id))
    return bool(ts) and (now or time.time()) - ts < cooldown_h * 3600


def cooldown_left(chat_id: int, cooldown_h: int, now: float | None = None) -> int:
    ts = last_sent().get(int(chat_id))
    return max(0, int(ts + cooldown_h * 3600 - (now or time.time()))) if ts else 0


# ── fasilə ──
def next_delay(base: int, rnd: float | None = None) -> float:
    """base saniyə ±30% sapma ilə, heç vaxt MIN_DELAY-dən az deyil (robot kimi görünməsin, limit aşılmasın)."""
    r = random.random() if rnd is None else rnd
    return max(float(MIN_DELAY), base * (0.7 + 0.6 * r))


# ── hesabat ──
def save_report(report: dict) -> None:
    _set(K_REPORT, json.dumps(report, ensure_ascii=False))


def last_report() -> dict:
    try:
        return json.loads(_get(K_REPORT, "{}") or "{}")
    except (ValueError, TypeError):
        return {}
