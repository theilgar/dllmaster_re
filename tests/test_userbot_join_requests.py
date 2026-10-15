"""📨 .istek — qrupa gələn qoşulma istəklərinə baxmaq, rədd və qəbul etmək (saxta Telethon client ilə)."""
import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest
from telethon.errors import ChatAdminRequiredError, FloodWaitError, RPCError
from telethon.tl.types import Channel, Chat, ChatInviteImporter, ChatPhotoEmpty, InputUserEmpty, User

import userbot.plugins.join_requests as jr

T0 = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)


def channel():
    return Channel(id=1, title="Test <qrup>", photo=ChatPhotoEmpty(), date=T0, megagroup=True)


class FakeClient:
    """Gözləyən istəklər: ən köhnədən yeniyə; GetChatInviteImporters səhifələməsi və Hide* sorğuları."""

    def __init__(self, n=0, *, invite=True, creator=False, bad=(), flood_once=False):
        self.pending = [(1000 + i, T0 + timedelta(minutes=i)) for i in range(n)]
        self.users = {uid: User(id=uid, first_name=f"U{uid}", username=f"user{uid}" if uid % 2 else None, access_hash=1)
                      for uid, _ in self.pending}
        self.perms = types.SimpleNamespace(is_creator=creator, invite_users=invite)
        self.log, self.bad, self.flood_once = [], set(bad), flood_once

    async def get_permissions(self, chat, who):
        return self.perms

    async def __call__(self, req):
        name = type(req).__name__
        self.log.append(name)
        if name == "GetChatInviteImportersRequest":
            assert req.requested is True
            rows = self.pending
            if not isinstance(req.offset_user, InputUserEmpty):
                after = (req.offset_date, req.offset_user.id)
                rows = [r for r in rows if (r[1], r[0]) > after]
            page = rows[:req.limit]
            imps = [ChatInviteImporter(user_id=uid, date=d, requested=True, about="salam" if uid == 1001 else None)
                    for uid, d in page]
            return types.SimpleNamespace(count=len(self.pending), importers=imps, users=[self.users[i.user_id] for i in imps])
        if name == "HideAllChatJoinRequestsRequest":
            if self.flood_once:
                self.flood_once = False
                raise FloodWaitError(request=None, capture=0)
            self.last_all = req.approved
            self.pending.clear()
            return None
        if name == "HideChatJoinRequestRequest":
            uid = req.user_id.id
            if uid in self.bad:
                raise RPCError(request=None, message="HIDE_REQUESTER_MISSING", code=400)
            self.pending = [r for r in self.pending if r[0] != uid]
            self.decided = getattr(self, "decided", []) + [(uid, req.approved)]
            return None
        raise AssertionError(name)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(jr, "PAUSE", 0)
    monkeypatch.setattr(jr, "box_text", lambda t: t)           # kod bloku ayrıca sınanır (test_output_follows_ui_box_style)


# ───────────────────────── saf məntiq ─────────────────────────
def test_parse_action_words():
    assert jr.parse_action("sil") is False and jr.parse_action("Rədd") is False and jr.parse_action("reject") is False
    assert jr.parse_action("qebul") is True and jr.parse_action("qəbul") is True and jr.parse_action("ok") is True
    assert jr.parse_action("") is None and jr.parse_action(None) is None and jr.parse_action("siyahı") is None
    assert jr.parse_action("başqa") is ...


async def test_rights_check():
    assert "superqrup" in await jr.check_rights(FakeClient(), Chat(id=2, title="g", photo=ChatPhotoEmpty(),
                                                                     participants_count=3, date=T0, version=1))
    assert "icazən yoxdur" in await jr.check_rights(FakeClient(invite=False), channel())
    assert await jr.check_rights(FakeClient(invite=True), channel()) is None
    assert await jr.check_rights(FakeClient(invite=False, creator=True), channel()) is None


async def test_iter_requests_pages_through_everything_oldest_first():
    c = FakeClient(250)
    got = [imp.user_id async for _, imp in jr.iter_requests(c, channel(), 1000)]
    assert got == [1000 + i for i in range(250)]                       # 100 + 100 + 50, təkrarsız, sıralı
    assert c.log.count("GetChatInviteImportersRequest") == 3
    assert [imp.user_id async for _, imp in jr.iter_requests(FakeClient(250), channel(), 120)] == [1000 + i for i in range(120)]
    assert [x async for x in jr.iter_requests(FakeClient(0), channel(), 10)] == []


