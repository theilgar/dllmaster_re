"""📣 Qrup reklamı paneli (🎙 Canlı Yayım & VC)."""
import types
from unittest.mock import AsyncMock

import pytest

import bot.menus.handlers.ads_flow as af
import bot.menus.state as st
import bot.menus.views.group_ads as gv
import bot.plugins.group_ads_plugin as gp
from common import group_ads as ads
from tests.test_group_ads import FakeClient, FakeSettings, _dialog, _slot  # noqa: F401


@pytest.fixture(autouse=True)
def store(monkeypatch):
    import common.database as cd
    db = FakeSettings()
    monkeypatch.setattr(cd, "get_db", lambda: db)
    st.ad.clear(); st.ad["stage"] = None
    return db


@pytest.fixture
def ctx(monkeypatch):
    dialogs = [_dialog(-i, f"Qrup {i}") for i in range(1, 12)]
    bot = types.SimpleNamespace(me=AsyncMock(return_value=types.SimpleNamespace(username="dllbot")),
                                send_message=AsyncMock(), edit_message_text=AsyncMock())
    c = types.SimpleNamespace(bot=bot, creator_id=1, is_creator=lambda u: u == 1,
                              voice_assistant=types.SimpleNamespace(slots=[_slot(0, FakeClient(dialogs))]))
    c.group_ads = gp.AdRunner(c)
    for mod in (gv, af):
        monkeypatch.setattr(mod, "get_context", lambda c=c: c)
    monkeypatch.setattr(st, "_CTX", c)
    monkeypatch.setattr(af, "is_creator", lambda u: u == 1)
    monkeypatch.setattr(ads, "next_delay", lambda base, rnd=None: 0.0)
    return c


def _cb():
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(id=1), message_id=5)
    return types.SimpleNamespace(data="menu:ads", from_user=types.SimpleNamespace(id=1), message=msg, answer=AsyncMock())


@pytest.fixture
def shown(monkeypatch):
    log = []

    async def fake_edit(cb, text, kb=None):
        log.append((text, kb))

    monkeypatch.setattr(af, "safe_edit_cb", fake_edit)
    return log


