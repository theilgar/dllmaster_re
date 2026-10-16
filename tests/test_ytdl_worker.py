"""Serverdə işləyən ytdl_worker.py köməkçiləri: səsin itkisiz çıxarılması, Firefox girişinin aşkarlanması, provayder ping-i."""
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from bot.core import ytdl_worker as w

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def make_mp4(path, audio="aac"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", audio, str(path)], check=True)


def codec(path, kind="a"):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries", "stream=codec_name",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return out


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg yoxdur")
def test_extract_aac_copies_the_audio_without_reencoding_and_drops_the_video(tmp_path):
    mp4 = tmp_path / "x.mp4"
    make_mp4(mp4)
    out = w.extract_aac(str(mp4), "mp4a.40.2", "avc1.42001E")
    assert out == str(tmp_path / "x.m4a") and os.path.exists(out) and not mp4.exists()    # video fayl silindi
    assert codec(out) == "aac" and codec(out, "v") == ""                                   # səs qaldı, video yoxdur


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg yoxdur")
def test_extract_aac_leaves_everything_else_alone(tmp_path, monkeypatch):
    mp4 = tmp_path / "y.mp4"
    make_mp4(mp4)
    assert w.extract_aac(str(mp4), "opus", "avc1") == str(mp4)                             # səs AAC deyil: yenidən kodlama olardı
    assert w.extract_aac(str(mp4), "mp4a.40.2", "none") == str(mp4)                        # artıq yalnız səsdir
    assert w.extract_aac(str(mp4), None, "avc1") == str(mp4)
    assert w.extract_aac(str(tmp_path / "a.m4a"), "mp4a.40.2", "avc1") == str(tmp_path / "a.m4a")
    assert w.extract_aac(str(tmp_path / "a.webm"), "mp4a.40.2", "vp9") == str(tmp_path / "a.webm")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)                       # ffmpeg yoxdur
    assert w.extract_aac(str(mp4), "mp4a.40.2", "avc1") == str(mp4) and mp4.exists()
    monkeypatch.undo()
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert w.extract_aac(str(broken), "mp4a.40.2", "avc1") == str(broken)                  # ffmpeg xətası: orijinal qalır
    assert broken.exists() and not (tmp_path / "broken.m4a").exists()                      # yarımçıq .m4a qalmır


def firefox_profile(home, name="abc.default", cookies=()):
    d = home / ".mozilla" / "firefox" / name
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "cookies.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table moz_cookies (host text, name text, value text)")
    con.executemany("insert into moz_cookies values (?,?,?)", cookies)
    con.commit()
    return con                                                           # açıq qalır: Firefox işləyərkən yazılar -wal faylındadır


def test_firefox_login_detection_reads_names_only_and_sees_unflushed_wal_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert w._firefox_login() is None                                    # profil yoxdur
    con = firefox_profile(tmp_path, cookies=[(".youtube.com", "PREF", "x"), (".youtube.com", "VISITOR_INFO1_LIVE", "x")])
    assert w._firefox_login() is None                                    # giriş yox: yalnız adi youtube cookies-i
    con.executemany("insert into moz_cookies values (?,?,?)", [(".google.com", "SID", "gizli1"), (".google.com", "__Secure-1PSID", "gizli2")])
    con.commit()
    assert os.path.exists(tmp_path / ".mozilla/firefox/abc.default/cookies.sqlite-wal")        # hələ ana fayla yazılmayıb
    assert w._firefox_login() == "abc.default"                           # amma -wal ilə birlikdə kopyalandığı üçün görünür
    con.close()


def test_firefox_login_needs_two_login_cookies_and_ignores_unrelated_sites(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    firefox_profile(tmp_path, "one.default", [(".google.com", "SID", "x")]).close()                       # tək cookie: kifayət deyil
    firefox_profile(tmp_path, "two.default", [(".example.com", "SID", "x"), (".example.com", "LOGIN_INFO", "x")]).close()
    assert w._firefox_login() is None                                    # başqa sayt
    firefox_profile(tmp_path, "three.default", [(".youtube.com", "LOGIN_INFO", "x"), ("accounts.google.com", "SID", "x")]).close()
    assert w._firefox_login() == "three.default"


def test_pot_version_ping(monkeypatch):
    import urllib.request

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"server_uptime": 5, "version": "2.0.1"}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: Resp())
    assert w._pot_version() == "2.0.1"

    def boom(url, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert w._pot_version() is None


def test_diag_reports_every_piece_youtube_needs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bin").mkdir()
    deno = tmp_path / "bin" / "deno"
    deno.write_text("#!/bin/sh\necho deno\n")
    deno.chmod(0o755)
    monkeypatch.setattr(w, "_pot_version", lambda: "2.0.1")
    info = w.diag()
    assert info["deno"] is True and "deno" in info["js"] and info["pot"] == "2.0.1" and info["firefox_login"] is None
    assert info["ram_mb"] and info["free_mb"] is not None and info["python"]
    deno.unlink()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))                  # Deno nə dlm_remote/bin-də, nə PATH-də
    assert w.diag()["deno"] is False
