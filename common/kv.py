"""
🧰 common/kv.py — ayar (settings) və «dövr» köməkçiləri: əvvəl hər modul özünə `_get/_set/_int_choice/cycle` yazırdı.

  get / set                 mətn ayarı (set(key, None) — silir); bazadakı kimi, xəta olarsa default
  get_bool / set_bool / toggle   "1" / "0" açarları
  get_int(key, choices, default) / cycle_int(key, choices, default)   seçimlər siyahısı ilə tam ədəd
  get_json / set_json       siyahı / lüğət ayarları
  next_of(seq, cur, default) «növbəti element» (sonuncudan sonra birinciyə qayıdır; cur siyahıda yoxdursa default / birinci)

Hamısı common.database.get_db()-ni çağırış anında götürür (testlərdə dəyişdirmək asandır) və heç vaxt istisna atmır.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _db():
    from common.database import get_db
    return get_db()


def next_of(seq, cur, default=None):
    """seq-də cur-dan sonrakı element (dövrü). cur yoxdursa — default (verilməyibsə birinci). Boş seq — default."""
    items = list(seq)
    if not items:
        return default
    if cur in items:
        return items[(items.index(cur) + 1) % len(items)]
    return items[0] if default is None else default


# ── mətn ──
def get(key: str, default: str = "") -> str:
    try:
        v = _db().get_setting(key)
        return default if v is None else str(v)
    except Exception:
        return default


def set(key: str, value) -> None:                                 # noqa: A001 — «set» adı bilərəkdən (kv.set)
    try:
        _db().set_setting(key, None if value is None else str(value))
    except Exception as e:
        logger.warning(f"Ayar saxlanmadı ({key}): {e}")


# ── bool ──
def get_bool(key: str, default: bool = False) -> bool:
    return get(key, "1" if default else "0") == "1"


def set_bool(key: str, value: bool) -> None:
    set(key, "1" if value else "0")


def toggle(key: str, default: bool = False) -> bool:
    """Açarı tərsinə çevirir → yeni dəyər."""
    new = not get_bool(key, default)
    set_bool(key, new)
    return new


# ── tam ədəd (icazə verilən seçimlərlə) ──
def get_int(key: str, choices=None, default: int = 0) -> int:
    try:
        v = int(get(key, str(default)))
    except ValueError:
        return default
    return v if choices is None or v in choices else default


def cycle_int(key: str, choices, default: int) -> int:
    nxt = next_of(choices, get_int(key, choices, default))
    set(key, nxt)
    return nxt


def cycle_str(key: str, choices, default: str) -> str:
    cur = get(key, default)
    nxt = next_of(choices, cur, default)
    set(key, nxt)
    return nxt


# ── JSON ──
def get_json(key: str, default=None):
    try:
        raw = get(key, "")
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def set_json(key: str, value, **kw) -> None:
    set(key, json.dumps(value, ensure_ascii=kw.pop("ensure_ascii", False), **kw))
