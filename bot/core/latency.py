"""
⏱ Düymə / komanda gecikməsi — bot/core/latency.py

Hər callback (düymə) və mesaj handler-inin işləmə müddətini ölçür (outer middleware: handler-lərə toxunmur).
Ölçü — handler-in bitməsinə qədər keçən vaxt; istifadəçinin gördüyü "ilişmə" əsasən budur.
Açar: callback üçün data-nın ilk iki hissəsi (`menu:yt`), mesaj üçün komanda (`/music`) və ya `text`.
Uzun işləməsi normal olan açarlar (yükləmə və s.) statistikaya düşür, amma yavaş sayılıb loglanmır.
/menu → ⚙️ Performans bu statistikanı göstərir.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)

SLOW_SECONDS = 3.0                      # bundan uzun çəkən (uzun-iş açarlarından başqa) WARNING ilə loglanır
KEEP_PER_KEY = 200
MAX_KEYS = 400
LONG_OK = ("choice_", "dlstop", "df:", "va:", "mix", "bulk", "pl:", "dl",       # yükləmə / uzun əməliyyat önəkləri
           "/play", "/p", "/oynat", "/mixplay", "/mp", "/radio", "/tt", "/tiktok", "/shazam", "/vctest",
           "ae:", "shz:", "tt:", "vj:", "vr:")     # handler işin bitməsini gözləyir (yükləmə, yayıma qoşulma, tanıma)

_stats: dict[str, deque] = {}
_inflight: dict = {}          # id -> (açar, başlama vaxtı) — hazırda işləyən handler-lər (donma hesabatı üçün)


def inflight() -> list:
    """[(açar, neçə saniyədir işləyir)] — ən uzun işləyən əvvəl."""
    now = time.monotonic()
    return sorted(((k, now - t0) for k, t0 in list(_inflight.values())), key=lambda x: -x[1])


def key_of(event) -> str | None:
    data = getattr(event, "data", None)
    if isinstance(data, str):                                    # CallbackQuery
        return "🖱 " + ":".join(data.split(":")[:2])[:40]
    text = getattr(event, "text", None) or getattr(event, "caption", None)
    if isinstance(text, str) and text:                           # Message
        if text.startswith(("/", ".")):
            return "💬 " + text.split()[0].split("@")[0][:30]
        return "💬 text"
    return None


def record(key: str, seconds: float):
    q = _stats.get(key)
    if q is None:
        if len(_stats) >= MAX_KEYS:
            _stats.pop(next(iter(_stats)), None)
        q = _stats[key] = deque(maxlen=KEEP_PER_KEY)
    q.append((time.time(), seconds))


def _is_long_ok(key: str) -> bool:
    name = key.split(" ", 1)[-1]
    return name.startswith(LONG_OK)


class LatencyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        key = key_of(event)
        t = time.monotonic()
        tok = object()
        if key:
            _inflight[id(tok)] = (key, t)
        try:
            return await handler(event, data)
        finally:
            _inflight.pop(id(tok), None)
            if key:
                dur = time.monotonic() - t
                record(key, dur)
                if dur >= SLOW_SECONDS and not _is_long_ok(key):
                    logger.warning(f"⏱ Yavaş handler: {key} — {dur:.1f} san.")


def _pct(vals: list, p: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p))] if vals else 0.0


def snapshot(window: int = 3600, top: int = 3) -> dict:
    """{'count','avg','p95','max','slow': [(açar, say, orta, maks)] (ən yavaş orta üzrə, uzun-iş açarları xaric)}"""
    now = time.time()
    all_vals, per = [], []
    for key, q in _stats.items():
        vals = [d for ts, d in q if now - ts <= window]
        if not vals:
            continue
        if not _is_long_ok(key):
            all_vals += vals
            per.append((key, len(vals), sum(vals) / len(vals), max(vals)))
    per.sort(key=lambda r: r[2], reverse=True)
    return {
        "count": len(all_vals),
        "avg": sum(all_vals) / len(all_vals) if all_vals else 0.0,
        "p95": _pct(all_vals, 0.95),
        "max": max(all_vals, default=0.0),
        "slow": per[:top],
    }


def detail(window: int = 3600) -> list:
    """Bütün açarlar (uzun-iş olanlar da): [(açar, say, orta, p95, maks, son_vaxt, uzun_işdir)] — orta üzrə azalan."""
    now = time.time()
    out = []
    for key, q in _stats.items():
        vals = [(ts, d) for ts, d in q if now - ts <= window]
        if not vals:
            continue
        ds = [d for _, d in vals]
        out.append((key, len(ds), sum(ds) / len(ds), _pct(ds, 0.95), max(ds), now - vals[-1][0], _is_long_ok(key)))
    out.sort(key=lambda r: r[2], reverse=True)
    return out


def reset() -> int:
    n = sum(len(q) for q in _stats.values())
    _stats.clear()
    return n


def summary_lines() -> list:
    s = snapshot()
    if not s["count"]:
        return ["⏱ <b>Düymə/komanda cavabı:</b> hələ ölçü yoxdur"]
    icon = "🟢" if s["p95"] < 1 else ("🟡" if s["p95"] < SLOW_SECONDS else "🔴")
    lines = [f"⏱ <b>Düymə/komanda cavabı (1 saat):</b> {icon} orta <b>{s['avg'] * 1000:.0f} ms</b> · "
             f"p95 <b>{s['p95'] * 1000:.0f} ms</b> · maks <b>{s['max']:.1f} san.</b> · {s['count']} sorğu"]
    for key, n, avg, mx in s["slow"]:
        if avg >= 0.3:
            lines.append(f"     🐢 <code>{key}</code>: orta {avg * 1000:.0f} ms, maks {mx:.1f} san. ({n}×)")
    return lines


def install(context):
    """Hər handler-dən əvvəl/sonra vaxtı ölçən outer middleware (callback + mesaj)."""
    mw = LatencyMiddleware()
    context.dp.callback_query.outer_middleware(mw)
    context.dp.message.outer_middleware(mw)
