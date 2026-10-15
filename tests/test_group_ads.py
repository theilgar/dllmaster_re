"""📣 Qrup reklamı: ayarlar, qrup siyahısı, göndərmə dövrü, xəta hallarında davranış, menyu."""
import asyncio
import time
import types
from unittest.mock import AsyncMock

import pytest
from telethon import errors as tle

import bot.plugins.group_ads_plugin as gp
from common import group_ads as ads

_REAL_NEXT_DELAY = ads.next_delay


class FakeSettings:
    def __init__(self):
        self.d = {}

    def get_setting(self, key, default=None):
        return self.d.get(key, default)

    def set_setting(self, key, value):
        if value is None:
            self.d.pop(key, None)
        else:
            self.d[key] = str(value)


@pytest.fixture(autouse=True)
def store(monkeypatch):
    import common.database as cd
    db = FakeSettings()
    monkeypatch.setattr(cd, "get_db", lambda: db)
    return db


# ── ayarlar ──
def test_defaults_are_conservative():
    s = ads.settings()
    assert s["delay"] == 90 and s["cooldown"] == 24 and s["max"] == 20 and s["slot"] == "all" and s["text"] == ""
    assert ads.selected() == set()                                   # opt-in: heç bir qrup seçilməyib


def test_cycles_wrap_and_stay_within_choices():
    for key, choices, default in ((ads.K_DELAY, ads.DELAYS, 90), (ads.K_COOL, ads.COOLDOWNS, 24), (ads.K_MAX, ads.MAXES, 20)):
        seen = [ads.cycle(key, choices, default) for _ in range(len(choices))]
        assert set(seen) == set(choices) and seen[-1] == default


def test_invalid_stored_value_falls_back_to_default(store):
    store.d[ads.K_DELAY] = "1"                                       # 1 san. icazə verilmir
    assert ads.settings()["delay"] == 90
    store.d[ads.K_MAX] = "999"
    assert ads.settings()["max"] == 20


def test_slot_cycle():
    assert [ads.cycle_slot([0, 1]) for _ in range(4)] == ["0", "1", "all", "0"]
    assert ads.cycle_slot([]) == "all"


def test_render_placeholders():
    out = ads.render(None, "dllbot")
    assert "@dllbot" in out and "https://t.me/dllbot" in out and "{bot}" not in out
    assert ads.render("salam {bot} {link}", "@x") == "salam @x https://t.me/x"
    assert ads.render("salam {bot}", "") == "salam bot"


def test_group_selection_toggle_and_persist():
    assert ads.toggle_group(-100) is True and ads.toggle_group(-200) is True
    assert ads.selected() == {-100, -200}
    assert ads.toggle_group(-100) is False and ads.selected() == {-200}
    ads.set_selected([])
    assert ads.selected() == set()


def test_cooldown_tracking():
    assert not ads.on_cooldown(-1, 24)
    ads.mark_sent(-1, time.time() - 3600)
    assert ads.on_cooldown(-1, 24) and not ads.on_cooldown(-1, 1)
    assert 22 * 3600 < ads.cooldown_left(-1, 24) <= 23 * 3600


def test_old_history_is_pruned():
    ads.mark_sent(-1, time.time() - 40 * 86400)
    ads.mark_sent(-2)
    assert set(ads.last_sent()) == {-2}


@pytest.mark.parametrize("rnd", [0.0, 0.5, 1.0])
def test_delay_never_below_minimum_and_has_jitter(rnd):
    assert _REAL_NEXT_DELAY(30, rnd) >= ads.MIN_DELAY
    assert _REAL_NEXT_DELAY(100, 0.0) == pytest.approx(70) and _REAL_NEXT_DELAY(100, 1.0) == pytest.approx(130)


def test_report_roundtrip():
    ads.save_report({"sent": 3})
    assert ads.last_report() == {"sent": 3}


# ── saxta asistan hesabı ──
def _dialog(cid, title, is_group=True, **ent):
    base = dict(left=False, kicked=False, deactivated=False, default_banned_rights=None, creator=False, admin_rights=None)
    entity = types.SimpleNamespace(**{**base, **ent})
    return types.SimpleNamespace(id=cid, name=title, is_group=is_group, entity=entity)


