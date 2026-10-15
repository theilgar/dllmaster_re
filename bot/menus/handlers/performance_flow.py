"""
bot.menus.handlers.performance_flow
Performans sazlamaları, CPU hovuzu, proseslər və resurs idarəetmə axını.
"""

import asyncio
import logging

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
