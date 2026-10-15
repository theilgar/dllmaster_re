"""
bot.menus.handlers.performance_flow
Performans sazlamaları, CPU hovuzu, proseslər və resurs idarəetmə axını.
"""

import asyncio
import contextlib
import logging
import time

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.menus.state import perf_inp, safe_edit_cb
from bot.menus.views.base import LINE, SECTIONS, crumb
from bot.menus.views.performance import (
    PERF_INFO,
    PERF_PICK_CONFIG,
    perf_cores_view,
    perf_hub_view,
    perf_picker_view,
)
from common.database import get_db
from common.diagnostics import server_snapshot
from common.menu import _btn

logger = logging.getLogger(__name__)


async def _mcb_perf(cb: CallbackQuery, parts: list, action: str):
    from bot.core import cpu_pool as cp

    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""
    note = ""
    if sub == "rmt":
        await _mcb_remote(cb, arg)
        return
    if sub == "pick" and arg in PERF_PICK_CONFIG:
        perf_inp["stage"] = None
        await cb.answer()
        await safe_edit_cb(cb, *perf_picker_view(arg))
        return
    elif sub == "vstep" and arg in PERF_PICK_CONFIG:
        delta = int(parts[4]) if len(parts) > 4 else 1
        cfg = PERF_PICK_CONFIG[arg]
        v = cfg["stepper"](cp, delta)
        unit = f" {cfg['unit']}" if cfg["unit"] else ""
        await cb.answer(f"{cfg['title']}: {v}{unit}")
        await safe_edit_cb(cb, *perf_picker_view(arg, f"✓ {cfg['title']}: {v}{unit}"))
        return
    elif sub == "vinp" and arg in PERF_PICK_CONFIG:
        await cb.answer()
        cfg = PERF_PICK_CONFIG[arg]
        cur = cfg["getter"](cp)
        unit = f" {cfg['unit']}" if cfg["unit"] else ""
        perf_inp.update(
            stage="await_val",
            key=arg,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        text = (
            crumb(SECTIONS["sys"]) + f" › ⚙️ <b>Performans › {cfg['title']}</b>\n"
            + LINE + "\n"
            f"Hazırkı dəyər: <b>{cur}{unit}</b>\n\n"
            f"✍️ <b>{cfg['title']} dəyərini daxil edin:</b>\n"
            f"• <b>Rəqəm</b> yazın (min: <code>{cfg['min']}</code>, maks: <code>{cfg['max']}</code>)\n"
            f"• <b>+</b> yazın — <b>maksimum ({cfg['max']})</b> olsun\n"
            f"• <b>-</b> yazın — <b>minimum ({cfg['min']})</b> olsun\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[_btn("✖️ Ləğv et", f"perf:pick:{arg}")]]
        )
        await safe_edit_cb(cb, text, kb)
        return
    elif sub == "mode" and arg in cp.MODES:
        cp.set_mode(arg)
        await cb.answer(cp.MODES[arg])
    elif sub == "mode" and arg in ("auto", "manual"):
        if arg == "auto":
            cp.set_mode_auto()
            await cb.answer(f"🤖 Avto: serverə görə {cp.MODES[cp.mode()]}")
        else:
            cp.set_mode(cp.mode())
            await cb.answer("✋ Manual — rejimi özün seç")
    elif sub in ("tauto", "nauto") and cp.turbo_on():
        await cb.answer("🔥 Tam performans açıqdır — dəyərləri o idarə edir. Avto üçün əvvəlcə onu söndürün.", show_alert=True)
        return
    elif sub == "tauto" and arg in ("auto", "manual"):
        cp.set_threads_auto(arg == "auto")
        await cb.answer("🤖 Avto: thread sayını bot tənzimləyir" if arg == "auto" else "✋ Manual — thread sayını özün seç")
    elif sub == "nauto" and arg in ("auto", "manual"):
        cp.set_nice_auto(arg == "auto")
        await cb.answer("🤖 Avto: nice-i bot tənzimləyir" if arg == "auto" else "✋ Manual — nice-i özün seç")
    elif sub in ("u", "d", "t", "n") and arg in ("+", "-"):
        fn = {"u": cp.step_user, "d": cp.step_depo, "t": cp.step_threads, "n": cp.step_nice}[
            sub
        ]
        v = fn(1 if arg == "+" else -1)
        await cb.answer(
            {
                "u": f"👤 {v} proses",
                "d": f"📦 {v} proses",
                "t": f"🧵 {v} thread",
                "n": f"🐢 nice +{v} — ♻️ ilə bütün proseslərə",
            }[sub]
        )
    elif sub == "asm" and arg in ("auto", "manual"):
        if (arg == "auto") != cp.autoscale_on():
            cp.toggle_autoscale()
        await cb.answer(
            "🤖 Avto-miqyas — proses sayını bot özü tənzimləyir"
            if arg == "auto"
            else "✋ Manual — proses sayını özün seç"
        )
    elif sub == "as":
        on = cp.toggle_autoscale()
        await cb.answer("🚀 Avto-miqyas " + ("açıldı" if on else "söndürüldü — ölçülər sabit"))
    elif sub == "c" and arg in ("+", "-"):
        await cb.answer(f"🎯 CPU hədəfi {cp.step_target(1 if arg == '+' else -1)}%")
    elif sub == "r" and arg in ("+", "-"):
        await cb.answer(f"💾 RAM ehtiyatı {cp.step_reserve(1 if arg == '+' else -1)} MB")
    elif sub == "frz":                                    # 📄 bot_logs/freeze_*.txt (+ shutdown_hang_*.txt) fayl kimi
        import glob
        import os
        from aiogram.types import FSInputFile
        root = cp.ROOT
        files = sorted(glob.glob(os.path.join(root, "bot_logs", "freeze_*.txt*")), key=os.path.getmtime)
        hangs = sorted(glob.glob(os.path.join(root, "bot_logs", "shutdown_hang_*.txt")), key=os.path.getmtime)
        w = cp.WATCHDOG.snapshot()
        if not files and not hangs:
            await cb.answer("Donma logu yoxdur — 1.5 san.-dən uzun donma qeydə alınmayıb", show_alert=True)
            return
        await cb.answer("📄 Göndərilir...")
        sent = []
        for path in ([files[-1]] if files else []) + ([hangs[-1]] if hangs else []):
            with contextlib.suppress(Exception):
                await cb.message.answer_document(
                    FSInputFile(path), caption=f"📄 <code>{os.path.relpath(path, root)}</code> · "
                    f"{os.path.getsize(path) // 1024 + 1} KB · {time.strftime('%d.%m %H:%M', time.localtime(os.path.getmtime(path)))}",
                    parse_mode="HTML")
                sent.append(os.path.basename(path))
        note = ("📄 Göndərildi: " + ", ".join(sent)) if sent else "❌ Fayl göndərilmədi"
        if w.get("freezes_hour"):
            note += f"\n🟡 Son 1 saatda {w['freezes_hour']} donma"
        from bot.menus.views.performance import latency_detail_view
        await safe_edit_cb(cb, *latency_detail_view(note))
        return
    elif sub == "lat" or sub == "latr":
        from bot.core import latency
        from bot.menus.views.performance import latency_detail_view
        note = ""
        if sub == "latr":
            note = f"🗑 {latency.reset()} ölçü silindi"
        await cb.answer()
        await safe_edit_cb(cb, *latency_detail_view(note))
        return
    elif sub == "cores":
        await cb.answer()
        await safe_edit_cb(cb, *perf_cores_view())
        return
    elif sub == "info" and arg in PERF_INFO:
        await cb.answer(PERF_INFO[arg][:200], show_alert=True)
        return
    elif sub == "wd":
        on = cp.toggle_watchdog()
        await cb.answer("🫀 Watchdog " + ("açıldı" if on else "söndürüldü"))
    elif sub == "restart":
        await cp.restart_pools()
        await cb.answer("♻️ Proseslər bağlandı — növbəti tapşırıqda yeniləri açılacaq")
        note = "♻️ <i>Bütün işçi proseslər yeniləndi (yeni nice / ölçü tətbiq olundu)</i>"
    elif sub == "lowres":
        if cp.lowres_on():
            await asyncio.to_thread(cp.disable_lowres)
            await cb.answer("🪶 Söndürüldü — əvvəlki ayarlar qaytarıldı")
            note = "↩️ <b>Aşağı resurs profili söndürüldü</b> — əvvəlki ayarlar bərpa olundu"
        else:
            had_turbo = cp.turbo_on()
            await asyncio.to_thread(cp.enable_lowres)
            await cb.answer("🪶 Aşağı resurs profili açıldı")
            note = (("↩️ Tam performans söndürüldü.\n" if had_turbo else "")
                    + "🪶 <b>Aşağı resurs:</b> 👤 2 · 📦 2 proses · RAM ehtiyatı 768 MB · CPU hədəfi 85% · "
                    "RAM diski və paralel fraqment söndürülüb · toplu 2 paralel · 🧵/🐢 Avto\n"
                    "<i>♻️ Prosesləri yenilə — açıq proseslərə də tətbiq olunsun. "
                    "DB hovuzu üçün <code>DB_POOL_MAX=12</code> (.env) tövsiyə olunur.</i>")
    elif sub == "turbo":
        if getattr(cp, "turbo_on", lambda: False)():
            await asyncio.to_thread(cp.disable_turbo)
            await cb.answer("🔥 Söndürüldü — əvvəlki ayarlar qaytarıldı")
            note = "↩️ <b>Tam performans söndürüldü</b> — əvvəlki ayarlar bərpa olundu"
        else:
            snap = server_snapshot()
            free_mb = (
                int((snap.get("ram_total", 0) - snap.get("ram_used", 0)) / 1048576)
                or cp.MON.avail_mb
            )
            plan = await asyncio.to_thread(cp.enable_turbo, free_mb)
            await cb.answer("🔥 Tam performans açıldı")
            note = (
                f"🔥 <b>Tam performans:</b> {cp.CORES} nüvə, ~{free_mb} MB boş RAM → "
                f"👤 maks {plan[cp.S_USER]} · 📦 maks {plan[cp.S_DEPO]} proses · "
                f"🧵 {plan[cp.S_THREADS]} thread · nice 0 · 🎯 CPU 100% · "
                f"⬇️ toplu {plan[cp.S_BATCH_PAR]} paralel\n"
                "<i>♻️ Prosesləri yenilə — açıq proseslərə də nice 0 tətbiq olunsun.</i>"
            )
    elif sub == "auto":
        snap = server_snapshot()
        free_mb = (snap.get("ram_total", 0) - snap.get("ram_used", 0)) / 1048576
        depo_cap = max(1, int(free_mb / 4 / 75)) if free_mb else cp.CORES * 2
        u = max(2, min(4, cp.CORES))
        d = max(1, min(cp.CORES * 2, depo_cap, cp.DEPO_STEPS[-1]))
        t = max(64, cp.CORES * 8)
        db = get_db()
        for k, v in (
            (cp.S_MODE, "process" if cp.CORES > 1 else "thread"),
            (cp.S_USER, u),
            (cp.S_DEPO, d),
            (cp.S_THREADS, min(t, cp.THREAD_STEPS[-1])),
            (cp.S_NICE, 5),
            (cp.S_SCALE, "on"),
        ):
            await asyncio.to_thread(db.set_setting, k, str(v))
        cp.LANES["user"].set_max(u)
        cp.LANES["depo"].set_max(d)
        cp.apply_threads(force=True)
        await cb.answer("⚡ Tənzimləndi")
        note = (
            f"⚡ <b>Avto-tənzimləndi:</b> {cp.CORES} nüvə, ~{free_mb:.0f} MB boş RAM → "
            f"👤 maks {u} · 📦 maks {d} proses · 🧵 {cp.threads()} thread · nice +5 · 🚀 avto-miqyas ✅"
        )
    else:
        await cb.answer()
    await safe_edit_cb(cb, *perf_hub_view(note))


# ── 🌐 Uzaq server (bot/core/remote.py) ──
_RMT_PROMPTS = {
    "host": ("✏️ Server ünvanı", "<code>istifadəçi@ip</code> yazın (məs. <code>root@203.0.113.5</code>). "
             "Boş göndərmək üçün <b>-</b> yazın.\n<i>Evdən bu serverə parolsuz (SSH açarı ilə) qoşula bilməlisiniz: "
             "<code>ssh-copy-id istifadəçi@ip</code></i>"),
    "port": ("🔌 SSH portu", "Rəqəm yazın (standart: <code>22</code>)."),
    "key": ("🔑 SSH açar faylı", "Açarın yolunu yazın (məs. <code>~/.ssh/id_ed25519</code>). "
            "Standart açarlar üçün <b>-</b> yazın."),
}


async def _mcb_remote(cb: CallbackQuery, arg: str):
    from bot.core import remote
    from bot.menus.views.performance import remote_view

    note = ""
    if arg.startswith("e") and arg[1:] in _RMT_PROMPTS:
        what = arg[1:]
        title, hint = _RMT_PROMPTS[what]
        perf_inp.update(stage="await_val", key=f"rmt_{what}", chat_id=cb.message.chat.id,
                        panel_id=cb.message.message_id)
        await cb.answer()
        await safe_edit_cb(cb, crumb(SECTIONS["sys"]) + f" › 🌐 <b>Uzaq server › {title}</b>\n" + LINE + "\n" + hint,
                           InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "perf:rmt")]]))
        return
    perf_inp["stage"] = None
    if arg == "on":
        if not remote.enabled() and not remote.configured():
            await cb.answer("Əvvəl server ünvanını daxil edin", show_alert=True)
            return
        on = remote._S.toggle("on")
        remote.mark_up()
        await cb.answer("🌐 Açıldı" if on else "⚪ Söndürüldü — hər şey evdə")
    elif arg == "dl":
        v = remote._S.toggle("download", "1")
        await cb.answer("⬇️ Yükləmə serverdə" if v else "⬇️ Yükləmə evdə (upload da evdə olacaq)")
    elif arg == "up":
        v = remote._S.toggle("upload", "1")
        await cb.answer("⬆️ Upload serverdən" if v else "⬆️ Upload evdən")
    elif arg in ("p+", "p-"):
        n = remote._S.int("procs", 2, lo=1, hi=4) + (1 if arg == "p+" else -1)
        remote._S.put("procs", max(1, min(4, n)))
        await cb.answer(f"👥 Paralel: {remote.procs()}")
    elif arg == "info":
        await cb.answer("Serverdə eyni anda neçə yükləmə / upload getsin. 2 GB RAM üçün 1–2 məsləhətdir.", show_alert=True)
        return
    elif arg == "test":
        await cb.answer("🧪 Yoxlanılır...")
        res = await remote.check()
        if res["ok"]:
            i = res["info"]
            note = (f"✅ <b>Server işləyir</b> · RAM {i.get('ram_mb')} MB · boş disk {i.get('free_mb')} MB · "
                    f"yt-dlp {i.get('yt_dlp')} · JS: {', '.join(i.get('js') or []) or '❌ yoxdur'} · "
                    f"ffmpeg: {'✅' if i.get('ffmpeg') else '—'}")
            if not i.get("js"):
                note += "\n⚠️ <i>YouTube üçün serverdə deno və ya node lazımdır (sudo apt install nodejs)</i>"
        else:
            note = f"❌ {escape_html(res['error'])}"
    elif arg == "setup":
        if not remote.configured():
            await cb.answer("Əvvəl server ünvanını daxil edin", show_alert=True)
            return
        await cb.answer("🛠 Qurulur (1–3 dəq.)...")
        await safe_edit_cb(cb, *remote_view("⏳ <b>Server qurulur</b> — yt-dlp və aiogram yüklənir, gözləyin..."))
        try:
            note = escape_html("\n".join(await remote.setup()))
        except remote.RemoteUnavailable as e:
            note = f"❌ {escape_html(str(e))}"
    else:
        await cb.answer()
    await safe_edit_cb(cb, *remote_view(note))


def escape_html(text: str) -> str:
    from html import escape
    return escape(text)
