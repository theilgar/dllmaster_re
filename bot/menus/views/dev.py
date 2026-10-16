"""
bot.menus.views.dev
Bot idarəsi, yeniləmələr, sağlamlıq yoxlaması və Git statusu pəncərələri.
"""

import logging
import shlex
import shutil
import sys
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import LINE, SECTIONS, crumb
from common.diagnostics import run_cmd
from common.menu import _btn
from common.utilities import run_shell

logger = logging.getLogger(__name__)

try:
    from common import updates as upd
except Exception:
    upd = None

try:
    from common import doctor
except Exception:
    doctor = None


def _upd_items() -> list:
    try:
        return upd.get_report().items if upd else []
    except Exception:
        return []


def upd_button_label() -> str:
    if upd is None:
        return "🔔 Yeniləmələr · ❌"
    n = len(_upd_items())
    return f"🔔 Yeniləmələr · {n} ⚠️" if n else "🔔 Yeniləmələr · ✅"


def health_label() -> str:
    if doctor is None:
        return "🩺 Sağlamlıq · ❌"
    ctx = get_context()
    cache = getattr(ctx, "_health_cache", None)
    if not cache:
        return "🩺 Sağlamlıq"
    f, w = doctor.summary(cache)
    if f:
        return f"🩺 Sağlamlıq · ❌ {f}"
    if w:
        return f"🩺 Sağlamlıq · ⚠️ {w}"
    return "🩺 Sağlamlıq · ✅"


def dev_button_label() -> str:
    parts = []
    if _upd_items():
        parts.append(f"🔔{len(_upd_items())}")
    hl = health_label()
    if "❌" in hl or "⚠️" in hl:
        parts.append("🩺⚠️")
    return "🛠 Bot idarəsi" + (" · " + " ".join(parts) if parts else " · 🌿 🧩 🔔 📦")


