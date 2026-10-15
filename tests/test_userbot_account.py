"""🪪 .username, .pfp, .delpfp (saxta Telethon client ilə)."""
import types

import pytest
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernameNotModifiedError, UsernameOccupiedError
from telethon.tl.types import User

import userbot.plugins.account as acc
from tests import userbot_fakes as fakes


class FakeClient:
    def __init__(self, username="eski", free=True, error=None, photos=0):
        self.me = User(id=5, first_name="Ali", username=username)
        self.free, self.error, self.log = free, error, []
        self.photos = [types.SimpleNamespace(id=i, access_hash=100 + i, file_reference=b"r") for i in range(photos)]

    async def get_me(self):
        return self.me

    async def upload_file(self, data, file_name=None):
        self.log.append(("upload", data, file_name))
        return "UPLOADED"

    async def get_profile_photos(self, who, limit=None):
        assert who == "me"
        return self.photos if limit is None else self.photos[:limit]

    async def __call__(self, req):
        name = type(req).__name__
        self.log.append((name, req))
        if name == "CheckUsernameRequest":
            return self.free
        if self.error and name in ("UpdateUsernameRequest",):
            raise self.error
        return True


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    async def fake_temp(event, text, delay=2):
        await acc.safe_edit(event, text)

    monkeypatch.setattr(acc, "temp_edit", fake_temp)


@pytest.fixture
def ub():
    u = fakes.FakeUB()
    acc.register(u)
    return u


def test_normalize_username():
    assert acc.normalize_username("@Ali_1x") == "Ali_1x"
    assert acc.normalize_username("https://t.me/ali_user") == "ali_user"
    assert acc.normalize_username("t.me/ali_user/") == "ali_user"
    for bad in ("", "abc", "1abcde", "_abcde", "ali_", "ali user", "a" * 33, "əli_user", "ali-user"):
        assert acc.normalize_username(bad) is None, bad
    assert acc.normalize_username("a" * 32) == "a" * 32 and acc.normalize_username("abcde") == "abcde"


async def test_change_username_paths():
    ok, text = await acc.change_username(FakeClient(), "yeni_ad")
    assert ok and "@yeni_ad" in text and "t.me/yeni_ad" in text
    c = FakeClient(free=False)
    ok, text = await acc.change_username(c, "tutulub")
    assert not ok and "tutulub" in text and not any(n == "UpdateUsernameRequest" for n, *_ in c.log)   # tutulubsa dəyişmir
    ok, text = await acc.change_username(FakeClient(), "")
    assert ok and "silindi" in text
    for err, needle in ((UsernameOccupiedError(request=None), "tutulub"), (UsernameInvalidError(request=None), "qəbul etmir"),
                        (UsernameNotModifiedError(request=None), "onsuz da"), (FloodWaitError(request=None, capture=30), "limiti")):
        ok, text = await acc.change_username(FakeClient(error=err), "yeni_ad")
        assert not ok and needle in text
    c = FakeClient()
    await acc.change_username(c, "")
    assert not any(n == "CheckUsernameRequest" for n, *_ in c.log)                                      # silmək üçün yoxlama lazım deyil


async def test_username_commands(ub):
    ev = await fakes.run_command(ub, "username", ".username", FakeClient("ali"))
    assert "@ali" in ev.edits[-1]
    ev = await fakes.run_command(ub, "username", ".username", FakeClient(None))
    assert "yoxdur" in ev.edits[-1]
    c = FakeClient("eski")
    ev = await fakes.run_command(ub, "username", ".username @Yeni_Ad", c)
    assert "@Yeni_Ad" in ev.edits[-1]
    assert [r.username for n, r in [x for x in c.log if len(x) == 2] if n == "UpdateUsernameRequest"] == ["Yeni_Ad"]
    ev = await fakes.run_command(ub, "username", ".username ab", FakeClient())
    assert "5–32" in ev.edits[-1]
    c = FakeClient()
    ev = await fakes.run_command(ub, "username", ".username -", c)
    assert "silindi" in ev.edits[-1]
    assert [r.username for n, r in [x for x in c.log if len(x) == 2] if n == "UpdateUsernameRequest"] == [""]
    c = FakeClient()
    ev = await fakes.run_command(ub, "delusername", ".delusername", c)
    assert "silindi" in ev.edits[-1]
    ev = await fakes.run_command(ub, "username", ".username tutulub", FakeClient(free=False))
    assert "tutulub" in ev.edits[-1]


def test_media_kind():
    assert acc.media_kind(None) is None
    assert acc.media_kind(fakes.msg()) is None
    assert acc.media_kind(fakes.msg(photo=object())) == "photo"
    assert acc.media_kind(fakes.msg(video=object())) == "video" and acc.media_kind(fakes.msg(gif=object())) == "video"
    assert acc.media_kind(fakes.msg(document=types.SimpleNamespace(mime_type="image/png"))) == "photo"
    assert acc.media_kind(fakes.msg(document=types.SimpleNamespace(mime_type="application/pdf"))) is None


async def test_pfp_sets_photo_or_video_and_needs_media(ub):
    ev = await fakes.run_command(ub, "pfp", ".pfp", FakeClient())
    assert "reply" in ev.edits[-1]
    c = FakeClient()
    ev = await fakes.run_command(ub, "pfp", ".pfp", c, reply=fakes.msg(photo=object(), data=b"IMG"))
    req = [r for n, *r in c.log if n == "UploadProfilePhotoRequest"][0][0]
    assert ("upload", b"IMG", "avatar.jpg") in c.log and req.file == "UPLOADED" and req.video is None
    assert "dəyişdi" in ev.edits[-1]
    c = FakeClient()
    await fakes.run_command(ub, "pfp", ".pfp", c, reply=fakes.msg(gif=object(), data=b"MP4"))
    req = [r for n, *r in c.log if n == "UploadProfilePhotoRequest"][0][0]
    assert ("upload", b"MP4", "avatar.mp4") in c.log and req.video == "UPLOADED" and req.file is None
    ev = await fakes.run_command(ub, "pfp", ".pfp", FakeClient(), reply=fakes.msg(data=b""))     # media yox
    assert "reply" in ev.edits[-1]
    ev = await fakes.run_command(ub, "pfp", ".pfp", FakeClient(), reply=fakes.msg(photo=object(), data=b""))
    assert "❌" in ev.edits[-1] and "yüklənmədi" in ev.edits[-1]


async def test_delpfp_one_n_all_and_batches(ub):
    def deleted(c):
        return [[p.id for p in r.id] for n, *r in c.log if n == "DeletePhotosRequest" for r in r]

    c = FakeClient(photos=5)
    ev = await fakes.run_command(ub, "delpfp", ".delpfp", c)
    assert deleted(c) == [[0]] and "<b>1</b> profil şəkli silindi" in ev.edits[-1]
    c = FakeClient(photos=5)
    await fakes.run_command(ub, "delpfp", ".delpfp 3", c)
    assert deleted(c) == [[0, 1, 2]]
    c = FakeClient(photos=250)
    ev = await fakes.run_command(ub, "delpfp", ".delpfp all", c)
    assert [len(x) for x in deleted(c)] == [100, 100, 50] and "<b>250</b> profil şəkli silindi" in ev.edits[-1]
    c = FakeClient(photos=0)
    ev = await fakes.run_command(ub, "delpfp", ".delpfp", c)
    assert "yoxdur" in ev.edits[-1] and not deleted(c)
