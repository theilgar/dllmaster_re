import asyncio
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, Message

from common.utilities import run_shell
from bot.menus.views.dev import run_updates
from bot.menus.handlers.system_flow import _mcb_upd_pwd
from bot.menus.handlers.input_router import waiting_sudo_pwd, sudo_pwd_input
from bot.menus.state import upd_inp


def test_run_shell_with_input_data():
    rc, out = asyncio.run(run_shell("cat", input_data="hello\n"))
    assert rc == 0
    assert out.strip() == "hello"


def test_run_updates_with_sudo_password(monkeypatch):
    calls = []

    async def fake_run_shell(cmd, timeout=600, env=None, input_data=None):
        calls.append((cmd, input_data))
        return 0, "upgraded"

    import bot.menus.views.dev as dev_mod
    monkeypatch.setattr(dev_mod, "run_shell", fake_run_shell)
    monkeypatch.setattr(dev_mod, "_upd_items", lambda: [])

    lines, restart = asyncio.run(run_updates(sudo_password="test_secret"))
    assert any("sistem paketləri" in ln for ln in lines)
    assert any(cmd.startswith("sudo -S") and inp == "test_secret\n" for cmd, inp in calls)
    assert restart is True


def test_sudo_pwd_flow(monkeypatch):
    import bot.menus.state as st
    import bot.menus.handlers.system_flow as sf
    import bot.menus.handlers.input_router as ir
    monkeypatch.setattr(st, "is_creator", lambda uid: True)
    monkeypatch.setattr(sf, "is_creator", lambda uid: True)
    monkeypatch.setattr(ir, "is_creator", lambda uid: True)

    cb = MagicMock()
    cb.from_user.id = 123
    cb.message.chat.id = 456
    cb.message.message_id = 789
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()

    # 1. User clicks the button to enter sudo password
    asyncio.run(_mcb_upd_pwd(cb, ["menu", "main"], "upd_pwd"))
    assert upd_inp["stage"] == "await_sudo"
    assert upd_inp["chat_id"] == 456
    assert upd_inp["panel_id"] == 789

    # 2. Waiting check should be True for creator
    msg = MagicMock()
    msg.from_user.id = 123
    msg.text = "mypassword123"
    msg.delete = AsyncMock()
    assert asyncio.run(waiting_sudo_pwd(msg)) is True

    # 3. User sends password
    ctx = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    monkeypatch.setattr(st, "_CTX", ctx)

    sudo_calls = []

    async def fake_run_updates(sudo_password=None):
        sudo_calls.append(sudo_password)
        return ["✅ 🐧 sistem paketləri"], True

    import bot.menus.views.dev as dev_mod
    monkeypatch.setattr(dev_mod, "run_updates", fake_run_updates)

    asyncio.run(sudo_pwd_input(msg))

    # Verification:
    # Message deleted immediately for privacy
    assert msg.delete.called
    # State reset
    assert upd_inp["stage"] is None
    # run_updates called with the secret password
    assert sudo_calls == ["mypassword123"]
    # Panel updated with result
    assert ctx.bot.edit_message_text.called
