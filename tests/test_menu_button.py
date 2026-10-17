"""⌨️ Menyu düymələri: həmişə görünən klaviatura (istifadəçi başına) + Telegram komanda siyahısı (sahib paneli, 🤖 avto / ✋ manual)."""
import asyncio
import datetime
import re
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher
from aiogram.filters import CommandObject
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import Chat, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, User

from bot.core import menu_button as mb
from bot.plugins import menu_button_plugin as P
from common import i18n


class Store:
    def __init__(self):
        self.d = {}

    def get(self, key, default=None):
        v = self.d.get(key)
        return default if v in (None, "") else v

    def put(self, key, value):
        self.d[key] = str(value)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(mb, "_cfg", Store())
    i18n.reload()


def run(coro):
    return asyncio.run(coro)


# ───────────── ortaq modul ─────────────
def test_every_language_has_every_label_and_a_usable_keyboard():
    for code in [i18n.DEFAULT, *i18n.LANGS]:
        kb = mb.keyboard(code, invite=True)
        assert isinstance(kb, ReplyKeyboardMarkup) and kb.is_persistent and kb.resize_keyboard
        texts = [b.text for row in kb.keyboard for b in row]
        assert len(texts) == len(set(texts)) == 7 and all(0 < len(t) <= 30 for t in texts)
        assert kb.input_field_placeholder
    assert [len(r) for r in mb.keyboard("az").keyboard] == [2, 2, 2]


def test_label_map_understands_every_language():
    m = mb.label_map()
    assert m["📋 Menyu"] == "menu" and m["📋 Menu"] == "menu" and m["📋 Меню"] == "menu"
    assert m["🔎 Search"] == "search" and m["🔎 Поиск"] == "search"
    assert set(m.values()) == set(mb.LABELS)
    assert "salam" not in m and "/menu" not in m


def test_keyboard_is_off_by_default_and_remembered_per_user():
    assert mb.kb_on(1) is False
    mb.set_kb(1, True)
    mb.set_kb(2, False)
    assert mb.kb_on(1) and not mb.kb_on(2) and not mb.kb_on(3)


@pytest.mark.parametrize("code", ["az", "en", "ru", "tr"])
def test_command_lists_are_valid_telegram_commands_in_every_language(code):
    for creator in (False, True):
        cmds = mb.commands(code, creator=creator)
        assert len(cmds) == len(mb.USER_COMMANDS) + (len(mb.CREATOR_COMMANDS) if creator else 0) <= 100
        for c in cmds:
            assert re.fullmatch(r"[a-z0-9_]{1,32}", c.command) and 3 <= len(c.description) <= 256
    assert mb.commands("en")[0].description == "Start the bot"
    assert mb.commands("ru")[1].description == "Открыть меню"


def test_every_listed_command_has_a_handler_somewhere():
    import pathlib
    src = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path("bot").rglob("*.py"))
    for name, _fn in mb.USER_COMMANDS + mb.CREATOR_COMMANDS:
        assert re.search(r'Command\([^)]*"%s"' % name, src), f"/{name} üçün handler tapılmadı"


class FakeBot:
    def __init__(self, current=None):
        self.calls = []
        self.current = current if current is not None else []

    async def set_my_commands(self, commands, scope=None, language_code=None):
        self.calls.append(("cmds", [c.command for c in commands], type(scope).__name__,
                           getattr(scope, "chat_id", None), language_code))

    async def set_chat_menu_button(self, chat_id=None, menu_button=None):
        self.calls.append(("menu_button", type(menu_button).__name__))

    async def get_my_commands(self, scope=None, language_code=None):
        return [types.SimpleNamespace(command=c) for c in self.current]


def test_apply_sets_default_and_per_language_lists_plus_creator_scope_and_menu_button():
    bot = FakeBot()
    res = run(mb.apply_commands(bot, creator_id=42))
    kinds = [(c[2], c[3], c[4]) for c in bot.calls if c[0] == "cmds"]
    codes = mb.lang_codes()
    assert ("BotCommandScopeDefault", None, None) in kinds
    assert all(("BotCommandScopeDefault", None, c) in kinds for c in codes)
    assert ("BotCommandScopeChat", 42, None) in kinds and all(("BotCommandScopeChat", 42, c) in kinds for c in codes)
    creator_list = next(c for c in bot.calls if c[0] == "cmds" and c[3] == 42 and c[4] is None)[1]
    assert "restart" in creator_list and "menu" in creator_list                  # sahibdə adi komandalar da var
    assert bot.calls[-1] == ("menu_button", "MenuButtonCommands")
    assert res["commands"] == len(mb.USER_COMMANDS) and mb.last_hash() == res["hash"]
    bot2 = FakeBot()
    run(mb.apply_commands(bot2, creator_id=0))
    assert not any(c[0] == "cmds" and c[2] == "BotCommandScopeChat" for c in bot2.calls)


