"""🛡 .block / .unblock / .blocked və .dmdel (gələn DM-ləri silmə) — saxta Telethon client ilə."""
import asyncio
import types

import pytest
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Channel, ChatPhotoEmpty, User

import userbot.plugins.dm_guard as dg
from tests import userbot_fakes as fakes
T0 = fakes.T0


def user(uid, name="U", **kw):
    return User(id=uid, first_name=f"{name}{uid}", access_hash=1, **kw)


class FakeUDB:
    def __init__(self, saved=None):
        self.d = {(dg.NS, dg.KEY): saved} if saved else {}

    def kv_get(self, ns, key, default=None):
        return self.d.get((ns, key), default)

    def kv_set(self, ns, key, value):
        self.d[(ns, key)] = value


class FakeClient:
    def __init__(self, me=5, blocked=0, flood_once=False):
        self.me = user(me, "Men")
        self.blocked = [user(1000 + i) for i in range(blocked)]
        self.entities = {}
        self.log, self.deleted, self.sent, self.flood_once = [], [], [], flood_once

    async def get_me(self):
        return self.me

    async def get_entity(self, ref):
        return self.entities[ref]

    async def send_message(self, to, text, **kw):
        self.sent.append((to, text))

    async def delete_messages(self, chat, ids, revoke=True):
        if self.flood_once:
            self.flood_once = False
            raise FloodWaitError(request=None, capture=1)
        self.deleted.append((chat, list(ids), revoke))

    async def __call__(self, req):
        name = type(req).__name__
        self.log.append((name, req))
        if name == "GetBlockedRequest":
            page = self.blocked[req.offset:req.offset + req.limit]
            return types.SimpleNamespace(users=page, count=len(self.blocked))
        if name == "UnblockRequest":
            self.blocked = [u for u in self.blocked if u.id != req.id.id]
        return True

    def calls(self, name):
        return [r for n, r in self.log if n == name]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    async def fake_temp(event, text, delay=2):
        await dg.safe_edit(event, text)

    monkeypatch.setattr(dg, "temp_edit", fake_temp)
    monkeypatch.setattr(dg, "box_text", lambda t: t)
    monkeypatch.setattr(dg, "run_bg", lambda fn, *a, **k: fn(*a, **k))        # fon yazısı sinxron — test dərhal oxuya bilsin
    monkeypatch.setattr(dg, "get_udb", lambda: FakeUDB())
    real_sleep = asyncio.sleep
    monkeypatch.setattr(dg.asyncio, "sleep", lambda s: real_sleep(0))                # gözləmələr testi yubatmasın


@pytest.fixture
def ub():
    u = fakes.FakeUB()
    dg.register(u)
    return u


# ───────────────────────── DMGuard qaydaları ─────────────────────────
def test_should_delete_rules():
    g = dg.DMGuard(FakeUDB())
    stranger, friend = user(10), user(11, contact=True)
    assert not g.should_delete(stranger, 5)                              # söndürülüb
    g.cfg["on"] = True
    assert g.should_delete(stranger, 5) and g.should_delete(friend, 5)    # «hamıdan»
    assert not g.should_delete(None, 5) and not g.should_delete(user(5), 5)               # özün
    assert not g.should_delete(user(777000), 5)                                           # Telegram xidməti: giriş kodları
    assert not g.should_delete(user(12, bot=True), 5)                                     # botlar
    g.cfg["allow"] = [10]
    assert not g.should_delete(stranger, 5) and g.should_delete(user(13), 5)              # istisna siyahısı
    g.cfg["scope"] = "strangers"
    assert not g.should_delete(friend, 5) and not g.should_delete(user(14, mutual_contact=True), 5)
    assert g.should_delete(user(15), 5)


def test_settings_persist_and_survive_a_broken_database():
    udb = FakeUDB({"on": True, "scope": "strangers", "revoke": True, "allow": ["7", 8], "başqa": 1})
    g = dg.DMGuard(udb)
    assert g.cfg == {"on": True, "scope": "strangers", "revoke": True, "allow": [7, 8]}

    class Broken:
        def kv_get(self, *a):
            raise RuntimeError("baza yoxdur")

    g2 = dg.DMGuard(Broken())
    assert g2.cfg["on"] is False and g2.cfg["allow"] == []                  # standart: söndürülüb
    dg.run_bg = lambda fn, *a, **k: fn(*a, **k)
    g.cfg["on"] = False
    g._udb = udb
    g.save()
    assert udb.d[(dg.NS, dg.KEY)]["on"] is False and udb.d[(dg.NS, dg.KEY)]["allow"] == [7, 8]


# ───────────────────────── gələn DM-ləri silmə (xam handler) ─────────────────────────
async def incoming(ub, client, sender, *, private=True, out=False):
    (_, fn), = ub.raw_handlers
    ev = fakes.FakeEvent(client, private=private, sender=sender, chat_id=sender.id, msg_id=42, out=out)
    await fn(ev)
    return ev


