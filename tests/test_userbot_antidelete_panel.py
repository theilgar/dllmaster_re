"""🛡 Anti-Delete inline paneli: .antidelete kartı və .menu → ⚡ Funksiyalar → 🛡 Anti-Delete bölməsi (eyni düymələr)."""
import types

import pytest
from aiogram.types import InlineKeyboardButton

from tests import userbot_fakes as fakes
from tests.test_userbot_antidelete import FakeUDB
import userbot.plugins.antidelete as ad


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "MEDIA_CACHE_DIR", str(tmp_path / "media"))
    ad._msg_cache.clear()
    ad._mid_to_chat.clear()
    monkeypatch.setattr(ad, "run_bg", lambda fn, *a, **kw: fn(*a, **kw))
    yield
    ad._msg_cache.clear()
    ad._mid_to_chat.clear()


def state(**cfg):
    s = ad.AntiDeleteState(FakeUDB())
    s.cfg.update(cfg)
    return s


def labels(rows):
    return [lbl for row in rows for lbl, _ in row]


def actions(rows):
    return [a for row in rows for _, a in row]


# ───────────── ortaq məntiq ─────────────
def test_layout_reflects_the_current_settings():
    on = labels(ad.layout(state(on=True, mode="dm", save_media=True)))
    assert "Anti-Delete: 🟢 Aktiv" in on and "✅ 🔒 Yalnız DM" in on and "🌐 Bütün çatlar" in on and "📎 Media: 🟢" in on
    off = labels(ad.layout(state(on=False, mode="all", save_media=False)))
    assert "Anti-Delete: 🔴 Deaktiv" in off and "🔒 Yalnız DM" in off and "✅ 🌐 Bütün çatlar" in off and "📎 Media: 🔴" in off
    assert actions(ad.layout(state())) == ["tg", "dm", "all", "media", "clear", "stats0", "r"]


def test_apply_changes_settings_and_persists_them():
    s = state(on=False, mode="all", save_media=True, caught=5, media_caught=2)
    assert "aktivləşdirildi" in ad.apply(s, "tg") and s.cfg["on"] is True
    assert "söndürüldü" in ad.apply(s, "tg") and s.cfg["on"] is False
    assert "yalnız şəxsi" in ad.apply(s, "dm") and s.cfg["mode"] == "dm"
    assert "bütün çatlar" in ad.apply(s, "all") and s.cfg["mode"] == "all"
    assert "saxlanmır" in ad.apply(s, "media") and s.cfg["save_media"] is False
    assert "saxlanır" in ad.apply(s, "media") and s.cfg["save_media"] is True
    assert "sıfırlandı" in ad.apply(s, "stats0") and s.cfg["caught"] == 0 == s.cfg["media_caught"]
    assert ad.apply(s, "r") == "" and ad.apply(s, "yoxdur") == ""
    saved = ad.AntiDeleteState(s._udb)
    assert saved.cfg["mode"] == "all" and saved.cfg["caught"] == 0                      # restartdan sonra qalır


def test_clear_cache_empties_memory_and_media_folder(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "1_2").write_bytes(b"x" * 1024)
    (media / "1_3").write_bytes(b"y" * 2048)
    ad._msg_cache[(1, 2)] = {"a": 1}
    ad._mid_to_chat[2] = 1
    assert ad.media_cache_stats()[0] == 2 and 0 < ad.media_cache_stats()[1] < 1
    s = state()
    assert ad.apply(s, "clear") == "🧹 Keş təmizləndi: 1 mesaj, 2 media faylı"
    assert not ad._msg_cache and not ad._mid_to_chat and list(media.iterdir()) == []
    assert ad.clear_cache() == (0, 0)


def test_status_text_shows_cache_size_and_note():
    ad._msg_cache[(1, 1)] = {}
    text = ad.status_text(state(on=True, mode="dm", caught=3, media_caught=1), "🔴 nümunə")
    assert "🟢 Aktiv" in text and "Yalnız Şəxsi (DM)" in text and "Keşdəki Mesajlar:</b> 1" in text
    assert "3 ədəd" in text and "Saved Messages" in text and text.endswith("🔴 nümunə")


# ───────────── .menu bölməsi ─────────────
class Sctx:
    def __init__(self, action=""):
        self.action, self.shown, self.answered = action, [], 0

    def btn(self, text, *parts):
        return InlineKeyboardButton(text=text, callback_data=":".join(["ubm", "tok", "antidelete", *parts]))

    def tr(self, text, **kw):
        return text

    def nav(self):
        return [InlineKeyboardButton(text="⬅️ Menyu", callback_data="ubm:tok:main")]

    async def answer(self, *a, **kw):
        self.answered += 1

    async def show(self, text, kb):
        self.shown.append((text, kb))


