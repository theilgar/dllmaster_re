"""bot/core/depo_shards.py — əlavə depo kanalları: siyahı, yükün bölünməsi, flood dayanması."""
import asyncio
import time
import types

import pytest

from bot.core import depo_shards as D


class Store:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        v = self.d.get(key)
        return default if v in (None, "") else v

    def put(self, key, value):
        self.d[key] = str(value)


class Bot:
    def __init__(self, ids):
        self.ids = ids

    async def get_chat(self, ref):
        if ref not in self.ids:
            raise RuntimeError("chat not found")
        return types.SimpleNamespace(id=self.ids[ref])


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(D, "_cfg", Store())
    for name in ("_ids", "_primary", "_pause", "_sent", "_main_ok", "_bad", "_slot_at"):
        monkeypatch.setattr(D, name, {})
    monkeypatch.setattr(D, "_loaded", False)
    monkeypatch.setattr(D, "capacity_fn", None)


BOT = Bot({"@b": -1002, "@c": -1003, -1002: -1002})


def run(coro):
    return asyncio.run(coro)


def ready(ref="@b", cid=-1002):
    """Kanal əlavə olunur, tapılır və əsas bot orada admin-dir."""
    D.add(ref)
    run(D.resolve(BOT, ref))
    D.set_main_ok(cid, True)


def test_add_remove_and_duplicates():
    assert D.add("@b") == "ok" and D.add("@B") == "dup" and D.add("") == "empty"
    assert D.add("@c", thread=7) == "ok"
    assert D.extras() == [{"ref": "@b", "thread": None}, {"ref": "@c", "thread": 7}]
    assert D.remove("@b") and not D.remove("@b") and D.extras() == [{"ref": "@c", "thread": 7}]


def test_without_extra_channels_nothing_changes():
    assert run(D.choose(BOT, "ytm", -1001, 5)) == (-1001, 5)


def test_unreachable_extra_channel_is_ignored():
    D.add("@nope")
    assert run(D.choose(BOT, "ytm", -1001, None)) == (-1001, None)


def test_least_loaded_channel_wins_and_ties_go_to_the_primary():
    ready()
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001               # bərabər → əsas
    for _ in range(5):
        D.note_sent(-1001)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1002
    for _ in range(9):
        D.note_sent(-1002)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001


def test_load_is_divided_by_the_number_of_helpers_in_the_channel():
    caps = {-1001: 48, -1002: 8}
    D.capacity_fn = caps.get
    ready()
    for _ in range(40):
        D.note_sent(-1001)
    for _ in range(10):
        D.note_sent(-1002)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001               # 40/48 < 10/8
    caps[-1002] = 0
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001               # köməkçisiz kanal seçilmir


def test_paused_channel_is_skipped_and_all_paused_picks_the_soonest():
    ready()
    D.pause(-1001, 1000)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1002
    D.pause(-1002, 3000)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001               # ən tez açılan


def test_alternative_needs_a_free_channel_with_helpers():
    D.add("@b", thread=9)
    run(D.resolve(BOT, "@b"))
    D.set_main_ok(-1002, True)
    run(D.choose(BOT, "ytm", -1001, 3))
    assert D.alternative(-1001) == (-1002, 9) and D.alternative(-1002) == (-1001, 3)
    D.pause(-1002, 500)
    assert D.alternative(-1001) is None
    assert D.alternative(-5) is None                                         # tanınmayan kanal


def test_pause_is_reported_once_and_survives_a_restart(monkeypatch):
    assert D.pause(-1001, 2000) is True
    assert D.pause(-1001, 1998) is False                                    # digər köməkçilərin eyni flood-u
    assert 1990 < D.left(-1001) <= 2001
    monkeypatch.setattr(D, "_pause", {})
    monkeypatch.setattr(D, "_loaded", False)
    assert D.left(-1001) > 1900
    D.clear()
    assert D.left(-1001) == 0
    monkeypatch.setattr(D, "_loaded", False)
    assert D.left(-1001) == 0


def test_load_window_forgets_old_uploads(monkeypatch):
    D.note_sent(-1001)
    assert D.load(-1001) == 1
    real = time.monotonic
    monkeypatch.setattr(D.time, "monotonic", lambda: real() + D.LOAD_WINDOW + 5)
    assert D.load(-1001) == 0


def test_report_lists_primary_first():
    D.add("@b")
    run(D.resolve(BOT, "@b"))
    run(D.choose(BOT, "ytm", -1001, None))
    rep = D.report()
    assert [r["ref"] for r in rep] == ["əsas", "@b"] and rep[0]["primary"] and rep[1]["id"] == -1002


def test_channel_is_not_used_until_the_main_bot_is_admin_there():
    D.add("@b")
    run(D.resolve(BOT, "@b"))
    for _ in range(5):
        D.note_sent(-1001)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001            # əsas bot yoxlanmayıb → kanal seçilmir
    assert D.alternative(-1001) is None
    D.set_main_ok(-1002, True)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1002
    assert D.alternative(-1001) == (-1002, None)


