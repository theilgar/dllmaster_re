"""
🩺 Vahid Sistem Diaqnostikası və Metriklər — common/diagnostics.py

Bu modul həm 🤖 Bot (/htop, /menu, /server), həm də 📱 Userbot (.ff, .speedtest, .ping)
üçün vahid sistem resursları, internet sürəti testi və fastfetch generasiyasını təmin edir.

Xüsusiyyətlər:
  - server_snapshot(): CPU, RAM, Disk, Load average, Uptime, Bot RSS (2-3 saniyəlik keşləmə ilə).
  - run_speedtest(): Ookla CLI / speedtest-cli / Cloudflare vasitəsilə vahid formatda internet sürəti.
  - run_fastfetch(): fastfetch çıxarışı (logo-suz, boru rejimində) və ya təmiz sistem xülasəsi.
  - run_cmd(): alt-proseslərin təhlükəsiz və vaxt məhdudiyyətli (timeout) icrası.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pwd
import shutil
import time
from typing import Any, Dict, Tuple

from common.utilities import BASE_DIR, format_bytes, read_proc_rss, read_sys_meminfo, read_sys_uptime, run_exec

logger = logging.getLogger(__name__)

_BOT_STARTED = time.time()
_SERVER_SNAP_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}
_SPEEDTEST_LOCK = asyncio.Lock()
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


async def run_cmd(*cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Alt prosesi təhlükəsiz başladır və çıxışı qaytarır."""
    return await run_exec(*cmd, timeout=timeout)


# ───────────────────────── /proc oxuyucusu ─────────────────────────
def cpu_times() -> list:
    """/proc/stat-ın "cpu" sətirləri: [(idle, total), ...] — [0] ümumi, qalanı nüvələr. Oxunmasa []."""
    out = []
    try:
        with open("/proc/stat") as f:
            for line in f:
                if not line.startswith("cpu"):
                    break
                vals = [int(x) for x in line.split()[1:]]
                out.append((vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)))   # idle + iowait
    except (OSError, ValueError, IndexError):
        pass
    return out


def cpu_percent(prev: list, cur: list) -> list:
    """İki cpu_times() ölçüsü arasındakı yük %: [ümumi, nüvə1, ...] (sətirlər indeks üzrə tutuşdurulur)."""
    return [100.0 * (1 - (i1 - i0) / (t1 - t0)) if t1 > t0 else 0.0
            for (i0, t0), (i1, t1) in zip(prev, cur)]


