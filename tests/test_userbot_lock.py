"""🔒 .lock / .unlock / .locks — seçilmiş mesaj növləri avtomatik silinir (userbot/plugins/lock.py)."""
import re
import types

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, ChatPhotoEmpty, MessageEntityTextUrl, MessageEntityUrl, User

from tests import userbot_fakes as fakes
from tests.conftest import FakeKV
import userbot.core.lock_core as core
import userbot.plugins.lock as lock

ME, ALI, VELI, BOT = 1, 20, 30, 777


class MessageMediaWebPage:                 # Telethon adı ilə eyni — link önizləməsi
    pass


def M(**kw):
    """Saxta mesaj; göstərilməyən sahələr None."""
    base = dict(media=None, sticker=None, gif=None, video_note=None, voice=None, audio=None, video=None, photo=None,
                contact=None, poll=None, dice=None, game=None, geo=None, venue=None, document=None, entities=None,
                message="", fwd_from=None, via_bot_id=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def media(**kw):
    return M(media=object(), **kw)


class Client:
    def __init__(self, admins=(), can_delete=True, flood=None):
        self.deleted, self.sent, self.admins, self.can_delete = [], [], set(admins), can_delete
        self.flood = list(flood or [])

    async def delete_messages(self, chat_id, ids, revoke=True):
        if self.flood:
            raise self.flood.pop(0)
        self.deleted.append((chat_id, list(ids), revoke))

    async def get_permissions(self, chat, user):
        if user == "me":
            return types.SimpleNamespace(is_creator=False, delete_messages=self.can_delete, is_admin=self.can_delete)
        return types.SimpleNamespace(is_creator=False, is_admin=user in self.admins, delete_messages=False)

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(delete=self._noop)

    async def _noop(self):
        return None

    async def get_entity(self, uid):
        return User(id=uid, first_name=f"U{uid}")


def group():
    return Channel(id=555, title="Qrup", photo=ChatPhotoEmpty(), date=fakes.T0, megagroup=True)


@pytest.fixture
def env(monkeypatch):
    fub = fakes.FakeUB()
    fub.db, fub.state, fub.context = FakeKV(), {}, types.SimpleNamespace()
    monkeypatch.setattr(core, "run_bg", lambda fn, *a, **kw: fn(*a, **kw))
    from userbot.core import userbot_api as api
    monkeypatch.setattr(api, "CARD_MIN_GAP", 0)
    fub.cards, fub.tr, fub.creator_id = {}, (lambda t, **kw: t), ME
    fub.is_inline_disabled = lambda chat_id: True               # .locks → mətn rejimi (inline yoxdur)
    fub.track = lambda *a, **kw: None
    notes = []

    async def temp_edit(event, text, delay=2):
        notes.append(text)
    monkeypatch.setattr(lock, "temp_edit", temp_edit)
    lock.register(fub)
    return fub, notes


def raw(fub, kind="NewMessage"):
    """Verilmiş hadisə növünün handler-i."""
    return next(fn for b, fn in fub.raw_handlers if b.__class__.__name__ == kind)


class Ev:
    """Gələn mesaj hadisəsi."""

    def __init__(self, client, message, *, chat_id=100, sender=ALI, private=True, mid=5, out=False, bot=False):
        self.client, self.message, self.chat_id, self.sender_id = client, message, chat_id, sender
        self.is_private, self.id, self.out, self._bot = private, mid, out, bot

    async def get_sender(self):
        return User(id=self.sender_id, first_name="X", bot=self._bot)


async def cmd(fub, name, text, client, **kw):
    return await fakes.run_command(fub, name, text, client, **kw)


# ───────────── növlərin tanınması ─────────────
def test_aliases_groups_and_leftover_words():
    assert core.parse_kinds(["sticker", "gif"]) == (["sticker", "gif"], [])
    assert core.parse_kinds(["stiker", "şəkil", "səs", "fayl"]) == (["sticker", "photo", "voice", "document"], [])
    kinds, rest = core.parse_kinds(["link", "@ali", "123456"])
    assert kinds == ["link"] and rest == ["@ali", "123456"]
    assert core.parse_kinds(["media"])[0] == list(core.MEDIA)
    assert set(core.parse_kinds(["all"])[0]) == set(core.KINDS)
    assert core.parse_kinds(["sticker", "stiker", "Sticker"])[0] == ["sticker"]               # təkrarsız
    assert core.parse_kinds(["bilinmir"]) == ([], ["bilinmir"])


@pytest.mark.parametrize("msg,expected", [
    (media(sticker=object(), document=object()), {"sticker"}),                                  # stiker həm də sənəddir
    (media(gif=object(), video=object(), document=object()), {"gif"}),
    (media(video_note=object(), video=object()), {"videonote"}),
    (media(voice=object(), audio=object(), document=object()), {"voice"}),
    (media(audio=object(), document=object()), {"audio"}),
    (media(video=object(), document=object()), {"video"}),
    (media(photo=object()), {"photo"}),
    (media(document=object()), {"document"}),
    (media(contact=object()), {"contact"}), (media(geo=object()), {"location"}), (media(poll=object()), {"poll"}),
    (media(dice=object()), {"dice"}), (media(game=object()), {"game"}),
    (M(message="salam"), {"text"}),
    (M(message="bax https://x.com/a"), {"text", "link"}),
    (M(message="bax", entities=[MessageEntityUrl(0, 3)]), {"text", "link"}),
    (M(message="bax", entities=[MessageEntityTextUrl(0, 3, "https://x.com")]), {"text", "link"}),
    (M(message="t.me/kanal"), {"text", "link"}),
    (types.SimpleNamespace(**{**M(message="x https://x.com", photo=object()).__dict__, "media": MessageMediaWebPage()}),
     {"text", "link"}),                                                                          # link önizləməsi şəkil sayılmır
    (media(photo=object(), fwd_from=object()), {"photo", "forward"}),
    (M(message="x", via_bot_id=5), {"text", "inline"}),
])
def test_message_kinds(msg, expected):
    assert core.kinds_of(msg) == expected


# ───────────── vəziyyət ─────────────
def test_state_add_remove_and_persistence(monkeypatch):
    monkeypatch.setattr(core, "run_bg", lambda fn, *a, **kw: fn(*a, **kw))
    db = FakeKV()
    s = core.LockState(db)
    s.add(100, ["sticker", "gif"])
    s.add(100, ["link"], uid=ALI)
    assert s.locked(100, ALI) == {"sticker", "gif", "link"} and s.locked(100, VELI) == {"sticker", "gif"}
    assert s.blocked_kind(100, VELI, {"text", "link"}) is None and s.blocked_kind(100, ALI, {"text", "link"}) == "link"
    assert s.blocked_kind(100, VELI, {"sticker"}) == "sticker" and s.blocked_kind(999, ALI, {"sticker"}) is None
    assert s.remove(100, ["gif"]) == ["sticker"] and s.remove(100, None, uid=ALI) == []
    assert "100" in s.chats                                                                     # çatın hələ kilidi var
    again = core.LockState(db)                                                                  # restart: bazadan oxunur
    assert again.locked(100, ALI) == {"sticker"}
    again.remove(100, None)
    assert again.chats == {} and core.LockState(db).chats == {}                                 # boş çat silinir
    assert again.remove(555, ["sticker"]) == []                                                 # olmayan çat — xəta yox


def test_labels():
    assert core.label(["sticker"]) == "🎭 stiker"
    assert core.label(core.MEDIA) == "🔒 bütün media" and core.label(tuple(core.KINDS)) == "🔒 hər şey"
    assert core.label(list(core.MEDIA) + ["link"]).startswith("🔒 media + 🔗 link")
    assert core.label([]) == "—"


# ───────────── komandalar ─────────────
@pytest.mark.asyncio
async def test_lock_in_a_private_chat_blocks_that_person(env):
    fub, notes = env
    client = Client()
    await cmd(fub, "lock", ".lock sticker gif", client, private=True, chat=User(id=ALI, first_name="Ali"), chat_id=ALI)
    assert fub.state["lock"].locked(ALI, ALI) == {"sticker", "gif"} and "Bağlandı" in notes[-1]
    ev = await cmd(fub, "locks", ".locks", client, private=True, chat=User(id=ALI, first_name="Ali"), chat_id=ALI)
    assert "Hamı üçün: 🎭 stiker, 🎞 GIF" in ev.edits[-1]
    await cmd(fub, "unlock", ".unlock gif", client, private=True, chat=User(id=ALI, first_name="Ali"), chat_id=ALI)
    assert fub.state["lock"].locked(ALI, ALI) == {"sticker"} and "Açıldı" in notes[-1] and "Qalan: 🎭 stiker" in notes[-1]
    await cmd(fub, "unlock", ".unlock all", client, private=True, chat=User(id=ALI, first_name="Ali"), chat_id=ALI)
    assert fub.state["lock"].chats == {}
    ev = await cmd(fub, "locks", ".locks", client, private=True, chat=User(id=ALI, first_name="Ali"), chat_id=ALI)
    assert "heç nə bağlı deyil" in ev.edits[-1]


@pytest.mark.asyncio
async def test_bare_commands_explain_themselves(env):
    fub, notes = env
    ev = await cmd(fub, "lock", ".lock", Client(), private=True, chat=User(id=ALI, first_name="A"), chat_id=ALI)
    assert "heç nə bağlı deyil" in ev.edits[-1]
    await cmd(fub, "lock", ".lock bilinmir", Client(), private=True, chat=User(id=ALI, first_name="A"), chat_id=ALI)
    await cmd(fub, "unlock", ".unlock", Client(), private=True, chat=User(id=ALI, first_name="A"), chat_id=ALI)
    assert "Nəyi açım" in notes[-1] and fub.state["lock"].chats == {}


@pytest.mark.asyncio
async def test_group_lock_needs_delete_rights_and_can_target_a_person(env):
    fub, notes = env
    await cmd(fub, "lock", ".lock link", Client(can_delete=False), chat=group(), chat_id=555)
    assert "icazən yoxdur" in notes[-1] and fub.state["lock"].chats == {}
    ok = Client()
    await cmd(fub, "lock", ".lock link", ok, chat=group(), chat_id=555)
    assert fub.state["lock"].locked(555, ALI) == {"link"} and fub.state["lock"].chats["555"]["users"] == {}
    reply = types.SimpleNamespace(sender_id=ALI, get_sender=lambda: _user(ALI))
    await cmd(fub, "lock", ".lock sticker", ok, chat=group(), chat_id=555, reply=reply)
    st = fub.state["lock"]
    assert st.chats["555"]["users"] == {str(ALI): ["sticker"]} and st.locked(555, VELI) == {"link"}      # yalnız Ali üçün
    assert "Ali" in notes[-1] or "U20" in notes[-1]
    ev = await cmd(fub, "locks", ".locks", ok, chat=group(), chat_id=555)
    assert "Hamı üçün: 🔗 link" in ev.edits[-1] and "U20: 🎭 stiker" in ev.edits[-1]
    await cmd(fub, "unlock", ".unlock all", ok, chat=group(), chat_id=555, reply=reply)
    assert st.chats["555"]["users"] == {} and st.locked(555, VELI) == {"link"}                         # yalnız Alinin kilidi açıldı


async def _user(uid):
    return User(id=uid, first_name=f"U{uid}")


# ───────────── gələn mesajlar ─────────────
@pytest.mark.asyncio
async def test_locked_kind_is_deleted_for_both_sides_and_other_kinds_stay(env):
    fub, _ = env
    fub.state["lock"].add(100, ["sticker", "link"])
    h, c = raw(fub), Client()
    await h(Ev(c, media(sticker=object(), document=object()), mid=7))
    await h(Ev(c, M(message="salam"), mid=8))                                                  # mətn bağlı deyil
    await h(Ev(c, M(message="bax https://x.com"), mid=9))                                      # link bağlıdır
    await h(Ev(c, media(photo=object()), mid=10))
    assert c.deleted == [(100, [7], True), (100, [9], True)] and fub.state["lock"].deleted == 2


@pytest.mark.asyncio
async def test_self_other_chats_services_and_bots_are_never_touched(env):
    fub, _ = env
    fub.state["lock"].add(100, ["sticker"])
    h, c, st = raw(fub), Client(), media(sticker=object())
    await h(Ev(c, st, out=True))                                                               # öz mesajın
    await h(Ev(c, st, chat_id=200))                                                            # başqa çat
    await h(Ev(c, st, sender=777000))                                                          # Telegram xidməti
    await h(Ev(c, st, bot=True, sender=BOT))
    await h(Ev(c, st, sender=None))
    assert c.deleted == []


@pytest.mark.asyncio
async def test_edited_message_cannot_sneak_a_link_in(env):
    fub, _ = env
    fub.state["lock"].add(100, ["link"])
    assert {b.__class__.__name__ for b, _ in fub.raw_handlers} == {"NewMessage", "MessageEdited"}
    c = Client()
    await raw(fub, "MessageEdited")(Ev(c, M(message="indi redaktə: https://x.com")))
    assert c.deleted == [(100, [5], True)]


@pytest.mark.asyncio
async def test_group_wide_lock_spares_admins_but_a_personal_lock_does_not(env):
    fub, _ = env
    st = fub.state["lock"]
    st.add(555, ["sticker"])
    st.add(555, ["photo"], uid=ALI)
    h, c = raw(fub), Client(admins={ALI, VELI})
    s = media(sticker=object())
    await h(Ev(c, s, chat_id=555, sender=VELI, private=False, mid=1))                           # admin — toxunulmur
    await h(Ev(c, s, chat_id=555, sender=40, private=False, mid=2))                             # sadə üzv — silinir
    await h(Ev(c, media(photo=object()), chat_id=555, sender=ALI, private=False, mid=3))        # şəxsi kilid admin olsa da işləyir
    await h(Ev(c, s, chat_id=555, sender=ALI, private=False, mid=4))                            # admin + yalnız qrup kilidi
    assert [d[1] for d in c.deleted] == [[2], [3]]


@pytest.mark.asyncio
async def test_flood_wait_is_retried_when_short_and_skipped_when_long(env, monkeypatch):
    fub, _ = env
    fub.state["lock"].add(100, ["sticker"])
    slept = []

    async def fast(delay):
        slept.append(delay)
    monkeypatch.setattr(lock.asyncio, "sleep", fast)
    c = Client(flood=[FloodWaitError(request=None, capture=3)])
    await raw(fub)(Ev(c, media(sticker=object()), mid=1))
    assert slept == [4] and c.deleted == [(100, [1], True)]
    c2 = Client(flood=[FloodWaitError(request=None, capture=500)])
    await raw(fub)(Ev(c2, media(sticker=object()), mid=2))
    assert c2.deleted == [] and slept == [4]                                                   # uzun flood — gözlənmir


@pytest.mark.asyncio
async def test_optional_notice_is_sent_once_per_gap_and_default_is_silent(env, monkeypatch):
    fub, _ = env
    fub.state["lock"].add(100, ["sticker"])
    c = Client()
    await raw(fub)(Ev(c, media(sticker=object()), mid=1))
    assert c.sent == []                                                                        # standart — səssiz
    fub.db.kv_set("cmdopt", "lock:notice", "on")
    slept = []

    async def fast(delay):
        slept.append(delay)
    monkeypatch.setattr(lock.asyncio, "sleep", fast)
    await raw(fub)(Ev(c, media(sticker=object()), mid=2))
    await raw(fub)(Ev(c, media(sticker=object()), mid=3))                                      # eyni şəxs, 30 san. içində
    assert c.sent == [(100, "🔒 🎭 stiker bu söhbətdə bağlıdır.")] and slept == [lock.NOTICE_LIFETIME]
    assert len(c.deleted) == 3                                                                 # üçü də silinib


def test_the_notice_option_is_exposed_to_the_menu(env):
    fub, _ = env
    (opt,) = fub.state["cmd_options"]["lock"]
    assert opt["key"] == "notice" and opt["default"] == "off" and [c for c, _ in opt["choices"]] == ["off", "on"]


def test_commands_are_documented_for_the_guide():
    fub = fakes.FakeUB()
    helps = {}
    fub.command = lambda name, pattern=None, help=None, **kw: (lambda fn: (helps.__setitem__(name, (pattern, help)), fn)[1])
    fub.state, fub.db = {}, FakeKV()
    lock.register(fub)
    assert set(helps) == {"lock", "unlock", "locks"} and all(h for _, h in helps.values())
    assert re.match(helps["lock"][0], ".lock sticker") and not re.match(helps["lock"][0], ".locks")
    assert re.match(helps["locks"][0], ".locks") and not re.match(helps["locks"][0], ".lockss")


def test_each_event_type_has_its_own_function_so_the_host_attaches_both(env):
    """Host raw handler-ləri id(fn) ilə izləyir: eyni funksiya iki dekoratorla qeyd olunsa yalnız biri qoşulurdu."""
    fub, _ = env
    fns = [fn for _, fn in fub.raw_handlers]
    assert len(fns) == 2 and len({id(f) for f in fns}) == 2


def test_real_host_attaches_both_the_new_and_the_edited_message_handlers(monkeypatch):
    from unittest.mock import MagicMock
    import userbot.core.userbot_api as api
    monkeypatch.setattr(api, "get_udb", lambda: FakeKV())
    monkeypatch.setattr(core, "run_bg", lambda fn, *a, **kw: fn(*a, **kw))
    real = api.UB(MagicMock(creator_id=1))
    real._loading = "lock"
    lock.register(real)
    added = []
    real.client = types.SimpleNamespace(add_event_handler=lambda fn, ev: added.append((fn, ev)))
    real.attach_pending()
    kinds = sorted(ev.__class__.__name__ for fn, ev in added if fn.__name__ in ("on_new", "on_edited"))
    assert kinds == ["MessageEdited", "NewMessage"]


# ───────────── 🔘 .locks inline paneli ─────────────
import userbot.core.lock_card as card  # noqa: E402


class PanelStub:
    """Menyu / inline kartın əvəzi: Out kartı bu mesajı redaktə edir (bot.edit_message_text)."""
    iid, dead, shown = "iid-1", False, False

    def __init__(self):
        self.outs = []

    def nav_rows(self, refresh=False):
        return []


class CardClient(Client):
    def __init__(self, dialogs=(), **kw):
        super().__init__(**kw)
        self.dialogs = list(dialogs)

    async def iter_dialogs(self, limit=None):
        for d in self.dialogs:
            yield d

    async def get_entity(self, uid):
        if uid < 0:
            return Channel(id=abs(uid) % 10 ** 9, title=f"Qrup {uid}", photo=ChatPhotoEmpty(), date=fakes.T0, megagroup=True)
        return User(id=uid, first_name=f"U{uid}")


def dialog(did, name, kind="group", bot=False):
    ent = types.SimpleNamespace(bot=bot)
    return types.SimpleNamespace(id=did, name=name, entity=ent, is_group=kind == "group", is_user=kind == "user",
                                 is_channel=kind == "channel")


class CardBot:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, **kw):
        self.calls.append(kw)


