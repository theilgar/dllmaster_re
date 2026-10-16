"""🎉 Xoş gəldin (yeni istifadəçi) Premium-u vahid testləri.

Settings helperləri, baza əməliyyatları, menyu paneli (günlə / tarixlə rejimləri),
daxiletmə filtrləri və /start onboarding integrasiyası.
"""
import contextlib
import time
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus.handlers.input_router as in_router
import bot.menus.handlers.welcome_prem_flow as wpflow
import bot.menus.state as st
import bot.menus.views.welcome_prem as wpviews
import bot.plugins.start_plugin as splug
import common.database as cd


# ───────────────────────── Fake Models & Database ─────────────────────────
class FakeChat:
    def __init__(self, chat_id: int, chat_type: str = "private", title: str = ""):
        self.id = chat_id
        self.type = chat_type
        self.title = title


class FakeUser:
    def __init__(self, user_id: int, first_name: str = "User", username: str = ""):
        self.id = user_id
        self.first_name = first_name
        self.username = username
        self.is_bot = False


class FakeMessage:
    def __init__(
        self,
        chat_id: int = 1,
        user_id: int = 1,
        text: str = "",
        chat_type: str = "private",
        message_id: int = 1,
        first_name: str = "User",
        title: str = "",
        bot=None,
    ):
        self.message_id = message_id
        self.chat = FakeChat(chat_id, chat_type, title)
        self.from_user = FakeUser(user_id, first_name)
        self.text = text
        self.answer = AsyncMock()
        self.reply = AsyncMock()
        self.delete = AsyncMock()
        self.edit_text = AsyncMock()
        self.bot = bot or types.SimpleNamespace(
            me=AsyncMock(return_value=FakeUser(999, username="testbot")),
            send_message=AsyncMock(),
            data={},
        )


class FakeCallback:
    def __init__(self, chat_id: int = 1, user_id: int = 1, message_id: int = 50, data: str = "wprem_cfg"):
        self.id = "cb_test"
        self.chat_instance = "ci_test"
        self.from_user = FakeUser(user_id)
        self.message = FakeMessage(chat_id=chat_id, user_id=user_id, message_id=message_id)
        self.data = data
        self.answer = AsyncMock()
        self.bot = self.message.bot


class FakeDB:
    def __init__(self):
        self.settings = {}
        self.welcome_trials = {}
        self.users = {}
        self.premium = {}
        self.invites = {}
        self.calls = []

    def record_invite(self, invited_id: int, inviter_id: int, reward_days: int) -> bool:
        self.invites[invited_id] = (inviter_id, reward_days)
        return True

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = str(value)

    def delete_setting(self, key):
        self.settings.pop(key, None)

    def record_welcome_trial(self, user_id: int, mode: str, days: int | None, until: int) -> bool:
        if user_id in self.welcome_trials:
            return False
        self.welcome_trials[user_id] = {
            "user_id": user_id,
            "mode": mode,
            "days": days,
            "until": until,
            "created_at": int(time.time()),
        }
        return True

    def has_welcome_trial(self, user_id: int) -> bool:
        return user_id in self.welcome_trials

    def get_welcome_trial_stats(self) -> dict:
        now = int(time.time())
        total = len(self.welcome_trials)
        active = sum(1 for t in self.welcome_trials.values() if t["until"] > now)
        return {"total_trials": total, "active_trials": active}

    def grant_premium_until(self, user_id: int, until_ts: int, granted_by=None, conn=None):
        now = int(time.time())
        if until_ts <= now:
            return None
        cur = self.premium.get(user_id)
        if cur is None and user_id in self.premium:  # lifetime
            return None
        new_until = until_ts
        if cur:
            new_until = max(int(cur), until_ts)
        self.premium[user_id] = new_until
        return new_until

    def grant_premium(self, user_id: int, days: int, granted_by=0, conn=None):
        now = int(time.time())
        cur = self.premium.get(user_id)
        if cur is None and user_id in self.premium:  # lifetime
            return None
        base = max(cur, now) if cur else now
        new_until = base + days * 86400
        self.premium[user_id] = new_until
        return new_until

    def get_user(self, user_id: int):
        return self.users.get(user_id)

    def upsert_user(self, user_id: int, username=None, first_name=None, last_name=None):
        now = int(time.time())
        if user_id not in self.users:
            self.users[user_id] = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "first_seen": now,
                "last_seen": now,
            }
        else:
            self.users[user_id].update(username=username, first_name=first_name, last_name=last_name, last_seen=now)

    def get_premium(self, user_id: int):
        if user_id in self.premium:
            return {"until": self.premium[user_id]}
        return None


