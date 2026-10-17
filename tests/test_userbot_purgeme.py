"""🧹 .purgeme saysız: say və reply olmadan çatdakı öz mesajları silir."""
import types

import pytest

from tests import userbot_fakes as fakes
import userbot.plugins.chat_tools as chat_tools


class PurgeClient:
    """Çatın tarixi: (id, out) cütləri; iter_messages max_id/min_id/limit-ə hörmət edir."""

    def __init__(self, history):
        self.history, self.deleted, self.calls = history, [], []

    async def iter_messages(self, chat_id, min_id=0, max_id=0, limit=None, **kw):
        self.calls.append({"min_id": min_id, "max_id": max_id, "limit": limit})
        n = 0
        for mid, out in sorted(self.history, reverse=True):
            if max_id and mid >= max_id or mid <= min_id:
                continue
            yield types.SimpleNamespace(id=mid, out=out)
            n += 1
            if limit and n >= limit:
                return

    async def delete_messages(self, chat_id, ids, revoke=True):
        self.deleted += list(ids)


class KV:
    def __init__(self):
        self.d = {}

    def kv_get(self, ns, key, default=None):
        return self.d.get((ns, key), default)

    def kv_set(self, ns, key, value):
        self.d[(ns, key)] = value


class OrderClient(PurgeClient):
    async def delete_messages(self, chat_id, ids, revoke=True):
        self.deleted.append(list(ids))


@pytest.fixture
def purge(monkeypatch):
    fub = fakes.FakeUB()
    fub.state, fub.context, fub.db = {}, types.SimpleNamespace(), KV()
    chat_tools.register(fub)
    notes = []

    async def temp_edit(event, text, delay=2):
        notes.append(text)
    monkeypatch.setattr(chat_tools, "temp_edit", temp_edit)
    monkeypatch.setattr(chat_tools, "can_delete_others", lambda client, chat: _false())
    return fub, notes


async def _false():
    return False


HISTORY = [(1, True), (2, False), (3, True), (4, False), (5, True), (6, False), (7, True)]


@pytest.mark.asyncio
async def test_purgeme_without_number_deletes_all_own_messages(purge):
    fub, notes = purge
    client = PurgeClient(HISTORY)
    ev = await fakes.run_command(fub, "purgeme", ".purgeme", client, msg_id=8)
    assert sorted(client.deleted) == [1, 3, 5, 7]                         # yalnız öz (out) mesajlar
    assert client.calls[0]["limit"] is None                                # tarix bütövlükdə axtarılır
    assert notes == ["✅ <b>4 mesaj silindi.</b>"] and not any("İstifadə" in n for n in notes)
    assert "Öz mesajların toplanır" in ev.edits[0]


@pytest.mark.asyncio
async def test_pme_alias_and_nothing_to_delete(purge):
    fub, notes = purge
    client = PurgeClient([(1, False), (2, False)])
    await fakes.run_command(fub, "purgeme", ".pme", client, msg_id=3)
    assert client.deleted == [] and notes == ["ℹ️ <i>Silinəcək mesaj tapılmadı.</i>"]


@pytest.mark.asyncio
async def test_purgeme_with_number_and_with_reply_still_work(purge):
    fub, notes = purge
    client = PurgeClient(HISTORY)
    await fakes.run_command(fub, "purgeme", ".purgeme 2", client, msg_id=8)
    assert sorted(client.deleted) == [5, 7]                                # yalnız son 2 öz mesaj
    client2 = PurgeClient(HISTORY)
    reply = types.SimpleNamespace(id=3)
    ev = fakes.FakeEvent(client2, msg_id=8, reply=reply)
    import re
    pattern, fn = fub.handlers["purgeme"]
    ev.pattern_match = re.match(pattern, ".purgeme")
    await fn(ev)
    assert sorted(client2.deleted) == [3, 5, 7]                            # reply-dan indiyə qədər öz mesajlar


@pytest.mark.asyncio
async def test_purge_without_number_still_needs_a_number_or_reply(purge):
    fub, notes = purge
    client = PurgeClient(HISTORY)
    await fakes.run_command(fub, "purge", ".purge", client, msg_id=8)
    assert client.deleted == [] and "İstifadə" in notes[0]                 # .purge-in davranışı dəyişmir


def test_purgeme_help_documents_the_numberless_form():
    fub = fakes.FakeUB()
    helps = {}
    fub.command = lambda name, pattern=None, help=None, **kw: (lambda fn: (helps.__setitem__(name, help), fn)[1])
    fub.state, fub.context, fub.db = {}, types.SimpleNamespace(), types.SimpleNamespace()
    chat_tools.register(fub)
    assert "<code>.purgeme</code> — saysız" in helps["purgeme"][1] and str(chat_tools.PURGE_MAX_MESSAGES) in helps["purgeme"][1]


@pytest.mark.asyncio
async def test_deletion_order_follows_the_setting_from_the_menu(purge, monkeypatch):
    fub, notes = purge
    monkeypatch.setattr(chat_tools, "PURGE_BATCH", 2)                       # 4 mesaj → 2 partiya, sıra görünsün
    for order, expected in (("new", [[7, 5], [3, 1]]), ("old", [[1, 3], [5, 7]])):
        fub.db.kv_set("cmdopt", "purgeme:order", order)
        client = OrderClient(HISTORY)
        await fakes.run_command(fub, "purgeme", ".purgeme", client, msg_id=8)
        assert client.deleted == expected, order
    fub.db.kv_set("cmdopt", "purgeme:order", "xaos")                          # yanlış dəyər → standart (yenidən köhnəyə)
    client = OrderClient(HISTORY)
    await fakes.run_command(fub, "purgeme", ".purgeme", client, msg_id=8)
    assert client.deleted == [[7, 5], [3, 1]]


@pytest.mark.asyncio
async def test_order_setting_is_per_command(purge, monkeypatch):
    fub, notes = purge
    monkeypatch.setattr(chat_tools, "PURGE_BATCH", 2)
    monkeypatch.setattr(chat_tools, "can_delete_others", lambda client, chat: _true())
    fub.db.kv_set("cmdopt", "purgeme:order", "old")                           # yalnız purgeme dəyişib
    client = OrderClient(HISTORY)
    await fakes.run_command(fub, "purge", ".purge 4", client, msg_id=8)
    assert client.deleted[0][0] > client.deleted[0][-1]                       # .purge hələ də yenidən köhnəyə


async def _true():
    return True


def test_purge_commands_register_their_menu_options():
    fub = fakes.FakeUB()
    fub.state, fub.context, fub.db = {}, types.SimpleNamespace(), KV()
    chat_tools.register(fub)
    for name in ("purge", "purgeme"):
        (opt,) = fub.state["cmd_options"][name]
        assert opt["key"] == "order" and opt["default"] == "new" and [c for c, _ in opt["choices"]] == ["new", "old"]
