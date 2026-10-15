"""
⚙️ Performans — bot/core/cpu_pool.py

Məqsəd: bot heç vaxt donmasın və bütün CPU nüvələrindən istifadə etsin.

1. 🧠 Proses hovuzları — yt-dlp (yükləmə, axtarış, playlist, Mix) və YT Music axtarışı ayrıca Python
   proseslərində (bot/core/ytdl_worker.py) işləyir: hər proses öz nüvəsində, öz GIL-i ilə. Əsas proses yalnız
   Telegram ilə danışır → GIL-i yt-dlp tutmur, bot donmur.
     👤 İstifadəçi zolağı — axtarış + istifadəçilərin yükləmələri (həmişə ayrıca, depo onları gözlətmir)
     📦 Depo zolağı      — 📦 doldurucunun yükləmələri (UPLOAD_CTX ilə tanınır)
   Proseslər tənbəl yaranır, N tapşırıqdan sonra yenilənir (yaddaş sızmasın), çökəni avtomatik dəyişilir.
   Nəsə alınmasa tapşırıq thread-də işləyir — heç nə itmir.
2. 🧵 Thread hovuzu — asyncio.to_thread (baza, fayl, Spotify) üçün ölçü.
3. 🫀 Watchdog — event loop-un gecikməsini ölçür; loop 1 san.-dən çox donanda əsas thread-in stack-ini tutub
   harada ilişdiyini yazır (/menu → ⚙️ Performans).
4. 🐢 Prioritet — işçi proseslər və ffmpeg `nice` ilə işləyir, bot həmişə üstündür.
5. 🚀 Avto-miqyas — hər 3 san. CPU / RAM ölçülür (SysMon). Növbədə tapşırıq varsa və CPU hədəfdən
   aşağıdırsa zolaq böyüdülür (maks. 👤/📦 dəyərinə qədər), CPU hədəfi aşanda və ya RAM ehtiyatı
   azalanda kiçildilir (əvvəl 📦 depo). 2 dəq. boş qalan artıq proseslər bağlanır (RAM qayıdır).
   👤 / 📦 düymələri avto-miqyas açıqkən MAKSİMUM rolunu oynayır.

Ayarlar bazada (perf:*) — /menu → 🖥 Sistem → ⚙️ Performans.
"""
import asyncio
import json
import itertools
import logging
import os
import pickle
import shutil
import struct
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor


class ManagedThreadPoolExecutor(ThreadPoolExecutor):
    """to_thread / run_in_executor üçün idarə olunan hovuz:
    - Shutdown zamanı ilişmiş işçiləri sonsuz gözləmir, ləğv edir (wait=False, cancel_futures=True).
    - Canlı statistika (aktiv işçilər, növbədəki tapşırıqlar, tamamlananlar, xətalar).
    - Dinamik və hamar ölçüləndirmə (set_max_workers).
    """

    def __init__(self, max_workers=None, thread_name_prefix="dlm-thread", initializer=None, initargs=()):
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix,
                         initializer=initializer, initargs=initargs)
        self._active_tasks = 0
        self._completed_tasks = 0
        self._failed_tasks = 0
        self._submitted_tasks = 0
        self._stats_lock = threading.Lock()

    def submit(self, fn, /, *args, **kwargs):
        with self._stats_lock:
            self._submitted_tasks += 1

        def _runner():
            with self._stats_lock:
                self._active_tasks += 1
            try:
                res = fn(*args, **kwargs)
                with self._stats_lock:
                    self._completed_tasks += 1
                return res
            except Exception:
                with self._stats_lock:
                    self._failed_tasks += 1
                raise
            finally:
                with self._stats_lock:
                    self._active_tasks -= 1

        return super().submit(_runner)

    def shutdown(self, wait=True, *, cancel_futures=False):
        super().shutdown(wait=False, cancel_futures=True)

    def set_max_workers(self, new_max: int):
        """Hovuzu yenidən yaratmadan dinamik şəkildə işçi sayını artırır/dəyişir."""
        lock = getattr(self, "_shutdown_lock", None)
        if lock is not None:
            with lock:
                return self._apply_new_max(new_max)
        return self._apply_new_max(new_max)

    def _apply_new_max(self, new_max: int):
        if getattr(self, "_shutdown", False):
            return self._max_workers
        old = self._max_workers
        self._max_workers = new_max
        if hasattr(self, "_work_queue") and hasattr(self, "_adjust_thread_count"):
            try:
                while self._work_queue.qsize() > 0 and len(self._threads) < self._max_workers:
                    self._adjust_thread_count()
            except Exception as _e:
                logger.debug("udulan xəta [bot/core/cpu_pool.py:98]: %r", _e)
        return old

    def stats(self) -> dict:
        with self._stats_lock:
            queued = max(0, self._work_queue.qsize()) if hasattr(self, "_work_queue") else 0
            threads_count = len(self._threads) if hasattr(self, "_threads") else 0
            return {
                "max": self._max_workers,
                "threads": threads_count,
                "active": self._active_tasks,
                "idle": max(0, threads_count - self._active_tasks),
                "queued": queued,
                "submitted": self._submitted_tasks,
                "done": self._completed_tasks,
                "failed": self._failed_tasks,
            }


_SafeNoWaitExecutor = ManagedThreadPoolExecutor


from common.database import Settings
from common.diagnostics import cpu_percent, cpu_times
from common.utilities import spawn

logger = logging.getLogger(__name__)

CORES = os.cpu_count() or 1
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # layihə kökü (bot/core/ → kök)
CANCEL_DIR = os.path.join(tempfile.gettempdir(), "dllmaster_cancel")

MODES = {"process": "🧠 Proses (bütün nüvələr)", "thread": "🧵 Thread (tək nüvə)"}
S_MODE, S_USER, S_DEPO, S_THREADS, S_NICE, S_WATCH = (
    "perf:mode", "perf:user_procs", "perf:depo_procs", "perf:threads", "perf:nice", "perf:watchdog")
S_THREADS_AUTO, S_NICE_AUTO = "perf:threads_auto", "perf:nice_auto"     # 🤖 Avto: dəyəri sistem seçir (defolt — Manual)
USER_STEPS = [1, 2, 3, 4, 6, 8, 12, 16]
DEPO_STEPS = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
THREAD_STEPS = [16, 32, 48, 64, 96, 128, 192, 256, 384, 512]
NICE_STEPS = [0, 2, 5, 10, 15, 19]
RECYCLE_AFTER = 40              # proses bu qədər tapşırıqdan sonra yenilənir
EXTRACT_TIMEOUT = 180           # axtarış / playlist (san.)
DOWNLOAD_TIMEOUT = 1200         # bir yükləmə (san.)
CANCEL_KILL_AFTER = 15          # ⏹ basılıb, proses bu qədər vaxtda dayanmasa — öldürülür
FREEZE_AT = 1.0                 # watchdog: loop bu qədər cavab verməsə — donma sayılır (san.)

