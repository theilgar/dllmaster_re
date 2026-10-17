"""🗑 Test suite for antidelete userbot plugin."""
import asyncio
import os
import time
import types
import pytest
from telethon.tl.types import User, Channel, ChatPhotoEmpty

from tests import userbot_fakes as fakes
import userbot.plugins.antidelete as antidelete


class FakeUDB:
    def __init__(self, saved=None):
        self.d = {(antidelete.NS, antidelete.KEY): saved} if saved else {}

    def kv_get(self, ns, key, default=None):
        return self.d.get((ns, key), default)

    def kv_set(self, ns, key, value):
        self.d[(ns, key)] = value


class FakeClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_files = []

    async def send_message(self, target, text, parse_mode="html", link_preview=False):
        self.sent_messages.append({
            "target": target,
            "text": text,
            "parse_mode": parse_mode,
        })
        return True

    async def send_file(self, target, file, caption=None, parse_mode="html"):
        self.sent_files.append({
            "target": target,
            "file": file,
            "caption": caption,
            "parse_mode": parse_mode,
        })
        return True


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    async def fake_temp(event, text, delay=2):
        await antidelete.safe_edit(event, text)

    monkeypatch.setattr(antidelete, "temp_edit", fake_temp)
    monkeypatch.setattr(antidelete, "run_bg", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(antidelete, "get_udb", lambda: FakeUDB())
    real_sleep = asyncio.sleep
    monkeypatch.setattr(antidelete.asyncio, "sleep", lambda s, *a, **k: real_sleep(0, *a, **k))
    # Keşləri hər testdən qabaq təmizləyirik
    antidelete._msg_cache.clear()
    antidelete._mid_to_chat.clear()


@pytest.fixture
def ub():
    u = fakes.FakeUB()
    u._udb = FakeUDB()
    u.client = FakeClient()
    antidelete.register(u)
    return u


def make_incoming_event(client, chat_id=12345, msg_id=10, is_private=True, text="Salam",
                        sender=None, chat=None, media_kw=None, file_size=1024):
    if sender is None:
        sender = User(id=777, first_name="Rauf", username="rauf_test")
    if chat is None:
        chat = User(id=chat_id, first_name="Rauf") if is_private else Channel(id=chat_id, title="Test Group", photo=ChatPhotoEmpty(), date=fakes.T0)

    media_kw = media_kw or {}
    file_obj = types.SimpleNamespace(size=file_size)
    msg = types.SimpleNamespace(
        id=msg_id,
        raw_text=text,
        file=file_obj,
        **media_kw
    )

    downloaded = []

    async def download_media(file=None):
        downloaded.append(file)
        # Saxta media faylı yaradılır
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file, "w") as f:
            f.write("fake media content")
        return file

    async def get_sender():
        return sender

    async def get_chat():
        return chat

    ev = types.SimpleNamespace(
        client=client,
        chat_id=chat_id,
        is_private=is_private,
        message=msg,
        get_sender=get_sender,
        get_chat=get_chat,
        download_media=download_media,
    )
    return ev


def make_deleted_event(client, del_ids, chat_id=None):
    return types.SimpleNamespace(
        client=client,
        chat_id=chat_id,
        deleted_ids=del_ids,
        deleted_id=del_ids[0] if del_ids else None,
    )


def test_antidelete_state_init_and_save():
    fake_db = FakeUDB({"on": False, "mode": "all", "save_media": False, "caught": 5, "media_caught": 2})
    st = antidelete.AntiDeleteState(fake_db)
    assert st.cfg["on"] is False
    assert st.cfg["mode"] == "all"
    assert st.cfg["save_media"] is False
    assert st.cfg["caught"] == 5
    assert st.cfg["media_caught"] == 2

    st.cfg["caught"] = 6
    st.save()
    assert fake_db.d[(antidelete.NS, antidelete.KEY)]["caught"] == 6


def test_detect_media_kind():
    m_voice = types.SimpleNamespace(voice=True)
    assert antidelete._detect_media_kind(m_voice) == "🎙 Səsli mesaj"

    m_vnote = types.SimpleNamespace(video_note=True)
    assert antidelete._detect_media_kind(m_vnote) == "📹 Video qeyd (yumru)"

    m_photo = types.SimpleNamespace(photo=True)
    assert antidelete._detect_media_kind(m_photo) == "🖼 Şəkil"

    m_sticker = types.SimpleNamespace(sticker=True)
    assert antidelete._detect_media_kind(m_sticker) == "🎭 Stiker"

    m_audio = types.SimpleNamespace(audio=True)
    assert antidelete._detect_media_kind(m_audio) == "🎵 Mahnı / Audio"

    m_video = types.SimpleNamespace(video=True)
    assert antidelete._detect_media_kind(m_video) == "🎬 Video"

    m_doc = types.SimpleNamespace(document=True)
    assert antidelete._detect_media_kind(m_doc) == "📄 Sənəd"

    m_plain = types.SimpleNamespace()
    assert antidelete._detect_media_kind(m_plain) is None