async def test_list_requests_and_rendering_is_html_safe():
    total, items = await jr.list_requests(FakeClient(25), channel(), 10)
    assert total == 25 and len(items) == 10
    text = jr.render_list("Test <qrup>", total, items)
    assert "gözləyən istək: <b>25</b>" in text and "Test &lt;qrup&gt;" in text
    assert "@user1001" in text and "salam" in text and "daha 15 nəfər" in text
    assert "<a " not in text                                           # blokda link işləmir
    assert ".istek sil" in text and ".istek qebul" in text
    assert "yoxdur" in jr.render_list("G", *await jr.list_requests(FakeClient(0), channel()))


async def test_resolve_all_uses_single_request_and_survives_flood():
    c = FakeClient(5)
    c.flood_once = True
    msgs = []

    async def report(t, final):
        msgs.append(t)

    orig = jr.asyncio.sleep
    jr.asyncio.sleep = lambda s: orig(0)                                # FloodWait gözləməsini qısalt
    try:
        await jr.resolve_all(c, channel(), True, report)
    finally:
        jr.asyncio.sleep = orig
    assert c.last_all is True and not c.pending and any("limiti" in m for m in msgs)
    assert c.log.count("HideAllChatJoinRequestsRequest") == 2           # flood-dan sonra təkrar
    c2 = FakeClient(3)
    await jr.resolve_all(c2, channel(), False)
    assert c2.last_all is False


async def test_resolve_some_handles_oldest_n_and_counts_failures():
    c = FakeClient(10, bad={1002})
    msgs = []

    async def report(t, final):
        msgs.append(t)

    done, failed = await jr.resolve_some(c, channel(), True, 5, report)
    assert (done, failed) == (4, 1)                                    # 1000..1004 cəhd; 1002 alınmadı
    assert [u for u, _ in c.decided] == [1000, 1001, 1003, 1004] and all(a is True for _, a in c.decided)
    assert len(c.pending) == 6 and c.pending[0][0] == 1002             # alınmayan qalır, qalanlar toxunulmaz
    d, f = await jr.resolve_some(FakeClient(3), channel(), False, 50, report)
    assert (d, f) == (3, 0)


async def test_resolve_some_stops_on_cancel_and_reports_admin_errors():
    c = FakeClient(5)
    stopped = {"n": 0}

    def is_stopped():
        stopped["n"] += 1
        return stopped["n"] > 2

    with pytest.raises(asyncio.CancelledError):
        await jr.resolve_some(c, channel(), True, 5, lambda *a: asyncio.sleep(0), is_stopped)
    assert len(c.decided) == 2

    class NoRights(FakeClient):
        async def __call__(self, req):
            if type(req).__name__ == "HideChatJoinRequestRequest":
                raise ChatAdminRequiredError(request=None)
            return await super().__call__(req)

    with pytest.raises(ChatAdminRequiredError):
        await jr.resolve_some(NoRights(3), channel(), True, 3, lambda *a: asyncio.sleep(0))


# ───────────────────────── komandanın özü ─────────────────────────
class FakeUB:
    def __init__(self):
        self.handlers, self.tracked = {}, []

    def command(self, name, pattern=None, help=None, **kw):
        def deco(fn):
            self.handlers[name] = (pattern, fn)
            return fn
        return deco

    def track(self, chat_id, kind, task):
        self.tracked.append(task)


class FakeEvent:
    def __init__(self, client, chat):
        self.client, self._chat, self.chat_id, self.edits = client, chat, 77, []
        self.pattern_match = None

    async def get_chat(self):
        return self._chat

    async def edit(self, text, **kw):
        self.edits.append(text)


async def run_command(ub, client, text, chat=None):
    import re
    pattern, fn = ub.handlers["istek"]
    ev = FakeEvent(client, chat or channel())
    ev.pattern_match = re.match(pattern, text)
    assert ev.pattern_match, f"pattern uyğun gəlmədi: {text}"
    await fn(ev)
    for t in ub.tracked:
        await t
    ub.tracked.clear()
    return ev


@pytest.fixture
def ub():
    u = FakeUB()
    jr.register(u)
    return u


def test_command_pattern_accepts_both_spellings():
    import re
    pat = r"^\.ist[eə]k(?:\s+(\S+))?(?:\s+(\d+))?$"
    ub_ = FakeUB()
    jr.register(ub_)
    assert ub_.handlers["istek"][0] == pat
    for text, g1, g2 in ((".istek", None, None), (".istək", None, None), (".istek sil", "sil", None),
                         (".istek qebul 20", "qebul", "20"), (".istək qəbul 5", "qəbul", "5"), (".istek 30", "30", None)):
        m = re.match(pat, text)
        assert m and (m.group(1), m.group(2)) == (g1, g2), text
    assert not re.match(pat, ".istekler") and not re.match(pat, ".isteksil")


