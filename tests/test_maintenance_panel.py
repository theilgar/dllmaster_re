"""🛠 Texniki iş rejimi paneli (Bot idarəsi): düymələr, açar məntiqi, mətn daxiletməsi."""
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus.handlers.maint_flow as mf
import bot.menus.state as st
import bot.menus.views.maintenance as mv
from common import maintenance as mt
from tests.test_maintenance import FakeFiller, FakeSettings, FakeVA  # noqa: F401


@pytest.fixture(autouse=True)
def store(monkeypatch):
    import common.database as cd
    db = FakeSettings()
    monkeypatch.setattr(cd, "get_db", lambda: db)
    mt._cache.update(t=0.0, v=None)
    mt._waiters.clear()
    mt._notices.clear()
    mt._notices_saved = 0.0
    st.mt.clear(); st.mt["stage"] = None
    return db


class API:
    """maintenance_plugin.setup-un context.maintenance obyektinin saxta surəti."""

    def __init__(self):
        self.started = self.finished = self.stopped = 0

    async def start(self, duration=None):
        self.started += 1
        mt.enable(duration)
        return {"voice": 2, "depo": True, "until": 0}

    async def finish(self, auto=False):
        self.finished += 1
        w = mt.disable()
        return {"waiters": len(w), "notified": bool(w), "depo": True}

    async def stop_voice(self):
        self.stopped += 1
        return 4

    async def cleanup(self):
        self.cleaned = getattr(self, "cleaned", 0) + 1
        return len(mt.pop_notices())


@pytest.fixture
def ctx(monkeypatch):
    c = types.SimpleNamespace(bot=types.SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock()),
                              creator_id=1, voice_assistant=FakeVA(2), maintenance=API(), is_creator=lambda u: u == 1)
    monkeypatch.setattr(mv, "get_context", lambda: c)
    monkeypatch.setattr(mf, "get_context", lambda: c)
    monkeypatch.setattr(st, "_CTX", c)
    monkeypatch.setattr(mf, "is_creator", lambda u: u == 1)
    return c


def _cb():
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=5)
    return types.SimpleNamespace(data="menu:maint", from_user=types.SimpleNamespace(id=1), message=msg, answer=AsyncMock())


@pytest.fixture
def shown(monkeypatch):
    log = []

    async def fake_edit(cb, text, kb=None):
        log.append((text, kb))

    monkeypatch.setattr(mf, "safe_edit_cb", fake_edit)
    return log


def _labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def test_panel_when_off(ctx):
    text, kb = mv.maint_view()
    assert "söndürülüb" in text and "🟢 Rejimi aç" in _labels(kb)
    assert not any("indi dayandır" in t for t in _labels(kb))
    for d in _datas(kb):
        assert len(d.encode()) <= 64


def test_panel_when_on_shows_remaining_and_active_streams(ctx):
    mt.enable(1800)
    text, kb = mv.maint_view()
    assert "AKTİV" in text and "qalan" in text and "indi 2 aktiv" in text
    assert "🔴 Rejimi söndür" in _labels(kb) and any("indi dayandır (2)" in t for t in _labels(kb))
    assert "menu:maint:stopv" in _datas(kb)


def test_panel_cycle_buttons_show_current_and_next(ctx):
    _, kb = mv.maint_view()
    labels = " ".join(_labels(kb))
    assert "Müddət: ♾ müddətsiz  ›  15 dəq." in labels
    assert "Yayımlar: davam edir  ›  dayandırılsın" in labels and "Depo: işləyir  ›  pauza" in labels
    mt.toggle_option(mt.K_VOICE)
    assert "Yayımlar: dayandırılır  ›  davam edir" in " ".join(_labels(mv.maint_view()[1]))


async def test_toggle_on_then_off(ctx, shown):
    cb = _cb()
    await mf._mcb_maint(cb, ["menu", "maint", "tog"], "maint")
    assert ctx.maintenance.started == 1 and mt.is_active()
    assert "AÇILDI" in shown[-1][0] and "2 yayım dayandırıldı" in shown[-1][0] and "depo pauza" in shown[-1][0]
    mt.add_waiter(9)
    await mf._mcb_maint(cb, ["menu", "maint", "tog"], "maint")
    assert ctx.maintenance.finished == 1 and not mt.is_active()
    assert "söndürüldü" in shown[-1][0] and "1 istifadəçiyə xəbər" in shown[-1][0]


