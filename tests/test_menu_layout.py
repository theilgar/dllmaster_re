"""/menu bölmələrinin düymələri: təkrar yoxdur, callback-lər limitdədir, köçürülənlər yerindədir."""
import types

import pytest


@pytest.fixture
def hub(monkeypatch):
    import bot.menus  # noqa: F401  (dövrü importları qaydaya salır)
    from bot.menus.views import system_hub as H
    from bot.menus.views import dev

    ctx = types.SimpleNamespace(userbot_enabled=True, bot_enabled=True, creator_id=1, plugin_status={}, mode="all")
    monkeypatch.setattr(H, "get_context", lambda: ctx)
    monkeypatch.setattr(dev, "get_context", lambda: ctx)
    return H, dev


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def _no_dupes_and_limits(markup):
    datas = [d for d in _datas(markup)]
    assert len(datas) == len(set(datas)), "təkrar callback"
    assert all(len(d.encode()) <= 64 for d in datas)


def test_users_section_has_single_stats_button(hub):
    H, _ = hub
    _, kb = H.aud_view()
    labels = _labels(kb)
    assert sum("Statistika" in t for t in labels) == 1
    assert not any(t in ("📈 Qrafik", "🏆 Top") for t in labels)
    _no_dupes_and_limits(kb)


def test_userbot_section_lost_moved_buttons(hub):
    H, _ = hub
    _, kb = H.bots_view()
    labels = " ".join(_labels(kb))
    for gone in ("Userbot Bazası", "İnline Söndürülmüş", "Moderasiya"):
        assert gone not in labels
    assert "Qrup auditi" in labels
    _no_dupes_and_limits(kb)


def test_help_screen_has_moderation_commands(hub):
    H, _ = hub
    _, kb = H.help_view()
    assert "menu:mod_cmds" in _datas(kb)


def test_depo_section_is_minimal(hub):
    H, _ = hub
    _, kb = H.depo_view()
    labels = " ".join(_labels(kb))
    assert "Köməkçilər" not in labels and "Kənar qruplar" not in labels
    _no_dupes_and_limits(kb)


def test_system_and_bot_admin_split(hub):
    H, dev = hub
    sys_labels = " ".join(_labels(H.sys_kb()))
    assert "Texniki iş rejimi" not in sys_labels and "Arxiv" not in sys_labels and "Görünüş" not in sys_labels
    assert "Server" in sys_labels and "Performans" in sys_labels
    _, kb = dev.dev_hub_view()
    admin_labels = " ".join(_labels(kb))
    for needed in ("Arxiv", "Görünüş", "Texniki iş rejimi", "GitHub", "Restart"):
        assert needed in admin_labels
    _no_dupes_and_limits(kb)


def test_music_section_has_no_codec_button(hub):
    from bot.menus.views import music

    _, kb = music.mus_view()
    assert "codec:panel" not in _datas(kb)