def test_in_sync_needs_the_same_names_and_the_same_signature():
    names = [c for c, _fn in mb.USER_COMMANDS]
    assert not mb.in_sync(names, 42)                                                 # heç vaxt tətbiq olunmayıb
    run(mb.apply_commands(FakeBot(), 42))
    assert mb.in_sync(names, 42) and not mb.in_sync(names[:-1], 42) and not mb.in_sync(None, 42)
    assert mb.signature(42) != mb.signature(0)


def test_mode_defaults_to_manual():
    assert mb.cmds_mode() == "manual"
    mb.set_cmds_mode("auto")
    assert mb.cmds_mode() == "auto"
    mb.set_cmds_mode("whatever")
    assert mb.cmds_mode() == "manual"


# ───────────── plugin ─────────────
@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(P, "spawn", lambda coro: coro.close())
    ctx = MagicMock()
    ctx.dp = Dispatcher()
    ctx.bot = AsyncMock()
    ctx.bot.session = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.set_my_commands = AsyncMock()
    ctx.bot.set_chat_menu_button = AsyncMock()
    ctx.bot.get_my_commands = AsyncMock(return_value=[])
    ctx.is_creator = lambda uid: uid == 1
    ctx.creator_id = 1
    ctx.dp.feed_update = AsyncMock()
    P.setup(ctx)
    return ctx


def handler(env, name):
    for obs in env.dp.observers.values():
        for h in obs.handlers:
            if h.callback.__name__ == name:
                return h
    raise AssertionError(name)


def msg(env, text, uid=5, private=True):
    """Həqiqi aiogram Message (Update doğrulaması üçün) — cavablar env.bot-a metod obyekti kimi gedir."""
    chat = Chat(id=uid if private else -100, type="private" if private else "supergroup")
    return Message(message_id=77, date=datetime.datetime.now(), chat=chat, text=text,
                   from_user=User(id=uid, is_bot=False, first_name="Ad")).as_(env.bot)


def calls(env, kind):
    return [c.args[0] for c in env.bot.await_args_list if c.args and isinstance(c.args[0], kind)]


def sent_markups(env):
    return [c.kwargs.get("reply_markup") for c in env.bot.send_message.await_args_list]


def test_keyboard_command_cycles_off_short_full_off_and_accepts_names(env, monkeypatch):
    monkeypatch.setattr(P.mb, "full_keyboard", P.mb.full_keyboard)
    h = handler(env, "keyboard_cmd").callback

    async def go(arg):
        await h(msg(env, f"/keyboard {arg or ''}".strip()), CommandObject(command="keyboard", args=arg))
    run(go(None))
    assert mb.kb_style(5) == "short" and isinstance(sent_markups(env)[-1], ReplyKeyboardMarkup)
    run(go("full"))
    assert mb.kb_style(5) == "full" and "Tam menyu" in env.bot.send_message.await_args.args[1]
    run(go(None))
    assert mb.kb_style(5) == "off" and isinstance(sent_markups(env)[-1], ReplyKeyboardRemove)
    run(go("on"))
    run(go("on"))
    assert mb.kb_style(5) == "short"                                                  # "on" iki dəfə də qısa saxlayır
    run(go("tam"))
    assert mb.kb_style(5) == "full"
    run(go("off"))
    assert not mb.kb_on(5)


def test_keyboard_refuses_groups(env):
    run(handler(env, "keyboard_cmd").callback(msg(env, "/keyboard", private=False), CommandObject(command="keyboard", args=None)))
    assert not mb.kb_on(5) and not env.bot.send_message.await_count
    assert calls(env, SendMessage) and "yalnız şəxsi çatda" in calls(env, SendMessage)[-1].text