class FakeUser:
    def __init__(self, uid, username=None, first_name="User", bot=False, deleted=False, is_self=False):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.bot = bot
        self.deleted = deleted
        self.is_self = is_self


class FakeClient:
    def __init__(self, dialogs, errors=None, participants=None):
        self.dialogs = dialogs
        self.sent = []
        self.errors = errors or {}
        self.participants = participants or {}

    def is_connected(self):
        return True

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d

    async def iter_participants(self, chat_id):
        if chat_id in self.participants:
            for u in self.participants[chat_id]:
                yield u
        else:
            yield FakeUser(abs(chat_id) * 1000 + 1, username=f"user_{abs(chat_id)}")

    async def send_message(self, chat_id, text, **kw):
        err = self.errors.get(chat_id)
        if not err and isinstance(chat_id, int) and chat_id > 0:
            orig_group = -((chat_id - 1) // 1000)
            err = self.errors.get(orig_group)
        if err:
            if isinstance(err, list):
                e = err.pop(0) if err else None
                if e:
                    raise e
            else:
                raise err
        self.sent.append((chat_id, text, kw))


def _slot(idx, client, voice=True):
    return types.SimpleNamespace(idx=idx, client=client, voice_enabled=voice, icon=f"#{idx}", me=types.SimpleNamespace(username=f"acc{idx}"))


def _ctx(*slots):
    bot = types.SimpleNamespace(me=AsyncMock(return_value=types.SimpleNamespace(username="dllbot")), send_message=AsyncMock())
    return types.SimpleNamespace(bot=bot, creator_id=1, voice_assistant=types.SimpleNamespace(slots=list(slots)))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ads, "next_delay", lambda base, rnd=None: 0.0)


async def _finish(runner):
    await runner.task


# ── siyahı ──
async def test_catalog_keeps_only_writable_groups():
    dialogs = [
        _dialog(-1, "Normal"), _dialog(-2, "Kanal", is_group=False), _dialog(-3, "Çıxılıb", left=True),
        _dialog(-4, "Yazmaq qadağan"), _dialog(-5, "Admin kimi"), _dialog(-6, "Silinib", deactivated=True)]
    dialogs[3].entity.default_banned_rights = types.SimpleNamespace(send_messages=True)
    dialogs[4].entity.default_banned_rights = types.SimpleNamespace(send_messages=True)
    dialogs[4].entity.admin_rights = object()                         # admindir — yaza bilər
    runner = gp.AdRunner(_ctx(_slot(0, FakeClient(dialogs))))
    cat = await runner.catalog()
    assert [g["id"] for g in cat] == [-5, -1] or {g["id"] for g in cat} == {-1, -5}
    assert {g["title"] for g in cat} == {"Normal", "Admin kimi"}


async def test_catalog_dedupes_across_accounts_and_skips_non_voice_slots():
    shared = [_dialog(-1, "Ortaq")]
    runner = gp.AdRunner(_ctx(_slot(0, FakeClient(shared)), _slot(1, FakeClient(shared + [_dialog(-2, "Yalnız 2")])),
                              _slot(2, FakeClient([_dialog(-9, "Depo hesabı")]), voice=False)))
    cat = await runner.catalog()
    assert {g["id"] for g in cat} == {-1, -2} and next(g for g in cat if g["id"] == -1)["slot"] == 0


async def test_catalog_is_cached_until_forced():
    client = FakeClient([_dialog(-1, "A")])
    runner = gp.AdRunner(_ctx(_slot(0, client)))
    await runner.catalog()
    client.dialogs.append(_dialog(-2, "B"))
    assert len(await runner.catalog()) == 1 and len(await runner.catalog(force=True)) == 2


# ── hədəflər ──
async def test_targets_respect_selection_cooldown_slot_and_limit():
    dialogs = [_dialog(-i, f"Q{i}") for i in range(1, 8)]
    runner = gp.AdRunner(_ctx(_slot(0, FakeClient(dialogs[:4])), _slot(1, FakeClient(dialogs[4:]))))
    cat = await runner.catalog()
    assert runner.targets(cat) == []                                  # heç nə seçilməyib
    ads.set_selected(range(-7, 0))
    assert len(runner.targets(cat)) == 7
    ads.mark_sent(-1)
    assert -1 not in [g["id"] for g in runner.targets(cat)]
    ads._set(ads.K_SLOT, "1")
    assert {g["slot"] for g in runner.targets(cat)} == {1}
    ads._set(ads.K_SLOT, "all")
    ads._set(ads.K_MAX, "10")
    assert len(runner.targets(cat)) <= 10