@pytest.fixture
def cardenv(env):
    fub, notes = env
    fub.is_inline_disabled = lambda chat_id: False
    fub.bot = CardBot()
    return fub, fub.state["lock"], fub.bot


async def open_card(fub, state, client, chat_id=-1001, home=None):
    ev = types.SimpleNamespace(client=client, chat_id=chat_id, sender_id=ME, panel=PanelStub(), reply_to_msg_id=None,
                               id=None, is_private=False)
    return await card.open_card(fub, ev, state)


async def press(out, action):
    assert action in out.actions, f"düymə qeydə alınmayıb: {action}"
    await out.actions[action](out, types.SimpleNamespace())


def datas(out):
    return [b.callback_data for r in out.rows for b in r]


def btn_texts(out):
    return [b.text for r in out.rows for b in r]


@pytest.mark.asyncio
async def test_card_opens_in_the_menu_message_with_valid_buttons(cardenv):
    fub, state, bot = cardenv
    out = await open_card(fub, state, CardClient())
    assert out.mode == "inline" and bot.calls[-1]["inline_message_id"] == "iid-1"
    assert "Kilidlər" in out.text and "Qrup -1001" in out.text and "<i>(qrup)</i>" in out.text and "heç nə" in out.text
    ds = datas(out)
    assert len(ds) == len(set(ds)) and all(d.startswith("ubc:") and len(d.encode()) <= 64 for d in ds)
    assert {f"ubc:{out.tok}:k:{k}" for k in core.MEDIA} <= set(ds)
    assert {"▫️ 🎭 stiker", "▫️ 🎞 GIF"} <= set(btn_texts(out)) and "✅ 🖼 Media" in btn_texts(out)


