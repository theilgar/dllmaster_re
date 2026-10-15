"""
🔔 bot/updater/handlers.py - Yeniləmə bildirişi, avtomatik izləmə və idarəetmə handler-ləri.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import F
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from bot.core import panel as ui
from bot.updater.git_client import (
    STATUS_ICONS,
    default_commit_message,
    dirty_paths,
    full_commit_message,
    git,
    incoming_text,
    install_requirements,
    is_sensitive,
    pull_from_github,
    push_all,
    push_update,
    redact,
    remote_updates,
    repo_status,
    rollback_pull,
    _token,
)
from bot.updater.reports import (
    DELAY_AFTER_START,
    build_diff,
    build_failure_only,
    build_report,
    build_short,
    compare,
    env_lines,
    fmt_size,
    fmt_time,
    folder_of,
    gh_kb,
    git_info,
    package_versions,
    ram_mb,
    scan_files,
    status_kb,
)
from bot.updater import ssh_key
from common.database import get_db
from common.utilities import BASE_DIR, get_tz

logger = logging.getLogger(__name__)

def setup(context):
    creator = context.creator_id
    started = time.time()
    dp = context.dp
    bot = context.bot
    root = Path(BASE_DIR)
    push_lock = asyncio.Lock()
    gh_state = {"await_msg": None}
    gh_full = {
        "message": None, "await": False, "chat_id": None, "panel_id": None,
        "selected": None, "items": [], "page": 0,
        "folder_filter": None, "folders": [], "folder_page": 0,
    }
    gh_setup = {"step": None, "chat_id": None, "panel_id": None, "status_msg": ""}

    from bot.core.input_wait import chain_menu_text
    chain_menu_text(context, lambda uid: uid == creator and bool(gh_state["await_msg"] or gh_full["await"]
                                                                  or gh_setup["step"]))

    def load_pending():
        try:
            return json.loads(get_db().get_setting("gh_pending") or "null")
        except ValueError:
            return None

    def save_pending(p):
        get_db().set_setting("gh_pending", json.dumps(p) if p else None)

    async def set_report_kb(p, kb):
        try:
            await bot.edit_message_reply_markup(chat_id=creator, message_id=p["message_id"], reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e):
                logger.debug(f"Hesabat düymələri yenilənmədi: {e}")

    NAV_TO = {"ghf:open": "GitHub", "menu:dev": "Bot idarəsi", "ghs:menu": "GitHub ayarları", "ghs:key": "SSH açarı", "ghf:pick": "Fayllar"}

    def nav(back: str, refresh: str = ""):
        return ui.nav(back, NAV_TO.get(back, ""), refresh_data=refresh)

    btn = ui.btn

    async def edit_full_panel(text: str, kb):
        try:
            await bot.edit_message_text(chat_id=gh_full["chat_id"], message_id=gh_full["panel_id"],
                                        text=text, parse_mode="HTML", reply_markup=kb,
                                        disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e):
                logger.warning(f"GitHub paneli yenilənmədi: {e}")

    async def edit_setup_panel(text: str, kb):
        cid = gh_setup.get("chat_id") or creator
        mid = gh_setup.get("panel_id")
        if mid:
            try:
                await bot.edit_message_text(chat_id=cid, message_id=mid, text=text,
                                            parse_mode="HTML", reply_markup=kb,
                                            disable_web_page_preview=True)
                return
            except Exception as e:
                if "not modified" in str(e):
                    return
        sent = await bot.send_message(cid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        gh_setup["chat_id"] = sent.chat.id
        gh_setup["panel_id"] = sent.message_id

    # ═════════════ ⚙️ GitHub Quraşdırma Menyusu ═════════════
    def _setup_info(remote):
        is_repo = git(root, "rev-parse", "--is-inside-work-tree")[0] == 0
        rc, url, _ = git(root, "remote", "get-url", remote) if is_repo else (1, "", "")
        uname = git(root, "config", "user.name")[1] if is_repo else ""
        uemail = git(root, "config", "user.email")[1] if is_repo else ""
        return is_repo, rc, url, uname, uemail

    async def setup_view():
        remote = os.getenv("GITHUB_REMOTE", "origin")
        is_repo, rc, url, uname, uemail = await asyncio.to_thread(_setup_info, remote)
        tok = _token()

        lines = ["⚙️ <b>GitHub Hesabının Quraşdırılması</b>\n"]
        if gh_setup.get("status_msg"):
            lines.append(f"{gh_setup['status_msg']}\n")
            gh_setup["status_msg"] = ""

        lines.append(f"📁 <b>Git Repozitoriyası:</b> {'✅ Aktiv' if is_repo else '❌ Başladılmayıb'}")
        lines.append(f"🔗 <b>Remote URL ({remote}):</b> {f'<code>{escape(url)}</code>' if (rc == 0 and url) else '❌ Təyin edilməyib'}")
        masked = f"<code>{escape(tok[:4] + '***' + tok[-4:])}</code>" if len(tok) >= 8 else ("✅ Mövcuddur" if tok else "❌ Daxil edilməyib")
        lines.append(f"🔑 <b>GitHub Token (PAT):</b> {masked}")
        lines.append(f"👤 <b>Git Müəllif:</b> {escape(uname or 'Default')} &lt;{escape(uemail or 'bot@dllmaster.local')}&gt;")
        lines.append(f"🔐 <b>SSH açarı:</b> {'✅ var' if ssh_key.exists() else '❌ yoxdur'} <i>(token əvəzinə — 🔐 düyməsi)</i>\n")

        kb = []
        if not is_repo:
            kb.append([btn("🛠 Repozitoriyanı başlat (git init)", "ghs:act:init")])
        kb.append([btn("🔗 Remote URL", "ghs:ask:remote"), btn("🔑 GitHub token", "ghs:ask:token")])
        kb.append([btn("👤 Git müəllifi", "ghs:ask:user")] + ([btn("🗑 Tokeni sil", "ghs:act:del_token")] if tok else []))
        kb.append([btn("🔐 SSH açarı (botdan al)", "ghs:key")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    # ═════════════ 🔐 SSH açarı: botda yarat → public hissəni GitHub-a əlavə et → yoxla ═════════════
    async def key_view(note: str = "", confirm: str = ""):
        remote = os.getenv("GITHUB_REMOTE", "origin")
        _, rc, url, _, _ = await asyncio.to_thread(_setup_info, remote)
        have = ssh_key.exists()
        lines = ["🔐 <b>GitHub SSH açarı</b>\n"]
        if note:
            lines.append(f"{note}\n")
        if confirm == "regen":
            lines.append("♻️ <b>Yeni açar yaradılsın?</b> Köhnə açar silinir — yenisini GitHub-da yenidən əlavə etməli olacaqsınız.")
        elif confirm == "del":
            lines.append("🗑 <b>SSH açarı silinsin?</b> Bundan sonra push / pull token ilə (HTTPS) işləyəcək.")
        elif not ssh_key.available():
            lines.append("❌ <b>ssh-keygen tapılmadı.</b> openssh quraşdırın: <code>sudo pacman -S openssh</code> "
                         "(Debian / Ubuntu: <code>sudo apt install openssh-client</code>)")
        elif not have:
            lines += ["Token əvəzinə SSH açarı ilə də GitHub-a push / pull etmək olar. Açar <b>botun serverində</b> yaranır; "
                      "yalnız <b>public</b> hissəsi göstərilir (private açar heç vaxt Telegram-a göndərilmir).\n",
                      "▶️ <b>🔑 SSH açarı yarat</b> düyməsinə basın."]
        else:
            fp = ssh_key.fingerprint()
            lines += [f"✅ Açar hazırdır{f' · <code>{escape(fp)}</code>' if fp else ''}\n",
                      "<b>Public açar</b> (toxunub kopyalayın):",
                      f"<code>{escape(ssh_key.public_key())}</code>\n",
                      "<b>Addımlar:</b>",
                      "1️⃣ Açarı kopyalayın",
                      "2️⃣ <b>🔗 GitHub-da əlavə et</b> → <i>Title</i> yazın, açarı yapışdırın, <i>Add SSH key</i>",
                      "   <i>(yalnız bir repo üçün: Repo → Settings → Deploy keys → ✅ Allow write access)</i>",
                      "3️⃣ <b>🧪 Bağlantını yoxla</b>",
                      "4️⃣ Remote HTTPS-dirsə <b>🔁 Remote → SSH</b>",
                      f"\n🔗 Remote: <code>{escape(url)}</code>" if (rc == 0 and url) else "\n🔗 Remote hələ təyin edilməyib"]
        kb = []
        if confirm == "regen":
            kb.append([btn("✅ Bəli, yenidən yarat", "ghs:kact:genok"), btn("✖️ Ləğv et", "ghs:key")])
        elif confirm == "del":
            kb.append([btn("✅ Bəli, sil", "ghs:kact:delok"), btn("✖️ Ləğv et", "ghs:key")])
        elif not have:
            if ssh_key.available():
                kb.append([btn("🔑 SSH açarı yarat", "ghs:kact:genok")])
        else:
            kb.append([btn("🔗 GitHub-da əlavə et", url=ssh_key.GITHUB_NEW_KEY_URL)])
            kb.append([btn("🧪 Bağlantını yoxla", "ghs:kact:test"), btn("🔁 Remote → SSH", "ghs:kact:ssh")])
            kb.append([btn("♻️ Yenidən yarat", "ghs:kact:regen"), btn("🗑 Sil", "ghs:kact:del")])
        kb.append(nav("ghs:menu"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    @dp.callback_query(F.data.startswith("ghs:"))
    async def gh_setup_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]

        gh_setup["chat_id"] = cb.message.chat.id
        gh_setup["panel_id"] = cb.message.message_id

        if action == "menu":
            gh_setup["step"] = None
            text, kb = await setup_view()
            await edit_setup_panel(text, kb)
            await cb.answer()
            return

        if action == "key":
            text, kb = await key_view()
            await edit_setup_panel(text, kb)
            await cb.answer()
            return

        if action == "kact" and len(parts) > 2:
            sub = parts[2]
            note, confirm = "", ""
            if sub in ("regen", "del"):
                confirm = sub
            elif sub == "genok":
                ok, msg = await asyncio.to_thread(ssh_key.generate)
                note = "✅ Yeni SSH açarı yaradıldı — public hissəni GitHub-da əlavə edin" if ok else f"❌ {escape(msg)}"
            elif sub == "delok":
                await asyncio.to_thread(ssh_key.delete)
                note = "🗑 SSH açarı silindi"
            elif sub == "test":
                await cb.answer("🧪 Yoxlanılır...")
                ok, msg = await asyncio.to_thread(ssh_key.test_connection)
                note = f"{'✅' if ok else '❌'} {escape(msg)}"
            elif sub == "ssh":
                remote = os.getenv("GITHUB_REMOTE", "origin")
                rc, url, _ = (await asyncio.to_thread(git, root, "remote", "get-url", remote))
                new_url = ssh_key.to_ssh_url(url) if rc == 0 else None
                if rc != 0:
                    note = "❌ Əvvəlcə Remote URL təyin edin (⚙️ GitHub ayarları → 🔗 Remote URL)"
                elif new_url is None:
                    note = "ℹ️ Remote artıq SSH-dir və ya GitHub HTTPS ünvanı deyil"
                else:
                    rc2, _, err = await asyncio.to_thread(git, root, "remote", "set-url", remote, new_url)
                    note = f"✅ Remote SSH-ə çevrildi: <code>{escape(new_url)}</code>" if rc2 == 0 else f"❌ {escape(err)}"
            if sub != "test":
                await cb.answer()
            text, kb = await key_view(note, confirm)
            await edit_setup_panel(text, kb)
            return

        if action == "act" and len(parts) > 2:
            sub = parts[2]
            if sub == "init":
                git(root, "init")
                git(root, "branch", "-M", "main")
                gh_setup["status_msg"] = "✅ Git repozitoriyası uğurla başladıldı (branch: main)!"
                await cb.answer("Repo yaradıldı")
            elif sub == "del_token":
                await asyncio.to_thread(get_db().set_setting, "gh_token", "")
                if "GITHUB_TOKEN" in os.environ:
                    os.environ.pop("GITHUB_TOKEN", None)
                gh_setup["status_msg"] = "🗑 GitHub Token silindi."
                await cb.answer("Token silindi")
            text, kb = await setup_view()
            await edit_setup_panel(text, kb)
            return

        if action == "ask" and len(parts) > 2:
            sub = parts[2]
            gh_setup["step"] = sub
            if sub == "remote":
                await cb.answer()
                await edit_setup_panel(
                    "🔗 <b>GitHub Remote URL-i daxil edin</b>\n\n"
                    "Repozitoriyanızın linkini mesaj olaraq göndərin:\n"
                    "<code>https://github.com/istifadəçi_adı/repo_adı.git</code>\n\n"
                    "<i>İmtina üçün aşağıdakı düyməni sıxın:</i>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "ghs:menu")]]),
                )
            elif sub == "token":
                await cb.answer()
                await edit_setup_panel(
                    "🔑 <b>GitHub Personal Access Token (PAT) daxil edin</b>\n\n"
                    "Tokeni mesaj olaraq göndərin (məs: <code>ghp_...</code> və ya <code>github_pat_...</code>).\n\n"
                    "<i>ℹ️ Token verilənlər bazasında saxlanılacaq və çatdan dərhal silinəcək. "
                    "Token əvəzinə 🔐 SSH açarı da işlədə bilərsiniz.</i>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("🔑 Token yarat (GitHub)", url=ssh_key.GITHUB_NEW_TOKEN_URL)],
                                                          [btn("✖️ Ləğv et", "ghs:menu")]]),
                )
            elif sub == "user":
                await cb.answer()
                await edit_setup_panel(
                    "👤 <b>Git Müəllif məlumatlarını daxil edin</b>\n\n"
                    "Format: <code>Ad Soyad | email@example.com</code>\n"
                    "Məsələn: <code>DLLMaster Bot | bot@dllmaster.local</code>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "ghs:menu")]]),
                )
            return

        await cb.answer()

    # ═════════════ hesabat düymələri (gh:) ═════════════
    @dp.callback_query(F.data.startswith("gh:"))
    async def gh_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        if action == "noop":
            await cb.answer()
            return

        pid = parts[2] if len(parts) > 2 else ""
        p = await asyncio.to_thread(load_pending)
        if not p or p["id"] != pid:
            await cb.answer("Bu yeniləmə artıq köhnəlib və ya bağlanıb", show_alert=True)
            try:
                await cb.message.edit_reply_markup(reply_markup=status_kb("⌛ Köhnəlib"))
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1175]: %r", _e)
            return

        if action == "skip":
            gh_state["await_msg"] = None
            await asyncio.to_thread(save_pending, None)
            await cb.answer("İmtina edildi")
            await set_report_kb(p, status_kb("❌ GitHub-a göndərilmədi"))
            if cb.message.message_id != p["message_id"]:
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1187]: %r", _e)
            return

        if action == "msg":
            gh_state["await_msg"] = pid
            await cb.answer()
            await bot.send_message(
                creator,
                "✏️ <b>Commit mesajını yazın</b>\n\n"
                f"Hazırkı:\n<pre>{escape(p['message'])}</pre>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ Olduğu kimi saxla", callback_data=f"gh:keep:{pid}")
                ]]),
            )
            return

        if action == "keep":
            gh_state["await_msg"] = None
            await cb.answer()
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1210]: %r", _e)
            return

        if action == "push":
            if push_lock.locked():
                await cb.answer("Artıq göndərilir...")
                return
            await cb.answer("Göndərilir...")
            gh_state["await_msg"] = None
            async with push_lock:
                await set_report_kb(p, status_kb("⏳ GitHub-a göndərilir..."))
                paths = p["files"].get("changed", []) + p["files"].get("added", []) + p["files"].get("removed", [])
                try:
                    res = await asyncio.to_thread(push_update, root, paths, p["message"])
                except Exception as e:
                    logger.error(f"GitHub push xətası: {e}", exc_info=True)
                    res = {"ok": False, "committed": False, "error": redact(str(e)), "hint": ""}

                if res["ok"]:
                    await asyncio.to_thread(save_pending, None)
                    await set_report_kb(p, status_kb(f"✅ GitHub-da: {res['hash']}", res.get("url")))
                    lines = [
                        "✅ <b>GitHub-a göndərildi</b>",
                        f"🌿 Branch: <code>{escape(res['branch'])}</code> · commit <code>{escape(res['hash'])}</code>",
                        f"📁 Fayl: <b>{len(res['files'])}</b>"
                        + ("" if res["committed"] else " <i>(yeni commit lazım olmadı, yalnız push edildi)</i>"),
                    ]
                    if res.get("others"):
                        lines.append(f"ℹ️ <i>Repoda commit edilməmiş başqa {res['others']} dəyişiklik də var "
                                     "(hesabatda olmadığı üçün toxunulmadı).</i>")
                    arc = getattr(context, "archive_after_push", None)        # 📦 archive_plugin
                    if arc:
                        try:
                            arc_res = await arc(res)
                        except Exception as e:
                            arc_res = f"❌ {str(e)[:150]}"
                        if arc_res:
                            lines.append(f"📦 Arxiv: {escape(arc_res)}")
                    await bot.send_message(
                        creator, "\n".join(lines), parse_mode="HTML",
                        reply_markup=status_kb("🔗 Commit-ə bax", res["url"]) if res.get("url") else None,
                    )
                else:
                    await set_report_kb(p, gh_kb(p["id"], configured=False))
                    note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı.</i>" if res["committed"] else ""
                    hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                    await bot.send_message(
                        creator,
                        f"❌ <b>GitHub-a göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("⚙️ GitHub ayarları", "ghs:menu")]]),
                    )
            return

        await cb.answer()

    # ═════════════ mətn daxiletmələri (commit mesajı və quraşdırma) ═════════════
    async def waiting_commit_msg(message: Message) -> bool:
        return bool(gh_state["await_msg"] or gh_full["await"] or gh_setup["step"]) and message.from_user \
            and message.from_user.id == creator

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_commit_msg)
    async def gh_text_input_handler(message: Message):
        # ⚙️ Quraşdırma addımları
        if gh_setup["step"]:
            step = gh_setup["step"]
            txt = message.text.strip()
            try:
                await message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1280]: %r", _e)

            if step == "remote":
                gh_setup["step"] = None
                if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
                    git(root, "init")
                    git(root, "branch", "-M", "main")
                remote = os.getenv("GITHUB_REMOTE", "origin")
                rc, _, _ = git(root, "remote", "get-url", remote)
                if rc == 0:
                    rc2, _, err = git(root, "remote", "set-url", remote, txt)
                else:
                    rc2, _, err = git(root, "remote", "add", remote, txt)
                if rc2 == 0:
                    gh_setup["status_msg"] = f"✅ Remote URL təyin edildi: <code>{escape(txt)}</code>"
                else:
                    gh_setup["status_msg"] = f"❌ Remote xətası: <code>{escape(err)}</code>"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

            if step == "token":
                gh_setup["step"] = None
                try:
                    await asyncio.to_thread(get_db().set_setting, "gh_token", txt)
                    os.environ["GITHUB_TOKEN"] = txt
                    gh_setup["status_msg"] = "✅ GitHub Token saxlanıldı!"
                except Exception as e:
                    gh_setup["status_msg"] = f"❌ Token saxlanılmadı: {escape(str(e))}"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

            if step == "user":
                gh_setup["step"] = None
                if "|" in txt:
                    uname, uemail = [x.strip() for x in txt.split("|", 1)]
                else:
                    uname, uemail = txt, "bot@dllmaster.local"
                if git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
                    git(root, "init")
                    git(root, "branch", "-M", "main")
                git(root, "config", "user.name", uname)
                git(root, "config", "user.email", uemail)
                gh_setup["status_msg"] = f"✅ Müəllif qeyd edildi: <code>{escape(uname)} &lt;{escape(uemail)}&gt;</code>"
                text, kb = await setup_view()
                await edit_setup_panel(text, kb)
                return

        # 📦 Full commit mesajı
        if gh_full["await"]:
            gh_full["await"] = False
            gh_full["message"] = message.text.strip()[:2000]
            try:
                await message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1336]: %r", _e)
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        # Hesabat commit mesajı
        pid = gh_state["await_msg"]
        gh_state["await_msg"] = None
        p = await asyncio.to_thread(load_pending)
        if not p or p["id"] != pid:
            await message.reply("⌛ Bu yeniləmə artıq bağlanıb.")
            return
        p["message"] = message.text.strip()[:2000]
        await asyncio.to_thread(save_pending, p)
        await message.reply(
            f"✏️ <b>Commit mesajı yeniləndi:</b>\n<pre>{escape(p['message'])}</pre>",
            parse_mode="HTML",
            reply_markup=gh_kb(pid),
        )

    # ═════════════ 📦 Full update & Seçim Panelləri ═════════════
    def changes_block(changes, excluded, limit=25, skipped=0, title="Dəyişikliklər") -> str:
        counts = {}
        for kind, _ in changes:
            counts[kind] = counts.get(kind, 0) + 1
        summary = " · ".join(f"{STATUS_ICONS[k]} {v}" for k, v in counts.items()) or "—"
        lines = [f"📁 <b>{title}:</b> {len(changes)} ({summary})"]
        for kind, path in changes[:limit]:
            lock = " 🔒" if is_sensitive(path) else ""
            lines.append(f"   {STATUS_ICONS[kind]}{lock} <code>{escape(path)}</code>")
        if len(changes) > limit:
            lines.append(f"   <i>… və daha {len(changes) - limit} fayl</i>")
        if excluded:
            lines.append(f"\n🔒 <b>Göndərilməyəcək (həssas):</b> {len(excluded)}")
            lines += [f"   🔒 <code>{escape(p)}</code>" for _, p in excluded[:10]]
        if skipped:
            lines.append(f"⏭ <i>Seçilmədiyi üçün göndərilməyəcək: {skipped} fayl</i>")
        return "\n".join(lines)

    PAGE = 8
    PAGE_F = 6

    def effective_selection(info) -> set:
        if gh_full["selected"] is None:
            return {p for _, p in info["changes"]}
        every = {p for _, p in info["changes"]} | {p for _, p in info["excluded"]}
        return gh_full["selected"] & every

    def short_path(path: str, n=30) -> str:
        return path if len(path) <= n else "…" + path[-(n - 1):]

    # ── 📁 Qovluqlar paneli ──
    async def folders_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])

        all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
        sel = effective_selection(info)
        gh_full["selected"] = set(sel)

        f_map = {}
        for item in all_items:
            f_map.setdefault(folder_of(item[1]), []).append(item)

        folders = sorted(f_map.keys())
        gh_full["folders"] = folders

        pages = max(1, -(-len(folders) // PAGE_F))
        page = min(max(gh_full.get("folder_page", 0), 0), pages - 1)
        gh_full["folder_page"] = page

        lines = [
            "📁 <b>Qovluqlar üzrə seçim</b>\n",
            "Qovluğun adına basaraq daxilindəki bütün faylları seçə / ləğv edə, "
            "<b>[Aç]</b> düyməsi ilə yalnız həmin qovluğu nəzərdən keçirə bilərsiniz.\n",
            f"Ümumi seçilib: <b>{len(sel)}</b> / {len(all_items)} fayl\n"
        ]

        kb = []
        chunk = folders[page * PAGE_F:(page + 1) * PAGE_F]
        for idx, f in enumerate(chunk, start=page * PAGE_F):
            f_items = f_map[f]
            tot = len(f_items)
            chosen = sum(1 for _, p, _ in f_items if p in sel)
            if chosen == tot and tot > 0:
                mark = "✅"
            elif chosen > 0:
                mark = "🔲"
            else:
                mark = "⬜"

            lines.append(f"{mark} <code>{escape(f)}</code> — <b>{chosen}/{tot}</b> fayl")
            kb.append([
                btn(f"{mark} {f} ({chosen}/{tot})", f"ghf:ftog:{idx}"),
                btn("📂 Aç", f"ghf:fset:{idx}"),
            ])

        if pages > 1:
            kb.append([btn(ui.PREV, f"ghf:fpg:{(page - 1) % pages}"),
                       btn(f"{page + 1}/{pages}", "ghf:noop"),
                       btn(ui.NEXT, f"ghf:fpg:{(page + 1) % pages}")])

        kb.append([btn("✅ Hamısı (həssassız)", "ghf:fall"), btn("▫️ Heç biri", "ghf:fnone")])
        kb.append(ui.nav("ghf:pick", f"Fayllar ({len(sel)})"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    # ── ☑️ Fayl seçimi paneli ──
    async def pick_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])

        all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
        sel = effective_selection(info)
        gh_full["selected"] = set(sel)

        f_map = {}
        for item in all_items:
            f_map.setdefault(folder_of(item[1]), []).append(item)
        gh_full["folders"] = sorted(f_map.keys())

        filt = gh_full.get("folder_filter")
        if filt and filt in f_map:
            items = f_map[filt]
        else:
            gh_full["folder_filter"] = None
            items = all_items

        gh_full["items"] = items
        pages = max(1, -(-len(items) // PAGE))
        page = min(max(gh_full["page"], 0), pages - 1)
        gh_full["page"] = page

        n_sens = sum(1 for _, p, s in all_items if s and p in sel)
        lines = ["☑️ <b>Göndəriləcək faylları seçin</b>\n"]
        if filt:
            lines.append(f"📁 Qovluq: <code>{escape(filt)}</code> ({len(items)} fayl)")
        lines.append(f"Seçilib: <b>{len(sel)}</b> / {len(all_items)}"
                     + (f" · 🚨 həssas: <b>{n_sens}</b>" if n_sens else ""))

        if not items:
            lines.append("\n✅ <i>Commit edilməmiş dəyişiklik yoxdur.</i>")

        kb = []
        chunk = items[page * PAGE:(page + 1) * PAGE]
        if chunk:
            lines.append("")
        for i, (kind, path, sens) in enumerate(chunk, start=page * PAGE):
            mark = "✅" if path in sel else "▫️"
            icon = "🔒" if sens else STATUS_ICONS[kind]
            lines.append(f"{mark} {icon} <code>{escape(path)}</code>")
            kb.append([btn(f"{mark} {icon} {short_path(path)}", f"ghf:t:{i}")])

        if any(s for _, _, s in items):
            lines.append("\n<i>🔒 = həssas fayl. Default seçilmir — seçsəniz repoya düşəcək.</i>")

        if pages > 1:
            kb.append([btn(ui.PREV, f"ghf:pg:{(page - 1) % pages}"),
                       btn(f"{page + 1}/{pages}", "ghf:noop"),
                       btn(ui.NEXT, f"ghf:pg:{(page + 1) % pages}")])

        f_btn_text = f"📁 Qovluqlar ({len(gh_full['folders'])})"
        row_folders = [btn(f_btn_text, "ghf:fview")]
        if filt:
            row_folders.append(btn("🌐 Bütün fayllar", "ghf:fclr"))
        kb.append(row_folders)

        if items:
            all_lbl = "✅ Hamısı" if not filt else f"✅ {filt} hamısı"
            none_lbl = "▫️ Heç biri" if not filt else f"▫️ {filt} heç biri"
            kb.append([btn(all_lbl, "ghf:all"), btn(none_lbl, "ghf:none")])

        kb.append([btn(f"➡️ Davam et ({len(sel)})", "ghf:back")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    async def status_view(fetch: bool = False):
        info = await asyncio.to_thread(repo_status, root, fetch)
        lines = ["🌿 <b>GitHub</b>\n"]
        kb = []

        if not info.get("is_repo"):
            lines.append("⚠️ <b>Git repozitoriyası başladılmayıb!</b>\n"
                         "Layihəni GitHub ilə sinxronlaşdırmaq üçün əvvəlcə quraşdırmanı tamamlayın.")
            kb.append([btn("⚙️ GitHub ayarları", "ghs:menu")])
            kb.append(nav("menu:dev"))
            return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

        repo = f'<a href="{info["web"]}">{escape(info["web"].replace("https://github.com/", ""))}</a>' \
            if info.get("web") else escape(info.get("remote_url") or "remote yoxdur")
        lines.append(f"📦 Repo: {repo}")
        lines.append(f"🌿 Branch: <code>{escape(info.get('branch', '—'))}</code>")

        if not info.get("remote_url"):
            lines.append("\n⚠️ <b>Remote URL təyin edilməyib!</b>")
        elif not info.get("has_token") and info.get("remote_url", "").startswith("https://"):
            lines.append("\n⚠️ <b>GitHub Token daxil edilməyib (PAT)!</b>")

        if info.get("last"):
            l = info["last"]
            lines.append(f"🕒 Son commit: <code>{escape(l['hash'])}</code> — {escape(l['subject'][:60])} "
                         f"<i>({fmt_time(l['ts'])})</i>")
        if "ahead" in info:
            sync = []
            if info["ahead"]:
                sync.append(f"⬆️ {info['ahead']} commit göndərilməyib")
            if info["behind"]:
                sync.append(f"⬇️ GitHub-da {info['behind']} yeni commit var")
            lines.append("🔄 " + (" · ".join(sync) if sync else "GitHub ilə sinxrondur"))
        else:
            lines.append("🔄 <i>Upstream branch qurulmayıb — ilk push onu yaradacaq</i>")
        if info.get("fetch_error"):
            lines.append(f"⚠️ fetch alınmadı: <code>{escape(info['fetch_error'][:150])}</code>")
        lines.append("")
        lines.append(changes_block(info.get("changes", []), info.get("excluded", []), limit=15))
        if not info.get("changes") and not info.get("ahead"):
            lines.append("\n✅ <i>Göndəriləcək heç nə yoxdur.</i>")

        if info.get("changes") or info.get("ahead"):
            kb.append([btn("📦 Hamısını göndər", "ghf:prep")])
        if info.get("changes") or info.get("excluded"):
            kb.append([btn("☑️ Seçərək göndər", "ghf:pick")])
        kb.append([btn("📡 GitHub-la yoxla", "ghf:fetch")])
        if info.get("remote_url"):
            behind = f" ({info['behind']})" if info.get("behind") else ""
            kb.append([btn(f"⬇️ GitHub-dan yenilə{behind}", "ghu:check")])
        kb.append([btn("⚙️ GitHub ayarları", "ghs:menu")])
        kb.append(nav("menu:dev", refresh="ghf:open"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)

    async def full_confirm_view():
        info = await asyncio.to_thread(repo_status, root, False)
        if info.get("error"):
            return f"❌ {escape(info['error'])}", InlineKeyboardMarkup(inline_keyboard=[nav("ghf:open")])
        sel = effective_selection(info)
        everything = info["changes"] + info["excluded"]
        changes = [(k, p) for k, p in everything if p in sel]
        excluded = [(k, p) for k, p in info["excluded"] if p not in sel]
        skipped = len({p for _, p in info["changes"] if p not in sel})
        sens_sent = [p for _, p in changes if is_sensitive(p)]
        custom = gh_full["selected"] is not None
        message = gh_full["message"] or full_commit_message(
            changes, time.time(), "Seçilmiş yeniləmə" if custom else "Tam yeniləmə")
        lines = [("☑️ <b>Seçilən fayllar — GitHub-a göndərilsin?</b>\n" if custom
                  else "📦 <b>Tam yeniləmə — GitHub-a göndərilsin?</b>\n"),
                 f"🌿 <code>{escape(info.get('branch', '—'))}</code> → <code>{escape(info.get('remote', 'origin'))}</code>"]
        if info.get("ahead"):
            lines.append(f"⬆️ Əvvəldən göndərilməmiş {info['ahead']} commit də push olunacaq")
        if info.get("behind"):
            lines.append(f"⚠️ GitHub-da sizdə olmayan {info['behind']} commit var — push rədd oluna bilər")
        lines.append("")
        lines.append(changes_block(changes, excluded, skipped=skipped, title="Göndəriləcək"))
        if sens_sent:
            lines.append(f"\n🚨 <b>Diqqət: {len(sens_sent)} həssas fayl da göndəriləcək!</b>")
        lines.append(f"\n📝 <b>Commit mesajı:</b>\n<pre>{escape(message[:600])}</pre>")
        kb = []
        if changes or info.get("ahead"):
            kb.append([btn("✅ Təsdiqlə və göndər", "ghf:go")])
        if everything:
            kb.append([btn(f"☑️ Faylları seç ({len(changes)}/{len({p for _, p in everything})})", "ghf:pick")])
        if changes or info.get("ahead"):
            kb.append([btn("✏️ Commit mesajı", "ghf:msg")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    @dp.callback_query(F.data.startswith("ghf:"))
    async def gh_full_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]
        new_msg = len(parts) > 2 and parts[2] == "new"

        if action in ("open", "fetch"):
            await cb.answer("GitHub yoxlanılır..." if action == "fetch" else None)
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            gh_full["await"] = False
            text, kb = await status_view(fetch=(action == "fetch"))
            await edit_full_panel(text, kb)
            return

        if action == "prep":
            await cb.answer()
            gh_full["message"] = None
            gh_full["selected"] = None
            gh_full["await"] = False
            if new_msg:
                sent = await bot.send_message(creator, "⏳ <i>Hazırlanır...</i>", parse_mode="HTML")
                gh_full.update(chat_id=sent.chat.id if getattr(sent, "chat", None) else creator,
                               panel_id=sent.message_id)
            else:
                gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        if action == "noop":
            await cb.answer()
            return

        if action == "pick":
            await cb.answer()
            gh_full["await"] = False
            gh_full["page"] = 0
            if new_msg:
                sent = await bot.send_message(creator, "⏳ <i>Hazırlanır...</i>", parse_mode="HTML")
                gh_full.update(chat_id=sent.chat.id if getattr(sent, "chat", None) else creator,
                               panel_id=sent.message_id)
                gh_full["message"] = None
                p = await asyncio.to_thread(load_pending)
                gh_full["selected"] = (
                    {x for k in ("changed", "added", "removed") for x in p["files"].get(k, [])} if p else None
                )
            else:
                gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        # ── Qovluq paneli və filtrlər ──
        if action == "fview":
            await cb.answer()
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        if action == "fclr":
            await cb.answer("Filtr sıfırlandı")
            gh_full["folder_filter"] = None
            gh_full["page"] = 0
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "fset" and len(parts) > 2:
            try:
                f_name = gh_full["folders"][int(parts[2])]
            except (IndexError, ValueError):
                await cb.answer("Məlumat köhnəlib")
                return
            gh_full["folder_filter"] = f_name
            gh_full["page"] = 0
            await cb.answer(f"Qovluq: {f_name}")
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "fpg" and len(parts) > 2:
            try:
                gh_full["folder_page"] = int(parts[2])
            except (IndexError, ValueError):
                pass
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            await cb.answer()
            return

        if action == "ftog" and len(parts) > 2:
            try:
                folder_name = gh_full["folders"][int(parts[2])]
            except (IndexError, ValueError):
                await cb.answer("Məlumat köhnəlib")
                return
            info = await asyncio.to_thread(repo_status, root, False)
            all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
            f_items = [item for item in all_items if folder_of(item[1]) == folder_name]
            sel = gh_full["selected"] if gh_full["selected"] is not None else set()
            chosen_in_f = [p for _, p, _ in f_items if p in sel]

            alert = None
            if len(chosen_in_f) == len(f_items) and len(f_items) > 0:
                for _, p, _ in f_items:
                    sel.discard(p)
            else:
                non_sens = [p for _, p, s in f_items if not s]
                if not all(p in sel for p in non_sens) and non_sens:
                    sel.update(non_sens)
                else:
                    has_sens = False
                    for _, p, s in f_items:
                        sel.add(p)
                        if s:
                            has_sens = True
                    if has_sens:
                        alert = f"⚠️ {folder_name} qovluğundakı həssas fayllar da seçildi!"
            gh_full["selected"] = sel
            await cb.answer(alert, show_alert=bool(alert))
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        if action in ("fall", "fnone"):
            info = await asyncio.to_thread(repo_status, root, False)
            all_items = [(k, p, False) for k, p in info["changes"]] + [(k, p, True) for k, p in info["excluded"]]
            if action == "fall":
                gh_full["selected"] = {p for _, p, s in all_items if not s}
            else:
                gh_full["selected"] = set()
            await cb.answer()
            text, kb = await folders_view()
            await edit_full_panel(text, kb)
            return

        # ── Tək-tək fayl seçimi və səhifələmə ──
        if action in ("t", "pg", "all", "none"):
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            items = gh_full["items"]
            sel = gh_full["selected"] if gh_full["selected"] is not None else set()
            alert = None
            if action == "t":
                try:
                    kind, path, sens = items[int(parts[2])]
                except (IndexError, ValueError):
                    await cb.answer("Siyahı köhnəlib, yeniləndi")
                    text, kb = await pick_view()
                    await edit_full_panel(text, kb)
                    return
                if path in sel:
                    sel.discard(path)
                else:
                    sel.add(path)
                    if sens:
                        alert = (f"⚠️ {path} həssas fayldır!\n\nİçində token və ya şəxsi məlumat "
                                 "ola bilər. Göndərsəniz repoya düşəcək.")
            elif action == "pg":
                try:
                    gh_full["page"] = int(parts[2])
                except (IndexError, ValueError):
                    pass
            elif action == "all":
                sel |= {p for _, p, s in items if not s}
            elif action == "none":
                for _, p, _ in items:
                    sel.discard(p)
            gh_full["selected"] = sel
            await cb.answer(alert, show_alert=bool(alert))
            text, kb = await pick_view()
            await edit_full_panel(text, kb)
            return

        if action == "msg":
            await cb.answer()
            gh_full["await"] = True
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await edit_full_panel(
                "✏️ <b>Commit mesajını yazın</b>\n\n"
                + (f"Hazırkı:\n<pre>{escape(gh_full['message'][:600])}</pre>" if gh_full["message"]
                 else "<i>Hazırda avtomatik mesaj istifadə olunur (seçilən fayllardan yaradılır).</i>"),
                InlineKeyboardMarkup(inline_keyboard=[nav("ghf:back")]),
            )
            return

        if action == "back":
            await cb.answer()
            gh_full["await"] = False
            text, kb = await full_confirm_view()
            await edit_full_panel(text, kb)
            return

        if action == "go":
            if push_lock.locked():
                await cb.answer("Artıq göndərilir...")
                return
            await cb.answer("Göndərilir...")
            gh_full["await"] = False
            gh_full.update(chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            message = gh_full["message"]
            selected = set(gh_full["selected"]) if gh_full["selected"] is not None else None
            async with push_lock:
                await edit_full_panel("⏳ <i>GitHub-a göndərilir...</i>", None)
                try:
                    res = await asyncio.to_thread(push_all, root, message, selected)
                except Exception as e:
                    logger.error(f"Full push xətası: {e}", exc_info=True)
                    res = {"ok": False, "committed": False, "error": redact(str(e)), "hint": "", "excluded": []}

            if res["ok"]:
                gh_full["message"] = None
                gh_full["selected"] = None
                p = await asyncio.to_thread(load_pending)
                if p:
                    pend = {x for k in ("changed", "added", "removed") for x in p["files"].get(k, [])}
                    if not pend & set(res.get("remaining", [])):
                        await asyncio.to_thread(save_pending, None)
                        await set_report_kb(p, status_kb(f"✅ GitHub-da: {res['hash']}", res.get("url")))
                lines = [
                    ("✅ <b>Seçilən fayllar GitHub-a göndərildi</b>\n" if selected is not None
                     else "✅ <b>Tam yeniləmə GitHub-a göndərildi</b>\n"),
                    f"🌿 <code>{escape(res['branch'])}</code> · commit <code>{escape(res['hash'])}</code>",
                    f"📁 {len(res.get('files', []))} fayl"
                    + ("" if res["committed"] else " <i>(yeni commit lazım olmadı, yalnız push edildi)</i>"),
                ]
                if res.get("sensitive_sent"):
                    lines.append(f"🚨 {len(res['sensitive_sent'])} həssas fayl da göndərildi")
                if res.get("excluded"):
                    lines.append(f"🔒 {len(res['excluded'])} həssas fayl göndərilmədi")
                other = res.get("skipped", 0) - len(res.get("excluded", []))
                if other > 0:
                    lines.append(f"⏭ {other} fayl seçilmədiyi üçün lokalda qaldı")
                arc = getattr(context, "archive_after_push", None)        # 📦 archive_plugin
                if arc:
                    try:
                        arc_res = await arc(res)
                    except Exception as e:
                        arc_res = f"❌ {str(e)[:150]}"
                    if arc_res:
                        lines.append(f"📦 Arxiv: {escape(arc_res)}")
                kb = []
                if res.get("url"):
                    kb.append([btn("🔗 Commit-ə bax", url=res["url"])])
                kb.append(nav("ghf:open"))
                await edit_full_panel("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb))
                logger.info(f"📦 Full push: {res['hash']} ({len(res.get('files', []))} fayl)")
            else:
                note = "\n\n<i>Commit lokal olaraq yaradıldı, yalnız push alınmadı.</i>" if res.get("committed") else ""
                hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                await edit_full_panel(
                    f"❌ <b>Göndərilmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}{note}",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [btn("🔁 Yenidən cəhd et", "ghf:back")],
                        [btn("⚙️ GitHub ayarları", "ghs:menu")],
                        nav("ghf:open")
                    ]),
                )
            return

        await cb.answer()

    # ═════════════ ⬇️ GitHub-dan yeniləmə (ghu:) ═════════════
    def load_json(key):
        try:
            return json.loads(get_db().get_setting(key) or "null")
        except ValueError:
            return None

    def save_json(key, value):
        get_db().set_setting(key, json.dumps(value) if value else None)

    async def edit_msg(msg, text: str, kb):
        await ui.show(msg, text, kb)

    async def restart_btn(label: str = "🔁 Botu yenidən başlat"):
        # birdəfəlik açar: restartdan sonra eyni callback təkrar gəlsə, bot ikinci dəfə restart olmasın
        nonce = os.urandom(4).hex()
        await asyncio.to_thread(get_db().set_setting, "gh_restart_nonce", nonce)
        return btn(label, f"ghu:restart:{nonce}")

    def updates_kb(info: dict) -> InlineKeyboardMarkup:
        kb = []
        if info.get("ok") and info.get("behind"):
            mode = ("s" if info["conflicts"] else "") + ("r" if info["ahead"] else "f")
            label = {"f": "⬇️ Yenilə (pull)", "r": "🔀 Rebase edib yenilə",
                     "sf": "🗄 Stash et və yenilə", "sr": "🗄 Stash + rebase edib yenilə"}[mode]
            kb.append([btn(label, f"ghu:pull:{mode}")])
        row = [btn("🔄 Yenilə", "ghu:check")]
        if info.get("ok") and info.get("behind") and info.get("web"):
            row.append(btn("🔗 Müqayisə", url=f"{info['web']}/compare/{info['head']}...{info['remote_head']}"))
        kb.append(row)
        if not info.get("ok"):
            kb.append([btn("⚙️ GitHub ayarları", "ghs:menu")])
        kb.append(nav("ghf:open"))
        return InlineKeyboardMarkup(inline_keyboard=kb)

    async def updates_view(fetch: bool = True):
        if push_lock.locked():
            return ("⏳ <i>Başqa git əməliyyatı gedir, bir az sonra yenidən yoxlayın.</i>",
                    InlineKeyboardMarkup(inline_keyboard=[[btn("🔄 Yenilə", "ghu:check")], nav("ghf:open")]))
        async with push_lock:
            info = await asyncio.to_thread(remote_updates, root, fetch)
        head = "⬇️ <b>GitHub yeniləmələri</b>\n\n"
        if not info["ok"]:
            hint = f"\n\n💡 {info['hint']}" if info.get("hint") else ""
            return head + f"❌ <code>{escape(info['error'])}</code>{hint}", updates_kb(info)
        return (head + incoming_text(info))[:4000], updates_kb(info)

    async def pull_result_view(res: dict, title: str, allow_rollback: bool = True):
        lines = [title,
                 f"🌿 <code>{escape(res['old'][:7])}</code> → <code>{escape(res['new'][:7])}</code>"
                 + (f" · {res['count']} commit" if res.get("count") else ""),
                 f"📁 {len(res.get('files', []))} fayl dəyişdi"]
        if res.get("stashed"):
            lines.append(f"🗄 Lokal dəyişikliklər saxlanıldı: <code>{escape(res.get('stash_ref', ''))}</code>\n"
                         "   <i>Geri qaytarmaq üçün serverdə:</i> <code>git stash pop</code>")
        if res.get("deps"):
            lines.append("📦 Asılılıqlar dəyişib: " + ", ".join(f"<code>{escape(p)}</code>" for p in res["deps"]))
        if res.get("errors"):
            lines.append("\n🚨 <b>Sintaksis xətaları — yenidən başlatmaq tövsiyə olunmur:</b>")
            lines += [f"❌ <code>{escape(p)}</code> — <i>{escape(e)}</i>" for p, e in res["errors"][:8]]
        lines.append("\nℹ️ <i>Yeni kod bot yenidən başlayandan sonra işə düşəcək.</i>")
        kb = [[await restart_btn("⚠️ Yenə də yenidən başlat" if res.get("errors") else "🔁 Botu yenidən başlat")]]
        if "requirements.txt" in {Path(p).name for p in res.get("deps", [])}:
            kb.append([btn("📦 pip install -r requirements.txt", "ghu:pip")])
        if allow_rollback:
            kb.append([btn("⏪ Geri qaytar", "ghu:rollback")])
        kb.append(nav("ghf:open"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=kb)

    @dp.callback_query(F.data.startswith("ghu:"))
    async def gh_update_callback(cb: CallbackQuery):
        if cb.from_user.id != creator:
            await cb.answer("⛔ İcazə yoxdur", show_alert=True)
            return
        parts = cb.data.split(":")
        action = parts[1]

        if action == "check":
            await cb.answer("GitHub yoxlanılır...")
            await edit_msg(cb.message, "⏳ <i>GitHub yoxlanılır...</i>", None)
            text, kb = await updates_view(fetch=True)
            await edit_msg(cb.message, text, kb)
            return

        if action == "mute":
            await cb.answer("Bu yeniləmə üçün bir daha xatırladılmayacaq")
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/update_notifier_plugin.py:1961]: %r", _e)
            return

        if action in ("pull", "rollback", "pip") and push_lock.locked():
            await cb.answer("Başqa git əməliyyatı gedir...", show_alert=True)
            return

        if action == "pull":
            mode = parts[2] if len(parts) > 2 else "f"
            await cb.answer("Yenilənir...")
            async with push_lock:
                await edit_msg(cb.message, "⏳ <i>GitHub-dan yenilənir...</i>", None)
                try:
                    res = await asyncio.to_thread(pull_from_github, root, "s" in mode, "r" in mode)
                except Exception as e:
                    logger.error(f"GitHub pull xətası: {e}", exc_info=True)
                    res = {"ok": False, "error": redact(str(e)), "hint": ""}
            if res["ok"]:
                await asyncio.to_thread(save_json, "gh_pull_last", {"old": res["old"], "new": res["new"]})
                await asyncio.to_thread(save_json, "gh_pulled",
                                        {"from": res["old"][:7], "to": res["new"][:7], "n": res.get("count", 0)})
                text, kb = await pull_result_view(res, "✅ <b>GitHub-dan yeniləndi</b>")
                logger.info(f"⬇️ GitHub pull: {res['old'][:7]} → {res['new'][:7]} ({len(res['files'])} fayl)")
            else:
                hint = f"\n\n💡 {res['hint']}" if res.get("hint") else ""
                text = f"❌ <b>Yenilənmədi</b>\n\n<code>{escape(res['error'])}</code>{hint}"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [btn("🔄 Yenilə", "ghu:check")], [btn("⚙️ GitHub ayarları", "ghs:menu")], nav("ghf:open")])
            await edit_msg(cb.message, text, kb)
            return

        if action == "rollback":
            last = await asyncio.to_thread(load_json, "gh_pull_last")
            if not last:
                await cb.answer("Geri qaytarılacaq yeniləmə yoxdur", show_alert=True)
                return
            async with push_lock:
                res = await asyncio.to_thread(rollback_pull, root, last["old"], last["new"])
            if not res["ok"]:
                await cb.answer(res["error"][:190], show_alert=True)
                return
            await cb.answer("Geri qaytarıldı")
            await asyncio.to_thread(save_json, "gh_pull_last", None)
            await asyncio.to_thread(save_json, "gh_pulled",
                                    {"from": last["new"][:7], "to": last["old"][:7], "rollback": True})
            changed = await asyncio.to_thread(
                lambda: [p for p in git(root, "diff", "--name-only", "-z", last["old"], last["new"], raw=True)[1]
                         .split("\0") if p])
            text, kb = await pull_result_view(
                {"old": last["new"], "new": last["old"], "files": changed},
                "⏪ <b>Əvvəlki versiyaya qaytarıldı</b>", allow_rollback=False)
            await edit_msg(cb.message, text, kb)
            return

        if action == "pip":
            await cb.answer("Quraşdırılır...")
            async with push_lock:
                await edit_msg(cb.message, "⏳ <i>pip install -r requirements.txt ...</i>", None)
                ok, out = await asyncio.to_thread(install_requirements, root)
            text = ("✅ <b>Asılılıqlar quraşdırıldı</b>" if ok else "❌ <b>pip xətası</b>") + \
                f"\n\n<pre>{escape(redact(out))}</pre>"
            kb = InlineKeyboardMarkup(inline_keyboard=[[await restart_btn()], nav("ghf:open")])
            await edit_msg(cb.message, text, kb)
            return

        if action == "restart":
            nonce = parts[2] if len(parts) > 2 else ""
            stored = await asyncio.to_thread(get_db().get_setting, "gh_restart_nonce")
            if not nonce or stored != nonce:
                await cb.answer("Bu düymə köhnəlib", show_alert=True)
                return
            await asyncio.to_thread(get_db().set_setting, "gh_restart_nonce", None)
            await cb.answer("Yenidən başladılır...")
            await edit_msg(cb.message, "🔁 <b>Bot yenidən başladılır...</b>\n"
                                       "<i>Bir neçə saniyəyə yeniləmə hesabatı gələcək.</i>", None)
            logger.info("🔁 Creator əmri ilə bot yenidən başladılır")
            await asyncio.sleep(2)  # polling callback-i təsdiqləsin
            from bot.plugins.restart_plugin import restart
            await restart(context)                 # asistan / userbot / işçilər düzgün bağlanır
            return

        await cb.answer()

    async def periodic_remote_check():
        try:
            minutes = float(os.getenv("GITHUB_CHECK_INTERVAL", "60"))
        except ValueError:
            minutes = 60
        if minutes <= 0 or not creator:
            return
        await asyncio.sleep(DELAY_AFTER_START + 60)
        while True:
            try:
                if not push_lock.locked():
                    async with push_lock:
                        info = await asyncio.to_thread(remote_updates, root, True)
                    if info["ok"] and info["behind"]:
                        seen = await asyncio.to_thread(get_db().get_setting, "gh_notified_remote")
                        if seen != info["remote_head"]:
                            text = "🔔 <b>GitHub-da yeniləmə var</b>\n\n" + incoming_text(info, compact=True)
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [btn("⬇️ Bax və yenilə", "ghu:check")],
                                [btn("🔕 Bu dəfəlik keç", "ghu:mute")],
                            ])
                            await bot.send_message(creator, text[:4000], parse_mode="HTML",
                                                   reply_markup=kb, disable_web_page_preview=True)
                            await asyncio.to_thread(get_db().set_setting, "gh_notified_remote", info["remote_head"])
                    elif not info["ok"]:
                        logger.debug(f"GitHub yoxlaması: {info['error']}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"GitHub avtomatik yoxlama xətası: {e}")
            await asyncio.sleep(minutes * 60)

    # ═════════════ başlanğıcda yeniləmə yoxlanışı ═════════════
    async def check_updates():
        await asyncio.sleep(DELAY_AFTER_START)
        if not creator:
            logger.warning("CREATOR_ID yoxdur — yeniləmə bildirişi göndərilmir")
            return

        db = get_db()
        root = Path(BASE_DIR)
        try:
            new = await asyncio.to_thread(scan_files, root)
            old = await asyncio.to_thread(db.get_file_snapshot)
            env_new = await asyncio.to_thread(package_versions)
            git_data = await asyncio.to_thread(git_info, root)
        except Exception as e:
            logger.error(f"Yeniləmə yoxlanışı xətası: {e}", exc_info=True)
            return

        try:
            env_old = json.loads(db.get_setting("update_env") or "{}")
        except ValueError:
            env_old = {}
        git_old = db.get_setting("update_git_head")
        env = env_lines(env_old, env_new, git_data, git_old, ram_mb())
        pulled = await asyncio.to_thread(load_json, "gh_pulled")
        if pulled:
            if pulled.get("rollback"):
                env.insert(0, f"⏪ GitHub yeniləməsi geri qaytarılıb: <code>{escape(pulled['from'])}</code> → "
                              f"<code>{escape(pulled['to'])}</code>")
            else:
                env.insert(0, f"⬇️ GitHub-dan çəkildi: <code>{escape(pulled['from'])}</code> → "
                              f"<code>{escape(pulled['to'])}</code> ({pulled.get('n', 0)} commit)")

        plugin_status = getattr(context, "plugin_status", {}) or {}
        failed = any(v != "ok" for v in plugin_status.values())
        env_changed = bool(env_old) and any(env_old.get(k) != v for k, v in env_new.items() if k in env_old)
        diff_doc = None
        pending = None

        # GitHub quraşdırılma vəziyyətini yoxla
        is_conf = False
        if git_data:
            remote = os.getenv("GITHUB_REMOTE", "origin")
            rc, rurl, _ = git(root, "remote", "get-url", remote)
            is_conf = (rc == 0 and bool(rurl) and (bool(_token()) or not rurl.startswith("https://")))

        if not old:
            text = (
                "📸 <b>Yeniləmə izləməsi aktivdir</b>\n\n"
                f"İlk vəziyyət saxlandı: <b>{len(new)}</b> fayl, "
                f"<b>{fmt_size(sum(f['size'] for f in new.values()))}</b>, "
                f"<b>{sum(f['lines'] for f in new.values())}</b> sətir.\n"
                + "\n".join(env) +
                "\n\n<i>Növbəti dəfə kodu yeniləyib botu yenidən başladanda dəyişikliklər buraya göndəriləcək.</i>"
            )
            if failed:
                text += "\n\n" + build_failure_only(plugin_status, started)
        else:
            # ağır iş (AST analizi, difflib) — thread-də: əvvəl başlanğıcda botu 2+ san. dondururdu
            added, changed, removed = await asyncio.to_thread(compare, old, new)
            if added or changed or removed:
                text = await asyncio.to_thread(build_report, old, new, added, changed, removed,
                                               plugin_status, started, env)
                diff_doc = await asyncio.to_thread(build_diff, old, new, added, changed, removed, started)
                if git_data:
                    files = {"changed": changed, "added": added, "removed": removed}
                    prev = await asyncio.to_thread(load_pending)
                    if prev:
                        for k in files:
                            files[k] = sorted(set(files[k]) | set(prev["files"].get(k, [])))
                    # GitHub-dan çəkilmiş (artıq commit-də olan) fayllar göndərmə siyahısına düşmür
                    dirty = await asyncio.to_thread(dirty_paths, root)
                    files = {k: [p for p in v if p in dirty] for k, v in files.items()}
                    if any(files.values()):
                        pending = {"id": str(int(started)), "files": files,
                                   "message": default_commit_message(files, started), "prev": prev}
                    elif prev:
                        await asyncio.to_thread(save_pending, None)
                        await set_report_kb(prev, status_kb("✔️ Göndəriləcək dəyişiklik qalmadı"))
            elif env_changed:
                text = "🔄 <b>Kod dəyişməyib, amma mühit yeniləndi</b>\n" \
                       f"🕒 {fmt_time(started)}\n\n" + "\n".join(env)
            elif failed:
                text = build_failure_only(plugin_status, started)
            elif pulled:
                text = "🔄 <b>Bot yenidən başladı</b>\n" f"🕒 {fmt_time(started)}\n\n" + "\n".join(env)
            else:
                logger.info("Kodda dəyişiklik yoxdur, yeniləmə bildirişi göndərilmir")
                return

        # 🔔 /ui → Yeniləmə bildirişi: box / short / full / off
        try:
            from bot.plugins import compact_ui_plugin as cui
            style = cui.update_style()
        except Exception:
            cui, style = None, "box"
        has_problem = failed or (diff_doc is not None and "🚨" in text)
        if style == "short" and old and (added or changed or removed):
            text = await asyncio.to_thread(build_short, old, new, added, changed, removed, plugin_status,
                                           started, git_data)
        if style in ("box", "script", "quote", "short"):
            if cui is not None:
                text = cui.boxify(text, True, style=style if style in ("box", "script", "quote") else None) or text
            else:
                lines = text.strip().split("\n")
                if len(lines) > 1:
                    from common.menu import wrap_ui_tags
                    tag, end_tag = wrap_ui_tags(style if style in ("box", "script", "quote") else "box")
                    text = f"<b>{lines[0]}</b>\n{tag}{chr(10).join(lines[1:])}{end_tag}"
        if style == "off" and not has_problem:
            logger.info("🔕 Yeniləmə bildirişi /ui ilə söndürülüb — göndərilmir")
            await asyncio.to_thread(db.save_file_snapshot, new)
            if pending:
                pending.pop("prev", None)
                await asyncio.to_thread(save_pending, pending)     # GitHub-a göndərmə: 🛠 Bot idarəsi → GitHub
            await asyncio.to_thread(db.set_setting, "update_env", json.dumps(env_new))
            if git_data:
                await asyncio.to_thread(db.set_setting, "update_git_head", git_data["hash"])
            if pulled:
                await asyncio.to_thread(save_json, "gh_pulled", None)
            return

        markup = gh_kb(pending["id"], configured=is_conf) if pending else None
        if markup is None:
            try:
                if db.get_setting("depo_fill:active_sync"):
                    markup = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="▶️ Sinxronu davam et", callback_data="df:syncgo:quick"),
                        InlineKeyboardButton(text="🔄 Yenidən başla", callback_data="df:syncgo:full")
                    ]])
            except Exception as _e:
                logger.debug("udulan xəta [bot/updater/handlers.py:1275]: %r", _e)
        try:
            ctx = cui.raw() if cui is not None else contextlib.nullcontext()
            with ctx:                                   # mətn artıq istənilən formadadır — middleware toxunmasın
                sent = await context.bot.send_message(
                    creator, text, parse_mode="HTML", disable_web_page_preview=True,
                    reply_markup=markup,
                )
            if diff_doc:
                stamp = datetime.fromtimestamp(started, get_tz()).strftime("%Y%m%d_%H%M")
                await context.bot.send_document(
                    creator,
                    BufferedInputFile(diff_doc.encode("utf-8"), filename=f"update_{stamp}.diff"),
                    caption="📄 Tam diff",
                )
        except Exception as e:
            logger.error(f"Yeniləmə bildirişi göndərilmədi: {e}")
            return

        await asyncio.to_thread(db.save_file_snapshot, new)
        if pending:
            prev = pending.pop("prev", None)
            pending["message_id"] = sent.message_id
            await asyncio.to_thread(save_pending, pending)
            if prev and prev.get("message_id"):
                await set_report_kb(prev, status_kb("↪️ Növbəti yeniləmə ilə birləşdi"))
        await asyncio.to_thread(db.set_setting, "update_env", json.dumps(env_new))
        if git_data:
            await asyncio.to_thread(db.set_setting, "update_git_head", git_data["hash"])
        if pulled:
            await asyncio.to_thread(save_json, "gh_pulled", None)
        logger.info("✅ Yeniləmə bildirişi göndərildi")

    context.update_check_task = asyncio.create_task(check_updates())
    context.remote_check_task = asyncio.create_task(periodic_remote_check())
