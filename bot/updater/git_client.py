"""
🐙 bot/updater/git_client.py - Git və GitHub əməliyyatları (commit, push, pull, rollback).
"""
from __future__ import annotations

import ast
import base64
import fnmatch
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path

from bot.updater import ssh_key
from common.database import get_db
from common.utilities import get_tz
from bot.updater.reports import (
    fmt_time,
    kind_icon,
)

logger = logging.getLogger(__name__)

# ───────────────────────── GitHub (git commit + push) ─────────────────────────
def _token() -> str:
    """Verilənlər bazasından və ya config.env-dən GitHub tokenini oxuyur."""
    try:
        db_tok = get_db().get_setting("gh_token")
        if db_tok:
            return db_tok.strip()
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:448]: %r", _e)
    return (os.getenv("GITHUB_TOKEN") or "").strip()


def redact(text: str) -> str:
    tok = _token()
    if tok:
        text = text.replace(tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{tok}".encode()).decode(), "***")
    env_tok = (os.getenv("GITHUB_TOKEN") or "").strip()
    if env_tok and env_tok != tok:
        text = text.replace(env_tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{env_tok}".encode()).decode(), "***")
    return text


def git(root: Path, *args, timeout=60, auth=False, raw=False):
    cmd = ["git", "-C", str(root)]
    if auth and _token():
        basic = base64.b64encode(f"x-access-token:{_token()}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C", **ssh_key.env())   # 🔐 SSH açarı varsa
    try:
        r = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", f"git {args[0]}: vaxt bitdi ({timeout} san.)"
    return r.returncode, (r.stdout if raw else r.stdout.strip()), redact(r.stderr.strip())


def github_web_url(remote_url: str):
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://(?:[^@/]+@)?github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$",
                 remote_url or "")
    return f"https://github.com/{m.group(1)}" if m else None


def short_error(err: str, limit=6) -> str:
    lines = [l for l in (err or "").splitlines() if l.strip() and not l.startswith("hint:")]
    return "\n".join(lines[-limit:])[-600:] or (err or "")[-300:]


def push_hint(err: str) -> str:
    e = err.lower()
    if "non-fast-forward" in e or "fetch first" in e or "rejected" in e:
        return "Uzaq repoda sizdə olmayan commit-lər var. Serverdə <code>git pull --rebase</code> edib yenidən cəhd edin."
    if "permission denied" in e or "authentication" in e or "could not read username" in e or "403" in e:
        return "Giriş alınmadı: GitHub Token və ya SSH açarını yoxlayın. <b>⚙️ Quraşdırma</b> panelindən token təyin edə bilərsiniz."
    if "could not resolve host" in e or "unable to access" in e:
        return "GitHub-a qoşulmaq alınmadı (şəbəkə xətası)."
    return ""


def default_commit_message(files: dict, started: float) -> str:
    def names(paths):
        return ", ".join(Path(p).name for p in paths)
    body = []
    if files.get("changed"):
        body.append(f"Dəyişən: {names(files['changed'])}")
    if files.get("added"):
        body.append(f"Yeni: {names(files['added'])}")
    if files.get("removed"):
        body.append(f"Silinən: {names(files['removed'])}")
    title = f"Bot yeniləməsi ({datetime.fromtimestamp(started, get_tz()).strftime('%d.%m.%Y %H:%M')})"
    return title + ("\n\n" + "\n".join(body) if body else "")


def push_update(root: Path, paths: list, message: str) -> dict:
    res = {"ok": False, "committed": False, "error": "", "hint": ""}

    rc, _, _ = git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        res["error"] = "Layihə git repo deyil."
        res["hint"] = "GitHub panelindən <b>⚙️ Quraşdırma</b> bölməsinə daxil olub 'Reponu Başlat' seçin."
        return res
    remote = os.getenv("GITHUB_REMOTE", "origin")
    rc, remote_url, _ = git(root, "remote", "get-url", remote)
    if rc != 0:
        res["error"] = f"'{remote}' remote-u tapılmadı."
        res["hint"] = "GitHub panelindən <b>⚙️ Quraşdırma</b> bölməsinə daxil olub Remote URL əlavə edin."
        return res
    rc, branch, _ = git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if rc != 0 or not branch:
        res["error"] = "Repo heç bir branch-da deyil (detached HEAD)."
        res["hint"] = "Terminalda <code>git switch main</code> (və ya istədiyiniz branch) ilə branch-a qayıdın."
        return res
    res["branch"] = branch
    remote_ref = f"{remote}/{branch}"
    if (git(root, "rev-parse", "--verify", "--quiet", "HEAD")[0] != 0
            and git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_ref}")[0] == 0):
        # boş lokal branch + uzaq branch-da commit var → push rədd olunar (fast-forward deyil)
        res["error"] = f"'{branch}' branch-ında hələ heç bir commit yoxdur, uzaq repoda isə var."
        res["hint"] = (f"Terminalda <code>git reset --mixed {remote_ref}</code> işlədin "
                       f"(fayllar silinmir), sonra yenidən göndərin.")
        return res

    use, to_add = [], []
    for p in paths:
        tracked = git(root, "ls-files", "--error-unmatch", "--", p)[0] == 0
        in_head = bool(git(root, "ls-tree", "--name-only", "HEAD", "--", p)[1])
        if not tracked and not in_head and not (root / p).exists():
            continue
        if git(root, "check-ignore", "-q", "--", p)[0] == 0:
            continue
        use.append(p)
        if tracked or (root / p).exists():
            to_add.append(p)
    res["files"] = use

    if use:
        rc, _, err = git(root, "add", "-A", "--", *to_add) if to_add else (0, "", "")
        if rc != 0:
            res["error"] = f"git add:\n{short_error(err)}"
            return res
        _, staged, _ = git(root, "diff", "--cached", "--name-only", "--", *use)
        if staged:
            ident = []
            if not git(root, "config", "user.name")[1]:
                ident += ["-c", "user.name=DLLMaster Bot"]
            if not git(root, "config", "user.email")[1]:
                ident += ["-c", "user.email=bot@dllmaster.local"]
            rc, _, err = git(root, *ident, "commit", "-m", message, "--", *use)
            if rc != 0:
                res["error"] = f"git commit:\n{short_error(err)}"
                return res
            res["committed"] = True

    rc, _, err = git(root, "push", "-u", remote, branch, timeout=120, auth=True)
    if rc != 0:
        res["error"] = f"git push:\n{short_error(err)}"
        res["hint"] = push_hint(err)
        return res

    res["ok"] = True
    res["hash"] = git(root, "rev-parse", "--short", "HEAD")[1]
    full = git(root, "rev-parse", "HEAD")[1]
    web = github_web_url(remote_url)
    res["url"] = f"{web}/commit/{full}" if web else None
    others = [l for l in git(root, "status", "--porcelain")[1].splitlines() if l.strip()]
    res["others"] = len(others)
    return res


# ───────────────────────── GitHub-dan yeniləmə (fetch + pull) ─────────────────────────
DEP_FILES = {"requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg"}
MAX_COMMITS = 10
MAX_INCOMING_FILES = 12
KIND_BY_CODE = {"A": "new", "C": "new", "D": "deleted", "R": "renamed"}


def git_ident(root: Path) -> list:
    """Repoda müəllif yoxdursa stash/rebase üçün müvəqqəti müəllif."""
    ident = []
    if not git(root, "config", "user.name")[1]:
        ident += ["-c", "user.name=DLLMaster Bot"]
    if not git(root, "config", "user.email")[1]:
        ident += ["-c", "user.email=bot@dllmaster.local"]
    return ident


def upstream_ref(root: Path):
    """Cari branch-ın izlədiyi uzaq branch (məs. origin/main). Upstream qurulmayıbsa remote/branch."""
    rc, up, _ = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc == 0 and up:
        return up
    rc, branch, _ = git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if rc != 0 or not branch:
        return None
    ref = f"{os.getenv('GITHUB_REMOTE', 'origin')}/{branch}"
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}")[0] == 0:
        return ref
    return None