async def test_option_buttons(ctx, shown):
    cb = _cb()
    await mf._mcb_maint(cb, ["menu", "maint", "dur"], "maint")
    assert mt.settings()["duration"] == 900
    await mf._mcb_maint(cb, ["menu", "maint", "voice"], "maint")
    await mf._mcb_maint(cb, ["menu", "maint", "depo"], "maint")
    await mf._mcb_maint(cb, ["menu", "maint", "notify"], "maint")
    s = mt.settings()
    assert s["voice"] and s["depo"] and not s["notify"]


async def test_stop_voice_now_button(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "stopv"], "maint")
    assert ctx.maintenance.stopped == 1 and "4 yayım dayandırıldı" in shown[-1][0]


async def test_apply_toggle_without_plugin_still_works(ctx):
    ctx.maintenance = None
    note = await mf.apply_toggle(ctx)
    assert "AÇILDI" in note and mt.is_active()
    note = await mf.apply_toggle(ctx)
    assert "söndürüldü" in note and not mt.is_active()


# ── mətn ──
async def test_text_flow_saves_custom_text(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "text"], "maint")
    assert st.mt["stage"] == "await_text" and st.menu_waiting_text(1)
    msg = types.SimpleNamespace(html_text="⚙️ <b>Yeniləmə</b> {eta}", from_user=types.SimpleNamespace(id=1),
                                delete=AsyncMock(), reply=AsyncMock())
    assert await mf.waiting_maint_text(msg) is True
    await mf.maint_text_input(msg)
    assert mt.settings()["text"] == "⚙️ <b>Yeniləmə</b> {eta}" and st.mt["stage"] is None
    assert "Yeniləmə" in ctx.bot.send_message.await_args.args[1]       # doğrulama: Telegram bu HTML-i qəbul etdi
    assert "Yeniləmə" in mt.render_text()


async def test_text_flow_rejects_empty_and_too_long(ctx):
    st.mt.update(stage="await_text", chat_id=1, panel_id=5)
    for html in ("  ", "x" * (mf.TEXT_LIMIT + 1)):
        msg = types.SimpleNamespace(html_text=html, from_user=types.SimpleNamespace(id=1), delete=AsyncMock())
        await mf.maint_text_input(msg)
        assert mt.settings()["text"] == "" and st.mt["stage"] == "await_text"


async def test_text_flow_rejects_html_telegram_refuses(ctx):
    st.mt.update(stage="await_text", chat_id=1, panel_id=5)
    ctx.bot.send_message = AsyncMock(side_effect=RuntimeError("can't parse entities"))
    msg = types.SimpleNamespace(html_text="<b>qırıq", from_user=types.SimpleNamespace(id=1), delete=AsyncMock())
    await mf.maint_text_input(msg)
    assert mt.settings()["text"] == "" and st.mt["stage"] == "await_text"


async def test_default_text_restore_and_preview(ctx, shown):
    mt.set_option(mt.K_TEXT, "xüsusi")
    await mf._mcb_maint(_cb(), ["menu", "maint", "textdef"], "maint")
    assert mt.settings()["text"] == ""
    await mf._mcb_maint(_cb(), ["menu", "maint", "prev"], "maint")
    assert "Texniki işlər" in ctx.bot.send_message.await_args.args[1]


# ── menyuya bağlama ──
def test_bot_admin_screen_links_to_panel(monkeypatch):
    import bot.menus  # noqa: F401
    from bot.menus.views import dev
    from bot.menus.views import system_hub as H

    c = types.SimpleNamespace(userbot_enabled=False, bot_enabled=True, creator_id=1, plugin_status={})
    monkeypatch.setattr(H, "get_context", lambda: c)
    monkeypatch.setattr(dev, "get_context", lambda: c)
    _, kb = dev.dev_hub_view()
    assert "menu:maint" in _datas(kb) and "menu:toggle_maint" not in _datas(kb)
    mt.enable(0)
    assert any("🟢 aktiv" in t and "Texniki iş" in t for t in _labels(dev.dev_hub_view()[1]))


def test_panel_mode_button_and_description(ctx):
    text, kb = mv.maint_view()
    assert "🔒 Bağlı" in text and "Növ: 🔒 Bağlı  ›  ⚠️ Hamı istifadə edir" in " ".join(_labels(kb))
    assert "menu:maint:mode" in _datas(kb)
    mt.toggle_mode()
    text, kb = mv.maint_view()
    assert "⚠️ Xəbərdarlıq" in text and "Hamı</b> botdan istifadə edə bilir" in text
    assert "Növ: ⚠️ Hamı istifadə edir  ›  🔒 Bağlı" in " ".join(_labels(kb))