def test_clean_old_media(tmp_path, monkeypatch):
    test_dir = str(tmp_path / "test_antidelete_media")
    os.makedirs(test_dir, exist_ok=True)
    monkeypatch.setattr(antidelete, "MEDIA_CACHE_DIR", test_dir)

    old_file = os.path.join(test_dir, "old.dat")
    fresh_file = os.path.join(test_dir, "fresh.dat")

    with open(old_file, "w") as f:
        f.write("old")
    with open(fresh_file, "w") as f:
        f.write("fresh")

    # old_file-in vaxtını 2 gün əvvələ çəkirik
    past_time = time.time() - 172800
    os.utime(old_file, (past_time, past_time))

    antidelete._clean_old_media()

    assert not os.path.exists(old_file)
    assert os.path.exists(fresh_file)


@pytest.mark.asyncio
async def test_on_incoming_message_caching(ub, tmp_path, monkeypatch):
    test_dir = str(tmp_path / "test_media_dir")
    monkeypatch.setattr(antidelete, "MEDIA_CACHE_DIR", test_dir)

    # raw handler-ləri tapırıq
    on_incoming = ub.raw_handlers[0][1]

    client = ub.client

    # 1. Şəxsi mesaj (DM) gəlir
    ev_dm = make_incoming_event(client, chat_id=1001, msg_id=50, is_private=True, text="Gizli mesaj")
    await on_incoming(ev_dm)

    assert (1001, 50) in antidelete._msg_cache
    assert antidelete._mid_to_chat.get(50) == 1001
    cached = antidelete._msg_cache[(1001, 50)]
    assert cached["text"] == "Gizli mesaj"
    assert cached["sender_name"] == "Rauf"
    assert cached["sender_username"] == "@rauf_test"

    # 2. Rejim 'dm' olduğu halda qrup mesajı gəldikdə keçilməlidir
    ev_group = make_incoming_event(client, chat_id=-1002, msg_id=51, is_private=False, text="Qrup mətni")
    await on_incoming(ev_group)
    assert (-1002, 51) not in antidelete._msg_cache

    # 3. Telegram xidmət mesajı (sender id 777000) keşlənməməlidir
    ev_srv = make_incoming_event(client, chat_id=777000, msg_id=52, is_private=True,
                                 sender=User(id=777000, first_name="Telegram"))
    await on_incoming(ev_srv)
    assert (777000, 52) not in antidelete._msg_cache


@pytest.mark.asyncio
async def test_on_incoming_with_media(ub, tmp_path, monkeypatch):
    test_dir = str(tmp_path / "test_media_dir")
    monkeypatch.setattr(antidelete, "MEDIA_CACHE_DIR", test_dir)

    on_incoming = ub.raw_handlers[0][1]
    client = ub.client

    # Səsli mesaj
    ev = make_incoming_event(
        client,
        chat_id=2002,
        msg_id=70,
        is_private=True,
        text="",
        media_kw={"voice": True},
        file_size=5000,
    )
    await on_incoming(ev)

    assert (2002, 70) in antidelete._msg_cache
    cached = antidelete._msg_cache[(2002, 70)]
    assert cached["media_kind"] == "🎙 Səsli mesaj"
    assert cached["media_path"] is not None
    assert os.path.isfile(cached["media_path"])


@pytest.mark.asyncio
async def test_on_message_deleted_text(ub):
    on_incoming = ub.raw_handlers[0][1]
    on_deleted = ub.raw_handlers[1][1]
    client = ub.client

    # Əvvəlcə mesaj daxil olur
    ev_in = make_incoming_event(client, chat_id=3003, msg_id=88, is_private=True, text="Bu mesaj silinəcək")
    await on_incoming(ev_in)

    # Silinmə hadisəsi baş verir (DM-lərdə Telegram chat_id vermir -> chat_id=None)
    ev_del = make_deleted_event(client, del_ids=[88], chat_id=None)
    await on_deleted(ev_del)

    # Keşdən silinməlidir
    assert (3003, 88) not in antidelete._msg_cache
    assert 88 not in antidelete._mid_to_chat

    # Bildiriş 'me' çatına göndərilməlidir
    assert len(client.sent_messages) == 1
    sent = client.sent_messages[0]
    assert sent["target"] == "me"
    assert "Silinən Mesaj Aşkarlandı!" in sent["text"]
    assert "Bu mesaj silinəcək" in sent["text"]
    assert "Rauf" in sent["text"]


