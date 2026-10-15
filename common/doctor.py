"""
🩺 Asılılıq həkimi — common/doctor.py

Botun işləməsi üçün lazım olan HƏR ŞEYİ bir yerdə yoxlayır və vəziyyəti verir:
  • Python paketləri (requirements.txt)        — common/bootstrap.py
  • Sistem paketləri (deps/<pm>.txt)           — common/bootstrap.py
  • Xarici proqramlar (ffmpeg, git, JS runtime, fastfetch, checkupdates, docker...)
  • Config dəyərləri (token, API ID/hash, Spotify, creator)         — config.env
  • İstəyə bağlı xidmətlər (PO token provider, sudo NOPASSWD)

Hər yoxlama bir Check verir: ok | warn | fail + necə düzəltmək (Arch / Debian əmri).
İstifadəsi:
  • başlanğıcda bir dəfə loga yazılır (init.py) — fail varsa dərhal görünür
  • /menu → 🖥 Sistem → 🩺 Sağlamlıq — canlı panel, 🔧 çatışmayanları quraşdır düyməsi ilə
Burada ağır import yoxdur; şəbəkə yoxlaması qısa timeout-ladır.
"""
from __future__ import annotations

import os
import logging
import shutil
import subprocess
from dataclasses import dataclass, field

from common.bootstrap import ROOT, detect_pm, missing_python, missing_system, read_system_packages
from common.bootstrap import port_open as _port_open

logger = logging.getLogger(__name__)



@dataclass
class Check:
    key: str
    title: str
    status: str                 # ok | warn | fail
    detail: str = ""
    fix_pacman: str = ""        # terminal əmri (Arch)
    fix_apt: str = ""           # terminal əmri (Debian)
    fix_pip: list = field(default_factory=list)   # pip paket adları

    @property
    def icon(self) -> str:
        return {"ok": "✅", "warn": "⚠️", "fail": "❌"}[self.status]

    def fix_cmd(self, pm: str | None) -> str:
        if self.fix_pip:
            return ""           # pip fix-i doctor özü edir (requirements)
        return self.fix_apt if pm == "apt" else self.fix_pacman


# ───────────────────────── ayrı-ayrı yoxlamalar ─────────────────────────
def _which_check(key, title, names, pacman, apt, critical) -> Check:
    found = next((n for n in names if shutil.which(n)), None)
    if found:
        return Check(key, title, "ok", found)
    return Check(key, title, "fail" if critical else "warn",
                 "tapılmadı", fix_pacman=pacman, fix_apt=apt)


def check_python() -> Check:
    miss = missing_python()
    if not miss:
        return Check("pip", "Python paketləri", "ok", "requirements.txt tam")
    names = [m.split()[0] for m in miss]
    return Check("pip", "Python paketləri", "fail", f"{len(miss)} çatışmır: " + ", ".join(names[:6]),
                 fix_pip=names)


def check_system() -> list:
    pm = detect_pm()
    if pm is None:
        return [Check("sys", "Sistem paketləri", "warn", "pacman/apt tapılmadı — əl ilə yoxla")]
    req, opt = read_system_packages(pm)
    out = []
    miss_req = missing_system(pm, req)
    if miss_req:
        cmd = ("sudo pacman -S --needed --noconfirm " if pm == "pacman" else "sudo apt install -y ") + " ".join(miss_req)
        out.append(Check("sys", "Sistem paketləri (vacib)", "fail", ", ".join(miss_req),
                         fix_pacman=cmd, fix_apt=cmd))
    else:
        out.append(Check("sys", "Sistem paketləri (vacib)", "ok", "hamısı var"))
    miss_opt = missing_system(pm, opt)
    if miss_opt:
        cmd = ("sudo pacman -S --needed --noconfirm " if pm == "pacman" else "sudo apt install -y ") + " ".join(miss_opt)
        out.append(Check("sysopt", "Sistem paketləri (könüllü)", "warn", ", ".join(miss_opt),
                         fix_pacman=cmd, fix_apt=cmd))
    return out


def check_ffmpeg() -> Check:
    c = _which_check("ffmpeg", "ffmpeg", ["ffmpeg"], "sudo pacman -S --noconfirm ffmpeg",
                     "sudo apt install -y ffmpeg", critical=True)
    if c.status == "ok" and not shutil.which("ffprobe"):
        c.status, c.detail = "warn", "ffmpeg var, ffprobe yoxdur"
    return c


def check_js_runtime() -> Check:
    found = next((n for n in ("deno", "node", "bun") if shutil.which(n)), None)
    if not found:
        return Check("js", "JS runtime (YouTube)", "fail",
                     "deno/node/bun yoxdur — YouTube yükləməsi işləməyəcək",
                     fix_pacman="sudo pacman -S --noconfirm deno",
                     fix_apt="sudo apt install -y nodejs  # və ya deno")
    if found == "node":
        try:
            ver = subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=5).stdout.strip()
            major = int(ver.lstrip("v").split(".")[0])
            if major < 20:
                return Check("js", "JS runtime (YouTube)", "warn",
                             f"node {ver} — 20+ tövsiyə olunur (deno daha etibarlıdır)",
                             fix_pacman="sudo pacman -S --noconfirm deno")
        except Exception as _e:
            logger.debug("udulan xəta [common/doctor.py:111]: %r", _e)
    return Check("js", "JS runtime (YouTube)", "ok", found)


