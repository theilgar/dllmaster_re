"""🔎 YT Music skaneri — paralel ifaçı oxuyucuları (engine_ytm.py, crawler.py, ytm_menu.py)."""
import asyncio
import threading
from collections import deque

import pytest

import bot.depo.crawler as C
import bot.depo.engine_queue as EQ
import bot.depo.engine_ytm as EY
from bot.depo import ytm_menu
from bot.depo.engine import Filler


class FakeCrawler:
    """step() uzun çəkir (gate gözləyir); neçə yuvanın eyni anda işlədiyini sayır."""

    def __init__(self):
        self.stats = {"artist": None, "err": 0, "err_at": 0.0, "note": ""}
        self.yt = object()
        self.active = self.peak = self.calls = 0
        self.gate = None
        self.ok = True

    def has_prio_pending(self):
        return False

    async def step(self, add_items, user_artists_fn, stop):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0.001)                      # həqiqi step şəbəkəyə çıxır — event loop-a növbə verir
            return self.ok
        finally:
            self.active -= 1


class F(EY.YtmFeederMixin):
    _mode = Filler._mode                           # həqiqi Avto / Manual qaydası

    def __init__(self):
        self.kv = {}
        self.workers = 128
        self.ytm_queue, self.country_queue = deque(), deque()
        self.stop_event = threading.Event()
        self.ytm_on = True
        self.ytm = FakeCrawler()
        self._ytm_par_init()

    def _get(self, k):
        return self.kv.get(k)

    def _set(self, k, v):
        self.kv[k] = v

    def set_mode(self, which, mode):
        if which in ("delay", "workers", "ytm_par") and mode in ("auto", "manual"):
            self._set(f"{which}_mode", mode)

    def countries(self):
        return []

    def _user_artists(self):
        return []

    async def ytm_add_items(self, items, front=False):
        return 0, 0


@pytest.fixture
def f():
    return F()


# ───────────── say və rejim ─────────────
def test_default_is_three_parallel_in_auto_mode(f):
    assert f.ytm_par_mode == "auto" and f.ytm_parallel == 3 == EY.YTM_PAR_DEFAULT


def test_manual_value_is_clamped_and_switches_to_manual(f):
    assert f.set_ytm_par(5) == 5 and f.ytm_par_mode == "manual" and f.ytm_parallel == 5
    assert f.set_ytm_par(0) == 1 and f.set_ytm_par(99) == EY.YTM_PAR_MAX
    f.kv["ytm_par"] = "kodlu"
    assert f.ytm_parallel == 3                                    # pozulmuş dəyər → standart


def test_a_saved_value_without_a_mode_counts_as_manual_like_the_other_settings():
    g = F()
    g.kv["ytm_par"] = "2"
    assert g.ytm_par_mode == "manual" and g.ytm_parallel == 2
    g.kv["ytm_par"] = "3"
    g.kv.pop("ytm_par_mode", None)
    assert g.ytm_par_mode == "auto"


