"""🛠 Texniki iş rejimi: vəziyyət / müddət / mətn, qapı (musiqi, səsli yayım, inline ...), yayım və depo siyasəti, panel."""
import time
import types
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, InlineQuery, Message, User

import bot.plugins.maintenance_plugin as mp
from common import maintenance as mt


class FakeSettings:
    def __init__(self):
        self.d = {}

    def get_setting(self, key, default=None):
        return self.d.get(key, default)

    def set_setting(self, key, value):
        if value is None:
            self.d.pop(key, None)
        else:
            self.d[key] = str(value)


@pytest.fixture(autouse=True)
def store(monkeypatch):
    import common.database as cd
    db = FakeSettings()
    monkeypatch.setattr(cd, "get_db", lambda: db)
    mt._cache.update(t=0.0, v=None)
    mt._waiters.clear()
    mt._notices.clear()
    mt._notices_saved = 0.0
    mt._waiters_saved = 0.0
    return db


# ── vəziyyət ──
def test_off_by_default_and_database_helper_delegates():
    from common.database import is_maintenance_mode, set_maintenance_mode
    assert not mt.is_active() and not is_maintenance_mode()
    set_maintenance_mode(True)
    assert mt.is_active() and is_maintenance_mode()
    set_maintenance_mode(False)
    assert not is_maintenance_mode()


def test_enable_with_duration_expires(monkeypatch):
    until = mt.enable(900)
    assert abs(until - (time.time() + 900)) < 3 and mt.is_active() and 880 < mt.remaining() <= 900
    real = time.time
    monkeypatch.setattr(mt.time, "time", lambda: real() + 901)
    mt._cache.update(t=0.0, v=None)
    assert not mt.is_active() and mt.is_due()                       # müddət bitdi — izləyici söndürməlidir


def test_enable_without_duration_is_open_ended():
    mt.enable(0)
    assert mt.is_active() and mt.remaining() is None and not mt.is_due()


def test_saved_duration_is_used_on_enable():
    while mt.settings()["duration"] != 3600:
        mt.cycle_duration()
    mt.enable()
    assert 3590 < mt.remaining() <= 3600


def test_duration_cycles_through_presets_and_wraps():
    seen = [mt.cycle_duration() for _ in range(len(mt.DURATIONS))]
    assert seen[-1] == mt.DURATIONS[0] and set(seen) == set(mt.DURATIONS)
    assert mt.duration_label(0) == "♾ müddətsiz" and mt.duration_label(7200) == "2 saat" and mt.duration_label(900) == "15 dəq."


def test_text_rendering_with_and_without_eta_placeholder():
    assert "Texniki işlər" in mt.render_text()
    mt.enable(1800)
    assert "Təxmini bitmə" in mt.render_text()                       # defolt mətn: {eta} dəyişəni
    assert mt.render_text("Salam {eta} sağ ol").count("Təxmini bitmə") == 1
    assert mt.render_text("Salam").endswith("dəq.</b>")             # {eta} yoxdursa sonuna əlavə olunur


def test_options_toggle_and_persist(store):
    assert mt.toggle_option(mt.K_VOICE) is True and mt.settings()["voice"] is True
    assert mt.toggle_option(mt.K_VOICE) is False
    assert mt.settings()["notify"] is True                           # defolt: xəbər ver
    assert mt.toggle_option(mt.K_NOTIFY, default_on=True) is False


def test_waiters_collected_and_returned_on_disable(store):
    mt.enable(0)
    for uid in (5, 6, 5):
        mt.add_waiter(uid)
    assert mt.waiters_count() == 2
    assert mt.disable() == [5, 6] and mt.waiters_count() == 0


def test_waiters_survive_restart(store):
    mt.enable(0)
    mt.add_waiter(11)                                                # ilk əlavə dərhal bazaya yazılır
    mt._waiters.clear()                                              # proses yenidən başladı
    assert mt.disable() == [11]


