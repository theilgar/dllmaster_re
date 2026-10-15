"""☕ Bəxşiş mesajı: adama göndərmə, istifadəçi kartı düyməsi, free / hamıya broadcast."""
import contextlib
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus.handlers.broadcast_flow as bf
import bot.menus.handlers.tipmsg_flow as tf
import bot.menus.state as st
import bot.menus.views.tipmsg as tv


class FakeDB:
    def __init__(self):
        self.users = {10: {"user_id": 10, "first_name": "Əli", "username": "ali_x", "blocked": 0},
                      11: {"user_id": 11, "first_name": "Vəli", "username": None, "blocked": 0}}
        self.targets = {"free": [("user", 10), ("user", 11)], "users": [("user", 10), ("user", 11), ("user", 99)],
                        "groups": [("group", -100501), ("group", -100502)]}
        self.chats = {-100501: {"chat_id": -100501, "title": "Qrup A", "type": "supergroup"}}
        self.blocked = []

    def broadcast_targets(self, target):
        return list(self.targets.get(target, []))

    def get_user(self, uid):
        return self.users.get(uid)

    def find_user_by_username(self, name):
        return next((u for u in self.users.values() if (u["username"] or "").lower() == name.lower()), None)

    def get_chat(self, chat_id):
        return self.chats.get(chat_id)

    def set_blocked(self, uid, on):
        self.blocked.append(uid)

    def set_chat_active(self, *a):
        pass


class FakeBot:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail or {}

    async def send_message(self, chat_id=None, text=None, **kw):
        if chat_id in self.fail:
            raise self.fail[chat_id]
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return types.SimpleNamespace(message_id=100 + len(self.sent), chat=types.SimpleNamespace(id=chat_id))

    async def edit_message_text(self, **kw):
        self.sent.append({"edit": True, **kw})

    async def get_chat(self, ref):
        if ref in (-100501, "@qrup_a"):
            return types.SimpleNamespace(id=-100501, title="Qrup A", type="supergroup")
        if ref == 85029:
            return types.SimpleNamespace(id=85029, title=None, type="private")
        raise RuntimeError("chat not found")


@pytest.fixture
def env(monkeypatch):
    db, bot = FakeDB(), FakeBot()
    ctx = types.SimpleNamespace(bot=bot, creator_id=1)
    for mod in (tf, tv, bf):
        monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(tf, "get_context", lambda: ctx)
    monkeypatch.setattr(bf, "get_context", lambda: ctx)
    monkeypatch.setattr(st, "_CTX", ctx)

    async def uname():
        return "dllbot"

    monkeypatch.setattr(tf, "bot_username", uname)
    monkeypatch.setattr(tv, "bot_username", uname)
    monkeypatch.setattr(tf, "is_creator", lambda uid: uid == 1)
    st.tp.clear(); st.tp["stage"] = None
    st.bc.clear(); st.bc["stage"] = None
    return types.SimpleNamespace(db=db, bot=bot, ctx=ctx)


def _cb(data="menu:tipmsg", user=1):
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=5, answer=AsyncMock(
        return_value=types.SimpleNamespace(message_id=77)), edit_text=AsyncMock())
    return types.SimpleNamespace(data=data, from_user=types.SimpleNamespace(id=user), message=msg, answer=AsyncMock())


@pytest.fixture
def shown(monkeypatch):
    log = []

    async def fake_edit(cb, text, kb=None):
        log.append((text, kb))

    monkeypatch.setattr(tf, "safe_edit_cb", fake_edit)
    return log


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


# ── mesajın özü ──
def test_tip_message_has_amount_buttons_and_close():
    from bot.plugins.start_plugin import tip_message
    text, kb = tip_message("dllbot")
    datas = _datas(kb)
    assert "tip:10" in datas and "tip:500" in datas and "tip:custom" in datas and "tip_prompt:close" in datas
    assert not any(d.startswith("menu:") for d in datas)               # naviqasiya sətri (🏠) yoxdur
    assert "Bəxşiş" in text


# ── menyu ──
async def test_menu_shows_counts_and_actions(env):
    text, kb = await tv.tip_menu_view()
    assert "<b>2</b>" in text and "<b>3</b>" in text
    datas = _datas(kb)
    for needed in ("menu:tipmsg:to", "menu:tipmsg:prev", "menu:tipmsg:bc:free", "menu:tipmsg:bc:users"):
        assert needed in datas


def test_com_section_and_user_card_link_to_tip_message(monkeypatch):
    import bot.menus  # noqa: F401
    from bot.menus.views import system_hub as H

    monkeypatch.setattr(H, "get_context", lambda: types.SimpleNamespace(userbot_enabled=False))
    _, kb = H.com_view()
    assert "menu:tipmsg" in _datas(kb)


# ── adama göndərmə ──
async def test_ask_for_user_sets_waiting_state(env, shown):
    await tf._mcb_tipmsg(_cb(), ["menu", "tipmsg", "to"], "tipmsg")
    assert st.tp["stage"] == "await_user" and "ID" in shown[-1][0]
    assert st.menu_waiting_text(1) is True                            # musiqi axtarışı bu mətni udmasın