@pytest.fixture
def plugin():
    fub = fakes.FakeUB()
    fub._udb = FakeUDB()
    ad.register(fub)
    return fub


@pytest.mark.asyncio
async def test_section_is_registered_with_a_label_and_renders_the_buttons(plugin):
    sec = plugin.sections["antidelete"]
    assert sec["label"] == "🛡 Anti-Delete" and sec["order"] == 43
    text, kb = await sec["render"](Sctx())
    ds = [b.callback_data for r in kb.inline_keyboard for b in r]
    assert ds == ["ubm:tok:antidelete:tg", "ubm:tok:antidelete:dm", "ubm:tok:antidelete:all", "ubm:tok:antidelete:media",
                  "ubm:tok:antidelete:clear", "ubm:tok:antidelete:stats0", "ubm:tok:antidelete:r", "ubm:tok:main"]
    assert "Anti-Delete Monitoru" in text and len(ds) == len(set(ds))


@pytest.mark.asyncio
async def test_section_buttons_change_settings_and_redraw_with_a_note(plugin):
    handle = plugin.sections["antidelete"]["handle"]
    s = Sctx("tg")
    await handle(s)
    assert s.answered == 1 and "Anti-Delete söndürüldü" in s.shown[-1][0] and "🔴 Söndürülüb" in s.shown[-1][0]      # standart açıq idi
    s2 = Sctx("dm")
    await handle(s2)
    assert "yalnız şəxsi (DM)" in s2.shown[-1][0] and "✅ 🔒 Yalnız DM" in [b.text for r in s2.shown[-1][1].inline_keyboard for b in r]
    s3 = Sctx("r")
    await handle(s3)
    assert s3.shown and "Anti-Delete Monitoru" in s3.shown[-1][0]                      # yenilə — qeydsiz yenidən çəkir


# ───────────── .antidelete kartı ─────────────
class PanelStub:
    iid, dead, shown = "iid-9", False, False

    def __init__(self):
        self.outs = []

    def nav_rows(self, refresh=False):
        return []


class CardBot:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, **kw):
        self.calls.append(kw)


@pytest.mark.asyncio
async def test_bare_command_opens_a_card_whose_buttons_work(plugin, monkeypatch):
    from userbot.core import userbot_api as api
    monkeypatch.setattr(api, "CARD_MIN_GAP", 0)
    plugin.cards, plugin.tr, plugin.bot, plugin.creator_id = {}, (lambda t, **kw: t), CardBot(), 1
    ev = fakes.FakeEvent(None, chat_id=5)
    ev.panel, ev.sender_id = PanelStub(), 1
    pattern, fn = plugin.handlers["antidelete"]
    import re
    ev.pattern_match = re.match(pattern, ".antidelete")
    await fn(ev)
    (tok, out), = plugin.cards.items()
    ds = [b.callback_data for r in out.rows for b in r]
    assert f"ubc:{tok}:tg" in ds and f"ubc:{tok}:r" not in ds and len(ds) == len(set(ds))        # 🔄 kartın öz sətrindədir
    assert "Anti-Delete Monitoru" in out.text and plugin.bot.calls[-1]["inline_message_id"] == "iid-9"
    before = out.actions["media"]
    await out.actions["media"](out, None)
    assert "saxlanmır" in out.text and "📎 Media: 🔴" in [b.text for r in out.rows for b in r] and before
    await out.actions["clear"](out, None)
    assert "Keş təmizləndi" in out.text


@pytest.mark.asyncio
async def test_without_inline_support_the_command_falls_back_to_text(plugin):
    ev = await fakes.run_command(plugin, "antidelete", ".antidelete", types.SimpleNamespace())
    assert any("Anti-Delete Monitoru" in e for e in ev.edits)


# ───────────── menyuda görünür ─────────────
def test_the_real_menu_lists_the_section_in_functions(monkeypatch):
    from unittest.mock import MagicMock
    import userbot.core.userbot_api as api
    from userbot.core import plugin_menu as pm
    monkeypatch.setattr(api, "get_udb", lambda: FakeUDB())
    real = api.UB(MagicMock(creator_id=1))
    real._loading = "antidelete"
    ad.register(real)
    text, kb = pm.functions_view(real, "tok")
    labels_ = [b.text for r in kb.inline_keyboard for b in r]
    assert "🛡 Anti-Delete" in labels_ and "ubm:tok:antidelete" in [b.callback_data for r in kb.inline_keyboard for b in r]
    assert "🛡 Anti-Delete" in text
