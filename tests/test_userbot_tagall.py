"""📢 Test suite for tagall userbot plugin."""
import pytest
from telethon.errors import ChatAdminRequiredError, ChatWriteForbiddenError
from telethon.tl.types import Channel, ChannelParticipantsAdmins, ChatPhotoEmpty, User

from tests import userbot_fakes as fakes
import userbot.plugins.tagall as tagall


def make_group(cid=555, title="Test Group"):
    return Channel(id=cid, title=title, photo=ChatPhotoEmpty(), date=fakes.T0, megagroup=True)


class FakeTagClient:
    def __init__(self, my_id=1, participants=None, admin_participants=None, raise_on_iter=None, raise_on_send=None):
        self.me = User(id=my_id, first_name="Self", is_self=True)
        self.participants = participants or []
        self.admin_participants = admin_participants or []
        self.raise_on_iter = raise_on_iter
        self.raise_on_send = raise_on_send
        self.sent_messages = []

    async def get_me(self):
        return self.me

    async def iter_participants(self, chat, filter=None):
        if self.raise_on_iter:
            raise self.raise_on_iter
        target_list = self.admin_participants if filter is ChannelParticipantsAdmins else self.participants
        for p in target_list:
            yield p

    async def send_message(self, chat, text, reply_to=None, parse_mode="html", link_preview=False):
        if self.raise_on_send:
            raise self.raise_on_send
        self.sent_messages.append({
            "chat": chat,
            "text": text,
            "reply_to": reply_to,
            "parse_mode": parse_mode,
        })
        return True


@pytest.fixture
def ub():
    u = fakes.FakeUB()
    tagall.register(u)
    return u


def test_clean_display_name():
    u1 = User(id=10, first_name="Ali", last_name="Valiyev")
    assert tagall.clean_display_name(u1) == "Ali Valiyev"

    u2 = User(id=11, first_name="", last_name="", username="ali_v")
    assert tagall.clean_display_name(u2) == "@ali_v"

    u3 = User(id=12, first_name="A" * 30)
    res = tagall.clean_display_name(u3, max_len=10)
    assert len(res) == 10 and res.endswith("…")

    u4 = User(id=13, first_name="", last_name="", username="")
    assert tagall.clean_display_name(u4) == "İstifadəçi"


def test_build_tag_message():
    m1 = User(id=101, first_name="Murad")
    m2 = User(id=102, first_name="Leyla")

    # Standart açıq tag
    text = tagall.build_tag_message([m1, m2], custom_text="İclas başladı")
    assert "📢 <b>İclas başladı</b>" in text
    assert '<a href="tg://user?id=101">Murad</a>' in text
    assert '<a href="tg://user?id=102">Leyla</a>' in text

    # Gizli tag (-h)
    hidden_text = tagall.build_tag_message([m1, m2], custom_text="Gizli bildiriş", hidden=True)
    assert "📢 <b>Gizli bildiriş</b>" in hidden_text
    assert f'<a href="tg://user?id=101">{tagall.ZERO_WIDTH_JOINER}</a>' in hidden_text

    # Admin rejimi
    admin_text = tagall.build_tag_message([m1], is_admin_mode=True)
    assert "Adminlərin nəzərinə!" in admin_text
    assert "🛡 <b>Adminlər (1):</b>" in admin_text


@pytest.mark.asyncio
async def test_tagall_private_chat(ub):
    client = FakeTagClient()
    ev = await fakes.run_command(ub, "tagall", ".tagall", client, private=True)
    assert any("yalnız <b>qruplarda</b>" in edit for edit in ev.edits)
    assert len(client.sent_messages) == 0


@pytest.mark.asyncio
async def test_tagall_single_message_success(ub):
    u1 = User(id=10, first_name="User1")
    u2 = User(id=20, first_name="User2")
    # bot və silinmiş hesablar nəzərə alınmamalıdır
    u_bot = User(id=99, first_name="Bot", bot=True)
    u_del = User(id=88, first_name="Deleted", deleted=True)

    client = FakeTagClient(my_id=1, participants=[u1, u2, u_bot, u_del])
    group_chat = make_group()

    ev = await fakes.run_command(ub, "tagall", ".tagall Hamı diqqət yetirsin!", client, chat=group_chat)

    # 1 tək mesaj göndərilib
    assert len(client.sent_messages) == 1
    sent = client.sent_messages[0]
    assert "Hamı diqqət yetirsin!" in sent["text"]
    assert '<a href="tg://user?id=10">User1</a>' in sent["text"]
    assert '<a href="tg://user?id=20">User2</a>' in sent["text"]
    assert "Bot" not in sent["text"]
    assert "Deleted" not in sent["text"]

    # Əmr mesajı silinib
    assert ev.deleted is True


@pytest.mark.asyncio
async def test_tagall_reply_preserved(ub):
    u1 = User(id=10, first_name="User1")
    client = FakeTagClient(my_id=1, participants=[u1])
    group_chat = make_group()
    reply_target = fakes.msg(id=777)

    ev = await fakes.run_command(
        ub, "tagall", ".tagall", client, chat=group_chat, reply=reply_target
    )

    assert len(client.sent_messages) == 1
    assert client.sent_messages[0]["reply_to"] == 777
    assert ev.deleted is True


@pytest.mark.asyncio
async def test_tagall_admins(ub):
    admin1 = User(id=201, first_name="Admin1")
    client = FakeTagClient(my_id=1, admin_participants=[admin1])
    group_chat = make_group()

    # .tagadmins komandası
    await fakes.run_command(ub, "tagadmins", ".tagadmins Vacib iclas", client, chat=group_chat)
    assert len(client.sent_messages) == 1
    assert "Adminlər (1)" in client.sent_messages[0]["text"]
    assert '<a href="tg://user?id=201">Admin1</a>' in client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_tagall_hidden_mode(ub):
    u1 = User(id=10, first_name="User1")
    client = FakeTagClient(my_id=1, participants=[u1])
    group_chat = make_group()

    await fakes.run_command(ub, "tagall", ".tagall -h Gizli xəbərdarlıq", client, chat=group_chat)
    assert len(client.sent_messages) == 1
    assert tagall.ZERO_WIDTH_JOINER in client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_tagall_chat_admin_required_error(ub):
    client = FakeTagClient(my_id=1, raise_on_iter=ChatAdminRequiredError(request=None))
    group_chat = make_group()

    ev = await fakes.run_command(ub, "tagall", ".tagall", client, chat=group_chat)
    assert any("gizlədilib" in edit for edit in ev.edits)
    assert len(client.sent_messages) == 0


@pytest.mark.asyncio
async def test_tagall_send_forbidden(ub):
    u1 = User(id=10, first_name="User1")
    client = FakeTagClient(my_id=1, participants=[u1], raise_on_send=ChatWriteForbiddenError(request=None))
    group_chat = make_group()

    ev = await fakes.run_command(ub, "tagall", ".tagall", client, chat=group_chat)
    assert any("hüququnuz yoxdur" in edit for edit in ev.edits)
    assert ev.deleted is False
