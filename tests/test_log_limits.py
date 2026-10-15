"""bot_logs qovluğu: köhnə fayllar və ümumi ölçü məhdudlaşdırılır."""
import datetime
import os
import time

from common.logger import enforce_log_limits


def _make(path, size_kb=1, age_days=0):
    with open(path, "wb") as f:
        f.write(b"x" * size_kb * 1024)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


def test_old_logs_removed_recent_kept(tmp_path):
    today = datetime.date.today().isoformat()
    _make(tmp_path / f"bot({today}).log", age_days=0)
    _make(tmp_path / "bot(2020-01-01).log", age_days=30)
    _make(tmp_path / "bot(2020-01-01).log.3", age_days=30)
    _make(tmp_path / "other.txt", age_days=90)                      # tanınmayan fayla toxunulmur
    removed = enforce_log_limits(str(tmp_path), max_days=7)
    assert removed == 2
    assert sorted(os.listdir(tmp_path)) == sorted([f"bot({today}).log", "other.txt"])


def test_hang_dumps_kept_longer(tmp_path):
    _make(tmp_path / "shutdown_hang_a.txt", age_days=10)             # 14 gündən az → qalır
    _make(tmp_path / "shutdown_hang_b.txt", age_days=20)
    enforce_log_limits(str(tmp_path), max_days=7)
    assert sorted(os.listdir(tmp_path)) == ["shutdown_hang_a.txt"]


def test_total_size_cap_removes_oldest_but_keeps_today(tmp_path):
    today = datetime.date.today().isoformat()
    _make(tmp_path / "bot(2026-01-01).log", size_kb=600, age_days=3)
    _make(tmp_path / "bot(2026-01-02).log", size_kb=600, age_days=2)
    _make(tmp_path / f"bot({today}).log", size_kb=600, age_days=0)
    removed = enforce_log_limits(str(tmp_path), max_days=30, max_total_mb=1)
    names = os.listdir(tmp_path)
    assert f"bot({today}).log" in names and "bot(2026-01-01).log" not in names
    assert removed >= 1


def test_console_out_rotated_when_too_big(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_CONSOLE_MAX_MB", "1")
    _make(tmp_path / "console.out", size_kb=1500)
    enforce_log_limits(str(tmp_path), max_days=7)
    assert os.path.getsize(tmp_path / "console.out") == 0
    assert (tmp_path / "console.out.1").exists()


def test_missing_dir_is_fine(tmp_path):
    assert enforce_log_limits(str(tmp_path / "yoxdur")) == 0
