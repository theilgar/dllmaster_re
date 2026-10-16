"""🎁 Dəvət (invite / referral) sistemi vahid testləri.

Baza metodları, menyu paneli, daxiletmə filtri, istifadəçi kartı və /start referal onboarding axını.
"""
from datetime import datetime, timezone
import contextlib
import time
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus.handlers.commands as cmd_h
import bot.menus.handlers.group_pm_flow as gpf
import bot.menus.handlers.input_router as in_router
import bot.menus.handlers.invites_flow as iflow
import bot.menus.state as st
import bot.menus.views.groups as gviews
import bot.menus.views.invites as iviews
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
        self.bot = bot or types.SimpleNamespace(
            me=AsyncMock(return_value=FakeUser(999, username="testbot")),
            send_message=AsyncMock(),
            data={},
        )


class FakeCallback:
    def __init__(self, chat_id: int = 1, user_id: int = 1, message_id: int = 50, data: str = "inv_cfg"):
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
        self.invites = {}  # invited_id -> (inviter_id, reward_days, created_at)
        self.users = {}    # user_id -> dict
        self.premium = {}  # user_id -> until (int or None)
        self.calls = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = str(value)

    def delete_setting(self, key):
        self.settings.pop(key, None)

    def record_invite(self, invited_id: int, inviter_id: int, reward_days: int) -> bool:
        if invited_id in self.invites:
            return False
        self.invites[invited_id] = {
            "invited_id": invited_id,
            "inviter_id": inviter_id,
            "reward_days": reward_days,
            "created_at": int(time.time()),
        }
        return True

    def is_invited(self, user_id: int) -> bool:
        return user_id in self.invites

    def get_user_invite_stats(self, inviter_id: int) -> dict:
        matched = [r for r in self.invites.values() if r["inviter_id"] == inviter_id]
        return {
            "count": len(matched),
            "total_days": sum(r["reward_days"] for r in matched),
        }

    def get_global_invite_stats(self) -> dict:
        inviters = {r["inviter_id"] for r in self.invites.values()}
        return {
            "total_invites": len(self.invites),
            "unique_inviters": len(inviters),
            "total_days": sum(r["reward_days"] for r in self.invites.values()),
        }

    def get_top_inviters(self, limit: int = 10) -> list[dict]:
        from collections import defaultdict
        counts = defaultdict(lambda: {"count": 0, "total_days": 0})
        for r in self.invites.values():
            counts[r["inviter_id"]]["count"] += 1
            counts[r["inviter_id"]]["total_days"] += r["reward_days"]
        res = [
            {"inviter_id": uid, "count": d["count"], "total_days": d["total_days"]}
            for uid, d in counts.items()
        ]
        res.sort(key=lambda x: x["count"], reverse=True)
        return res[:limit]

    def get_user(self, user_id: int):
        return self.users.get(user_id)

    def grant_premium(self, user_id: int, days: int, granted_by=None):
        now = int(time.time())
        cur = self.premium.get(user_id)
        base = max(cur, now) if cur else now
        until = base + days * 86400
        self.premium[user_id] = until
        self.calls.append(("grant_premium", user_id, days, until))
        return until

    def get_premium(self, user_id: int):
        now = int(time.time())
        until = self.premium.get(user_id)
        if until and until > now:
            return {"until": until}
        return None