@pytest.mark.asyncio
async def test_on_message_deleted_with_media(ub, tmp_path, monkeypatch):
    test_dir = str(tmp_path / "test_media_dir")
    monkeypatch.setattr(antidelete, "MEDIA_CACHE_DIR", test_dir)

    on_incoming = ub.raw_handlers[0][1]
    on_deleted = ub.raw_handlers[1][1]
    client = ub.client

    # Şəkilli mesaj daxil olur
    ev_in = make_incoming_event(
        client,
        chat_id=4004,
        msg_id=99,
        is_private=True,
        text="Şəklin mətni",
        media_kw={"photo": True},
    )
    await on_incoming(ev_in)
    saved_path = antidelete._msg_cache[(4004, 99)]["media_path"]
    assert os.path.isfile(saved_path)

    # Silinmə hadisəsi
    ev_del = make_deleted_event(client, del_ids=[99], chat_id=None)
    await on_deleted(ev_del)

    # Fayl 'send_file' ilə 'me'-yə göndərilməlidir və sonra diskdən təmizlənməlidir
    assert len(client.sent_files) == 1
    sf = client.sent_files[0]
    assert sf["target"] == "me"
    assert sf["file"] == saved_path
    assert "Şəkil" in sf["caption"]
    assert "Şəklin mətni" in sf["caption"]

    # Fayl silinmiş olmalıdır
    assert not os.path.exists(saved_path)


@pytest.mark.asyncio
async def test_on_message_deleted_long_caption(ub, tmp_path, monkeypatch):
    test_dir = str(tmp_path / "test_media_dir")
    monkeypatch.setattr(antidelete, "MEDIA_CACHE_DIR", test_dir)

    on_incoming = ub.raw_handlers[0][1]
    on_deleted = ub.raw_handlers[1][1]
    client = ub.client

    # 1500 simvolluq uzun mətnli foto
    long_text = "UzunMətn_" * 150
    ev_in = make_incoming_event(
        client,
        chat_id=5005,
        msg_id=101,
        is_private=True,
        text=long_text,
        media_kw={"photo": True},
    )
    await on_incoming(ev_in)

    ev_del = make_deleted_event(client, del_ids=[101], chat_id=None)
    await on_deleted(ev_del)

    # Uzun mətndə media fayl qısa caption ilə göndərilir, tam mətn isə ayrıca send_message ilə
    assert len(client.sent_files) == 1
    assert len(client.sent_messages) == 1
    assert len(client.sent_files[0]["caption"]) <= 1024
    assert long_text in client.sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_antidelete_commands(ub):
    client = ub.client

    # 1. Status yoxlanışı
    ev = await fakes.run_command(ub, "antidelete", ".antidelete", client)
    assert any("Anti-Delete Monitoru" in e for e in ev.edits)

    # 2. Söndürmə (.antidelete off)
    ev_off = await fakes.run_command(ub, "antidelete", ".antidelete off", client)
    assert any("Anti-Delete söndürüldü" in e for e in ev_off.edits)

    # 3. Yenidən açma (.antidelete on)
    ev_on = await fakes.run_command(ub, "antidelete", ".antidelete on", client)
    assert any("Anti-Delete aktivləşdirildi" in e for e in ev_on.edits)

    # 4. DM rejimi (.antidelete dm)
    ev_dm = await fakes.run_command(ub, "antidelete", ".antidelete dm", client)
    assert any("Yalnız şəxsi (DM)" in e for e in ev_dm.edits)

    # 5. Bütün çatlar rejimi (.antidelete all)
    ev_all = await fakes.run_command(ub, "antidelete", ".antidelete all", client)
    assert any("Bütün çatlar" in e for e in ev_all.edits)

    # 6. Media rejimi tənzimləmə (.antidelete media off / on)
    ev_moff = await fakes.run_command(ub, "antidelete", ".antidelete media off", client)
    assert any("Media yaddaşı:</b> Söndürüldü" in e for e in ev_moff.edits)

    ev_mon = await fakes.run_command(ub, "antidelete", ".antidelete media on", client)
    assert any("Media yaddaşı:</b> Aktiv" in e for e in ev_mon.edits)

    # 7. Keşi təmizləmə (.antidelete clear)
    antidelete._msg_cache[(999, 999)] = {}
    antidelete._mid_to_chat[999] = 999
    ev_clear = await fakes.run_command(ub, "antidelete", ".antidelete clear", client)
    assert any("Anti-Delete keşi təmizləndi" in e for e in ev_clear.edits)
    assert len(antidelete._msg_cache) == 0
    assert len(antidelete._mid_to_chat) == 0