def _press(env, text, uid=5):
    m = msg(env, text, uid)
    verdict = run(handler(env, "on_label").filters[1].callback(m))
    if verdict:
        run(handler(env, "on_label").callback(m, **verdict))
    return verdict


def test_label_filter_matches_only_button_texts_in_private_chats(env):
    assert _press(env, "salam necəsən") is False and _press(env, "/menu") is False
    flt = handler(env, "on_label").filters[1].callback
    assert run(flt(msg(env, "📋 Menyu", private=False))) is False
    assert run(flt(msg(env, "📋 Menyu"))) == {"kb_action": "menu"}
    assert run(flt(msg(env, "📋 Menu"))) == {"kb_action": "menu"} and run(flt(msg(env, "📋 Меню"))) == {"kb_action": "menu"}


@pytest.mark.parametrize("text,cmd", [("📋 Menyu", "/menu"), ("🎧 Format", "/codec"), ("📚 Pleylistlər", "/playlist"),
                                      ("💎 Premium", "/premium"), ("🌐 Dil", "/lang"), ("🎁 Dəvət et", "/invite")])
def test_pressing_a_button_runs_the_matching_command_and_cleans_up(env, text, cmd):
    mb.set_kb(5, True)
    _press(env, text)
    update = env.dp.feed_update.await_args.args[1]
    assert update.message.text == cmd and update.message.entities[0].type == "bot_command"
    assert update.message.entities[0].length == len(cmd) and update.message.message_id == 77
    assert update.message.from_user.id == 5 and update.message.chat.type == "private"
    assert calls(env, DeleteMessage) and calls(env, DeleteMessage)[0].message_id == 77       # düymə mətni silinir
    assert not calls(env, SendMessage)                                                       # klaviatura açıqdır — əlavə mesaj yox


def test_search_button_shows_a_hint_with_inline_search(env):
    mb.set_kb(5, True)
    _press(env, "🔎 Axtar")
    env.dp.feed_update.assert_not_awaited()
    sent = calls(env, SendMessage)[-1]
    assert sent.reply_markup.inline_keyboard[0][0].switch_inline_query_current_chat == ""


def test_a_stale_keyboard_is_hidden_but_the_action_still_runs(env):
    _press(env, "📋 Menyu")                                                          # klaviatura söndürülüb, düymə köhnədir
    assert isinstance(calls(env, SendMessage)[0].reply_markup, ReplyKeyboardRemove)
    assert env.dp.feed_update.await_args.args[1].message.text == "/menu"


def test_button_press_beats_text_waiters_because_it_registers_first(env):
    # PRIORITY=3 → bütün digər plugin-lərdən əvvəl; handler dp-də ilk sıradadır
    first = next(h for obs in env.dp.observers.values() for h in obs.handlers)
    assert P.PRIORITY < 10 and first.callback.__name__ in ("keyboard_cmd", "on_label", "menubutton_cmd")


# ── sahib paneli ──
def callback(env, data, uid=1):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = uid
    cb.from_user.first_name = "Ad"
    cb.message.chat.type = "private"
    cb.message.chat.id = uid
    cb.message.edit_text = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.answer = AsyncMock()
    run(handler(env, "mbtn_cb").callback(cb))
    return cb


def shown(cb):
    call = cb.message.edit_text.await_args
    return call.args[0], [[(b.text, b.callback_data) for b in r] for r in call.kwargs["reply_markup"].inline_keyboard]


def test_creator_panel_shows_state_and_rules(env):
    env.bot.get_my_commands = AsyncMock(return_value=[types.SimpleNamespace(command="start")])
    text, rows = shown(callback(env, "mbtn:panel"))
    assert "Menyu düymələri" in text and "Telegram-da: <b>1</b>" in text and "fərqlidir" in text and "✋" in text
    assert "qısa 0 · tam 0" in text and "🔴 Deaktiv" in text
    flat = [b for r in rows for b in r]
    assert ("🔄 Tətbiq et", "mbtn:apply") in flat and ("Rejim: ✋ Manual", "mbtn:mode") in flat
    assert ("⌨️ Mənim klaviaturam: 🔴 Deaktiv", "mbtn:kb:panel") in flat
    assert rows[-1][-1][1] == "mbtn:close" and "menu:sys" in [d for _, d in rows[-1]]          # naviqasiya sonuncu
    assert all(len(d.encode()) <= 64 for _, d in flat) and all(len(r) <= 2 for r in rows[:-1])


