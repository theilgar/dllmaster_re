"""🧹🧬 Sinxron və dublikat yoxlaması əlavə depo kanallarını da əhatə edir (kanal-kanal, nəticələr birləşir)."""
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl.types import PeerChannel

import bot.depo.engine_sync as ES
from bot.core import audio_cache, depo_shards as D
from bot.depo.panels import dd_result_view, sync_result_view
from tests.test_depo_shards import Store

MAIN, B = -1003987878159, -1003993387505


class FakeClient:
    """Telethon-un sadə əvəzi: kanal başına mövcud mesaj id-ləri."""
    def __init__(self, alive):
        self.alive = alive                                     # {peer_id: {mid, ...}}

    def is_connected(self):
        return True

    async def get_entity(self, ref):
        name = str(ref).lstrip("@")
        ids = {"dllmasterdepo": MAIN, "dllmasterdepo2": B, str(MAIN): MAIN, str(B): B}
        return PeerChannel(abs(ids[name]) - 10 ** 12)

    async def get_messages(self, ent, ids=None, limit=None):
        pid = int(f"-100{ent.channel_id}")
        return [types.SimpleNamespace(media=1) if i in self.alive[pid] else None for i in ids]


class Filler(ES.SyncMixin):
    def __init__(self, alive, ids):
        self.ctx = types.SimpleNamespace(bot=MagicMock(), voice_assistant=None, depo_helpers=None)
        self.tg = types.SimpleNamespace(ok=AsyncMock(return_value=True), client=FakeClient(alive))
        self.removed, self.store, self.ids = set(), {}, ids

    _set = lambda self, k, v: self.store.__setitem__(k, v)         # noqa: E731
    _get = lambda self, k, d=None: self.store.get(k, d)            # noqa: E731

    def _depo_msg_ids(self, depo_id, checked_before=None, limit=None):
        return list(self.ids[depo_id])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(D, "_cfg", Store())
    for name in ("_ids", "_primary", "_pause", "_sent", "_main_ok", "_bad", "_slot_at"):
        monkeypatch.setattr(D, name, {})
    monkeypatch.setattr(audio_cache, "depo_chat_id", AsyncMock(return_value=MAIN))
    monkeypatch.setattr(audio_cache, "depo_setting", lambda: "@dllmasterdepo")
    monkeypatch.setattr(ES, "get_db", lambda: MagicMock())
    f = Filler(alive={MAIN: {1, 3}, B: {10}}, ids={MAIN: [1, 2, 3], B: [10, 11]})
    return f


def _add_shard():
    D.add("@dllmasterdepo2")
    D._ids["@dllmasterdepo2"] = {"id": B, "ts": 0}


def run(coro):
    return asyncio.run(coro)


def test_targets_are_the_main_depo_first_then_extra_channels(env):
    assert run(env._depo_targets()) == [(MAIN, "@dllmasterdepo")]
    _add_shard()
    assert run(env._depo_targets()) == [(MAIN, "@dllmasterdepo"), (B, "@dllmasterdepo2")]


def test_sync_without_extra_channels_keeps_the_old_result_shape(env):
    st = run(env.sync_scan(mode="quick"))
    assert "parts" not in st and st["depo_id"] == MAIN and st["missing"] == [2] and st["total"] == 3


def test_sync_checks_every_channel_and_merges_the_results(env):
    _add_shard()
    seen = []

    async def progress(st):
        seen.append((st["total"], st["checked"]))
    st = run(env.sync_scan(progress, mode="quick"))
    assert [p["depo_id"] for p in st["parts"]] == [MAIN, B]
    assert st["total"] == 5 and st["checked"] == 5 and sorted(st["missing"]) == [2, 11]
    assert [(p["ref"], p["missing"]) for p in st["parts"]] == [("@dllmasterdepo", [2]), ("@dllmasterdepo2", [11])]
    assert "2 kanal" in st["method"] and not st["stopped"] and seen and seen[-1][0] == 5


def test_a_broken_extra_channel_does_not_fail_the_whole_sync(env, monkeypatch):
    _add_shard()
    orig = env._sync_scan_one

    async def flaky(depo_id, *a, **kw):
        if depo_id == B:
            raise RuntimeError("userbot başqa çat açdı")
        return await orig(depo_id, *a, **kw)
    monkeypatch.setattr(env, "_sync_scan_one", flaky)
    st = run(env.sync_scan(mode="quick"))
    assert [p["depo_id"] for p in st["parts"]] == [MAIN] and st["errors"] and "@dllmasterdepo2" in st["errors"][0]
    text, _ = sync_result_view(st)
    assert "@dllmasterdepo2" in text and "başqa çat" in text