# ── qapı ──
def _msg(text="salam", chat_type="private", user_id=7, successful_payment=None):
    return Message.model_construct(
        message_id=1, date=datetime.now(), chat=Chat.model_construct(id=user_id if chat_type == "private" else -100, type=chat_type),
        from_user=User.model_construct(id=user_id, is_bot=False, first_name="x"), text=text,
        successful_payment=successful_payment)


def _cb(data, user_id=7):
    return CallbackQuery.model_construct(id="1", from_user=User.model_construct(id=user_id, is_bot=False, first_name="x"),
                                         chat_instance="c", data=data)


@pytest.fixture
def gate(monkeypatch):
    ctx = types.SimpleNamespace(is_creator=lambda uid: uid == 1, creator_id=1, tip_waiting=set(),
                                bot=types.SimpleNamespace(delete_message=AsyncMock()))
    answers = AsyncMock()
    monkeypatch.setattr(Message, "answer", answers)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(InlineQuery, "answer", AsyncMock())
    g = mp.MaintenanceGate(ctx)
    g.answers, g.ctx = answers, ctx
    return g


async def _run(gate, event):
    called = AsyncMock(return_value="handled")
    res = await gate(called, event, {})
    return res == "handled"


async def test_everything_passes_when_off(gate):
    assert await _run(gate, _msg("/play abc"))


async def test_creator_always_passes(gate):
    mt.enable(0)
    assert await _run(gate, _msg("/play abc", user_id=1))
    assert await _run(gate, _cb("va:panel", user_id=1))


@pytest.mark.parametrize("text", ["salam", "https://youtu.be/xyz", "/music abc", "/play abc", "/mixplay", "/shazam", "/artist x",
                                  "/edit", "/codec", ".play x", "!radio"])
async def test_private_features_are_blocked(gate, text):
    mt.enable(0)
    assert not await _run(gate, _msg(text))
    assert 7 in mt._waiters


@pytest.mark.parametrize("text", ["/start", "/start premium", "/help", "/lang", "/premium", "/start@dllbot"])
async def test_basic_commands_stay_open(gate, text):
    mt.enable(0)
    assert await _run(gate, _msg(text))


async def test_notice_sent_once_per_cooldown(gate):
    mt.enable(0)
    await _run(gate, _msg("a"))
    await _run(gate, _msg("b"))
    assert gate.answers.await_count == 1 and "Texniki işlər" in gate.answers.await_args.args[0]


async def test_group_chat_only_commands_are_blocked(gate):
    mt.enable(0)
    assert await _run(gate, _msg("sadəcə söhbət", chat_type="supergroup"))
    assert not await _run(gate, _msg("/play abc", chat_type="supergroup"))
    assert not await _run(gate, _msg("/mixplay", chat_type="supergroup", user_id=9))
    assert await _run(gate, _msg("/start", chat_type="supergroup", user_id=9))


async def test_payment_completion_is_never_blocked(gate):
    mt.enable(0)
    assert await _run(gate, _msg(text=None, successful_payment=object()))


async def test_custom_tip_amount_typing_is_allowed(gate):
    mt.enable(0)
    gate.ctx.tip_waiting.add(7)
    assert await _run(gate, _msg("75"))


@pytest.mark.parametrize("data,allowed", [
    ("pla:panel", False), ("va:panel", False), ("vp:x", False), ("shz:1", False), ("art:1", False), ("codec:panel", False),
    ("menu:main", False), ("sg:download", True), ("tip:50", True), ("tip_prompt:close", True), ("buy:month", True),
    ("ln:def:az", True), ("lang:menu", True), ("start:home", True)])
async def test_callbacks(gate, data, allowed):
    mt.enable(0)
    assert await _run(gate, _cb(data)) is allowed
    if not allowed:
        assert CallbackQuery.answer.await_args.kwargs["show_alert"] is True