def test_a_forbidden_channel_is_benched_for_a_while_and_recovers_when_verified(monkeypatch):
    ready()
    D.mark_bad(-1002, "Forbidden: bot is not a member of the channel chat")
    assert D.is_bad(-1002) and not D.usable(-1002)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001
    real = time.monotonic
    monkeypatch.setattr(D.time, "monotonic", lambda: real() + D.BAD_FOR + 5)
    assert not D.is_bad(-1002) and D.usable(-1002)
    D.mark_bad(-1002, "x")
    D.set_main_ok(-1002, True)                                           # admin edildi + 🩺 Yoxla
    assert not D.is_bad(-1002)


def test_report_flags_a_channel_without_the_main_bot():
    D.add("@b")
    run(D.resolve(BOT, "@b"))
    rep = D.report()
    assert rep[-1]["main_ok"] is False and rep[-1]["bad"] is False


# ── 🎚 siyasət: 🤖 avto / ✋ manual paylar ──
def test_default_policy_is_auto_and_weights_default_to_equal():
    assert D.policy() == "auto" and D.weight("main") == 100 and D.weight("@x") == 100
    D.set_policy("manual")
    assert D.policy() == "manual"
    D.set_policy("whatever")
    assert D.policy() == "auto"


def test_weights_are_clamped_and_case_insensitive():
    assert D.set_weight("@B", 5000) == 1000 and D.weight("@b") == 1000
    assert D.set_weight("main", -4) == 0 and D.weight("main") == 0 and D.weight(None) == 0


def test_manual_policy_splits_new_uploads_by_weight():
    ready()
    D.set_policy("manual")
    D.set_weight("main", 300)
    D.set_weight("@b", 100)
    picks = []
    for _ in range(40):
        cid = run(D.choose(BOT, "ytm", -1001, None))[0]
        picks.append(cid)
        D.note_sent(cid)
    assert picks.count(-1001) == 30 and picks.count(-1002) == 10                 # 3:1


def test_manual_weight_zero_means_no_new_uploads_to_that_channel():
    ready()
    D.set_policy("manual")
    D.set_weight("@b", 0)
    for _ in range(10):
        cid = run(D.choose(BOT, "ytm", -1001, None))[0]
        D.note_sent(cid)
        assert cid == -1001
    D.set_weight("main", 0)
    D.set_weight("@b", 50)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1002
    D.set_weight("@b", 0)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001                       # hamısı 0 → əsas depo (yazı itməsin)


def test_manual_ignores_helper_counts_but_still_skips_floods_and_unusable_channels():
    ready()
    D.capacity_fn = {-1001: 48, -1002: 1}.get
    D.set_policy("manual")
    for _ in range(10):
        D.note_sent(-1001)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1002                       # avtoda 10/48 < 0/1 olmazdı
    D.pause(-1002, 900)
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001                       # flood hər siyasətdə atlanır
    D.pause(-1002, 0)
    D.clear()
    D.mark_bad(-1002, "x")
    assert run(D.choose(BOT, "ytm", -1001, None))[0] == -1001


def test_flood_reroute_in_manual_skips_channels_with_zero_weight():
    ready()
    run(D.choose(BOT, "ytm", -1001, None))
    D.set_policy("manual")
    assert D.alternative(-1001) == (-1002, None)
    D.set_weight("@b", 0)
    assert D.alternative(-1001) is None
    D.set_weight("@b", 10)
    D.set_weight("main", 0)
    assert D.alternative(-1002) is None and D.alternative(-1001) == (-1002, None)


def test_report_carries_weights_for_the_panel():
    ready()
    D.set_weight("@b", 40)
    run(D.choose(BOT, "ytm", -1001, None))
    rep = D.report()
    assert [(r["key"], r["weight"]) for r in rep] == [("main", 100), ("@b", 40)]


# ── 🚦 kanal başına ümumi sürət (sürü effekti) ──
def test_chat_slots_space_posts_evenly_and_respect_not_before(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(D.time, "monotonic", lambda: now[0])
    starts = [D.chat_slot(-1001, 60) for _ in range(5)]
    assert starts == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0]                    # 60/dəq. → saniyədə 1
    assert D.chat_slot(-1002, 60) == 1000.0                                        # başqa kanal öz cədvəlindədir
    # flood dayanması bitəndə hamı eyni anda deyil, sıra ilə
    after_ban = [D.chat_slot(-1003, 30, not_before=1900.0) for _ in range(3)]
    assert after_ban == [1900.0, 1902.0, 1904.0]
    now[0] = 5000.0                                                               # uzun fasilədən sonra yığılma yoxdur
    assert D.chat_slot(-1001, 60) == 5000.0