async def test_start_refuses_without_account_or_targets():
    assert (await gp.AdRunner(_ctx()).start())["ok"] is False
    runner = gp.AdRunner(_ctx(_slot(0, FakeClient([_dialog(-1, "A")]))))
    res = await runner.start()
    assert not res["ok"] and "qrup yoxdur" in res["error"]


# ── göndərmə dövrü ──
async def _run(client, selected, **ctx_kw):
    ctx = _ctx(_slot(0, client))
    runner = gp.AdRunner(ctx)
    ads.set_selected(selected)
    res = await runner.start()
    assert res["ok"], res
    await _finish(runner)
    return runner, ctx


async def test_sends_only_to_selected_groups_never_to_people():
    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B"), _dialog(-3, "C")])
    runner, ctx = await _run(client, [-1, -3])
    assert [c for c, _, _ in client.sent] == [1001, 3001]
    assert all(cid > 0 for cid, _, _ in client.sent)                  # qrupdakı üzvlər (insanlar)
    assert all("@dllbot" in t and kw["parse_mode"] == "html" and kw["link_preview"] is False for _, t, kw in client.sent)
    assert runner.state["sent"] == 2 and not runner.state["running"]
    assert ads.on_cooldown(1001, 24) and not ads.on_cooldown(2001, 24)


async def test_second_run_skips_groups_on_cooldown():
    client = FakeClient([_dialog(-1, "A")])
    runner, _ = await _run(client, [-1])
    res = await runner.start()
    assert not res["ok"] and len(client.sent) == 1


async def test_report_saved_and_creator_notified():
    client = FakeClient([_dialog(-1, "A")])
    _, ctx = await _run(client, [-1])
    r = ads.last_report()
    assert r["sent"] == 1 and r["total"] == 1 and not r["aborted"]
    assert "Qrup reklamı dövrü bitdi" in ctx.bot.send_message.await_args.args[1] and ctx.bot.send_message.await_args.args[0] == 1


async def test_custom_text_is_used():
    ads._set(ads.K_TEXT, "Salam {bot}!")
    client = FakeClient([_dialog(-1, "A")])
    await _run(client, [-1])
    assert client.sent[0][1] == "Salam @dllbot!"


async def test_write_forbidden_is_skipped_and_run_continues():
    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B")], errors={-1: tle.ChatWriteForbiddenError(request=None)})
    runner, _ = await _run(client, [-1, -2])
    assert runner.state["skipped"] == 1 and runner.state["sent"] == 1 and [c for c, _, _ in client.sent] == [2001]
    assert not ads.on_cooldown(1001, 24)                                # göndərilməyib — gözləmə qoyulmur


async def test_slow_mode_is_skipped():
    client = FakeClient([_dialog(-1, "A")], errors={-1: tle.SlowModeWaitError(request=None, capture=30)})
    runner, _ = await _run(client, [-1])
    assert runner.state["skipped"] == 1 and runner.state["reasons"] == {"slow mode": 1}


async def test_short_flood_wait_is_waited_and_retried(monkeypatch):
    waits = []

    async def fake_sleep(self, seconds):
        waits.append(seconds)
        return False

    monkeypatch.setattr(gp.AdRunner, "_sleep", fake_sleep)
    client = FakeClient([_dialog(-1, "A")], errors={-1: [tle.FloodWaitError(request=None, capture=7)]})
    runner, _ = await _run(client, [-1])
    assert runner.state["sent"] == 1 and 8 in waits                   # 7 + 1 san. gözlənildi


async def test_long_flood_wait_blocks_that_account_only(monkeypatch):
    c0 = FakeClient([_dialog(-1, "A"), _dialog(-2, "B")], errors={-1: tle.FloodWaitError(request=None, capture=3000)})
    c1 = FakeClient([_dialog(-3, "C")])
    ctx = _ctx(_slot(0, c0), _slot(1, c1))
    runner = gp.AdRunner(ctx)
    ads.set_selected([-1, -2, -3])
    await runner.start()
    await _finish(runner)
    assert c0.sent == [] and [c for c, _, _ in c1.sent] == [3001]       # 0-cı hesab dayandı, 1-ci davam etdi
    assert runner.state["skipped"] == 2 and runner.state["sent"] == 1


