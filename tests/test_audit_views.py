"""Audit menyusu: düymələr, callback uzunluğu, silinmiş düymələrin olmaması."""
import pytest


@pytest.fixture
def views(logger_mod, ub, monkeypatch):
    from bot.menus.views import audit as av

    monkeypatch.setattr(av, "_lg", lambda: logger_mod)
    monkeypatch.setattr(av, "_lgub", lambda: ub)
    return av


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _check_limits(markup):
    for b in _buttons(markup):
        assert len((b.callback_data or "").encode()) <= 64, b.callback_data


def test_hub_has_only_the_essential_buttons(views):
    _, kb = views.audit_settings_view()
    texts = " ".join(b.text for b in _buttons(kb))
    assert "Qrup üzrə ayarlar" in texts and "Audit" in texts
    for removed in ("Ümumi hədəf", "23:59", "HTML Hesabat", "Silinən", "Redaktə", "İzləyən"):
        assert removed not in texts
    _check_limits(kb)


def test_group_list_shows_only_added_groups(views, logger_mod, ub):
    logger_mod._remember_group(-5, "Seen only")
    logger_mod.add_group(ub, -6, "Added")
    text, kb = views.audit_groups_view()
    labels = [b.text for b in _buttons(kb)]
    assert any("Added" in t for t in labels)
    assert not any("Seen only" in t for t in labels)
    assert any("Qrup əlavə et" in t for t in labels) and any("Bilinənlərdən" in t for t in labels)
    _check_limits(kb)


def test_discover_view_offers_unlisted_groups(views, logger_mod, ub):
    logger_mod._remember_group(-5, "Seen only")
    _, kb = views.audit_discover_view()
    assert any("Seen only" in b.text for b in _buttons(kb))
    _check_limits(kb)


def test_group_screen_has_all_controls(views, logger_mod, ub):
    logger_mod.add_group(ub, -7, "G")
    text, kb = views.audit_gview(-7)
    labels = " ".join(b.text for b in _buttons(kb))
    for needed in ("Bu qrupda audit", "Hamısı üçün hədəf", "İzləyən", "23:59 hesabat", "İndi hesabat",
                   "Hesabat hədəfi", "Tək mesaj silindi", "Ayarları sıfırla", "Qrupu siyahıdan sil"):
        assert needed in labels, needed
    _check_limits(kb)


def test_report_target_label_is_personal_chat_until_group_target_set(views, logger_mod, ub):
    logger_mod.add_group(ub, -7, "G")
    logger_mod.set_target(ub, "au", "-100999")                    # köhnə qlobal hədəf nəzərə alınmamalıdır
    _, kb = views.audit_gview(-7)
    assert any("Hesabat hədəfi: şəxsi çat" in b.text for b in _buttons(kb))
    logger_mod.set_group_target(ub, -7, "report", "-100888")
    _, kb = views.audit_gview(-7)
    assert not any("şəxsi çat" in b.text for b in _buttons(kb))


def test_target_view_offers_topic_and_reset_only_when_relevant(views, logger_mod, ub):
    logger_mod.add_group(ub, -7, "G")
    _, kb = views.audit_gtarget_view(-7, "join")
    labels = " ".join(b.text for b in _buttons(kb))
    assert "Topic-i sil" not in labels and "Qrupun ayarını sil" not in labels
    logger_mod.set_group_target(ub, -7, "join", "-1001234567890", "5")
    _, kb = views.audit_gtarget_view(-7, "join")
    labels = " ".join(b.text for b in _buttons(kb))
    assert "Topic-i sil" in labels and "Qrupun ayarını sil" in labels and "Topic seç" in labels
    _check_limits(kb)


def test_clear_view_counts(views, logger_mod, ub):
    logger_mod.add_group(ub, -1, "on")
    logger_mod.add_group(ub, -2, "off")
    ub.db.kv_set("logger", logger_mod._gk(-2, "on_au"), "0")
    text, kb = views.audit_clear_view()
    labels = [b.text for b in _buttons(kb)]
    assert any("İzlənməyənləri sil (1)" in t for t in labels) and any("Hamısını sil (2)" in t for t in labels)
