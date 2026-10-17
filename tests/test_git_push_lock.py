"""GitHub-a göndərmə: köhnə .git/index.lock özü təmizlənir; token olmadan fetch alınmırdı (şəxsi repo)."""
import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

import bot.updater.git_client as G
import common.updates as U


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Lokal bare «GitHub» + onun klonu; token / baza yoxdur."""
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
    monkeypatch.delenv("GITHUB_REMOTE", raising=False)
    return work, bare


def make_lock(work, age):
    lock = work / ".git" / "index.lock"
    lock.write_text("")
    t = time.time() - age
    os.utime(lock, (t, t))
    return lock


def test_stale_lock_is_removed_and_the_push_goes_through(repo, monkeypatch):
    work, bare = repo
    lock = make_lock(work, age=18 * 3600)
    (work / "a.txt").write_text("2\n")
    monkeypatch.setattr(G.subprocess, "run", _no_git_running(G.subprocess.run))
    res = G.push_update(work, ["a.txt"], "yeniləmə")
    assert res["ok"] and res["committed"] and not lock.exists()
    assert sh(bare, "git", "log", "-1", "--format=%s", "main") == "yeniləmə"


def _no_git_running(real):
    def run(cmd, *a, **kw):
        if cmd[:1] == ["pgrep"]:
            return subprocess.CompletedProcess(cmd, 1, b"", b"")             # heç bir git prosesi yoxdur
        return real(cmd, *a, **kw)
    return run


def test_fresh_lock_is_left_alone_and_the_error_is_explained(repo):
    work, _ = repo
    lock = make_lock(work, age=5)
    (work / "a.txt").write_text("3\n")
    res = G.push_update(work, ["a.txt"], "yeniləmə")
    assert not res["ok"] and lock.exists() and "index.lock" in res["error"]
    assert G.clear_stale_lock(work) is False                                  # təzə kilid toxunulmazdır
    assert "rm .git/index.lock" in G.push_hint("fatal: Unable to create '.git/index.lock': File exists.")


def test_lock_is_kept_while_a_git_process_is_running(repo, monkeypatch):
    work, _ = repo
    lock = make_lock(work, age=3600)
    real = subprocess.run
    monkeypatch.setattr(G.subprocess, "run", lambda cmd, *a, **kw:
                        subprocess.CompletedProcess(cmd, 0, b"123\n", b"") if cmd[:1] == ["pgrep"] else real(cmd, *a, **kw))
    assert G.clear_stale_lock(work) is False and lock.exists()


def test_missing_pgrep_only_clears_very_old_locks(repo, monkeypatch):
    work, _ = repo
    real = subprocess.run

    def run(cmd, *a, **kw):
        if cmd[:1] == ["pgrep"]:
            raise FileNotFoundError("pgrep")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(G.subprocess, "run", run)
    lock = make_lock(work, age=300)
    assert G.clear_stale_lock(work) is False and lock.exists()               # 5 dəq. — pgrep olmadan təhlükəli
    make_lock(work, age=3600)
    assert G.clear_stale_lock(work) is True and not lock.exists()


def test_no_lock_is_a_noop(repo):
    assert G.clear_stale_lock(repo[0]) is False


# ───────────── token ─────────────
def test_auth_args_carry_the_token_only_when_there_is_one(monkeypatch):
    monkeypatch.setattr(G, "_token", lambda: "")
    assert G.auth_args() == []
    monkeypatch.setattr(G, "_token", lambda: "ghp_secret")
    args = G.auth_args()
    assert args[0] == "-c" and args[1].startswith("http.https://github.com/.extraheader=AUTHORIZATION: basic ")
    assert "ghp_secret" not in args[1]                                         # base64 — açıq mətn deyil


def test_update_check_fetches_with_the_github_token(monkeypatch):
    """Əvvəl `git fetch` tokensiz idi → şəxsi repoda «git fetch alınmadı» (gündə onlarla dəfə)."""
    seen = []

    async def run(*cmd, timeout=120):
        seen.append(cmd)
        return 1, ""
    monkeypatch.setattr(U, "_run", run)
    monkeypatch.setattr(U.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.delenv("DLM_UPDATES_GIT", raising=False)
    monkeypatch.setattr(G, "_token", lambda: "ghp_secret")
    errors = []
    assert asyncio.run(U._check_git(errors)) == [] and errors == ["git fetch alınmadı"]
    fetch = seen[0]
    assert "fetch" in fetch and "-c" in fetch and any("extraheader" in a for a in fetch)
    monkeypatch.setattr(G, "_token", lambda: "")
    seen.clear()
    asyncio.run(U._check_git([]))
    assert "-c" not in seen[0]                                                  # token yoxdursa əvvəlki əmr
