"""common/kv.py — ortaq ayar və dövr köməkçiləri."""
import pytest

from common import kv


class FakeSettings:
    def __init__(self):
        self.d = {}

    def get_setting(self, key, default=None):
        return self.d.get(key, default)

    def set_setting(self, key, value):
        if value is None:
            self.d.pop(key, None)
        else:
            self.d[key] = str(value)


@pytest.fixture
def db(monkeypatch):
    import common.database as cd
    s = FakeSettings()
    monkeypatch.setattr(cd, "get_db", lambda: s)
    return s


# ── next_of ──
@pytest.mark.parametrize("seq,cur,expected", [
    ([1, 2, 3], 1, 2), ([1, 2, 3], 3, 1), ("abc", "b", "c"), ((5,), 5, 5), ([1, 2, 3], 99, 1)])
def test_next_of_cycles(seq, cur, expected):
    assert kv.next_of(seq, cur) == expected


def test_next_of_missing_uses_default_and_empty_is_safe():
    assert kv.next_of(["a", "b"], "zzz", "b") == "b"
    assert kv.next_of([], 1) is None and kv.next_of([], 1, "x") == "x"
    assert kv.next_of(range(3), 2) == 0                                 # istənilən iterable


# ── mətn / bool ──
def test_text_roundtrip_and_delete(db):
    assert kv.get("a", "dflt") == "dflt"
    kv.set("a", 5)
    assert kv.get("a") == "5"
    kv.set("a", None)
    assert kv.get("a", "dflt") == "dflt"


def test_bool_and_toggle(db):
    assert kv.get_bool("f") is False and kv.get_bool("f", True) is True
    assert kv.toggle("f") is True and kv.get_bool("f") is True
    assert kv.toggle("f") is False
    assert kv.toggle("g", default=True) is False                       # defolt açıqdır → ilk basış söndürür


# ── tam ədəd ──
def test_int_choices_and_cycle(db):
    assert kv.get_int("n", (10, 20, 30), 20) == 20
    db.d["n"] = "999"
    assert kv.get_int("n", (10, 20, 30), 20) == 20                      # icazəsiz dəyər → default
    db.d["n"] = "abc"
    assert kv.get_int("n", (10, 20, 30), 20) == 20
    assert [kv.cycle_int("c", (10, 20, 30), 10) for _ in range(4)] == [20, 30, 10, 20]


def test_cycle_str(db):
    assert [kv.cycle_str("s", ("a", "b"), "a") for _ in range(3)] == ["b", "a", "b"]
    db.d["s"] = "unknown"
    assert kv.cycle_str("s", ("a", "b"), "b") == "b"


# ── json ──
def test_json_roundtrip_and_bad_data(db):
    assert kv.get_json("j", []) == []
    kv.set_json("j", {"ad": "Əli", "n": [1, 2]})
    assert kv.get_json("j") == {"ad": "Əli", "n": [1, 2]} and "Əli" in db.d["j"]      # unicode escape olmur
    db.d["j"] = "{pozuq"
    assert kv.get_json("j", "x") == "x"


# ── dayanıqlıq ──
def test_never_raises_when_db_is_down(monkeypatch):
    import common.database as cd

    def boom():
        raise RuntimeError("baza yoxdur")

    monkeypatch.setattr(cd, "get_db", boom)
    assert kv.get("x", "d") == "d" and kv.get_bool("x", True) is True and kv.get_int("x", (1, 2), 2) == 2
    kv.set("x", 1)                                                       # xəta atmır (log yazır)
    assert kv.get_json("x", [1]) == [1]
