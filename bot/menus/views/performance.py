"""
bot.menus.views.performance
CPU hovuzu, performans, RAM diski, L1 keşi və donma monitoru pəncərələri.
"""

import logging
import os
import time
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.views.base import LINE, SECTIONS, crumb
from common.formatting import progress_bar
from common.menu import _btn, _dot, _module_ok

logger = logging.getLogger(__name__)


def mode_btn(label: str, auto: bool, prefix: str) -> InlineKeyboardButton:
    """🤖 Avto / ✋ Manual — callback <prefix>:auto|manual (handler yalnız bu iki dəyəri qəbul edir)."""
    return panel.mode(label, auto, f"menu:{prefix}:{'manual' if auto else 'auto'}")


def perf_button_label() -> str:
    try:
        from bot.core import cpu_pool as cp
    except Exception:
        return "⚙️ Performans · ❌"
    warn = cp.health()
    icon = "🧠" if cp.mode() == "process" else "🧵"
    return f"⚙️ Performans · {icon} {cp.CORES} nüvə" + (f" · ⚠️ {warn}" if warn else "")


def _ms(sec: float) -> str:
    return f"{sec * 1000:.0f} ms" if sec < 1 else f"{sec:.1f} san"


PERF_PICK_CONFIG = {
    "c": {
        "title": "🎯 CPU hədəfi",
        "unit": "%",
        "desc": "Avto-miqyas zamanı hədəflənən maksimum CPU yükü (10–100%).",
        "min": 10,
        "max": 100,
        "getter": lambda cp: cp.cpu_target(),
        "setter": lambda cp, v: cp.set_cpu_target(v),
        "stepper": lambda cp, d: cp.step_target_delta(d),
    },
    "r": {
        "title": "💾 RAM ehtiyatı",
        "unit": "MB",
        "desc": "Sistem üçün saxlanılan minimum ehtiyat RAM miqdarı (MB).",
        "min": 0,
        "max": 32768,
        "getter": lambda cp: cp.ram_reserve(),
        "setter": lambda cp, v: cp.set_ram_reserve(v),
        "stepper": lambda cp, d: cp.step_reserve_delta(d * 50 if abs(d) == 5 else d * 10),
    },
    "u": {
        "title": "👤 İstifadəçi prosesləri",
        "unit": "proses",
        "desc": "İstifadəçi sorğuları və ani yükləmələr üçün proses limiti.",
        "min": 1,
        "max": 64,
        "getter": lambda cp: cp.user_procs(),
        "setter": lambda cp, v: cp.set_user_procs(v),
        "stepper": lambda cp, d: cp.step_user_delta(d),
    },
    "d": {
        "title": "📦 Depo prosesləri",
        "unit": "proses",
        "desc": "Depo doldurucu və fonda yükləmələr üçün proses limiti.",
        "min": 1,
        "max": 64,
        "getter": lambda cp: cp.depo_procs(),
        "setter": lambda cp, v: cp.set_depo_procs(v),
        "stepper": lambda cp, d: cp.step_depo_delta(d),
    },
    "t": {
        "title": "🧵 Thread sayı",
        "unit": "thread",
        "desc": "Baza, fayl və Spotify üçün thread hovuzu ölçüsü.",
        "min": 16,
        "max": 512,
        "getter": lambda cp: cp.threads(),
        "setter": lambda cp, v: cp.set_threads(v),
        "stepper": lambda cp, d: cp.step_threads_delta(d * 8 if abs(d) == 5 else d * 2),
    },
    "n": {
        "title": "🐢 nice prioriteti",
        "unit": "",
        "desc": "İşçi proseslərin nice prioriteti (+0 ən yüksək, +19 ən aşağı).",
        "min": 0,
        "max": 19,
        "getter": lambda cp: cp.nice(),
        "setter": lambda cp, v: cp.set_nice(v),
        "stepper": lambda cp, d: cp.step_nice_delta(d),
    },
}