@pytest.mark.asyncio
async def test_toggling_kinds_locks_and_unlocks_them(cardenv):
    fub, state, _ = cardenv
    out = await open_card(fub, state, CardClient())
    await press(out, "k:sticker")
    assert state.locked(-1001) == {"sticker"} and "🔒 🎭 stiker" in btn_texts(out) and "🔒 Bağlandı: 🎭 stiker" in out.text
    await press(out, "k:sticker")
    assert state.locked(-1001) == set() and "▫️ 🎭 stiker" in btn_texts(out) and "🔓 Açıldı" in out.text
    await press(out, "tab-o")                                                 # 💬 Digər tabı
    assert "✅ 💬 Digər" in btn_texts(out) and "▫️ 🔗 link" in btn_texts(out) and "▫️ 🎭 stiker" not in btn_texts(out)
    await press(out, "k:link")
    assert state.locked(-1001) == {"link"}


@pytest.mark.asyncio
async def test_presets(cardenv):
    fub, state, _ = cardenv
    out = await open_card(fub, state, CardClient())
    await press(out, "pm")
    assert state.locked(-1001) == set(core.MEDIA) and "Bütün media bağlandı" in out.text
    await press(out, "pa")
    assert state.locked(-1001) == set(core.KINDS)
    await press(out, "pn")
    assert state.chats == {} and "açıldı" in out.text


