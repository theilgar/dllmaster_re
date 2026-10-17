"""
bot.menus.handlers.youtube_flow
YouTube parametrləri, hesab rotasiyası, axtarış mənbəyi, yükləmə formatı və brauzer profilləri axını.
"""

import asyncio
import contextlib
import io
import logging
import os
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.core import panel
from bot.menus.state import (
    get_context,
    is_creator,
    safe_edit_cb,
    yt_inp,
    yta_inp,
)
from bot.menus.views.base import LINE, SECTIONS, crumb
from bot.menus.views.youtube_accs import (
    FMT_PRESETS,
    _yta_browser_profiles,
    dl_format_view,
    search_source_view,
    web_profile_view,
    web_profiles_view,
    yt_hub_view,
    yt_picker_view,
    yta_acc_view,
    yta_browser_view,
    yta_help_view,
    yta_list_view,
    yta_login_view,
    yta_own_device_view,
    yta_links_view,
    yta_server_login_view,
)
from common.database import get_db
from common.menu import _btn
from common.kv import next_of

logger = logging.getLogger(__name__)


async def yta_show(text_kb):
    ctx = get_context()
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(
            text=text_kb[0],
            reply_markup=text_kb[1],
            chat_id=yta_inp["chat_id"],
            message_id=yta_inp["panel_id"],
            parse_mode="HTML",
        )