@pytest.fixture(autouse=True)
def fakedb(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(cd, "_db", db)
    monkeypatch.setattr(cd, "get_db", lambda: db)
    monkeypatch.setattr(iviews, "get_db", lambda: db)
    monkeypatch.setattr(gviews, "get_db", lambda: db)
    monkeypatch.setattr(splug, "get_db", lambda: db)
    monkeypatch.setattr(cd, "get_setting", db.get_setting)
    monkeypatch.setattr(cd, "set_setting", db.set_setting)
    st.inv_inp.update(stage=None, chat_id=None, panel_id=None)
    return db


# ───────────────────────── 1. Settings Helperləri ─────────────────────────
def test_invite_settings_helpers(fakedb):
    # Defolt vəziyyət
    assert cd.is_invite_enabled() is True
    assert cd.get_invite_reward_days() == 3

    # Dəyişdirilməsi
    cd.set_invite_enabled(False)
    assert cd.is_invite_enabled() is False

    cd.set_invite_enabled(True)
    assert cd.is_invite_enabled() is True

    cd.set_invite_reward_days(7)
    assert cd.get_invite_reward_days() == 7

    cd.set_invite_reward_days(0)  # max(1, 0)
    assert cd.get_invite_reward_days() == 1


# ───────────────────────── 2. Baza Dəvət Əməliyyatları ─────────────────────────
def test_database_invite_operations(fakedb):
    # Yeni dəvət
    assert fakedb.record_invite(101, 1, 3) is True
    assert fakedb.is_invited(101) is True
    assert fakedb.is_invited(102) is False

    # Dublikat dəvət bloklanmalıdır
    assert fakedb.record_invite(101, 2, 7) is False

    # İkinci dəvət
    assert fakedb.record_invite(102, 1, 3) is True

    # 1-ci istifadəçinin statistikası
    st1 = fakedb.get_user_invite_stats(1)
    assert st1["count"] == 2
    assert st1["total_days"] == 6

    # Başqa istifadəçinin dəvəti
    assert fakedb.record_invite(103, 2, 5) is True
    st2 = fakedb.get_user_invite_stats(2)
    assert st2["count"] == 1
    assert st2["total_days"] == 5

    # Qlobal statistika
    gst = fakedb.get_global_invite_stats()
    assert gst["total_invites"] == 3
    assert gst["unique_inviters"] == 2
    assert gst["total_days"] == 11

    # Top dəvətçilər
    top = fakedb.get_top_inviters()
    assert len(top) == 2
    assert top[0]["inviter_id"] == 1 and top[0]["count"] == 2
    assert top[1]["inviter_id"] == 2 and top[1]["count"] == 1


# ───────────────────────── 3. Menyu Görünüşləri (Views) ─────────────────────────
def test_admin_invite_view(fakedb):
    cd.set_invite_enabled(True)
    cd.set_invite_reward_days(3)
    text, kb = iviews.invite_view()
    assert "Dəvət sistemi" in text
    assert "🟢 Aktiv" in text
    assert "3 gün" in text

    buttons = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in buttons if b.callback_data]
    assert any("inv_toggle" in c for c in callbacks)
    assert any("inv_set_days" in c for c in callbacks)
    assert any("inv_days:1" in c for c in callbacks)
    assert any("inv_days:3" in c for c in callbacks)
    assert any("inv_days:7" in c for c in callbacks)
    assert any("inv_days:14" in c for c in callbacks)
    assert any("inv_days:30" in c for c in callbacks)
    assert any("inv_top:0" in c for c in callbacks)


def test_admin_invite_top_view(fakedb):
    # Boş vəziyyət
    text, kb = iviews.invite_top_view()
    assert "Hələ ki, heç bir dəvət qeydə alınmayıb" in text

    # Məlumat olduqda
    fakedb.users[1] = {"first_name": "Əli", "username": "ali_user"}
    fakedb.record_invite(101, 1, 3)
    text2, kb2 = iviews.invite_top_view()
    assert "Əli" in text2
    assert "@ali_user" in text2
    assert "1" in text2 and "dəvət" in text2


@pytest.mark.asyncio
async def test_user_invite_view_enabled_and_disabled(fakedb):
    user = FakeUser(user_id=777, first_name="Rəşad")

    # Aktiv olanda
    cd.set_invite_enabled(True)
    cd.set_invite_reward_days(5)
    fakedb.record_invite(1001, 777, 5)

    text, kb = await iviews.user_invite_view(user, "testbot")
    assert "https://t.me/testbot?start=inv_777" in text
    assert "+5 gün" in text
    assert "<b>1</b> nəfər" in text  # 1 dost dəvət edilib

    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert any("t.me/share/url" in u for u in urls)

    # Deaktiv olanda
    cd.set_invite_enabled(False)
    text_off, kb_off = await iviews.user_invite_view(user, "testbot")
    assert "hazırda müvəqqəti dayandırılıb" in text_off


# ───────────────────────── 4. Menyu Callback Axını (Flow) ─────────────────────────
@pytest.mark.asyncio
async def test_invite_flow_callbacks(monkeypatch):
    edited = []

    async def fake_safe_edit(cb, text, kb):
        edited.append((text, kb))

    monkeypatch.setattr(iflow, "safe_edit_cb", fake_safe_edit)

    cb = FakeCallback(chat_id=1, user_id=1, message_id=50, data="menu:biz:inv_cfg")

    # 1. inv_cfg
    await iflow.handle_invite_callback(cb, ["menu", "biz", "inv_cfg"], "inv_cfg")
    assert cb.answer.called
    assert len(edited) == 1
    assert "Dəvət sistemi" in edited[0][0]

    # 2. inv_toggle
    cd.set_invite_enabled(True)
    await iflow.handle_invite_callback(cb, ["menu", "biz", "inv_toggle"], "inv_toggle")
    assert cd.is_invite_enabled() is False

    # 3. inv_days
    await iflow.handle_invite_callback(cb, ["menu", "biz", "inv_days", "14"], "inv_days")
    assert cd.get_invite_reward_days() == 14

    # 4. inv_set_days
    await iflow.handle_invite_callback(cb, ["menu", "biz", "inv_set_days"], "inv_set_days")
    assert st.inv_inp["stage"] == "await_invite_days"
    assert st.inv_inp["panel_id"] == 50

    # 5. inv_top
    await iflow.handle_invite_callback(cb, ["menu", "biz", "inv_top", "0"], "inv_top")
    assert "Ən çox dəvət edənlər" in edited[-1][0]