def test_a_broken_main_depo_still_raises(env, monkeypatch):
    _add_shard()
    monkeypatch.setattr(env, "_sync_scan_one", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run(env.sync_scan(mode="quick"))


def test_stop_request_stops_before_the_next_channel(env):
    _add_shard()
    st = run(env.sync_scan(should_stop=lambda: True, mode="quick"))
    assert st["stopped"] is True and st["parts"] == []


def test_sync_apply_all_removes_each_channels_missing_posts_by_its_own_chat(env):
    _add_shard()
    st = run(env.sync_scan(mode="quick"))
    calls = []

    def fake_apply(depo_id, missing, block=True, record=True):
        calls.append((depo_id, list(missing), record))
        return {"posts": len(missing), "rows": len(missing), "songs": len(missing), "blocked": block,
                "removed": [(f"v{depo_id}{m}", f"t{m}") for m in missing]}
    env.sync_apply = fake_apply
    r = env.sync_apply_all(st, True)
    assert calls == [(MAIN, [2], False), (B, [11], False)]
    assert r == {"posts": 2, "rows": 2, "songs": 2, "blocked": True}
    assert len(__import__("json").loads(env.store["last_removed"])) == 2        # "geri qaytar" üçün hamısı yazılır


def test_sync_apply_all_on_a_single_channel_result_uses_the_old_path(env):
    env.sync_apply = MagicMock(return_value={"posts": 1})
    env.sync_apply_all({"depo_id": MAIN, "missing": [5]}, False)
    env.sync_apply.assert_called_once_with(MAIN, [5], False)


# ── dublikat / indeks ──
def _part(chat, ref, **kw):
    base = {"depo_id": chat, "ref": ref, "scanned": 10, "stopped": False, "method": "userbot", "started": 100.0,
            "posts": [{"mid": 1, "chat": chat}], "novid_posts": [], "orphans": [], "dups": {}, "total": 10, "in_db": 9,
            "novid": 0, "same_vid": 0, "same_song": 0, "repoint": 0, "missing_db": 0, "samples": []}
    base.update(kw)
    return base


def test_post_keys_do_not_collide_between_channels():
    assert ES.post_key(MAIN, 5, MAIN) == 5 and ES.post_key(None, 5, MAIN) == 5
    assert ES.post_key(B, 5, MAIN) != 5 and ES.post_key(B, 5, MAIN) != ES.post_key(-1003000000001, 5, MAIN)
    f = Filler({}, {})
    assert f.post_key_of({"mid": 7, "chat": B}, MAIN) == ES.post_key(B, 7, MAIN)
    assert f.post_key_of({"mid": 7}, MAIN) == 7


def test_dedupe_scan_merges_channel_results(env, monkeypatch):
    _add_shard()
    parts = {MAIN: _part(MAIN, "@dllmasterdepo", dups={2: 1}, same_vid=1, orphans=[{"mid": 3, "chat": MAIN, "vid": "a"}],
                         samples=[["x"]]),
             B: _part(B, "@dllmasterdepo2", dups={5: 4}, same_song=1, novid_posts=[{"mid": 9, "chat": B}], repoint=2)}
    monkeypatch.setattr(env, "_dedupe_scan_one", AsyncMock(side_effect=lambda cid, ref, *a: parts[cid]))
    st = run(env.dedupe_scan())
    assert st["total"] == 20 and st["scanned"] == 20 and st["same_vid"] == 1 and st["same_song"] == 1 and st["repoint"] == 2
    assert len(st["dups"]) == 2 and 2 in st["dups"] and ES.post_key(B, 5, MAIN) in st["dups"]
    assert len(st["orphans"]) == 1 and len(st["novid_posts"]) == 1 and len(st["posts"]) == 2
    assert [p["depo_id"] for p in st["parts"]] == [MAIN, B] and "2 kanal" in st["method"]
    text, kb = dd_result_view(st)
    assert "Kanallar" in text and "@dllmasterdepo2" in text and "dublikat: 1" in text


def test_dedupe_scan_single_channel_keeps_the_old_shape(env, monkeypatch):
    one = _part(MAIN, "@dllmasterdepo")
    monkeypatch.setattr(env, "_dedupe_scan_one", AsyncMock(return_value=one))
    assert run(env.dedupe_scan()) is one


def test_dedupe_link_works_per_channel_and_records_one_summary(env, monkeypatch):
    seen = []
    monkeypatch.setattr(env, "_dedupe_link_one", lambda p: (seen.append(p["depo_id"]) or (3, 2)))
    monkeypatch.setattr(audio_cache, "mark_dirty", lambda: None)
    st = {"parts": [_part(MAIN, "a", dups={2: 1}), _part(B, "b", dups={5: 4, 6: 4})]}
    assert env.dedupe_link(st) == {"linked": 6, "repointed": 4} and seen == [MAIN, B]
    import json
    assert json.loads(env.store["last_dedupe"])["dups"] == 3


def test_novid_link_groups_matched_posts_by_their_channel(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env, "dedupe_link", lambda s: (calls.append((s["depo_id"], [p["mid"] for p in s["posts"]]))
                                                       or {"linked": len(s["posts"])}))
    n = env.novid_link(MAIN, [{"mid": 1, "chat": MAIN}, {"mid": 2, "chat": B}, {"mid": 3}])
    assert n == 3 and sorted(calls) == sorted([(MAIN, [1, 3]), (B, [2])])


def test_dedupe_delete_runs_every_channel_and_reports_one_total(env, monkeypatch):
    done = []

    async def one(p, prog):
        done.append(p["depo_id"])
        await prog(len(p["dups"]), len(p["dups"]))
        return len(p["dups"])
    monkeypatch.setattr(env, "_dedupe_delete_one", one)
    reported = []

    async def progress(d, t):
        reported.append((d, t))
    st = {"parts": [_part(MAIN, "a", dups={2: 1}), _part(B, "b", dups={5: 4, 6: 4})]}
    assert run(env.dedupe_delete(st, progress)) == 3 and done == [MAIN, B] and reported == [(1, 3), (3, 3)]


# ── həqiqi oxuma yolu: mesajlar chat işarəsi ilə gəlir, hər kanal öz entity-si ilə açılır ──
def _audio_msg(mid, title, vid="dQw4w9WgXcQ"):
    from telethon.tl.types import DocumentAttributeAudio
    doc = types.SimpleNamespace(attributes=[DocumentAttributeAudio(duration=200, title=title, performer="P")], size=50)
    return types.SimpleNamespace(id=mid, document=doc, message=f"https://www.youtube.com/watch?v={vid}", entities=None)


def test_dedupe_reads_each_channel_with_its_own_entity_and_tags_posts(env, monkeypatch):
    _add_shard()
    opened = []

    async def iter_messages(ent, **kw):
        opened.append(int(f"-100{ent.channel_id}"))
        for m in ({int(f"-100{ent.channel_id}"): [_audio_msg(1, "a"), _audio_msg(2, "b")]}[opened[-1]]):
            yield m
    env.tg.client.iter_messages = iter_messages
    monkeypatch.setattr(Filler, "_dedupe_analyze", staticmethod(lambda depo_id, posts: {
        "novid_posts": [], "total": len(posts), "in_db": 0, "novid": 0, "orphans": [], "dups": {}, "same_vid": 0,
        "same_song": 0, "repoint": 0, "samples": [], "missing_db": 0}))
    st = run(env.dedupe_scan())
    assert opened == [MAIN, B]
    assert sorted((p["chat"], p["mid"]) for p in st["posts"]) == [(B, 1), (B, 2), (MAIN, 1), (MAIN, 2)]
    assert st["total"] == 4 and st["scanned"] == 4 and [p["ref"] for p in st["parts"]] == ["@dllmasterdepo", "@dllmasterdepo2"]


# ── 🧰 yan yerlər: səsli oxuma ehtiyatı və öz-yoxlama ──
def test_ref_of_returns_the_extra_channels_reference(env):
    _add_shard()
    assert D.ref_of(B) == "@dllmasterdepo2" and D.ref_of(MAIN) is None


def test_selftest_mentions_extra_channels(env):
    from bot.core import selftest
    ctx = types.SimpleNamespace(bot=MagicMock())
    ok, text = run(selftest._depo(ctx))
    assert ok and "əlavə kanal" not in text
    _add_shard()
    D.set_main_ok(B, True)
    D.capacity_fn = lambda cid: 5
    ok, text = run(selftest._depo(ctx))
    assert "əlavə kanal: 1/1 hazır" in text