async def git_summary() -> str:
    if not shutil.which("git"):
        return ""
    try:
        rc, branch, _ = await run_cmd("git", "rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        if rc != 0:
            return ""
        _, last, _ = await run_cmd("git", "log", "-1", "--format=%h · %cr · %s", timeout=5)
        _, dirty, _ = await run_cmd("git", "status", "--porcelain", timeout=5)
        n = len([x for x in dirty.splitlines() if x.strip()])
        return (
            f"🌿 <b>Git:</b> <code>{escape(branch.strip())}</code> · {escape(last.strip()[:70])}"
            + (f"\n     ✏️ <i>{n} fayl commit olunmayıb</i>" if n else "\n     ✅ <i>təmiz</i>")
        )
    except Exception:
        return ""


def dev_hub_view(git_line: str = "", note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_context()
    lbl = lambda name, default: (getattr(ctx, name, None) or (lambda: default))()
    lines = [crumb(SECTIONS["sys"], "🛠 Bot idarəsi") + "\n" + LINE]
    lines.append(git_line or "🌿 <b>Git:</b> <i>məlumat yoxdur</i>")
    lines += [
        "━━━━━━━━━━━━━━━━━━",
        "🌿 <b>GitHub</b> — dəyişikliklər, commit və push",
        "🧩 <b>Plugin-lər</b> — quraşdır, yenilə, söndür",
        f"🔔 <b>Yeniləmələr:</b> {escape(upd_button_label())}",
        f"📦 <b>Asılılıqlar:</b> {escape(lbl('deps_label', '📦 Asılılıqlar'))}",
        f"🩺 <b>Sağlamlıq:</b> {escape(health_label())}",
        "♻️ <b>Restart</b> — botu yenidən başlat",
        "🗄 <b>Arxiv · yedək · loglar</b> — baza yedəyi, kod arxivi, log kanalı, hədəflər",
        "📋 <b>Görünüş</b> — menyu üslubu · 🛠 <b>Texniki iş rejimi</b> — müddət, yayım / depo siyasəti, istifadəçi mətni",
    ]
    if note:
        lines.append(f"\n{note}")
    from bot.menus.views.system_hub import _maint_on, arch_button_label
    from common.menu import ui_button_label

    rows = [
        [
            InlineKeyboardButton(text="🌿 GitHub", callback_data="ghf:open"),
            InlineKeyboardButton(text="🧩 Plugin-lər", callback_data="pm:list"),
        ],
        [
            _btn(upd_button_label(), "upd:dev"),
            InlineKeyboardButton(text=lbl("deps_label", "📦 Asılılıqlar"), callback_data="dp:panel"),
        ],
        [
            InlineKeyboardButton(text=health_label(), callback_data="hc:open"),
            InlineKeyboardButton(text="♻️ Restart", callback_data="botrestart:ask"),
        ],
        [_btn(arch_button_label(), "arch"), InlineKeyboardButton(text=ui_button_label(), callback_data="ui:open")],
        [_btn(f"🛠 Texniki iş rejimi {'🟢 aktiv' if _maint_on() else '🔴'} — ayarlar", "maint")],
        nav_row("sys", refresh="dev"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def run_updates(sudo_password: str | None = None) -> tuple[list, bool]:
    from common.bootstrap import ROOT, detect_pm

    lines, restart = [], False
    q = shlex.quote
    items = _upd_items()
    kinds = {u.kind for u in items}

    run_bot_and_pip = (sudo_password is None) or ("bot" in kinds or "pip" in kinds)

    if run_bot_and_pip:
        if "bot" in kinds or not items:
            rc, out = await run_shell(f"git -C {q(str(ROOT))} pull --ff-only")
            tail = (out.strip().splitlines() or ["—"])[-1][:120]
            lines.append(f"{'✅' if rc == 0 else '❌'} 🤖 git pull · <code>{escape(tail)}</code>")
            restart = restart or (rc == 0 and "up to date" not in out.lower())

        if "pip" in kinds:
            req = str(ROOT / "requirements.txt")
            rc, out = await run_shell(f"{q(sys.executable)} -m pip install -U -r {q(req)}")
            lines.append(f"{'✅' if rc == 0 else '❌'} 📦 pip -U -r requirements.txt")
            if rc != 0:
                err = [ln for ln in out.strip().splitlines() if ln.strip()]
                key = [ln for ln in err if "error" in ln.lower()] or err
                lines.append(f"<code>{escape(' | '.join(key[-3:])[:350])}</code>")
            restart = restart or rc == 0

    if "system" in kinds or sudo_password is not None:
        pm = detect_pm()
        if sudo_password:
            cmd = "sudo -S pacman -Syu --noconfirm" if pm == "pacman" else "sudo -S apt-get -y upgrade"
            rc, out = await run_shell(cmd, input_data=sudo_password.strip() + "\n")
        else:
            cmd = "sudo -n pacman -Syu --noconfirm" if pm == "pacman" else "sudo -n apt-get -y upgrade"
            rc, out = await run_shell(cmd)

        out_low = out.lower()
        if rc != 0 and ("password is required" in out_low or "sudo:" in out_low or "incorrect password" in out_low or "try again" in out_low):
            lines.append("⚠️ 🐧 sistem paketləri — sudo parol istədi")
        elif rc != 0:
            err_tail = (out.strip().splitlines() or ["xəta"])[-1][:120]
            lines.append(f"❌ 🐧 sistem paketləri · <code>{escape(err_tail)}</code>")
        else:
            lines.append("✅ 🐧 sistem paketləri")
            restart = True

    if not lines:
        lines.append("ℹ️ Yeniləmə yoxdur")
    if restart:
        lines.append("\n<i>Dəyişiklik üçün restart lazımdır.</i>")
    return lines, restart


def health_view(note: str = "", back: str = "dev") -> tuple[str, InlineKeyboardMarkup]:
    kb_back = InlineKeyboardMarkup(inline_keyboard=[nav_row(back)])
    if doctor is None:
        return "🩺 <b>Sağlamlıq</b>\n\n❌ <code>common/doctor.py</code> yüklənmədi.", kb_back
    ctx = get_context()
    checks = getattr(ctx, "_health_cache", None) or []
    lines = [crumb(SECTIONS["sys"], "🩺 Sağlamlıq") + "\n" + LINE]
    if not checks:
        lines.append("⏳ <i>Hələ yoxlanmayıb — 🔄 Yenilə bas.</i>")
    else:
        f, w = doctor.summary(checks)
        head = "✅ Hər şey qaydasındadır" if not (f or w) else f"❌ {f} problem · ⚠️ {w} xəbərdarlıq"
        lines.append(f"<b>{head}</b>\n")
        for c in checks:
            lines.append(f"{c.icon} <b>{escape(c.title)}</b> — {escape(c.detail)}")
    if note:
        lines.append(f"\n{note}")
    rows = []
    if any(c.status != "ok" and (c.fix_pip or c.fix_pacman or c.fix_apt) for c in checks):
        rows.append([InlineKeyboardButton(text="🔧 Çatışmayanları quraşdır", callback_data="hc:fix")])
    rows.append(nav_row(back, refresh="hc:refresh"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def upd_view(back: str = "main", note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    kb_back = InlineKeyboardMarkup(inline_keyboard=[nav_row(back)])
    if upd is None:
        return "🔔 <b>Yeniləmələr</b>\n\n❌ <code>common/updates.py</code> yüklənmədi.", kb_back
    r = upd.get_report()
    lines = [crumb(*([SECTIONS["sys"]] if back in ("sys", "dev") else []), "🔔 Yeniləmələr") + "\n" + LINE]
    if not r.checked_at:
        lines.append("⏳ <i>Hələ yoxlanmayıb — 🔎 İndi yoxla bas.</i>")
    else:
        lines.append(upd.menu_notice(max_items=30) or "✅ <b>Hər şey ən son versiyadadır.</b>")
    if note:
        lines.append(f"\n{note}")
    rows = [[_btn("🔎 İndi yoxla", f"upd:{back}:check")]]
    if r.items and any(not u.blocked for u in r.items):
        rows.append([_btn(f"⬇️ Hamısını yenilə ({sum(1 for u in r.items if not u.blocked)})", f"upd:{back}:do")])
    rows.append(nav_row(back))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "dev_button_label",
    "dev_hub_view",
    "git_summary",
    "health_label",
    "health_view",
    "run_updates",
    "upd_button_label",
    "upd_view",
]
