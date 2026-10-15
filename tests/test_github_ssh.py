"""GitHub SSH açarı: botda yarat → public hissəni göstər → bağlantını yoxla → remote-u SSH-ə çevir."""
import os
import shutil
import stat
import subprocess
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher

from bot.updater import git_client, ssh_key

needs_openssh = pytest.mark.skipif(not ssh_key.available(), reason="openssh yoxdur")


@pytest.fixture
def keydir(tmp_path, monkeypatch):
    d = tmp_path / "github"
    monkeypatch.setattr(ssh_key, "KEY_DIR", d)
    monkeypatch.setattr(ssh_key, "KEY", d / "id_ed25519")
    monkeypatch.setattr(ssh_key, "PUB", d / "id_ed25519.pub")
    monkeypatch.setattr(ssh_key, "KNOWN_HOSTS", d / "known_hosts")
    return d


@needs_openssh
def test_generate_creates_private_key_with_safe_permissions(keydir):
    ok, msg = ssh_key.generate("test-bot")
    assert ok, msg
    assert ssh_key.exists()
    assert stat.S_IMODE(os.stat(ssh_key.KEY).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(keydir).st_mode) == 0o700
    pub = ssh_key.public_key()
    assert pub.startswith("ssh-ed25519 ") and "test-bot" in pub
    assert "PRIVATE" not in pub
    assert ssh_key.fingerprint().startswith("SHA256:")


@needs_openssh
def test_regenerate_replaces_key_and_delete_removes_everything(keydir):
    ssh_key.generate()
    first = ssh_key.public_key()
    ssh_key.generate()
    assert ssh_key.public_key() != first
    ssh_key.KNOWN_HOSTS.write_text("x")
    assert ssh_key.delete() is True
    assert not ssh_key.exists() and not ssh_key.KNOWN_HOSTS.exists()
    assert ssh_key.public_key() == "" and ssh_key.fingerprint() == ""


def test_env_only_when_key_exists(keydir):
    assert ssh_key.env() == {}
    keydir.mkdir()
    ssh_key.KEY.write_text("k")
    ssh_key.PUB.write_text("p")
    cmd = ssh_key.env()["GIT_SSH_COMMAND"]
    assert str(ssh_key.KEY) in cmd and "IdentitiesOnly=yes" in cmd and "BatchMode=yes" in cmd


def test_git_passes_ssh_command_to_subprocess(keydir, monkeypatch, tmp_path):
    keydir.mkdir()
    ssh_key.KEY.write_text("k")
    ssh_key.PUB.write_text("p")
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(git_client.subprocess, "run", fake_run)
    rc, out, _ = git_client.git(tmp_path, "status")
    assert rc == 0 and out == "ok"
    assert "GIT_SSH_COMMAND" in seen["env"] and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/user/repo.git", "git@github.com:user/repo.git"),
    ("https://github.com/user/repo", "git@github.com:user/repo.git"),
    ("https://ghp_token@github.com/user/repo.git", "git@github.com:user/repo.git"),
    ("git@github.com:user/repo.git", None),
    ("https://gitlab.com/user/repo.git", None),
    ("", None),
])
def test_https_to_ssh_url(url, expected):
    assert ssh_key.to_ssh_url(url) == expected


def _fake_ssh(monkeypatch, stdout="", stderr="", exc=None):
    def run(cmd, **kw):
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr)     # GitHub uğurda da 1 qaytarır

    monkeypatch.setattr(ssh_key.subprocess, "run", run)


def test_connection_results(keydir, monkeypatch):
    keydir.mkdir()
    ssh_key.KEY.write_text("k")
    ssh_key.PUB.write_text("p")
    _fake_ssh(monkeypatch, stderr="Hi octocat! You've successfully authenticated, but GitHub does not provide shell access.")
    assert ssh_key.test_connection() == (True, "GitHub hesabı: octocat")
    _fake_ssh(monkeypatch, stderr="git@github.com: Permission denied (publickey).")
    ok, msg = ssh_key.test_connection()
    assert not ok and "əlavə" in msg
    _fake_ssh(monkeypatch, exc=subprocess.TimeoutExpired("ssh", 20))
    ok, msg = ssh_key.test_connection()
    assert not ok and "vaxt" in msg


def test_connection_without_key(keydir):
    ok, msg = ssh_key.test_connection()
    assert not ok and "açarı yoxdur" in msg


# ── bot paneli ──
class FakeDB:
    def get_setting(self, *a, **k):
        return None

    def set_setting(self, *a, **k):
        pass