async def test_incoming_dm_is_deleted_only_when_enabled_and_allowed(ub):
    c = FakeClient()
    await incoming(ub, c, user(10))
    assert not c.deleted                                                  # söndürülüb
    await fakes.run_command(ub, "dmdel", ".dmdel on", c, private=True)
    await incoming(ub, c, user(10))
    assert c.deleted == [(10, [42], False)]                               # yalnız səndən (standart)
    await incoming(ub, c, user(777000))
    await incoming(ub, c, user(12, bot=True))
    await incoming(ub, c, user(10), private=False)                        # qrup mesajı
    await incoming(ub, c, user(10), out=True)
    assert len(c.deleted) == 1
    await fakes.run_command(ub, "dmdel", ".dmdel both", c)
    await incoming(ub, c, user(11))
    assert c.deleted[-1] == (11, [42], True) and ub.state["dm_guard"].deleted == 2


async def test_incoming_dm_waits_out_a_short_flood_and_gives_up_on_a_long_one(ub):
    c = FakeClient(flood_once=True)
    ub.state["dm_guard"].cfg["on"] = True
    await incoming(ub, c, user(10))
    assert c.deleted == [(10, [42], False)] and ub.state["dm_guard"].deleted == 1

    class Long(FakeClient):
        async def delete_messages(self, *a, **k):
            raise FloodWaitError(request=None, capture=999)

    c = Long()
    await incoming(ub, c, user(10))
    assert not c.deleted


# ───────────────────────── .dmdel komandaları ─────────────────────────
async def test_dmdel_commands(ub):
    c = FakeClient()
    g = ub.state["dm_guard"]
    ev = await fakes.run_command(ub, "dmdel", ".dmdel", c)
    assert "söndürülüb" in ev.edits[-1] and "hamıdan" in ev.edits[-1] and "yalnız səndən" in ev.edits[-1]
    ev = await fakes.run_command(ub, "dmdel", ".dmdel on strangers", c)
    assert g.cfg["on"] and g.cfg["scope"] == "strangers" and "kontaktında olmayanların" in ev.edits[-1]
    await fakes.run_command(ub, "dmdel", ".dmdel on", c)
    assert g.cfg["scope"] == "all"
    await fakes.run_command(ub, "dmdel", ".dmdel both", c)
    assert g.cfg["revoke"] is True
    await fakes.run_command(ub, "dmdel", ".dmdel me", c)
    assert g.cfg["revoke"] is False
    ev = await fakes.run_command(ub, "dmdel", ".dmdel off", c)
    assert not g.cfg["on"] and "Söndürüldü" in ev.edits[-1]
    ev = await fakes.run_command(ub, "dmdel", ".dmdel nə", c)
    assert "on [strangers]" in ev.edits[-1]


async def test_dmdel_allow_deny_list(ub):
    c = FakeClient()
    g = ub.state["dm_guard"]
    c.entities["@dost"] = user(30, "Dost")
    ev = await fakes.run_command(ub, "dmdel", ".dmdel allow @dost", c)
    assert g.cfg["allow"] == [30] and "istisnadır" in ev.edits[-1]
    await fakes.run_command(ub, "dmdel", ".dmdel allow", c, private=True, chat=user(31, "Dost"))   # şəxsi çatda həmin şəxs
    assert g.cfg["allow"] == [30, 31]
    await fakes.run_command(ub, "dmdel", ".dmdel allow @dost", c)
    assert g.cfg["allow"] == [30, 31]                                       # təkrar yoxdur
    c.entities[30] = user(30, "Dost")
    c.entities[31] = user(31, "Dost")
    ev = await fakes.run_command(ub, "dmdel", ".dmdel list", c)
    assert "istisnaları</b> — 2" in ev.edits[-1] and "Dost30" in ev.edits[-1]
    ev = await fakes.run_command(ub, "dmdel", ".dmdel deny @dost", c)
    assert g.cfg["allow"] == [31] and "çıxarıldı" in ev.edits[-1]
    await fakes.run_command(ub, "dmdel", ".dmdel deny @dost", c)             # olmayanı çıxarmaq xəta vermir
    assert g.cfg["allow"] == [31]
    ev = await fakes.run_command(ub, "dmdel", ".dmdel allow", c)             # nə hədəf, nə şəxsi çat
    assert "Kimi?" in ev.edits[-1]


# ───────────────────────── bloklama ─────────────────────────
async def test_block_by_username_in_a_group_edits_the_command(ub):
    c = FakeClient()
    c.entities["@spam"] = user(40, "Spam")
    ev = await fakes.run_command(ub, "block", ".block @spam", c, chat=Channel(id=1, title="G", photo=ChatPhotoEmpty(), date=T0))
    assert [r.id.id for r in c.calls("BlockRequest")] == [40]
    assert "bloklandı" in ev.edits[-1] and "Spam40" in ev.edits[-1] and not c.sent and not ev.deleted