@pytest.mark.asyncio
async def test_group_without_delete_rights_cannot_be_locked(cardenv):
    fub, state, _ = cardenv
    out = await open_card(fub, state, CardClient(can_delete=False))
    assert "icazən yoxdur" in out.text
    await press(out, "k:sticker")
    await press(out, "pa")
    assert state.chats == {} and "⛔" in out.text
    private = await open_card(fub, state, CardClient(can_delete=False), chat_id=ALI)         # şəxsi çatda icazə tələb olunmur
    await press(private, "k:sticker")
    assert state.locked(ALI) == {"sticker"}


@pytest.mark.asyncio
async def test_pause_stops_deleting_without_losing_the_locks(cardenv):
    fub, state, _ = cardenv
    out = await open_card(fub, state, CardClient())
    assert "⏸ Fasilə" not in btn_texts(out)                                            # kilid yoxdursa fasilə düyməsi də yox
    await press(out, "k:sticker")
    assert "⏸ Fasilə" in btn_texts(out)
    await press(out, "pause")
    assert state.chats["-1001"]["paused"] and "▶️ Davam" in btn_texts(out) and "fasilədədir" in out.text
    assert state.blocked_kind(-1001, ALI, {"sticker"}) is None and state.locked(-1001) == {"sticker"}
    c = Client()
    await raw(fub)(Ev(c, media(sticker=object()), chat_id=-1001, private=False))
    assert c.deleted == []
    await press(out, "pause")
    assert state.blocked_kind(-1001, ALI, {"sticker"}) == "sticker"


