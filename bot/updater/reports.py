"""
📊 bot/updater/reports.py - Fayl skanı, fərq (diff) analizi və hesabat generasiyası.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.utilities import format_bytes, get_tz, read_proc_rss

logger = logging.getLogger(__name__)

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", ".env", "node_modules",
    "download", "data", "logs", ".idea", ".vscode", "site-packages",
}
PACKAGES = ["aiogram", "yt-dlp", "yt-dlp-ejs", "spotipy", "aiohttp"]
MAX_TEXT = 3900
MAX_NAMES = 8
DELAY_AFTER_START = 3
_CMD_RE = re.compile(r"""Command\(\s*(?:commands\s*=\s*)?\[?\s*((?:["'][\w]+["']\s*,?\s*)+)""")

def fmt_time(ts) -> str:
    return datetime.fromtimestamp(ts, get_tz()).strftime("%d.%m.%Y %H:%M:%S")


def fmt_size(n: int) -> str:
    return format_bytes(n, digits=1, space=True)



def fmt_delta(n: int, unit_bytes=True) -> str:
    sign = "+" if n > 0 else ("−" if n < 0 else "±")
    return sign + (fmt_size(abs(n)) if unit_bytes else str(abs(n)))


def kind_icon(path: str) -> str:
    if path.startswith(("bot/plugins/", "userbot/plugins/", "plugins/")):
        return "🔌"
    if path.startswith(("bot/core/", "userbot/core/", "common/", "core/")):
        return "⚙️"
    return "📄"


def folder_of(path: str) -> str:
    p = Path(path).parent.as_posix()
    return "(kök)" if p == "." else f"{p}/"


def names_line(icon: str, label: str, names) -> str:
    names = sorted(names)
    shown = ", ".join(f"<code>{escape(n)}</code>" for n in names[:MAX_NAMES])
    more = f" +{len(names) - MAX_NAMES}" if len(names) > MAX_NAMES else ""
    return f"   {icon} {label}: {shown}{more}"


# ───────────────────────── fayl skanı ─────────────────────────
def scan_files(root: Path) -> dict:
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                data = path.read_bytes()
                st = path.stat()
            except OSError as e:
                logger.warning(f"Fayl oxunmadı ({path}): {e}")
                continue
            result[path.relative_to(root).as_posix()] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
                "mtime": int(st.st_mtime),
                "content": data.decode("utf-8", errors="replace"),
            }
    return result


def compare(old: dict, new: dict):
    added = sorted(p for p in new if p not in old)
    removed = sorted(p for p in old if p not in new)
    changed = sorted(p for p in new if p in old and new[p]["sha256"] != old[p]["sha256"])
    return added, changed, removed


# ───────────────────────── kod analizi ─────────────────────────
def analyze(source: str):
    commands = set()
    for m in _CMD_RE.finditer(source or ""):
        commands.update(re.findall(r"""["'](\w+)["']""", m.group(1)))

    try:
        tree = ast.parse(source or "")
    except SyntaxError as e:
        return {}, commands, f"SyntaxError: {e.msg} (sətir {e.lineno})"

    lines = (source or "").splitlines()
    symbols = {}

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                kids = [
                    (min([d.lineno for d in getattr(k, "decorator_list", [])] + [k.lineno]), k.end_lineno)
                    for k in ast.walk(child)
                    if k is not child and isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                start = min([d.lineno for d in getattr(child, "decorator_list", [])] + [child.lineno])
                covered = set()                     # iç tərif sətirləri — bir dəfə (əvvəl hər sətirdə hamısı yoxlanırdı)
                for a, b in kids:
                    covered.update(range(a, b + 1))
                own = [
                    lines[i - 1].strip()
                    for i in range(start, (child.end_lineno or child.lineno) + 1)
                    if 0 < i <= len(lines) and i not in covered
                ]
                own = [line for line in own if line]
                symbols[name] = hashlib.md5("\n".join(own).encode()).hexdigest()
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree)
    return symbols, commands, None


def line_stats(old_text: str, new_text: str):
    plus = minus = 0
    for line in difflib.unified_diff((old_text or "").splitlines(), (new_text or "").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    return plus, minus


def unified(path: str, old_text, new_text) -> str:
    return "\n".join(difflib.unified_diff(
        (old_text or "").splitlines(), (new_text or "").splitlines(),
        fromfile=f"a/{path}" if old_text is not None else "/dev/null",
        tofile=f"b/{path}" if new_text is not None else "/dev/null",
        lineterm="",
    ))


# ───────────────────────── mühit məlumatı ─────────────────────────
def package_versions() -> dict:
    from importlib import metadata
    out = {"python": platform.python_version()}
    for pkg in PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:211]: %r", _e)
    return out


def git_info(root: Path):
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%h%x1f%s%x1f%ct"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if not out:
            return None
        h, subject, ts = out.split("\x1f")
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {"hash": h, "subject": subject, "ts": int(ts), "dirty": bool(dirty)}
    except Exception:
        return None


def ram_mb():
    rss = read_proc_rss()
    return (rss / 1024 / 1024) if rss else None



# ───────────────────────── hesabat ─────────────────────────
def file_entry(path: str, o, n) -> tuple:
    icon = kind_icon(path)
    if o is None:
        syms, cmds, err = analyze(n["content"])
        head = [f"🆕 {icon} <code>{escape(path)}</code>",
                f"   💾 <b>{fmt_size(n['size'])}</b> · 📃 {n['lines']} sətir · 🕒 {fmt_time(n['mtime'])}"]
        if syms:
            head.append(names_line("🧩", f"{len(syms)} funksiya/class", syms))
        if cmds:
            head.append(names_line("⌨️", "komandalar", {f'/{c}' for c in cmds}))
        return "\n".join(head), err

    if n is None:
        syms, cmds, _ = analyze(o.get("content"))
        head = [f"🗑 {icon} <code>{escape(path)}</code>",
                f"   💾 köhnə: {fmt_size(o['size'])} · 📃 {o['lines']} sətir"]
        if cmds:
            head.append(names_line("⌨️", "silinən komandalar", {f'/{c}' for c in cmds}))
        return "\n".join(head), None

    head = [f"{icon} <code>{escape(path)}</code>",
            f"   💾 {fmt_size(o['size'])} → <b>{fmt_size(n['size'])}</b> ({fmt_delta(n['size'] - o['size'])})",
            f"   📃 {o['lines']} → <b>{n['lines']}</b> sətir"]
    new_syms, new_cmds, err = analyze(n["content"])
    if o.get("content") is None:
        head[-1] += f" ({fmt_delta(n['lines'] - o['lines'], False)})"
        head.append("   <i>ℹ️ köhnə məzmun saxlanmayıb — ətraflı müqayisə növbəti yeniləmədən</i>")
    else:
        plus, minus = line_stats(o["content"], n["content"])
        head[-1] += f" · <b>+{plus}</b> / <b>−{minus}</b>"
        old_syms, old_cmds, _ = analyze(o["content"])
        if not err:
            add = new_syms.keys() - old_syms.keys()
            rem = old_syms.keys() - new_syms.keys()
            chg = {k for k in new_syms.keys() & old_syms.keys() if new_syms[k] != old_syms[k]}
            if add:
                head.append(names_line("➕", "yeni", add))
            if chg:
                head.append(names_line("✏️", "dəyişən", chg))
            if rem:
                head.append(names_line("➖", "silinən", rem))
        if new_cmds - old_cmds:
            head.append(names_line("⌨️", "yeni komandalar", {f'/{c}' for c in new_cmds - old_cmds}))
        if old_cmds - new_cmds:
            head.append(names_line("⌨️", "silinən komandalar", {f'/{c}' for c in old_cmds - new_cmds}))
    head.append(f"   🕒 {fmt_time(n['mtime'])}")
    return "\n".join(head), err


def env_lines(env_old: dict, env_new: dict, git, git_old: str, ram) -> list:
    out = []
    if git:
        mark = " <i>(commit edilməmiş dəyişikliklər var)</i>" if git["dirty"] else ""
        changed = f" (əvvəl <code>{escape(git_old)}</code>)" if git_old and git_old != git["hash"] else ""
        out.append(f"🌿 Git: <code>{escape(git['hash'])}</code> — {escape(git['subject'][:80])}{changed}{mark}")

    diffs = [k for k in env_new if env_old and env_old.get(k) and env_old.get(k) != env_new[k]]
    if diffs:
        out.append("📦 <b>Versiyalar dəyişdi:</b> " + " · ".join(
            f"{escape(k)} {escape(env_old[k])} → <b>{escape(env_new[k])}</b>" for k in diffs))
    news = [k for k in env_new if env_old and k not in env_old]
    if news:
        out.append("📦 Yeni paket: " + " · ".join(f"{escape(k)} {escape(env_new[k])}" for k in news))
    out.append("🐍 " + " · ".join(f"{escape(k)} {escape(v)}" for k, v in env_new.items()))
    if ram:
        out.append(f"🧠 RAM: <b>{ram:.0f} MB</b>")
    return out


def build_report(old, new, added, changed, removed, plugin_status, started, env) -> str:
    head = ["🔄 <b>Bot yeniləndi</b>", f"🕒 Başlama: <b>{fmt_time(started)}</b>"]
    touched = [new[p]["mtime"] for p in added + changed]
    if touched:
        head.append(f"📝 Son yenilənmə: <b>{fmt_time(max(touched))}</b>")
    head += env

    total_plus = total_minus = 0
    for p in changed:
        if old[p].get("content") is not None:
            a, b = line_stats(old[p]["content"], new[p]["content"])
            total_plus, total_minus = total_plus + a, total_minus + b
    total_plus += sum(new[p]["lines"] for p in added)
    total_minus += sum(old[p]["lines"] for p in removed)

    old_total = sum(f["size"] for f in old.values())
    new_total = sum(f["size"] for f in new.values())
    head += [
        f"\n📊 Dəyişən: <b>{len(changed)}</b> · Yeni: <b>{len(added)}</b> · Silinən: <b>{len(removed)}</b>",
        f"📃 Sətirlər: <b>+{total_plus}</b> / <b>−{total_minus}</b>",
        f"💾 Layihə: {fmt_size(old_total)} → <b>{fmt_size(new_total)}</b> ({fmt_delta(new_total - old_total)})",
    ]

    entries, syntax_errors = [], []
    for p in changed:
        e, err = file_entry(p, old[p], new[p])
        entries.append(e)
        if err:
            syntax_errors.append((p, err))
    for p in added:
        e, err = file_entry(p, None, new[p])
        entries.append(e)
        if err:
            syntax_errors.append((p, err))
    for p in removed:
        entries.append(file_entry(p, old[p], None)[0])

    tail = []
    if syntax_errors:
        tail.append("\n🚨 <b>Sintaksis xətaları:</b>")
        tail += [f"❌ <code>{escape(p)}</code> — <i>{escape(err)}</i>" for p, err in syntax_errors]
    failed = {n: e for n, e in (plugin_status or {}).items() if e != "ok"}
    if failed:
        tail.append("\n⚠️ <b>Yüklənməyən plugin-lər:</b>")
        tail += [f"❌ <code>{escape(n)}</code>\n   <i>{escape(e)[:200]}</i>" for n, e in sorted(failed.items())]
    elif plugin_status:
        tail.append(f"\n✅ Bütün plugin-lər yükləndi ({len(plugin_status)})")

    text = "\n".join(head) + "\n"
    tail_text = "\n".join(tail)
    shown = 0
    for entry in entries:
        candidate = text + "\n" + entry + "\n"
        if len(candidate) + len(tail_text) + 80 > MAX_TEXT:
            break
        text, shown = candidate, shown + 1
    if shown < len(entries):
        text += f"\n<i>… və daha {len(entries) - shown} fayl (tam siyahı diff faylındadır)</i>\n"
    return text + tail_text


def build_short(old, new, added, changed, removed, plugin_status, started, git_data) -> str:
    """✂️ Qısa hesabat (/ui → 🔔 Yeniləmə: Qısa) — hər fayl bir sətir, sonra kod blokuna çevrilir."""
    lines = ["🔄 Bot yeniləndi",
             f"🕒 {fmt_time(started)}" + (f" · 🌿 {git_data['hash']}" if git_data else "")]
    plus = minus = 0
    rows, errs = [], []
    for p in changed:
        a = b = 0
        if old[p].get("content") is not None:
            a, b = line_stats(old[p]["content"], new[p]["content"])
        plus, minus = plus + a, minus + b
        rows.append(f"✏️ {p}  +{a}/−{b}")
    for p in added:
        plus += new[p]["lines"]
        rows.append(f"🆕 {p}  {new[p]['lines']} sətir")
    for p in removed:
        minus += old[p]["lines"]
        rows.append(f"🗑 {p}")
    for p in changed + added:
        err = analyze(new[p]["content"])[2]
        if err:
            errs.append(f"🚨 {p} — {err}")
    lines.append(f"📊 ✏️ {len(changed)} · 🆕 {len(added)} · 🗑 {len(removed)} · +{plus}/−{minus}")
    lines.append("")
    shown = 0
    for r in rows:
        if len("\n".join(lines)) + len(r) > MAX_TEXT - 600:
            break
        lines.append(r)
        shown += 1
    if shown < len(rows):
        lines.append(f"… və daha {len(rows) - shown} fayl")
    failed = {n: e for n, e in (plugin_status or {}).items() if e != "ok"}
    if errs or failed:
        lines.append("")
        lines += errs
        lines += [f"⚠️ {n} — {e[:120]}" for n, e in sorted(failed.items())]
    elif plugin_status:
        lines.append(f"\n✅ Plugin-lər: {len(plugin_status)}")
    return "\n".join(escape(l) for l in lines)


def build_diff(old, new, added, changed, removed, started) -> str:
    parts = [f"# DLLMaster Bot yeniləməsi — {fmt_time(started)}",
             f"# Dəyişən: {len(changed)}, yeni: {len(added)}, silinən: {len(removed)}", ""]
    for p in changed:
        if old[p].get("content") is None:
            parts.append(f"# {p}: köhnə məzmun saxlanmayıb, diff mümkün deyil\n")
        else:
            parts.append(unified(p, old[p]["content"], new[p]["content"]) + "\n")
    for p in added:
        parts.append(unified(p, None, new[p]["content"]) + "\n")
    for p in removed:
        if old[p].get("content") is not None:
            parts.append(unified(p, old[p]["content"], None) + "\n")
        else:
            parts.append(f"# {p}: silinib\n")
    return "\n".join(parts)


def build_failure_only(plugin_status: dict, started: float) -> str:
    lines = ["⚠️ <b>Bot başladı, amma bəzi plugin-lər yüklənmədi</b>", f"🕒 {fmt_time(started)}\n"]
    for name, err in sorted(plugin_status.items()):
        if err != "ok":
            lines.append(f"❌ <code>{escape(name)}</code>\n   <i>{escape(err)[:200]}</i>")
    return "\n".join(lines)[:MAX_TEXT]



def gh_kb(pid: str, configured: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if not configured:
        rows.append([InlineKeyboardButton(text="⚙️ GitHub ayarları", callback_data="ghs:menu")])
    rows += [
        [InlineKeyboardButton(text="✅ GitHub-a göndər", callback_data=f"gh:push:{pid}"),
         InlineKeyboardButton(text="✖️ Ləğv et", callback_data=f"gh:skip:{pid}")],
        [InlineKeyboardButton(text="✏️ Commit mesajı", callback_data=f"gh:msg:{pid}"),
         InlineKeyboardButton(text="📦 Hamısını göndər", callback_data="ghf:prep:new")],
        [InlineKeyboardButton(text="☑️ Seçərək göndər", callback_data="ghf:pick:new")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_kb(text: str, url: str = None) -> InlineKeyboardMarkup:
    btn_item = InlineKeyboardButton(text=text, url=url) if url else InlineKeyboardButton(text=text, callback_data="gh:noop")
    return InlineKeyboardMarkup(inline_keyboard=[[btn_item]])

