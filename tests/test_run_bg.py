"""run_bg: SQLite yazıları fon thread-ində, sıra ilə."""
import threading

from userbot.core.userbot_db import run_bg


def test_runs_off_the_calling_thread_in_order():
    seen, names = [], set()

    def work(i):
        seen.append(i)
        names.add(threading.current_thread().name)

    futures = [run_bg(work, i) for i in range(20)]
    for f in futures:
        f.result(timeout=5)
    assert seen == list(range(20))                                  # put → del sırası qarışmır
    assert threading.current_thread().name not in names
    assert all(n.startswith("ub-db-bg") for n in names)


def test_exception_does_not_break_the_queue():
    def boom():
        raise RuntimeError("x")

    assert run_bg(boom).result(timeout=5) is None
    assert run_bg(lambda: 7).result(timeout=5) == 7


def test_mute_registry_writes_through_run_bg(monkeypatch):
    import userbot.plugins.chat_tools as ct

    calls = []
    monkeypatch.setattr(ct, "run_bg", lambda fn, *a: calls.append((fn.__name__, a)))
    reg = ct.MuteRegistry.__new__(ct.MuteRegistry)
    reg.data = {}
    monkeypatch.setattr(ct, "get_udb", lambda: type("U", (), {"rec_put": lambda *a: None, "rec_del": lambda *a: None})())
    reg.add(-1, 5, "x", 60)
    assert reg.remove(-1, 5) is True
    assert [c[0] for c in calls] == ["<lambda>", "<lambda>"] or len(calls) == 2