def test_apply_pushes_the_commands_and_reports(env):
    cb = callback(env, "mbtn:apply")
    assert env.bot.set_my_commands.await_count >= 2 and env.bot.set_chat_menu_button.await_count == 1
    assert "tətbiq olundu" in shown(cb)[0] and mb.last_hash()
    env.bot.get_my_commands = AsyncMock(return_value=[types.SimpleNamespace(command=c) for c, _fn in mb.USER_COMMANDS])
    assert "✅ eynidir" in shown(callback(env, "mbtn:panel"))[0]


def test_apply_failure_is_reported_not_raised(env):
    env.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("Too Many Requests"))
    cb = callback(env, "mbtn:apply")
    assert "Too Many Requests" in shown(cb)[0] and not mb.last_hash()


def test_mode_button_switches_between_manual_and_auto(env):
    _, rows = shown(callback(env, "mbtn:mode"))
    assert mb.cmds_mode() == "auto" and ("Rejim: 🤖 Avto", "mbtn:mode") in [b for r in rows for b in r]
    callback(env, "mbtn:mode")
    assert mb.cmds_mode() == "manual"


def test_only_the_creator_can_use_the_panel_but_everyone_can_toggle_their_keyboard(env):
    cb = callback(env, "mbtn:apply", uid=9)
    env.bot.set_my_commands.assert_not_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert") is True
    callback(env, "mbtn:mode", uid=9)
    assert mb.cmds_mode() == "manual"


def test_user_menu_toggle_cycles_the_style_and_refreshes_the_menu(env, monkeypatch):
    views = types.ModuleType("bot.menus.views.groups")
    views.user_menu_view = AsyncMock(return_value=("menyu", MagicMock(inline_keyboard=[])))
    monkeypatch.setitem(__import__("sys").modules, "bot.menus.views.groups", views)
    callback(env, "mbtn:kb:menu", uid=9)
    assert mb.kb_style(9) == "short" and isinstance(sent_markups(env)[-1], ReplyKeyboardMarkup)
    views.user_menu_view.assert_awaited()
    callback(env, "mbtn:kb:menu", uid=9)
    assert mb.kb_style(9) == "full"
    callback(env, "mbtn:kb:menu", uid=9)
    assert mb.kb_style(9) == "off" and isinstance(sent_markups(env)[-1], ReplyKeyboardRemove)


def test_user_menu_view_has_the_keyboard_toggle(monkeypatch):
    from bot.menus.views import groups
    monkeypatch.setattr(groups, "get_db", lambda: MagicMock(get_premium=lambda uid: None))
    mb.set_kb(8, True)
    text, kb = run(groups.user_menu_view(MagicMock(), types.SimpleNamespace(id=8, first_name="Ad")))
    row = next(r for r in kb.inline_keyboard if r[0].callback_data == "mbtn:kb:menu")
    assert "🟢 Qısa" in row[0].text
    mb.set_kb_style(8, "full")
    assert "🟢 Tam" in next(r for r in run(groups.user_menu_view(MagicMock(), types.SimpleNamespace(id=8, first_name="Ad")))[1].inline_keyboard
                           if r[0].callback_data == "mbtn:kb:menu")[0].text
    mb.set_kb(8, False)
    text, kb = run(groups.user_menu_view(MagicMock(), types.SimpleNamespace(id=8, first_name="Ad")))
    assert "🔴" in next(r for r in kb.inline_keyboard if r[0].callback_data == "mbtn:kb:menu")[0].text


def test_language_change_refreshes_the_keyboard_in_the_new_language_only_when_it_is_on(env):
    run(env.menu_kb_refresh(5, 5))
    env.bot.send_message.assert_not_awaited()                                          # bağlıdır — toxunulmur
    mb.set_kb(5, True)
    tok = i18n.use("en")
    try:
        run(env.menu_kb_refresh(5, 5))
    finally:
        i18n.reset(tok)
    kb = sent_markups(env)[-1]
    assert [b.text for b in kb.keyboard[0]] == ["📋 Menu", "🔎 Search"]


def test_system_hub_links_to_the_panel():
    from bot.menus.views.system_hub import sys_kb
    assert "mbtn:panel" in [b.callback_data for r in sys_kb().inline_keyboard for b in r]


