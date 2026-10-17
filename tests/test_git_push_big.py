"""Böyük fayl `git add`-ı saatlarla yeyir (1,9 GB yedək arxivi → «git add: vaxt bitdi», 4 GB tmp_pack zibili, index.lock)."""
import subprocess
from pathlib import Path

import pytest

import bot.updater.git_client as G


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    sh(tmp_path, "git", "init", "--bare", "-b", "main", str(bare))
    sh(tmp_path, "git", "clone", str(bare), str(work))
    sh(work, "git", "config", "user.name", "T")
    sh(work, "git", "config", "user.email", "t@t")
    (work / "a.txt").write_text("1\n")
    sh(work, "git", "add", "a.txt")
    sh(work, "git", "commit", "-m", "ilk")
    sh(work, "git", "push", "-u", "origin", "main")
    monkeypatch.setattr(G, "_token", lambda: "")
    monkeypatch.setattr(G, "MAX_FILE_MB", 0.01)                  # 10 KB — «böyük» test üçün
    monkeypatch.delenv("GITHUB_REMOTE", raising=False)
    return work, bare


def big(work, name="dlm_backup.tar.gz", size=20_000):
    (work / name).write_bytes(b"x" * size)
    return name


def test_oversize_detection(repo):
    work, _ = repo
    name = big(work)
    assert G.is_oversize(work, name) and not G.is_oversize(work, "a.txt") and not G.is_oversize(work, "yoxdur.bin")


def test_big_files_are_never_picked_automatically(repo):
    work, _ = repo
    big(work)
    (work / "b.txt").write_text("kiçik\n")
    changes, excluded = G.working_changes(work)
    assert ("new", "b.txt") in changes and ("new", "dlm_backup.tar.gz") in excluded
    assert all(p != "dlm_backup.tar.gz" for _, p in changes)


def test_push_update_skips_the_big_file_and_sends_the_rest(repo):
    work, bare = repo
    name = big(work)
    (work / "b.txt").write_text("kiçik\n")
    res = G.push_update(work, [name, "b.txt"], "yeni")
    assert res["ok"] and res["oversize"] == [name] and res["files"] == ["b.txt"]
    tree = sh(bare, "git", "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "b.txt" in tree and name not in tree


def test_selecting_the_big_file_by_hand_does_not_force_it_through(repo):
    work, bare = repo
    name = big(work)
    (work / "b.txt").write_text("kiçik\n")
    res = G.push_all(work, "seçilmiş", {name, "b.txt"})
    assert res["ok"] and name in res["oversize"] and name in res["oversize_skipped"] + res["oversize"]
    assert name not in sh(bare, "git", "ls-tree", "-r", "--name-only", "main").splitlines()


def test_only_the_big_file_means_nothing_to_commit_but_no_hang(repo):
    work, _ = repo
    name = big(work)
    res = G.push_update(work, [name], "yalnız böyük")
    assert res["ok"] and not res["committed"] and res["oversize"] == [name]


# ───────────── öldürülən git əmrinin zibili ─────────────
def _leftovers(work):
    (work / ".git" / "index.lock").write_text("")
    pack = work / ".git" / "objects" / "pack"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "tmp_pack_AbC123").write_bytes(b"0" * 4096)
    (pack / "pack-keep.idx").write_bytes(b"1")


def _timeout(*a, **kw):
    raise subprocess.TimeoutExpired("git", 1)


def test_timeout_of_add_cleans_lock_and_partial_packs(repo, monkeypatch):
    work, _ = repo
    _leftovers(work)
    real = subprocess.run

    def run(cmd, *a, **kw):
        if cmd[:1] == ["pgrep"]:
            return subprocess.CompletedProcess(cmd, 1, b"", b"")
        return _timeout() if "add" in cmd else real(cmd, *a, **kw)
    monkeypatch.setattr(G.subprocess, "run", run)
    rc, _, err = G.git(work, "add", "-A", "--", "x", timeout=1)
    assert rc == 124 and "vaxt bitdi" in err
    pack = work / ".git" / "objects" / "pack"
    assert not (work / ".git" / "index.lock").exists() and not list(pack.glob("tmp_pack_*"))
    assert (pack / "pack-keep.idx").exists()                                   # həqiqi pack faylına toxunulmur


def test_cleanup_is_skipped_while_another_git_runs(repo, monkeypatch):
    work, _ = repo
    _leftovers(work)
    real = subprocess.run
    monkeypatch.setattr(G.subprocess, "run", lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 0, b"9\n", b"")
                        if cmd[:1] == ["pgrep"] else (_timeout() if "add" in cmd else real(cmd, *a, **kw)))
    G.git(work, "add", "x", timeout=1)
    assert (work / ".git" / "index.lock").exists() and list((work / ".git" / "objects" / "pack").glob("tmp_pack_*"))


def test_timeout_of_read_only_commands_leaves_files_alone(repo, monkeypatch):
    work, _ = repo
    _leftovers(work)
    monkeypatch.setattr(G.subprocess, "run", _timeout)
    assert G.git(work, "status", timeout=1)[0] == 124
    assert (work / ".git" / "index.lock").exists()


def test_project_backup_archives_are_ignored():
    ignore = Path(__file__).resolve().parent.parent / ".gitignore"
    assert "dlm_backup*" in ignore.read_text(encoding="utf-8").splitlines()
