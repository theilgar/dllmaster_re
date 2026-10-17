"""bot/core/webprofile.py — brauzer profillərinin aşkarlanması: Local State olmayan qovluq xəta sayılmır."""
import json
import logging

from bot.core import webprofile as wp


def _chromium_root(tmp_path, with_state=True, state=None):
    root = tmp_path / "google-chrome"
    (root / "Default" / "Network").mkdir(parents=True)
    (root / "Default" / "Network" / "Cookies").write_bytes(b"x")
    if with_state:
        (root / "Local State").write_text(state if state is not None else json.dumps(
            {"profile": {"info_cache": {"Default": {"name": "Şəxsi"}}}}), encoding="utf-8")
    return str(root)


def test_profile_names_come_from_local_state(tmp_path):
    out = wp._chromium_profiles(_chromium_root(tmp_path))
    assert [p["name"] for p in out] == ["Şəxsi (Default)"]


def test_a_root_without_local_state_is_not_an_error_and_logs_nothing(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="bot.core.webprofile"):
        out = wp._chromium_profiles(_chromium_root(tmp_path, with_state=False))
    assert [p["name"] for p in out] == ["Default"]
    assert not [r for r in caplog.records if "No such file" in r.getMessage() or "udulan" in r.getMessage()]


def test_an_empty_flatpak_style_root_gives_no_profiles_quietly(tmp_path, caplog):
    root = tmp_path / "flatpak-chrome"
    root.mkdir()
    with caplog.at_level(logging.DEBUG, logger="bot.core.webprofile"):
        assert wp._chromium_profiles(str(root)) == []
    assert not caplog.records


def test_a_broken_local_state_is_logged_but_does_not_break_detection(tmp_path, caplog):
    root = _chromium_root(tmp_path, state="{ bu JSON deyil")
    with caplog.at_level(logging.DEBUG, logger="bot.core.webprofile"):
        out = wp._chromium_profiles(root)
    assert [p["name"] for p in out] == ["Default"]
    assert any("Local State" in r.getMessage() for r in caplog.records)