async def test_command_list(ub):
    ev = await run_command(ub, FakeClient(12), ".istek")
    assert "gözləyən istək: <b>12</b>" in ev.edits[-1] and "daha 2 nəfər" in ev.edits[-1]
    ev = await run_command(ub, FakeClient(12), ".istek 3")
    assert "daha 9 nəfər" in ev.edits[-1]
    ev = await run_command(ub, FakeClient(0), ".istek")
    assert "gözləyən istək yoxdur" in ev.edits[-1]


async def test_command_decline_all_and_approve_all(ub):
    c = FakeClient(40)
    ev = await run_command(ub, c, ".istek sil")
    assert c.last_all is False and not c.pending
    assert "40 istək rədd edildi" in ev.edits[-1] and "Qalan gözləyən: 0" in ev.edits[-1]
    c = FakeClient(7)
    ev = await run_command(ub, c, ".istək qəbul")
    assert c.last_all is True and "7 istək qəbul edildi" in ev.edits[-1]


async def test_command_limited_and_empty(ub):
    c = FakeClient(10, bad={1001})
    ev = await run_command(ub, c, ".istek qebul 4")
    assert "3 istək qəbul edildi" in ev.edits[-1] and "alınmadı: 1" in ev.edits[-1] and "Qalan gözləyən: 7" in ev.edits[-1]
    assert [u for u, _ in c.decided] == [1000, 1002, 1003]
    ev = await run_command(ub, FakeClient(0), ".istek sil")
    assert "gözləyən istək yoxdur" in ev.edits[-1]


async def test_command_guards_and_errors(ub):
    ev = await run_command(ub, FakeClient(3, invite=False), ".istek sil")
    assert "icazən yoxdur" in ev.edits[-1]
    ev = await run_command(ub, FakeClient(3), ".istek sil", chat=Chat(id=2, title="g", photo=ChatPhotoEmpty(),
                                                                       participants_count=3, date=T0, version=1))
    assert "superqrup" in ev.edits[-1]
    ev = await run_command(ub, FakeClient(3), ".istek nə")
    assert "İstifadə" in ev.edits[-1]

    class Boom(FakeClient):
        async def __call__(self, req):
            raise RuntimeError("şəbəkə <xətası>")

    ev = await run_command(ub, Boom(3), ".istek sil")
    assert "❌" in ev.edits[-1] and "&lt;xətası&gt;" in ev.edits[-1]


@pytest.mark.parametrize("style,open_tag", [("box", "<pre>"), ("script", "<blockquote expandable>"), ("quote", "<blockquote>")])
async def test_output_follows_ui_box_style(monkeypatch, style, open_tag):
    """Siyahı və yekun nəticə /ui → Görünüş ayarına görə kod blokunda: başlıq qalın, sətirlər blokun içində."""
    import common.menu as menu
    from userbot.core.userbot_api import box_text
    from bot.plugins import compact_ui_plugin as cui

    monkeypatch.setattr(jr, "box_text", box_text)
    monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", style)
    monkeypatch.setattr(cui, "_on", lambda key: True)
    ub = FakeUB()
    jr.register(ub)

    ev = await run_command(ub, FakeClient(12), ".istek")
    out = ev.edits[-1]
    assert out.startswith("<b>📨 Test &lt;qrup&gt; — gözləyən istək: 12</b>") and open_tag in out
    block = out[out.index(open_tag):]
    assert "1. U1000" in block and "@user1001" in block and "daha 2 nəfər" in block and ".istek sil" in block
    assert "<a " not in out                                            # bütün sətirlər blokdadır, sıra pozulmur

    ev = await run_command(ub, FakeClient(7), ".istek sil")
    out = ev.edits[-1]
    assert out.startswith("<b>✅ Test &lt;qrup&gt;</b>") and open_tag in out
    assert "7 istək rədd edildi" in out[out.index(open_tag):] and "Qalan gözləyən: 0" in out

    ev = await run_command(ub, FakeClient(3, invite=False), ".istek sil")      # qısa xəbərdarlıqlar blok olmur
    assert "<pre" not in ev.edits[-1] and "icazən yoxdur" in ev.edits[-1]


def test_box_text_returns_text_unchanged_when_disabled_or_failing(monkeypatch):
    from bot.plugins import compact_ui_plugin as cui
    from userbot.core.userbot_api import box_text

    monkeypatch.setattr(cui, "_on", lambda key: False)
    assert box_text("başlıq\nsətir") == "başlıq\nsətir"
    monkeypatch.setattr(cui, "_on", lambda key: (_ for _ in ()).throw(RuntimeError("baza yoxdur")))
    assert box_text("başlıq\nsətir") == "başlıq\nsətir"