def _msg(text, user=1):
    return types.SimpleNamespace(text=text, from_user=types.SimpleNamespace(id=user), delete=AsyncMock(),
                                 reply=AsyncMock(), answer=AsyncMock())


async def test_send_by_username(env):
    st.tp.update(stage="await_user", chat_id=1, panel_id=5)
    await tf.tip_user_input(_msg("@ali_x"))
    sent = [m for m in env.bot.sent if m.get("chat_id") == 10]
    assert len(sent) == 1 and sent[0]["reply_markup"] is not None and "Bəxşiş" in sent[0]["text"]
    assert st.tp["stage"] is None
    assert any("göndərildi" in m.get("text", "") for m in env.bot.sent if m.get("edit"))


async def test_send_by_numeric_id_even_if_unknown(env):
    st.tp.update(stage="await_user", chat_id=1, panel_id=5)
    await tf.tip_user_input(_msg("555"))
    assert [m["chat_id"] for m in env.bot.sent if not m.get("edit")] == [555]


async def test_unknown_username_keeps_waiting(env):
    st.tp.update(stage="await_user", chat_id=1, panel_id=5)
    await tf.tip_user_input(_msg("@nobody_here"))
    assert st.tp["stage"] == "await_user" and not [m for m in env.bot.sent if not m.get("edit")]
    assert "tapılmadı" in env.bot.sent[-1]["text"]


async def test_bad_format_and_self_are_rejected(env):
    st.tp.update(stage="await_user", chat_id=1, panel_id=5)
    await tf.tip_user_input(_msg("!!!"))
    await tf.tip_user_input(_msg("1"))                                 # creator özü
    assert st.tp["stage"] == "await_user" and not [m for m in env.bot.sent if not m.get("edit")]


async def test_blocked_user_reports_clearly(env):
    from aiogram.exceptions import TelegramForbiddenError
    env.bot.fail[10] = TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
    st.tp.update(stage="await_user", chat_id=1, panel_id=5)
    await tf.tip_user_input(_msg("10"))
    assert 10 in env.db.blocked
    assert any("bloklayıb" in m.get("text", "") for m in env.bot.sent if m.get("edit"))


async def test_only_creator_text_is_captured(env):
    st.tp.update(stage="await_user")
    assert await tf.waiting_tip_user(_msg("x", user=1)) is True
    assert await tf.waiting_tip_user(_msg("x", user=2)) is False


# ── istifadəçi kartından ──
async def test_send_from_user_card(env, shown, monkeypatch):
    import bot.menus.views.users_chats as uc

    async def fake_card(uid, back):
        return f"card {uid} back={back}", None

    monkeypatch.setattr(uc, "user_card", fake_card)
    cb = _cb("menu:tipmsg:u:10:users:all:0")
    await tf._mcb_tipmsg(cb, ["menu", "tipmsg", "u", "10", "users", "all", "0"], "tipmsg")
    assert [m["chat_id"] for m in env.bot.sent] == [10]
    text = shown[-1][0]
    assert "back=users:all:0" in text and "göndərildi" in text         # geri ünvanı tam saxlanıb


# ── broadcast ──
async def test_broadcast_confirm_prepares_bc_state(env, shown):
    cb = _cb()
    await tf._mcb_tipmsg(cb, ["menu", "tipmsg", "bc", "free"], "tipmsg")
    assert st.bc["stage"] == "confirm" and st.bc["target"] == "free" and "tip:10" in _datas(st.bc["markup"])
    assert st.bc["preview_id"] == 77 and "Bəxşiş" in st.bc["text"]
    kb = cb.message.answer.await_args.kwargs["reply_markup"]
    assert "menu:bc_send" in _datas(kb)                               # mövcud göndər düyməsi işləyir


async def test_broadcast_with_no_recipients(env, shown):
    env.db.targets["free"] = []
    cb = _cb()
    await tf._mcb_tipmsg(cb, ["menu", "tipmsg", "bc", "free"], "tipmsg")
    assert st.bc["stage"] is None and cb.answer.await_args.kwargs.get("show_alert") is True


async def test_run_broadcast_sends_text_with_tip_buttons_to_free_users(env, monkeypatch):
    from bot.plugins.start_plugin import tip_message
    text, _ = tip_message("dllbot")
    _, markup = tip_message("dllbot")
    st.bc.update(stage="sending", target="free", text=text, markup=markup, chat_id=1, panel_id=5, stop=False)
    monkeypatch.setattr(bf, "BROADCAST_DELAY", 0)
    await bf.run_broadcast()
    sent = [m for m in env.bot.sent if not m.get("edit")]
    assert [m["chat_id"] for m in sent] == [10, 11]
    assert all("tip:10" in _datas(m["reply_markup"]) for m in sent)
    assert all(m["text"] == text for m in sent)


async def test_plain_broadcast_has_no_markup(env, monkeypatch):
    st.bc.update(stage="sending", target="users", text="salam", chat_id=1, panel_id=5, stop=False)
    monkeypatch.setattr(bf, "BROADCAST_DELAY", 0)
    await bf.run_broadcast()
    sent = [m for m in env.bot.sent if not m.get("edit")]
    assert len(sent) == 3 and all(m["reply_markup"] is None for m in sent)


