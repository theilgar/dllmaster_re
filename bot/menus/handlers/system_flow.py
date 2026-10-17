"""
bot.menus.handlers.system_flow
Sistem Paneli, Keş/Yaddaş təmizliyi, Sürətləndirmə (RAM disk/L1), Yeniləmələr və Userbot idarəetməsi.
"""

import asyncio
import contextlib
from html import escape
import logging
import os

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.views.music import mus_view
from bot.menus.state import answer_and_edit, get_context, is_creator, reset_inputs, safe_edit_cb
from bot.menus.views.base import LINE, SECTIONS, crumb
from bot.menus.views.dev import dev_hub_view, git_summary, run_updates, upd_view
from bot.menus.views.system_hub import accel_view, bots_view, sys_kb, sys_text
from common.menu import _btn

logger = logging.getLogger(__name__)


async def _mcb_ubacc(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    view = getattr(ctx, "userbots_hub_view", None)
    if view is not None:
        try:
            text, kb = view()
            await cb.answer()
            await safe_edit_cb(cb, text, kb)
            return
        except Exception as e:
            logger.error("📱 Userbotlar paneli xətası: %s", e, exc_info=True)
            diag = f"❌ Panel açılmadı: <code>{escape(str(e)[:300])}</code>"
    else:
        diag = (
            "❌ <b>Userbot hissəsi söndürülüb</b> — <code>python init.py</code> və ya "
            "<code>python init.py userbot</code> ilə başlat"
            if not getattr(ctx, "userbot_enabled", True)
            else "❌ <b>userbot/core/accounts paneli yüklənməyib</b>"
        )
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    st = getattr(ctx, "plugin_status", {}) or {}
    checks = []
    for rel in (
        "userbot/core/tg_login.py",
        "userbot/core/tg_session.py",
        "userbot/core/accounts.py",
        "bot/plugins/voice_assistant_plugin.py",
    ):
        checks.append(
            ("✅" if os.path.isfile(os.path.join(root, rel)) else "❌ yoxdur")
            + f" <code>{rel}</code>"
        )
    for name in ("accounts.py", "voice_assistant_plugin.py"):
        v = st.get(name)
        if v is not None:
            checks.append(
                f"{'✅' if v == 'ok' else '❌'} {name}: <code>{escape(str(v)[:200])}</code>"
            )
    await cb.answer()
    await safe_edit_cb(
        cb,
        f"📱 <b>Userbotlar</b>\n\n{diag}\n\n"
        + "\n".join(checks)
        + "\n\n<i>Faylları yerinə qoyub botu restart et.</i>",
        InlineKeyboardMarkup(inline_keyboard=[nav_row("main")]),
    )


async def _mcb_upd(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    upd = getattr(ctx, "upd", None)
    if upd is None:
        try:
            from common import updates as upd
        except Exception:
            upd = None
    back = parts[2] if len(parts) > 2 and parts[2] in ("main", "dev", "sys") else "main"
    note = ""
    do = len(parts) > 3 and parts[3] == "do"
    if len(parts) > 3 and parts[3] == "check" and upd is not None:
        await cb.answer("🔎 Yoxlanılır...")
        await safe_edit_cb(
            cb,
            "🔔 <b>Yeniləmələr</b>\n\n⏳ <i>PyPI, git və sistem paketləri yoxlanılır...</i>",
        )
        try:
            rep = await asyncio.wait_for(upd.check_updates(), 90)
            upd.print_report(rep)
        except TimeoutError:
            note = "⚠️ Yoxlama 90 san.-də bitmədi — şəbəkəni yoxla"
        except Exception as e:
            note = f"❌ <code>{escape(str(e))[:200]}</code>"
    elif do and upd is not None:
        await cb.answer("⬇️ Yenilənir...")
        await safe_edit_cb(
            cb,
            "🔔 <b>Yeniləmələr</b>\n\n⏳ <i>git pull · pip -U · sistem paketləri... (bir neçə dəqiqə çəkə bilər)</i>",
        )
        lines2, restart = await run_updates()
        with contextlib.suppress(Exception):
            await upd.check_updates()
        rows = []
        if any("sudo parol istədi" in ln for ln in lines2):
            rows.append([_btn("🔑 Şifrəni daxil et (sistem üçün)", f"upd_pwd:{back}")])
        rb = getattr(ctx, "restart_button", None)
        if restart and rb:
            rows.append([rb("♻️ Botu restart et")])
        rows.append(nav_row(back))
        await safe_edit_cb(
            cb,
            "🔔 <b>Yeniləmə nəticəsi</b>\n" + LINE + "\n" + "\n".join(lines2),
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    else:
        await cb.answer()
    await safe_edit_cb(cb, *upd_view(back, note))


async def _mcb_upd_pwd(cb: CallbackQuery, parts: list, action: str):
    back = parts[1] if len(parts) > 1 and parts[1] in ("main", "dev", "sys") else "main"
    if not is_creator(cb.from_user.id):
        await cb.answer("⛔ Bu əməliyyat yalnız bot sahibi üçündür", show_alert=True)
        return
    from bot.menus.state import upd_inp
    upd_inp["stage"] = "await_sudo"
    upd_inp["chat_id"] = cb.message.chat.id
    upd_inp["panel_id"] = cb.message.message_id
    upd_inp["back"] = back

    crumb_path = [SECTIONS["sys"]] if back in ("sys", "dev") else []
    text = (
        crumb(*crumb_path, "🔑 Sudo Şifrəsi") + "\n"
        + LINE + "\n"
        "🔑 <b>Linux sistem paketləri üçün sudo şifrəsi</b>\n\n"
        "<i>Paketləri (pacman/apt) yeniləmək üçün şifrənizi bu çata göndərin.</i>\n\n"
        "🛡 <b>Təhlükəsizlik:</b>\n"
        "• Göndərdiyiniz mesaj <b>dərhal çatdan silinəcək</b>.\n"
        "• Şifrə loglara və ya bazaya yazılmayacaq.\n"
        "• Yalnız bot sahibi daxil edə bilər."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn("✖️ Ləğv et", f"upd:{back}")]
    ])
    await cb.answer()
    await safe_edit_cb(cb, text, kb)


async def _mcb_dq(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    filler = getattr(ctx, "depo_filler", None)
    if filler is None:
        await cb.answer("📦 Depo plugin-i yüklənməyib", show_alert=True)
        return
    stall = filler.stall_reason() if hasattr(filler, "stall_reason") else ""
    if filler.running and stall and hasattr(filler, "force"):
        done = await filler.force()
        await cb.answer("⚡ Məcburi davam:\n" + "\n".join(done)[:180], show_alert=True)
    elif filler.running:
        await cb.answer("🔴 Söndürülür — depoya gedən mahnılar bitirilir...")
        await filler.stop()
    else:
        filler.start()
        await cb.answer("🟢 Depo doldurucu işə salındı")
    await safe_edit_cb(cb, *mus_view())


async def handle_system_action(cb: CallbackQuery, parts: list[str], action: str) -> bool:
    """Sistem, Keş, Server, Sürətləndirmə və Userbot hərəkətlərini idarə edir."""
    ctx = get_context()

    if action == "sys":
        reset_inputs()
        text = await asyncio.to_thread(sys_text)               # sistem sorğuları loop-u tutmasın
        await answer_and_edit(cb, text, sys_kb())              # answer + edit paralel
        return True

    if action in ("accel", "accel_refresh", "accel_t", "accel_c"):
        from bot.core.audio_cache import (
            clear_l1_cache,
            is_l1_cache_enabled,
            set_l1_cache_enabled,
        )
        from common.utilities import (
            cleanup_ram_disk,
            is_ram_disk_enabled,
            set_ram_disk_enabled,
        )

        if action in ("accel", "accel_refresh"):
            await cb.answer()
            await safe_edit_cb(cb, *accel_view())
        elif action == "accel_t":
            sub = parts[2] if len(parts) > 2 else ""
            from common.utilities import is_accel_auto_enabled, set_accel_auto_enabled

            if sub == "auto":
                new_val = not is_accel_auto_enabled()
                set_accel_auto_enabled(new_val)
                await cb.answer(
                    f"⚙️ Rejim: {'🤖 Avto (Ağıllı idarəetmə aktivdir 🟢)' if new_val else '✋ Manual (Əl ilə fərdi idarəetmə)'}"
                )
            else:
                was_auto = is_accel_auto_enabled()
                if was_auto:
                    set_accel_auto_enabled(False)
                tag_prefix = "✋ Manual: " if was_auto else ""
                if sub == "l1":
                    new_val = not is_l1_cache_enabled()
                    set_l1_cache_enabled(new_val)
                    await cb.answer(
                        f"{tag_prefix}🧠 L1 RAM Keşi {'aktiv edildi 🟢' if new_val else 'deaktiv edildi 🔴'}"
                    )
                elif sub == "ramdisk":
                    new_val = not is_ram_disk_enabled()
                    set_ram_disk_enabled(new_val)
                    await cb.answer(
                        f"{tag_prefix}⚡ RAM Diski {'aktiv edildi 🟢' if new_val else 'fiziki diskə keçirildi 🔴'}"
                    )
                elif sub == "smartcopy":
                    from bot.core.youtube_handler import (
                        is_smart_copy_enabled,
                        set_smart_copy_enabled,
                    )

                    new_val = not is_smart_copy_enabled()
                    set_smart_copy_enabled(new_val)
                    await cb.answer(
                        f"{tag_prefix}🎯 Ağıllı Stream Copy {'aktiv edildi 🟢' if new_val else 'deaktiv edildi 🔴'}"
                    )
                elif sub == "paralleldl":
                    from bot.core.youtube_handler import (
                        is_parallel_dl_enabled,
                        set_parallel_dl_enabled,
                    )

                    new_val = not is_parallel_dl_enabled()
                    set_parallel_dl_enabled(new_val)
                    await cb.answer(
                        f"{tag_prefix}🚀 Paralel Fraqmentlər {'aktiv edildi 🟢' if new_val else 'deaktiv edildi 🔴'}"
                    )
                else:
                    await cb.answer()
            await safe_edit_cb(cb, *accel_view())
        elif action == "accel_c":
            sub = parts[2] if len(parts) > 2 else ""
            if sub == "l1":
                clear_l1_cache()
                await cb.answer("🧹 L1 RAM keşi təmizləndi")
            elif sub == "ramdisk":
                freed_b, freed_cnt = await cleanup_ram_disk()
                await cb.answer(
                    f"🧹 {freed_cnt} müvəqqəti fayl ({freed_b / 1024 / 1024:.1f} MB) silindi"
                )
            else:
                await cb.answer()
            await safe_edit_cb(cb, *accel_view())
        return True

    if action in ("storage", "storage_refresh"):
        await cb.answer("Yenilənir..." if action == "storage_refresh" else "")
        from common.diagnostics import project_storage_snapshot
        from common.menu import storage_view

        if action == "storage_refresh":
            project_storage_snapshot(force=True)
        await safe_edit_cb(cb, *storage_view())
        return True

    if action == "storage_clean":
        from common.diagnostics import project_storage_snapshot
        from common.menu import storage_view
        from common.utilities import cleanup_downloads, format_bytes

        freed_bytes, freed_files = await cleanup_downloads()
        project_storage_snapshot(force=True)
        note = (
            f"🧹 <b>Keş təmizləndi:</b> {freed_files} fayl ({format_bytes(freed_bytes)} boşaldıldı)"
            if freed_files
            else "ℹ️ <i>Keş artıq təmizdir (0 bayt)</i>"
        )
        await cb.answer("🧹 Keş təmizləndi!" if freed_files else "Keş artıq təmizdir", show_alert=True)
        await safe_edit_cb(cb, *storage_view(note=note))
        return True

    if action == "storage_clean_records":
        from common.diagnostics import project_storage_snapshot
        from common.menu import storage_view
        from common.utilities import cleanup_records, format_bytes

        freed_bytes, freed_files = await cleanup_records()
        project_storage_snapshot(force=True)
        note = (
            f"🎙 <b>Səs qeydləri təmizləndi:</b> {freed_files} fayl ({format_bytes(freed_bytes)} boşaldıldı)"
            if freed_files
            else "ℹ️ <i>Silinəcək səs qeydi tapılmadı (0 bayt)</i>"
        )
        await cb.answer("🎙 Qeydlər təmizləndi!" if freed_files else "Qeyd tapılmadı", show_alert=True)
        await safe_edit_cb(cb, *storage_view(note=note))
        return True

    if action == "dev":
        await cb.answer()
        await safe_edit_cb(cb, *dev_hub_view(await git_summary()))
        return True

    if action == "upd":
        await _mcb_upd(cb, parts, action)
        return True

    if action == "upd_pwd":
        await _mcb_upd_pwd(cb, parts, action)
        return True

    if action == "dq":
        await _mcb_dq(cb, parts, action)
        return True

    if action == "ub1toggle":
        await cb.answer()
        note = ""
        fn = getattr(ctx, "userbot1_toggle", None)
        if fn:
            note = await fn()
        await safe_edit_cb(cb, *bots_view(note))
        return True

    if action == "ub2toggle":
        await cb.answer()
        note = ""
        fn = getattr(ctx, "userbot2_toggle", None)
        if fn:
            note = await fn()
        else:
            va = getattr(ctx, "voice_assistant", None)
            if va:
                from bot.plugins.voice_assistant_plugin import S_ON, _set

                if va.enabled and va.running:
                    _set(S_ON, "0")
                    await va.stop()
                    note = "🔴 2️⃣ UB2 (Asistan) söndürüldü"
                else:
                    _set(S_ON, "1")
                    err = await va.start()
                    note = f"⚠️ {escape(err)}" if err else "🟢 2️⃣ UB2 (Asistan) işləyir"
            else:
                note = "⚠️ 2️⃣ Asistan yüklənməyib"
        await safe_edit_cb(cb, *bots_view(note))
        return True

    if action == "ubacc":
        await _mcb_ubacc(cb, parts, action)
        return True

    return False