@pytest.fixture(autouse=True)
def fakedb(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(cd, "_db", db)
    monkeypatch.setattr(cd, "get_db", lambda: db)
    monkeypatch.setattr(wpviews, "get_db", lambda: db)
    monkeypatch.setattr(splug, "get_db", lambda: db)
    monkeypatch.setattr(cd, "get_setting", db.get_setting)
    monkeypatch.setattr(cd, "set_setting", db.set_setting)
    st.wprem_inp.update(stage=None, chat_id=None, panel_id=None)
    return db


# ───────────────────────── 1. Settings Helperləri ─────────────────────────
def test_welcome_prem_settings_helpers(fakedb):
    # Defolt vəziyyət
    assert cd.is_welcome_prem_enabled() is False
    assert cd.get_welcome_prem_mode() == "days"
    assert cd.get_welcome_prem_days() == 3
    assert cd.get_welcome_prem_until() is None
    assert cd.is_welcome_prem_active() is False

    # Aktivləşdirmə
    cd.set_welcome_prem_enabled(True)
    assert cd.is_welcome_prem_enabled() is True
    # Days rejimində 3 gün olduğu üçün artıq aktivdir
    assert cd.is_welcome_prem_active() is True

    # Günlərin dəyişdirilməsi
    cd.set_welcome_prem_days(7)
    assert cd.get_welcome_prem_days() == 7
    cd.set_welcome_prem_days(0)  # max(1, 0)
    assert cd.get_welcome_prem_days() == 1

    # Rejimin dəyişdirilməsi: date
    cd.set_welcome_prem_mode("date")
    assert cd.get_welcome_prem_mode() == "date"
    # Bitmə vaxtı hələ təyin olunmayıb -> False
    assert cd.is_welcome_prem_active() is False

    # Gələcək tarix təyin edildikdə -> True
    future = int(time.time()) + 86400 * 5
    cd.set_welcome_prem_until(future)
    assert cd.get_welcome_prem_until() == future
    assert cd.is_welcome_prem_active() is True

    # Keçmiş tarix olduqda -> False
    cd.set_welcome_prem_until(int(time.time()) - 100)
    assert cd.is_welcome_prem_active() is False

    # Tarixi təmizləmək
    cd.set_welcome_prem_until(None)
    assert cd.get_welcome_prem_until() is None

    # Yenidən days rejiminə qayıtmaq
    cd.set_welcome_prem_mode("days")
    assert cd.is_welcome_prem_active() is True


# ───────────────────────── 2. Baza Trial Əməliyyatları ─────────────────────────
def test_database_welcome_trial_operations(fakedb):
    now = int(time.time())
    until = now + 86400 * 3

    # Yeni sınaq qeydi
    assert fakedb.record_welcome_trial(101, "days", 3, until) is True
    assert fakedb.has_welcome_trial(101) is True
    assert fakedb.has_welcome_trial(102) is False

    # Təkrar sınaq qeydi bloklanmalıdır
    assert fakedb.record_welcome_trial(101, "days", 7, until + 86400) is False

    # İkinci istifadəçi (tarix rejimi ilə)
    assert fakedb.record_welcome_trial(102, "date", None, until) is True
    assert fakedb.has_welcome_trial(102) is True

    # Statistika
    stats = fakedb.get_welcome_trial_stats()
    assert stats["total_trials"] == 2
    assert stats["active_trials"] == 2


def test_grant_premium_until_logic(fakedb):
    now = int(time.time())

    # 1. Sıfırdan gələcək tarixə premium verilməsi
    fut1 = now + 86400 * 3
    assert fakedb.grant_premium_until(201, fut1) == fut1
    assert fakedb.get_premium(201)["until"] == fut1

    # 2. Daha uzaq tarix verildikdə uzadılmalıdır
    fut2 = now + 86400 * 10
    assert fakedb.grant_premium_until(201, fut2) == fut2
    assert fakedb.get_premium(201)["until"] == fut2

    # 3. Mövcuddan daha yaxın tarix verildikdə qısaldılmamalıdır
    fut_short = now + 86400 * 5
    assert fakedb.grant_premium_until(201, fut_short) == fut2
    assert fakedb.get_premium(201)["until"] == fut2

    # 4. Keçmiş tarix verildikdə heç nə dəyişməməlidir
    past = now - 500
    assert fakedb.grant_premium_until(201, past) is None

    # 5. Ömürlük premiumu olan istifadəçi (until is None)
    fakedb.premium[202] = None
    assert fakedb.grant_premium_until(202, fut2) is None
    assert fakedb.get_premium(202)["until"] is None


# ───────────────────────── 3. Menyu Görünüşləri (Views) ─────────────────────────
def test_welcome_prem_view_days_mode(fakedb):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_mode("days")
    cd.set_welcome_prem_days(7)

    text, kb = wpviews.welcome_prem_view()
    assert "Xoş gəldin Premium-u" in text
    assert "🟢 Aktiv" in text
    assert "Sınaq müddəti (Günlə)" in text
    assert "7 gün" in text

    buttons = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert any("wprem_toggle" in c for c in callbacks)
    assert any("wprem_mode" in c for c in callbacks)
    assert any("wprem_set_days" in c for c in callbacks)
    assert any("wprem_days:1" in c for c in callbacks)
    assert any("wprem_days:3" in c for c in callbacks)
    assert any("wprem_days:7" in c for c in callbacks)
    assert any("wprem_days:14" in c for c in callbacks)
    assert any("wprem_days:30" in c for c in callbacks)


def test_welcome_prem_view_date_mode(fakedb):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_mode("date")
    fut = int(time.time()) + 86400 * 3
    cd.set_welcome_prem_until(fut)

    text, kb = wpviews.welcome_prem_view(note="✅ Test bildiriş")
    assert "Xoş gəldin Premium-u" in text
    assert "Aksiya kampaniyası (Tarixədək)" in text
    assert "✅ Test bildiriş" in text

    buttons = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert any("wprem_toggle" in c for c in callbacks)
    assert any("wprem_mode" in c for c in callbacks)
    assert any("wprem_set_date" in c for c in callbacks)
    assert any("wprem_qdate:1" in c for c in callbacks)
    assert any("wprem_qdate:3" in c for c in callbacks)
    assert any("wprem_qdate:7" in c for c in callbacks)
    assert any("wprem_clr_date" in c for c in callbacks)


# ───────────────────────── 4. Callback Router (wpflow) ─────────────────────────
@pytest.mark.asyncio
async def test_welcome_prem_callbacks(fakedb):
    # 1. wprem_cfg
    cb = FakeCallback(data="wprem_cfg")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "cfg"], "wprem_cfg") is True
    cb.message.edit_text.assert_called()

    # 2. wprem_toggle
    cb = FakeCallback(data="wprem_toggle")
    assert cd.is_welcome_prem_enabled() is False
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "toggle"], "wprem_toggle") is True
    assert cd.is_welcome_prem_enabled() is True

    # 3. wprem_mode
    cb = FakeCallback(data="wprem_mode")
    assert cd.get_welcome_prem_mode() == "days"
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "mode"], "wprem_mode") is True
    assert cd.get_welcome_prem_mode() == "date"
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "mode"], "wprem_mode") is True
    assert cd.get_welcome_prem_mode() == "days"

    # 4. wprem_days:14
    cb = FakeCallback(data="wprem_days:14")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "days", "14"], "wprem_days") is True
    assert cd.get_welcome_prem_days() == 14

    # 5. wprem_qdate:5
    cb = FakeCallback(data="wprem_qdate:5")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "qdate", "5"], "wprem_qdate") is True
    assert cd.get_welcome_prem_until() > int(time.time())

    # 6. wprem_clr_date
    cb = FakeCallback(data="wprem_clr_date")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "clr", "date"], "wprem_clr_date") is True
    assert cd.get_welcome_prem_until() is None

    # 7. wprem_set_days & wprem_set_date (FSM stage)
    cb = FakeCallback(data="wprem_set_days")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "set", "days"], "wprem_set_days") is True
    assert st.wprem_inp.get("stage") == "await_wprem_days"

    cb = FakeCallback(data="wprem_set_date")
    assert await wpflow.handle_welcome_prem_callback(cb, ["wprem", "set", "date"], "wprem_set_date") is True
    assert st.wprem_inp.get("stage") == "await_wprem_date"


