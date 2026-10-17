"""
🔬 Doldurucu ilişəndə daxili vəziyyəti özü yazır — işçilər NƏDƏ gözləyir görünsün.

Problem: işçilərin hamısı "yükləndi, amma depoya düşmədi" mərhələsində ilişəndə nə şəbəkədə, nə bazada, nə loglarda iz
qalır — kənardan baxanda səbəb bilinmir (py-spy / `python -m asyncio ps` icazə tələb edir).

  • avtomatik: hər 30 san. yoxlanır; doldurucu işləyir, növbə var, amma STALL_AFTER saniyədir heç bir mahnı bitməyib →
    bot_logs/stall_<tarix>.txt-ə yazılır (10 dəq.-də bir dəfədən çox yox) + logda qısa xülasə;
  • əl ilə: /depo stacks  (xülasə çatda, tam hesabat fayl kimi).

Hesabat: hər asyncio tapşırığın gözlədiyi nöqtə üzrə qruplaşma (+ nümunə zəncir), thread-lərin üst çərçivələri,
DB hovuzu, default executor növbəsi. Heç nə dəyişmir, yalnız oxuyur.
"""
import asyncio
import collections
import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

STALL_AFTER = 240.0        # san. — bu qədər müddət heç bir mahnı bitməyibsə ilişmiş sayılır
COOLDOWN = 600.0           # san. — avtomatik hesabat bu qədər tez-tez yazılmır
SAMPLES = 4                # qrup başına nümunə zəncir sayı
CHAIN = 9                  # nümunə zəncirdə maksimum çərçivə

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _loc(frame) -> str:
    code = frame.f_code
    path = code.co_filename
    rel = os.path.relpath(path, _ROOT) if path.startswith(_ROOT) and "/.venv/" not in path else os.path.basename(path)
    return f"{rel}:{frame.f_lineno} {code.co_qualname}"


def _mine(frame) -> bool:
    p = frame.f_code.co_filename
    return p.startswith(_ROOT) and "/.venv/" not in p


def task_groups(tasks=None) -> list:
    """[(say, gözləmə nöqtəsi, bizim koddakı sahibi, nümunə zəncir)] — say azalan sırada."""
    groups = collections.OrderedDict()
    try:
        tasks = tasks if tasks is not None else asyncio.all_tasks()
    except RuntimeError:                                     # işləyən loop yoxdur
        return []
    for t in tasks:
        if t.done():
            continue
        try:
            graph = asyncio.capture_call_graph(t)
            frames = [e.frame for e in graph.call_stack] if graph else []
        except Exception:
            frames = []
        if not frames:
            continue
        wait = _loc(frames[0])
        owner = next((_loc(f) for f in frames if _mine(f)), "—")
        key = (wait, owner)
        g = groups.setdefault(key, {"n": 0, "samples": []})
        g["n"] += 1
        if len(g["samples"]) < SAMPLES:
            g["samples"].append(" ← ".join(_loc(f) for f in frames[:CHAIN]))
    return sorted(((g["n"], w, o, g["samples"]) for (w, o), g in groups.items()), key=lambda x: -x[0])


def thread_groups() -> list:
    names = {t.ident: t.name for t in threading.enumerate()}
    groups = collections.Counter()
    for ident, frame in sys._current_frames().items():
        mine = next((f for f in _walk(frame) if _mine(f)), None)
        top = _loc(frame) + (f"  ⟵ {_loc(mine)}" if mine is not None and mine is not frame else "")
        groups[(names.get(ident, "?").split("_")[0], top)] += 1
    return sorted(((n, name, top) for (name, top), n in groups.items()), key=lambda x: -x[0])


def _walk(frame):
    while frame is not None:
        yield frame
        frame = frame.f_back


def build_report(filler=None) -> str:
    out = [f"🔬 Doldurucu hesabatı · {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    if filler is not None:
        try:
            idle = time.time() - filler.last_progress
            out.append(f"işçi: {filler.workers} · işlənən: {len(filler.inflight)} · "
                       f"növbə: {len(filler.queue)} əsas + {len(filler.ytm_queue)} YTM · "
                       f"son mahnı bitəli: {idle:.0f} san. · sayğaclar: {dict(filler.stats)}")
            pool = getattr(filler, "helpers", None)
            if pool is not None:
                out.append(f"köməkçilər: aktiv {len(pool.active())}/{len(pool.helpers)} · rate {pool.rate} · "
                           f"statistika {pool.stats} · kanal fasilələri {pool.channel_cool_left():.0f} san.")
        except Exception as e:
            out.append(f"doldurucu: oxunmadı ({type(e).__name__}: {e})")
    try:
        from common.database import get_db
        out.append(f"DB hovuzu: {get_db().pool_stats()}")
    except Exception as e:
        out.append(f"DB hovuzu: oxunmadı ({e})")
    try:
        ex = getattr(asyncio.get_running_loop(), "_default_executor", None)
        if ex is not None:
            out.append(f"default executor: thread {len(getattr(ex, '_threads', ()))}/{getattr(ex, '_max_workers', '?')} · "
                       f"növbədə {ex._work_queue.qsize()}")
    except Exception as e:
        out.append(f"default executor: oxunmadı ({e})")
    tasks = task_groups()
    out += ["", f"═══ asyncio tapşırıqları: {sum(t[0] for t in tasks)} ═══"]
    for n, wait, owner, samples in tasks:
        out.append(f"{n:5d} × gözləyir: {wait}\n        bizim kod: {owner}")
        out += [f"        ↳ {s}" for s in samples[:2]]
    out += ["", "═══ thread-lər ═══"]
    out += [f"{n:5d} × [{name}] {top}" for n, name, top in thread_groups()[:25]]
    return "\n".join(out)


def summary(report: str, top: int = 6) -> str:
    """Çat / log üçün qısa: ən çox tapşırığın gözlədiyi yerlər."""
    lines = report.splitlines()
    head = [ln for ln in lines[:6] if not ln.startswith("🔬")]
    waits = [ln.strip() for ln in lines if " × gözləyir: " in ln][:top]
    return "\n".join(head + ["", *waits])


def write(report: str) -> str:
    os.makedirs(os.path.join(_ROOT, "bot_logs"), exist_ok=True)
    path = os.path.join(_ROOT, "bot_logs", time.strftime("stall_%Y%m%d.txt"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(report + "\n\n" + "=" * 100 + "\n\n")
    return path


async def watch(filler, every: float = 30.0, stall_after: float = STALL_AFTER, cooldown: float = COOLDOWN):
    """Fon nəzarətçisi: doldurucu ilişəndə hesabatı özü yazır."""
    last = 0.0
    while True:
        await asyncio.sleep(every)
        try:
            busy = filler.running and filler.workers and (filler.queue or filler.ytm_queue or filler.country_queue)
            if not busy or time.time() - filler.last_progress < stall_after or time.time() - last < cooldown:
                continue
            last = time.time()
            report = build_report(filler)
            path = write(report)
            logger.warning(f"🔬 Doldurucu {time.time() - filler.last_progress:.0f} san.-dir heç nə bitirmir — "
                           f"daxili vəziyyət {os.path.relpath(path, _ROOT)} faylına yazıldı\n" + summary(report))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"🔬 stall_watch: {e}")
