"""Qrup üzrə audit: siyahı, aç/bağla, izləyən hesab, hədəf zənciri."""


def test_audit_is_off_by_default(logger_mod, ub):
    assert not logger_mod.is_audit_active_for(ub, -1001)


def test_add_group_enables_only_that_group(logger_mod, ub):
    logger_mod.add_group(ub, -1001, "A")
    assert logger_mod.is_audit_active_for(ub, -1001)
    assert not logger_mod.is_audit_active_for(ub, -1002)


def test_master_switch_overrides_groups(logger_mod, ub):
    logger_mod.add_group(ub, -1001, "A")
    ub.db.kv_set("logger", "audit_enabled", "0")
    assert not logger_mod.is_audit_active_for(ub, -1001)


def test_group_can_be_switched_off_but_stays_listed(logger_mod, ub):
    logger_mod.add_group(ub, -1001, "A")
    ub.db.kv_set("logger", logger_mod._gk(-1001, "on_au"), "0")
    assert not logger_mod.is_audit_active_for(ub, -1001)
    assert -1001 in logger_mod.listed_groups(ub)


def test_messages_do_not_add_groups_to_the_list(logger_mod, ub):
    logger_mod._remember_group(-2001, "Seen")
    assert -2001 not in logger_mod.listed_groups(ub)
    assert -2001 in logger_mod.discovered_groups(ub)


def test_removed_group_does_not_come_back_on_new_message(logger_mod, ub):
    logger_mod.add_group(ub, -2001, "A")
    logger_mod.forget_group(ub, -2001)
    logger_mod._remember_group(-2001, "A")
    assert -2001 not in logger_mod.listed_groups(ub)


def test_forget_all_inactive_keeps_active(logger_mod, ub):
    logger_mod.add_group(ub, -1, "on")
    logger_mod.add_group(ub, -2, "off")
    ub.db.kv_set("logger", logger_mod._gk(-2, "on_au"), "0")
    assert logger_mod.forget_all(ub, only_inactive=True) == 1
    assert set(logger_mod.listed_groups(ub)) == {-1}
    logger_mod.forget_all(ub, only_inactive=False)
    assert not logger_mod.listed_groups(ub)


def test_forget_group_clears_its_settings(logger_mod, ub):
    logger_mod.add_group(ub, -3, "g")
    logger_mod.set_group_target(ub, -3, "join", "-100555", "7")
    logger_mod.set_group_source(ub, -3, "userbot")
    logger_mod.set_daily(ub, -3, False)
    logger_mod.forget_group(ub, -3)
    assert not any(k.startswith("g-3:") for k in ub.db.kv_all("logger"))


def test_target_chain_group_event_beats_group_all_beats_global(logger_mod, ub):
    logger_mod.set_target(ub, "au", "-100999")
    assert logger_mod.get_stream_target(ub, "join", -1)[0] == "-100999"
    logger_mod.set_group_target(ub, -1, "au", "-100777")
    assert logger_mod.get_stream_target(ub, "left", -1)[0] == "-100777"
    logger_mod.set_group_target(ub, -1, "join", "-100555", "7")
    chat, topic, label = logger_mod.get_stream_target(ub, "join", -1)
    assert (chat, topic) == ("-100555", "7") and "[qrup]" in label
    assert logger_mod.get_stream_target(ub, "join", -2)[0] == "-100999"      # başqa qrup toxunulmaz


def test_clear_topic_keeps_chat(logger_mod, ub):
    logger_mod.set_group_target(ub, -1, "join", "-100555", "7")
    logger_mod.clear_topic(ub, "join", -1)
    assert logger_mod.get_stream_target(ub, "join", -1)[:2] == ("-100555", None)


def test_reset_group_stream_returns_to_inherited(logger_mod, ub):
    logger_mod.set_target(ub, "au", "-100999")
    logger_mod.set_group_target(ub, -1, "join", "-100555")
    logger_mod.reset_group_stream(ub, -1, "join")
    assert logger_mod.get_stream_target(ub, "join", -1)[0] == "-100999"


def test_event_toggle_is_per_group(logger_mod, ub):
    logger_mod.set_group_event_on(ub, -1, "mute", False)
    assert not logger_mod.stream_on(ub, "mute", -1)
    assert logger_mod.stream_on(ub, "mute", -2)
    assert logger_mod.stream_on(ub, "kick", -1)


def test_enabling_event_reenables_disabled_parent_for_that_group_only(logger_mod, ub):
    ub.db.kv_set("logger", "on_ban", "0")                      # qlobal bölmə söndürülüb
    assert not logger_mod.stream_on(ub, "mute", -2)
    logger_mod.set_group_event_on(ub, -1, "mute", True)
    assert logger_mod.stream_on(ub, "mute", -1)
    assert not logger_mod.stream_on(ub, "mute", -2)


def test_daily_report_default_on_and_per_group(logger_mod, ub):
    logger_mod.add_group(ub, -1, "a")
    logger_mod.add_group(ub, -2, "b")
    logger_mod.set_daily(ub, -2, False)
    assert set(logger_mod.daily_groups(ub)) == {-1}


def test_daily_groups_requires_active_audit(logger_mod, ub):
    logger_mod.add_group(ub, -1, "a")
    ub.db.kv_set("logger", logger_mod._gk(-1, "on_au"), "0")
    assert not logger_mod.daily_groups(ub)
