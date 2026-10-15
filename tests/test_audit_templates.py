"""Qrup şablonu: bir qrupun ayarlarını başqasına / hamısına kopyalamaq."""
import pytest


@pytest.fixture
def groups(logger_mod, ub):
    lg = logger_mod
    for cid, name in ((-1, "Şablon"), (-2, "Yeni"), (-3, "Üçüncü")):
        lg.add_group(ub, cid, name)
    lg.set_group_target(ub, -1, "au", "-100777", "9")
    lg.set_group_target(ub, -1, "join", "-100555")
    lg.set_group_event_on(ub, -1, "mute", False)
    lg.set_group_source(ub, -1, "userbot")
    lg.set_daily(ub, -1, False)
    return lg


def test_group_settings_excludes_audit_switch(groups, ub):
    data = groups.group_settings(ub, -1)
    assert "on_au" not in data
    assert data["audit_target_chat"] == "-100777" and data["on_mute"] == "0" and data["source"] == "userbot"


def test_copy_applies_everything_but_keeps_audit_switch(groups, ub):
    ub.db.kv_set("logger", groups._gk(-2, "on_au"), "0")                  # hədəf qrup söndürülüb
    n = groups.copy_group_settings(ub, -1, -2)
    assert n == len(groups.group_settings(ub, -1))
    assert groups.get_stream_target(ub, "left", -2)[:2] == ("-100777", "9")
    assert groups.get_stream_target(ub, "join", -2)[0] == "-100555"
    assert not groups.stream_on(ub, "mute", -2)
    assert groups.group_source(ub, -2) == "userbot" and not groups.daily_on(ub, -2)
    assert not groups.is_audit_active_for(ub, -2)                          # söndürülü qaldı


def test_copy_replaces_previous_settings(groups, ub):
    groups.set_group_target(ub, -2, "pin", "-100111")
    groups.set_group_event_on(ub, -2, "kick", False)
    groups.copy_group_settings(ub, -1, -2)
    assert groups.get_stream_target(ub, "pin", -2)[0] == "-100777"         # köhnə "pin" hədəfi getdi
    assert groups.stream_on(ub, "kick", -2)


def test_copy_does_not_touch_source_or_self(groups, ub):
    before = dict(groups.group_settings(ub, -1))
    assert groups.copy_group_settings(ub, -1, -1) == 0
    groups.copy_group_settings(ub, -1, -2)
    assert groups.group_settings(ub, -1) == before


def test_copy_to_all_affects_every_other_listed_group(groups, ub):
    assert groups.copy_to_all(ub, -1) == 2
    for cid in (-2, -3):
        assert groups.get_stream_target(ub, "title", cid)[0] == "-100777"
        assert not groups.stream_on(ub, "mute", cid)


def test_copy_to_all_ignores_unlisted_groups(groups, ub):
    groups._remember_group(-9, "yalnız görülən")
    groups.copy_to_all(ub, -1)
    assert not groups.group_settings(ub, -9)


@pytest.fixture
def views(logger_mod, ub, monkeypatch):
    from bot.menus.views import audit as av

    monkeypatch.setattr(av, "_lg", lambda: logger_mod)
    monkeypatch.setattr(av, "_lgub", lambda: ub)
    return av


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_group_screen_offers_copy_and_apply_all(groups, views):
    _, kb = views.audit_gview(-2)
    labels = " ".join(b.text for b in _buttons(kb))
    assert "Başqa qrupdan kopyala" in labels and "Hamısına tətbiq et" in labels


def test_copy_view_lists_other_groups_with_setting_counts(groups, views):
    _, kb = views.audit_copy_view(-2)
    texts = [b.text for b in _buttons(kb)]
    assert any("Şablon" in t for t in texts) and not any("Yeni" in t for t in texts)      # özü siyahıda yoxdur
    for b in _buttons(kb):
        assert len((b.callback_data or "").encode()) <= 64
    assert "menu:au:gcp:-2:-1" in [b.callback_data for b in _buttons(kb)]


def test_apply_all_requires_confirmation(groups, views):
    text, kb = views.audit_apply_all_view(-1)
    datas = [b.callback_data for b in _buttons(kb)]
    assert "menu:au:gall:-1:ok" in datas and "2 qrupa" in " ".join(b.text for b in _buttons(kb))
    assert "digər <b>2</b> qrupa" in text


def test_apply_all_without_other_groups_has_no_confirm(logger_mod, ub, views):
    logger_mod.add_group(ub, -1, "tək")
    _, kb = views.audit_apply_all_view(-1)
    assert not any("gall" in (b.callback_data or "") and b.callback_data.endswith(":ok") for b in _buttons(kb))