PERF_INFO = {
    "turbo": "🔥 Tam performans: Resurslar maksimuma çatdırılır.",
    "u": "👤 İstifadəçi zolağı: yt-dlp proseslərinin sayı.",
    "d": "📦 Depo zolağı: Doldurucunun paralel prosesləri.",
    "t": "🧵 Thread hovuzu: Qısa I/O əməliyyatları üçün thread sayı.",
    "s": "🚀 Avto-miqyas: Yükə görə proseslərin avtomatik tənzimlənməsi.",
    "c": "🎯 CPU hədəfi: Avto-miqyas üçün CPU faiz həddi.",
    "r": "💾 RAM ehtiyatı: Serveri OOM qəzasından qoruyan ehtiyat RAM.",
    "n": "🐢 nice: İşçi proseslərin prioriteti (0-19).",
    "auto": "🤖 Avto: 🧵 thread sayı növbə və bot gecikməsinə görə artıb-azalır; 🐢 nice bot ləngiyəndə artır (işçilər botu sıxmasın), sakitləşəndə +5-ə qayıdır. Turbo açıq olanda əl ilə dəyərlər keçərlidir.",
}


def perf_hub_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    try:
        from bot.core import cpu_pool as cp
    except Exception as e:
        return f"❌ bot/core/cpu_pool.py yüklənmədi: <code>{escape(str(e))}</code>", InlineKeyboardMarkup(
            inline_keyboard=[nav_row("sys")]
        )
    from bot.core.audio_cache import l1_cache_stats
    from bot.core.youtube_handler import (
        DL_STATS,
        is_parallel_dl_enabled,
        is_smart_copy_enabled,
    )
    from common.utilities import is_accel_auto_enabled, ram_disk_stats

    auto_on = is_accel_auto_enabled()
    l1 = l1_cache_stats()
    rd = ram_disk_stats()
    sc_on = is_smart_copy_enabled()
    pdl_on = is_parallel_dl_enabled()

    mode_tag = " · 🤖 Avto" if auto_on else " · ✋ Manual"
    l1_st = f"🟢 Aktiv (L1 RAM{mode_tag})" if l1["enabled"] else f"🔴 Deaktiv ({mode_tag.strip(' ·')})"
    rd_st = f"🟢 Aktiv ({rd['path']}{mode_tag})" if rd["enabled"] else f"🔴 Deaktiv ({mode_tag.strip(' ·')})"
    sc_st = f"🟢 Aktiv (0.05s / -c copy{mode_tag})" if sc_on else f"🔴 Deaktiv ({mode_tag.strip(' ·')})"
    pdl_st = f"🟢 Aktiv (4x Paralel{mode_tag})" if pdl_on else f"🔴 Deaktiv ({mode_tag.strip(' ·')})"

    cryptg_ok = _module_ok("cryptg")
    orjson_ok = _module_ok("orjson")
    aiodns_ok = _module_ok("aiodns")
    uvloop_ok = _module_ok("uvloop")

    m = cp.mode()
    w = cp.WATCHDOG.snapshot()
    lines = [crumb(SECTIONS["sys"], "⚙️ Performans") + "\n" + LINE]

    if w.get("now_frozen"):
        lines.append("🔴 <b>Bot hazırda donub!</b>")
    elif w.get("freezes_hour"):
        lines.append(f"🟡 <b>Son 1 saatda {w['freezes_hour']} donma</b> (>{cp.FREEZE_AT:.0f} san.)")
    else:
        lines.append("🫀 <b>Event loop:</b> 🟢 Sərbəst (donma qeydə alınmayıb)")
    if w.get("last"):
        ts, dur, where = w["last"]
        ago = int(time.time() - ts)
        lines.append(f"     Son donma: <b>{dur:.1f} san.</b>, {ago} san. əvvəl")

    from bot.core import latency
    lines += latency.summary_lines()

    turbo = getattr(cp, "turbo_on", getattr(cp, "is_turbo", lambda: False))()
    scale = getattr(cp, "autoscale_on", getattr(cp, "is_auto_scaling", lambda: False))()
    lines.append(f"🔥 <b>Rejim:</b> {'🔥 Turbo' if turbo else 'Standart'} · <b>{cp.CORES} CPU nüvə</b>")

    lines.append("━━━━━━━━━━━━━━━━━━")
    mode_header = "🤖 <b>Avtomatik</b> (Ağıllı Resurs İdarəsi)" if auto_on else "✋ <b>Manual</b> (Əl ilə Fərdi İdarə)"
    lines.append(f"⚡ <b>Sürətləndirmə və RAM:</b> {mode_header}")
    lines.append(
        f"🧠 <b>L1 Audio Keşi:</b> {l1_st} · <b>{l1['size']}/{l1['max']}</b> trek · hit: <b>{l1['hits']}</b> ({l1['hit_rate']:.1f}%)"
    )
    lines.append(
        f"⚡ <b>RAM Diski:</b> {rd_st} · <b>{rd['files_count']} fayl</b> ({rd['used_mb']:.1f} MB) · boş: {rd['free_gb']:.1f} GB"
    )
    lines.append(f"🎯 <b>FFmpeg Copy:</b> {sc_st} · <b>{DL_STATS.get('stream_copied', 0)}</b> trek kopyalanıb")
    lines.append(f"🚀 <b>Paralel Axın:</b> {pdl_st}")
    lines.append(
        f"🏎 <b>C Mühərriklər:</b> {_dot(cryptg_ok)} cryptg · {_dot(orjson_ok)} orjson · {_dot(aiodns_ok)} aiodns · {_dot(uvloop_ok)} uvloop"
    )
    t_st = cp.thread_pool_stats() if hasattr(cp, "thread_pool_stats") else {}
    t_act = t_st.get("active", 0)
    t_q = t_st.get("queued", 0)
    t_done = t_st.get("done", 0)
    t_thr = t_st.get("threads", 0)
    t_details = f"<b>{t_act}/{cp.threads()}</b> aktiv"
    if t_q:
        t_details += f" · ⏳ <b>{t_q}</b> növbədə"
    if t_thr:
        t_details += f" · 🧵 {t_thr} yaradılıb"
    lines.append(f"🧵 <b>Thread Hovuzu:</b> {t_details} · tamamlanan: <b>{t_done}</b>")

    if note:
        lines.append(f"\n{note}")

    rows = [
        [panel.mode("⚙️ Sürətləndirmə Rejimi", auto_on, "menu:accel_t:auto")],
    ]
    if auto_on:
        rows.append(
            [
                InlineKeyboardButton(text="🧹 L1 Keşi Boşalt", callback_data="menu:accel_c:l1"),
                InlineKeyboardButton(text="🧹 RAM Diski Təmizlə", callback_data="menu:accel_c:ramdisk"),
            ]
        )
    else:
        rows.extend(
            [
                [
                    panel.toggle("🧠 L1 RAM Keşi", l1["enabled"], "menu:accel_t:l1", compact=True),
                    InlineKeyboardButton(text="🧹 L1 Keşi Boşalt", callback_data="menu:accel_c:l1"),
                ],
                [
                    panel.toggle("⚡ RAM Diski", rd["enabled"], "menu:accel_t:ramdisk", compact=True),
                    InlineKeyboardButton(text="🧹 RAM Diski Təmizlə", callback_data="menu:accel_c:ramdisk"),
                ],
                [
                    panel.toggle("🎯 Ağıllı Copy", sc_on, "menu:accel_t:smartcopy", compact=True),
                    panel.toggle("🚀 Paralel DL", pdl_on, "menu:accel_t:paralleldl", compact=True),
                ],
            ]
        )
    rows.extend(
        [
            [panel.toggle("🔥 Tam performans", turbo, "menu:perf:turbo"), _btn("ℹ️", "perf:info:turbo")],
            [
                panel.btn(
                    "Rejim: 🧠 Proses  ›  🧵 Thread" if m == "process" else "Rejim: 🧵 Thread  ›  🧠 Proses",
                    "menu:perf:mode:thread" if m == "process" else "menu:perf:mode:process",
                ),
                panel.mode("⚙️ Seçim", cp.mode_auto(), f"menu:perf:mode:{'manual' if cp.mode_auto() else 'auto'}"),
            ],
        ]
    )
    if m == "process":
        mx = "maks " if scale else ""
        rows.append([mode_btn("🚀 Proses sayı", scale, "perf:asm"), _btn("🧮 Nüvələr", "perf:cores")])
        if scale:
            rows.append(
                [
                    _btn(f"🎯 CPU hədəfi: {cp.cpu_target()}%", "perf:pick:c"),
                    _btn(f"💾 RAM ehtiyatı: {cp.ram_reserve()} MB", "perf:pick:r"),
                ]
            )
        else:
            rows.append(
                [
                    _btn(f"👤 İstifadəçi: {mx}{cp.user_procs()}", "perf:pick:u"),
                    _btn(f"📦 Depo: {mx}{cp.depo_procs()}", "perf:pick:d"),
                ]
            )
    t_btn = f"🧵 Thread: {cp.threads()}" + (f" ({t_act} aktiv)" if t_act else "")
    t_auto, n_auto = cp.threads_auto(), cp.nice_auto()
    if cp.turbo_on():
        lines.append("🔥 <i>Tam performans açıqdır: thread və nice Avto işləmir (turbo öz dəyərlərini qoyur). "
                     "Avto üçün turbonu söndürün.</i>")
    rows += [
        [
            panel.mode(f"🧵 Thread {cp.threads()}" if t_auto else "🧵 Thread", t_auto,
                       f"menu:perf:tauto:{'manual' if t_auto else 'auto'}"),
            panel.mode(f"🐢 nice +{cp.nice()}" if n_auto else "🐢 nice", n_auto,
                       f"menu:perf:nauto:{'manual' if n_auto else 'auto'}"),
        ],
        ([_btn(t_btn, "perf:pick:t")] if not t_auto else []) + ([_btn(f"🐢 nice: +{cp.nice()}", "perf:pick:n")] if not n_auto else [])
        or [_btn("ℹ️ Avto necə işləyir", "perf:info:auto")],
        [
            panel.toggle("🫀 Watchdog", cp.WATCHDOG.enabled, "menu:perf:wd", compact=True),
            _btn("⚡ Hovuz proseslərini yenilə", "perf:restart"),
        ],
        [_btn("⚡ Avto-tənzimlə", "perf:auto")],
        nav_row("sys", refresh="perf"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def perf_picker_view(key: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import cpu_pool as cp

    cfg = PERF_PICK_CONFIG.get(key)
    if not cfg:
        return perf_hub_view(note)
    cur = cfg["getter"](cp)
    unit = f" {cfg['unit']}" if cfg["unit"] else ""
    lines = [
        crumb(SECTIONS["sys"]) + f" › ⚙️ <b>Performans › {cfg['title']}</b>",
        LINE,
        f"{cfg['title']}:\n",
        f"Hazırkı dəyər: <b>{cur}{unit}</b>\n",
        f"<i>{cfg['desc']}</i>",
    ]
    if key == "t" and hasattr(cp, "thread_pool_stats"):
        st = cp.thread_pool_stats()
        lines.append(f"\n📊 <b>Canlı vəziyyət:</b> <b>{st.get('active', 0)}</b> aktiv · <b>{st.get('queued', 0)}</b> növbədə · <b>{st.get('threads', 0)}</b> işçi thread")
    if note:
        lines.append(f"\n{note}")
    rows = panel.stepper(f"menu:perf:vstep:{key}:", f"menu:perf:vinp:{key}") + [nav_row("perf")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def perf_cores_view() -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import cpu_pool as cp

    mon = cp.MON
    lines = [f"🧮 <b>Nüvələr</b> — {cp.CORES} nüvə · ümumi {mon.cpu:.0f}%", ""]
    if not mon.cores:
        lines.append("<i>Ölçülür — bir neçə saniyə sonra 🔄 bas.</i>")
    else:
        rows = [f"{i:>2} {progress_bar(v, width=8)} {v:>3.0f}%" for i, v in enumerate(mon.cores)]
        pairs = (
            [rows[i] + ("   " + rows[i + 1] if i + 1 < len(rows) else "") for i in range(0, len(rows), 2)]
            if len(rows) > 8
            else rows
        )
        from common.menu import wrap_ui_text

        lines.append(wrap_ui_text(escape("\n".join(pairs))))
        busy = sum(1 for v in mon.cores if v >= 80)
        idle = sum(1 for v in mon.cores if v < 20)
        lines.append(f"🔥 dolu (≥80%): {busy} · 💤 boş (&lt;20%): {idle}")
    lines.append(f"🤖 bot: {mon.bot_cpu:.0f}% · ⚙️ işçilər+ffmpeg: {mon.work_cpu:.0f}% <i>(maşının %-i)</i>")
    try:
        l1, l5, l15 = os.getloadavg()
        lines.append(f"📈 load: {l1:.2f} {l5:.2f} {l15:.2f} <i>(nüvə sayından çox — növbə var)</i>")
    except OSError:
        pass
    rows = [nav_row("perf", refresh="perf:cores")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "PERF_INFO",
    "perf_button_label",
    "perf_cores_view",
    "perf_hub_view",
    "perf_picker_view",
]