@pytest.mark.asyncio
async def test_choosing_another_chat_or_group(cardenv):
    fub, state, _ = cardenv
    state.add(-2002, ["link"])
    client = CardClient([dialog(-3003, "Dost qrupu"), dialog(500, "Ali", "user"), dialog(-1004, "Kanal", "channel"),
                         dialog(600, "Robot", "user", bot=True), dialog(-2002, "İş qrupu")])
    out = await open_card(fub, state, client)
    await press(out, "chats")
    assert "Çat seç" in out.text and "🔒 Kilidlilər (1)" in out.text and "🔒 İş qrupu" in btn_texts(out)
    await press(out, "f:group")
    assert btn_texts(out).count("Dost qrupu") == 1 and "Kanal" not in " ".join(btn_texts(out)) and "Robot" not in " ".join(btn_texts(out))
    await press(out, "f:user")
    assert "Ali" in btn_texts(out) and "Dost qrupu" not in btn_texts(out)
    await press(out, "f:group")
    idx = btn_texts(out).index("Dost qrupu") - len(card.FILTERS) // 2 * 2                 # düymənin sırası (süzgəc sətirlərindən sonra)
    await press(out, "c:0")                                                              # siyahıdakı 1-ci qrup
    assert out.ctx["chat"] == -3003 and "Qrup -3003" in out.text and idx >= 0
    await press(out, "k:sticker")                                                        # kilid məhz seçilmiş çata düşür
    assert state.locked(-3003) == {"sticker"} and state.locked(-1001) == set()
    await press(out, "chats")
    await press(out, "f:here")                                                           # 📍 ilkin çata qayıt
    assert out.ctx["chat"] == -1001