def dirty_paths(root: Path) -> set:
    changes, excluded = working_changes(root)
    return {p for _, p in changes + excluded}


def parse_name_status(out: str) -> list:
    """`git diff --name-status -z` çıxışı → [(kind, path, old_path)]"""
    items, res, i = out.split("\0"), [], 0
    while i < len(items):
        status = items[i]
        i += 1
        if not status:
            continue
        code = status[0]
        if code in "RC" and i + 1 < len(items):
            old, new = items[i], items[i + 1]
            i += 2
            res.append((KIND_BY_CODE[code], new, old if code == "R" else None))
        elif i < len(items):
            res.append((KIND_BY_CODE.get(code, "modified"), items[i], None))
            i += 1
    return res


def syntax_errors(root: Path, paths) -> list:
    errors = []
    for p in paths:
        f = root / p
        if not p.endswith(".py") or not f.is_file():
            continue
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=p)
        except SyntaxError as e:
            errors.append((p, f"{e.msg} (sətir {e.lineno})"))
    return errors


def remote_updates(root: Path, fetch: bool = True) -> dict:
    """GitHub-da lokalda olmayan commit-ləri, gələn faylları və mümkün toqquşmaları tapır."""
    info = {"ok": False, "error": "", "hint": "", "behind": 0, "ahead": 0,
            "commits": [], "files": [], "conflicts": [], "deps": [], "plus": 0, "minus": 0}
    if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
        info["error"] = "Layihə git repo deyil."
        info["hint"] = "<b>⚙️ Quraşdırma</b> panelindən reponu başladın və Remote URL əlavə edin."
        return info
    remote = os.getenv("GITHUB_REMOTE", "origin")
    rc, url, _ = git(root, "remote", "get-url", remote)
    if rc != 0:
        info["error"] = f"'{remote}' remote-u tapılmadı."
        info["hint"] = "<b>⚙️ Quraşdırma</b> panelindən Remote URL əlavə edin."
        return info
    info["web"] = github_web_url(url)

    if fetch:
        rc, _, err = git(root, "fetch", "--prune", remote, timeout=60, auth=True)
        if rc != 0:
            info["error"] = f"git fetch:\n{short_error(err)}"
            info["hint"] = push_hint(err)
            return info

    up = upstream_ref(root)
    if not up:
        info["error"] = "Uzaq branch tapılmadı."
        info["hint"] = "GitHub-da bu adda branch yoxdur — əvvəlcə bir dəfə push edin."
        return info
    info["upstream"] = up
    info["branch"] = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
    info["head"] = git(root, "rev-parse", "HEAD")[1]
    info["remote_head"] = git(root, "rev-parse", up)[1]

    rc, counts, _ = git(root, "rev-list", "--left-right", "--count", f"{up}...HEAD")
    if rc == 0 and counts:
        info["behind"], info["ahead"] = (int(x) for x in counts.split())

    if info["behind"]:
        _, log, _ = git(root, "log", f"-n{MAX_COMMITS}", "--format=%h%x1f%an%x1f%ct%x1f%s", f"HEAD..{up}")
        for line in log.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                info["commits"].append({"hash": parts[0], "author": parts[1],
                                        "ts": int(parts[2]), "subject": parts[3]})
        _, out, _ = git(root, "diff", "--name-status", "-z", f"HEAD...{up}", raw=True)
        info["files"] = parse_name_status(out)
        _, num, _ = git(root, "diff", "--numstat", f"HEAD...{up}")
        for line in num.splitlines():
            a, b = (line.split("\t") + ["", ""])[:2]
            info["plus"] += int(a) if a.isdigit() else 0
            info["minus"] += int(b) if b.isdigit() else 0
        incoming = {p for _, p, _ in info["files"]} | {o for _, _, o in info["files"] if o}
        info["conflicts"] = sorted(dirty_paths(root) & incoming)
        info["deps"] = sorted(p for p in incoming if Path(p).name in DEP_FILES)

    info["ok"] = True
    return info