class ProcSampler:
    """psutil olmadan: iki ölçü arasındakı fərqdən CPU% hesablayır."""

    def __init__(self):
        self.prev_cpu = None
        self.prev_procs = {}
        self.prev_time = None
        self.users = {}

    def _user(self, uid: int) -> str:
        if uid not in self.users:
            try:
                self.users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                self.users[uid] = str(uid)
        return self.users[uid]

    def _procs(self):
        procs = {}
        try:
            pids = os.listdir("/proc")
        except OSError:
            pids = []
        for pid in pids:
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/stat") as f:
                    stat = f.read()
                rpar = stat.rfind(")")
                comm = stat[stat.find("(") + 1:rpar]
                fields = stat[rpar + 2:].split()
                ticks = int(fields[11]) + int(fields[12])        # utime + stime
                rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
                uid = os.stat(f"/proc/{pid}").st_uid
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\0", b" ").decode(errors="ignore").strip()
                except OSError:
                    cmd = ""
                procs[int(pid)] = {"ticks": ticks, "rss": rss, "name": comm, "cmd": cmd or comm, "uid": uid}
            except (OSError, ValueError, IndexError):
                continue
        return procs

    def sample(self) -> dict:
        now = time.monotonic()
        cpu = cpu_times()
        procs = self._procs()
        result = {"cores": [], "total": 0.0, "procs": []}

        if self.prev_cpu and len(self.prev_cpu) == len(cpu):
            pct = cpu_percent(self.prev_cpu, cpu)
            result["total"], result["cores"] = pct[0], pct[1:]
        else:                                        # ilk ölçü (və ya nüvə sayı dəyişib)
            result["cores"] = [0.0] * max(0, len(cpu) - 1)

        elapsed = (now - self.prev_time) if self.prev_time else None
        for pid, p in procs.items():
            cpu_pct = 0.0
            if elapsed and pid in self.prev_procs:
                cpu_pct = (p["ticks"] - self.prev_procs[pid]["ticks"]) / CLK_TCK / elapsed * 100
            result["procs"].append({
                "pid": pid, "cpu": max(cpu_pct, 0.0), "rss": p["rss"],
                "name": p["name"], "cmd": p["cmd"], "user": self._user(p["uid"])
            })

        self.prev_cpu, self.prev_procs, self.prev_time = cpu, procs, now

        mem = read_sys_meminfo()
        result["mem_total"] = mem.get("MemTotal", 0)
        result["mem_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", mem.get("MemFree", 0))
        result["swap_total"] = mem.get("SwapTotal", 0)
        result["swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        try:
            with open("/proc/loadavg") as f:
                la = f.read().split()
            result["load"] = la[:3]
            result["tasks"] = la[3] if len(la) > 3 else ""
        except Exception as _e:
            logger.debug("udulan xəta [common/diagnostics.py:sample]: %r", _e)
        result["uptime"] = read_sys_uptime()
        return result


def server_snapshot() -> Dict[str, Any]:
    """Sistem resurslarının /proc-dan sürətli oxunuşu (2 saniyəlik keşlənmə ilə)."""
    now = time.monotonic()
    if now - _SERVER_SNAP_CACHE["ts"] < 2.0 and _SERVER_SNAP_CACHE["data"]:
        return dict(_SERVER_SNAP_CACHE["data"])

    snap: Dict[str, Any] = {}
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        snap.update(load=(load1, load5, load15), cores=cores, cpu=min(100.0, (load1 / cores) * 100))
    except (OSError, AttributeError):
        pass

    try:
        mem = read_sys_meminfo()
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        if total:
            snap.update(
                ram_total=total,
                ram_used=total - avail,
                ram=((total - avail) / total) * 100,
            )
        if mem.get("SwapTotal"):
            sw_total = mem["SwapTotal"]
            sw_free = mem.get("SwapFree", 0)
            snap.update(swap=((sw_total - sw_free) / sw_total) * 100)
    except (KeyError, ValueError, ZeroDivisionError):
        pass

    try:
        du = shutil.disk_usage("/")
        snap.update(disk_total=du.total, disk_used=du.used, disk=(du.used / du.total) * 100)
    except OSError:
        pass

    snap["uptime"] = read_sys_uptime()
    snap["bot_rss"] = read_proc_rss()
    snap["bot_uptime"] = time.time() - _BOT_STARTED

    _SERVER_SNAP_CACHE["ts"] = now
    _SERVER_SNAP_CACHE["data"] = snap
    return snap


async def run_speedtest() -> Dict[str, Any]:
    """Vahid internet sürət testi (Ookla CLI və ya speedtest-cli)."""
    async with _SPEEDTEST_LOCK:
        cli, ookla = shutil.which("speedtest-cli"), shutil.which("speedtest")
        if ookla:
            cmd, kind = [ookla, "--accept-license", "--accept-gdpr", "--format=json", "--progress=no"], "ookla"
        elif cli:
            cmd, kind = [cli, "--json"], "python"
        else:
            raise RuntimeError("speedtest və ya speedtest-cli quraşdırılmayıb.")

        rc, out, err = await run_cmd(*cmd, timeout=180)
        if rc != 0:
            msg = (err or out).strip()[-300:]
            raise RuntimeError(msg or f"Speedtest çıxış xətası: {rc}")

        start = out.find("{")
        data = json.loads(out[start:] if start >= 0 else out)
        if kind == "ookla":
            server = data.get("server", {})
            return {
                "down": data["download"]["bandwidth"] * 8 / 1e6,
                "up": data["upload"]["bandwidth"] * 8 / 1e6,
                "ping": data["ping"]["latency"],
                "server": f"{server.get('name', '?')} ({server.get('location', '')}, {server.get('country', '')})",
                "isp": data.get("isp", "?"),
            }

        server, client = data.get("server", {}), data.get("client", {})
        return {
            "down": data["download"] / 1e6,
            "up": data["upload"] / 1e6,
            "ping": data["ping"],
            "server": f"{server.get('sponsor', '?')} ({server.get('name', '')}, {server.get('country', '')})",
            "isp": client.get("isp", "?"),
        }


async def run_fastfetch() -> str:
    """Sistem məlumatını fastfetch vasitəsilə qaytarır (boruda, rəngsiz)."""
    ff = shutil.which("fastfetch")
    if not ff:
        snap = server_snapshot()
        return (
            f"OS: Linux\n"
            f"Uptime: {int(snap.get('uptime', 0) // 3600)} saat\n"
            f"CPU: {snap.get('cpu', 0):.1f}%\n"
            f"RAM: {snap.get('ram', 0):.1f}%\n"
            f"Disk: {snap.get('disk', 0):.1f}%\n"
        )
    rc, out, _ = await run_cmd(ff, "--logo", "none", "--pipe", timeout=30)
    return out.strip() if rc == 0 else "fastfetch xətası"


_STORAGE_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def project_storage_snapshot(force: bool = False) -> Dict[str, Any]:
    """Layihənin bütün disk sahəsi, bölmələr, pluginlər və RAM istifadəsini hesablayır."""
    now = time.monotonic()
    if not force and _STORAGE_CACHE["data"] is not None and (now - _STORAGE_CACHE["ts"] < 15.0):
        return dict(_STORAGE_CACHE["data"])

    from pathlib import Path

    root = Path(BASE_DIR).resolve()

    def _dir_stats(rel_path: str, exclude_dirs: tuple = ()):
        target = root / rel_path
        if not target.exists():
            return 0, 0, []
        total_size = 0
        total_files = 0
        file_list = []
        for dirpath, dirnames, filenames in os.walk(target):
            if any(skip in dirpath for skip in ("__pycache__", ".git", ".venv")):
                continue
            rpath = os.path.relpath(dirpath, root)
            if any(rpath == exc or rpath.startswith(exc + os.sep) for exc in exclude_dirs):
                continue
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    sz = os.path.getsize(fp)
                    total_size += sz
                    total_files += 1
                    file_list.append((f, sz, os.path.relpath(fp, root)))
                except OSError:
                    pass
        return total_size, total_files, file_list

    data_sub_zones = ("data/records", "data/voice_cache", "data/backups", "data/yt_profiles")

    zones = [
        ("records",     "⏺ Səs Qeydləri",       "data/records", ()),
        ("voice_cache", "🎵 Yayım Səs Keşi",    "data/voice_cache", ()),
        ("backups",     "💾 Baza Yedəkləri",    "data/backups", ()),
        ("yt_profiles", "🌐 YT Brauzer Keşi",   "data/yt_profiles", ()),
        ("data",        "🗄 Əsas Baza & Data",  "data", data_sub_zones),
        ("download",    "⬇️ Yükləmələr & Keş", "download", ()),
        ("bot_plugins", "🔌 Bot Pluginləri",    "bot/plugins", ()),
        ("ub_plugins",  "📱 UB Pluginləri",     "userbot/plugins", ()),
        ("bot_core",    "⚙️ Bot Core",          "bot/core", ()),
        ("ub_core",     "📱 UB Core",           "userbot/core", ()),
        ("common",      "🧩 Ümumi (Common)",    "common", ()),
    ]

    categories = []
    all_plugins = []
    for key, label, rel_dir, exc in zones:
        sz, count, files = _dir_stats(rel_dir, exclude_dirs=exc)
        categories.append({
            "key": key,
            "label": label,
            "path": rel_dir,
            "size": sz,
            "size_str": format_bytes(sz),
            "count": count,
        })
        if key in ("bot_plugins", "ub_plugins"):
            prefix = "🔌" if key == "bot_plugins" else "📱"
            for fn, fsz, rpath in files:
                if fn.endswith(".py") and not fn.startswith("_"):
                    all_plugins.append({
                        "name": fn,
                        "size": fsz,
                        "size_str": format_bytes(fsz),
                        "type": prefix,
                        "path": rpath,
                    })

    all_plugins.sort(key=lambda x: x["size"], reverse=True)
    top_plugins = all_plugins[:8]

    total_proj_size = 0
    total_proj_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if any(skip in dirpath for skip in ("__pycache__", ".git", ".venv")):
            continue
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total_proj_size += os.path.getsize(fp)
                total_proj_files += 1
            except OSError:
                pass

    mem = read_sys_meminfo()
    ram_total = mem.get("MemTotal", 0)
    ram_avail = mem.get("MemAvailable", mem.get("MemFree", 0))
    ram_used = ram_total - ram_avail if ram_total else 0
    du = shutil.disk_usage(root)

    res = {
        "total_size": total_proj_size,
        "total_size_str": format_bytes(total_proj_size),
        "total_files": total_proj_files,
        "categories": categories,
        "top_plugins": top_plugins,
        "proc_rss": read_proc_rss(),
        "proc_rss_str": format_bytes(read_proc_rss()),
        "ram_total": ram_total,
        "ram_used": ram_used,
        "ram_total_str": format_bytes(ram_total),
        "ram_used_str": format_bytes(ram_used),
        "ram_pct": (ram_used / ram_total * 100) if ram_total else 0,
        "disk_total_str": format_bytes(du.total),
        "disk_used_str": format_bytes(du.used),
        "disk_pct": (du.used / du.total * 100) if du.total else 0,
    }

    _STORAGE_CACHE["ts"] = now
    _STORAGE_CACHE["data"] = res
    return res

