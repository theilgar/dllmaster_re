"""Gündəlik / əl ilə HTML hesabat: hədəf seçimi və qrup üzrə göndəriş."""
import types

import pytest


@pytest.fixture
def audit(ub, monkeypatch, tmp_path):
    import importlib

    import common.audit as ca
    import common.database as cd

    ca = importlib.reload(ca)
    monkeypatch.setattr(ca, "AUDIT_REPORTS_DIR", str(tmp_path))

    class FakeDB:
        settings = {}

        def get_admin_audit_logs(self, **k):
            return []

        def get_audit_summary_stats(self, **k):
            return {}

        def get_setting(self, key, default=None):
            return self.settings.get(key, default)

        def set_setting(self, key, value):
            self.settings[key] = value

    db = FakeDB()
    monkeypatch.setattr(cd, "get_db", lambda: db)
    return ca, db


def test_report_destination_chain(audit, logger_mod, ub):
    ca, _ = audit
    logger_mod.add_group(ub, -5, "g")
    assert ca.group_report_dest(-5) == (None, None)                        # şəxsi çat
    logger_mod.set_target(ub, "au", "-100999")                              # köhnə qlobal hədəf yox sayılır
    assert ca.group_report_dest(-5) == (None, None)
    logger_mod.set_group_target(ub, -5, "au", "-100777", "9")
    assert ca.group_report_dest(-5) == ("-100777", 9)
    logger_mod.set_group_target(ub, -5, "report", "-100888")
    assert ca.group_report_dest(-5) == ("-100888", None)


class FakeBot:
    def __init__(self):
        self.sent = {}

    async def send_document(self, chat, f, caption=None, parse_mode=None, **kw):
        self.sent = {"chat": chat, "kw": kw}


async def test_report_goes_to_group_and_topic(audit):
    ca, _ = audit
    bot = FakeBot()
    ctx = types.SimpleNamespace(bot=bot, creator_id=1)
    await ca.send_daily_audit_report(ctx, force=True, chat_id=-5, chat_title="G", target_chat="-100888", thread_id=12)
    assert bot.sent == {"chat": -100888, "kw": {"message_thread_id": 12}}


async def test_report_defaults_to_creator(audit):
    ca, _ = audit
    bot = FakeBot()
    ctx = types.SimpleNamespace(bot=bot, creator_id=1)
    await ca.send_daily_audit_report(ctx, force=True, chat_id=-5, chat_title="G")
    assert bot.sent["chat"] == 1 and bot.sent["kw"] == {}


async def test_group_reports_sent_once_per_day(audit, monkeypatch):
    ca, db = audit
    calls = []

    async def fake_send(context, date_str=None, force=False, target_chat=None, chat_id=None, chat_title=None,
                        thread_id=None):
        calls.append((chat_id, target_chat, thread_id))
        return "file.html", 1, "123"

    monkeypatch.setattr(ca, "send_daily_audit_report", fake_send)
    monkeypatch.setattr(ca, "_daily_groups", lambda: {-1: "A", -2: "B"})
    monkeypatch.setattr(ca, "group_report_dest", lambda cid: ("-100888", 3) if cid == -2 else (None, None))
    assert await ca._send_group_reports(None, "2026-10-14") == 2
    assert (-1, None, None) in calls and (-2, "-100888", 3) in calls
    assert await ca._send_group_reports(None, "2026-10-14") == 0           # eyni gündə təkrar yox
    assert await ca._send_group_reports(None, "2026-10-15") == 2