def check_pot_provider() -> Check:
    port = int(os.getenv("BGUTIL_POT_PORT", "4416"))
    if _port_open("127.0.0.1", port):
        return Check("pot", "PO token provider", "ok", f"127.0.0.1:{port} aktiv")
    return Check("pot", "PO token provider", "warn",
                 "yoxdur — bəzi YouTube formatları 403 verə bilər (mahnı yenə yüklənir)",
                 fix_pacman="pip install bgutil-ytdlp-pot-provider  # + docker brainicism/bgutil-ytdlp-pot-provider")


def check_config() -> list:
    out = []

    def cfg(key, title, env, level="fail", hint=""):
        val = os.getenv(env)
        out.append(Check(key, title, "ok" if val else level, "təyin edilib" if val else (hint or "config.env-də yoxdur")))

    cfg("token", "Bot tokeni (TELEGRAM_TOKEN)", "TELEGRAM_TOKEN")
    cfg("creator", "Creator ID", "CREATOR_ID", "warn", "CREATOR_ID yoxdur — admin əmrləri işləməz")
    has_sp = os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET")
    out.append(Check("spotify", "Spotify açarları", "ok" if has_sp else "warn",
                     "təyin edilib" if has_sp else "yoxdur — Spotify linkləri / metadata işləməz"))
    has_tele = os.getenv("TELETHON_API_ID") and os.getenv("TELETHON_API_HASH")
    out.append(Check("telethon", "Telethon API (userbot)", "ok" if has_tele else "warn",
                     "təyin edilib" if has_tele else "yoxdur — userbot / depo / canlı yayım işləməz"))
    return out


def check_optional_tools() -> list:
    return [
        _which_check("git", "git", ["git"], "sudo pacman -S --noconfirm git",
                     "sudo apt install -y git", critical=False),
        _which_check("checkupdates", "checkupdates (sistem yeniləmə yoxlaması)", ["checkupdates", "apt"],
                     "sudo pacman -S --noconfirm pacman-contrib", "", critical=False),
        _which_check("fastfetch", "fastfetch (.ff / sistem kartı)", ["fastfetch", "neofetch"],
                     "sudo pacman -S --noconfirm fastfetch", "sudo apt install -y fastfetch", critical=False),
    ]


# ───────────────────────── ümumi ─────────────────────────
def run_all() -> list:
    checks: list = []
    checks += check_config()
    checks.append(check_python())
    checks += check_system()
    checks.append(check_ffmpeg())
    checks.append(check_js_runtime())
    checks += check_optional_tools()
    checks.append(check_pot_provider())
    order = {"fail": 0, "warn": 1, "ok": 2}
    checks.sort(key=lambda c: (order[c.status], c.key))
    return checks


def summary(checks: list) -> tuple:
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    return fails, warns


def worst(checks: list) -> str:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"


def log_report(checks: list, logger) -> None:
    fails, warns = summary(checks)
    for c in checks:
        line = f"🩺 {c.icon} {c.title}: {c.detail}"
        if c.status == "fail":
            logger.error(line + (f"  →  {c.fix_pacman or c.fix_apt}" if (c.fix_pacman or c.fix_apt) else ""))
        elif c.status == "warn":
            logger.warning(line)
        else:
            logger.info(line)
    if fails:
        logger.error(f"🩺 {fails} ciddi problem — bəzi funksiyalar işləməyə bilər. /menu → 🖥 Sistem → 🩺 Sağlamlıq")
    elif warns:
        logger.warning(f"🩺 {warns} xəbərdarlıq — əsas funksiyalar işləyir.")
    else:
        logger.info("🩺 Bütün yoxlamalar qaydasındadır ✓")


async def install_missing() -> str:
    """Çatışan pip + sistem paketlərini quraşdırır (🔧 düyməsi). Nəticə mətni qaytarır."""
    import sys

    msgs = []
    pm = detect_pm()

    sys_checks = check_system()
    for c in sys_checks:
        cmd = c.fix_cmd(pm)
        if c.status == "fail" and cmd:
            rc = await _run_shell(cmd)
            msgs.append(("✅" if rc == 0 else "❌") + f" sistem: {c.detail}")

    pyc = check_python()
    if pyc.status == "fail":
        req = str(ROOT / "requirements.txt")
        import shlex                                   # yolda boşluq ola bilər ("Новый том")
        rc = await _run_shell(f"{shlex.quote(sys.executable)} -m pip install -r {shlex.quote(req)}")
        msgs.append(("✅" if rc == 0 else "❌") + f" pip: {len(pyc.fix_pip)} paket")

    return "\n".join(msgs) if msgs else "ℹ️ Avtomatik quraşdırılacaq vacib paket yoxdur (könüllüləri əl ilə)."


async def _run_shell(cmd: str, timeout: int = 900) -> int:
    import asyncio
    proc = await asyncio.create_subprocess_shell(cmd, stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return 124