def pull_from_github(root: Path, stash: bool = False, rebase: bool = False) -> dict:
    """Artıq fetch edilmiş uzaq branch-ı tətbiq edir (ff-only və ya rebase, istəyə görə stash)."""
    res = {"ok": False, "error": "", "hint": "", "stashed": False}
    up = upstream_ref(root)
    if not up:
        res["error"] = "Uzaq branch tapılmadı."
        return res
    old = git(root, "rev-parse", "HEAD")[1]
    res["old"] = old
    ident = git_ident(root)

    def fail(error: str, hint: str = "") -> dict:
        if res["stashed"]:
            rc, _, err = git(root, "stash", "pop")
            if rc != 0:
                hint += ("\n" if hint else "") + "Stash geri qaytarılmadı, serverdə <code>git stash list</code> yoxlayın."
        res.update(error=error, hint=hint)
        return res

    if stash:
        label = f"dllmaster-autostash {datetime.now(get_tz()).strftime('%d.%m.%Y %H:%M')}"
        rc, out, err = git(root, *ident, "stash", "push", "-u", "-m", label)
        if rc != 0:
            return fail(f"git stash:\n{short_error(err)}")
        res["stashed"] = "No local changes" not in out

    if rebase:
        rc, _, err = git(root, *ident, "rebase", up, timeout=120)
        if rc != 0:
            git(root, "rebase", "--abort")
            return fail(f"git rebase:\n{short_error(err)}",
                        "Konflikt var — rebase ləğv edildi. Serverdə əl ilə həll etmək lazımdır.")
    else:
        rc, _, err = git(root, "merge", "--ff-only", up, timeout=120)
        if rc != 0:
            e = err.lower()
            hint = ""
            if "would be overwritten" in e or "untracked working tree" in e:
                hint = "Lokal dəyişikliklər gələn fayllarla toqquşur — <b>🗄 Stash et və yenilə</b> seçin."
            elif "fast-forward" in e or "diverg" in e:
                hint = "Lokal və GitHub tarixçəsi ayrılıb — <b>🔀 Rebase edib yenilə</b> seçin."
            return fail(f"git merge:\n{short_error(err)}", hint)

    new = git(root, "rev-parse", "HEAD")[1]
    _, names, _ = git(root, "diff", "--name-only", "-z", old, new, raw=True)
    changed = [p for p in names.split("\0") if p]
    count = git(root, "rev-list", "--count", f"{old}..{up}")[1]
    res.update(
        ok=True, new=new, files=changed,
        count=int(count) if count.isdigit() else 0,
        errors=syntax_errors(root, changed),
        deps=sorted(p for p in changed if Path(p).name in DEP_FILES),
    )
    if res["stashed"]:
        res["stash_ref"] = git(root, "stash", "list", "-n1", "--format=%gd")[1] or "stash@{0}"
    return res