# ───────────────────────── 5. Mətn Daxiletməsi (input_router) ─────────────────────────
@pytest.mark.asyncio
async def test_welcome_prem_days_input(fakedb, monkeypatch):
    ctx = types.SimpleNamespace(
        bot=types.SimpleNamespace(
            edit_message_text=AsyncMock(),
            data={},
        )
    )
    monkeypatch.setattr(in_router, "get_context", lambda: ctx)
    st.wprem_inp.update(stage="await_wprem_days", chat_id=1, panel_id=10)

    # Düzgün rəqəm
    msg = FakeMessage(chat_id=1, user_id=1, text="21")
    await in_router.welcome_prem_days_input(msg)
    assert cd.get_welcome_prem_days() == 21
    assert st.wprem_inp.get("stage") is None
    ctx.bot.edit_message_text.assert_called()

    # Səhv daxiletmə
    st.wprem_inp.update(stage="await_wprem_days", chat_id=1, panel_id=10)
    msg_bad = FakeMessage(chat_id=1, user_id=1, text="salam")
    await in_router.welcome_prem_days_input(msg_bad)
    assert cd.get_welcome_prem_days() == 21  # dəyişməz qaldı
    assert st.wprem_inp.get("stage") == "await_wprem_days"


@pytest.mark.asyncio
async def test_welcome_prem_date_input(fakedb, monkeypatch):
    ctx = types.SimpleNamespace(
        bot=types.SimpleNamespace(
            edit_message_text=AsyncMock(),
            data={},
        )
    )
    monkeypatch.setattr(in_router, "get_context", lambda: ctx)
    st.wprem_inp.update(stage="await_wprem_date", chat_id=1, panel_id=10)

    # Düzgün nisbi format (+3g)
    msg = FakeMessage(chat_id=1, user_id=1, text="+3g")
    await in_router.welcome_prem_date_input(msg)
    assert cd.get_welcome_prem_until() > int(time.time())
    assert st.wprem_inp.get("stage") is None

    # Təmizləmə formatı (-)
    st.wprem_inp.update(stage="await_wprem_date", chat_id=1, panel_id=10)
    msg_clr = FakeMessage(chat_id=1, user_id=1, text="-")
    await in_router.welcome_prem_date_input(msg_clr)
    assert cd.get_welcome_prem_until() is None
    assert st.wprem_inp.get("stage") is None

    # Səhv format
    st.wprem_inp.update(stage="await_wprem_date", chat_id=1, panel_id=10)
    msg_bad = FakeMessage(chat_id=1, user_id=1, text="düzgün olmayan vaxt")
    await in_router.welcome_prem_date_input(msg_bad)
    assert st.wprem_inp.get("stage") == "await_wprem_date"