async def _mcb_yt(cb: CallbackQuery, parts: list, action: str):
    import bot.core.youtube_handler as yh
    from bot.core import webprofile as wp

    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    db = get_db()
    note = ""
    if sub == "srcm" and arg in ("auto", "manual"):
        await asyncio.to_thread(
            yh.set_search_mode,
            "auto" if arg == "auto" else (db.get_setting("search:last_source") or "ytmusic"),
        )
        await cb.answer("🤖 Avto (hibrid)" if arg == "auto" else "✋ Manual — seç")
    elif sub == "srcset" and arg in yh.SEARCH_SOURCES and arg != "auto":
        await asyncio.to_thread(yh.set_search_source, arg)
        await asyncio.to_thread(db.set_setting, "search:last_source", arg)
        await cb.answer(f"🔎 {yh.SEARCH_SOURCES[arg]}")
    elif sub == "fmtm" and arg in ("auto", "manual"):
        await asyncio.to_thread(
            yh.set_fmt_mode,
            "auto" if arg == "auto" else (db.get_setting("dl:last_fmt_preset") or "hybrid"),
        )
        await cb.answer("🤖 Avto" if arg == "auto" else "✋ Manual — seç")
    elif sub == "fmtset" and arg in yh.FMT_MODES and arg != "auto":
        await asyncio.to_thread(yh.set_download_preset, arg)
        await asyncio.to_thread(db.set_setting, "dl:last_fmt_preset", arg)
        await cb.answer(f"🎧 {yh.FMT_MODES[arg]}")
    elif sub == "ckm" and arg in ("auto", "manual"):
        await asyncio.to_thread(
            wp.set_source,
            "auto" if arg == "auto" else (db.get_setting("web:cookies_last") or "browser"),
        )
        await cb.answer("🤖 Avto" if arg == "auto" else "✋ Manual — seç")
    elif sub == "ckset" and arg in wp.COOKIE_SOURCES and arg != "auto":
        await asyncio.to_thread(wp.set_source, arg)
        await asyncio.to_thread(db.set_setting, "web:cookies_last", arg)
        if arg == "file" and not os.path.isfile(wp.cookies_file() or "/nonexistent"):
            note = "⚠️ YOUTUBE_COOKIES_FILE tapılmadı — fayl qoyulana qədər brauzer istifadə olunacaq"
        await cb.answer(f"🍪 {wp.COOKIE_SOURCES[arg]}")
    elif sub == "uam" and arg in ("auto", "manual"):
        await asyncio.to_thread(
            wp.set_ua_mode,
            "ytdlp" if arg == "auto" else (db.get_setting("web:ua_last") or "match"),
        )
        await cb.answer("🤖 Avto" if arg == "auto" else "✋ Manual — seç")
    elif sub == "uaset" and arg in wp.UA_MODES and arg != "ytdlp":
        await asyncio.to_thread(wp.set_ua_mode, arg)
        await asyncio.to_thread(db.set_setting, "web:ua_last", arg)
        await cb.answer(f"🕵️ {wp.UA_MODES[arg]}")
    elif sub == "src":
        keys = list(yh.SEARCH_SOURCES)
        nxt = next_of(keys, yh.search_source())
        await asyncio.to_thread(db.set_setting, yh.SEARCH_SETTING, nxt)
        await cb.answer(f"🔎 {yh.SEARCH_SOURCES[nxt]}")
    elif sub == "fmt":
        m = yh.download_mode()
        idx = next(
            (
                i
                for i, p in enumerate(FMT_PRESETS)
                if p[1] == m["direct_m4a"] and p[2] == m["ffmpeg"]
            ),
            -1,
        )
        p = FMT_PRESETS[(idx + 1) % len(FMT_PRESETS)]
        await asyncio.to_thread(db.set_setting, "dl:direct_m4a", "on" if p[1] else "off")
        await asyncio.to_thread(db.set_setting, "dl:ffmpeg", "on" if p[2] else "off")
        await cb.answer(f"🎧 {p[3]}")
    elif sub == "ck":
        keys = list(wp.COOKIE_SOURCES)
        nxt = next_of(keys, wp.current()["cookies"])
        await asyncio.to_thread(wp.set_source, nxt)
        if nxt == "file" and not os.path.isfile(wp.cookies_file() or "/nonexistent"):
            note = "⚠️ YOUTUBE_COOKIES_FILE tapılmadı — fayl qoyulana qədər brauzer istifadə olunacaq"
        await cb.answer(f"🍪 {wp.COOKIE_SOURCES[nxt]}")
    elif sub == "ua":
        v = await asyncio.to_thread(wp.cycle_ua_mode)
        await cb.answer(f"🕵️ {wp.UA_MODES[v]}")
    elif sub in ("bp", "pick") and (len(parts) <= 3 or parts[3] == "bp"):
        arg = parts[3] if len(parts) > 3 else ""
        if arg in ("+", "-"):
            cur = yh.batch_parallel()
            v = await asyncio.to_thread(yh.step_batch_parallel, 1 if arg == "+" else -1)
            await cb.answer(
                f"⬇️ {v} paralel"
                if v != cur
                else ("Minimum 1 (ardıcıl)" if v == 1 else f"Maksimum {v}")
            )
        else:
            yt_inp["stage"] = None
            await cb.answer()
            await safe_edit_cb(cb, *yt_picker_view())
            return
    elif sub == "vstep" and len(parts) > 3 and parts[3] == "bp":
        delta = int(parts[4]) if len(parts) > 4 else 1
        cur = yh.batch_parallel()
        v = await asyncio.to_thread(yh.step_batch_parallel_delta, delta)
        await cb.answer(
            f"⬇️ {v} paralel"
            if v != cur
            else ("Minimum 1 (ardıcıl)" if v == 1 else f"Maksimum {v}")
        )
        await safe_edit_cb(cb, *yt_picker_view(f"✓ {v} paralel"))
        return
    elif sub == "vinp" and len(parts) > 3 and parts[3] == "bp":
        await cb.answer()
        yt_inp.update(
            stage="await_bp",
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        cur = yh.batch_parallel()
        text = (
            crumb(SECTIONS["mus"]) + " › 🎵 <b>YouTube › Toplu yükləmə</b>\n"
            + LINE + "\n"
            f"Hazırkı paralel sayı: <b>{cur}</b>\n\n"
            "✍️ <b>Toplu yükləmə paralelliyini daxil edin:</b>\n"
            "• <b>Rəqəm</b> yazın (məs: <code>3</code>) — 1 ilə 10 arası\n"
            "• <b>+</b> yazın — <b>maksimum (10)</b> olsun\n"
            "• <b>-</b> yazın — <b>minimum (1)</b> olsun\n"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "yt:pick:bp")]])
        await safe_edit_cb(cb, text, kb)
        return
    elif sub == "pot":
        ok = await asyncio.to_thread(yh.recheck_pot)
        await cb.answer(
            "🛡 PO token provider aktivdir" if ok else "❌ PO token provider cavab vermir",
            show_alert=not ok,
        )
    elif sub == "cd":
        yh.reset_cooldown()
        await cb.answer("⏭ Cooldown sıfırlandı — növbə davam edir")
    elif sub == "test":
        await cb.answer("🧪 Yoxlanılır...")
        await safe_edit_cb(
            cb,
            "🎵 <b>YouTube ayarları</b>\n\n🧪 <i>Cookies oxunur... (keyring varsa 30 san.-yə qədər)</i>",
        )
        try:
            r = await asyncio.wait_for(asyncio.to_thread(wp.test_cookies), 45)
            if r.get("error") and not r.get("ok"):
                note = f"🧪 ❌ {escape(str(r['error'])[:200])}"
            elif r.get("source") == "none":
                note = "🧪 🚫 cookiesiz rejim — yoxlanacaq cookie yoxdur"
            else:
                note = (
                    f"🧪 ✅ {r['total']} cookie · YouTube: <b>{r['yt']}</b> · "
                    + ("🔓 hesaba daxil olunub" if r["logged_in"] else "🔒 anonim (giriş yoxdur)")
                )
        except TimeoutError:
            note = (
                "🧪 ❌ 45 san.-də bitmədi — keyring parol gözləyir. "
                "📖 Brauzer → 🔐 Keyring: BASICTEXT sına"
            )
    else:
        await cb.answer()
    text, kb = await asyncio.to_thread(yt_hub_view, note)
    await safe_edit_cb(cb, text, kb)


