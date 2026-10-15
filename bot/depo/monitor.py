"""
📊 bot/depo/monitor.py - Canlı monitor (depo top / /depotop) və proses metrikləri.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from html import escape

from bot.depo.cleaner import _EMOJI_RE
from common.database import get_db
from common.formatting import fmt_rate, sparkline_zero as _spark
from common.menu import wrap_ui_text
from common.utilities import (
    format_duration as _mmss,
    get_tz,
    human_bytes_short as _human,
    vertical_bar as _bar,
)

logger = logging.getLogger(__name__)

TOP_INTERVAL = 1.0
TOP_SLOW_INTERVAL = 3.0
TOP_SLOW_FOR = 600
TOP_MAX_RUNTIME = 600
TOP_SPARK_MIN = 15
TOP_TITLE = 27
TOP_ROWS = 40
PHASE_ORDER = {"UP": 0, "DL": 1, "SLOT": 2, "CHK": 3, "SRCH": 4, "SLP": 5, "WAIT": 6}
EVENT_TAGS = {
    "done": "OK  ", "cached": "SKIP", "failed": "ERR ", "notfound": "404 ", "retry": "RTRY",
    "removed": "DEL ", "stopped": "STOP", "flood": "FLD ",
}
_SPARK = "▁▂▃▄▅▆▇█"


def get_top_interval() -> float:
    try:
        val = float(get_db().get_setting("depo_top:interval", str(TOP_INTERVAL)))
        return max(0.5, min(60.0, val))
    except Exception:
        return TOP_INTERVAL


def set_top_interval(val: float) -> float:
    val = max(0.5, min(60.0, round(val, 1)))
    try:
        get_db().set_setting("depo_top:interval", f"{val:g}")
    except Exception as _e:
        logger.debug("interval setting yazılmadı: %r", _e)
    return val


class ProcMeter:
    """Botun öz prosesi: CPU% (os.times fərqi, uşaq proseslər daxil), RSS, thread sayı, load."""

    def __init__(self):
        self.prev = None

    @staticmethod
    def _rss() -> int:
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except Exception as _e:
            logger.debug("ProcMeter statm oxunmadı: %r", _e)
        try:
            import psutil
            return psutil.Process().memory_info().rss
        except Exception:
            return 0

    def sample(self) -> dict:
        t, now = os.times(), time.monotonic()
        cpu_t = t.user + t.system + t.children_user + t.children_system
        pct = 0.0
        if self.prev:
            pct = max(0.0, (cpu_t - self.prev[0]) / max(now - self.prev[1], 1e-6) * 100)
        self.prev = (cpu_t, now)
        try:
            load = " ".join(f"{x:.2f}" for x in os.getloadavg())
        except (AttributeError, OSError):
            load = "-"
        return {"cpu": pct, "rss": self._rss(), "threads": threading.active_count(), "load": load}


def _short(title: str, width: int) -> str:
    t = _EMOJI_RE.sub("", title or "").replace("\n", " ").strip()
    return t if len(t) <= width else t[:width - 1] + "…"


def _helpers_line(pool) -> list:
    if pool is None or not pool.helpers:
        return []
    st = pool.stats
    fl = pool.flooded()
    return [
        f"Hlp {len(pool.active())}/{len(pool.helpers)} bot  up {st['helper']}  ana {st['fallback']}"
        + (f"  flood {fl}" if fl else "") + f"  max {pool.capacity()}/dq",
        f"Fid {pool.conv_mode}  gozleyir {pool.pending()}  cevrildi {pool.cstats['lazy'] + pool.cstats['warm']}",
    ]


bot_tz = get_tz                                   # köhnə ad (depo handlers / depo_filler_plugin)


def _user_lines(now: float) -> list:
    try:
        from common import activity as act
    except Exception:
        return []
    ub = [0] * TOP_SPARK_MIN
    for ts in reversed(act.done_ts):
        age = now - ts
        if age >= TOP_SPARK_MIN * 60:
            break
        ub[TOP_SPARK_MIN - 1 - int(age // 60)] += 1
    st = act.stats
    return [
        f"Usr {_spark(ub)} {sum(ub[-5:]) / 5:.1f}/dq  akt {len(act.active)}",
        f"    yuk {st['done']}  kes {st['cached']}  err {st['failed']}  dur {st['stopped']}"
        + (f"  fld {st['flood']}" if st.get("flood") else "") + f"  {_human(st['bytes'])}",
    ]


def _user_rows(now: float, width: int) -> list:
    try:
        from common import activity as act
    except Exception:
        return []
    rows = sorted(act.active.values(), key=lambda a: a["start"])
    if not rows:
        return []
    out = ["", f"{'MNB':<3} {'ST':<4} {'VAXT':>6} ISTIFADECI YUKLEMESI"]
    for a in rows[:10]:
        out.append(f"{act.tag(a['src']):<3} {a['phase']:<4} {_mmss(now - a['start']):>6} {_short(a['title'], width)}")
    if len(rows) > 10:
        out.append(f"    … +{len(rows) - 10}")
    return out


def _xfer_lines(filler, now: float) -> list:
    rows = [x for x in getattr(filler, "xfer", ()) if now - x[0] < 300]
    if not rows:
        return []
    dl_b = sum(x[1] for x in rows)
    dl_s = sum(x[2] for x in rows)
    up_b = sum(x[3] for x in rows)
    up_s = sum(x[4] for x in rows)
    last = [x for x in rows if now - x[0] < 60]
    flow_dl = sum(x[1] for x in last) / 60
    flow_up = sum(x[3] for x in last) / 60
    return [
        f"Net dl {fmt_rate(dl_b, dl_s, compact=True)}  up {fmt_rate(up_b, up_s, compact=True)}  (1 fayl)",
        f"    axin dl {fmt_rate(flow_dl, 1, compact=True)}  up {fmt_rate(flow_up, 1, compact=True)}  (cemi/dq)",
    ]


def render_top(filler, sess: dict, proc: dict, now_str: str) -> str:
    now = time.time()
    st = filler.stats
    on = filler.running
    want = filler.workers
    ws = dict(filler.wstate)
    active = sum(1 for w in ws.values() if w["phase"] in ("DL", "CHK", "SRCH", "UP", "SLOT"))
    buckets = [0] * TOP_SPARK_MIN
    for ts in reversed(filler.done_ts):
        age = now - ts
        if age >= TOP_SPARK_MIN * 60:
            break
        buckets[TOP_SPARK_MIN - 1 - int(age // 60)] += 1
    last5 = sum(buckets[-5:]) / 5
    avg = (sum(filler.durations) / len(filler.durations)) if filler.durations else 0
    uptime = _mmss(now - st["started"]) if on and st["started"] else "-"

    cur_int = float(sess.get("interval", get_top_interval()))
    total_q = st.get("queue_built", 0)
    cur_q = len(filler.queue)
    pct_q = ""
    if total_q > 0 and cur_q <= total_q:
        done_pct = max(0.0, min(100.0, (1 - cur_q / total_q) * 100))
        pct_q = f" ({done_pct:.1f}%)"

    lines = [
        f"Wrk [{_bar(active / max(want, 1))}] {active}/{want} aktiv  fas {filler.delay}s",
        f"Que esas {cur_q}" + (f"/{total_q}{pct_q}" if total_q else "")
        + f"  ytm {len(filler.ytm_queue)}" + (f"  rtry {len(filler.retry)}" if filler.retry else ""),
        f"Spd {_spark(buckets)} {last5:.1f}/dq",
        f"Avg {avg:.0f}s/mahni  cemi {_human(filler.bytes)}",
        f"Tot up {st['done']}  skip {st['cached']}  err {st['failed']}",
        *_xfer_lines(filler, now),
        *_user_lines(now),
        f"Bot cpu {proc['cpu']:.0f}%  ram {_human(proc['rss'])}  thr {proc['threads']}",
        *_helpers_line(getattr(filler, "helpers", None)),
        f"Up  {uptime}  load {proc['load']}  int {cur_int:g}s",
        "",
    ]
    if sess["view"] == "log":
        lines.append(f"{'VAXT':<8} {'#':>3} {'NOV':<4} {'SAN':>4} {'OLCU':>5} MAHNI")
        evs = [(ts, f"{idx + 1:>3}", kind, title, secs, size) for ts, idx, kind, title, secs, size
               in list(filler.events)[-40:]]
        try:
            from common import activity as act
            evs += [(ts, f"{act.tag(src):>3}", kind, title, secs, size) for ts, src, kind, title, secs, size, _u
                    in list(act.events)[-40:]]
        except Exception as _e:
            logger.debug("activity oxunmadı: %r", _e)
        evs = sorted(evs, key=lambda e: e[0], reverse=True)[:24]
        for ts, who, kind, title, secs, size in evs:
            t = datetime.fromtimestamp(ts, bot_tz()).strftime("%H:%M:%S")
            lines.append(f"{t:<8} {who} {EVENT_TAGS.get(kind, kind[:4]):<4} {secs:>4.0f} "
                         f"{(_human(size) if size else '-'):>5} {_short(title, 21)}")
        if not evs:
            lines.append("  (hələ hadisə yoxdur)")
    else:
        rows = []
        for i in range(max(want, max(ws) + 1 if ws else 0)):
            w = ws.get(i)
            if w is None:
                rows.append((i, "-", 0.0, "(başlayır)" if on and i < want else "(dayanıb)"))
                continue
            ph = w["phase"] or "WAIT"
            title = w["title"] or {"SLP": "(fasilə)", "WAIT": "(növbə gözləyir)"}.get(ph, "")
            rows.append((i, ph, now - w["since"], title))
        if sess["sort"] == "time":
            rows.sort(key=lambda r: (PHASE_ORDER.get(r[1], 9), -r[2]))
        if len(rows) > 16:
            cnt = {}
            for r in rows:
                cnt[r[1]] = cnt.get(r[1], 0) + 1
            lines.insert(-1, "Faz " + "  ".join(f"{k} {cnt[k]}" for k in sorted(cnt, key=lambda k: PHASE_ORDER.get(k, 9))))
        pages = max(1, -(-len(rows) // TOP_ROWS))
        sess["pages"] = pages
        pg = sess["page"] = max(0, min(sess.get("page", 0), pages - 1))
        page_rows = rows[pg * TOP_ROWS:(pg + 1) * TOP_ROWS]
        w = 3 if len(rows) > 99 else 2
        title_w = TOP_TITLE - (w - 2)
        if pages > 1:
            lines.append(f"Sehife {pg + 1}/{pages} · {len(rows)} isci")
        lines.append(f"{'#':>{w}} {'ST':<4} {'VAXT':>6} MAHNI")
        for i, ph, el, title in page_rows:
            lines.append(f"{i + 1:>{w}} {ph:<4} {_mmss(el) if ph != '-' else '':>6} {_short(title, title_w)}")
        lines += _user_rows(now, TOP_TITLE - 1)

    if sess["paused"]:
        state = "⏸ fasilə"
    else:
        left = int(sess["until"] - now)
        slow = now < sess.get("slow_until", 0)
        eff_int = max(cur_int, TOP_SLOW_INTERVAL) if slow else cur_int
        state = f"🔄 hər {eff_int:g} san." + (" 🐢" if slow else "") \
            + f" · {left} san. qalıb"
    head = (f"📦 <b>depo top</b> · {'🟢 işləyir' if on else '🔴 söndürülüb'} · "
            f"⬆️ <b>{last5:.1f}</b>/dəq\n<i>{now_str} · {state}</i>")
    if st["note"]:
        head += f"\nℹ️ <i>{escape(st['note'][:120])}</i>"
    legend = ("<i>DL yükləyir · UP depoya göndərir · SLOT köməkçi bot gözləyir · SRCH axtarır · "
              "CHK depo yoxlanır · SLP fasilə · WAIT növbə</i>"
              if sess["view"] != "log" else "<i>OK yükləndi · SKIP artıq depoda · RTRY sonra təkrar · ERR ❌ siyahısına · 404 tapılmadı · "
              "DEL depodan silinib · STOP istifadəçi dayandırdı · FLD Telegram flood gözləməsi</i>")
    legend += ("\n<i>📡 # — doldurucu işçisi · MSG şəxsi çat · GRP qrup · INL inline · MIX · "
               "BAT toplu · PL playlist · ART /artist · LNK link · Usr — istifadəçi yükləmələri · "
               "Net — sürət: dl YouTube→server, up server→depo; (1 fayl) orta, axın — bütün işçilər birlikdə</i>")
    return f"{head}\n{wrap_ui_text(escape(chr(10).join(lines)))}\n{legend}"