async def test_block_in_private_chat_deletes_command_and_reports_to_saved_messages(ub):
    c = FakeClient()
    ev = await fakes.run_command(ub, "block", ".block", c, private=True, chat=user(41, "Yad"))
    assert [r.id.id for r in c.calls("BlockRequest")] == [41]
    assert ev.deleted and not ev.edits                                       # blokdan sonra bu çatda redaktə mümkün deyil
    assert c.sent[0][0] == "me" and "Yad41" in c.sent[0][1] and "bloklandı" in c.sent[0][1]


async def test_block_by_reply_and_refusals(ub):
    c = FakeClient(me=5)
    ev = await fakes.run_command(ub, "block", ".block", c)                 # nə reply, nə @, nə şəxsi çat
    assert "Kimi?" in ev.edits[-1]
    c.entities["@men"] = user(5, "Men")
    ev = await fakes.run_command(ub, "block", ".block @men", c)
    assert "Özünü" in ev.edits[-1]
    c.entities["@tg"] = user(777000, "Telegram")
    ev = await fakes.run_command(ub, "block", ".block @tg", c)
    assert "Telegram xidmətini" in ev.edits[-1]
    c.entities["@qrup"] = Channel(id=9, title="K", photo=ChatPhotoEmpty(), date=T0)
    ev = await fakes.run_command(ub, "block", ".block @qrup", c)
    assert "Yalnız istifadəçi" in ev.edits[-1]
    assert not c.calls("BlockRequest")


async def test_block_error_is_reported_to_saved_messages_in_private(ub):
    class Boom(FakeClient):
        async def __call__(self, req):
            if type(req).__name__ == "BlockRequest":
                raise RuntimeError("PEER_ID_INVALID")
            return await super().__call__(req)

    c = Boom()
    await fakes.run_command(ub, "block", ".block", c, private=True, chat=user(42))
    assert c.sent[0][0] == "me" and "PEER_ID_INVALID" in c.sent[0][1]


async def test_unblock_single_all_and_blocked_list(ub):
    c = FakeClient(blocked=250)
    ev = await fakes.run_command(ub, "blocked", ".blocked", c)
    assert "cəmi 250" in ev.edits[-1] and "1. U1000" in ev.edits[-1] and "daha 220 nəfər" in ev.edits[-1]
    c.entities["@u"] = c.blocked[3]
    ev = await fakes.run_command(ub, "unblock", ".unblock @u", c)
    assert [r.id.id for r in c.calls("UnblockRequest")] == [1003] and "blokdan çıxarıldı" in ev.edits[-1]
    assert len(c.blocked) == 249
    ev = await fakes.run_command(ub, "unblock", ".unblock all", c)
    assert len(c.calls("UnblockRequest")) == 1 + 249 and "249 nəfər" in ev.edits[-1] and not c.blocked    # heç kim atlanmadı
    ev = await fakes.run_command(ub, "unblock", ".unblock all", c)
    assert "Bloklanan yoxdur" in ev.edits[-1]
    ev = await fakes.run_command(ub, "blocked", ".blocked", c)
    assert "siyahı boşdur" in ev.edits[-1]
    ev = await fakes.run_command(ub, "unblock", ".unblock", c)
    assert "Kimi?" in ev.edits[-1]


def test_patterns_do_not_collide_with_other_commands():
    import re
    ub_ = fakes.FakeUB()
    dg.register(ub_)
    for text, name in ((".block", "block"), (".block @a", "block"), (".unblock all", "unblock"), (".blocked", "blocked"),
                       (".dmdel", "dmdel"), (".dmdel on strangers", "dmdel")):
        matches = [n for n, (pat, _) in ub_.handlers.items() if re.match(pat, text)]
        assert matches == [name], (text, matches)


async def test_unblock_all_does_not_skip_people_while_the_list_shrinks_and_survives_failures(monkeypatch):
    monkeypatch.setattr(dg, "BLOCK_PAGE", 10)                  # 35 nəfər → 4 səhifə; offset ilə səhifələmə 20-dən çoxunu atlayardı

    class Flaky(FakeClient):
        async def __call__(self, req):
            if type(req).__name__ == "UnblockRequest" and req.id.id in (1003, 1017):
                self.log.append(("UnblockRequest", req))
                raise RPCError(request=None, message="PEER_ID_INVALID", code=400)
            return await super().__call__(req)

    c = Flaky(blocked=35)
    done, failed = await dg.unblock_all(c, pause=0)
    assert (done, failed) == (33, 2)
    assert [u.id for u in c.blocked] == [1003, 1017]            # yalnız alınmayanlar qalıb
    assert await dg.unblock_all(FakeClient(blocked=0), pause=0) == (0, 0)