# ───────────── avto: xətada azalır, sakit dövrdə qayıdır ─────────────
def test_auto_backs_off_on_errors_once_per_minute_and_recovers_when_calm(f, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(EY.time, "monotonic", lambda: now[0])
    f.note_ytm_error()
    assert f.ytm_parallel == 2
    now[0] += 10
    f.note_ytm_error()                                            # 3 yuva eyni anda xəta alsa — yenə bir addım
    assert f.ytm_parallel == 2
    now[0] += EY.YTM_ERR_DEBOUNCE + 1
    f.note_ytm_error()
    f.note_ytm_error()
    assert f.ytm_parallel == 1
    now[0] += EY.YTM_ERR_DEBOUNCE + 1
    f.note_ytm_error()
    assert f.ytm_parallel == 1                                    # 1-dən aşağı düşmür
    f._ytm_recover_tick()
    assert f.ytm_parallel == 1                                    # hələ sakit deyil
    now[0] += EY.YTM_PAR_RECOVER + 1
    f._ytm_recover_tick()
    assert f.ytm_parallel == 2
    f._ytm_recover_tick()
    assert f.ytm_parallel == 2                                    # hər fasilədə bir addım
    now[0] += EY.YTM_PAR_RECOVER + 1
    f._ytm_recover_tick()
    f.now = now[0]
    now[0] += EY.YTM_PAR_RECOVER + 1
    f._ytm_recover_tick()
    assert f.ytm_parallel == 3                                    # başlanğıcdan yuxarı qalxmır


def test_manual_ignores_errors(f):
    f.set_ytm_par(4)
    f.note_ytm_error()
    assert f.ytm_parallel == 4


# ───────────── yuvalar ─────────────
@pytest.mark.asyncio
async def test_feeder_runs_that_many_slots_at_once(f):
    f.ytm.gate = asyncio.Event()
    task = asyncio.create_task(f.ytm_feeder())
    await asyncio.sleep(0.05)
    assert f.ytm.active == 3 and f.ytm.peak == 3                  # 3 ifaçı eyni anda
    f.set_ytm_par(5)
    f.stop_event.set()
    task.cancel()
    f.ytm.gate.set()
    await asyncio.gather(task, return_exceptions=True)
    assert f.ytm.active == 0                                      # yuvalar təmiz bağlandı


@pytest.mark.asyncio
async def test_feeder_grows_and_shrinks_with_the_setting(f, monkeypatch):
    real_sleep = asyncio.sleep

    async def fast(delay):
        await real_sleep(0.01 if delay >= 3 else min(delay, 0.01))
    monkeypatch.setattr(EY.asyncio, "sleep", fast)
    f.ytm.gate = asyncio.Event()
    f.set_ytm_par(2)
    task = asyncio.create_task(f.ytm_feeder())
    await real_sleep(0.1)
    assert f.ytm.active == 2
    f.set_ytm_par(4)
    await real_sleep(0.1)
    assert f.ytm.active == 4                                      # artıq yenidən başlamadan
    f.set_ytm_par(1)
    f.ytm.gate.set()                                              # çalışanlar bitir
    await real_sleep(0.1)
    f.ytm.gate = asyncio.Event()
    f.ytm.ok = True
    await real_sleep(0.1)
    assert f.ytm.active <= 1                                      # artıq yuvalar başlamır
    f.stop_event.set()
    task.cancel()
    f.ytm.gate.set()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_slots_wait_while_the_queue_is_full(f):
    f.ytm.gate = asyncio.Event()
    f.ytm_queue.extend(range(max(EY.YTM_LOW_WATER, f.workers * 4)))
    slot = asyncio.create_task(f._ytm_slot(0))
    await asyncio.sleep(0.05)
    assert f.ytm.calls == 0                                       # növbə dolu — ifaçı açılmır
    f.stop_event.set()
    slot.cancel()
    await asyncio.gather(slot, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_throttle_error_from_the_crawler_lowers_auto_once(f):
    async def step(*a, **kw):
        f.ytm.stats["err_at"] = 123.0
        f.stop_event.set()
        return True
    f.ytm.step = step
    await f._ytm_slot(0)
    assert f.ytm_parallel == 2 and f._ytm_err_seen == 123.0


@pytest.mark.asyncio
async def test_slot_beyond_the_setting_exits_by_itself(f):
    f.set_ytm_par(2)
    await asyncio.wait_for(f._ytm_slot(2), 1)                    # i ≥ ytm_parallel → dərhal qayıdır
    assert f.ytm.calls == 0


# ───────────── crawler: ifaçını yuvalar bölüşür ─────────────
class FakeDB:
    def __init__(self, rows):
        self.rows = list(rows)

    def sql_one(self, sql, params=()):
        busy = set(params[0]) if params else set()
        return next((r for r in self.rows if r[0] not in busy), None)


@pytest.fixture
def crawler(monkeypatch):
    c = C.YtmCrawler()
    db = FakeDB([("UC1", "Bir", 0), ("UC2", "İki", 0), ("UC3", "Üç", 0)])
    monkeypatch.setattr(C, "get_db", lambda: db)
    return c


def test_claim_gives_each_slot_a_different_artist_and_release_frees_it(crawler):
    a, b, c = crawler._claim(), crawler._claim(), crawler._claim()
    assert [a[0], b[0], c[0]] == ["UC1", "UC2", "UC3"] and crawler._claim() is None
    assert crawler.stats["artist"] == "Bir · İki · Üç"
    crawler._release("UC2")
    assert crawler.stats["artist"] == "Bir · Üç" and crawler._claim()[0] == "UC2"
    crawler._release("UC1")
    crawler._release("UC3")
    crawler._release("UC2")
    assert crawler.stats["artist"] is None


def test_many_names_are_summarised(crawler):
    crawler._inflight = {f"U{i}": f"Ad{i}" for i in range(5)}
    crawler._show_reading()
    assert crawler.stats["artist"] == "Ad0 · Ad1 · Ad2 +2"


@pytest.mark.asyncio
async def test_parallel_steps_take_distinct_artists_and_always_release(crawler, monkeypatch):
    taken, gate = [], asyncio.Event()

    def artist_sync(aid, stop):
        taken.append(aid)
        return aid, [], []
    monkeypatch.setattr(crawler, "_artist_sync", artist_sync)
    monkeypatch.setattr(crawler, "add_artists", lambda pairs, prio=0: 0)
    monkeypatch.setattr(crawler, "_mark", lambda *a, **kw: None)

    async def add_items(items, front=False):
        await gate.wait()
        return 0, 0
    steps = [asyncio.create_task(crawler.step(add_items, lambda: [], threading.Event())) for _ in range(3)]
    await asyncio.sleep(0.1)
    assert sorted(taken) == ["UC1", "UC2", "UC3"] and len(crawler._inflight) == 3     # üçü də fərqli, üçü də davam edir
    gate.set()
    assert await asyncio.gather(*steps) == [True, True, True]
    assert crawler._inflight == {} and crawler.stats["artist"] is None


@pytest.mark.asyncio
async def test_a_failing_artist_is_marked_released_and_network_errors_are_counted(crawler, monkeypatch):
    marks = []
    monkeypatch.setattr(crawler, "_mark", lambda aid, st, songs=0: marks.append((aid, st)))
    for exc, counted in ((ConnectionError("Connection reset by peer"), 1), (ValueError("artist tapılmadı"), 1)):
        def boom(aid, stop, exc=exc):
            raise exc
        monkeypatch.setattr(crawler, "_artist_sync", boom)
        assert await crawler.step(None, lambda: [], threading.Event()) is True
        assert crawler.stats["err"] == counted                     # yalnız şəbəkə xətası sayılır
        crawler._release("UC2")                                    # (sınaq üçün: növbəti dövrdə eyni sıra)
    assert marks[0] == ("UC1", 2) and crawler._inflight == {"UC2": "İki"} or crawler._inflight == {}
    assert crawler.stats["err_at"] > 0


def test_throttle_detection():
    t = C.YtmCrawler._is_throttle
    assert t(ConnectionError("x")) and t(RuntimeError("HTTP 429")) and t(RuntimeError("Too Many Requests"))
    assert t(RuntimeError("Read timed out")) and not t(RuntimeError("Artist not found"))


@pytest.mark.asyncio
async def test_only_one_slot_seeds_when_nothing_is_pending(monkeypatch):
    c = C.YtmCrawler()
    pending, seeds = [], []

    class DB:
        def sql_one(self, sql, params=()):
            busy = set(params[0]) if params else set()
            return next((r for r in pending if r[0] not in busy), None)
    monkeypatch.setattr(C, "get_db", lambda: DB())
    monkeypatch.setattr(c, "_requeue_old", lambda: 0)

    def seed(user_artists, stop):
        seeds.append(1)
        pending.append(("UCS", "Seed", 0))
        return [("UCS", "Seed")]
    monkeypatch.setattr(c, "_seed_sync", seed)
    monkeypatch.setattr(c, "add_artists", lambda pairs, prio=0: len(pairs))
    monkeypatch.setattr(c, "_artist_sync", lambda aid, stop: ("Seed", [], []))
    monkeypatch.setattr(c, "_mark", lambda *a, **kw: None)

    async def add_items(items, front=False):
        return 0, 0
    real_sleep = asyncio.sleep

    async def fast(delay):
        await real_sleep(0.001)
    monkeypatch.setattr(C.asyncio, "sleep", fast)
    res = await asyncio.gather(*[c.step(add_items, lambda: [], threading.Event()) for _ in range(3)])
    assert len(seeds) == 1                                        # 3 yuva — 1 seed
    assert res == [True, True, True]                              # digərləri saatlıq yox, qısa gözləyib qayıdır


@pytest.mark.asyncio
async def test_with_no_artists_at_all_the_slot_sleeps_long(monkeypatch):
    c = C.YtmCrawler()

    class DB:
        def sql_one(self, sql, params=()):
            return None
    monkeypatch.setattr(C, "get_db", lambda: DB())
    monkeypatch.setattr(c, "_requeue_old", lambda: 0)
    monkeypatch.setattr(c, "_seed_sync", lambda ua, stop: [])
    monkeypatch.setattr(c, "add_artists", lambda pairs, prio=0: 0)
    assert await c.step(None, lambda: [], threading.Event()) is False
    assert "1 saat" in c.stats["note"]


# ───────────── növbə: eyni mahnı iki dəfə düşmür ─────────────
class Q(EQ.QueueMixin):
    def __init__(self):
        self.ytm_seen, self.ytm_queue, self.country_queue = set(), deque(), deque()


def items(*vids):
    return [{"vid": v, "title": v, "meta": None} for v in vids]


@pytest.mark.asyncio
async def test_two_slots_adding_overlapping_songs_queue_each_once(monkeypatch):
    monkeypatch.setattr(EQ.audio_cache, "get_cached", lambda vid: None)
    q = Q()
    r1, r2 = await asyncio.gather(q.ytm_add_items(items("a", "b", "c")), q.ytm_add_items(items("b", "c", "d")))
    assert sorted(i["vid"] for i in q.ytm_queue) == ["a", "b", "c", "d"]
    assert r1[0] + r2[0] == 4                                     # cəmi 4 yeni


@pytest.mark.asyncio
async def test_songs_are_not_lost_when_the_depo_check_fails(monkeypatch):
    def boom(vid):
        raise RuntimeError("baza yoxdur")
    monkeypatch.setattr(EQ.audio_cache, "get_cached", boom)
    q = Q()
    with pytest.raises(RuntimeError):
        await q.ytm_add_items(items("a", "b"))
    assert q.ytm_seen == set()                                    # növbəti dəfə yenidən baxılacaq
    monkeypatch.setattr(EQ.audio_cache, "get_cached", lambda vid: None)
    assert (await q.ytm_add_items(items("a", "b")))[0] == 2


@pytest.mark.asyncio
async def test_songs_already_in_the_depo_are_skipped(monkeypatch):
    monkeypatch.setattr(EQ.audio_cache, "get_cached", lambda vid: {"file_id": "x"} if vid == "a" else None)
    q = Q()
    assert await q.ytm_add_items(items("a", "b")) == (1, 1) and [i["vid"] for i in q.ytm_queue] == ["b"]


# ───────────── 🔎 menyu ─────────────
class CB:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)


def keyboard(kb):
    return [b for r in kb.inline_keyboard for b in r]


def test_menu_view_auto_and_manual(f):
    f.ytm_queue.extend(range(40))
    f.ytm.stats.update(artist="Bir · İki", err=2)
    text, kb = ytm_menu.view(f)
    assert "Eyni anda oxunan ifaçı:</b> 3 · 🤖 Avto" in text and "<b>40</b> / 512" in text
    assert "Bir · İki" in text and "Bağlantı / limit xətası: 2" in text
    assert [b.callback_data for b in keyboard(kb)][0] == "df:ypm:manual" and not any(b.callback_data.startswith("df:yps") for b in keyboard(kb))
    f.set_ytm_par(4)
    text, kb = ytm_menu.view(f)
    ds = [b.callback_data for b in keyboard(kb)]
    assert "✋ Manual" in text and ds[0] == "df:ypm:auto" and "df:ypd:-1" in ds and "df:ypd:1" in ds
    assert [b.text for b in keyboard(kb) if b.callback_data.startswith("df:yps")] == ["1", "2", "3", "✅ 4", "6"]
    assert len(ds) == len(set(ds)) and all(len(d.encode()) <= 64 for d in ds)
    assert ds[-3:] == ["df:yp", "df:panel", "df:close"] or "df:panel" in ds                # naviqasiya sonuncu
    assert ytm_menu.button_text(f) == "🔎 Skaner: ✋ 4 paralel"


@pytest.mark.asyncio
async def test_menu_actions(f):
    shown, cb = [], CB()

    async def show(text, kb):
        shown.append(text)
    await ytm_menu.handle(f, "yp", ["df", "yp"], cb, show)
    await ytm_menu.handle(f, "ypm", ["df", "ypm", "manual"], cb, show)
    assert f.ytm_par_mode == "manual" and f.ytm_parallel == 3 and "Manual — sayı özün seç" in shown[-1]    # cari say qalır
    await ytm_menu.handle(f, "ypd", ["df", "ypd", "1"], cb, show)
    await ytm_menu.handle(f, "ypd", ["df", "ypd", "1"], cb, show)
    assert f.ytm_parallel == 5 and cb.answers[-1] == "✅ 5 paralel"
    await ytm_menu.handle(f, "yps", ["df", "yps", "2"], cb, show)
    assert f.ytm_parallel == 2
    await ytm_menu.handle(f, "ypd", ["df", "ypd", "-5"], cb, show)
    assert f.ytm_parallel == 1                                    # 1-dən aşağı düşmür
    await ytm_menu.handle(f, "yps", ["df", "yps", "50"], cb, show)
    assert f.ytm_parallel == EY.YTM_PAR_MAX
    f._ytm_auto = 1
    await ytm_menu.handle(f, "ypm", ["df", "ypm", "auto"], cb, show)
    assert f.ytm_par_mode == "auto" and f.ytm_parallel == 3 and "Avto — 3 paralel" in shown[-1]       # avto başlanğıcdan
    n = len(shown)
    await ytm_menu.handle(f, "ypm", ["df", "ypm", "xaos"], cb, show)                    # naməlum rejim — dəyişmir
    assert f.ytm_par_mode == "auto" and len(shown) == n + 1


def test_the_real_filler_is_wired_for_parallel_scanning():
    assert issubclass(Filler, EY.YtmFeederMixin)
    assert "ytm_feeder" not in Filler.__dict__                    # köhnə tək yuvalı dövrə qalmayıb
    assert Filler.ytm_parallel is EY.YtmFeederMixin.ytm_parallel


# ───────────── /depo paneli ─────────────
def _panel_filler(f):
    from unittest.mock import MagicMock
    m = MagicMock()
    m.stats = {"done": 1, "cached": 0, "failed": 0, "started": None, "last_build": None, "queue_built": 0, "note": ""}
    m.running = m.enabled = m.ytm_on = True
    m.queue, m.retry, m.country_queue, m.ytm_queue, m.current = [], [], [], f.ytm_queue, {}
    m.delay_mode = m.workers_mode = "auto"
    m.delay, m.workers, m.restored = 2, 128, 0
    m.ytm_parallel, m.ytm_par_mode = f.ytm_parallel, f.ytm_par_mode
    m.stall_reason = lambda: ""
    m._get = lambda key: None
    m.removed = set()
    m.sources = lambda: []
    m.countries = lambda: []
    m.country_strict = lambda: False
    m.failed_list = lambda page: (0, [])
    m.ytm.counts.return_value = {"done": 1, "pending": 2, "failed": 0, "songs": 3}
    m.ytm.stats = {"artist": "Bir · İki", "note": "", "err": 0}
    return m


def test_main_depo_panel_shows_the_scanner_button_and_the_parallel_count(f):
    from unittest.mock import MagicMock
    import bot.depo.panels as P
    from tests.test_depo_panels import _hp
    v = P.make_views(filler=_panel_filler(f), helpers=_hp(), tg=MagicMock(), context=MagicMock(),
                     scan_state={"running": False, "stop": False, "task": None}, clean_pending={}, clean_wait={},
                     hp_page={"n": 0}, HP_PER_PAGE=8)
    text, kb = v.panel({"songs": 1, "hits": 0, "size": 0})
    btns = {b.callback_data: b.text for r in kb.inline_keyboard for b in r}
    assert btns["df:yp"] == "🔎 Skaner: 🤖 3 paralel" and "df:ytm" in btns
    assert "Bir · İki</i> oxunur (3 paralel)" in text
    row = next(r for r in kb.inline_keyboard if any(b.callback_data == "df:yp" for b in r))
    assert len(row) == 2                                                   # 🎵 YT Music + 🔎 Skaner — sətirdə ≤ 2