def _labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _datas(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def test_panel_initial_state(ctx):
    text, kb = gv.ads_view()
    assert "Seçilmiş qruplar: <b>0</b>" in text and "siyahı yüklənməyib" in text and "şəxslərə mesaj yoxdur" in text
    assert "▶️ Başlat" in _labels(kb) and "menu:ads:go" in _datas(kb)
    assert any("Fasilə: ~1.5" in t or "Fasilə: ~90 san." in t for t in _labels(kb))
    assert not any(d == "menu:ads:slot" for d in _datas(kb))              # tək hesab — seçim lazım deyil
    for d in _datas(kb):
        assert len(d.encode()) <= 64


async def test_groups_view_pagination_and_marks(ctx):
    ads.toggle_group(-1)
    text, kb = await gv.groups_view(0)
    assert text.count("Qrup") >= 1 and "✅ Qrup 1" in _labels(kb) and "▫️ Qrup 2" in " ".join(_labels(kb))
    assert "menu:ads:grp:1" in _datas(kb)                                  # 11 qrup > 8 → 2 səhifə
    text, kb = await gv.groups_view(1)
    assert sum(1 for t in _labels(kb) if t.startswith(("✅ Qrup", "▫️ Qrup"))) == 3
    for d in _datas(kb):
        assert len(d.encode()) <= 64


async def test_cooldown_marker_in_group_list(ctx):
    ads.mark_sent(-2)
    _, kb = await gv.groups_view(0)
    assert any(t.endswith("⏳") and "Qrup 2" in t for t in _labels(kb))


async def test_toggle_select_all_none_and_refresh(ctx, shown):
    await af._mcb_ads(_cb(), ["menu", "ads", "t", "-3", "0"], "ads")
    assert ads.selected() == {-3}
    await af._mcb_ads(_cb(), ["menu", "ads", "all"], "ads")
    assert len(ads.selected()) == 11
    await af._mcb_ads(_cb(), ["menu", "ads", "none"], "ads")
    assert ads.selected() == set()
    ctx.voice_assistant.slots[0].client.dialogs.append(_dialog(-99, "Yeni"))
    await af._mcb_ads(_cb(), ["menu", "ads", "ref"], "ads")
    assert "Yeni" in " ".join(_labels(shown[-1][1])) or len(ctx.group_ads._catalog[1]) == 12


async def test_settings_buttons(ctx, shown):
    for sub in ("dly", "cool", "max"):
        await af._mcb_ads(_cb(), ["menu", "ads", sub], "ads")
    s = ads.settings()
    assert (s["delay"], s["cooldown"], s["max"]) == (180, 72, 40)
    assert "Dövrdə" in shown[-1][0] or "dövrdə" in shown[-1][0] or "40" in shown[-1][0]


async def test_start_requires_selection_then_runs_and_updates_panel(ctx, shown):
    await af._mcb_ads(_cb(), ["menu", "ads", "go"], "ads")
    assert "qrup yoxdur" in shown[-1][0]
    ads.set_selected([-1, -2])
    await af._mcb_ads(_cb(), ["menu", "ads", "go"], "ads")
    assert "Başladı: 2 qrup" in shown[-1][0]
    await ctx.group_ads.task
    sent = ctx.voice_assistant.slots[0].client.sent
    assert [c for c, _, _ in sent] == [1001, 2001]
    edits = [c.kwargs["text"] for c in ctx.bot.edit_message_text.await_args_list]
    assert edits and "Son dövr" in edits[-1]                              # dövr bitəndə panel yenilənir
    _, kb = gv.ads_view()
    assert "▶️ Başlat" in " ".join(_labels(kb)) or "Başlat" in " ".join(_labels(kb))


async def test_running_panel_shows_stop_button(ctx, shown, monkeypatch):
    ads.set_selected([-1, -2, -3])
    monkeypatch.setattr(ads, "next_delay", lambda base, rnd=None: 5.0)
    await af._mcb_ads(_cb(), ["menu", "ads", "go"], "ads")
    text, kb = gv.ads_view()
    assert "Gedir" in text and "⏹ Dayandır" in _labels(kb) and "menu:ads:stop" in _datas(kb)
    await af._mcb_ads(_cb(), ["menu", "ads", "stop"], "ads")
    assert "Dayandırılır" in shown[-1][0]
    await ctx.group_ads.task
    assert ads.last_report()["stopped"] is True


async def test_account_button_appears_with_two_accounts(ctx, shown):
    ctx.voice_assistant.slots.append(_slot(1, FakeClient([])))
    _, kb = gv.ads_view()
    assert "menu:ads:slot" in _datas(kb)
    await af._mcb_ads(_cb(), ["menu", "ads", "slot"], "ads")
    assert ads.settings()["slot"] == "0"


async def test_text_flow_saves_validates_and_restores(ctx, shown):
    await af._mcb_ads(_cb(), ["menu", "ads", "text"], "ads")
    assert st.ad["stage"] == "await_text" and st.menu_waiting_text(1)
    msg = types.SimpleNamespace(html_text="🎵 <b>Yeni</b> {link}", from_user=types.SimpleNamespace(id=1), delete=AsyncMock())
    assert await af.waiting_ads_text(msg) is True
    await af.ads_text_input(msg)
    assert ads.settings()["text"] == "🎵 <b>Yeni</b> {link}" and st.ad["stage"] is None
    assert "https://t.me/dllbot" in ctx.bot.send_message.await_args.args[1]       # yoxlama mesajı creator-a
    await af._mcb_ads(_cb(), ["menu", "ads", "textdef"], "ads")
    assert ads.settings()["text"] == ""


async def test_text_validation_and_telegram_rejection(ctx):
    st.ad.update(stage="await_text", chat_id=1, panel_id=5)
    for html in (" ", "x" * (ads.TEXT_LIMIT + 1)):
        await af.ads_text_input(types.SimpleNamespace(html_text=html, from_user=types.SimpleNamespace(id=1), delete=AsyncMock()))
        assert ads.settings()["text"] == "" and st.ad["stage"] == "await_text"
    ctx.bot.send_message = AsyncMock(side_effect=RuntimeError("can't parse entities"))
    await af.ads_text_input(types.SimpleNamespace(html_text="<b>qırıq", from_user=types.SimpleNamespace(id=1), delete=AsyncMock()))
    assert ads.settings()["text"] == ""


async def test_preview_goes_to_creator_only(ctx, shown):
    await af._mcb_ads(_cb(), ["menu", "ads", "prev"], "ads")
    assert ctx.bot.send_message.await_args.args[0] == 1 and "@dllbot" in ctx.bot.send_message.await_args.args[1]


async def test_missing_plugin_is_reported(ctx, shown):
    ctx.group_ads = None
    cb = _cb()
    await af._mcb_ads(cb, ["menu", "ads"], "ads")
    assert cb.answer.await_args.kwargs.get("show_alert") is True


def test_live_section_links_to_panel(monkeypatch):
    import bot.menus  # noqa: F401
    from bot.menus.views import system_hub as H

    monkeypatch.setattr(H, "get_context", lambda: types.SimpleNamespace(userbot_enabled=False, voice_assistant=None))
    _, kb = H.live_view()
    assert "menu:ads" in _datas(kb)