@pytest.fixture
async def panel(keydir, monkeypatch, tmp_path):
    import bot.updater.handlers as uh

    monkeypatch.setattr(uh, "get_db", lambda: FakeDB())
    monkeypatch.setattr(uh, "BASE_DIR", str(tmp_path))
    dp = Dispatcher()
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock(return_value=types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=9))
    ctx = types.SimpleNamespace(dp=dp, bot=bot, creator_id=1, is_creator=lambda u: u == 1, plugin_status={},
                                user_searches={})
    uh.setup(ctx)
    handler = next(h.callback for h in dp.callback_query.handlers if h.callback.__name__ == "gh_setup_callback")

    async def press(data, user=1):
        cb = types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=user),
                                   message=types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=5),
                                   answer=AsyncMock())
        bot.edit_message_text.reset_mock()
        await handler(cb)
        kw = bot.edit_message_text.await_args.kwargs if bot.edit_message_text.await_count else {}
        return kw.get("text", ""), kw.get("reply_markup"), cb

    yield types.SimpleNamespace(press=press, bot=bot)
    import asyncio
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task():
            t.cancel()


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def test_settings_menu_links_to_ssh_key(panel):
    text, kb, _ = await panel.press("ghs:menu")
    assert "SSH açarı" in text and any(b.callback_data == "ghs:key" for b in _buttons(kb))


async def test_key_screen_without_key_offers_generation(panel):
    text, kb, _ = await panel.press("ghs:key")
    datas = [b.callback_data for b in _buttons(kb)]
    if ssh_key.available():
        assert "ghs:kact:genok" in datas
    assert "PRIVATE" not in text


@needs_openssh
async def test_generate_then_show_public_key_only(panel):
    text, kb, _ = await panel.press("ghs:kact:genok")
    assert "ssh-ed25519" in text and "yaradıldı" in text
    assert "PRIVATE KEY" not in text and ssh_key.KEY.read_text().strip() not in text
    urls = [b.url for b in _buttons(kb) if b.url]
    assert ssh_key.GITHUB_NEW_KEY_URL in urls
    datas = [b.callback_data for b in _buttons(kb)]
    for needed in ("ghs:kact:test", "ghs:kact:ssh", "ghs:kact:regen", "ghs:kact:del"):
        assert needed in datas


@needs_openssh
async def test_regenerate_and_delete_need_confirmation(panel):
    await panel.press("ghs:kact:genok")
    first = ssh_key.public_key()
    text, kb, _ = await panel.press("ghs:kact:regen")
    assert "yaradılsın" in text and ssh_key.public_key() == first                   # hələ dəyişməyib
    await panel.press("ghs:kact:genok")
    assert ssh_key.public_key() != first
    text, kb, _ = await panel.press("ghs:kact:del")
    assert "silinsin" in text and ssh_key.exists()
    await panel.press("ghs:kact:delok")
    assert not ssh_key.exists()


async def test_test_button_reports_result(panel, monkeypatch):
    monkeypatch.setattr(ssh_key, "exists", lambda: True)
    monkeypatch.setattr(ssh_key, "public_key", lambda: "ssh-ed25519 AAA test")
    monkeypatch.setattr(ssh_key, "test_connection", lambda: (True, "GitHub hesabı: octocat"))
    text, _, cb = await panel.press("ghs:kact:test")
    assert "octocat" in text and "✅" in text


async def test_only_creator_can_use_it(panel):
    text, kb, cb = await panel.press("ghs:key", user=999)
    assert text == "" and cb.answer.await_args.kwargs.get("show_alert") is True


async def test_remote_is_converted_to_ssh(panel, tmp_path):
    git_client.git(tmp_path, "init")
    git_client.git(tmp_path, "remote", "add", "origin", "https://github.com/user/repo.git")
    text, _, _ = await panel.press("ghs:kact:ssh")
    assert "git@github.com:user/repo.git" in text
    assert git_client.git(tmp_path, "remote", "get-url", "origin")[1] == "git@github.com:user/repo.git"
    text, _, _ = await panel.press("ghs:kact:ssh")                     # ikinci dəfə: artıq SSH-dir
    assert "artıq SSH" in text


async def test_remote_conversion_needs_remote(panel, tmp_path):
    git_client.git(tmp_path, "init")
    text, _, _ = await panel.press("ghs:kact:ssh")
    assert "Remote URL təyin edin" in text