def test_broadcast_target_free_excludes_premium_and_creator(monkeypatch):
    """DB sorğusu: blok yoxdur, premium və creator xaric."""
    import common.database as cd

    class Conn(contextlib.AbstractContextManager):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            if "FROM premium" in sql:
                return [{"user_id": 3}]
            return [{"user_id": 1}, {"user_id": 2}, {"user_id": 3}, {"user_id": 4}]

    db = object.__new__(cd.Database) if hasattr(cd, "Database") else None
    if db is None:
        pytest.skip("Database sinfi tapılmadı")
    db._conn = lambda: Conn()
    monkeypatch.setattr(cd, "creator_id", lambda: 1)
    assert db.broadcast_targets("free") == [("user", 2), ("user", 4)]


# ── qrup üçün ──
def test_group_variant_uses_deep_link_instead_of_payment_buttons():
    from bot.plugins.start_plugin import tip_message
    text, kb = tip_message("dllbot", group=True)
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == 1 and buttons[0].url == "https://t.me/dllbot?start=tip"
    assert not any(b.callback_data for b in buttons)                    # qrupda ödəniş / bağla düyməsi yoxdur
    assert "şəxsi çatda" in text


def test_start_handler_knows_tip_deep_link():
    import inspect
    import bot.plugins.start_plugin as sp
    assert 'p_val == "tip"' in inspect.getsource(sp)


async def test_menu_offers_group_actions(env):
    text, kb = await tv.tip_menu_view()
    assert "menu:tipmsg:g" in _datas(kb) and "menu:tipmsg:bc:groups" in _datas(kb) and "<b>2</b>" in text


async def test_ask_for_group_sets_waiting_state(env, shown):
    await tf._mcb_tipmsg(_cb(), ["menu", "tipmsg", "g"], "tipmsg")
    assert st.tp["stage"] == "await_group" and st.menu_waiting_text(1) is True


async def test_send_to_group_by_id_and_username(env):
    for ref in ("-100501", "@qrup_a", "t.me/qrup_a"):
        env.bot.sent.clear()
        st.tp.update(stage="await_group", chat_id=1, panel_id=5)
        await tf.tip_group_input(_msg(ref))
        sent = [m for m in env.bot.sent if not m.get("edit")]
        assert [m["chat_id"] for m in sent] == [-100501], ref
        assert sent[0]["reply_markup"].inline_keyboard[0][0].url.endswith("?start=tip")
        assert st.tp["stage"] is None


async def test_group_input_rejects_users_unknown_and_bad_format(env):
    for ref, word in (("85029", "qrup deyil"), ("-100999", "tapılmadı"), ("!!", "format")):
        st.tp.update(stage="await_group", chat_id=1, panel_id=5)
        await tf.tip_group_input(_msg(ref))
        assert st.tp["stage"] == "await_group"
        assert word in env.bot.sent[-1]["text"], ref
    assert not [m for m in env.bot.sent if not m.get("edit")]


async def test_send_from_chat_card(env, shown, monkeypatch):
    import bot.menus.views.users_chats as uc

    async def fake_card(chat_id, back, live=True):
        return f"chat {chat_id} back={back} live={live}", None

    monkeypatch.setattr(uc, "chat_card", fake_card)
    await tf._mcb_tipmsg(_cb(), ["menu", "tipmsg", "c", "-100501", "chats", "all", "0"], "tipmsg")
    assert [m["chat_id"] for m in env.bot.sent] == [-100501]
    text = shown[-1][0]
    assert "back=chats:all:0" in text and "Qrup A" in text and "göndərildi" in text


async def test_group_failure_message_is_group_specific(env, shown, monkeypatch):
    import bot.menus.views.users_chats as uc
    from aiogram.exceptions import TelegramForbiddenError

    async def fake_card(chat_id, back, live=True):
        return "card", None

    monkeypatch.setattr(uc, "chat_card", fake_card)
    env.bot.fail[-100501] = TelegramForbiddenError(method=None, message="Forbidden: bot was kicked")
    await tf._mcb_tipmsg(_cb(), ["menu", "tipmsg", "c", "-100501", "chats", "all", "0"], "tipmsg")
    assert "çıxarılıb" in shown[-1][0] and "bloklayıb" not in shown[-1][0]


async def test_broadcast_to_groups_uses_group_message(env, shown, monkeypatch):
    cb = _cb()
    await tf._mcb_tipmsg(cb, ["menu", "tipmsg", "bc", "groups"], "tipmsg")
    assert st.bc["target"] == "groups" and st.bc["markup"].inline_keyboard[0][0].url.endswith("?start=tip")
    monkeypatch.setattr(bf, "BROADCAST_DELAY", 0)
    st.bc["stage"] = "sending"
    await bf.run_broadcast()
    sent = [m for m in env.bot.sent if not m.get("edit")]
    assert [m["chat_id"] for m in sent] == [-100501, -100502]
    assert all(m["reply_markup"].inline_keyboard[0][0].url for m in sent)