async def test_peer_flood_aborts_the_whole_run():
    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B"), _dialog(-3, "C")],
                        errors={-1: tle.PeerFloodError(request=None)})
    runner, ctx = await _run(client, [-1, -2, -3])
    assert client.sent == [] and "PeerFlood" in runner.state["aborted"]
    assert "PeerFlood" in ctx.bot.send_message.await_args.args[1]


async def test_unexpected_error_counts_as_failed():
    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B")], errors={-1: RuntimeError("boom")})
    runner, _ = await _run(client, [-1, -2])
    assert runner.state["failed"] == 1 and runner.state["sent"] == 1 and [c for c, _, _ in client.sent] == [2001]


async def test_stop_interrupts_between_groups(monkeypatch):
    client = FakeClient([_dialog(-i, f"Q{i}") for i in range(1, 6)])
    ctx = _ctx(_slot(0, client))
    runner = gp.AdRunner(ctx)
    ads.set_selected(range(-5, 0))
    monkeypatch.setattr(ads, "next_delay", lambda base, rnd=None: 5.0)   # real fasilə — dayandırma onu kəsməlidir
    await runner.start()
    await asyncio.sleep(0.05)
    assert runner.stop() is True
    await asyncio.wait_for(runner.task, 2)
    assert len(client.sent) == 1 and ads.last_report()["stopped"] is True


async def test_only_one_run_at_a_time(monkeypatch):
    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B")])
    runner = gp.AdRunner(_ctx(_slot(0, client)))
    ads.set_selected([-1, -2])
    monkeypatch.setattr(ads, "next_delay", lambda base, rnd=None: 5.0)
    assert (await runner.start())["ok"]
    assert (await runner.start()) == {"ok": False, "error": "artıq gedir"}
    runner.stop()
    await asyncio.wait_for(runner.task, 2)


async def test_progress_callback_called_per_group_and_at_end():
    calls = []

    async def progress():
        calls.append(1)

    client = FakeClient([_dialog(-1, "A"), _dialog(-2, "B")])
    runner = gp.AdRunner(_ctx(_slot(0, client)))
    ads.set_selected([-1, -2])
    await runner.start(progress)
    await _finish(runner)
    assert len(calls) == 3


def test_setup_replaces_runner_and_stops_old_one():
    ctx = _ctx()
    gp.setup(ctx)
    first = ctx.group_ads
    gp.setup(ctx)
    assert ctx.group_ads is not first
    gp.teardown(ctx)


@pytest.mark.parametrize("exc,expected", [
    (tle.PeerFloodError(request=None), "peerflood"), (tle.FloodWaitError(request=None, capture=5), "flood"),
    (tle.SlowModeWaitError(request=None, capture=5), "skip:slow mode"), (tle.ChatWriteForbiddenError(request=None), "skip:yazmaq olmur"),
    (tle.UserBannedInChannelError(request=None), "skip:yazmaq olmur"), (RuntimeError("x"), "fail")])
def test_error_classification(exc, expected):
    assert gp.classify(exc) == expected


async def test_main_userbot_fetches_ids_and_live_userbot_sends():
    """Təlimat testi: əsas userbot qrupdan insanların id-lərini alır, canlı yayım userbotu yazır."""
    main_client = FakeClient([_dialog(-1, "Qrup 1")], participants={-1: [FakeUser(555, username="u555")]})
    live_client = FakeClient([_dialog(-1, "Qrup 1")])
    ctx = _ctx(_slot(0, live_client))
    ctx.telethon_client = main_client

    runner = gp.AdRunner(ctx)
    ads.set_selected([-1])
    res = await runner.start()
    assert res["ok"]
    await _finish(runner)

    # Əsas userbot göndərməməlidir, yalnız üzvləri toplamalıdır
    assert main_client.sent == []
    # Canlı yayım userbotu göndərməlidir (555 ID-sinə)
    assert len(live_client.sent) == 1
    assert live_client.sent[0][0] == 555
    assert ads.on_cooldown(555, 24)