async def test_inline_is_answered_with_single_maintenance_result(gate):
    mt.enable(0)
    q = InlineQuery.model_construct(id="1", from_user=User.model_construct(id=7, is_bot=False, first_name="x"),
                                    query="mahnı", offset="")
    assert not await _run(gate, q)
    results = InlineQuery.answer.await_args.args[0]
    assert len(results) == 1 and "Texniki" in results[0].title


# ── tətbiq: yayım və depo ──
class FakeVA:
    def __init__(self, n):
        self.chats = {i: object() for i in range(n)}
        self.ended = []

    async def _end_chat(self, cid, **kw):
        self.ended.append((cid, kw))
        self.chats.pop(cid, None)


class FakeFiller:
    def __init__(self, running=True):
        self.running_ = running
        self.stopped_with = None
        self.started = False

    @property
    def running(self):
        return self.running_

    async def stop(self, persist=True, grace=20):
        self.stopped_with = persist
        self.running_ = False

    def start(self):
        self.started = True
        self.running_ = True


def _ctx(va=None, filler=None):
    bot = types.SimpleNamespace(send_message=AsyncMock())
    return types.SimpleNamespace(bot=bot, creator_id=1, voice_assistant=va, depo_filler=filler, is_creator=lambda u: u == 1)


async def test_start_stops_voice_and_pauses_depo_when_enabled():
    mt.set_option(mt.K_VOICE, "1")
    mt.set_option(mt.K_DEPO, "1")
    va, filler = FakeVA(3), FakeFiller()
    res = await mp.start_maintenance(_ctx(va, filler), 0)
    assert res["voice"] == 3 and not va.chats and all(kw["reason"] for _, kw in va.ended)
    assert res["depo"] is True and filler.stopped_with is False      # on bayrağı qalır
    assert mt.is_active()


async def test_start_leaves_voice_and_depo_alone_by_default():
    va, filler = FakeVA(2), FakeFiller()
    res = await mp.start_maintenance(_ctx(va, filler), 0)
    assert res["voice"] == 0 and len(va.chats) == 2 and filler.running and res["depo"] is False


async def test_finish_resumes_depo_and_notifies_waiters_and_creator():
    mt.set_option(mt.K_DEPO, "1")
    filler = FakeFiller()
    ctx = _ctx(FakeVA(0), filler)
    await mp.start_maintenance(ctx, 0)
    mt.add_waiter(21)
    mt.add_waiter(22)
    mp.NOTIFY_DELAY = 0
    res = await mp.finish_maintenance(ctx, auto=True)
    import asyncio
    await asyncio.sleep(0.05)
    assert not mt.is_active() and filler.started and res["depo"] is True
    sent = [c.args[0] for c in ctx.bot.send_message.await_args_list]
    assert 21 in sent and 22 in sent and 1 in sent                    # gözləyənlər + creator (avto bitmə)


async def test_finish_without_notify_option_sends_nothing_to_users():
    mt.set_option(mt.K_NOTIFY, "0")
    ctx = _ctx()
    await mp.start_maintenance(ctx, 0)
    mt.add_waiter(21)
    await mp.finish_maintenance(ctx)
    assert ctx.bot.send_message.await_count == 0


async def test_watcher_ends_expired_maintenance(monkeypatch):
    ctx = _ctx()
    await mp.start_maintenance(ctx, 900)
    real = time.time
    monkeypatch.setattr(mt.time, "time", lambda: real() + 901)
    mt._cache.update(t=0.0, v=None)
    assert mt.is_due()
    await mp.finish_maintenance(ctx, auto=True)
    assert not mt.settings()["on"]


def test_setup_registers_gate_once():
    from aiogram import Dispatcher
    dp = Dispatcher()
    ctx = _ctx()
    ctx.dp = dp
    import asyncio

    async def go():
        mp.setup(ctx)
        first = dp._maintenance_gate
        mp.setup(ctx)
        assert dp._maintenance_gate is first
        assert callable(ctx.maintenance.start) and callable(ctx.maintenance.stop_voice)
        mp.teardown(ctx)

    asyncio.run(go())


