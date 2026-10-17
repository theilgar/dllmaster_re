"""🕵️ Test suite for anonim userbot plugin."""
import types
import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty, DocumentAttributeAudio, User

from tests import userbot_fakes as fakes
import userbot.plugins.anonim as anonim


class FakeAnonimClient:
    def __init__(self, my_id=1, messages=None, photos_count=3, is_admin=False):
        self.me = User(id=my_id, first_name="Self", is_self=True)
        self.messages = messages or []
        self.photos = [
            types.SimpleNamespace(id=100 + i, access_hash=200 + i, file_reference=b"ref")
            for i in range(photos_count)
        ]
        self.deleted_chunks = []
        self.deleted_photo_chunks = []
        self.updated_profile = []
        self.sent_messages = []
        self.is_admin = is_admin

    async def get_me(self):
        return self.me

    async def get_permissions(self, chat, user):
        return types.SimpleNamespace(
            is_creator=self.is_admin,
            delete_messages=self.is_admin,
            is_admin=self.is_admin,
        )

    async def get_profile_photos(self, who, limit=None):
        assert who == "me"
        return self.photos

    async def iter_messages(self, chat_id, max_id=None, min_id=None, limit=None):
        for m in self.messages:
            if max_id is not None and m.id >= max_id:
                continue
            if min_id is not None and m.id <= min_id:
                continue
            yield m

    async def delete_messages(self, chat_id, chunk, revoke=True):
        self.deleted_chunks.append(list(chunk))

    async def send_message(self, target, text, parse_mode="html", link_preview=False):
        self.sent_messages.append({"target": target, "text": text})

    async def __call__(self, req):
        name = type(req).__name__
        if name == "DeletePhotosRequest":
            self.deleted_photo_chunks.append(req.id)
            self.photos.clear()
            return True
        if name == "UpdateProfileRequest":
            self.updated_profile.append(req)
            return True
        return True


@pytest.fixture
def ub():
    u = fakes.FakeUB()
    anonim.register(u)
    return u


def make_msg(mid, out=True, sender_id=1, audio=None, voice=None, document=None):
    return types.SimpleNamespace(
        id=mid,
        out=out,
        sender_id=sender_id,
        audio=audio,
        voice=voice,
        document=document,
    )


def test_is_song_or_audio():
    # Audio obyekti
    m1 = make_msg(1, audio=types.SimpleNamespace())
    assert anonim.is_song_or_audio(m1) is True

    # Voice obyekti
    m2 = make_msg(2, voice=types.SimpleNamespace())
    assert anonim.is_song_or_audio(m2) is True

    # Audio mime-type
    doc_audio = types.SimpleNamespace(mime_type="audio/mpeg", attributes=[])
    m3 = make_msg(3, document=doc_audio)
    assert anonim.is_song_or_audio(m3) is True

    # DocumentAttributeAudio
    doc_attr = types.SimpleNamespace(mime_type="application/octet-stream", attributes=[DocumentAttributeAudio(duration=180)])
    m4 = make_msg(4, document=doc_attr)
    assert anonim.is_song_or_audio(m4) is True

    # Adi mətn mesajı
    m5 = make_msg(5)
    assert anonim.is_song_or_audio(m5) is False


@pytest.mark.asyncio
async def test_delete_all_profile_photos():
    client = FakeAnonimClient(photos_count=3)
    count = await anonim.delete_all_profile_photos(client)
    assert count == 3
    assert len(client.deleted_photo_chunks) == 1
    assert len(client.photos) == 0


@pytest.mark.asyncio
async def test_clear_profile_bio():
    client = FakeAnonimClient()
    ok = await anonim.clear_profile_bio(client)
    assert ok is True
    assert len(client.updated_profile) == 1
    assert client.updated_profile[0].about == ""


@pytest.mark.asyncio
async def test_delete_messages_top_down_order():
    # Sırasız gələn ID-lər
    ids = [5, 20, 15, 100, 50, 1]
    client = FakeAnonimClient()

    # anonim.DELETE_CHUNK = 10 olduğu üçün 6 element tək partiya ilə silinəcək
    deleted = await anonim.delete_messages_top_down(client, chat_id=123, ids=ids)
    assert deleted == 6
    # Ən sonuncudan (100) ən əvvələ (1) doğru silinməlidir
    assert client.deleted_chunks[0] == [100, 50, 20, 15, 5, 1]


@pytest.mark.asyncio
async def test_anonim_command_deletes_songs_latest_first(ub):
    # Çatda mesajlar: botun mahnıları (out=False) və istifadəçinin axtarışları (out=True)
    doc_audio = types.SimpleNamespace(mime_type="audio/mp3", attributes=[])
    msgs = [
        make_msg(105, out=True, sender_id=1),                  # Ən sonuncu öz mesajımız
        make_msg(104, out=False, sender_id=99, document=doc_audio), # Botun sonuncu mahnısı
        make_msg(103, out=True, sender_id=1),                  # Əvvəlki mesaj
        make_msg(102, out=False, sender_id=99, document=doc_audio), # Botun köhnə mahnısı
        make_msg(101, out=True, sender_id=1),                  # Ən başdakı mesaj
    ]

    # Şəxsi çat (User obyekti)
    private_chat = User(id=99, first_name="MusicBot", bot=True)
    client = FakeAnonimClient(my_id=1, messages=msgs, photos_count=2)

    ev = await fakes.run_command(
        ub, "anonim", ".anonim", client, chat=private_chat, chat_id=99, msg_id=110, private=True
    )

    # 1. .anonim komandası dərhal silinib
    assert ev.deleted is True

    # 2. Çatda heç bir bildiriş qalmayıb
    assert len(ev.edits) == 0

    # 3. Mesajlar və mahnılar sonuncudan başa doğru silinib: 105 -> 104 -> 103 -> 102 -> 101
    all_deleted = [mid for chunk in client.deleted_chunks for mid in chunk]
    assert all_deleted == [105, 104, 103, 102, 101]

    # 4. Şəxsi Saved Messages-ə hesabat göndərilib
    assert len(client.sent_messages) == 1
    assert client.sent_messages[0]["target"] == "me"
    assert "sonuncudan əvvələ doğru" in client.sent_messages[0]["text"]
    assert "Silinən mahnı və mesajlar:</b> 5" in client.sent_messages[0]["text"]

    # 5. PP və Bio təmizlənib
    assert len(client.deleted_photo_chunks) == 1
    assert client.updated_profile[0].about == ""


@pytest.mark.asyncio
async def test_anonim_strictly_single_pattern(ub):
    pattern, _ = ub.handlers["anonim"]
    import re
    assert re.match(pattern, ".anonim") is not None
    assert re.match(pattern, ".anonim all") is None
