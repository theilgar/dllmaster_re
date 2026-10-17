"""🔬 bot/core/stall_watch.py — doldurucu ilişəndə işçilərin NƏDƏ gözlədiyini yazır."""
import asyncio
import os
import threading
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import stall_watch as S


def run(coro):
    return asyncio.run(coro)


async def _park(lock):
    async with lock:
        pass


async def _sleeper():
    await asyncio.sleep(100)


def test_task_groups_collect_what_each_task_awaits_and_our_owner_frame():
    async def main():
        lock = asyncio.Lock()
        await lock.acquire()
        tasks = [asyncio.create_task(_park(lock)) for _ in range(5)] + [asyncio.create_task(_sleeper()) for _ in range(2)]
        await asyncio.sleep(0.05)
        groups = S.task_groups(tasks)
        for t in tasks:
            t.cancel()
        return groups
    groups = run(main())
    by_wait = {w: (n, o, s) for n, w, o, s in groups}
    lock_wait = next(k for k in by_wait if "Lock.acquire" in k)
    assert by_wait[lock_wait][0] == 5 and "_park" in by_wait[lock_wait][1] and "test_stall_watch.py" in by_wait[lock_wait][1]
    sleep_wait = next(k for k in by_wait if "sleep" in k)
    assert by_wait[sleep_wait][0] == 2
    assert groups[0][0] == 5                                           # çoxdan aza
    assert all(len(s) <= S.SAMPLES for _, _, _, s in groups)


def test_thread_groups_show_blocked_threads():
    ev = threading.Event()
    th = [threading.Thread(target=ev.wait, name=f"dlm-thread_{i}", daemon=True) for i in range(3)]
    for t in th:
        t.start()
    time.sleep(0.05)
    try:
        groups = S.thread_groups()
    finally:
        ev.set()
    assert any(name == "dlm-thread" and n >= 3 and "wait" in top for n, name, top in groups)


def fake_filler(idle=0.0, queue=3):
    pool = types.SimpleNamespace(active=lambda: [1, 2], helpers=[1, 2, 3], rate=20, stats={"helper": 5},
                                 channel_cool_left=lambda: 0.0)
    return types.SimpleNamespace(running=True, workers=128, inflight={1: 1, 2: 2}, queue=[], ytm_queue=[0] * queue,
                                 country_queue=[], stats={"done": 7}, last_progress=time.time() - idle, helpers=pool)


def test_report_has_every_section_and_a_short_summary():
    async def main():
        t = asyncio.create_task(_sleeper())
        await asyncio.sleep(0.02)
        rep = S.build_report(fake_filler(idle=300))
        t.cancel()
        return rep
    rep = run(main())
    for part in ("Doldurucu hesabatı", "işçi: 128", "son mahnı bitəli: 300", "köməkçilər: aktiv 2/3", "DB hovuzu",
                 "asyncio tapşırıqları", "thread-lər", "gözləyir:"):
        assert part in rep
    short = S.summary(rep)
    assert "gözləyir:" in short and len(short) < len(rep)


def test_report_survives_a_broken_filler():
    async def main():
        return S.build_report(MagicMock())
    assert "Doldurucu hesabatı" in run(main())
    assert "Doldurucu hesabatı" in S.build_report(None)                    # loop-suz çağırış da yıxılmır


def test_write_appends_to_a_dated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_ROOT", str(tmp_path))
    p1 = S.write("birinci")
    p2 = S.write("ikinci")
    assert p1 == p2 and os.path.basename(p1).startswith("stall_")
    text = open(p1, encoding="utf-8").read()
    assert "birinci" in text and "ikinci" in text


def test_watchdog_writes_once_when_stalled_and_stays_quiet_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_ROOT", str(tmp_path))
    written = []
    monkeypatch.setattr(S, "write", lambda rep: written.append(rep) or "x")

    async def go(filler, n=4):
        task = asyncio.create_task(S.watch(filler, every=0.01, stall_after=0.05, cooldown=60))
        await asyncio.sleep(0.01 * n + 0.3)
        task.cancel()
    run(go(fake_filler(idle=0.0)))
    assert written == [] or len(written) == 1                             # idle=0 → bir müddət sonra stall sayıla bilər
    written.clear()
    run(go(fake_filler(idle=500)))
    assert len(written) == 1                                              # cooldown: təkrar yazmır
    written.clear()
    stopped = fake_filler(idle=500)
    stopped.running = False
    run(go(stopped))
    assert written == []                                                  # dayanıb → ilişmə sayılmır
    empty = fake_filler(idle=500, queue=0)
    run(go(empty))
    assert written == []                                                  # növbə boşdur → ilişmə deyil