# ── ⚠️ xəbərdarlıq rejimi: hamı istifadə edir ──
def test_notice_mode_is_active_but_not_blocking():
    from common.database import is_maintenance_mode
    mt.enable(0)
    assert mt.mode() == "block" and mt.is_blocking() and is_maintenance_mode()
    assert mt.toggle_mode() == "notice"
    assert mt.is_active() and not mt.is_blocking()
    assert not is_maintenance_mode()                     # musiqi / link plugin-lərinin köhnə yoxlamaları da bağlamır
    assert mt.toggle_mode() == "block" and mt.is_blocking()


def test_notice_mode_has_its_own_default_text():
    mt.toggle_mode()
    text = mt.render_text()
    assert "Bot işləyir" in text and "bağlı" not in text.lower()
    mt.set_option(mt.K_TEXT, "öz mətnim")
    assert mt.render_text().startswith("öz mətnim")


@pytest.mark.parametrize("text", ["salam", "/play abc", "https://youtu.be/xyz", "/mixplay"])
async def test_notice_mode_lets_everyone_in_and_warns_once(gate, text):
    mt.enable(0)
    mt.toggle_mode()
    assert await _run(gate, _msg(text))
    assert await _run(gate, _msg(text))
    assert gate.answers.await_count == 1 and "Texniki işlər" in gate.answers.await_args.args[0]
    assert mt.waiters_count() == 0                       # heç kim bloklanmır — gözləyən yoxdur


async def test_notice_mode_passes_callbacks_inline_and_groups_silently(gate):
    mt.enable(0)
    mt.toggle_mode()
    assert await _run(gate, _cb("va:panel")) and await _run(gate, _cb("pla:panel"))
    q = InlineQuery.model_construct(id="1", from_user=User.model_construct(id=7, is_bot=False, first_name="x"),
                                    query="a", offset="")
    assert await _run(gate, q)
    assert await _run(gate, _msg("sadəcə söhbət", chat_type="supergroup"))
    assert await _run(gate, _msg("/artist x", chat_type="supergroup"))            # səsli yayım əmri deyil
    assert await _run(gate, _msg(text=None, successful_payment=object()))
    assert gate.answers.await_count == 0                 # xəbərdarlıq özəl çatda və qrupda səsli yayım başlananda


async def test_notice_repeats_only_after_the_interval(gate, monkeypatch):
    mt.enable(0)
    mt.toggle_mode()
    await _run(gate, _msg("a"))
    for k in list(gate.last_notice):
        gate.last_notice[k] -= mt.NOTICE_EVERY + 1       # 30 dəq. keçdi
    await _run(gate, _msg("b"))
    assert gate.answers.await_count == 2


async def test_switching_back_to_block_blocks_again(gate):
    mt.enable(0)
    mt.toggle_mode()
    assert await _run(gate, _msg("/play x"))
    mt.toggle_mode()
    assert not await _run(gate, _msg("/play x"))


# ── 🎙 yayım qruplarına eyni bildiriş ──
@pytest.mark.parametrize("cmd", ["/play abc", "/p abc", "/mixplay", "/radio", ".oynat x"])
async def test_notice_mode_voice_start_in_group_gets_the_same_notice_once(gate, cmd):
    mt.enable(0)
    mt.toggle_mode()
    assert await _run(gate, _msg(cmd, chat_type="supergroup", user_id=9))
    assert await _run(gate, _msg(cmd, chat_type="supergroup", user_id=10))
    assert gate.answers.await_count == 1 and "Texniki işlər" in gate.answers.await_args.args[0]


