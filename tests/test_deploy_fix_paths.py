"""deploy/fix_paths.py — köçürmədən sonra bazadakı mütləq yolların düzəldilməsi."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fix_paths", ROOT / "deploy" / "fix_paths.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

OLD = "/run/media/user/Новый том/dllmaster_restructured"
NEW = "/opt/dllmaster"


def accounts(base):
    return json.dumps([{"id": "a1", "kind": "browser", "profile": f"{base}/data/yt_profiles/a1", "managed": True},
                       {"id": "f1", "kind": "file", "file": f"{base}/data/yt_accounts/f1.txt"}], ensure_ascii=False)


def test_rewrites_every_path_in_the_accounts_json():
    fixed, n = fp.rewrite_text(accounts(OLD), OLD, NEW)
    data = json.loads(fixed)
    assert n == 2 and data[0]["profile"] == "/opt/dllmaster/data/yt_profiles/a1" and data[1]["file"] == "/opt/dllmaster/data/yt_accounts/f1.txt"
    assert OLD not in fixed


def test_handles_json_escaped_cyrillic_and_escaped_slashes():
    escaped = json.dumps([{"profile": f"{OLD}/data/yt_profiles/a1"}])                       # ensure_ascii=True → \uXXXX
    assert "\\u" in escaped
    fixed, n = fp.rewrite_text(escaped, OLD, NEW)
    assert n == 1 and json.loads(fixed)[0]["profile"] == "/opt/dllmaster/data/yt_profiles/a1"
    slashes = escaped.replace("/", "\\/")
    assert json.loads(fp.rewrite_text(slashes, OLD, NEW)[0])[0]["profile"] == "/opt/dllmaster/data/yt_profiles/a1"


def test_only_whole_path_components_change_and_rerun_is_a_noop():
    other = f"{OLD}_backup/data"                                                           # eyni prefiks, başqa qovluq
    assert fp.rewrite_text(other, OLD, NEW) == (other, 0)
    assert fp.rewrite_text(OLD, OLD, NEW) == (NEW, 1)                                      # dəyər məhz kök qovluqdur
    once, _ = fp.rewrite_text(accounts(OLD), OLD, NEW)
    assert fp.rewrite_text(once, OLD, NEW) == (once, 0)
    assert fp.rewrite_text("/home/user/.ssh/dlm_vps", OLD, NEW)[1] == 0                    # başqa yollara toxunulmur
    assert fp.rewrite_text(accounts(OLD), OLD + "/", NEW + "/")[0] == fp.rewrite_text(accounts(OLD), OLD, NEW)[0]


def test_plan_lists_only_changed_settings():
    settings = {"yt:accounts": accounts(OLD), "remote:key": "/home/user/.ssh/dlm_vps", "web:cookies": "browser", "n": 5}
    assert set(fp.plan(settings, OLD, NEW)) == {"yt:accounts"}
    assert fp.plan({"yt:accounts": accounts(NEW)}, OLD, NEW) == {}


def test_vds_mode_disables_the_home_browser_account_and_remote_offload():
    settings = {"yt:acc_main": json.dumps({"name": "Əsas hesab", "roles": ["depo", "user"], "on": True}, ensure_ascii=False),
                "remote:on": "1"}
    changes = fp.plan(settings, OLD, NEW, vds=True)
    main = json.loads(changes["yt:acc_main"])
    assert main["on"] is False and main["roles"] == ["depo", "user"] and main["name"] == "Əsas hesab"
    assert changes["remote:on"] == "0"
    assert fp.plan({"remote:on": "0", "yt:acc_main": changes["yt:acc_main"]}, OLD, NEW, vds=True) == {}      # təkrar — dəyişiklik yox
    assert fp.plan({}, OLD, NEW, vds=True)["yt:acc_main"]                                                   # əsas hesab qeydi yoxdursa da yaradılır


@pytest.mark.parametrize("script", ["pack_home.sh", "restore_vds.sh"])
def test_shell_scripts_are_syntactically_valid(script):
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / script)]).returncode == 0


def test_restore_dry_run_changes_nothing(tmp_path):
    bundle = tmp_path / "paket.tar.gz"
    bundle.write_bytes(b"")
    target = tmp_path / "hedef"
    res = subprocess.run(["bash", str(ROOT / "deploy" / "restore_vds.sh"), str(bundle), str(target), "--dry-run"],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0 and not target.exists()
    for step in ("tar -xzf", "install.sh --yes --service --dry-run", "psql", "fix_paths.py", "chmod 600 config.env"):
        assert step in res.stdout, step
    assert subprocess.run(["bash", str(ROOT / "deploy" / "restore_vds.sh")], capture_output=True, text=True).returncode == 1


def test_pack_dry_run_lists_what_goes_into_the_bundle_and_skips_the_heavy_caches():
    res = subprocess.run(["bash", str(ROOT / "deploy" / "pack_home.sh"), "--dry-run"], capture_output=True, text=True, timeout=120, cwd=ROOT)
    out = res.stdout
    assert res.returncode == 0 and "db.sql.gz" in out and "MANIFEST.txt" in out and "heç nə yazılmadı" in out
    assert "config.env" in out and "data/yt_profiles" in out


def test_runtime_caches_and_cookie_exports_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for path in ("data/antidelete_media/", "data/yt_profiles/", "data/yt_accounts/", "data/remote_cookies/", "dlm_backup*"):
        assert path in ignore, path