# ───────────────────────── 6. /start Onboarding İnteqrasiyası ─────────────────────────
@pytest.mark.asyncio
async def test_start_welcome_trial_grant_days_mode(fakedb, monkeypatch):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_mode("days")
    cd.set_welcome_prem_days(7)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # Yeni istifadəçi /start yazır
    msg = FakeMessage(chat_id=701, user_id=701, text="/start", first_name="Təzə", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)

    # 1. welcome_trials qeyd olunub
    assert fakedb.has_welcome_trial(701) is True
    trial = fakedb.welcome_trials[701]
    assert trial["mode"] == "days"
    assert trial["days"] == 7
    assert trial["until"] > int(time.time())

    # 2. Premium verilib
    prem = fakedb.get_premium(701)
    assert prem is not None
    assert prem["until"] == trial["until"]

    # 3. İstifadəçiyə hədiyyə təbriki göndərilib
    msg.answer.assert_called()
    call_args = msg.answer.call_args[0][0]
    assert "Xoş gəldiniz!" in call_args
    assert "7 gün" in call_args


@pytest.mark.asyncio
async def test_start_welcome_trial_grant_date_mode(fakedb, monkeypatch):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_mode("date")
    fut = int(time.time()) + 86400 * 5
    cd.set_welcome_prem_until(fut)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # Yeni istifadəçi /start yazır
    msg = FakeMessage(chat_id=702, user_id=702, text="/start", first_name="Aygün", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)

    assert fakedb.has_welcome_trial(702) is True
    trial = fakedb.welcome_trials[702]
    assert trial["mode"] == "date"
    assert trial["until"] == fut

    prem = fakedb.get_premium(702)
    assert prem is not None
    assert prem["until"] == fut