S_SCALE, S_TARGET, S_RESERVE = "perf:autoscale", "perf:cpu_target", "perf:ram_reserve"
TARGET_STEPS = [50, 60, 70, 80, 85, 90, 95, 100]
RESERVE_STEPS = [256, 512, 768, 1024, 1536, 2048, 4096, 8192]
SCALE_EVERY = 3.0               # ölçmə / miqyas intervalı (san.)
IDLE_KEEP = 120                 # bu qədər boş qalan artıq proses bağlanır (san.)
PROC_MB = 75                    # bir işçi prosesin təxmini RAM-ı
LAG_LIMIT = 0.25                # avto-miqyas: orta loop gecikməsi bundan çoxdursa böyütmə yox, kiçiltmə


# ───────────────────────── ayarlar ─────────────────────────
def _db():
    from common.database import get_db
    return get_db()


_S = Settings(empty_is_default=True)
_get = _S.get


_set = _S.put


def _int(key, default, lo, hi):
    return _S.int(key, default, lo=lo, hi=hi)


_RAM_TOTAL_MB = []


def _total_ram_mb() -> int:
    if not _RAM_TOTAL_MB:
        mb = 0
        try:
            for line in _read("/proc/meminfo").splitlines():
                if line.startswith("MemTotal:"):
                    mb = int(line.split()[1]) // 1024
                    break
        except Exception as e:
            logger.debug(f"MemTotal oxunmadı: {e!r}")
        _RAM_TOTAL_MB.append(mb)
    return _RAM_TOTAL_MB[0]


def default_mode() -> str:
    """🤖 Avto rejim: tək nüvə və ya az RAM (<1 GB) — thread; qalanında proses."""
    ram = _total_ram_mb()
    return "process" if CORES > 1 and (not ram or ram >= 1024) else "thread"


def mode_auto() -> bool:
    return not _get(S_MODE)


def mode() -> str:
    m = _get(S_MODE, default_mode())
    return m if m in MODES else "process"


def set_mode_auto():
    """Əl ilə seçimi sil — rejim serverə görə seçilir (default_mode)."""
    _db().set_setting(S_MODE, None)
    if mode() == "thread":
        spawn(shutdown_pools())


def user_procs() -> int:
    return _int(S_USER, max(2, min(4, CORES)), 1, USER_STEPS[-1])


def depo_procs() -> int:
    return _int(S_DEPO, max(2, CORES * 2), 1, DEPO_STEPS[-1])


def base_threads() -> int:
    return max(THREAD_STEPS[0], min(THREAD_STEPS[-1], max(64, CORES * 8)))


def threads_auto() -> bool:
    return _get(S_THREADS_AUTO, "off") == "on" and not turbo_on()      # 🔥 turbo öz dəyərlərini qoyur


def nice_auto() -> bool:
    return _get(S_NICE_AUTO, "off") == "on" and not turbo_on()


# 🤖 Avto dəyərlər yaddaşdadır (yadda saxlanmır): restartdan sonra defoltdan başlayıb yenidən öyrənir
_AUTO = {"threads": None, "nice": None, "idle": 0, "calm": 0}
AUTO_NICE_BASE, AUTO_NICE_MAX = 5, 15


def threads() -> int:
    if threads_auto():
        return _AUTO["threads"] or base_threads()
    return _int(S_THREADS, max(64, CORES * 8), THREAD_STEPS[0], THREAD_STEPS[-1])


def nice() -> int:
    if nice_auto():
        return _AUTO["nice"] if _AUTO["nice"] is not None else AUTO_NICE_BASE
    return _int(S_NICE, 5, 0, 19)


def set_threads_auto(on: bool):
    _set(S_THREADS_AUTO, "on" if on else "off")
    if on:
        _AUTO["threads"], _AUTO["idle"] = None, 0
    else:
        _AUTO["threads"] = None
    apply_threads(force=True)


def set_nice_auto(on: bool):
    _set(S_NICE_AUTO, "on" if on else "off")
    _AUTO["nice"], _AUTO["calm"] = None, 0


def watchdog_on() -> bool:
    return _get(S_WATCH, "on") != "off"


def _step(cur, steps, direction):
    if direction > 0:
        return next((x for x in steps if x > cur), steps[-1])
    return next((x for x in reversed(steps) if x < cur), steps[0])


def set_mode(m: str):
    _set(S_MODE, m)
    if m == "thread":
        spawn(shutdown_pools())


def step_user(d):
    v = _step(user_procs(), USER_STEPS, d)
    _set(S_USER, v)
    LANES["user"].set_max(v)
    return v


def set_user_procs(v: int) -> int:
    val = max(1, min(USER_STEPS[-1], int(v)))
    _set(S_USER, val)
    LANES["user"].set_max(val)
    return val


def step_user_delta(delta: int) -> int:
    cur = user_procs()
    return set_user_procs(cur + delta)


def step_depo(d):
    v = _step(depo_procs(), DEPO_STEPS, d)
    _set(S_DEPO, v)
    LANES["depo"].set_max(v)
    return v


def set_depo_procs(v: int) -> int:
    val = max(1, min(DEPO_STEPS[-1], int(v)))
    _set(S_DEPO, val)
    LANES["depo"].set_max(val)
    return val


def step_depo_delta(delta: int) -> int:
    cur = depo_procs()
    return set_depo_procs(cur + delta)


def step_threads(d):
    v = _step(threads(), THREAD_STEPS, d)
    _set(S_THREADS, v)
    apply_threads(force=True)
    return v


def set_threads(v: int) -> int:
    val = max(THREAD_STEPS[0], min(THREAD_STEPS[-1], int(v)))
    _set(S_THREADS, val)
    apply_threads(force=True)
    return val


def step_threads_delta(delta: int) -> int:
    cur = threads()
    return set_threads(cur + delta)


def step_nice(d):
    v = _step(nice(), NICE_STEPS, d)
    _set(S_NICE, v)
    return v                    # yeni proseslərə tətbiq olunur (♻️ ilə hamısına)


def set_nice(v: int) -> int:
    val = max(0, min(19, int(v)))
    _set(S_NICE, val)
    return val


def step_nice_delta(delta: int) -> int:
    cur = nice()
    return set_nice(cur + delta)


def autoscale_on() -> bool:
    return _get(S_SCALE, "on") != "off"


def cpu_target() -> int:
    return _int(S_TARGET, 90, 10, 100)


def set_cpu_target(v: int) -> int:
    val = max(10, min(100, int(v)))
    _set(S_TARGET, val)
    return val


def step_target_delta(delta: int) -> int:
    cur = cpu_target()
    return set_cpu_target(cur + delta)


def ram_reserve() -> int:
    return _int(S_RESERVE, 512, 0, 1 << 20)


def set_ram_reserve(v: int) -> int:
    val = max(0, min(32768, int(v)))
    _set(S_RESERVE, val)
    return val