@pytest.mark.asyncio
async def test_locked_filter_includes_chats_missing_from_the_dialog_list_and_pages(cardenv):
    fub, state, _ = cardenv
    state.add(-9999, ["gif"])
    dialogs = [dialog(-100 - i, f"Qrup {i:02d}") for i in range(11)]
    out = await open_card(fub, state, CardClient(dialogs))
    await press(out, "chats")
    assert "ID -9999" in " ".join(btn_texts(out)) or "-9999" in " ".join(btn_texts(out))
    await press(out, "f:group")
    assert "1/2" in btn_texts(out)
    await press(out, "pg+")
    assert "2/2" in btn_texts(out) and "Qrup 10" in btn_texts(out) and "Qrup 00" not in btn_texts(out)
    await press(out, "pg+")
    assert "2/2" in btn_texts(out)                                                      # son səhifədən kənara çıxmır
    await press(out, "c:0")                                                              # 2-ci səhifənin 1-ci qrupu
    assert out.ctx["chat"] == -100 - 8


@pytest.mark.asyncio
async def test_stale_pick_button_is_handled(cardenv):
    fub, state, _ = cardenv
    out = await open_card(fub, state, CardClient([dialog(-5, "Yeganə")]))
    await press(out, "chats")
    await press(out, "f:group")
    await press(out, "c:0")
    await press(out, "chats")
    out.ctx["page_items"] = []
    await press(out, "c:0")
    assert "Siyahı dəyişib" in out.text


