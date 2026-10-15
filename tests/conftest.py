"""Ortaq test köməkçiləri — DB / Telegram olmadan işləyir (saxta kv bazası, saxta kontekst)."""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("DLM_SKIP_DEPS", "1")


class FakeKV:
    """userbot_db.UserbotDB-nin kv_* hissəsinin yaddaşdakı surəti."""

    def __init__(self):
        self.d = {}

    def kv_get(self, ns, key, default=None):
        return self.d.get((ns, key), default)

    def kv_set(self, ns, key, value):
        self.d[(ns, key)] = value

    def kv_del(self, ns, key):
        return self.d.pop((ns, key), None) is not None

    def kv_all(self, ns):
        return {k: v for (n, k), v in self.d.items() if n == ns}


@pytest.fixture
def logger_mod():
    """userbot/plugins/logger.py — modul səviyyəli vəziyyət hər testdə sıfırlanır."""
    import userbot.plugins.logger as lg

    lg._group_titles.clear()
    lg._groups_dirty = False
    lg.AUDIT_HANDLERS[:] = []
    lg._AUDIT_UB = None
    lg._CUR_CLIENT.set(None)
    yield lg
    lg._group_titles.clear()
    lg.AUDIT_HANDLERS[:] = []
    lg._AUDIT_UB = None


@pytest.fixture
def ub(monkeypatch):
    """Saxta userbot obyekti: .db (kv), .context (səsli asistan olmadan), .client=None."""
    db = FakeKV()
    obj = types.SimpleNamespace(db=db, context=types.SimpleNamespace(voice_assistant=None), client=None)
    import userbot.core.userbot_db as udb

    monkeypatch.setattr(udb, "get_udb", lambda: db)
    return obj


class FakeSlot:
    running = True
    voice_enabled = True

    def __init__(self):
        self.client = object()


@pytest.fixture
def assistant(ub):
    """Aktiv asistan slotu olan userbot."""
    va = types.SimpleNamespace(slots=[FakeSlot()])
    ub.context.voice_assistant = va
    return va
