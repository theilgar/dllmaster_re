"""Bot bazasının bərpası: extension (pg_trgm) sahibi başqa istifadəçidirsə `DROP EXTENSION` bərpanı kəsməməlidir (VDS-ə köçürmə)."""
import os
import subprocess
import types

import pytest

import common.backup as b

TOC = """;
; Archive created at 2026-10-18 00:06:10 +04
;     dbname: dlmaster
; Selected TOC Entries:
;
2; 3079 22737 EXTENSION - pg_trgm
3730; 0 0 COMMENT - EXTENSION pg_trgm
218; 1259 22781 TABLE public audio_cache dlm
219; 1259 22790 TABLE public extension_notes dlm
5011; 1259 22900 INDEX public idx_audio_title_trgm dlm
"""


def test_only_extension_entries_are_dropped_from_the_restore_list():
    lines = TOC.splitlines()
    dropped = [ln for ln in lines if b._EXT_TOC.match(ln)]
    assert dropped == ["2; 3079 22737 EXTENSION - pg_trgm", "3730; 0 0 COMMENT - EXTENSION pg_trgm"]
    assert not b._EXT_TOC.match("219; 1259 22790 TABLE public extension_notes dlm")        # adında «extension» olan cədvəl qalır
    assert not b._EXT_TOC.match("; Selected TOC Entries: EXTENSION")                       # şərh sətirləri qalır


def fake_run(stdout="", rc=0, raises=None):
    def run(cmd, **kw):
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    return run


def test_restore_list_file_keeps_everything_else(monkeypatch):
    monkeypatch.setattr(b, "_pg_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(b.subprocess, "run", fake_run(TOC))
    path = b.restore_list("/x/bot.dump", {})
    try:
        kept = open(path, encoding="utf-8").read().splitlines()
    finally:
        os.unlink(path)
    assert not any("EXTENSION" in ln for ln in kept if not ln.startswith(";")) and any("TABLE public audio_cache" in ln for ln in kept)
    assert any("extension_notes" in ln for ln in kept) and any(ln.startswith("; Archive created") for ln in kept)


@pytest.mark.parametrize("kw", [{"rc": 1, "stdout": "xəta"}, {"stdout": "  "}, {"raises": OSError("yoxdur")},
                                {"raises": subprocess.TimeoutExpired("pg_restore", 1)}])
def test_restore_list_failure_falls_back_to_a_plain_restore(monkeypatch, kw):
    monkeypatch.setattr(b, "_pg_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(b.subprocess, "run", fake_run(**kw))
    assert b.restore_list("/x/bot.dump", {}) is None


class Conn:
    dsn = "postgresql://dlm:parol@localhost:5432/dlmaster"

    def __init__(self):
        self.reloaded = 0

    def reload_caches(self):
        self.reloaded += 1


def test_pg_restore_uses_the_filtered_list_and_ensures_the_extension(monkeypatch, tmp_path):
    calls = []

    def run(cmd, env, timeout=1800):
        calls.append(cmd)
        return None
    monkeypatch.setattr(b, "_pg_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(b, "_run", run)
    monkeypatch.setattr(b, "snapshot", lambda *a, **kw: "pre-snapshot")
    monkeypatch.setattr(b.subprocess, "run", fake_run(TOC))
    conn = Conn()
    pre = b.restore(b.PG_KIND, conn, None, str(tmp_path / "bot.dump"))
    assert pre == "pre-snapshot" and conn.reloaded == 1
    ensure, restore = calls
    assert ensure[0].endswith("psql") and "CREATE EXTENSION IF NOT EXISTS pg_trgm" in ensure
    assert restore[0].endswith("pg_restore") and "--clean" in restore and "--single-transaction" in restore
    toc = restore[restore.index("-L") + 1]
    assert toc.endswith(".list") and restore[-1].endswith("bot.dump")
    assert not os.path.exists(toc)                                              # müvəqqəti siyahı silinib


def test_restore_still_works_when_the_list_cannot_be_built_and_cleans_up_on_failure(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(b, "_pg_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(b, "snapshot", lambda *a, **kw: "pre")
    monkeypatch.setattr(b.subprocess, "run", fake_run(rc=1))
    monkeypatch.setattr(b, "_run", lambda cmd, env, timeout=1800: cmds.append(cmd))
    b.restore(b.PG_KIND, Conn(), None, str(tmp_path / "bot.dump"))
    assert "-L" not in cmds[-1]                                                 # siyahı yoxdur → əvvəlki kimi

    created = []
    monkeypatch.setattr(b.subprocess, "run", fake_run(TOC))
    real = b.restore_list
    monkeypatch.setattr(b, "restore_list", lambda *a, **kw: (created.append(real(*a, **kw)) or created[-1]))

    def failing(cmd, env, timeout=1800):
        if "pg_restore" in cmd[0]:
            raise RuntimeError("pg_restore xətası: başqa səbəb")
    monkeypatch.setattr(b, "_run", failing)
    with pytest.raises(RuntimeError, match="başqa səbəb"):
        b.restore(b.PG_KIND, Conn(), None, str(tmp_path / "bot.dump"))
    assert created and not os.path.exists(created[-1])                          # xəta olsa da temp fayl silinir


def test_ensure_extension_never_raises(monkeypatch):
    monkeypatch.setattr(b, "_pg_bin", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(b, "_run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("permission denied")))
    b.ensure_extension({})