@pytest.mark.asyncio
async def test_per_user_locks_page(cardenv):
    fub, state, _ = cardenv
    state.add(-1001, ["link"], uid=ALI)
    state.add(-1001, ["sticker", "gif"], uid=VELI)
    out = await open_card(fub, state, CardClient())
    assert "👥 Şəxs üzrə: 2 nəfər" in out.text and "👥 Şəxslər (2)" in btn_texts(out)
    await press(out, "users")
    assert "U20: 🔗 link" in out.text and "U30: 🎭 stiker, 🎞 GIF" in out.text
    assert f"ubc:{out.tok}:ud:{ALI}" in datas(out)
    await press(out, f"ud:{ALI}")
    assert str(ALI) not in state.chats["-1001"]["users"] and "U20" not in out.text
    await press(out, f"ud:{VELI}")
    assert state.chats == {} and "Kilidlər" in out.text and out.ctx["view"] == "chat"      # son şəxs açılınca çata qayıdır


@pytest.mark.asyncio
async def test_settings_toggles_and_wipe(cardenv):
    fub, state, _ = cardenv
    state.add(-1001, ["sticker"])
    state.add(555, ["link"])
    out = await open_card(fub, state, CardClient())
    await press(out, "settings")
    assert "Kilid ayarları" in out.text and "hər iki tərəfdən" in out.text and "Botların mesajı da silinir: xeyr" in out.text
    for action, key, expected in (("cfg-revoke", "revoke", False), ("cfg-admins", "admins", False), ("cfg-bots", "bots", True),
                                  ("cfg-enabled", "enabled", False)):
        await press(out, action)
        assert state.cfg[key] is expected and "Ayar dəyişdi" in out.text
    assert "yalnız səndən" in out.text and "ümumilikdə söndürülüb" in out.text
    await press(out, "cfg-notice")
    assert fub.db.kv_get("cmdopt", "lock:notice") == "on" and "bildiriş: bəli" in out.text
    await press(out, "cfg-notice")
    assert fub.db.kv_get("cmdopt", "lock:notice") == "off"
    await press(out, "wipe")
    assert "Bütün kilidlər silinsin" in out.text and len(state.chats) == 2                  # təsdiqdən əvvəl silinmir
    await press(out, "settings")
    await press(out, "wipe")
    await press(out, "wipe-yes")
    assert state.chats == {} and "2 çatın bütün kilidləri silindi" in out.text and out.ctx["view"] == "chat"
    assert state.cfg["bots"] is True                                                       # ümumi ayarlar qalır


