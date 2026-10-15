"""
🔔 Yeniləmə bildirişi və Git idarəetməsi — bot/plugins/update_notifier_plugin.py
(Modullaşdırılmış entry-point; əsas məntiq bot/updater/ paketindədir)
"""
from __future__ import annotations

from bot.updater.git_client import (
    git,
    github_web_url,
    pull_from_github,
    push_all,
    push_update,
    remote_updates,
    repo_status,
    rollback_pull,
)
from bot.updater.handlers import setup
from bot.updater.reports import (
    build_diff,
    build_failure_only,
    build_report,
    build_short,
    compare,
    fmt_delta,
    fmt_size,
    fmt_time,
    folder_of,
    gh_kb,
    kind_icon,
    names_line,
    scan_files,
    status_kb,
)

PRIORITY = 1000

__all__ = [
    "setup",
    "git",
    "github_web_url",
    "push_update",
    "pull_from_github",
    "rollback_pull",
    "remote_updates",
    "repo_status",
    "push_all",
    "build_report",
    "build_short",
    "build_diff",
    "build_failure_only",
    "scan_files",
    "compare",
    "gh_kb",
    "status_kb",
    "fmt_time",
    "fmt_size",
    "fmt_delta",
    "kind_icon",
    "folder_of",
    "names_line",
    "PRIORITY",
]
