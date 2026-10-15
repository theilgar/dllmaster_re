"""
⚙️ bot/depo/engine.py - Depo doldurucu mühərriki (Filler) və növbə idarəçisi.
"""
# Modul səviyyəli sabitlər / köməkçilər engine_common.py-dədir; Filler metodları mövzuya görə mixin-lərə bölünüb.
from bot.depo.engine_common import *  # noqa: F401,F403
from bot.depo.engine_common import __all__ as _COMMON_ALL  # noqa: F401
from bot.depo.engine_countries import CountriesMixin
from bot.depo.engine_failures import FailuresMixin
from bot.depo.engine_queue import QueueMixin
from bot.depo.engine_sync import SyncMixin


class Filler(FailuresMixin, SyncMixin, QueueMixin, CountriesMixin):
    def __init__(self, context):
        self.ctx = context
        self.task = None
        self.stop_event = threading.Event()
        self.queue = deque()
        self.seen = set()
        self.tg = None                          # TgUser (userbot) — setup-da verilir
        self.stats = {"done": 0, "cached": 0, "failed": 0, "started": None,
                      "last_build": None, "queue_built": 0, "note": ""}
        self.current = {}                       # işçi nömrəsi → hazırda yüklənən mahnı
        self.workers_tasks = {}
        # 📊 canlı monitor üçün
        self.wstate = {}                        # işçi → {"phase", "title", "since"}
        self.events = deque(maxlen=80)          # son hadisələr: (ts, işçi, növ, ad, san., bayt)
        self.xfer = deque(maxlen=400)           # 📶 (ts, dl_bayt, dl_san, up_bayt, up_san) — sürət üçün
        self.done_ts = deque(maxlen=20000)      # yüklənən hər mahnının vaxtı (sürət qrafiki)
        self.durations = deque(maxlen=200)      # bir mahnının tam müddəti (axtarış + yükləmə + göndərmə)
        self.bytes = 0
        # 💾 restart arası davamlılıq
        self.inflight = {}                      # işçi → hazırda işlənən element (restartda növbənin önünə qayıdır)
        self._save_lock = asyncio.Lock()
        self._saved_sig = None
        self.restored = 0
        self.build_lock = asyncio.Lock()
        # 🎵 YouTube Music kataloqu — ayrıca növbə (əsas mənbələr həmişə öndədir)
        self.ytm_queue = deque()
        self.country_queue = deque()                # 🌍 seçilmiş ölkələrin mahnıları — ən yüksək prioritet
        self.ytm_seen = set()
        self.ytm = YtmCrawler() if HAS_YTM else None
        if self.ytm:
            self.ytm.filler = self
            try:
                self.ytm.countries = self.countries()  # 🌍 seed və skaner sırası üçün
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:1142]: %r", _e)
        self.retry = []                         # [{"item", "tries", "next", "err", "kind"}]
        self.removed = set()                    # depodan silinib — doldurucu yenidən yükləməsin (video_id)
        self.wake = asyncio.Event()             # ⚡ Məcburi davam — gözləyən bütün işçiləri dərhal oyadır
        self.last_progress = time.time()        # son uğurlu / keşdən keçən mahnı (ilişmə aşkarlanması)
        self._init_resolve_table()
        self._init_fail_tables()
        self.load_state()

    # ── ayarlar (bazada) ──
    _cfg = Settings("depo_fill:")

    _get = staticmethod(_cfg.get)

    _set = staticmethod(_cfg.put)

    @property
    def enabled(self) -> bool:
        return self._get("on") == "1"

    # ── 🤖 Avto / ✋ Manual (⏱ fasilə, 🧵 worker) — rejim yazılmayıbsa: əl ilə fərqli dəyər → manual ──
    def _mode(self, key: str, value_key: str, default) -> str:
        m = self._get(key)
        if m in ("auto", "manual"):
            return m
        v = self._get(value_key)
        if value_key == "delay" and v is None and self._get("speed"):
            return "manual"                             # köhnə 🐢/🚶/🏃 ayarı — istifadəçi seçimi
        return "manual" if v not in (None, "", str(default)) else "auto"

    @property
    def delay_mode(self) -> str:
        return self._mode("delay_mode", "delay", DEFAULT_DELAY)

    @property
    def workers_mode(self) -> str:
        return self._mode("workers_mode", "workers", DEFAULT_WORKERS)

    def set_mode(self, which: str, mode: str):
        if which in ("delay", "workers") and mode in ("auto", "manual"):
            self._set(f"{which}_mode", mode)
            if which == "workers":
                ensure_thread_pool(self.workers)

    @staticmethod
    def auto_delay() -> int:
        """🤖 Fasilə hesabların vəziyyətinə görə: hamısı sağlam — 2 · biri limitdədir — 10 · işlək yoxdur — 30."""
        try:
            from bot.core import yt_accounts as ya
            if ya.pick("depo") is None:
                return 30
            role = [a for a in ya.accounts() if a.get("on", True) and "depo" in a["roles"]]
            return 10 if any(ya.block_left(a["id"]) for a in role) else 2
        except Exception:
            return DEFAULT_DELAY

    @staticmethod
    def auto_workers() -> int:
        """🤖 Worker sayı = 📦 depo proses zolağının hazırkı ölçüsü (⚙️ avto-miqyas onu CPU / RAM-a görə dəyişir)."""
        try:
            from bot.core import cpu_pool
            n = cpu_pool.LANES["depo"].size if cpu_pool.mode() == "process" else cpu_pool.depo_procs()
            return max(1, min(MAX_WORKERS, int(n)))
        except Exception:
            return DEFAULT_WORKERS

    @property
    def delay(self) -> int:
        """Hər işçinin mahnılar arası fasiləsi (san.)."""
        if self.delay_mode == "auto":
            return self.auto_delay()
        v = self._get("delay")
        if v is None:                                   # köhnə 🐢/🚶/🏃 ayarından köçür
            return OLD_SPEEDS.get(self._get("speed") or "", DEFAULT_DELAY)
        try:
            return max(0, min(MAX_DELAY, int(v)))
        except (TypeError, ValueError):
            return DEFAULT_DELAY

    def set_delay(self, value: int) -> int:
        value = max(0, min(MAX_DELAY, int(value)))
        self._set("delay", str(value))
        self._set("delay_mode", "manual")
        return value

    def step_delay(self, direction: int) -> int:
        cur = self.delay
        if direction > 0:
            nxt = next((x for x in DELAY_STEPS if x > cur), min(MAX_DELAY, cur + 60))
        else:
            nxt = next((x for x in reversed(DELAY_STEPS) if x < cur), 0)
        return self.set_delay(nxt)

    def step_delay_delta(self, delta: int) -> int:
        return self.set_delay(self.delay + int(delta))

    @property
    def workers(self) -> int:
        """Eyni anda neçə mahnı yüklənsin (1 = tək-tək)."""
        if self.workers_mode == "auto":
            return self.auto_workers()
        try:
            return max(1, min(MAX_WORKERS, int(self._get("workers") or DEFAULT_WORKERS)))
        except (TypeError, ValueError):
            return DEFAULT_WORKERS

    def set_workers(self, value: int) -> int:
        value = max(1, min(MAX_WORKERS, int(value)))
        self._set("workers", str(value))
        self._set("workers_mode", "manual")
        ensure_thread_pool(value)
        return value

    def step_workers(self, direction: int) -> int:
        cur = self.workers
        if direction > 0:
            nxt = next((x for x in WORKER_STEPS if x > cur), MAX_WORKERS)
        else:
            nxt = next((x for x in reversed(WORKER_STEPS) if x < cur), 1)
        return self.set_workers(nxt)

    def step_workers_delta(self, delta: int) -> int:
        return self.set_workers(self.workers + int(delta))

    # ── 📊 monitor köməkçiləri ──
    def _phase(self, idx: int, phase: str, title: str = None):
        st = self.wstate.setdefault(idx, {"phase": "", "title": "", "since": time.time()})
        if title is not None:
            if title != st.get("title"):
                st.pop("via", None)                 # 🌐 yeni mahnı — yükləmə / upload yeri (S/E) sıfırlanır
            st["title"] = title
        if st["phase"] != phase:
            st["phase"], st["since"] = phase, time.time()

    def _event(self, idx: int, kind: str, title: str, secs: float = 0.0, size: int = 0):
        now = time.time()
        self.events.append((now, idx, kind, title, secs, size))
        if kind == "done":
            self.done_ts.append(now)
            self.durations.append(secs)
            self.bytes += size

    @property
    def ytm_on(self) -> bool:
        return HAS_YTM and self._get("ytm_all") == "1"

    def set_ytm(self, on: bool):
        self._set("ytm_all", "1" if on else "0")

    def sources(self) -> list:
        try:
            data = json.loads(self._get("sources") or "null")
        except ValueError:
            data = None
        return data if isinstance(data, list) else list(DEFAULT_SOURCES)

    def save_sources(self, sources):
        self._set("sources", json.dumps(sources))

    def tg_source_ids(self) -> set:
        return {int(s["id"]) for s in self.sources() if s.get("kind") == "tg_chat" and str(s["id"]).lstrip("-").isdigit()}

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    # ── 💾 növbənin saxlanması (restartdan sonra davam) ──
    def _snapshot(self) -> dict:
        """Event loop thread-ində çağırılır — siyahılar kopyalanır, JSON isə ayrıca thread-də yazılır."""
        fly_main, fly_ytm = [], []
        for _, it in sorted(self.inflight.items()):
            (fly_ytm if it.get("src") == "ytm" else fly_main).append(dict(it))
        st = self.stats
        return {
            "v": 1, "saved": time.time(),
            "queue": fly_main + list(self.queue),
            "ytm_queue": fly_ytm + list(self.ytm_queue),
            "last_build": st.get("last_build"), "queue_built": st.get("queue_built", 0),
            "build_pending": self.build_lock.locked(),
            "counters": {k: st.get(k, 0) for k in ("done", "cached", "failed")},
            "bytes": self.bytes,
            "retry": [dict(r, item=dict(r["item"])) for r in self.retry],
        }

    def _sig(self):
        q, y = self.queue, self.ytm_queue
        return (len(q), len(y), len(self.retry), self._key(q[0]) if q else None, y[0].get("vid") if y else None,
                tuple(sorted(self.inflight)), self.stats["done"] + self.stats["cached"] + self.stats["failed"])

    @staticmethod
    def _write_state(data: dict):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)                    # atomik — yarımçıq fayl qalmır

    async def save_state(self, force: bool = False):
        sig = self._sig()
        if not force and sig == self._saved_sig:
            return
        if self._save_lock.locked() and not force:
            return
        async with self._save_lock:             # force — gedən yazını gözləyib təzə snapshot yazır
            data = self._snapshot()
            try:
                await asyncio.to_thread(self._write_state, data)
                self._saved_sig = sig
            except Exception as e:
                logger.warning(f"💾 Depo növbəsi yazılmadı: {e}")

    async def state_saver(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(STATE_SAVE_EVERY)
            await self.save_state()

    def load_state(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"💾 {STATE_FILE} oxunmadı ({e}) — növbə sıfırdan qurulacaq")
            return
        for it in data.get("queue") or []:
            if isinstance(it, dict) and self._key(it) and self._key(it) not in self.seen:
                self.seen.add(self._key(it))
                self.queue.append(it)
        for it in data.get("ytm_queue") or []:
            if isinstance(it, dict) and it.get("vid") and it["vid"] not in self.ytm_seen:
                self.ytm_seen.add(it["vid"])
                self.ytm_queue.append(it)
        st = self.stats
        # qurulma yarımçıq qalmışdısa — bərpa olunan növbə bitən kimi yenidən qurulsun (Telegram qaldığı yerdən)
        st["last_build"] = None if data.get("build_pending") else data.get("last_build")
        st["queue_built"] = data.get("queue_built") or 0
        for k, v in (data.get("counters") or {}).items():
            if k in st and isinstance(v, int):
                st[k] = v
        self.bytes = int(data.get("bytes") or 0)
        self.retry = [r for r in (data.get("retry") or []) if isinstance(r, dict) and isinstance(r.get("item"), dict)]
        self.restored = len(self.queue) + len(self.ytm_queue) + len(self.retry)
        self._saved_sig = self._sig()
        if self.restored:
            ago = int((time.time() - (data.get("saved") or time.time())) / 60)
            logger.info(f"💾 Depo növbəsi bərpa olundu: {len(self.queue)} əsas + {len(self.ytm_queue)} YT Music "
                        f"({ago} dəq. əvvəl yazılıb)")

    def reset_state(self):
        """🔁 Növbəni yenidən qur: növbə + Telegram skan mövqeləri sıfırlanır."""
        self.queue.clear()
        self.seen.clear()
        self.stats["queue_built"] = 0
        self.stats["last_build"] = None
        self._set("tgscan", "{}")

    # ── Telegram artımlı skan mövqeləri ──
    def tg_state(self, peer_id) -> dict:
        try:
            all_ = json.loads(self._get("tgscan") or "{}")
        except ValueError:
            all_ = {}
        st = all_.get(str(peer_id))
        return st if isinstance(st, dict) else {}

    async def tg_checkpoint(self, peer_id, state: dict):
        """Əvvəl növbə (skan olunanlar itməsin), sonra skan mövqeyi yazılır."""
        await self.save_state(force=True)
        try:
            all_ = json.loads(self._get("tgscan") or "{}")
        except ValueError:
            all_ = {}
        all_[str(peer_id)] = state
        self._set("tgscan", json.dumps(all_))

    async def tg_scan_source(self, ent, limit, on_item, should_stop, progress=None) -> dict:
        peer_id = self.tg.describe(ent)[0]
        state = dict(self.tg_state(peer_id))

        async def checkpoint(stt):
            await self.tg_checkpoint(peer_id, stt)

        return await self.tg.scan(ent, limit, on_item, should_stop, progress, state=state, checkpoint=checkpoint)

    # ── ⚡ Məcburi davam ──
    async def nap(self, secs: float):
        """asyncio.sleep, amma ⚡ Məcburi davam basılanda dərhal oyanır."""
        if secs <= 0:
            return
        try:
            await asyncio.wait_for(self.wake.wait(), secs)
        except asyncio.TimeoutError:
            pass

    def kick(self):
        self.wake.set()
        self.wake.clear()                   # gözləyənlər artıq oyandı; növbəti nap yenə gözləyir

    def stall_reason(self) -> str:
        """Doldurucu niyə irəliləmir — boşdursa hər şey qaydasındadır."""
        if not self.running:
            return "doldurucu dayanıb (xəta və ya restart)" if self.enabled else ""
        depo = audio_cache.depo_status()
        if not depo["id"] and depo["error"]:
            return "depo kanalı əlçatan deyil"
        try:
            from bot.core.youtube_handler import cooldown_left
            cd = cooldown_left()
            if cd:
                return f"YouTube 403 cooldown — {cd} san"
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:1565]: %r", _e)
        now = time.time()
        stuck = [i for i, st in self.wstate.items()
                 if st.get("phase") in _ACTIVE_PHASES and now - st.get("since", now) > STUCK_AFTER]
        if stuck:
            return f"{len(stuck)} işçi {STUCK_AFTER // 60} dəq.+ ilişib qalıb"
        empty = not self.queue and not self.ytm_queue and not self.country_queue and not self.build_lock.locked()
        if empty and self.retry:
            nxt = self.next_retry_in() or now
            if nxt > now:
                return f"yalnız təkrar növbəsi qalıb — növbəti {int((nxt - now) / 60) + 1} dəq. sonra"
        if empty and not self.retry and not self.ytm_on:
            return "növbə boşdur — yenidən qurulmanı gözləyir"
        if (self.queue or self.ytm_queue or self.country_queue) and now - self.last_progress > NO_PROGRESS_AFTER \
                and now - (self.stats.get("started") or now) > NO_PROGRESS_AFTER:
            return f"{int((now - self.last_progress) / 60)} dəq.-dir heç nə yüklənmir"
        return ""

    async def force(self) -> list:
        """⚡ Məcburi davam: bütün gözləmələri ləğv edib işi dərhal yenidən başladır. Görülən işlərin siyahısı."""
        done = []
        if not self.running:
            self.start()
            done.append("▶️ doldurucu işə salındı")
        try:
            from bot.core.youtube_handler import cooldown_left, reset_cooldown
            if cooldown_left():
                reset_cooldown()
                done.append("⏭ YouTube 403 cooldown sıfırlandı")
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:1595]: %r", _e)
        depo = audio_cache.depo_status()
        if not depo["id"]:
            try:
                audio_cache._depo["checked"] = 0          # depo kanalını dərhal yenidən yoxla
                done.append("📦 depo kanalı yenidən yoxlanılır")
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:1602]: %r", _e)
        if self.retry:
            for r in self.retry:
                r["next"] = 0
            done.append(f"🔁 {len(self.retry)} təkrar indi sınanır")
        if not self.queue and not self.ytm_queue and not self.country_queue and not self.build_lock.locked():
            self.stats["last_build"] = None               # növbə dərhal yenidən qurulsun
            done.append("📋 növbə yenidən qurulur")
        # ilişib qalan işçilər: əlindəki mahnı növbənin önünə, işçi yenidən yaradılır
        now = time.time()
        stuck = [i for i, st in list(self.wstate.items())
                 if st.get("phase") in _ACTIVE_PHASES and now - st.get("since", now) > STUCK_AFTER]
        for i in stuck:
            item = self.inflight.pop(i, None)
            t = self.workers_tasks.pop(i, None)
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if item:
                (self.ytm_queue if item.get("src") == "ytm" else self.queue).appendleft(item)
            self.current.pop(i, None)
            self.wstate.pop(i, None)
        if stuck:
            done.append(f"♻️ {len(stuck)} ilişmiş işçi yenidən başladıldı")
        self.last_progress = time.time()
        self.stats["note"] = ""
        self.kick()
        done.append("⚡ gözləyən işçilər oyadıldı")
        logger.info("📦 Məcburi davam: " + "; ".join(done))
        return done

    # ── idarə ──
    def start(self):
        self._set("on", "1")
        if self.running:
            return
        self.stop_event = threading.Event()
        self.stats.update(started=time.time(), note="")
        # əvvəlki dayandırmada yarımçıq qalan mahnılar növbənin önünə
        for _, it in sorted(self.inflight.items(), reverse=True):
            (self.ytm_queue if it.get("src") == "ytm" else self.queue).appendleft(it)
        self.inflight.clear()
        ensure_thread_pool(self.workers)
        self.task = asyncio.create_task(self.loop())
        logger.info("📦 Depo doldurucu işə düşdü")

    async def stop(self, persist=True, grace: float = 20):
        if persist:
            self._set("on", "0")
        await self.save_state(force=True)      # işçilərin əlindəki mahnılar da (ləğvdən əvvəl) — thread-də
        self.stop_event.set()                  # yt-dlp yükləmələri dərhal dayanır
        self.kick()                            # fasilədə yatanlar oyanıb çıxsın
        # depoya GÖNDƏRİLƏN mahnılar yarımçıq kəsilməsin: kəsilsə post kanalda olur, bazada olmur
        # və restartdan sonra eyni mahnı yenidən yüklənirdi
        self._grace = grace
        uploading = sum(1 for st in self.wstate.values() if st.get("phase") in ("UP", "SLOT"))
        if uploading and grace:
            logger.info(f"📦 {uploading} mahnının depoya göndərilməsi gözlənilir (ən çox {grace:.0f} san.)")
        if self.task and not self.task.done() and grace:
            await asyncio.wait({self.task}, timeout=grace + 5)   # loop() özü işçiləri gözləyib bağlanır
        await self.save_state(force=True)      # sinxron fsync yavaş diskdə botu 2.5 san. dondururdu
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None
        self.current.clear()
        self.wstate.clear()
        logger.info("📦 Depo doldurucu dayandı")

    def _user_artists(self) -> list:
        try:
            rows = get_db().sql_all(
                """SELECT title, COUNT(*) AS n FROM downloads WHERE ts >= %s AND title IS NOT NULL
                   GROUP BY title ORDER BY n DESC LIMIT 300""", (int(time.time()) - 30 * 86400,))
        except Exception:
            return []
        names = {}
        for r in rows:
            a = (r[0] or "").split(" - ", 1)[0].split(",")[0].strip()
            if a and a not in ("YouTube", "Naməlum"):
                names[a] = names.get(a, 0) + r[1]
        return sorted(names, key=names.get, reverse=True)[:40]

    async def ytm_feeder(self):
        """YT Music kataloqunu fonda gəzir — növbə azaldıqca növbəti ifaçını açır."""
        while not self.stop_event.is_set():
            if not self.ytm_on or not self.ytm:
                await asyncio.sleep(10)
                continue
            low = max(YTM_LOW_WATER, self.workers * 4)
            country_need = (bool(self.countries()) and len(self.country_queue) < low
                            and self.ytm.has_prio_pending())
            if len(self.ytm_queue) >= low and not country_need:   # çox işçi növbəni tez boşaldır
                await asyncio.sleep(5)
                continue
            try:
                ok = await self.ytm.step(self.ytm_add_items, self._user_artists, self.stop_event)
                if not ok:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"🎵 YT Music skaneri: {e}")
                self.ytm.stats["note"] = f"xəta: {str(e)[:60]} — 1 dəq. sonra"
                self.ytm.yt = None                     # sessiyanı yenilə
                await asyncio.sleep(60)

    # ── 🧵 İşçilər ──
    async def loop(self):
        """Nəzarətçi: istənilən sayda işçi saxlayır (➕/➖ dərhal tətbiq olunur) + YT Music skaneri."""
        await asyncio.sleep(5)
        if self.countries() and not self.country_queue:     # restartdan sonra ölkə növbəsi yenidən
            with contextlib.suppress(Exception):
                await self.apply_countries()
        feeder = asyncio.create_task(self.ytm_feeder())
        saver = asyncio.create_task(self.state_saver())
        try:
            while not self.stop_event.is_set():
                want = self.workers
                for i in range(want):
                    t = self.workers_tasks.get(i)
                    if t is None or t.done():
                        self.workers_tasks[i] = asyncio.create_task(self.worker(i))
                for i in [k for k, t in self.workers_tasks.items() if t.done()]:
                    self.workers_tasks.pop(i, None)
                await asyncio.sleep(2)
        finally:
            # dayandırılıb: depoya göndərən işçilər bitirsin (yoxsa post kanalda olur, bazada yox)
            busy = [t for t in self.workers_tasks.values() if not t.done()]
            grace = getattr(self, "_grace", 0)
            if busy and grace and self.stop_event.is_set():
                with contextlib.suppress(Exception):
                    await asyncio.wait(busy, timeout=grace)
            tasks = [feeder, saver, *self.workers_tasks.values()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.workers_tasks.clear()

    async def next_item(self, idx: int):
        """Növbəti mahnı: əvvəl əsas növbə (mənbələr), boşdursa YT Music kataloqu. Yoxdursa None (gözləyib)."""
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        if not fetch:
            self.stats["note"] = "music_plugin yüklənməyib"
            await self.nap(60)
            return None
        if not await audio_cache.depo_chat_id(self.ctx.bot):
            self.stats["note"] = "⚠️ depo kanalı əlçatan deyil — 10 dəq. sonra yenidən (⚡ ilə indi)"
            await self.nap(600)
            return None
        due = self.retry_due()                    # 🔁 vaxtı çatmış təkrar cəhdlər əvvəl
        if due is not None:
            return due
        if self.country_queue:                    # 🌍 seçilmiş ölkələr — hər şeydən əvvəl
            self.stats["note"] = ""
            return self.country_queue.popleft()
        if self.country_strict():                 # 🎯 yalnız seçilmiş ölkələr: başqa növbələrə toxunma
            if self.ytm and self.ytm_on and self.ytm.has_prio_pending():
                self.stats["note"] = "🎯 ölkə ifaçıları skan edilir..."
                await self.nap(5)
            else:
                self.stats["note"] = "🎯 seçilmiş ölkələrin mahnıları bitdi — chartlar yenilənənə qədər gözləyir"
                await self.nap(600)
                with contextlib.suppress(Exception):
                    await self.apply_countries()
            return None
        if self.queue:
            self.stats["note"] = ""
            return self.queue.popleft()
        since = time.time() - (self.stats["last_build"] or 0)
        need_build = not self.stats["last_build"] or since >= IDLE_SLEEP
        if need_build and not self.build_lock.locked():
            async with self.build_lock:
                if not self.queue:
                    self.stats["note"] = "növbə qurulur..."
                    self.seen.clear()
                    await self.build_queue()
                    if not self.stop_event.is_set():
                        self.stats["last_build"] = time.time()
            await self.save_state(force=True)
            if self.queue:
                self.stats["note"] = ""
                return self.queue.popleft()
        if self.ytm_queue:
            self.stats["note"] = ""
            return self.ytm_queue.popleft()
        if not self.build_lock.locked():
            self.stats["note"] = ("növbə bitdi — YT Music kataloqu oxunur" if self.ytm_on
                                  else "növbə bitdi — yeni mahnılar üçün gözləyir")
        await self.nap(5 if self.ytm_on else min(60, max(5, IDLE_SLEEP - since)))
        return None

    async def worker(self, idx: int):
        try:
            while not self.stop_event.is_set():
                if idx >= self.workers:                 # ➖ basıldı — artıq işçi dayanır
                    return
                self._phase(idx, "WAIT", "")
                item = await self.next_item(idx)
                if item is not None:
                    await self.process(idx, item)
        finally:
            self.wstate.pop(idx, None)

    async def process(self, idx: int, item: dict):
        fetch = getattr(self.ctx, "music_fetch_audio", None)
        had_url = bool(item.get("url"))
        t0 = time.time()
        self.current[idx] = item["title"]
        self.inflight[idx] = item
        self._phase(idx, "SRCH", item["title"])
        try:
            # ⚡ Əvvəl depoda AD üzrə yoxla — varsa YouTube axtarışına və yükləməyə ümumiyyətlə getmə
            meta = item.get("meta") or {}
            if meta.get("artist") and meta.get("track"):
                self._phase(idx, "CHK", item["title"])
                same = await asyncio.to_thread(audio_cache.find_same, meta["artist"], meta["track"],
                                               int(item.get("raw_duration") or 0))
                if same and same.get("file_id"):
                    vid_m = re.search(r"v=([\w-]{11})", item.get("url") or "")
                    vid_val = item.get("vid") or (vid_m.group(1) if vid_m else None)
                    if vid_val:
                        await asyncio.to_thread(audio_cache.alias, vid_val, same, item.get("title"))
                    self.last_progress = time.time()
                    self.stats["cached"] += 1
                    self._event(idx, "cached", item["title"], time.time() - t0)
                    return
            url, from_cache = await self.resolve(item)
            searched = not had_url and not from_cache       # API-lərdə axtarış edildi
            vid = re.search(r"v=([\w-]{11})", url or "")
            if not vid:
                self.stats["failed"] += 1
                self._event(idx, "notfound", item["title"], time.time() - t0)
                self.schedule_retry(item, "tapılmadı", "notfound")
                if searched:
                    logger.info(f"Doldurucu: {item['title']}: tapılmadı")
                    self._phase(idx, "SLP", item["title"])
                    await asyncio.sleep(min(2, self.delay))
                return
            if vid.group(1) in self.removed:               # 🧹 depodan əl ilə silinib — geri qaytarma
                self.stats["cached"] += 1
                self._event(idx, "removed", item["title"], time.time() - t0)
                return
            self._phase(idx, "CHK", item["title"])
            if await asyncio.to_thread(audio_cache.get_cached, vid.group(1)):
                self.last_progress = time.time()
                self.stats["cached"] += 1
                self._event(idx, "cached", item["title"], time.time() - t0)
                if searched:
                    self._phase(idx, "SLP", item["title"])
                    await asyncio.sleep(min(2, self.delay))
                return
            self._phase(idx, "DL", item["title"])
            ctx_token = UPLOAD_CTX.set((self, idx))        # 🤖 depo yükləməsi köməkçi botlara getsin
            xf = {}                                         # 📶 fetch_audio buraya DL / UP ölçülərini yazır
            try:
                from common import activity as _act
                st_token = _act.set_stage(xf)
            except Exception:
                _act = st_token = None
            try:
                res = await fetch(url, item["title"], int(item.get("raw_duration") or 0), self.stop_event,
                                  meta=item.get("meta"), thumb_url=item.get("thumb"))
            finally:
                UPLOAD_CTX.reset(ctx_token)
                if st_token is not None:
                    _act._STAGE.reset(st_token)
            if xf.get("dl_bytes") or xf.get("up_bytes"):
                self.xfer.append((time.time(), xf.get("dl_bytes", 0), xf.get("dl_secs", 0.0),
                                  xf.get("up_bytes", 0), xf.get("up_secs", 0.0)))
            size = 0
            if res.get("path"):
                try:
                    from bot.core import remote
                    size = remote.file_size(res["path"])           # 🌐 serverdə qalan fayl da əsl ölçü ilə
                    os.remove(res["path"])
                except OSError:
                    pass
            if res.get("cached"):
                self.last_progress = time.time()
                self.stats["cached"] += 1
                self._event(idx, "cached", item.get("title") or "", time.time() - t0)
            elif res.get("file_id"):
                self.last_progress = time.time()
                self.stats["done"] += 1
                self._event(idx, "done", item.get("title") or "", time.time() - t0, size)
            else:
                # yükləndi, amma depoya düşmədi (flood / icazə / depo əlçatan deyil) — əvvəllər "SKIP" sayılıb itirdi
                self.stats["failed"] += 1
                self._event(idx, "retry", item.get("title") or "", time.time() - t0)
                st = audio_cache.depo_status()
                self.schedule_retry(item, f"depoya yüklənmədi: {st.get('error') or 'flood / müvəqqəti xəta'}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.stop_event.is_set():
                return
            self.stats["failed"] += 1
            err = str(e)
            kind = self.classify(err)
            again = self.schedule_retry(item, err, kind)
            self._event(idx, "retry" if again else "failed", item["title"], time.time() - t0)
            logger.info(f"Doldurucu: {item['title']}: {err[:200]}"
                        + (f" — {item.get('_tries')}. təkrar sonra" if again else " — ❌ siyahısına"))
        finally:
            self.current.pop(idx, None)
            if not self.stop_event.is_set():       # dayandırılıbsa — element növbəyə qayıtmaq üçün qalır
                self.inflight.pop(idx, None)
        # istifadəçilər yükləyirsə onlara mane olmasın deyə fasilə
        if self.delay:
            self._phase(idx, "SLP", "")
            await self.nap(self.delay)