def step_reserve_delta(delta: int) -> int:
    cur = ram_reserve()
    return set_ram_reserve(cur + delta)


def toggle_autoscale() -> bool:
    on = not autoscale_on()
    _set(S_SCALE, "on" if on else "off")
    for pool in LANES.values():
        pool.set_max(pool.max_size)          # rejimə görə effektiv ölçünü yenidən qurur
    return on


def step_target(d):
    v = _step(cpu_target(), TARGET_STEPS, d)
    _set(S_TARGET, v)
    return v


def step_reserve(d):
    v = _step(ram_reserve(), RESERVE_STEPS, d)
    _set(S_RESERVE, v)
    return v


def toggle_watchdog() -> bool:
    on = not watchdog_on()
    _set(S_WATCH, "on" if on else "off")
    WATCHDOG.enabled = on
    return on


# ───────────────────────── 🔥 Tam performans ─────────────────────────
# Bir toxunuşla hər şey maksimuma (serverin nüvə və RAM-ına görə); əvvəlki ayarlar yadda saxlanır və
# söndürəndə olduğu kimi qaytarılır.
S_TURBO, S_TURBO_PREV = "perf:turbo", "perf:turbo_prev"
S_BATCH_PAR = "dl:batch_parallel"            # bot/core/youtube_handler.py — toplu yükləmədə paralel
TURBO_KEYS = (S_MODE, S_USER, S_DEPO, S_THREADS, S_NICE, S_SCALE, S_TARGET, S_RESERVE, S_BATCH_PAR,
              S_THREADS_AUTO, S_NICE_AUTO)
# 🚀 Sürətləndirmə ayarları (Menyu → Performans): turbo hamısını açır, Avto-nu söndürür; söndürəndə qaytarılır
TURBO_ACCEL = ("opt:accel_auto", "opt:l1_cache", "opt:ram_disk", "opt:smart_copy", "opt:parallel_dl")


def turbo_on() -> bool:
    return _get(S_TURBO) == "on"