def rollback_pull(root: Path, old: str, new: str) -> dict:
    if git(root, "rev-parse", "HEAD")[1] != new:
        return {"ok": False, "error": "HEAD artıq dəyişib — avtomatik geri qaytarmaq təhlükəlidir."}
    rc, _, err = git(root, "reset", "--keep", old)
    if rc != 0:
        return {"ok": False, "error": f"git reset:\n{short_error(err)}"}
    return {"ok": True}


def install_requirements(root: Path):
    req = root / "requirements.txt"
    if not req.is_file():
        return False, "requirements.txt tapılmadı"
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)],
                           capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "pip: vaxt bitdi (15 dəq.)"
    out = (r.stdout + "\n" + r.stderr).strip()
    return r.returncode == 0, "\n".join(out.splitlines()[-8:])[-800:]


def incoming_text(info: dict, compact: bool = False) -> str:
    lines = [f"🌿 <code>{escape(info.get('branch') or '—')}</code> ← <code>{escape(info.get('upstream') or '—')}</code>"]
    if not info["behind"]:
        lines.append("\n✅ <b>Bot ən son versiyadadır.</b>")
        if info["ahead"]:
            lines.append(f"⬆️ <i>Lokalda GitHub-a göndərilməmiş {info['ahead']} commit var.</i>")
        return "\n".join(lines)

    lines.append(f"\n🆕 <b>GitHub-da {info['behind']} yeni commit</b> · 📃 <b>+{info['plus']}</b> / <b>−{info['minus']}</b>")
    web = info.get("web")
    commits = info["commits"][:3] if compact else info["commits"]
    for c in commits:
        h = f'<a href="{web}/commit/{c["hash"]}">{c["hash"]}</a>' if web else f"<code>{c['hash']}</code>"
        lines.append(f"• {h} {escape(c['subject'][:70])} — <i>{escape(c['author'])}, {fmt_time(c['ts'])}</i>")
    if info["behind"] > len(commits):
        lines.append(f"<i>… və daha {info['behind'] - len(commits)} commit</i>")

    files = info["files"]
    if files:
        counts = {}
        for kind, _, _ in files:
            counts[kind] = counts.get(kind, 0) + 1
        summary = " · ".join(f"{STATUS_ICONS[k]} {v}" for k, v in counts.items())
        lines.append(f"\n📁 <b>Fayllar:</b> {len(files)} ({summary})")
        if not compact:
            for kind, path, old in files[:MAX_INCOMING_FILES]:
                extra = f" ← <code>{escape(old)}</code>" if old else ""
                lines.append(f"   {STATUS_ICONS[kind]} {kind_icon(path)} <code>{escape(path)}</code>{extra}")
            if len(files) > MAX_INCOMING_FILES:
                lines.append(f"   <i>… və daha {len(files) - MAX_INCOMING_FILES} fayl</i>")

    if info["deps"]:
        lines.append("\n📦 Asılılıqlar dəyişir: " + ", ".join(f"<code>{escape(p)}</code>" for p in info["deps"]))
    if info["ahead"]:
        lines.append(f"\n🔀 <b>Tarixçə ayrılıb:</b> lokalda GitHub-da olmayan {info['ahead']} commit var — rebase lazımdır.")
    if info["conflicts"]:
        lines.append(f"\n⚠️ <b>Lokal dəyişikliklərlə toqquşur ({len(info['conflicts'])}):</b>")
        lines += [f"   ✏️ <code>{escape(p)}</code>" for p in info["conflicts"][:6]]
        if len(info["conflicts"]) > 6:
            lines.append(f"   <i>… və daha {len(info['conflicts']) - 6}</i>")
        lines.append("<i>Yeniləmək üçün lokal dəyişikliklər stash-ə qoyulacaq.</i>")
    return "\n".join(lines)