async def test_group_voice_notice_can_be_turned_off(gate):
    mt.set_option(mt.K_STREAM_NOTICE, "0")
    mt.enable(0)
    mt.toggle_mode()
    assert await _run(gate, _msg("/play abc", chat_type="supergroup", user_id=9))
    assert gate.answers.await_count == 0


async def test_start_announces_to_active_stream_groups_and_finish_says_done():
    va = FakeVA(2)
    ctx = _ctx(va, FakeFiller())
    res = await mp.start_maintenance(ctx, 0)
    sent = {c.args[0]: c.args[1] for c in ctx.bot.send_message.await_args_list}
    assert res["announced"] == 2 and set(sent) == {0, 1}
    assert all("Texniki işlər" in t for t in sent.values())                    # istifadəçilərlə eyni mətn
    assert va.chats                                                           # yayım defolt olaraq davam edir
    ctx.bot.send_message.reset_mock()
    mp.NOTIFY_DELAY = 0
    res = await mp.finish_maintenance(ctx)
    import asyncio
    await asyncio.sleep(0.05)
    assert res["streams"] == 2
    done = [c.args[0] for c in ctx.bot.send_message.await_args_list]
    assert set(done) == {0, 1}
    assert maintenance_chats_empty()


def maintenance_chats_empty():
    return mt.pop_stream_chats() == []


async def test_notice_includes_eta_and_uses_notice_text_in_notice_mode():
    mt.toggle_mode()
    ctx = _ctx(FakeVA(1))
    await mp.start_maintenance(ctx, 1800)
    text = ctx.bot.send_message.await_args.args[1]
    assert "Bot işləyir" in text and "Təxmini bitmə" in text


async def test_stream_announcement_precedes_stop_when_voice_is_stopped():
    mt.set_option(mt.K_VOICE, "1")
    va = FakeVA(1)
    ctx = _ctx(va)
    res = await mp.start_maintenance(ctx, 0)
    assert res["announced"] == 1 and res["voice"] == 1 and not va.chats


async def test_announcement_failures_do_not_break_start():
    ctx = _ctx(FakeVA(2))
    ctx.bot.send_message = AsyncMock(side_effect=RuntimeError("bot qrupda deyil"))
    res = await mp.start_maintenance(ctx, 0)
    assert res["announced"] == 0 and mt.is_active()


async def test_no_announcement_when_option_off_or_no_streams():
    mt.set_option(mt.K_STREAM_NOTICE, "0")
    ctx = _ctx(FakeVA(2))
    assert (await mp.start_maintenance(ctx, 0))["announced"] == 0 and ctx.bot.send_message.await_count == 0
    mt.disable()
    mt.set_option(mt.K_STREAM_NOTICE, "1")
    ctx = _ctx(FakeVA(0))
    assert (await mp.start_maintenance(ctx, 0))["announced"] == 0


# ── 📋 Görünüş (kod bloku) üslubu iş rejimi mesajlarında ──
@pytest.fixture
def ui_style(monkeypatch):
    import common.menu as menu

    def set_style(name):
        monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", name)

    set_style("box")
    return set_style


@pytest.mark.parametrize("style,open_tag,close_tag", [
    ("box", "<pre>", "</pre>"),
    ("script", "<blockquote expandable><code>", "</code></blockquote>"),
    ("quote", "<blockquote><code>", "</code></blockquote>")])
def test_message_follows_ui_style(ui_style, style, open_tag, close_tag):
    ui_style(style)
    mt.enable(1800)
    text = mt.message()
    assert text.startswith("<b>🛠 Texniki işlər gedir</b>\n" + open_tag) and text.endswith(close_tag)
    assert "Təxmini bitmə" in text and "<b>~" not in text                   # gövdə düz mətn kimi blokdadır


def test_done_message_is_styled_too(ui_style):
    text = mt.message("done")
    assert text.startswith("<b>✅ Texniki işlər başa çatdı</b>\n<pre>") and "yenidən işləyir" in text