# ───────────────────────── 5. Mətn Daxiletmə Axını (Input Router) ─────────────────────────
@pytest.mark.asyncio
async def test_invite_days_text_input(monkeypatch):
    bot = types.SimpleNamespace(edit_message_text=AsyncMock())
    ctx = types.SimpleNamespace(bot=bot)
    monkeypatch.setattr(in_router, "get_context", lambda: ctx)
    monkeypatch.setattr(in_router, "is_creator", lambda uid: uid == 1)

    st.inv_inp.update(stage="await_invite_days", chat_id=1, panel_id=50)

    # Creator yoxlanışı
    msg_non_admin = FakeMessage(chat_id=1, user_id=2, text="5")
    assert await in_router.waiting_invite_days(msg_non_admin) is False

    msg_admin = FakeMessage(chat_id=1, user_id=1, text="invalid")
    assert await in_router.waiting_invite_days(msg_admin) is True

    # Səhv daxiletmə
    await in_router.invite_days_input(msg_admin)
    assert st.inv_inp["stage"] == "await_invite_days"  # hələ də gözləyir
    assert bot.edit_message_text.called
    assert "Yalnız 1–3650 arası" in bot.edit_message_text.call_args.kwargs["text"]

    # Düzgün daxiletmə
    msg_admin.text = "7"
    await in_router.invite_days_input(msg_admin)
    assert st.inv_inp["stage"] is None
    assert cd.get_invite_reward_days() == 7


# ───────────────────────── 6. /start Referal Axını (Start Plugin) ─────────────────────────
@pytest.mark.asyncio
async def test_start_referral_success(fakedb, monkeypatch):
    cd.set_invite_enabled(True)
    cd.set_invite_reward_days(5)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # Yeni istifadəçi (first_seen yoxdur)
    msg = FakeMessage(chat_id=200, user_id=200, text="/start inv_555", first_name="Qonaq", bot=bot)

    monkeypatch.setattr(splug, "send_welcome", AsyncMock())
    monkeypatch.setattr(splug, "build_private_text", lambda *a: "Welcome")

    await splug.start_command(msg)

    # 1. Dəvət qeydə alındı
    assert fakedb.is_invited(200) is True
    # 2. İnviter 555-ə premium verildi
    assert fakedb.get_premium(555) is not None
    # 3. İnviterə təbrik bildirişi göndərildi
    sent_msgs = [c.args for c in bot.send_message.call_args_list]
    assert any(c[0] == 555 and "Yeni dəvət!" in c[1] for c in sent_msgs)


@pytest.mark.asyncio
async def test_start_referral_self_invite_rejected(fakedb, monkeypatch):
    cd.set_invite_enabled(True)
    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    # Özü-özünü dəvət edir
    msg = FakeMessage(chat_id=555, user_id=555, text="/start inv_555", first_name="Özüm", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)
    assert fakedb.is_invited(555) is False
    assert fakedb.get_premium(555) is None


@pytest.mark.asyncio
async def test_start_referral_old_user_rejected(fakedb, monkeypatch):
    cd.set_invite_enabled(True)
    # İstifadəçi 1 gün əvvəldən qeydiyyatdan keçib
    fakedb.users[300] = {"user_id": 300, "first_seen": int(time.time()) - 86400}

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    msg = FakeMessage(chat_id=300, user_id=300, text="/start inv_555", first_name="Köhnə", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)
    assert fakedb.is_invited(300) is False
    assert fakedb.get_premium(555) is None


@pytest.mark.asyncio
async def test_start_referral_disabled_rejected(fakedb, monkeypatch):
    cd.set_invite_enabled(False)

    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot, lang_ask_first=None)
    bot.data["app_context"] = ctx

    msg = FakeMessage(chat_id=400, user_id=400, text="/start inv_555", first_name="Yeni", bot=bot)
    monkeypatch.setattr(splug, "send_welcome", AsyncMock())

    await splug.start_command(msg)
    assert fakedb.is_invited(400) is False
    assert fakedb.get_premium(555) is None