# ── avto rejim: açılış yoxlaması ──
def auto_sync_coro(env, monkeypatch):
    holder = []
    monkeypatch.setattr(P, "spawn", lambda coro: holder.append(coro))
    monkeypatch.setattr(P.asyncio, "sleep", AsyncMock())
    P.setup(env)
    return holder[-1]


def test_auto_mode_updates_on_start_only_when_different(env, monkeypatch):
    mb.set_cmds_mode("auto")
    run(auto_sync_coro(env, monkeypatch))
    assert env.bot.set_chat_menu_button.await_count == 1                              # Telegram-da heç nə yox → yenilənir
    env.bot.set_chat_menu_button.reset_mock()
    env.bot.get_my_commands = AsyncMock(return_value=[types.SimpleNamespace(command=c) for c, _fn in mb.USER_COMMANDS])
    run(auto_sync_coro(env, monkeypatch))
    env.bot.set_chat_menu_button.assert_not_awaited()                                  # eynidir → toxunmur


def test_manual_mode_never_touches_telegram_on_start(env, monkeypatch):
    run(auto_sync_coro(env, monkeypatch))
    env.bot.set_my_commands.assert_not_awaited()


# ── real Dispatcher: düymə → həqiqətən /menu handler-i işləyir (iç-içə feed_update) ──
def test_real_dispatcher_routes_a_button_press_to_the_command_handler(monkeypatch):
    from aiogram import Bot
    from aiogram.filters import Command
    from aiogram.methods import DeleteMessage as DM
    from aiogram.types import Update

    seen, sent = [], []

    async def fake_call(self, method, request_timeout=None):
        sent.append(method)
        return True
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(P, "spawn", lambda coro: coro.close())
    bot = Bot(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    dp = Dispatcher()
    ctx = types.SimpleNamespace(dp=dp, bot=bot, is_creator=lambda uid: uid == 1, creator_id=1)
    P.setup(ctx)

    @dp.message(Command("menu"))
    async def menu_handler(message: Message):
        seen.append(message.text)

    @dp.message()
    async def search_fallback(message: Message):                                      # axtarış plugin-ini təmsil edir
        seen.append("AXTARIŞ:" + (message.text or ""))

    def update(text, uid=5):
        m = Message(message_id=90, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"), text=text,
                    from_user=User(id=uid, is_bot=False, first_name="Ad"))
        return Update(update_id=1, message=m)

    run(dp.feed_update(bot, update("📋 Menyu")))
    assert seen == ["/menu"]                                                          # axtarış fallback-ına düşmədi
    assert any(isinstance(m, DM) and m.message_id == 90 for m in sent)
    run(dp.feed_update(bot, update("📋 Меню")))
    assert seen == ["/menu", "/menu"]
    run(dp.feed_update(bot, update("Eminem Lose Yourself")))
    assert seen[-1] == "AXTARIŞ:Eminem Lose Yourself"                                  # adi mətn əvvəlki kimi axtarışa gedir


# ───────────── ⌨️ tam üslub: əsas menyunun BÜTÜN düymələri ─────────────
from aiogram.methods import AnswerCallbackQuery, EditMessageText  # noqa: E402
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: E402


def inline(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def test_full_keyboard_mirrors_every_actionable_button_and_keeps_the_layout():
    markup = inline(
        [InlineKeyboardButton(text="🎵 Musiqi", callback_data="menu:mus"), InlineKeyboardButton(text="📦 Depo", callback_data="menu:depo")],
        [InlineKeyboardButton(text="🌿 GitHub", url="https://github.com/x")],
        [InlineKeyboardButton(text="🔎 Axtar", switch_inline_query_current_chat=""),
         InlineKeyboardButton(text="📤 Paylaş", switch_inline_query="abc")],
        [InlineKeyboardButton(text="🎵 Musiqi", callback_data="menu:other")],            # eyni yazı — atılır
    )
    kb, mapping = mb.full_keyboard(markup, "az")
    assert [[b.text for b in r] for r in kb.keyboard] == [["🎵 Musiqi", "📦 Depo"], ["🌿 GitHub"], ["🔎 Axtar", "📤 Paylaş"]]
    assert kb.is_persistent and kb.resize_keyboard
    assert mapping == {"🎵 Musiqi": {"cb": "menu:mus"}, "📦 Depo": {"cb": "menu:depo"}, "🌿 GitHub": {"url": "https://github.com/x"},
                       "🔎 Axtar": {"swc": ""}, "📤 Paylaş": {"sw": "abc"}}


def test_the_real_creator_home_menu_becomes_a_keyboard_with_all_eleven_buttons():
    from common.menu import main_kb
    kb, mapping = mb.full_keyboard(main_kb(None), "az")
    cbs = [spec["cb"] for spec in mapping.values()]
    assert len(cbs) == 11 and all(c.startswith("menu:") for c in cbs)
    assert {"menu:aud", "menu:mus", "menu:depo", "menu:live", "menu:biz", "menu:com", "menu:bots", "menu:sys",
            "menu:main", "menu:help", "menu:close"} == set(cbs)
    assert max(len(r) for r in kb.keyboard) <= 3


def test_the_button_map_is_persisted_per_user_and_survives_a_restart():
    mb.store_map(7, {"A": {"cb": "x:1"}})
    mb.store_map(8, {"B": {"cb": "x:2"}})
    mb._map_cache.clear()                                                              # restart
    assert mb.load_map(7) == {"A": {"cb": "x:1"}} and mb.load_map(8) == {"B": {"cb": "x:2"}} and mb.load_map(9) == {}


def test_style_helpers_and_backward_compatible_storage():
    assert mb.kb_style(1) == "off" and mb.next_style("off") == "short" and mb.next_style("short") == "full"
    assert mb.next_style("full") == "off" and mb.next_style("bilinmir") == "short"
    mb._cfg.put("kb:3", "1")                                                           # ilk versiyada yazılmış dəyər
    assert mb.kb_style(3) == "short" and mb.kb_on(3)
    mb.set_kb_style(3, "full")
    assert mb.kb_style(3) == "full" and mb._cfg.get("kb:3") == "full"
    mb.set_kb(3, False)
    assert mb.kb_style(3) == "off"


def test_middleware_swallows_fake_answers_alerts_become_messages_and_touches_are_tracked():
    mw = mb.BridgeMiddleware()
    sent = []

    async def make_request(bot, method):
        sent.append(method)
        return "ok"
    mw.register("kbbridge:abc", 55)
    assert run(mw(make_request, None, AnswerCallbackQuery(callback_query_id="kbbridge:abc"))) is True and not sent
    assert run(mw(make_request, None, AnswerCallbackQuery(callback_query_id="kbbridge:abc", text="Xəta", show_alert=True))) is True
    assert len(sent) == 1 and isinstance(sent[0], SendMessage) and sent[0].chat_id == 55 and sent[0].text == "Xəta"
    sent.clear()
    assert run(mw(make_request, None, AnswerCallbackQuery(callback_query_id="real-id", text="ok"))) == "ok"   # həqiqi cavab keçir
    assert len(sent) == 1
    assert not mw.was_touched(55, 9)
    run(mw(make_request, None, EditMessageText(chat_id=55, message_id=9, text="x")))
    assert mw.was_touched(55, 9) and not mw.was_touched(55, 10)
    for i in range(1100):                                                              # yaddaş böyüməsin
        run(mw(make_request, None, EditMessageText(chat_id=1, message_id=i, text="x")))
    assert len(mw.touched) <= 1000


def test_full_keyboard_for_the_creator_and_for_a_normal_user(env, monkeypatch):
    h = handler(env, "keyboard_cmd").callback
    run(h(msg(env, "/keyboard full", uid=1), CommandObject(command="keyboard", args="full")))   # sahib
    kb = sent_markups(env)[-1]
    assert [len(r) for r in kb.keyboard] == [2, 2, 2, 2, 3] and sum(len(r) for r in kb.keyboard) == 11
    assert len(mb.load_map(1)) == 11 and "Tam menyu" in env.bot.send_message.await_args.args[1]
    views = types.ModuleType("bot.menus.views.groups")
    views.user_menu_view = AsyncMock(return_value=("t", inline([InlineKeyboardButton(text="🎛 Audio formatı", callback_data="codec:panel")],
                                                               [InlineKeyboardButton(text="🌐 Dil", callback_data="lang:menu")])))
    monkeypatch.setitem(__import__("sys").modules, "bot.menus.views.groups", views)
    run(h(msg(env, "/keyboard full", uid=5), CommandObject(command="keyboard", args="full")))   # adi istifadəçi
    assert mb.load_map(5) == {"🎛 Audio formatı": {"cb": "codec:panel"}, "🌐 Dil": {"cb": "lang:menu"}}
    assert mb.load_map(1) != mb.load_map(5)


def test_filter_uses_the_users_own_full_map_and_ignores_it_in_other_styles(env):
    flt = handler(env, "on_label").filters[1].callback
    mb.set_kb_style(5, "full")
    mb.store_map(5, {"🖥 Sistem": {"cb": "menu:sys"}, "🌐 Dil": {"cb": "lang:menu"}})
    assert run(flt(msg(env, "🖥 Sistem", uid=5))) == {"kb_spec": {"cb": "menu:sys"}}
    assert run(flt(msg(env, "🌐 Dil", uid=5))) == {"kb_spec": {"cb": "lang:menu"}}      # tam xəritə qısa etiketdən üstündür
    assert run(flt(msg(env, "🖥 Sistem", uid=6))) is False                               # başqasının xəritəsi tətbiq olunmur
    mb.set_kb_style(5, "short")
    assert run(flt(msg(env, "🖥 Sistem", uid=5))) is False
    assert run(flt(msg(env, "🌐 Dil", uid=5))) == {"kb_action": "lang"}


# ── real Dispatcher: tam üslubda düymə → həqiqi callback handler-i, panel "gözləmə" mesajında ──
def _full_env(monkeypatch, panel_handler):
    from aiogram import Bot
    from aiogram.filters import Command  # noqa: F401
    seen, requests = [], []
    holder = {}

    async def inner(bot, method):
        requests.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=500 + len(requests), date=datetime.datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=method.text).as_(bot)
        return True

    async def fake_call(self, method, request_timeout=None):
        return await holder["mw"](inner, self, method)
    monkeypatch.setattr(Bot, "__call__", fake_call)
    monkeypatch.setattr(P, "spawn", lambda coro: coro.close())
    bot = Bot(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    dp = Dispatcher()
    ctx = types.SimpleNamespace(dp=dp, bot=bot, is_creator=lambda uid: uid == 1, creator_id=1)
    P.setup(ctx)
    holder["mw"] = ctx._kb_bridge_mw

    @dp.callback_query(lambda cb: cb.data == "menu:sys")
    async def sys_panel(cb):
        seen.append(("cb", cb.data, cb.from_user.id, cb.message.chat.id))
        await panel_handler(cb)

    @dp.message()
    async def search_fallback(message: Message):
        seen.append(("search", message.text))

    def press(text, uid=1):
        m = Message(message_id=90, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"), text=text,
                    from_user=User(id=uid, is_bot=False, first_name="Ad"))
        run(dp.feed_update(bot, Update(update_id=1, message=m)))
    return types.SimpleNamespace(seen=seen, requests=requests, press=press)


from aiogram.types import Update  # noqa: E402


def test_pressing_a_full_menu_button_opens_the_real_panel_in_the_waiting_message(monkeypatch):
    async def panel(cb):
        await cb.answer()
        await cb.message.edit_text("🖥 SİSTEM PANELİ")
    f = _full_env(monkeypatch, panel)
    mb.set_kb_style(1, "full")
    mb.store_map(1, {"🖥 Sistem": {"cb": "menu:sys"}})
    f.press("🖥 Sistem")
    assert f.seen == [("cb", "menu:sys", 1, 1)]                                        # axtarışa düşmədi
    kinds = [type(m).__name__ for m in f.requests]
    assert kinds == ["SendMessage", "EditMessageText", "DeleteMessage"]               # ⏳ → panel, düymə mətni silindi
    assert f.requests[0].text == "⏳" and f.requests[1].text == "🖥 SİSTEM PANELİ" and f.requests[1].message_id == f.requests[1].message_id
    assert f.requests[2].message_id == 90                                              # yalnız istifadəçinin mətni silinir
    assert not any(isinstance(m, AnswerCallbackQuery) for m in f.requests)


def test_an_alert_only_answer_is_delivered_as_a_message_and_the_waiting_message_is_removed(monkeypatch):
    async def panel(cb):
        await cb.answer("⚠️ Yalnız sahib", show_alert=True)
    f = _full_env(monkeypatch, panel)
    mb.set_kb_style(1, "full")
    mb.store_map(1, {"🖥 Sistem": {"cb": "menu:sys"}})
    f.press("🖥 Sistem")
    texts = [m.text for m in f.requests if isinstance(m, SendMessage)]
    assert texts == ["⏳", "⚠️ Yalnız sahib"]
    deleted = [m.message_id for m in f.requests if isinstance(m, DeleteMessage)]
    assert 90 in deleted and len(deleted) == 2                                         # gözləmə mesajı + düymə mətni


def test_a_failing_handler_does_not_break_the_chat_and_cleans_up(monkeypatch):
    async def panel(cb):
        raise RuntimeError("boom")
    f = _full_env(monkeypatch, panel)
    mb.set_kb_style(1, "full")
    mb.store_map(1, {"🖥 Sistem": {"cb": "menu:sys"}})
    f.press("🖥 Sistem")
    assert len([m for m in f.requests if isinstance(m, DeleteMessage)]) == 2


def test_text_that_is_not_in_the_map_still_goes_to_search(monkeypatch):
    async def panel(cb):
        await cb.message.edit_text("x")
    f = _full_env(monkeypatch, panel)
    mb.set_kb_style(1, "full")
    mb.store_map(1, {"🖥 Sistem": {"cb": "menu:sys"}})
    f.press("Eminem Lose Yourself")
    f.press("🖥 Sistem", uid=2)                                                        # başqa istifadəçi — onun xəritəsi yoxdur
    assert f.seen == [("search", "Eminem Lose Yourself"), ("search", "🖥 Sistem")]


# ───────────── 👤 istifadəçi menyusunu sahib idarə edir ─────────────
def _um_rows(markup):
    return [b.callback_data or b.switch_inline_query for r in markup.inline_keyboard for b in r]


def test_user_menu_shows_everything_by_default_and_follows_the_creators_switches(monkeypatch):
    from bot.menus.views import groups
    monkeypatch.setattr(groups, "get_db", lambda: MagicMock(get_premium=lambda uid: None))
    monkeypatch.setattr("common.database.is_invite_enabled", lambda: True)
    user = types.SimpleNamespace(id=8, first_name="Ad")
    full = _um_rows(run(groups.user_menu_view(MagicMock(), user))[1])
    assert full == ["codec:panel", "mbtn:kb:menu", "umenu:invite", "umenu:groups", "umenu:premium", "", "umenu:help",
                    "lang:menu", "umenu:close"]
    for key in ("codec", "keyboard", "premium", "lang", "search"):
        mb.set_um(key, False)
    cut = _um_rows(run(groups.user_menu_view(MagicMock(), user))[1])
    assert cut == ["umenu:invite", "umenu:groups", "umenu:help", "umenu:close"]
    for key in mb.USER_MENU:
        mb.set_um(key, False)
    assert _um_rows(run(groups.user_menu_view(MagicMock(), user))[1]) == ["umenu:close"]   # ✖️ Bağla həmişə qalır


def test_creator_page_toggles_each_button_and_restores_all(env):
    text, rows = shown(callback(env, "mbtn:um"))
    assert "İstifadəçi menyusu" in text and all(mb.USER_MENU[k] in text for k in mb.USER_MENU)
    flat = [b for r in rows for b in r]
    assert all((f"mbtn:umt:{k}" in [d for _, d in flat]) for k in mb.USER_MENU) and all(len(r) <= 2 for r in rows[:-1])
    assert rows[-1][-1][1] == "mbtn:close" and "mbtn:panel" in [d for _, d in rows[-1]]
    callback(env, "mbtn:umt:premium")
    assert not mb.um_on("premium") and mb.um_on("lang")
    assert "🔴" in shown(callback(env, "mbtn:um"))[0]
    callback(env, "mbtn:umall")
    assert all(mb.um_on(k) for k in mb.USER_MENU)


def test_only_the_creator_can_change_the_user_menu(env):
    callback(env, "mbtn:umt:premium", uid=9)
    assert mb.um_on("premium")


def test_creator_panel_links_to_the_user_menu_page(env):
    _, rows = shown(callback(env, "mbtn:panel"))
    assert ("👤 İstifadəçi menyusu", "mbtn:um") in [b for r in rows for b in r]