def test_notice_mode_message_is_styled(ui_style):
    mt.toggle_mode()
    assert mt.message().startswith("<b>⚠️ Texniki işlər gedir</b>\n<pre>")


def test_styling_can_be_turned_off(ui_style):
    mt.set_option(mt.K_STYLED, "0")
    assert mt.message() == mt.render_text() and "<pre>" not in mt.message()
    assert mt.message("done") == mt.DONE_TEXT


def test_custom_text_is_styled_with_first_line_as_title(ui_style):
    mt.set_option(mt.K_TEXT, "🔧 <b>Yeniləmə</b>\nsaat 3-də bitəcək {eta}")
    text = mt.message()
    assert text.startswith("<b>🔧 Yeniləmə</b>\n<pre>saat 3-də bitəcək")


def test_style_change_applies_immediately(ui_style):
    assert "<pre>" in mt.message()
    ui_style("quote")
    assert "<blockquote>" in mt.message() and "<pre>" not in mt.message()


def test_too_long_text_is_trimmed_to_telegram_limit(ui_style):
    mt.set_option(mt.K_TEXT, "başlıq\n" + "x" * 5000)
    text = mt.message()
    assert len(text) <= 4096 and text.endswith("</pre>") and "davamı kəsildi" in text      # boxify özü kəsir


async def test_blocked_user_receives_styled_notice(gate, ui_style):
    mt.enable(0)
    await _run(gate, _msg("salam"))
    assert gate.answers.await_args.args[0].startswith("<b>🛠 Texniki işlər gedir</b>\n<pre>")


async def test_notice_mode_warning_is_styled(gate, ui_style):
    mt.enable(0)
    mt.toggle_mode()
    await _run(gate, _msg("salam"))
    assert "<pre>" in gate.answers.await_args.args[0]


async def test_inline_result_is_styled(gate, ui_style):
    mt.enable(0)
    q = InlineQuery.model_construct(id="1", from_user=User.model_construct(id=7, is_bot=False, first_name="x"),
                                    query="a", offset="")
    await _run(gate, q)
    content = InlineQuery.answer.await_args.args[0][0].input_message_content
    assert "<pre>" in content.message_text and content.parse_mode == "HTML"


async def test_stream_groups_and_done_messages_are_styled(ui_style):
    ctx = _ctx(FakeVA(1))
    await mp.start_maintenance(ctx, 0)
    assert "<pre>" in ctx.bot.send_message.await_args.args[1]
    ctx.bot.send_message.reset_mock()
    mp.NOTIFY_DELAY = 0
    await mp.finish_maintenance(ctx)
    import asyncio
    await asyncio.sleep(0.05)
    assert all("<b>✅ Texniki işlər başa çatdı</b>\n<pre>" in c.args[1] for c in ctx.bot.send_message.await_args_list)


# ── 🗑 bildirişlərin silinməsi ──
def _sent(chat_id=7, mid=500):
    return types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id), message_id=mid)


@pytest.fixture
def deletions(monkeypatch):
    """_delete_later çağırışlarını (gecikməni) toplayır, heç nə gözləmir."""
    calls = []

    async def fake(bot, chat_id, mid, delay):
        calls.append((chat_id, mid, delay))

    monkeypatch.setattr(mp, "_delete_later", fake)
    monkeypatch.setattr(mp, "spawn", lambda coro: __import__("asyncio").ensure_future(coro))
    return calls


def test_delete_mode_cycles_and_defaults_to_never():
    assert mt.delete_mode() == "0"
    seen = [mt.cycle_delete() for _ in range(len(mt.DELETE_MODES))]
    assert seen == ["60", "600", "3600", "end", "0"]
    assert set(mt.DELETE_LABELS) == set(mt.DELETE_MODES)


async def test_never_mode_schedules_nothing(deletions):
    mp.schedule_cleanup(_ctx(), _sent())
    assert deletions == [] and mt.notices_count() == 0