# ───────────────────────── Full update & dəyişikliklər ─────────────────────────
SENSITIVE_PATTERNS = [
    "config.env", ".env", "*.env", ".env.*", "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "*cookies*.txt", "*.pem", "*.key", "*.session", "*.session-journal",
    "data/*", "download/*", "logs/*",
]
STATUS_ICONS = {"new": "🆕", "modified": "✏️", "deleted": "🗑", "renamed": "🔀"}


def is_sensitive(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in SENSITIVE_PATTERNS)


def working_changes(root: Path):
    rc, out, _ = git(root, "status", "--porcelain=v1", "-z", "-uall", raw=True)
    if rc != 0:
        return [], []
    items = out.split("\0")
    changes, excluded, i = [], [], 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            old = items[i] if i < len(items) else ""
            i += 1
            kind = "renamed"
            if old and "R" in xy:
                if is_sensitive(old):
                    excluded.append(("deleted", old))
                else:
                    changes.append(("deleted", old))
        elif xy == "??":
            kind = "new"
        elif "D" in xy:
            kind = "deleted"
        else:
            kind = "modified"
        if is_sensitive(path):
            excluded.append((kind, path))
        else:
            changes.append((kind, path))
    return changes, excluded


def repo_status(root: Path, fetch: bool = False) -> dict:
    info = {}
    is_repo = git(root, "rev-parse", "--is-inside-work-tree")[0] == 0
    info["is_repo"] = is_repo
    if not is_repo:
        info["error"] = "Layihə git repo deyil."
        info["changes"], info["excluded"] = [], []
        info["configured"] = False
        return info

    remote = os.getenv("GITHUB_REMOTE", "origin")
    info["remote"] = remote
    rc, url, _ = git(root, "remote", "get-url", remote)
    info["remote_url"] = url if rc == 0 else None
    info["web"] = github_web_url(url) if rc == 0 else None
    info["branch"] = git(root, "rev-parse", "--abbrev-ref", "HEAD")[1]
    tok = _token()
    info["has_token"] = bool(tok)
    info["configured"] = bool(url and (tok or not url.startswith("https://")))

    rc, last, _ = git(root, "log", "-1", "--format=%h%x1f%s%x1f%ct")
    if rc == 0 and last:
        h, subj, ts = last.split("\x1f")
        info["last"] = {"hash": h, "subject": subj, "ts": int(ts)}
    if fetch and info["remote_url"]:
        rc, _, err = git(root, "fetch", remote, timeout=30, auth=True)
        info["fetch_error"] = short_error(err) if rc != 0 else None
    rc, counts, _ = git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if rc == 0 and counts:
        behind, ahead = (int(x) for x in counts.split())
        info["behind"], info["ahead"] = behind, ahead
    info["changes"], info["excluded"] = working_changes(root)
    return info