@pytest.mark.asyncio
async def test_start_welcome_trial_old_user_rejected(fakedb, monkeypatch):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_days(3)

    # İstifadəçi 1 ay əvvəl ilk dəfə görünüb
    fakedb.users[703] = {"user_id": 703, "first_seen": int(time.time()) - 86400 * 30}

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    msg = FakeMessage(chat_id=703, user_id=703, text="/start", first_name="Köhnə", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)

    # Köhnə istifadəçi xoş gəldin paketi ala bilməz
    assert fakedb.has_welcome_trial(703) is False
    assert fakedb.get_premium(703) is None


@pytest.mark.asyncio
async def test_start_welcome_trial_duplicate_prevented(fakedb, monkeypatch):
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_days(3)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # İlk /start
    msg1 = FakeMessage(chat_id=704, user_id=704, text="/start", first_name="Yeni", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())
    await splug.start_command(msg1)

    assert fakedb.has_welcome_trial(704) is True
    initial_until = fakedb.premium[704]

    # İkinci /start (təkrar icra)
    msg2 = FakeMessage(chat_id=704, user_id=704, text="/start", first_name="Yeni", bot=bot)
    await splug.start_command(msg2)

    # Müddət təkrar uzadılmamalıdır
    assert fakedb.premium[704] == initial_until


@pytest.mark.asyncio
async def test_start_welcome_trial_with_referral(fakedb, monkeypatch):
    # Həm dəvət sistemi, həm də xoş gəldin paketi aktivdir
    cd.set_invite_enabled(True)
    cd.set_invite_reward_days(5)
    cd.set_welcome_prem_enabled(True)
    cd.set_welcome_prem_days(7)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # Yeni istifadəçi 888 referal linki ilə gəlir (inv_888)
    msg = FakeMessage(chat_id=705, user_id=705, text="/start inv_888", first_name="Dəvətli", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)

    # 1. Dəvət edən (888) 5 gün qazandı
    assert fakedb.get_premium(888) is not None

    # 2. Yeni qoşulan (705) xoş gəldin 7 gün sınağı aldı
    assert fakedb.has_welcome_trial(705) is True
    assert fakedb.get_premium(705) is not None