def turbo_plan(avail_mb: int = 0) -> dict:
    """Serverə görə maksimum: bütün nüvələr, RAM-ın ~60%-i işçi proseslərə, nice 0, CPU hədəfi 100%."""
    avail_mb = avail_mb or MON.avail_mb or 0
    ram_cap = int(avail_mb * 0.6 / PROC_MB) if avail_mb else CORES * 4
    user = min(USER_STEPS[-1], max(2, CORES))
    depo = max(1, min(DEPO_STEPS[-1], CORES * 4, max(1, ram_cap - user)))
    depo = max(x for x in DEPO_STEPS if x <= depo)               # pilləyə yuvarlaqlaşdır
    thr = min(256, max(128, CORES * 16))          # çox thread = GIL rəqabəti → event loop ləngiyir (donma)
    thr = max(x for x in THREAD_STEPS if x <= thr)
    return {S_MODE: "process" if CORES > 1 else "thread", S_USER: user, S_DEPO: depo, S_THREADS: thr,
            S_NICE: 0, S_THREADS_AUTO: "off", S_NICE_AUTO: "off", S_SCALE: "on", S_TARGET: 100, S_RESERVE: 256, S_BATCH_PAR: min(10, max(4, CORES // 2))}


def _apply_live():
    LANES["user"].set_max(user_procs())
    LANES["depo"].set_max(depo_procs())
    apply_threads(force=True)


def _apply_accel(vals: dict):
    """opt:* sürətləndirmə ayarlarını öz setter-ləri ilə tətbiq et (yaddaş override-ları da yenilənir). None → default."""
    from bot.core import audio_cache, youtube_handler as yh
    from common import utilities as cu
    on = lambda k: vals.get(k) != "0"                    # None / "1" → aktiv (defoltlar aktivdir)
    cu.set_accel_auto_enabled(on("opt:accel_auto"))
    audio_cache.set_l1_cache_enabled(on("opt:l1_cache"))
    cu.set_ram_disk_enabled(on("opt:ram_disk"))
    yh.set_smart_copy_enabled(on("opt:smart_copy"))
    yh.set_parallel_dl_enabled(on("opt:parallel_dl"))


# ───────────────────────── 🪶 Aşağı resurs profili (2 GB RAM / 2 nüvə kimi kiçik serverlər) ─────────────────────────
S_LOWRES, S_LOWRES_PREV = "perf:lowres", "perf:lowres_prev"


def lowres_on() -> bool:
    return _get(S_LOWRES) == "on"


def lowres_plan() -> dict:
    return {S_MODE: default_mode(), S_USER: min(2, USER_STEPS[-1]), S_DEPO: 2,
            S_THREADS: 64, S_NICE: 10, S_THREADS_AUTO: "on", S_NICE_AUTO: "on", S_SCALE: "on", S_TARGET: 85,
            S_RESERVE: 768, S_BATCH_PAR: 2}


def enable_lowres() -> dict:
    """RAM/CPU qənaəti: az proses, RAM ehtiyatı 768 MB, RAM diski və paralel fraqmentlər söndürülür,
    thread/nice Avto. Əvvəlki ayarlar saxlanır — söndürəndə qaytarılır. Turbo açıqdırsa əvvəl o söndürülür."""
    if turbo_on():
        disable_turbo()
    if not lowres_on():
        prev = {k: _db().get_setting(k) for k in tuple(lowres_plan()) + TURBO_ACCEL}
        _set(S_LOWRES_PREV, json.dumps(prev))
    plan = lowres_plan()
    for k, v in plan.items():
        _set(k, v)
    _set(S_LOWRES, "on")
    _apply_accel({"opt:accel_auto": "0", "opt:l1_cache": "1", "opt:smart_copy": "1",
                  "opt:parallel_dl": "0", "opt:ram_disk": "0"})
    _AUTO.update(threads=None, nice=None, idle=0, calm=0)
    _apply_live()
    MON.event("🪶 Aşağı resurs profili: 👤2 📦2 proses, RAM ehtiyatı 768 MB, RAM diski söndürüldü")
    return plan


def disable_lowres() -> dict:
    try:
        prev = json.loads(_get(S_LOWRES_PREV) or "{}")
    except ValueError:
        prev = {}
    for k in lowres_plan():
        _db().set_setting(k, prev.get(k))
    _set(S_LOWRES, "off")
    try:
        _apply_accel({k: prev.get(k) for k in TURBO_ACCEL})
    except Exception as e:
        logger.warning(f"Sürətləndirmə ayarları qaytarılmadı: {e!r}")
    _AUTO.update(threads=None, nice=None, idle=0, calm=0)
    _apply_live()
    MON.event("🪶 Aşağı resurs profili söndürüldü — əvvəlki ayarlar qaytarıldı")
    return prev


S_AUTOPROFILE = "perf:autoprofile"          # "done" — ilk işə salınmada seçim edilib, bir də toxunulmur
LOWRES_RAM_MB, LOWRES_CORES = 3072, 2


def auto_profile() -> str:
    """İlk işə salınmada serveri yoxla: RAM ≤ 3 GB və ya ≤ 2 nüvə → 🪶 Aşağı resurs profili açılır.
    Yalnız BİR DƏFƏ (sonra istifadəçinin seçiminə toxunulmur); əvvəldən turbo / aşağı resurs / əl ilə ölçü
    seçilibsə heç nə dəyişmir. Nəticə: 'lowres' | 'default' | 'skip'."""
    if _get(S_AUTOPROFILE):
        return "skip"
    ram = _total_ram_mb()
    if turbo_on() or lowres_on():
        _set(S_AUTOPROFILE, "done")
        return "skip"
    small = CORES <= LOWRES_CORES or (0 < ram <= LOWRES_RAM_MB)
    if small:
        enable_lowres()
        _set(S_AUTOPROFILE, "done")
        logger.info(f"🪶 Avto-profil: server kiçikdir ({CORES} nüvə, {ram} MB RAM) → Aşağı resurs profili açıldı "
                    "(Performans panelindən söndürmək olar)")
        return "lowres"
    _set(S_AUTOPROFILE, "done")
    logger.info(f"⚙️ Avto-profil: server kifayətdir ({CORES} nüvə, {ram} MB RAM) → standart ayarlar")
    return "default"


def enable_turbo(avail_mb: int = 0) -> dict:
    if lowres_on():
        disable_lowres()
    if not turbo_on():                                   # əvvəlki vəziyyət (yalnız ilk dəfə)
        prev = {k: _db().get_setting(k) for k in TURBO_KEYS + TURBO_ACCEL}
        _set(S_TURBO_PREV, json.dumps(prev))
    plan = turbo_plan(avail_mb)
    for k, v in plan.items():
        _set(k, v)
    _set(S_TURBO, "on")
    try:
        ram_ok = shutil.disk_usage("/dev/shm").free >= 300 * 1024 * 1024
    except OSError:
        ram_ok = False
    _apply_accel({"opt:accel_auto": "0", "opt:l1_cache": "1", "opt:smart_copy": "1", "opt:parallel_dl": "1",
                  "opt:ram_disk": "1" if ram_ok else "0"})
    _apply_live()
    MON.event(f"🔥 Tam performans: 👤{plan[S_USER]} 📦{plan[S_DEPO]} 🧵{plan[S_THREADS]} nice 0")
    return plan


def disable_turbo() -> dict:
    try:
        prev = json.loads(_get(S_TURBO_PREV) or "{}")
    except ValueError:
        prev = {}
    for k in TURBO_KEYS:
        _db().set_setting(k, prev.get(k))                # None → ayar silinir (default-a qayıdır)
    _set(S_TURBO, "off")
    try:
        _apply_accel({k: prev.get(k) for k in TURBO_ACCEL})
    except Exception as e:
        logger.warning(f"Sürətləndirmə ayarları qaytarılmadı: {e!r}")
    _apply_live()
    MON.event("🔥 Tam performans söndürüldü — əvvəlki ayarlar qaytarıldı")
    return prev


# ───────────────────────── 🧠 proses hovuzu ─────────────────────────
class WorkerError(Exception):
    """İşçi prosesdə yt-dlp xətası (mətn olduğu kimi — 403 və s. yoxlamaları işləyir)."""


class WorkerCrashed(Exception):
    pass


class _Worker:
    def __init__(self, proc):
        self.proc = proc
        self.tasks = 0
        self.busy_since = None
        self.idle_since = None

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _recv(self):
        size = struct.unpack(">I", await self.proc.stdout.readexactly(4))[0]
        return pickle.loads(await self.proc.stdout.readexactly(size))

    def kill(self):
        if self.alive:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


class ProcPool:
    def __init__(self, name: str, label: str, size: int):
        self.name, self.label, self.size = name, label, size
        self.max_size = size                      # istifadəçinin qoyduğu (avto-miqyasda — maksimum)
        self.workers = []
        self.idle = deque()
        self.waiters = deque()
        self._ids = itertools.count(1)
        self.stats = {"done": 0, "err": 0, "crash": 0, "fallback": 0, "spawned": 0, "secs": 0.0}
        self.waiting = 0

    @property
    def running(self) -> int:
        return sum(1 for w in self.workers if getattr(w, "busy_since", None))

    async def _spawn(self) -> _Worker:
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bot.core.ytdl_worker", str(nice()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=None,
            cwd=os.getcwd(), env=env, limit=64 * 1024 * 1024)
        w = _Worker(proc)
        try:
            hello = await asyncio.wait_for(w._recv(), 60)
            if hello[2] != "ready":
                raise RuntimeError(hello)
        except Exception as e:
            w.kill()
            raise WorkerCrashed(f"işçi proses başlamadı: {e}")
        self.stats["spawned"] += 1
        return w

    async def _acquire(self) -> _Worker:
        while True:
            while self.idle:
                w = self.idle.popleft()
                if w.alive:
                    return w
                self._forget(w)
            if len(self.workers) < self.size:
                placeholder = object()
                self.workers.append(placeholder)          # yer tutulur (paralel spawn həddi aşmasın)
                try:
                    w = await self._spawn()
                except BaseException:
                    self.workers.remove(placeholder)
                    raise
                self.workers[self.workers.index(placeholder)] = w
                return w
            fut = asyncio.get_running_loop().create_future()
            self.waiters.append(fut)
            self.waiting += 1
            try:
                w = await fut
            except asyncio.CancelledError:
                if fut.done() and not fut.cancelled() and fut.result() is not None:
                    self._release(fut.result(), True)        # verilmiş prosesi geri qaytar
                raise
            finally:
                self.waiting -= 1
            if w is not None and w.alive:
                return w

    def _forget(self, w):
        if w in self.workers:
            self.workers.remove(w)

    def _release(self, w: _Worker, healthy: bool):
        w.busy_since = None
        if not healthy or not w.alive or w.tasks >= RECYCLE_AFTER or len(self.workers) > self.size:
            w.kill()
            self._forget(w)
            w = None                       # gözləyən varsa — o özü yenisini yaradacaq
        while self.waiters:
            fut = self.waiters.popleft()
            if not fut.done():
                fut.set_result(w)
                return
        if w is not None:
            w.idle_since = time.monotonic()
            self.idle.append(w)

    # ── 🚀 avto-miqyas ──
    def set_max(self, v: int):
        self.max_size = v
        if autoscale_on() and mode() == "process":
            alive = sum(1 for w in self.workers if isinstance(w, _Worker))
            # başlanğıc: nüvə sayı (və ya hazırda açıq proses sayı) — sonra yükə görə böyüyür / kiçilir
            self.size = max(1, min(v, max(alive, CORES)))
        else:
            self.size = v
        self.kick()

    def kick(self):
        """Ölçü böyüyəndə növbədəkiləri oyadır — onlar yeni proses açır."""
        free = self.size - len(self.workers)
        while free > 0 and self.waiters:
            fut = self.waiters.popleft()
            if not fut.done():
                fut.set_result(None)
                free -= 1

    def reap_idle(self, keep: int = 1) -> int:
        """IDLE_KEEP-dən çox boş qalan artıq prosesləri bağlayır (ən azı `keep` boş qalır)."""
        now = time.monotonic()
        idle = [w for w in self.idle if w.alive]
        old = [w for w in idle if w.idle_since and now - w.idle_since > IDLE_KEEP]
        victims = old[:max(0, len(idle) - keep)]
        for w in victims:
            try:
                self.idle.remove(w)
            except ValueError:
                pass
            w.kill()
            self._forget(w)
        return len(victims)

    async def call(self, name: str, args: tuple, timeout: float, cancel_event=None, cancel_path=None):
        w = await self._acquire()
        healthy = False
        recv = None
        t0 = time.monotonic()
        w.busy_since, w.tasks = time.time(), w.tasks + 1
        try:
            tid = next(self._ids)
            data = pickle.dumps((tid, name, args), protocol=pickle.HIGHEST_PROTOCOL)
            w.proc.stdin.write(struct.pack(">I", len(data)) + data)
            await w.proc.stdin.drain()
            recv = asyncio.ensure_future(w._recv())
            deadline, cancel_seen = t0 + timeout, None
            while True:
                done, _ = await asyncio.wait({recv}, timeout=0.5)
                if done:
                    break
                now = time.monotonic()
                if cancel_event is not None and cancel_event.is_set():
                    if cancel_path and not os.path.exists(cancel_path):
                        open(cancel_path, "w").close()       # proses növbəti hissədə özü dayanır
                    cancel_seen = cancel_seen or now
                    if now - cancel_seen > CANCEL_KILL_AFTER:
                        recv.cancel()
                        from yt_dlp.utils import DownloadCancelled
                        raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                if now > deadline:
                    recv.cancel()
                    raise WorkerError(f"vaxt bitdi ({timeout:.0f} san.)")
            _, status, payload = recv.result()
            healthy = True
            if status == "ok":
                self.stats["done"] += 1
                return payload
            if status == "cancel":
                from yt_dlp.utils import DownloadCancelled
                raise DownloadCancelled(payload)
            self.stats["err"] += 1
            raise WorkerError(payload)
        except (asyncio.IncompleteReadError, BrokenPipeError, ConnectionResetError, EOFError) as e:
            self.stats["crash"] += 1
            raise WorkerCrashed(f"işçi proses çökdü: {type(e).__name__}")
        finally:
            if recv is not None and not recv.done():
                recv.cancel()
            self.stats["secs"] += time.monotonic() - t0
            # ləğv / timeout / çöküş — prosesin vəziyyəti məlum deyil → öldürülür, yerinə yenisi gəlir
            self._release(w, healthy)

    async def shutdown(self):
        for w in list(self.workers):
            if isinstance(w, _Worker):
                w.kill()
        self.workers.clear()
        self.idle.clear()
        for fut in self.waiters:
            if not fut.done():
                fut.set_result(None)
        self.waiters.clear()

    def snapshot(self) -> dict:
        alive = [w for w in self.workers if isinstance(w, _Worker) and w.alive]
        return {"size": self.size, "max": self.max_size, "alive": len(alive), "running": self.running,
                "waiting": self.waiting, "pids": [w.proc.pid for w in alive], **self.stats}


LANES = {
    "user": ProcPool("user", "👤 İstifadəçi", user_procs()),
    "depo": ProcPool("depo", "📦 Depo", depo_procs()),
}


async def shutdown_pools():
    for p in LANES.values():
        await p.shutdown()


_STOPPING = False


async def stop_pools():
    """Bot dayanır: işçiləri bağla və yarımçıq tapşırıqları thread-də YENİDƏN BAŞLATMA.
    Əvvəl hər işçinin bağlanması "çökmə" sayılırdı — onlarla yükləmə dayanma anında thread-də yenidən
    işə düşür, botu dondurur və dayanmanı 30 san.-yə uzadırdı (10.10 19:49)."""
    global _STOPPING
    _STOPPING = True
    await shutdown_pools()


async def restart_pools():
    await shutdown_pools()
    LANES["user"].set_max(user_procs())
    LANES["depo"].set_max(depo_procs())


def _lane() -> str:
    try:
        from bot.core.depo_helpers import UPLOAD_CTX
        return "depo" if UPLOAD_CTX.get() else "user"
    except Exception:
        return "user"


async def _run(name: str, args: tuple, timeout: float, cancel_event=None, cancel_path=None):
    from bot.core import ytdl_worker
    if mode() == "process":
        pool = LANES[_lane()]
        try:
            return await pool.call(name, args, timeout, cancel_event, cancel_path)
        except WorkerCrashed as e:
            if _STOPPING:                                # dayanma — işçini biz bağladıq, təkrar yoxdur
                raise asyncio.CancelledError("bot dayanır") from e
            pool.stats["fallback"] += 1
            logger.warning(f"⚙️ {pool.label} prosesi: {e} — tapşırıq thread-də işlənir")
    return await asyncio.to_thread(ytdl_worker.TASKS[name], *args)


# ── ictimai API (youtube_handler istifadə edir) ──
async def ytdl_extract(url: str, opts: dict, timeout: float = EXTRACT_TIMEOUT):
    opts = {k: v for k, v in opts.items() if k not in ("progress_hooks", "logger")}
    return await _run("ytdl_extract", (url, opts), timeout)


async def _poll_progress(path: str, on_progress):
    """İşçinin yazdığı irəliləyiş faylını (JSON) oxuyub on_progress(dict) çağırır (hər ~1 san.)."""
    import json
    last = None
    while True:
        await asyncio.sleep(0.4)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d != last:
            last = d
            try:
                res = on_progress(d)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug(f"on_progress xətası: {e!r}")


async def _final_progress(path: str, on_progress):
    import json
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    try:
        res = on_progress(d)
        if asyncio.iscoroutine(res):
            await res
    except Exception as e:
        logger.debug(f"on_progress (yekun) xətası: {e!r}")


async def ytdl_download(url: str, opts: dict, cancel_event=None, timeout: float = DOWNLOAD_TIMEOUT,
                        on_progress=None) -> dict:
    """→ {"path", "info"}. ⏹ — cancel_event; proses / thread fərqi yoxdur (fayl bayrağı ilə).
    on_progress(dict{done,total,speed,eta}) — yükləmə gedərkən canlı irəliləyiş (könüllü)."""
    opts = {k: v for k, v in opts.items() if k not in ("progress_hooks", "logger")}
    os.makedirs(CANCEL_DIR, exist_ok=True)
    cancel_path = os.path.join(CANCEL_DIR, f"{os.getpid()}_{next(_cancel_ids)}")
    watcher = None
    if cancel_event is not None:
        async def watch():                       # ⏹ → bayraq faylı (proses də, thread də onu yoxlayır)
            while True:
                if cancel_event.is_set():
                    open(cancel_path, "w").close()
                    return
                await asyncio.sleep(0.3)
        watcher = asyncio.ensure_future(watch())
    poller = asyncio.ensure_future(_poll_progress(cancel_path + ".prog", on_progress)) if on_progress else None
    try:
        return await _run("ytdl_download", (url, opts, cancel_path), timeout, cancel_event, cancel_path)
    finally:
        if watcher:
            watcher.cancel()
        if poller:
            poller.cancel()
            await _final_progress(cancel_path + ".prog", on_progress)      # son (yekun) dəyər itməsin
        for pth in (cancel_path, cancel_path + ".prog", cancel_path + ".prog.t"):
            try:
                os.remove(pth)
            except OSError:
                pass


_cancel_ids = itertools.count(1)


def _search_proxy():
    try:
        from common import proxy
        return proxy.current("search")
    except Exception:
        return None


async def ytm_search(query: str, limit: int):
    return await _run("ytm_search", (query, limit, _search_proxy()), EXTRACT_TIMEOUT)


# ── ⚡ sürətli axtarış (inline): proses növbəsini KEÇİR ──
# Axtarış yüngül şəbəkə sorğusudur (extract_flat). 👤 zolağın prosesləri yükləmə ilə məşğul olanda
# axtarış onların arxasında növbəyə düşürdü — inline gecikməsinin əsas səbəbi bu idi.
# Burada birbaşa thread-də işləyir, növbəsiz.
FAST_SEARCH_TIMEOUT = float(os.getenv("INLINE_SEARCH_TIMEOUT", "12"))


def _fast_timeout() -> float:
    try:
        from bot.core import inline_settings
        return float(inline_settings.get("timeout"))
    except Exception:
        return FAST_SEARCH_TIMEOUT


async def fast_extract(url: str, opts: dict, timeout: float = None):
    timeout = timeout or _fast_timeout()
    opts = {k: v for k, v in opts.items() if k not in ("progress_hooks", "logger")}
    from bot.core import ytdl_worker              # əsas prosesdə birbaşa (thread-də) işlədilir
    return await asyncio.wait_for(asyncio.to_thread(ytdl_worker.TASKS["ytdl_extract"], url, opts), timeout)


async def fast_ytm_search(query: str, limit: int, timeout: float = None):
    timeout = timeout or _fast_timeout()
    from bot.core import ytdl_worker
    return await asyncio.wait_for(asyncio.to_thread(ytdl_worker.TASKS["ytm_search"], query, limit,
                                                    _search_proxy()), timeout)


# ───────────────────────── 🧵 thread hovuzu ─────────────────────────
def apply_threads(force: bool = False):
    want = max(threads(), _MIN_POOL)
    cur = set_pool_size(want, grow_only=not force)
    if cur is not None:
        logger.info(f"🧵 Thread hovuzu: {cur} → {want}")


_MIN_POOL = 0                                    # depo işçiləri üçün tələb olunan minimum (ensure_thread_pool)
POOL_PREFIX = "dlm-thread"                       # asyncio-nun öz "asyncio_N" adından fərqli — dump-larda aydın olsun


def set_pool_size(want: int, grow_only: bool = True):
    """to_thread hovuzunu `want` ölçülü ManagedThreadPoolExecutor ilə idarə edir (init, cpu_pool, depo).
    Mövcud hovuz varsa, onu hamar şəkildə genişləndirir (grow_only), sıfırdan yaratmır. Dəyişməyibsə None."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    ex = getattr(loop, "_default_executor", None)
    cur = getattr(ex, "_max_workers", 0) if ex else min(32, CORES + 4)
    if isinstance(ex, ManagedThreadPoolExecutor):
        if cur == want or (grow_only and cur > want):
            return None
        if want > cur or not grow_only:
            ex.set_max_workers(want)
            return cur
    new_ex = ManagedThreadPoolExecutor(max_workers=want, thread_name_prefix=POOL_PREFIX)
    loop.set_default_executor(new_ex)
    if ex is not None:
        ex.shutdown(wait=False)
    return cur


def thread_pool_stats() -> dict:
    """ThreadPool-un cari vəziyyəti: aktiv, növbədəki, ümumi işçi və tamamlanan tapşırıqlar."""
    try:
        ex = getattr(asyncio.get_running_loop(), "_default_executor", None)
        if isinstance(ex, ManagedThreadPoolExecutor):
            return ex.stats()
        return {
            "max": getattr(ex, "_max_workers", 0) if ex else 0,
            "threads": len(getattr(ex, "_threads", [])),
            "active": 0,
            "idle": 0,
            "queued": 0,
            "submitted": 0,
            "done": 0,
            "failed": 0,
        }
    except RuntimeError:
        return {"max": 0, "threads": 0, "active": 0, "idle": 0, "queued": 0, "submitted": 0, "done": 0, "failed": 0}


def thread_pool_size() -> int:
    try:
        ex = getattr(asyncio.get_running_loop(), "_default_executor", None)
        return getattr(ex, "_max_workers", 0) if ex else min(32, CORES + 4)
    except RuntimeError:
        return 0


def ffmpeg_prefix() -> list:
    """ffmpeg-i aşağı prioritetlə işə salmaq üçün (bot donmasın)."""
    n = nice()
    import shutil
    return ["nice", "-n", str(n)] if n > 0 and shutil.which("nice") else []


# ───────────────────────── 📊 sistem monitoru + 🚀 avto-miqyas ─────────────────────────
def _read(path) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _proc_ticks(pid) -> int:
    """utime + stime (taktla)."""
    raw = _read(f"/proc/{pid}/stat")
    if not raw:
        return 0
    try:
        f = raw.rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])
    except (IndexError, ValueError):
        return 0


def _tree(pid, depth=0) -> list:
    """pid + bütün övladları (ffmpeg və s. — yt-dlp-nin açdığı proseslər də sayılsın)."""
    out = [pid]
    if depth > 4:
        return out
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return out
    for tid in tids:
        for c in _read(f"/proc/{pid}/task/{tid}/children").split():
            if c.isdigit():
                out += _tree(int(c), depth + 1)
    return out


class SysMon:
    def __init__(self):
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.prev_cpu = None
        self.prev_ticks = {}
        self.prev_t = None
        self.cpu = 0.0                 # ümumi %
        self.cores = []                # hər nüvə %
        self.bot_cpu = 0.0             # botun özü (maşının %-i)
        self.work_cpu = 0.0            # işçi proseslər + övladları (maşının %-i)
        self.avail_mb = 0
        self.total_mb = 0
        self.hist = deque(maxlen=60)   # ~3 dəq.
        self.events = deque(maxlen=8)  # (ts, mətn) — miqyas dəyişiklikləri

    def sample(self):
        cur = cpu_times()
        if self.prev_cpu and len(self.prev_cpu) == len(cur):
            pct = cpu_percent(self.prev_cpu, cur)
            self.cpu, self.cores = pct[0], pct[1:]
            self.hist.append(self.cpu)
        self.prev_cpu = cur

        now = time.monotonic()
        groups = {"bot": [os.getpid()], "work": []}
        for pool in LANES.values():
            for w in pool.workers:
                if isinstance(w, _Worker) and w.alive:
                    groups["work"] += _tree(w.proc.pid)
        ticks = {}
        for g, pids in groups.items():
            ticks[g] = {p: _proc_ticks(p) for p in pids}
        if self.prev_t:
            dt = max(0.001, now - self.prev_t) * self.hz * CORES
            for g in ticks:
                used = sum(max(0, t - self.prev_ticks.get(p, t)) for p, t in ticks[g].items())
                setattr(self, "bot_cpu" if g == "bot" else "work_cpu", min(100.0, 100.0 * used / dt))
        self.prev_ticks = {p: t for g in ticks.values() for p, t in g.items()}
        self.prev_t = now

        mem = {}
        for line in _read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            if v.split():
                mem[k] = int(v.split()[0]) // 1024
        self.total_mb = mem.get("MemTotal", 0)
        self.avail_mb = mem.get("MemAvailable", mem.get("MemFree", 0))

    def event(self, text):
        self.events.append((time.time(), text))
        logger.info(f"🚀 Avto-miqyas: {text}")


MON = SysMon()


def _scale_step():
    cpu, avail = MON.cpu, MON.avail_mb
    target, reserve = cpu_target(), ram_reserve()
    user, depo = LANES["user"], LANES["depo"]
    # bot ləngiyirsə (event loop gecikməsi) — CPU boş olsa da böyütmə; darboğaz başqa yerdədir (baza kilidi, şəbəkə)
    w = WATCHDOG.snapshot() if WATCHDOG.enabled else {"lag_avg": 0.0, "now_frozen": False}
    laggy = w["now_frozen"] or w["lag_avg"] > LAG_LIMIT
    over = cpu > min(100, target + 5) or (avail and avail < reserve) or laggy
    room = cpu < target - 3 and (not avail or avail - PROC_MB > reserve) and w["lag_avg"] < LAG_LIMIT / 2
    step = max(1, CORES // 4)
    why = f"CPU {cpu:.0f}%, boş RAM {avail} MB" + (f", bot gecikməsi {w['lag_avg'] * 1000:.0f} ms" if laggy else "")
    if over:
        for pool in (depo, user):                  # əvvəl depo kiçilir — istifadəçi qorunur
            if pool.size > 1:
                old = pool.size
                pool.size -= 1
                MON.event(f"{pool.label} {old}→{pool.size} ({why})")
                break
    elif room:
        for pool in (user, depo):                  # əvvəl istifadəçi böyüyür
            if pool.waiting and pool.size < pool.max_size:
                old = pool.size
                ram_cap = (avail - reserve) // PROC_MB if avail else step
                grow = max(1, min(step, pool.waiting, ram_cap))
                pool.size = min(pool.max_size, pool.size + grow)
                pool.kick()
                MON.event(f"{pool.label} {old}→{pool.size} ({why}, növbə {pool.waiting})")
                break
    for pool in (user, depo):
        n = pool.reap_idle()
        if n:
            logger.info(f"🚀 {pool.label}: {n} boş proses bağlandı (RAM qaytarıldı)")


def _renice_workers(n: int):
    """Açıq işçi proseslərə nice-i yuxarı qaldır (aşağı prioritet). Azaltmaq icazə tələb edir — yeni proseslərə qalır."""
    for pool in LANES.values():
        for w in pool.workers:
            if isinstance(w, _Worker) and w.alive:
                try:
                    if os.getpriority(os.PRIO_PROCESS, w.proc.pid) < n:
                        os.setpriority(os.PRIO_PROCESS, w.proc.pid, n)
                except (OSError, AttributeError):
                    pass


def _auto_tune():
    """🤖 Thread sayı və nice — loop gecikməsinə, thread növbəsinə görə (hər SCALE_EVERY san.)."""
    t_auto, n_auto = threads_auto(), nice_auto()
    if not (t_auto or n_auto):
        return
    w = WATCHDOG.snapshot() if WATCHDOG.enabled else {"lag_avg": 0.0, "now_frozen": False}
    laggy = w["now_frozen"] or w["lag_avg"] > LAG_LIMIT
    calm = not laggy and w["lag_avg"] < LAG_LIMIT / 2
    if t_auto:
        cur = _AUTO["threads"] or base_threads()
        lo, hi = base_threads(), min(THREAD_STEPS[-1], max(128, CORES * 16))   # çox thread = GIL rəqabəti
        st = thread_pool_stats()
        queued, active = st.get("queued", 0), st.get("active", 0)
        new = cur
        if laggy:
            new = max(lo, cur - 8)
        elif queued >= 8 and calm:
            new = min(hi, cur + max(8, cur // 4))
            _AUTO["idle"] = 0
        elif queued == 0 and active < cur * 0.25:
            _AUTO["idle"] += 1
            if _AUTO["idle"] >= 20 and cur > lo:               # ~60 san. boş
                new, _AUTO["idle"] = max(lo, cur - 8), 0
        else:
            _AUTO["idle"] = 0
        _AUTO["threads"] = new
        if new != cur:
            apply_threads(force=True)
            MON.event(f"🧵 Thread {cur}→{new} (növbə {queued}, aktiv {active}, gecikmə {w['lag_avg'] * 1000:.0f} ms)")
    if n_auto:
        cur = _AUTO["nice"] if _AUTO["nice"] is not None else AUTO_NICE_BASE
        new = cur
        if laggy:
            new, _AUTO["calm"] = min(AUTO_NICE_MAX, cur + 2), 0
        elif calm:
            _AUTO["calm"] += 1
            if _AUTO["calm"] >= 10 and cur > AUTO_NICE_BASE:    # ~30 san. sakit
                new, _AUTO["calm"] = max(AUTO_NICE_BASE, cur - 1), 0
        else:
            _AUTO["calm"] = 0
        _AUTO["nice"] = new
        if new != cur:
            _renice_workers(new)
            MON.event(f"🐢 nice +{cur}→+{new} (gecikmə {w['lag_avg'] * 1000:.0f} ms)")


async def _scaler():
    while True:
        await asyncio.sleep(SCALE_EVERY)
        try:
            await asyncio.to_thread(MON.sample)
            _auto_tune()
            if mode() == "process" and autoscale_on():
                _scale_step()
        except Exception as e:
            logger.debug(f"avto-miqyas: {e}")


_scaler_task = None


# ───────────────────────── 🫀 event loop watchdog ─────────────────────────
class LoopWatchdog:
    def __init__(self):
        self.enabled = True
        self.loop = None
        self.loop_tid = None
        self.beat = time.monotonic()
        self.lags = deque(maxlen=1500)         # (ts, gecikmə san.) — ~5 dəq.
        self.freezes = deque(maxlen=50)        # (ts, müddət, harada)
        self._cur = None                       # gedən donma: (başlanğıc, stack)
        self._task = None
        self._thread = None

    def start(self, loop):
        if self._task and not self._task.done():
            return
        self.loop = loop
        self.enabled = watchdog_on()
        self._task = loop.create_task(self._ticker())
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)
            self._thread.start()

    async def _ticker(self):
        self.loop_tid = threading.get_ident()
        while True:
            t = time.monotonic()
            self.beat = t
            await asyncio.sleep(0.2)
            lag = max(0.0, time.monotonic() - t - 0.2)
            self.lags.append((time.time(), lag))
            if self._cur and lag < FREEZE_AT:
                self._finish()

    def _full_report(self) -> str:
        """Donma anında: event loop-un TAM yığını, işləyən handler-lər, bütün thread-lərin yığını."""
        try:
            from bot.core import latency
            run = ", ".join(f"{k} ({d:.1f}s)" for k, d in latency.inflight()[:8]) or "—"
        except Exception:
            run = "?"
        lines = [f"işləyən handler-lər: {run}", "── event loop yığını ──"]
        frames = sys._current_frames()
        main = frames.get(self.loop_tid)
        if main is not None:
            lines += traceback.format_stack(main)[-14:]
        lines.append("── digər thread-lər ──")
        names = {t.ident: t.name for t in threading.enumerate()}
        for tid, fr in frames.items():
            if tid in (self.loop_tid, threading.get_ident()):
                continue
            st = traceback.extract_stack(fr)
            own = [f for f in st if ROOT in os.path.abspath(f.filename) and "site-packages" not in f.filename]
            f = (own or st)[-1]
            lines.append(f"  {names.get(tid, tid)}: {os.path.basename(f.filename)}:{f.lineno} {f.name}")
        return "\n".join(lines)

    def _finish(self):
        start, where = self._cur
        dur = time.monotonic() - start
        self._cur = None
        self.freezes.append((time.time(), dur, where))
        rep = getattr(self, "_report", "")
        if rep and dur >= 1.5:                       # bot_logs/freeze_<tarix>.txt — mənə göndərmək üçün
            try:
                os.makedirs(os.path.join(ROOT, "bot_logs"), exist_ok=True)
                path = os.path.join(ROOT, "bot_logs", time.strftime("freeze_%Y%m%d.txt"))
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"\n===== {time.strftime('%H:%M:%S')} · {dur:.1f} san. · {where}\n{rep}\n")
                if os.path.getsize(path) > 2_000_000:
                    os.replace(path, path + ".old")
            except OSError:
                pass
        if dur >= 2:
            logger.warning(f"🫀 Bot {dur:.1f} san. dondu — {where}")

    def _watch(self):
        while True:
            time.sleep(0.25)
            if not self.enabled or self.loop_tid is None:
                continue
            age = time.monotonic() - self.beat
            if age > FREEZE_AT + 0.2 and self._cur is None:
                self._cur = (self.beat, self._where())
                self._report = self._full_report()

    def _where(self) -> str:
        """Event loop thread-i hazırda harada ilişib — layihə fayllarından ən dərin sətir."""
        frame = sys._current_frames().get(self.loop_tid)
        if frame is None:
            return "?"
        stack = traceback.extract_stack(frame)
        own = [f for f in stack if ROOT in os.path.abspath(f.filename) and "cpu_pool" not in f.filename
               and "site-packages" not in f.filename]
        f = (own or stack)[-1]
        rel = os.path.relpath(f.filename, ROOT) if ROOT in os.path.abspath(f.filename) else os.path.basename(f.filename)
        deeper = stack[-1]
        extra = "" if deeper is f else f" → {os.path.basename(deeper.filename)}:{deeper.lineno} {deeper.name}"
        return f"{rel}:{f.lineno} {f.name}(){extra}"

    def snapshot(self) -> dict:
        now = time.time()
        recent = [l for t, l in self.lags if now - t <= 300]
        hour = [f for f in self.freezes if now - f[0] <= 3600]
        return {
            "lag": self.lags[-1][1] if self.lags else 0.0,
            "lag_avg": sum(recent) / len(recent) if recent else 0.0,
            "lag_max": max(recent, default=0.0),
            "freezes_hour": len(hour),
            "last": self.freezes[-1] if self.freezes else None,
            "now_frozen": bool(self._cur),
        }


WATCHDOG = LoopWatchdog()


def health() -> str:
    """'' — hər şey qaydasındadır, əks halda qısa xəbərdarlıq (menyu düyməsi üçün)."""
    s = WATCHDOG.snapshot()
    if s["freezes_hour"]:
        return f"{s['freezes_hour']} donma/saat"
    if s["lag_max"] > 0.5:
        return f"gecikmə {s['lag_max'] * 1000:.0f} ms"
    t_st = thread_pool_stats()
    if t_st.get("queued", 0) >= 30:
        return f"🧵 {t_st['queued']} iş növbədə"
    return ""


# ───────────────────────── başlatma ─────────────────────────
_started = False


def init(context=None):
    """menu_plugin.setup çağırır: loop işləyirsə dərhal, yoxdursa dp.startup-da."""

    def _go():
        global _started
        if _started:
            return
        loop = asyncio.get_running_loop()
        _started = True
        try:
            auto_profile()
        except Exception as e:
            logger.warning(f"Avto-profil seçilmədi: {e!r}")
        apply_threads()
        WATCHDOG.start(loop)
        global _scaler_task
        for pool, v in ((LANES["user"], user_procs()), (LANES["depo"], depo_procs())):
            pool.set_max(v)
        if _scaler_task is None or _scaler_task.done():
            _scaler_task = loop.create_task(_scaler())
        logger.info(f"⚙️ Performans: {MODES[mode()]} · {CORES} nüvə · 👤 {user_procs()} + 📦 {depo_procs()} proses"
                    f" · 🧵 {threads()} thread · nice +{nice()}"
                    + (f" · 🚀 avto-miqyas: CPU hədəfi {cpu_target()}%" if autoscale_on() else ""))

    try:
        asyncio.get_running_loop()
        _go()
    except RuntimeError:
        dp = getattr(context, "dp", None)
        if dp is not None:
            async def _startup():
                _go()
            dp.startup.register(_startup)
            dp.shutdown.register(stop_pools)
            return
    if context is not None and getattr(context, "dp", None) is not None \
            and not getattr(context, "_cpu_pool_shutdown", False):
        context._cpu_pool_shutdown = True
        context.dp.shutdown.register(stop_pools)


is_turbo = turbo_on
is_auto_scaling = autoscale_on