@pytest.mark.asyncio
async def test_every_view_has_unique_short_callbacks(cardenv):
    fub, state, _ = cardenv
    state.add(-1001, ["sticker"], uid=ALI)
    state.add(-1001, ["link"])
    out = await open_card(fub, state, CardClient([dialog(-100 - i, f"Qrup {i}") for i in range(12)]))
    for step in (None, "tab-o", "tab-m", "chats", "f:group", "pg+", "f:user", "f:locked", "back", "users", "back", "settings", "wipe"):
        if step:
            await press(out, step)
        ds = datas(out)
        assert len(ds) == len(set(ds)) and all(len(d.encode()) <= 64 for d in ds), step


@pytest.mark.asyncio
async def test_refresh_reloads_the_dialog_list(cardenv):
    fub, state, _ = cardenv
    client = CardClient([dialog(-1, "Birinci")])
    out = await open_card(fub, state, client)
    await press(out, "chats")
    await press(out, "f:group")
    client.dialogs.append(dialog(-2, "İkinci"))
    await out.refresh(out)
    assert "İkinci" in btn_texts(out)


# ───────────── ayarlar silmə qaydalarına təsir edir ─────────────
@pytest.mark.asyncio
async def test_settings_change_what_the_handler_does(env):
    fub, _ = env
    st = fub.state["lock"]
    st.add(100, ["sticker"])
    st.add(555, ["sticker"])
    h, c = raw(fub), Client(admins={VELI})
    s = media(sticker=object())
    await h(Ev(c, s, mid=1))
    st.set_cfg("revoke", False)
    await h(Ev(c, s, mid=2))                                                           # şəxsi çat: yalnız səndən
    await h(Ev(c, s, chat_id=555, sender=40, private=False, mid=3))                    # qrup: həmişə hamı üçün
    assert [d[2] for d in c.deleted] == [True, False, True]
    await h(Ev(c, s, chat_id=555, sender=VELI, private=False, mid=4))                  # admin toxunulmaz
    st.set_cfg("admins", False)
    await h(Ev(c, s, chat_id=555, sender=VELI, private=False, mid=5))                  # ayar: adminlər də silinir
    n = len(c.deleted)
    await h(Ev(c, s, bot=True, sender=BOT, mid=6))
    assert len(c.deleted) == n                                                         # botlar standart toxunulmazdır
    st.set_cfg("bots", True)
    await h(Ev(c, s, bot=True, sender=BOT, mid=7))
    assert len(c.deleted) == n + 1
    st.set_cfg("enabled", False)
    await h(Ev(c, s, mid=8))
    assert len(c.deleted) == n + 1                                                     # ümumi söndürülüb


def test_new_settings_survive_a_restart(monkeypatch):
    monkeypatch.setattr(core, "run_bg", lambda fn, *a, **kw: fn(*a, **kw))
    db = FakeKV()
    s = core.LockState(db)
    s.add(100, ["sticker"])
    s.pause(100, True)
    s.set_cfg("revoke", False)
    s.set_cfg("bots", True)
    assert not s.set_cfg("yoxdur", True) and s.pause(555, True) is None                 # olmayan ayar / çat — xəta yox
    again = core.LockState(db)
    assert again.chats["100"]["paused"] and again.cfg == {"enabled": True, "revoke": False, "admins": True, "bots": True}
    again.remove(100, None)                                                            # kilid qalmadı → fasilə bayrağı da itir
    assert again.chats == {}
    old_format = FakeKV()
    old_format.kv_set("lock", "cfg", {"chats": {"7": {"all": ["gif"], "users": {}}}})
    legacy = core.LockState(old_format)                                                # köhnə format oxunur
    assert legacy.locked(7) == {"gif"} and legacy.cfg == core.DEFAULT_CFG


def test_locks_command_help_mentions_the_panel(env):
    fub, _ = env
    assert fub.handlers["locks"][0] == r"^\.locks$"