@pytest.mark.parametrize("mode,delay", [("60", 60), ("600", 600), ("3600", 3600)])
async def test_timed_modes_delete_after_delay(deletions, mode, delay):
    mt.set_option(mt.K_DELETE, mode)
    mp.schedule_cleanup(_ctx(), _sent(9, 41))
    import asyncio
    await asyncio.sleep(0)
    assert deletions == [(9, 41, delay)] and mt.notices_count() == 0


async def test_end_mode_tracks_and_finish_deletes_them(deletions):
    mt.set_option(mt.K_DELETE, "end")
    ctx = _ctx(FakeVA(0))
    ctx.bot.delete_message = AsyncMock()
    mp.schedule_cleanup(ctx, _sent(1, 10))
    mp.schedule_cleanup(ctx, _sent(2, 20))
    assert mt.notices_count() == 2 and deletions == []
    mt.enable(0)
    mp.DELETE_STEP = 0
    res = await mp.finish_maintenance(ctx)
    assert res["deleted"] == 2 and mt.notices_count() == 0
    assert {c.args for c in ctx.bot.delete_message.await_args_list} == {(1, 10), (2, 20)}


async def test_done_message_is_not_tracked_in_end_mode(deletions):
    mt.set_option(mt.K_DELETE, "end")
    mp.schedule_cleanup(_ctx(), _sent(), done=True)
    assert mt.notices_count() == 0 and deletions == []


async def test_done_message_follows_timed_delay(deletions):
    mt.set_option(mt.K_DELETE, "600")
    mp.schedule_cleanup(_ctx(), _sent(3, 33), done=True)
    import asyncio
    await asyncio.sleep(0)
    assert deletions == [(3, 33, 600)]


async def test_gate_notice_is_scheduled_for_deletion(gate, deletions):
    mt.set_option(mt.K_DELETE, "60")
    mt.enable(0)
    gate.answers.return_value = _sent(7, 123)
    await _run(gate, _msg("salam"))
    import asyncio
    await asyncio.sleep(0)
    assert deletions == [(7, 123, 60)]


async def test_notice_mode_warning_is_tracked_for_end(gate, deletions):
    mt.set_option(mt.K_DELETE, "end")
    mt.enable(0)
    mt.toggle_mode()
    gate.answers.return_value = _sent(7, 124)
    await _run(gate, _msg("salam"))
    assert mt.notices_count() == 1


async def test_stream_announcements_are_cleaned_too(deletions):
    mt.set_option(mt.K_DELETE, "end")
    ctx = _ctx(FakeVA(2))
    ctx.bot.send_message = AsyncMock(side_effect=[_sent(0, 1), _sent(1, 2)])
    await mp.start_maintenance(ctx, 0)
    assert mt.notices_count() == 2


async def test_cleanup_survives_delete_errors_and_clears_list():
    ctx = _ctx()
    ctx.bot.delete_message = AsyncMock(side_effect=[RuntimeError("yoxdur"), True])
    mt.track_notice(1, 10)
    mt.track_notice(2, 20)
    mp.DELETE_STEP = 0
    assert await mp.cleanup_notices(ctx) == 1 and mt.notices_count() == 0


def test_tracked_notices_survive_restart_and_dedupe():
    mt.track_notice(1, 10)                                  # ilk əlavə dərhal bazaya yazılır
    mt._notices.clear()                                      # proses yenidən başladı
    mt.track_notice(1, 10)
    assert mt.pop_notices() == [(1, 10)]


def test_mock_answer_result_without_ids_is_ignored(deletions):
    mt.set_option(mt.K_DELETE, "end")
    mp.schedule_cleanup(_ctx(), AsyncMock())                  # tanınmayan cavab obyekti — səssiz keçir
    mp.schedule_cleanup(_ctx(), None)
    assert mt.notices_count() == 0
