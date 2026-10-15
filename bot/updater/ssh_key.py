"""
🔐 bot/updater/ssh_key.py - GitHub üçün SSH açarı: botda yarat, public hissəni GitHub-a əlavə et, bağlantını yoxla.

Private açar yalnız serverdə (data/github/, icazə 600) qalır və HEÇ VAXT Telegram-a göndərilmir;
göstərilən yalnız public hissədir (paylaşmaq təhlükəsizdir). git əmrləri bu açarı GIT_SSH_COMMAND ilə işlədir.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from common.utilities import BASE_DIR

logger = logging.getLogger(__name__)

KEY_DIR = Path(BASE_DIR) / "data" / "github"
KEY = KEY_DIR / "id_ed25519"
PUB = KEY_DIR / "id_ed25519.pub"
KNOWN_HOSTS = KEY_DIR / "known_hosts"
GITHUB_NEW_KEY_URL = "https://github.com/settings/ssh/new"
GITHUB_NEW_TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"


def available() -> bool:
    return bool(shutil.which("ssh-keygen") and shutil.which("ssh"))


def exists() -> bool:
    return KEY.is_file() and PUB.is_file()


def public_key() -> str:
    try:
        return PUB.read_text(encoding="utf-8").strip() if PUB.is_file() else ""
    except OSError:
        return ""


def fingerprint() -> str:
    if not PUB.is_file() or not shutil.which("ssh-keygen"):
        return ""
    try:
        r = subprocess.run(["ssh-keygen", "-lf", str(PUB)], capture_output=True, text=True, timeout=10)
        return r.stdout.split()[1] if r.returncode == 0 and len(r.stdout.split()) > 1 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def generate(comment: str = "dllmaster-bot") -> tuple[bool, str]:
    """Yeni ed25519 açar cütü yaradır (köhnəsi varsa əvəz olunur). → (uğurlu, mesaj)"""
    if not available():
        return False, "ssh-keygen / ssh tapılmadı (openssh quraşdırın: pacman -S openssh · apt install openssh-client)"
    try:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        for f in (KEY, PUB):
            f.unlink(missing_ok=True)
        r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(KEY)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or not exists():
            return False, (r.stderr or r.stdout or "ssh-keygen alınmadı").strip()[-300:]
        os.chmod(KEY, 0o600)
        os.chmod(PUB, 0o644)
        return True, "açar yaradıldı"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)[:300]


def delete() -> bool:
    ok = False
    for f in (KEY, PUB, KNOWN_HOSTS):
        try:
            if f.exists():
                f.unlink()
                ok = True
        except OSError as e:
            logger.warning(f"SSH faylı silinmədi ({f.name}): {e}")
    return ok


def _ssh_args() -> list[str]:
    return ["ssh", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}"]


def env() -> dict:
    """git üçün əlavə mühit: açar varsa GIT_SSH_COMMAND (https remote-lara təsiri yoxdur)."""
    return {"GIT_SSH_COMMAND": shlex.join(_ssh_args())} if exists() else {}


def test_connection(timeout: int = 20) -> tuple[bool, str]:
    """`ssh -T git@github.com` — GitHub uğurlu girişdə də 1 qaytarır, ona görə mətnə baxılır."""
    if not exists():
        return False, "SSH açarı yoxdur — əvvəlcə 🔑 yarat"
    try:
        r = subprocess.run(_ssh_args() + ["-T", "git@github.com"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "vaxt bitdi — şəbəkəni / firewall-u (port 22) yoxlayın"
    except OSError as e:
        return False, str(e)[:200]
    out = f"{r.stdout}\n{r.stderr}".strip()
    m = re.search(r"Hi ([^!\s]+)!.*successfully authenticated", out)
    if m:
        return True, f"GitHub hesabı: {m.group(1)}"
    if "Permission denied" in out:
        return False, "GitHub bu açarı tanımır — public açarı GitHub-da əlavə etdiniz?"
    return False, (out.splitlines()[-1] if out else "naməlum xəta")[:200]


def to_ssh_url(url: str) -> str | None:
    """https://github.com/sahib/repo(.git) → git@github.com:sahib/repo.git (GitHub olmayan / artıq ssh — None)."""
    m = re.match(r"^https://(?:[^@/]+@)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", (url or "").strip())
    return f"git@github.com:{m.group(1)}/{m.group(2)}.git" if m else None