async def test_mode_button_toggles_and_reports(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "mode"], "maint")
    assert mt.mode() == "notice" and "Xəbərdarlıq rejimi" in shown[-1][0]
    await mf._mcb_maint(_cb(), ["menu", "maint", "mode"], "maint")
    assert mt.mode() == "block" and "Bağlı rejim" in shown[-1][0]


async def test_opening_in_notice_mode_is_labelled(ctx, shown):
    mt.toggle_mode()
    note = await mf.apply_toggle(ctx)
    assert "AÇILDI" in note and "xəbərdarlıq" in note


def test_panel_shows_stream_notice_toggle(ctx):
    text, kb = mv.maint_view()
    assert "Yayım qruplarına bildiriş: <b>bəli</b>" in text and "menu:maint:streams" in _datas(kb)


async def test_stream_notice_button_toggles(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "streams"], "maint")
    assert mt.settings()["stream_notice"] is False and "göndərilməyəcək" in shown[-1][0]
    await mf._mcb_maint(_cb(), ["menu", "maint", "streams"], "maint")
    assert mt.settings()["stream_notice"] is True


async def test_toggle_note_mentions_announced_groups(ctx):
    ctx.maintenance.start = AsyncMock(return_value={"voice": 0, "depo": False, "announced": 3})
    note = await mf.apply_toggle(ctx)
    assert "3 yayım qrupuna bildiriş" in note


def test_panel_shows_style_and_toggle(ctx, monkeypatch):
    import common.menu as menu
    monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", "script")
    text, kb = mv.maint_view()
    assert "Mesaj üslubu: <b>📜 Script sitat</b>" in text and "menu:maint:styled" in _datas(kb)
    mt.set_option(mt.K_STYLED, "0")
    assert "adi mətn" in mv.maint_view()[0]


async def test_styled_button_toggles(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "styled"], "maint")
    assert mt.settings()["styled"] is False and "adi mətn" in shown[-1][0]
    await mf._mcb_maint(_cb(), ["menu", "maint", "styled"], "maint")
    assert mt.settings()["styled"] is True


async def test_preview_button_sends_styled_message(ctx, shown, monkeypatch):
    import common.menu as menu
    monkeypatch.setattr(menu, "_UI_STYLE_OVERRIDE", "quote")
    await mf._mcb_maint(_cb(), ["menu", "maint", "prev"], "maint")
    assert "<blockquote>" in ctx.bot.send_message.await_args.args[1]


def test_panel_shows_delete_button_cycle(ctx):
    text, kb = mv.maint_view()
    assert "Bildirişin silinməsi: <b>silinmir</b>" in text
    assert "Mesajın silinməsi: silinmir  ›  1 dəq. sonra" in " ".join(_labels(kb))
    assert "menu:maint:del" in _datas(kb) and "menu:maint:delnow" not in _datas(kb)


def test_panel_offers_delete_now_when_notices_are_tracked(ctx):
    mt.set_option(mt.K_DELETE, "end")
    mt.track_notice(5, 50)
    text, kb = mv.maint_view()
    assert "indi 1 izlənir" in text and "menu:maint:delnow" in _datas(kb)
    for d in _datas(kb):
        assert len(d.encode()) <= 64


async def test_delete_button_cycles_and_delnow_cleans(ctx, shown):
    await mf._mcb_maint(_cb(), ["menu", "maint", "del"], "maint")
    assert mt.delete_mode() == "60" and "1 dəq. sonra silinəcək" in shown[-1][0]
    for _ in range(3):
        await mf._mcb_maint(_cb(), ["menu", "maint", "del"], "maint")
    assert mt.delete_mode() == "end"
    mt.track_notice(5, 50)
    await mf._mcb_maint(_cb(), ["menu", "maint", "delnow"], "maint")
    assert ctx.maintenance.cleaned == 1 and "1 bildiriş silindi" in shown[-1][0]
    await mf._mcb_maint(_cb(), ["menu", "maint", "del"], "maint")
    assert mt.delete_mode() == "0" and "silinməyəcək" in shown[-1][0]


async def test_toggle_off_reports_deleted_notices(ctx):
    mt.enable(0)
    ctx.maintenance.finish = AsyncMock(return_value={"waiters": 0, "notified": False, "depo": False, "deleted": 3})
    note = await mf.apply_toggle(ctx)
    assert "3 bildiriş silindi" in note
