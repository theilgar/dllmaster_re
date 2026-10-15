"""set_setting event loop-da DB-ni gözləmir (fon thread-i, sıra ilə); loop yoxdursa sinxron yazır."""
import asyncio
import threading

from common.database import Database


def _fake_db():
    db = Database.__new__(Database)
    db._settings, db._bg, db._bg_lock = {}, None, threading.Lock()
    db.calls = []

    def fake_exec(sql, params=None):
        db.calls.append((threading.current_thread().name, params))
        return 1

    db._exec = fake_exec
    return db


def test_sync_context_writes_directly():
    db = _fake_db()
    db.set_setting("a", 1)
    assert db.get_setting("a") == "1"
    assert db.calls[0][0] == threading.current_thread().name


def test_event_loop_writes_in_background_in_order():
    db = _fake_db()

    async def main():
        for i in range(10):
            db.set_setting("k", i)
        assert db.get_setting("k") == "9"            # keş dərhal yenilənir
        db.delete_setting("k")
        assert db.get_setting("k") is None

    asyncio.run(main())
    db.flush_writes()
    names = {n for n, _ in db.calls}
    assert len(db.calls) == 11 and all(n.startswith("db-settings") for n in names)
    assert [p[1] for _, p in db.calls[:10]] == [str(i) for i in range(10)]   # sıra qarışmır