@pytest.mark.asyncio
async def test_start_payload_show_invite_card(monkeypatch):
    bot = types.SimpleNamespace(
        me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")),
        data={},
    )
    ctx = types.SimpleNamespace(bot=bot)
    bot.data["app_context"] = ctx

    msg = FakeMessage(chat_id=400, user_id=400, text="/start invite", first_name="User", bot=bot)
    sent = []

    async def fake_send_media(b, cid, text, kb, slot):
        sent.append((cid, text, kb, slot))

    monkeypatch.setattr(splug, "send_media", fake_send_media)

    await splug.start_command(msg)
    assert len(sent) == 1
    assert sent[0][3] == "invite"
    assert "Dostlarını dəvət et" in sent[0][1]


# ───────────────────────── 7. /invite Komandası ─────────────────────────
@pytest.mark.asyncio
async def test_invite_command_in_private_and_group():
    # Şəxsi çat
    bot = types.SimpleNamespace(me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")))
    pm_msg = FakeMessage(chat_id=10, user_id=10, text="/invite", first_name="Dost", bot=bot)

    await cmd_h.invite_command(pm_msg)
    assert pm_msg.answer.called
    assert "Dostlarını dəvət et" in pm_msg.answer.call_args.args[0]

    # Qrup çatı
    grp_msg = FakeMessage(
        chat_id=-1001,
        user_id=10,
        text="/invite",
        chat_type="supergroup",
        title="Test Qrupu",
        first_name="Dost",
        bot=bot,
    )

    await cmd_h.invite_command(grp_msg)
    assert grp_msg.reply.called
    assert "t.me/testbot?start=invite" in str(grp_msg.reply.call_args)


# ───────────────────────── 8. İstifadəçi Menyu Düyməsi ─────────────────────────
@pytest.mark.asyncio
async def test_user_menu_invite_button():
    bot = types.SimpleNamespace(me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")))
    user = FakeUser(user_id=1, first_name="Test")

    cd.set_invite_enabled(True)
    _, kb_on = await gviews.user_menu_view(bot, user)
    all_btns = [b for row in kb_on.inline_keyboard for b in row]
    assert any(b.callback_data == "umenu:invite" for b in all_btns)

    cd.set_invite_enabled(False)
    _, kb_off = await gviews.user_menu_view(bot, user)
    all_btns_off = [b for row in kb_off.inline_keyboard for b in row]
    assert not any(b.callback_data == "umenu:invite" for b in all_btns_off)


# ───────────────────────── 9. Start Keyboard & Guide İnteqrasiyası ─────────────────────────
def test_private_keyboard_invite_button():
    cd.set_invite_enabled(True)
    kb_on = splug.private_keyboard("testbot", 123)
    btns_on = [b.callback_data for row in kb_on.inline_keyboard for b in row if b.callback_data]
    assert "sg:invite" in btns_on

    cd.set_invite_enabled(False)
    kb_off = splug.private_keyboard("testbot", 123)
    btns_off = [b.callback_data for row in kb_off.inline_keyboard for b in row if b.callback_data]
    assert "sg:invite" not in btns_off


@pytest.mark.asyncio
async def test_start_guide_invite_callback(monkeypatch):
    bot = types.SimpleNamespace(me=AsyncMock(return_value=types.SimpleNamespace(username="testbot")))
    cb = FakeCallback(chat_id=50, user_id=50, data="sg:invite")
    cb.bot = bot

    sent = []

    async def fake_send_media(b, cid, text, kb, slot):
        sent.append((cid, text, kb, slot))

    monkeypatch.setattr(splug, "send_media", fake_send_media)
    monkeypatch.setattr(splug, "delete_quietly", AsyncMock())

    await splug.start_guide(cb)
    assert len(sent) == 1
    assert sent[0][3] == "invite"
    assert "Dostlarını dəvət et" in sent[0][1]
    # Klaviaturada sg:back geri qayıtma düyməsi var
    kb = sent[0][2]
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    assert "sg:back" in cbs
    assert "sg:close" in cbs


def test_build_private_text_invite_filter():
    cd.set_invite_enabled(True)
    text_on = splug.build_private_text("Əli", "testbot", 123)
    assert "/invite" in text_on

    cd.set_invite_enabled(False)
    text_off = splug.build_private_text("Əli", "testbot", 123)
    assert "/invite" not in text_off


def test_sample_message_invite():
    text, kb = splug.sample_message("invite", "Əli", 123, "testbot")
    assert "Dostlarını dəvət et" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]
    assert "sg:back" in cbs

