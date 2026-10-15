"""
📡 İstifadəçi yükləmələrinin canlı izlənməsi — common/activity.py

music_plugin hər mahnı üçün (axtarış seçimi, qrup, inline, toplu yükləmə, /artist, playlist, linklər)
burada iz qoyur; depo_filler_plugin-in 📊 Canlı monitoru (/depotop) bunu doldurucu işçiləri ilə birlikdə
göstərir. Doldurucunun öz yükləmələri burada izlənmir (onun ayrıca işçi cədvəli var).

  with activity.using("msg", uid):        # çağıran: kim, haradan
      res = await fetch_audio(...)        # fetch_audio içində: begin → phase("DL"/"UP") → end
  activity.instant("inl", uid, title, "cached")   # bir anlıq hadisə (məs. inline depo kartı)
"""
import contextlib
import contextvars
import itertools
import time
from collections import deque

# mənbə qısaltmaları (monitorda 3 simvol)
SRC = {"msg": "MSG", "grp": "GRP", "inl": "INL", "bat": "BAT", "pl": "PL ", "art": "ART", "lnk": "LNK",
       "mix": "MIX"}

_CTX = contextvars.ContextVar("activity_ctx", default=None)       # (src, uid)
_TOK = contextvars.ContextVar("activity_tok", default=None)       # hazırkı iş
_STAGE = contextvars.ContextVar("activity_stage", default=None)   # toplu yükləmənin bu mahnı üçün dict-i

active = {}                              # token → {"src", "uid", "title", "phase", "since", "start"}
events = deque(maxlen=150)               # (ts, src, kind, title, secs, size, uid)
done_ts = deque(maxlen=20000)            # göndərilən hər mahnının vaxtı (sürət qrafiki)
stats = {"done": 0, "cached": 0, "failed": 0, "stopped": 0, "flood": 0, "bytes": 0}
_ids = itertools.count(1)


@contextlib.contextmanager
def using(src: str, uid: int):
    tok = _CTX.set((src, uid))
    try:
        yield
    finally:
        _CTX.reset(tok)


def current_src():
    return _CTX.get()


def begin(title: str):
    """Kontekst varsa yeni iş açır → token (yoxdursa None — məs. doldurucu)."""
    ctx = _CTX.get()
    if not ctx:
        return None
    t = next(_ids)
    now = time.time()
    active[t] = {"src": ctx[0], "uid": ctx[1], "title": title or "", "phase": "CHK", "since": now, "start": now}
    _TOK.set(t)
    return t


def set_stage(d: dict):
    """Toplu yükləmə (music_plugin run_batch) bu task üçün mərhələ dict-ini verir — phase()/flood() onu yeniləyir."""
    return _STAGE.set(d)


def stage(ph: str, **kw):
    d = _STAGE.get()
    if d is not None:
        if d.get("phase") != ph:
            d["phase"], d["since"] = ph, time.time()
        d.update(kw)


def flood(wait: float):
    """Telegram flood limiti — göndərmə `wait` san. gözləyir (monitor jurnalında FLD, toplu statusda ⏳)."""
    stage("FLOOD", until=time.time() + wait)
    d = _STAGE.get()
    if d is not None:
        d["flood_n"] = d.get("flood_n", 0) + 1
        d["flood_s"] = d.get("flood_s", 0) + wait
    ctx = _CTX.get()
    if ctx:
        _record(ctx[0], "flood", f"Telegram flood — {wait:.0f} san.", wait, 0, ctx[1])


def phase(ph: str, title: str = None):
    stage(ph)
    t = _TOK.get()
    a = active.get(t) if t else None
    if a:
        if title:
            a["title"] = title
        if a["phase"] != ph:
            a["phase"], a["since"] = ph, time.time()


def end(token, kind: str, size: int = 0, title: str = None):
    a = active.pop(token, None) if token else None
    if not a:
        return
    _record(a["src"], kind, title or a["title"], time.time() - a["start"], size, a["uid"])


def instant(src: str, uid: int, title: str, kind: str = "cached"):
    _record(src, kind, title, 0.0, 0, uid)


def _record(src, kind, title, secs, size, uid):
    now = time.time()
    events.append((now, src, kind, title, secs, size, uid))
    if kind in stats:
        stats[kind] += 1
    if kind in ("done", "cached"):
        done_ts.append(now)
    if size:
        stats["bytes"] += size


def tag(src: str) -> str:
    return SRC.get(src, (src or "?")[:3].upper())