async def _mcb_srch(cb: CallbackQuery, parts: list, action: str):
    note = ""
    mode = parts[2] if len(parts) > 2 else None
    if mode:
        try:
            from bot.core.youtube_handler import (
                SEARCH_MODE_SETTING,
                SEARCH_SETTING,
                SEARCH_SOURCES,
            )
        except Exception:
            SEARCH_SOURCES, SEARCH_SETTING, SEARCH_MODE_SETTING = {}, "search:source", "search:mode"
        if mode in SEARCH_SOURCES:
            await asyncio.to_thread(get_db().set_setting, SEARCH_SETTING, mode)
            await asyncio.to_thread(get_db().set_setting, SEARCH_MODE_SETTING, "manual")
            note = f"✅ Axtarış mənbəyi: {SEARCH_SOURCES[mode]} — növbəti axtarışdan etibarən"
            await cb.answer(note)
        elif mode == "auto":
            await asyncio.to_thread(get_db().set_setting, SEARCH_MODE_SETTING, "auto")
            note = "✅ Axtarış mənbəyi: Avtomatik (hibrid)"
            await cb.answer(note)
        else:
            await cb.answer()
    else:
        await cb.answer()
    text, kb = search_source_view(note)
    await safe_edit_cb(cb, text, kb)


async def _mcb_yta(cb: CallbackQuery, parts: list, action: str):
    from bot.core import yt_accounts as ya

    sub = parts[2] if len(parts) > 2 else ""
    aid = parts[3] if len(parts) > 3 else ""
    if sub not in ("addf", "rf", "nm", "lo", "relogin"):
        yta_inp["stage"] = None
    if sub in ("ol", "lk", "la", "ld", "ln"):               # 🔘 hesabın düymələri (brauzerdə aç)
        await _yta_links(cb, parts, sub, aid)
        return
    if sub == "a":
        await cb.answer()
        await safe_edit_cb(cb, *yta_acc_view(aid))
    elif sub == "login":
        await cb.answer()
        await safe_edit_cb(cb, *yta_login_view())
    elif sub == "ls":
        await cb.answer("🖥 Brauzer açılır...")
        try:
            acc = await asyncio.to_thread(ya.start_server_login)
            await safe_edit_cb(
                cb,
                *yta_server_login_view(
                    acc["id"], "🟢 Serverin ekranında Google giriş pəncərəsi açıldı"
                ),
            )
        except Exception as e:
            await safe_edit_cb(cb, *yta_login_view(f"❌ {escape(str(e)[:200])}"))
    elif sub in ("lf", "lfx"):
        await cb.answer("🧪 Yoxlanılır..." if sub == "lf" else "⏳ Chrome bağlanır (60 san.-yə qədər)...")
        res = await asyncio.to_thread(ya.finish_server_login, aid, sub == "lfx")
        if res["ok"]:
            await safe_edit_cb(
                cb,
                *yta_acc_view(
                    aid,
                    f"✅ Giriş alındı — {res['yt']} YouTube cookie. Hesab aktivdir (rol: 📦 depo)",
                ),
            )
        elif res.get("open"):
            await safe_edit_cb(cb, *yta_server_login_view(aid, "🪟 " + escape(str(res["error"])[:200])))
        else:
            await safe_edit_cb(
                cb,
                *yta_server_login_view(
                    aid,
                    "❌ Giriş tapılmadı: "
                    + escape(str(res["error"])[:200])
                    + "\n🔁 Pəncərəni yenidən aç, daxil ol, YouTube açılanda "
                    "pəncərəni <b>özün bağla</b>, sonra ✅",
                ),
            )
    elif sub == "lr":
        try:
            await asyncio.to_thread(ya.reopen_server_login, aid)
            await cb.answer("🔁 Açıldı")
            await safe_edit_cb(cb, *yta_server_login_view(aid, "🟢 Pəncərə yenidən açıldı"))
        except Exception as e:
            await cb.answer(f"❌ {e}"[:100], show_alert=True)
            await safe_edit_cb(cb, *yta_server_login_view(aid, f"❌ {escape(str(e)[:200])}"))
    elif sub == "lc":
        deleted = await asyncio.to_thread(ya.cancel_server_login, aid)
        await cb.answer("❌ Ləğv edildi")
        if deleted:
            await safe_edit_cb(cb, *yta_list_view("❌ Server girişi ləğv edildi"))
        else:
            await safe_edit_cb(cb, *yta_acc_view(aid, "❌ Yenidən giriş ləğv edildi"))
    elif sub == "relogin":
        a = ya.get(aid)
        if not a:
            await cb.answer("⚠️ Hesab tapılmadı", show_alert=True)
            await safe_edit_cb(cb, *yta_list_view("⚠️ Hesab tapılmadı"))
            return
        if a["kind"] == "browser" and ya.can_server_login(a):
            await cb.answer("🖥 Brauzer açılır...")
            try:
                await asyncio.to_thread(ya.reopen_server_login, aid)
                await safe_edit_cb(
                    cb,
                    *yta_server_login_view(
                        aid,
                        "🟢 Serverin ekranında Google giriş pəncərəsi açıldı. "
                        "Daxil olub pəncərəni bağla, sonra ✅",
                    ),
                )
            except Exception as e:
                await cb.answer(f"❌ {e}"[:100], show_alert=True)
                await safe_edit_cb(
                    cb, *yta_acc_view(aid, f"❌ Brauzer açıla bilmədi: {escape(str(e)[:200])}")
                )
        elif a["kind"] == "file":
            await cb.answer()
            yta_inp.update(
                stage="await_file",
                acc=aid,
                chat_id=cb.message.chat.id,
                panel_id=cb.message.message_id,
            )
            await safe_edit_cb(
                cb,
                crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "📄 cookies.txt yenilə")
                + "\n"
                + LINE
                + "\n\n"
                + f"<b>{escape(a['name'])}</b> üçün yeni <code>cookies.txt</code> faylını bu çata <b>sənəd kimi</b> göndər.\n\n"
                + "1. Brauzerdə YouTube hesabına daxil ol.\n"
                + "2. «Get cookies.txt LOCALLY» uzantısı ilə faylı ixrac et.\n"
                + "3. Faylı bura göndər (hesab avtomatik yenilənəcək və rotasiyaya qayıdacaq).\n\n"
                + "<i>Fayl yoxlanılır (YouTube girişi olmalıdır), serverdə gizli saxlanılır, mesaj çatdan silinir.</i>",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_btn("📱 Təlimat", "yta:lo")],
                        [_btn("✖️ Ləğv et", f"yta:a:{aid}")],
                    ]
                ),
            )
        elif a["kind"] == "main":
            await cb.answer()
            await safe_edit_cb(
                cb,
                crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "⭐ Əsas hesab")
                + "\n"
                + LINE
                + "\n\n"
                + "⭐ <b>Əsas hesab</b> serverin sistem brauzeri və ya ümumi cookies ayarı ilə işləyir.\n\n"
                + "Yenidən giriş üçün:\n"
                + "• 🌐 <b>Brauzer ayarları</b> bölməsindən cookie mənbəyini və ya profilini seç / yenilə\n"
                + "• və ya ➕ <b>Google hesabı ilə giriş</b> vasitəsilə ayrıca hesab əlavə et.\n",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_btn("🌐 Brauzer / cookies ayarları", "web")],
                        [_btn("🧪 Əsas hesabı test et", "yta:t:main")],
                        [_btn("⬅️ Hesab", "yta:a:main")],
                    ]
                ),
            )
        else:
            await cb.answer()
            prof_name = ya.describe(a)
            await safe_edit_cb(
                cb,
                crumb(SECTIONS["mus"], "🔑 YouTube hesabları", f"🔐 {escape(a['name'])}")
                + "\n"
                + LINE
                + "\n\n"
                + f"Bu hesab serverdəki <b>{escape(prof_name)}</b> profilinə bağlıdır.\n\n"
                + "1. Serverdə həmin brauzeri açıb <b>youtube.com</b>-da Google hesabına daxil ol.\n"
                + "2. Daxil olandan sonra aşağıdakı 🧪 <b>Test et</b> düyməsinə bas.\n\n"
                + "<i>Və ya bu hesabı silib «🔐 Google hesabı ilə giriş» ilə server brauzerində birbaşa aça bilərsən.</i>",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [_btn("🧪 Test et", f"yta:t:{aid}")],
                        [_btn("⬅️ Hesab", f"yta:a:{aid}")],
                    ]
                ),
            )
    elif sub == "lo":
        await cb.answer()
        yta_inp.update(
            stage="await_file",
            acc=None,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        await safe_edit_cb(cb, *yta_own_device_view())
    elif sub == "mode":
        m = ya.cycle_mode()
        await cb.answer(f"Rotasiya: {ya.MODES[m]}")
        await safe_edit_cb(cb, *yta_list_view(f"🔁 Rotasiya rejimi: <b>{ya.MODES[m]}</b>"))
    elif sub == "pin":
        role = parts[4] if len(parts) > 4 else ""
        ya.set_pin(aid, role)
        note = f"📌 {ya.ROLES.get(role, role)} zolağı indi bu hesabla işləyir"
        await cb.answer(note)
        await safe_edit_cb(cb, *yta_acc_view(aid, note))
    elif sub == "r":
        on = ya.toggle_role(aid, parts[4] if len(parts) > 4 else "")
        note = f"{ya.ROLES.get(parts[4] if len(parts) > 4 else '', '')}: " + (
            "✅ açıldı" if on else "❌ söndürüldü"
        )
        await cb.answer(note)
        await safe_edit_cb(cb, *yta_acc_view(aid, note))
    elif sub == "on":
        on = ya.toggle_on(aid)
        await cb.answer("🟢 Aktiv" if on else "⚫ Söndürüldü")
        await safe_edit_cb(cb, *yta_acc_view(aid))
    elif sub == "up":
        ya.move_up(aid)
        await cb.answer("⬆️ Prioritet qaldırıldı")
        await safe_edit_cb(cb, *yta_acc_view(aid, "⬆️ Prioritet qaldırıldı"))
    elif sub == "t":
        await cb.answer("🧪 Yoxlanılır...")
        res = await asyncio.to_thread(ya.test, aid)
        await safe_edit_cb(
            cb,
            *yta_acc_view(
                aid,
                (
                    "✅ Girişli, işləyir"
                    if res["ok"]
                    else f"❌ {escape(str(res['error'])[:200])}"
                ),
            ),
        )
    elif sub == "tall":
        accs = ya.accounts()
        await cb.answer(f"🧪 {len(accs)} hesab yoxlanılır...")
        res = await asyncio.gather(*(asyncio.to_thread(ya.test, a["id"]) for a in accs))
        ok = sum(1 for r in res if r["ok"])
        bad = [
            f"{escape(a['name'])}: {escape(str(r['error'])[:60])}"
            for a, r in zip(accs, res)
            if not r["ok"]
        ]
        note = f"🧪 Test: ✅ {ok} işləyir" + (
            f" · ❌ {len(bad)} problemli" if bad else " — hamısı girişlidir"
        )
        if bad:
            note += "\n" + "\n".join(f"• {b}" for b in bad[:6])
        await safe_edit_cb(cb, *yta_list_view(note))
    elif sub == "ub":
        ya.unblock(None if aid == "all" else aid)
        await cb.answer("⏭ Blok götürüldü")
        await safe_edit_cb(
            cb,
            *(
                yta_list_view("⏭ Bütün bloklar götürüldü")
                if aid == "all"
                else yta_acc_view(aid, "⏭ Blok götürüldü")
            ),
        )
    elif sub == "del":
        await cb.answer()
        a = ya.get(aid)
        await safe_edit_cb(
            cb,
            f"🗑 <b>{escape(a['name'] if a else aid)}</b> silinsin?"
            + (
                "\n<i>cookies.txt faylı da serverdən silinəcək.</i>"
                if a and a["kind"] == "file"
                else ""
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    panel.confirm(f"menu:yta:delok:{aid}", f"menu:yta:a:{aid}", "sil")
                ]
            ),
        )
    elif sub == "delok":
        ok = ya.remove(aid)
        await cb.answer("🗑 Silindi" if ok else "Silinmədi")
        await safe_edit_cb(cb, *yta_list_view("🗑 Hesab silindi" if ok else "⚠️ Silinmədi"))
    elif sub in ("addf", "rf"):
        await cb.answer()
        yta_inp.update(
            stage="await_file",
            acc=aid if sub == "rf" else None,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        a = ya.get(aid) if sub == "rf" else None
        await safe_edit_cb(
            cb,
            crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "📄 cookies.txt")
            + "\n"
            + LINE
            + "\n\n"
            + (f"<b>{escape(a['name'])}</b> üçün yeni " if a else "")
            + "cookies.txt faylını bu çata <b>sənəd kimi</b> göndər.\n"
            + ("" if a else "Caption-a hesabın adını yaza bilərsən (məs. <code>2-ci hesab</code>).\n")
            + "\n<i>Fayl yoxlanılır (YouTube girişi olmalıdır), serverdə gizli saxlanılır, "
            "mesaj çatdan silinir. 📖 Necə əlavə edim? — ətraflı.</i>",
            InlineKeyboardMarkup(
                inline_keyboard=[[_btn("✖️ Ləğv et", f"yta:a:{aid}" if a else "yta")]]
            ),
        )
    elif sub == "nm":
        await cb.answer()
        yta_inp.update(
            stage="await_name",
            acc=aid,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        await safe_edit_cb(
            cb,
            "✏️ Hesabın yeni adını yaz (40 simvola qədər):",
            InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", f"yta:a:{aid}")]]),
        )
    elif sub == "addb":
        await cb.answer()
        from bot.core import webprofile as wp

        await asyncio.to_thread(wp.detect, True)
        await safe_edit_cb(cb, *yta_browser_view())
    elif sub == "bp":
        profs = _yta_browser_profiles()
        key, idx = aid, int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else -1
        prof = next((p for k, i, p in profs if k == key and i == idx), None)
        if prof is None:
            await cb.answer("Profil tapılmadı — 🔄", show_alert=True)
            return
        acc = ya.add_browser(key, prof["path"], f"{prof['name'][:30]}")
        await cb.answer("➕ Əlavə olundu — yoxlanılır...")
        res = await asyncio.to_thread(ya.test, acc["id"])
        await safe_edit_cb(
            cb,
            *yta_acc_view(
                acc["id"],
                "➕ Hesab əlavə olundu (rol: 📦 depo) — "
                + ("✅ girişli" if res["ok"] else f"⚠️ {escape(str(res['error'])[:150])}"),
            ),
        )
    elif sub == "help":
        await cb.answer()
        await safe_edit_cb(cb, *yta_help_view())
    else:
        await cb.answer()
        await safe_edit_cb(cb, *yta_list_view())


def _link_idx(parts: list) -> int:
    return int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else -1


async def _yta_links(cb: CallbackQuery, parts: list, sub: str, aid: str):
    """ol — düyməni aç · lk — idarə səhifəsi · la — əlavə (hazır / öz linki) · ld — sil · ln — adı dəyiş."""
    from bot.core import yt_accounts as ya

    a = ya.get(aid)
    if not a:
        await cb.answer("⚠️ Hesab tapılmadı", show_alert=True)
        await safe_edit_cb(cb, *yta_list_view("⚠️ Hesab tapılmadı"))
        return
    idx = _link_idx(parts)
    items = ya.links(a)
    if sub == "ol":
        if not 0 <= idx < len(items):
            await cb.answer("Düymə tapılmadı — səhifəni yenilə", show_alert=True)
            return
        await cb.answer("🖥 Brauzer açılır...")
        try:
            url = await asyncio.to_thread(ya.open_url, aid, items[idx]["url"])
            note = f"🟢 <b>{escape(items[idx]['name'])}</b> serverin ekranında açıldı\n<code>{escape(url[:60])}</code>"
        except Exception as e:
            note = f"❌ {escape(str(e)[:200])}"
        await safe_edit_cb(cb, *yta_acc_view(aid, note))
    elif sub == "lk":
        await cb.answer()
        await safe_edit_cb(cb, *yta_links_view(aid))
    elif sub == "la":
        key = parts[4] if len(parts) > 4 else ""
        if key == "own":
            await cb.answer()
            yta_inp.update(stage="await_lurl", acc=aid, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
            await safe_edit_cb(
                cb, "🔗 Linki yaz (məs. <code>mail.google.com</code> və ya <code>https://studio.youtube.com</code>):",
                InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", f"yta:lk:{aid}")]]))
        elif key in ya.LINK_PRESETS:
            label, url = ya.LINK_PRESETS[key]
            try:
                ya.add_link(aid, label, url)
                await cb.answer("➕ Əlavə olundu — ✏️ ilə öz adını ver")
                await safe_edit_cb(cb, *yta_links_view(aid, f"➕ {escape(label)} əlavə olundu"))
            except ValueError as e:
                await cb.answer(f"⚠️ {e}"[:150], show_alert=True)
        else:
            await cb.answer("Naməlum düymə", show_alert=True)
    elif sub == "ld":
        ok = ya.remove_link(aid, idx)
        await cb.answer("🗑 Silindi" if ok else "Tapılmadı")
        await safe_edit_cb(cb, *yta_links_view(aid, "🗑 Düymə silindi" if ok else "⚠️ Düymə tapılmadı"))
    elif sub == "ln":
        if not 0 <= idx < len(items):
            await cb.answer("Düymə tapılmadı", show_alert=True)
            return
        await cb.answer()
        yta_inp.update(stage="await_lname", acc=aid, idx=idx, chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await safe_edit_cb(
            cb, f"✏️ <b>{escape(items[idx]['name'])}</b> düyməsinin yeni adını yaz (24 simvola qədər, emoji ola bilər):",
            InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", f"yta:lk:{aid}")]]))


async def _mcb_web(cb: CallbackQuery, parts: list, action: str):
    from bot.core import webprofile as wp

    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    note = ""
    if sub == "src" and arg in wp.COOKIE_SOURCES:
        await asyncio.to_thread(wp.set_source, arg)
        note = f"✅ Cookie mənbəyi: {wp.COOKIE_SOURCES[arg]}"
        if arg == "file" and not os.path.isfile(wp.cookies_file() or "/nonexistent"):
            note += "\n⚠️ YOUTUBE_COOKIES_FILE tapılmadı — fayl qoyulana qədər brauzer istifadə olunacaq"
        await cb.answer(wp.COOKIE_SOURCES[arg])
    elif sub == "bmode" and arg in wp.BROWSER_MODES:
        await asyncio.to_thread(wp.set_browser_mode, arg)
        cur = await asyncio.to_thread(wp.current)
        info = wp.BROWSERS[cur["browser"]]
        note = (
            f"🤖 Avto: ən son istifadə olunan brauzer — {info['emoji']} {info['label']}"
            if arg == "auto"
            else f"✋ Manual: brauzeri aşağıdan seç (indi {info['emoji']} {info['label']})"
        )
        await cb.answer(wp.BROWSER_MODES[arg])
    elif sub == "b" and arg in wp.BROWSERS:
        await asyncio.to_thread(wp.set_browser_mode, "manual")
        await asyncio.to_thread(wp.set_browser, arg)
        info = wp.BROWSERS[arg]
        note = f"✅ Brauzer: {info['emoji']} {info['label']}"
        if not wp.detect().get(arg):
            note += " · ⚠️ serverdə profili tapılmadı"
        if wp.current()["cookies"] in ("file", "none"):
            note += "\n<i>Cookie mənbəyi brauzer deyil — tətbiq üçün 🌐 Brauzer və ya 🔄 Avto seç.</i>"
        await cb.answer(f"{info['emoji']} {info['label']}")
    elif sub == "prof":
        await cb.answer()
        await safe_edit_cb(cb, *web_profiles_view())
        return
    elif sub == "p":
        profs = wp.detect().get(wp.current()["browser"]) or []
        if arg == "auto":
            await asyncio.to_thread(wp.set_profile, "")
            await cb.answer("🔄 Avto profil")
        elif arg.isdigit() and int(arg) < len(profs):
            p = profs[int(arg)]
            await asyncio.to_thread(wp.set_profile, p["path"])
            await cb.answer(f"👤 {p['name']}"[:190])
        else:
            await cb.answer("Profil tapılmadı — 🔄 yenilə")
        await safe_edit_cb(cb, *web_profiles_view())
        return
    elif sub == "kr":
        v = await asyncio.to_thread(wp.cycle_keyring)
        await cb.answer(f"🔐 Keyring: {v or 'avto'}")
    elif sub == "krmode" and arg in wp.BROWSER_MODES:
        await asyncio.to_thread(wp.set_keyring_mode, arg)
        if arg == "auto":
            res = await asyncio.to_thread(wp.test_cookies)
            kr = (await asyncio.to_thread(wp.current))["keyring"]
            note = (
                f"🤖 Keyring avto: <b>{kr or 'yt-dlp özü'}</b> — "
                + (
                    "✅ hesab girişlidir"
                    if res.get("logged_in")
                    else "⚠️ girişli cookie tapılmadı"
                )
            )
        else:
            note = "✋ Keyring manual — aşağıdan seç"
        await cb.answer(wp.BROWSER_MODES[arg])
    elif sub == "krset":
        kr = "" if arg == "-" else arg
        await asyncio.to_thread(wp.set_keyring, kr)
        note = f"✋ Keyring: <b>{kr or 'yt-dlp özü'}</b> — 🧪 Cookie testi ilə yoxla"
        await cb.answer(f"🔐 {kr or 'yt-dlp özü'}")
    elif sub == "ua":
        v = await asyncio.to_thread(wp.cycle_ua_mode)
        await cb.answer(f"🕵️ {wp.UA_MODES[v]}")
    elif sub == "scan":
        await asyncio.to_thread(wp.detect, True)
        await cb.answer("🔄 Brauzerlər yenidən axtarıldı")
    elif sub == "test":
        await cb.answer("🧪 Yoxlanılır...")
        await safe_edit_cb(
            cb,
            "🌐 <b>Brauzer və cookies</b>\n\n🧪 <i>Cookies oxunur... (keyring varsa 30 san.-yə qədər)</i>",
        )
        try:
            r = await asyncio.wait_for(asyncio.to_thread(wp.test_cookies), 45)
            note = "✅ Test bitdi" if r["ok"] else "⚠️ Test problem tapdı — aşağıya bax"
        except TimeoutError:
            note = (
                "❌ Test 45 san.-də bitmədi — çox güman keyring (gnome-keyring / kwallet) parol gözləyir. "
                "🔐 Keyring: BASICTEXT sına və ya cookies.txt istifadə et."
            )
    else:
        await cb.answer()
    text, kb = await asyncio.to_thread(web_profile_view, note)
    await safe_edit_cb(cb, text, kb)


async def _mcb_dlf(cb: CallbackQuery, parts: list, action: str):
    note = ""
    if action != "dlf":
        key = "dl:direct_m4a" if action == "dlf_direct" else "dl:ffmpeg"
        db = get_db()
        cur = (db.get_setting(key) or "on").strip().lower() in ("1", "on", "true", "yes")
        await asyncio.to_thread(db.set_setting, key, "off" if cur else "on")
        await asyncio.to_thread(db.set_setting, "dl:fmt_mode", "manual")
        label = "⚡ Birbaşa m4a" if action == "dlf_direct" else "🎞 ffmpeg çevirmə"
        note = f"{'❌ Bağlandı' if cur else '✅ Açıldı'}: {label} — növbəti yükləmədən etibarən"
        await cb.answer(note)
    else:
        await cb.answer()
    text, kb = dl_format_view(note)
    await safe_edit_cb(cb, text, kb)


async def waiting_yt_input(message: Message) -> bool:
    return yt_inp.get("stage") == "await_bp" and is_creator(message.from_user.id)


async def yt_batch_parallel_input(message: Message):
    ctx = get_context()
    from bot.core import youtube_handler as yh

    raw = message.text.strip().lower()
    try:
        await message.delete()
    except Exception as _e:
        logger.debug("yt_batch_parallel_input delete message xətası: %r", _e)
    panel_args = dict(chat_id=yt_inp["chat_id"], message_id=yt_inp["panel_id"], parse_mode="HTML")
    note = ""
    if raw in ("-", "min", "off"):
        v = yh.set_batch_parallel(1)
        note = f"✅ Toplu yükləmə: {v} paralel (minimum)"
    elif raw in ("+", "max", "sonsuz"):
        v = yh.set_batch_parallel(10)
        note = f"✅ Toplu yükləmə: {v} paralel (maksimum)"
    elif raw.isdigit():
        val = int(raw)
        v = yh.set_batch_parallel(val)
        note = f"✅ Toplu yükləmə: {v} paralel"
    else:
        text = (
            crumb(SECTIONS["mus"]) + " › 🎵 <b>YouTube › Toplu yükləmə</b>\n"
            + LINE + "\n"
            f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
            "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
            "• <b>Rəqəm</b> (məs: <code>3</code>)\n"
            "• <b>+</b> (maksimum: 10)\n"
            "• <b>-</b> (minimum: 1)\n"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "yt:pick:bp")]])
        with contextlib.suppress(Exception):
            await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
        return

    yt_inp["stage"] = None
    text, kb = yt_picker_view(note)
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)


async def waiting_yta_file(message: Message) -> bool:
    return yta_inp.get("stage") == "await_file" and bool(message.from_user) and is_creator(message.from_user.id)


async def yta_file_input(message: Message):
    ctx = get_context()
    from bot.core import yt_accounts as ya

    doc = message.document
    replace_id = yta_inp.get("acc") or ""
    yta_inp["stage"] = None
    data = None
    note = ""
    try:
        if doc.file_size and doc.file_size > ya.MAX_COOKIE_FILE:
            raise ValueError("fayl çox böyükdür")
        buf = io.BytesIO()
        await ctx.bot.download(doc, destination=buf)
        data = buf.getvalue()
    except Exception as e:
        note = f"❌ Fayl alınmadı: {escape(str(e)[:150])}"
    finally:
        with contextlib.suppress(Exception):
            await message.delete()  # cookie faylı çatda qalmasın
    if data is not None:
        try:
            acc, info = await asyncio.to_thread(
                ya.add_file, data, (message.caption or "").strip(), replace_id
            )
            note = (
                ("🔄 cookies.txt yeniləndi" if replace_id else "➕ Hesab əlavə olundu (rol: 📦 depo)")
                + f" — ✅ girişli · {info['yt']} YouTube cookie"
            )
            await yta_show(yta_acc_view(acc["id"], note))
            return
        except ValueError as e:
            note = f"❌ {escape(str(e))}"
        except Exception as e:
            note = f"❌ Saxlanmadı: {escape(str(e)[:150])}"
    await yta_show(yta_acc_view(replace_id, note) if replace_id else yta_list_view(note))


async def waiting_yta_name(message: Message) -> bool:
    return yta_inp.get("stage") == "await_name" and bool(message.from_user) and is_creator(message.from_user.id)


async def yta_name_input(message: Message):
    from bot.core import yt_accounts as ya

    aid = yta_inp.get("acc")
    yta_inp["stage"] = None
    with contextlib.suppress(Exception):
        await message.delete()
    name = message.text.strip()[:40]
    if aid and name and ya.update(aid, name=name):
        await yta_show(yta_acc_view(aid, f"✏️ Ad: {escape(name)}"))
    else:
        await yta_show(yta_acc_view(aid, "⚠️ Ad dəyişmədi") if aid else yta_list_view())


async def waiting_yta_link(message: Message) -> bool:
    return (yta_inp.get("stage") in ("await_lname", "await_lurl") and bool(message.from_user)
            and is_creator(message.from_user.id))


async def yta_link_input(message: Message):
    """🔘 Düymənin adı (ad dəyişmə) və ya öz linki (yeni düymə: ad linkdən götürülür, sonra ✏️ ilə dəyişilir)."""
    from bot.core import yt_accounts as ya

    aid, stage, idx = yta_inp.get("acc"), yta_inp.get("stage"), yta_inp.get("idx", -1)
    yta_inp["stage"] = None
    with contextlib.suppress(Exception):
        await message.delete()
    text = (message.text or "").strip()
    try:
        if stage == "await_lname":
            if not ya.rename_link(aid, idx, text):
                raise ValueError("ad dəyişmədi")
            note = f"✏️ Ad: {escape(ya.clean_link_name(text))}"
        else:
            url = ya.normalize_url(text)
            host = url.split("://", 1)[1].split("/", 1)[0].removeprefix("www.")
            ya.add_link(aid, f"🔗 {host}", url)
            note = f"➕ {escape(host)} əlavə olundu — ✏️ ilə öz adını ver"
    except ValueError as e:
        note = f"⚠️ {escape(str(e))}"
    await yta_show(yta_links_view(aid, note))


def register_youtube_handlers(router: Router):
    router.message.register(
        yt_batch_parallel_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_yt_input,
    )
    router.message.register(
        yta_file_input,
        F.chat.type == "private",
        F.document,
        waiting_yta_file,
    )
    router.message.register(
        yta_link_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_yta_link,
    )
    router.message.register(
        yta_name_input,
        F.chat.type == "private",
        F.text,
        ~F.text.startswith("/"),
        waiting_yta_name,
    )