def full_commit_message(changes, started: float, label: str = "Tam yeniləmə") -> str:
    groups = {}
    for kind, path in changes:
        groups.setdefault(kind, []).append(Path(path).name)
    title = f"{label} ({datetime.fromtimestamp(started, get_tz()).strftime('%d.%m.%Y %H:%M')})"
    labels = [("modified", "Dəyişən"), ("new", "Yeni"), ("deleted", "Silinən"), ("renamed", "Adı dəyişən")]
    body = []
    for key, label in labels:
        names = groups.get(key)
        if names:
            shown = ", ".join(names[:15]) + (f" və daha {len(names) - 15}" if len(names) > 15 else "")
            body.append(f"{label} ({len(names)}): {shown}")
    return title + ("\n\n" + "\n".join(body) if body else "")


def push_all(root: Path, message, selected=None) -> dict:
    changes, excluded = working_changes(root)
    if selected is None:
        chosen, skipped = changes, excluded
    else:
        everything = changes + excluded
        chosen = [(k, p) for k, p in everything if p in selected]
        skipped = [(k, p) for k, p in everything if p not in selected]
    paths = sorted({p for _, p in chosen})
    label = "Tam yeniləmə" if selected is None else "Seçilmiş yeniləmə"
    res = push_update(root, paths, message or full_commit_message(chosen, time.time(), label))
    res["changes"] = chosen
    res["skipped"] = len({p for _, p in skipped})
    res["excluded"] = [p for _, p in skipped if is_sensitive(p)]
    res["sensitive_sent"] = [p for p in paths if is_sensitive(p)]
    if res.get("ok"):
        left, left_ex = working_changes(root)
        res["remaining"] = sorted({p for _, p in left + left_ex})
    return res


