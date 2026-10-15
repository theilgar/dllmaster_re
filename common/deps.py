"""
📦 Python asılılıqları — common/deps.py

Botun BÜTÜN kodunu (common/, bot/, userbot/, giriş faylları) oxuyub hansı xarici paketlərin
import edildiyini tapır, hər birinin vəziyyətini verir və pip ilə quraşdırır / yeniləyir.

  scan()            — import → pip paketi xəritəsi, hansı fayllarda, VACİB yoxsa İSTƏYƏ BAĞLI
                      (faylın əvvəlində, try-sız import → vacib; try / funksiya içində → istəyə bağlı)
  packages()        — scan + quraşdırılmış versiya
  outdated()        — `pip list --outdated` (şəbəkə; nəticə 6 saat yadda saxlanır)
  pip_install()     — asinxron pip (bot donmur), nəticənin sonu qaytarılır
  write_requirements() — requirements.txt-i koda uyğunlaşdırır (mövcud versiya şərtləri saxlanır)
  missing_required()   — başlanğıcda avtomatik quraşdırma üçün

Ayarlar (settings): deps:auto = off | ytdlp | all — gündəlik avtomatik yeniləmə.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
import time
from importlib import metadata, util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_FILE = ROOT / "requirements.txt"
SKIP_DIRS = {".venv", "venv", ".git", "data", "__pycache__", "download", "downloads", "node_modules"}

# import adı → pip paketi (fərqli olanlar)
IMPORT_TO_PIP = {
    "telethon": "Telethon", "yt_dlp": "yt-dlp", "dotenv": "python-dotenv", "PIL": "pillow",
    "pytgcalls": "py-tgcalls", "yaml": "PyYAML", "bs4": "beautifulsoup4", "cv2": "opencv-python",
    "git": "GitPython", "Crypto": "pycryptodome", "speedtest": "speedtest-cli", "gtts": "gTTS",
    "magic": "python-magic", "tgcrypto": "TgCrypto", "socks": "PySocks", "edge_tts": "edge-tts",
    "bgutil_ytdlp_pot_provider": "bgutil-ytdlp-pot-provider", "Levenshtein": "python-Levenshtein",
    "dateutil": "python-dateutil", "telegram": "python-telegram-bot", "OpenSSL": "pyOpenSSL",
}
# izah (menyuda göstərilir)
PURPOSE = {
    "aiogram": "Telegram bot çərçivəsi", "aiohttp": "HTTP / şəbəkə", "Telethon": "userbot / asistan hesabı",
    "yt-dlp": "YouTube yükləmə və axtarış", "ytmusicapi": "YouTube Music axtarışı", "spotipy": "Spotify",
    "mutagen": "mp3 teqləri və örtük", "python-dotenv": "config.env oxumaq", "psutil": "CPU / RAM monitoru",
    "py-tgcalls": "🎙 canlı yayım", "pillow": "şəkil (QR, örtük)", "qrcode": "QR ilə giriş",
    "uvloop": "⚡ sürətli event loop", "packaging": "versiya müqayisəsi", "cryptg": "Telethon sürətləndirici",
    "aiofiles": "asinxron fayl", "requests": "HTTP (sinxron)", "speedtest-cli": ".speedtest",
}
AUTO_YTDLP = ("yt-dlp", "ytmusicapi")          # YouTube tez-tez dəyişir — bunları yeni saxlamaq vacibdir


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _local_names() -> set:
    out = {p.name for p in ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    out |= {p.name for p in ROOT.iterdir() if p.is_dir() and p.name in ("common", "bot", "userbot")}
    out |= {p.stem for p in ROOT.glob("*.py")}
    out |= {"core", "plugins", "userbot_plugins"}   # köhnə struktur adları (common/compat.py ləqəbləri)
    return out


def _py_files() -> list:
    out = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        out.append(p)
    return sorted(out)


def _imports(tree: ast.AST):
    """(modul, vacib?) — modulun ən üst səviyyəsində, try xaricində olan import vacib sayılır."""
    def walk(node, top: bool):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.Import):
                for a in ch.names:
                    yield a.name.split(".")[0], top
            elif isinstance(ch, ast.ImportFrom):
                if ch.level == 0 and ch.module:
                    yield ch.module.split(".")[0], top
            elif isinstance(ch, ast.Try) or (hasattr(ast, "TryStar") and isinstance(ch, ast.TryStar)):
                yield from walk(ch, False)
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If,
                                 ast.With, ast.For, ast.While)):
                yield from walk(ch, top and isinstance(ch, ast.ClassDef))
            else:
                yield from walk(ch, top)
    yield from walk(tree, True)


def _dist_map() -> dict:
    try:
        return metadata.packages_distributions()
    except Exception:
        return {}


def pip_name(module: str, dmap: dict = None) -> str:
    if module in IMPORT_TO_PIP:
        return IMPORT_TO_PIP[module]
    dists = (dmap or {}).get(module)
    if dists:
        return dists[0]
    return module


def scan() -> dict:
    """pip adı → {"imports": set, "files": set, "required": bool}"""
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    local = _local_names()
    dmap = _dist_map()
    out = {}
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        for mod, req in _imports(tree):
            if not mod or mod in std or mod in local or mod.startswith("_"):
                continue
            name = pip_name(mod, dmap)
            e = out.setdefault(name, {"imports": set(), "files": set(), "required": False})
            e["imports"].add(mod)
            e["files"].add(rel)
            e["required"] |= req
    return out


def installed_version(name: str, imports=()) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
    except Exception:
        return None
    for mod in imports:                      # sistem paketi kimi quraşdırılıb (pacman / apt) — versiya bilinmir
        try:
            if util.find_spec(mod) is not None:
                return "?"
        except (ImportError, ValueError):
            pass
    return None


def packages() -> list:
    """Sıralı siyahı: əvvəl çatışmayan vacib, sonra vacib, sonra istəyə bağlı."""
    rows = []
    for name, e in scan().items():
        rows.append({"name": name, "version": installed_version(name, e["imports"]),
                     "required": e["required"], "imports": sorted(e["imports"]), "files": sorted(e["files"]),
                     "purpose": PURPOSE.get(name, "")})
    rows.sort(key=lambda r: (r["version"] is not None, not r["required"], r["name"].lower()))
    return rows


def missing_required() -> list:
    return [r["name"] for r in packages() if r["required"] and r["version"] is None]


# ───────────────────────── pip ─────────────────────────
_OUTDATED = {"ts": 0.0, "data": {}, "error": ""}
OUTDATED_TTL = 6 * 3600


async def _run(args: list, timeout: int = 900) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONUNBUFFERED": "1"})
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "⏱ vaxt bitdi"
    return proc.returncode, (out or b"").decode("utf-8", "replace")


async def outdated(force: bool = False) -> dict:
    """{normallaşdırılmış ad: (quraşdırılmış, ən son)} — yalnız koddakı paketlər."""
    if not force and time.time() - _OUTDATED["ts"] < OUTDATED_TTL and _OUTDATED["data"]:
        return _OUTDATED["data"]
    rc, out = await _run(["list", "--outdated", "--format=json"], timeout=180)
    if rc != 0:
        _OUTDATED["error"] = out.strip().splitlines()[-1][:200] if out.strip() else f"pip rc={rc}"
        return _OUTDATED["data"]
    try:
        js = json.loads(out[out.index("["):]) if "[" in out else []
    except ValueError:
        js = []
    ours = {_norm(n) for n in scan()}
    data = {_norm(x["name"]): (x.get("version"), x.get("latest_version")) for x in js if _norm(x["name"]) in ours}
    _OUTDATED.update(ts=time.time(), data=data, error="")
    return data


def outdated_cached() -> dict:
    return _OUTDATED["data"]


def major_jump(cur: str, new: str) -> bool:
    """3.x → 4.x kimi böyük dəyişiklik (kodu poza bilər). Tarix versiyaları (yt-dlp 2025.x → 2026.x) sayılmır."""
    try:
        a, b = int(str(cur).split(".")[0]), int(str(new).split(".")[0])
    except (ValueError, TypeError):
        return False
    if a >= 2000 and b >= 2000:                      # il.ay.gün — calendar versioning
        return False
    return a < b


async def pip_install(names: list, upgrade: bool = False) -> tuple:
    """(ok, son sətirlər). Bot donmur — ayrıca proses, asinxron gözləmə."""
    if not names:
        return True, ""
    args = ["install"] + (["-U"] if upgrade else []) + list(names)
    rc, out = await _run(args)
    if rc != 0 and "externally-managed-environment" in out:
        # Sistem Python-unu (venv-dən kənar) dəyişmək təhlükəlidir — yalnız açıq icazə ilə
        if os.getenv("DLM_ALLOW_SYSTEM_PIP") == "1":
            rc, out = await _run(args + ["--break-system-packages"])
        else:
            out += ("\n⚠️ Sistem Python-u qorunur (PEP 668). venv işlət və ya config.env-də "
                    "DLM_ALLOW_SYSTEM_PIP=1 yaz.")
    lines = [l for l in out.strip().splitlines() if l.strip()]
    tail = "\n".join(lines[-4:])
    if rc == 0:
        for n in names:
            _OUTDATED["data"].pop(_norm(n), None)
    return rc == 0, tail


# ───────────────────────── requirements.txt ─────────────────────────
def _req_lines() -> list:
    if not REQ_FILE.exists():
        return []
    return REQ_FILE.read_text(encoding="utf-8").splitlines()


def _req_name(line: str) -> str | None:
    line = re.split(r"\s+#", line, maxsplit=1)[0].strip().lstrip("#").strip()
    if not line or line.startswith("-"):
        return None
    m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
    return m.group(0) if m else None


# Kodda import olunmur, amma başqa paketlər istifadə edir — "istifadə olunmur" siyahısına düşməsin
IMPLICIT = {"mutagen", "cryptg", "aiodns", "orjson", "colorama", "yt-dlp-ejs", "secretstorage",
            "psycopg-pool", "bgutil-ytdlp-pot-provider", "uvloop"}


def requirements_diff() -> dict:
    """{"add": [...vacib, faylda yoxdur], "optional": [...], "unused": [...faylda var, kodda yox]}"""
    have = {}
    for l in _req_lines():
        n = _req_name(l)
        if n and not l.strip().startswith("#"):
            have[_norm(n)] = n
    sc = scan()
    req = {_norm(n): n for n, e in sc.items() if e["required"]}
    opt = {_norm(n): n for n, e in sc.items() if not e["required"]}
    return {"add": sorted(v for k, v in req.items() if k not in have),
            "optional": sorted(v for k, v in opt.items() if k not in have),
            "unused": sorted(v for k, v in have.items()
                             if k not in req and k not in opt and k not in {_norm(x) for x in IMPLICIT})}


def write_requirements() -> dict:
    """Vacib paketləri əlavə edir (mövcud sətirlər və versiya şərtləri toxunulmaz),
    istəyə bağlıları şərh kimi yazır — bootstrap onları məcburi quraşdırmasın."""
    lines = _req_lines()
    d = requirements_diff()
    existing_opt = {_norm(_req_name(l) or "") for l in lines if l.strip().startswith("#")}
    if d["add"]:
        lines += ["", "# ── koddan avtomatik əlavə olunub (📦 Asılılıqlar) ──"] + d["add"]
    new_opt = [n for n in d["optional"] if _norm(n) not in existing_opt]
    if new_opt:
        lines += ["", "# ── istəyə bağlı (quraşdırmaq: pip install <ad>) ──"]
        lines += [f"# {n}" + (f"  — {PURPOSE[n]}" if n in PURPOSE else "") for n in new_opt]
    if d["add"] or new_opt:
        REQ_FILE.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return {"added": d["add"], "optional": new_opt, "unused": d["unused"]}
