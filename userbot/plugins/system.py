"""🖥 system.py — Sistem, diaqnostika, terminal və bot idarəetməsi.

Komandalar və panellər:
  • .alive (userbot/plugins/alive.py)       — uptime, ping, sistem və bot başlama vaxtı
  • .status                                  — userbot vəziyyəti kartı və paneli (📊 Status)
  • .fastfetch [san] / .ff [san]             — canlı sistem məlumatı (⏹ / 🔁 ilə)
  • .speedtest                               — internet sürət testi (Cloudflare)
  • .sh / .bash / .term / .cd / .pwd / .env  — linux terminal icrası (YALNIZ creator)
  • .stop                                    — bu çatdakı fon işlərini dayandır
  • .bmenu / .botmenu                        — botun /menu idarəetmə paneli kartı
  • .install / .plugins / .reload / .uninstall — userbot pluginlərinin canlı idarəsi
  • 📦 Depo                                  — depo doldurucu bölməsi və tənzimləmələri
"""
import asyncio
import logging
import math
import os
import re
import shlex
import signal
import time
from contextlib import suppress
from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardMarkup

from common.utilities import format_uptime, read_sys_uptime, safe_plugin_name
from userbot.core.userbot_api import (
    Bar,
    FF_DEFAULT,
    FF_INTERVAL,
    FF_MAX,
    LiveStats,
    Out,
    ff,
    fmt_span,
    logger,
    plain_name,
    render_fastfetch,
    safe_edit,
    static_info,
    telethon_version,
)

try:
    import aiohttp
except ImportError:
    aiohttp = None


# ─────────────────────────────────────────────────────────────
# 1. Speedtest Parametrləri və Köməkçiləri
# ─────────────────────────────────────────────────────────────
TEST_DURATION = 10.0          # hər mərhələnin maks. müddəti
STREAMS = 2                   # paralel axın
CHUNK = 64 * 1024
PING_COUNT = 5
SAMPLE_INTERVAL = 0.5         # qrafik nöqtəsi hər 0.5 san.
UI_INTERVAL = 1.5             # Telegram FloodWait-dən qorunmaq üçün

DOWN_REQ_BYTES = 50_000_000   # bir download sorğusu
DOWN_MAX_REQS = 12            # → maks. 600 MB
UP_REQ_BYTES = 10_000_000     # bir upload sorğusu
UP_MAX_REQS = 8               # → maks. 80 MB
DOWN_URL = None
CF = "https://speed.cloudflare.com"

SPARK = "▁▂▃▄▅▆▇█"
SPARK_W = 10


def _spark(vals) -> str:
    vals = vals[-SPARK_W:]
    if not vals:
        return ""
    hi = max(vals) or 1
    return "".join(SPARK[min(7, int(v / hi * 7.999))] for v in vals)


def _fmt_mbps(mbps: float) -> str:
    return f"{mbps / 1000:.2f} Gbps" if mbps >= 1000 else f"{mbps:.2f} Mbps"


def _speed_rows(label: str, s: dict) -> list:
    if s["phase"] == "wait":
        return [Bar(label, 0, "gözlənilir")]
    if s["phase"] == "run":
        out = [Bar(label, s["pct"], f"{s['pct']}% {_fmt_mbps(s['cur'])}")]
    else:
        out = [(label, f"✓ {_fmt_mbps(s['final'])}")]
    if s["hist"]:
        out.append(("Peak", f"{_spark(s['hist'])} {_fmt_mbps(s['peak'])}"))
    return out


def render_speedtest(st: dict) -> str:
    if st["ping"] is None:
        ping = "ölçülür..." if st["stage"] == "ping" else "—"
    else:
        ping = f"{st['ping']:.1f} ms ±{st['jitter']:.1f}"
    server = st["server"] + (f" · {st['colo']}" if st["colo"] else "")
    body = [("ISP", st["isp"]), ("Server", server), ("Ping", ping),
            "# Download", *_speed_rows("Down", st["down"]),
            "# Upload", *_speed_rows("Up", st["up"]),
            "", ("Test", f"{int(time.monotonic() - st['t0'])} san. · Cloudflare")]
    body += [("Qeyd", note) for note in st["notes"]]
    if st["error"]:
        body.append(("Xəta", st["error"][:120]))
    return ff(st["who"], body, logo=True,
              footer=f"⚡ {UI_INTERVAL:g} san.-dən bir · {st['status']} · {datetime.now():%H:%M:%S}")


def _new_speed_state(who: str) -> dict:
    def speed():
        return {"phase": "wait", "cur": 0.0, "final": 0.0, "pct": 0, "hist": [], "peak": 0.0}
    return {"t0": time.monotonic(), "stage": "ping", "status": "ping ölçülür", "who": who,
            "isp": "…", "server": "…", "colo": "", "ping": None, "jitter": 0.0,
            "down": speed(), "up": speed(), "error": "", "notes": []}


class _Meter:
    """Bütün axınların baytlarını sayır, hər SAMPLE_INTERVAL-da sürəti qrafikə əlavə edir."""

    def __init__(self, s: dict, max_reqs: int = 0, req_bytes: int = 0):
        self.s = s
        self.t0 = self.last = self.end = time.perf_counter()
        self.bytes = self.last_b = 0
        self.max_reqs = max_reqs
        self.req_bytes = req_bytes
        self.done_reqs = 0

    def add(self, n: int):
        self.bytes += n

    def finish_req(self):
        self.done_reqs += 1

    def tick(self):
        now = time.perf_counter()
        dur = now - self.last
        if dur >= SAMPLE_INTERVAL:
            bps = (self.bytes - self.last_b) * 8 / dur
            mbps = bps / 1_000_000
            self.s["cur"] = mbps
            self.s["peak"] = max(self.s["peak"], mbps)
            self.s["hist"].append(mbps)
            self.last, self.last_b = now, self.bytes
        total_d = max(0.001, now - self.t0)
        time_pct = min(100, int(total_d / TEST_DURATION * 100))
        if self.max_reqs and self.req_bytes:
            req_pct = min(100, int(self.bytes / (self.max_reqs * self.req_bytes) * 100))
            self.s["pct"] = max(time_pct, req_pct)
        else:
            self.s["pct"] = time_pct

    def finish(self):
        self.end = time.perf_counter()
        dur = max(0.001, self.end - self.t0)
        self.s["final"] = (self.bytes * 8 / dur) / 1_000_000
        self.s["pct"] = 100
        self.s["phase"] = "done"


# ─────────────────────────────────────────────────────────────
# 3. Shell Köməkçiləri
# ─────────────────────────────────────────────────────────────
SH_TIMEOUT = int(os.getenv("SH_TIMEOUT", "300"))
SH_KILL_GRACE = 3
OUT_LIMIT = 3500
SH_UI_INTERVAL = 1.2
TAIL_KEEP = 60

_DANGER = [
    (re.compile(r":\s*\(\s*\)\s*\{.*\}\s*;?\s*:"), "fork bomb"),
    (re.compile(r"\brm\s+(?:-\w*\s+)*(?:-[rfRF]\w*|--recursive|--force).*\s+(?:/|/\*|~|\$HOME)\s*$"), "kökü silmək"),
    (re.compile(r"\bmkfs(\.\w+)?\b|\bdd\b.*\bof=/dev/(sd|nvme|mmcblk|vd)"), "diski formatlamaq / üzərinə yazmaq"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|vd)\w*"), "blok cihazına yazmaq"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt|init\s+0|init\s+6)\b"), "serveri söndürmək / restart"),
    (re.compile(r"\bkill(all)?\s+-9\s+-1\b|\bkill\s+-9\s+1\b"), "bütün prosesləri öldürmək"),
    (re.compile(r"\bchmod\s+-R\s+(?:777|000)\s+/\s*$|\bchown\s+-R\s+\S+\s+/\s*$"), "kökün icazələrini dəyişmək"),
]


def danger_of(cmd: str):
    for rx, why in _DANGER:
        if rx.search(cmd):
            return why
    return None


def _fmt_dur(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec >= 60 else f"{sec} san"


def _trim(text: str) -> str:
    if len(text) <= OUT_LIMIT:
        return text
    lines = text.splitlines()[-TAIL_KEEP:]
    cut = "\n".join(lines)
    if len(cut) > OUT_LIMIT:
        cut = cut[-OUT_LIMIT:]
    return "… (əvvəli kəsildi)\n" + cut


def _is_tty_error(text: str) -> str:
    low = text.lower()
    if "a password is required" in low or "sudo: no tty" in low or "askpass" in low:
        return "\n⚠️ <i>sudo parol istədi — NOPASSWD qur və ya parolsuz komanda işlət</i>"
    if "is there a terminal attached" in low or "/dev/tty" in low:
        return "\n⚠️ <i>interaktiv komanda — cavab gözləyə bilmir (--noconfirm / -y işlət)</i>"
    return ""


# ─────────────────────────────────────────────────────────────
# 4. Bmenu & Install Köməkçiləri
# ─────────────────────────────────────────────────────────────
def compact(text, kb):
    """📋 botun /ui ayarlarına görə mətn → kod bloku, düymələr → yığcam."""
    try:
        from bot.plugins import compact_ui_plugin as cui
    except Exception:
        return text, kb
    try:
        if cui._on("ui_box"):
            text = cui.boxify(text, True) or text
        if cui._on("ui_kb"):
            kb = cui.compact_kb(kb)
    except Exception as e:
        logger.debug(f".bmenu yığcam görünüş: {e}")
    return text, kb


def safe_name(filename: str) -> str:
    return safe_plugin_name(filename, prefix_numeric=True)


# ─────────────────────────────────────────────────────────────
# Əsas Qeydiyyat (register)
# ─────────────────────────────────────────────────────────────
def register(ub):
    # ═════════════════════════════════════════════════════════
    # A) .status Komandası və 📊 Status Paneli
    # ═════════════════════════════════════════════════════════
    ub.add_help("p_status", "📊 Status", "userbot vəziyyəti",
                "Hesab, ping, botun işləmə müddəti, aktiv işlər, Telegram mənbələri.", "panel")

    async def build_status() -> str:
        client = ub.client
        if not client or not client.is_connected():
            return ff(ub.title("status"), [("Userbot", "○ qoşulmayıb")], logo=True)
        t0 = time.monotonic()
        me = await client.get_me()
        ping = (time.monotonic() - t0) * 1000
        st = await static_info()
        filler = getattr(ub.context, "depo_filler", None)
        try:
            n_src = len(filler.tg_source_ids()) if filler else 0
        except Exception:
            n_src = 0
        scan = getattr(ub.context, "depo_scan_state", {}) or {}
        body = [
            (ub.tr("Hesab"), plain_name(me)),
            ("ID", me.id),
            ("Ping", f"{ping:.0f} ms"),
            (ub.tr("Uptime"), fmt_span(time.time() - st["bot_start"])),
            f"# {ub.tr('İşlər')}",
            (ub.tr("Aktiv"), f"{ub.active_tasks()} {ub.tr('fon işi')}"),
            (ub.tr("Kart"), f"{len(ub.cards)} {ub.tr('açıq')}"),
            (ub.tr("Mənbə"), f"{n_src} Telegram" + (f" · {ub.tr('skan gedir')}" if scan.get("running") else "")),
            ("Plugin", f"{len(ub.plugin_files)} {ub.tr('fayl')} · {len(ub.commands)} {ub.tr('komanda')}"),
        ]
        cp = ub.cpu_pool
        if cp:
            try:
                m = cp.mode()
                icon = "🧠" if m == "process" else "🧵"
                procs = f"{cp.user_procs()} işçi" if m == "process" else f"{cp.threads()} thread"
                body.append(f"# {ub.tr('Performans')}")
                body.append((ub.tr("CPU Hovuzu"), f"{icon} {cp.CORES} nüvə · {procs}"))
                hl = cp.health()
                if hl:
                    body.append((ub.tr("Gecikmə"), f"⚠️ {hl}"))
                elif hasattr(cp, "WATCHDOG") and cp.WATCHDOG.enabled:
                    snap = cp.WATCHDOG.snapshot()
                    body.append((ub.tr("Watchdog"), f"🟢 normal ({snap.get('lag', 0) * 1000:.0f} ms)"))
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/system.py:287]: %r", _e)
        body.extend([
            f"# {ub.tr('Versiya')}",
            ("Telethon", telethon_version),
            ("Python", st["py"]),
        ])
        return ff(ub.title("status"), body, logo=True, footer=f"{datetime.now():%H:%M:%S}")

    @ub.section("status", "📊 Status", order=10)
    async def render_sec_status(sctx):
        rows = [[sctx.btn(sctx.tr("🔄 Yenilə"), "r")], sctx.nav()]
        return await build_status(), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.on_section("status")
    async def handle_sec_status(sctx):
        await sctx.answer("🔄")
        text, kb = await render_sec_status(sctx)
        await sctx.show(text, kb)

    @ub.command("status", help=("userbot statusu (kart)",
                                "Statusu inline kart kimi göstərir (🔄 ilə yenilənir).\n\n"
                                "<b>İstifadə:</b> <code>.status</code>"))
    async def on_status(event):
        out = await ub.out(event, await build_status())

        async def refresh(o):
            await o.update(await build_status())
        out.refresh = refresh

    @ub.command("storage", pattern=r"^\.(?:storage|disk|size)(?:\s+(.+))?$",
                help=("layihə və fayl yaddaşı",
                      "Layihənin, pluginlərin və digər bot fayllarının yaddaşını göstərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.storage</code> — yaddaş və fayl bölgüsü\n"
                      "• <code>.storage clean</code> — keşi və müvəqqəti faylları təmizlə\n"
                      "• <code>.storage records</code> — səs qeydlərini (data/records) təmizlə"))
    async def on_storage(event):
        from common.diagnostics import project_storage_snapshot
        from common.utilities import cleanup_downloads, cleanup_records, format_bytes

        def build_storage_body():
            s = project_storage_snapshot()
            body = [
                ("Layihə", f"{s['total_size_str']} ({s['total_files']} fayl)"),
                ("Bot RSS", s['proc_rss_str']),
                ("Sistem RAM", f"{s['ram_used_str']} / {s['ram_total_str']} ({s['ram_pct']:.0f}%)"),
                ("Sistem Diski", f"{s['disk_used_str']} / {s['disk_total_str']} ({s['disk_pct']:.0f}%)"),
                ("# Bölmələr", ""),
            ]
            for cat in s["categories"]:
                body.append((cat["label"], f"{cat['size_str']} ({cat['count']} fayl)"))
            body.append(("# Ən Böyük Pluginlər", ""))
            for i, p in enumerate(s["top_plugins"][:5], 1):
                body.append((f"{p['type']} {p['name']}", p['size_str']))
            return ff(ub.title("storage"), body, footer="🧹 Təmizləmək: .storage clean · .storage records")

        arg = (event.pattern_match.group(1) or "").strip().lower()
        if arg in ("clean records", "clean_records", "records", "recs", "vcrec"):
            freed_bytes, freed_files = await cleanup_records()
            await asyncio.to_thread(project_storage_snapshot, force=True)
            msg = f"🎙 Səs qeydləri təmizləndi: {freed_files} fayl ({format_bytes(freed_bytes)} boşaldıldı)" if freed_files else "ℹ️ Silinəcək səs qeydi tapılmadı"
            await safe_edit(event, f"<code>{msg}</code>\n\n" + await asyncio.to_thread(build_storage_body))
            return
        if arg in ("clean", "clear"):
            freed_bytes, freed_files = await cleanup_downloads()
            await asyncio.to_thread(project_storage_snapshot, force=True)
            msg = f"🧹 Keş təmizləndi: {freed_files} fayl ({format_bytes(freed_bytes)} boşaldıldı)" if freed_files else "ℹ️ Keş artıq təmizdir"
            await safe_edit(event, f"<code>{msg}</code>\n\n" + await asyncio.to_thread(build_storage_body))
            return

        # bütün layihə ağacını gəzir — event loop-u dondurmasın (host.py:341 kimi thread-də)
        out = await ub.out(event, await asyncio.to_thread(build_storage_body))

        async def refresh(o):
            await asyncio.to_thread(project_storage_snapshot, force=True)
            await o.update(await asyncio.to_thread(build_storage_body))

        out.refresh = refresh

    # ═════════════════════════════════════════════════════════
    # B.1) .perf / .cpupool Komandası
    # ═════════════════════════════════════════════════════════
    @ub.command("perf", pattern=r"^\.(?:perf|cpu|cpupool)$",
                help=("CPU hovuzu və performans",
                      "Multiproses CPU hovuzu, worker sayı, event loop gecikməsi və donma monitoru.\n\n"
                      "<b>İstifadə:</b> <code>.perf</code>"))
    async def on_perf(event):
        cp = ub.cpu_pool
        if not cp:
            await safe_edit(event, "❌ cpu_pool modulu əlçatan deyil.")
            return

        def build_perf_body():
            m = cp.mode()
            w = cp.WATCHDOG.snapshot() if hasattr(cp, "WATCHDOG") else {}
            mon = getattr(cp, "MON", None)
            turbo = getattr(cp, "turbo_on", lambda: False)()

            body = [
                ("Rejim", cp.MODES.get(m, m)),
                ("CPU", f"{cp.CORES} nüvə" + (f" · {mon.cpu:.0f}%" if mon and getattr(mon, "hist", None) else "")),
                ("Workerlər", f"👤 {cp.user_procs()} user · 📦 {cp.depo_procs()} depo"),
                ("Threadlər", f"{cp.threads()} thread · nice +{cp.nice()}"),
                ("Avto-miqyas", "✅ Açıq" if cp.autoscale_on() else "○ Bağlı"),
            ]
            if turbo:
                body.append(("Turbo", "🔥 AÇIQ (100% CPU)"))
            if w:
                body.append(("# Watchdog & Sağlamlıq", ""))
                status_str = "🔴 DONUB" if w.get("now_frozen") else ("🟡 Donma olub" if w.get("freezes_hour") else "🟢 Normal")
                body.append(("Vəziyyət", status_str))
                body.append(("Gecikmə", f"{w.get('lag', 0) * 1000:.0f} ms (orta {w.get('lag_avg', 0) * 1000:.0f} ms)"))
                if w.get("freezes_hour"):
                    body.append(("Donma (1 saat)", f"{w['freezes_hour']} dəfə"))
                if w.get("last"):
                    ts, dur, where = w["last"]
                    ago = int(time.time() - ts)
                    body.append(("Son donma", f"{dur:.1f}s ({ago}s əvvəl)"))
                    body.append(("Səbəb", where[:80]))
            return ff(ub.title("perf"), body, logo=True, footer="⚙️ .perf · 🔄 yeniləmək üçün yenidən .perf yazın")

        out = await ub.out(event, build_perf_body())

        async def refresh(o):
            await o.update(build_perf_body())

        out.refresh = refresh


    # ═════════════════════════════════════════════════════════
    # C) .fastfetch / .ff Komandası
    # ═════════════════════════════════════════════════════════
    @ub.command("fastfetch", pattern=r"^\.(?:fastfetch|ff)(?:\s+(\d+))?$",
                help=("sistem məlumatı (canlı)",
                      "Server haqqında fastfetch görünüşündə məlumat: OS, CPU, RAM, disk, şəbəkə, bot.\n\n"
                      "<b>İstifadə:</b>\n• <code>.ff</code> — 60 san. canlı\n"
                      f"• <code>.ff 300</code> — 300 san. (maks. {FF_MAX})\n• <code>.ff 0</code> — snapshot\n"
                      "• kartda ⏹ və ya <code>.stop</code> — dayandır"))
    async def on_ff(event):
        arg = event.pattern_match.group(1)
        secs = min(FF_MAX, int(arg)) if arg is not None else FF_DEFAULT
        st = await static_info()
        live = LiveStats()
        await asyncio.sleep(0.5)
        out = Out(ub, event)
        again = [[out.btn("🔁 Canlı", "again")]]

        async def loop(dur):
            end = time.monotonic() + dur
            try:
                while (left := end - time.monotonic()) > 0:
                    await asyncio.sleep(FF_INTERVAL)
                    await out.update(render_fastfetch(st, live.sample(), f"{int(left)} san. qalıb"), rows=[])
            except asyncio.CancelledError:
                await out.update(render_fastfetch(st, live.sample(), "dayandırıldı"), rows=again)
                raise
            await out.update(render_fastfetch(st, live.sample(), "bitdi"), rows=again)

        @out.on("again")
        async def _(o, cb):
            if not o.running():
                o.track("ff", loop(secs or FF_DEFAULT))

        async def refresh(o):
            await o.update(render_fastfetch(st, live.sample(), "snapshot"))
        out.refresh = refresh

        await out.open(render_fastfetch(st, live.sample(), "başlayır" if secs else "snapshot"),
                       [] if secs else again)
        if secs:
            out.track("ff", loop(secs))

    # ═════════════════════════════════════════════════════════
    # D) .speedtest Komandası
    # ═════════════════════════════════════════════════════════
    async def _ping(sess, count: int = PING_COUNT) -> tuple:
        samples = []
        for _ in range(count):
            t0 = time.perf_counter()
            try:
                async with sess.get(f"{CF}/__down?bytes=0", timeout=aiohttp.ClientTimeout(total=4)) as r:
                    await r.read()
                    if r.status == 200:
                        samples.append((time.perf_counter() - t0) * 1000)
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/system.py:464]: %r", _e)
            await asyncio.sleep(0.05)
        if not samples:
            return None, 0.0
        avg = sum(samples) / len(samples)
        var = sum((x - avg) ** 2 for x in samples) / len(samples)
        return avg, math.sqrt(var)

    async def _down_stream(sess, meter: _Meter, stop_ev: asyncio.Event):
        url = DOWN_URL or f"{CF}/__down?bytes={DOWN_REQ_BYTES}"
        headers = {"Accept-Encoding": "identity"}
        while not stop_ev.is_set():
            if not DOWN_URL and meter.done_reqs >= meter.max_reqs:
                break
            try:
                async with sess.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(connect=5, sock_read=TEST_DURATION + 2)) as r:
                    if r.status == 429:
                        meter.s["notes"].append("Cloudflare 429 (rate-limit)")
                        break
                    if r.status != 200:
                        break
                    while not stop_ev.is_set():
                        chunk = await r.content.read(CHUNK)
                        if not chunk:
                            break
                        meter.add(len(chunk))
                    meter.finish_req()
            except asyncio.CancelledError:
                raise
            except Exception:
                if stop_ev.is_set():
                    break
                await asyncio.sleep(0.2)

    async def _up_stream(sess, meter: _Meter, stop_ev: asyncio.Event):
        body = b"0" * UP_REQ_BYTES
        url = f"{CF}/__up"
        while not stop_ev.is_set():
            if meter.done_reqs >= meter.max_reqs:
                break
            try:
                async with sess.post(url, data=body,
                                     timeout=aiohttp.ClientTimeout(connect=5, sock_read=TEST_DURATION + 2)) as r:
                    if r.status == 429:
                        meter.s["notes"].append("Cloudflare 429 (rate-limit)")
                        break
                    meter.add(len(body))
                    meter.finish_req()
            except asyncio.CancelledError:
                raise
            except Exception:
                if stop_ev.is_set():
                    break
                await asyncio.sleep(0.2)

    async def _run_phase(sess, kind: str, meter: _Meter, draw):
        stop_ev = asyncio.Event()
        worker = _down_stream if kind == "down" else _up_stream
        tasks = [asyncio.create_task(worker(sess, meter, stop_ev)) for _ in range(STREAMS)]

        async def ticker():
            while not stop_ev.is_set():
                meter.tick()
                await draw()
                await asyncio.sleep(SAMPLE_INTERVAL)

        tick_t = asyncio.create_task(ticker())
        try:
            await asyncio.sleep(TEST_DURATION)
        finally:
            stop_ev.set()
            tick_t.cancel()
            with suppress(asyncio.CancelledError):
                await tick_t
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            meter.finish()
            await draw()

    async def run_speedtest(out: Out):
        if aiohttp is None:
            await out.update("❌ <code>aiohttp</code> quraşdırılmayıb (<code>pip install aiohttp</code>).")
            return
        st = _new_speed_state(ub.title("speedtest"))
        last_ui = [0.0]

        async def draw(force=False):
            now = time.monotonic()
            if force or now - last_ui[0] >= UI_INTERVAL:
                last_ui[0] = now
                with suppress(Exception):
                    await out.update(render_speedtest(st))

        await draw(force=True)
        timeout = aiohttp.ClientTimeout(total=TEST_DURATION * 3 + 15)
        conn = aiohttp.TCPConnector(limit=STREAMS * 2, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as sess:
            # 1) Ping + meta
            st["status"] = "ping ölçülür"
            await draw(force=True)
            try:
                async with sess.get(f"{CF}/__down?bytes=0") as r:
                    st["server"] = r.headers.get("Server", "Cloudflare")
                    st["colo"] = r.headers.get("cf-ray", "").split("-")[-1]
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/system.py:571]: %r", _e)
            p, j = await _ping(sess)
            st["ping"], st["jitter"] = p, j
            if st["ping"] is None:
                st["notes"].append("Cloudflare ping cavab vermədi")

            # 2) Download
            st["stage"] = "down"
            st["status"] = "download ölçülür"
            st["down"]["phase"] = "run"
            meter_d = _Meter(st["down"], max_reqs=DOWN_MAX_REQS, req_bytes=DOWN_REQ_BYTES)
            await _run_phase(sess, "down", meter_d, draw)

            # 3) Upload
            st["stage"] = "up"
            st["status"] = "upload ölçülür"
            st["up"]["phase"] = "run"
            meter_u = _Meter(st["up"], max_reqs=UP_MAX_REQS, req_bytes=UP_REQ_BYTES)
            await _run_phase(sess, "up", meter_u, draw)

            st["stage"] = "done"
            st["status"] = "bitdi"
            await draw(force=True)

    @ub.command("speedtest", help=("internet sürət testi",
                                   "Cloudflare Speed CDN vasitəsilə serverin internet sürətini ölçür (canlı qrafik).\n\n"
                                   "<b>İstifadə:</b> <code>.speedtest</code>\n"
                                   "⏹ Dayandır: <code>.stop</code> və ya kartdakı düymə."))
    async def on_speedtest(event):
        out = Out(ub, event)
        again = [[out.btn(out.tr("🔁 Yenidən"), "again")]]

        async def launch():
            try:
                await run_speedtest(out)
                await out.buttons(again)
            except asyncio.CancelledError:
                await out.buttons(again)
                raise

        @out.on("again")
        async def _(o, cb):
            if not o.running():
                await cb.answer("⚡ Sürət testi başlayır...")
                o.track("speedtest", launch())

        async def refresh(o):
            if not o.running():
                o.track("speedtest", launch())
        out.refresh = refresh

        await out.open("⚡ <b>Speedtest başlayır...</b>")
        out.track("speedtest", launch())

    # ═════════════════════════════════════════════════════════
    # E) .sh / .bash / .term / .cd / .pwd / .env Komandaları
    # ═════════════════════════════════════════════════════════
    sess_sh = ub.state.setdefault("shell", {"cwd": os.getcwd(), "env": {}})

    def card_sh(cmd: str, out_txt: str, status: str, dur: float, extra: str = "") -> str:
        rel = sess_sh["cwd"]
        home = os.path.expanduser("~")
        if rel.startswith(home):
            rel = "~" + rel[len(home):]
        head = (f"🖥 <b>Shell</b> · <code>{escape(rel)}</code> · {status} · {_fmt_dur(dur)}\n"
                f"<b>$</b> <code>{escape(cmd[:300])}</code>")
        body = _trim(out_txt).rstrip()
        from common.menu import wrap_ui_text
        block = f"\n{wrap_ui_text(escape(body))}" if body else "\n<i>(çıxış yoxdur)</i>"
        return head + block + (extra or "")

    async def run_sh(out: Out, cmd: str, use_bash: bool):
        start = time.monotonic()
        buf = bytearray()
        last_ui = [0.0]
        env = {**os.environ, **sess_sh["env"]}
        env.setdefault("TERM", "dumb")

        async def draw(status: str, extra: str = ""):
            await out.update(card_sh(cmd, buf.decode("utf-8", "replace"), status, time.monotonic() - start, extra))

        try:
            if use_bash or re.search(r"[|&;<>*?$`(){}\[\]~]", cmd):
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c", cmd, cwd=sess_sh["cwd"], env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True)
            else:
                proc = await asyncio.create_subprocess_exec(
                    *shlex.split(cmd), cwd=sess_sh["cwd"], env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True)
        except FileNotFoundError:
            await draw("✗ tapılmadı", "\n<i>Belə komanda yoxdur (PATH-ə bax).</i>")
            return
        except Exception as e:
            await draw("✗ xəta", f"\n<code>{escape(str(e)[:200])}</code>")
            return

        await draw("● işləyir")

        async def pump():
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                del buf[:-120_000]
                now = time.monotonic()
                if now - last_ui[0] >= SH_UI_INTERVAL:
                    last_ui[0] = now
                    with suppress(Exception):
                        await draw("● işləyir")

        try:
            await asyncio.wait_for(pump(), timeout=SH_TIMEOUT)
            rc = await proc.wait()
        except asyncio.TimeoutError:
            await _terminate_sh(proc)
            await draw(f"⏱ vaxt bitdi ({SH_TIMEOUT} san)", "\n<i>SH_TIMEOUT ilə artırıla bilər.</i>")
            return
        except asyncio.CancelledError:
            await _terminate_sh(proc)
            with suppress(Exception):
                await draw("⏹ dayandırıldı")
            raise
        text = buf.decode("utf-8", "replace")
        status = "✓ bitdi" if rc == 0 else f"✗ kod {rc}"
        await draw(status, _is_tty_error(text))

    async def _terminate_sh(proc):
        if proc.returncode is not None:
            return
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), SH_KILL_GRACE)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    async def launch_sh(event, cmd: str, use_bash: bool):
        cmd = cmd.strip()
        if not cmd:
            await safe_edit(event, "ℹ️ Komanda yaz: <code>.sh pacman -Qi python</code> "
                                   "və ya öz mesajına reply edib <code>.sh</code>.")
            return
        out = Out(ub, event)
        why = danger_of(cmd)
        if why:
            confirmed = {"v": False}

            @out.on("go")
            async def _(o, cb):
                if confirmed["v"]:
                    return
                confirmed["v"] = True
                await cb.answer("▶️ İcra olunur...")
                o.track("sh", run_sh(o, cmd, use_bash))

            @out.on("no")
            async def _(o, cb):
                await cb.answer("Ləğv edildi")
                await o.close()

            await out.open(
                f"⚠️ <b>Təhlükəli komanda:</b> {escape(why)}\n<b>$</b> <code>{escape(cmd[:300])}</code>\n\n"
                "Əminsən?",
                [[out.btn(out.tr("✅ Bəli, işlət"), "go"), out.btn(out.tr("✖️ Ləğv et"), "no")]])
            return

        async def refresh(o):
            if not o.running():
                o.track("sh", run_sh(o, cmd, use_bash))
        out.refresh = refresh
        await out.open(card_sh(cmd, "", "● başlayır", 0))
        out.track("sh", run_sh(out, cmd, use_bash))

    async def read_cmd(event, inline_arg: str) -> str:
        if inline_arg and inline_arg.strip():
            return inline_arg.strip()
        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and not reply.out:                  # başqasının mesajını terminalda icra etmə (təhlükəsizlik)
                return ""
            return (reply.raw_text or reply.text or "") if reply else ""
        return ""

    @ub.command("sh", pattern=r"^\.sh(?:\s+([\s\S]+))?$",
                help=("terminal komandası işlət",
                      "Linux komandasını serverdə icra edir, çıxışı canlı göstərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.sh pacman -Qi python</code>\n"
                      "• <code>.sh sudo pacman -S --noconfirm ffmpeg</code>\n"
                      "• <code>.sh pip install edge-tts</code>\n"
                      "• skriptə <b>reply</b> → <code>.sh</code>\n\n"
                      "⏹ Dayandır: <code>.stop</code>. İşçi qovluq: <code>.cd</code> / <code>.pwd</code>."))
    async def on_sh(event):
        await launch_sh(event, await read_cmd(event, event.pattern_match.group(1)), use_bash=False)

    @ub.command("bash", pattern=r"^\.(?:bash|term)(?:\s+([\s\S]+))?$",
                help=("bash -c ilə işlət",
                      "Komandanı həmişə <code>bash -c</code> ilə icra edir — pipe, <code>&&</code>, "
                      "<code>*</code> işləyir.\n\n<b>İstifadə:</b> <code>.bash ls -la | grep .py</code>"))
    async def on_bash(event):
        await launch_sh(event, await read_cmd(event, event.pattern_match.group(1)), use_bash=True)

    @ub.command("cd", pattern=r"^\.cd(?:\s+([\s\S]+))?$",
                help=("işçi qovluğu dəyiş",
                      "Növbəti <code>.sh</code> komandaları bu qovluqda işləyir.\n\n"
                      "<b>İstifadə:</b> <code>.cd /home/user</code> · <code>.cd ..</code>"))
    async def on_cd(event):
        arg = (event.pattern_match.group(1) or "").strip() or "~"
        target = os.path.expanduser(os.path.expandvars(arg))
        if not os.path.isabs(target):
            target = os.path.join(sess_sh["cwd"], target)
        target = os.path.normpath(target)
        if not os.path.isdir(target):
            await safe_edit(event, f"❌ Belə qovluq yoxdur: <code>{escape(target)}</code>")
            return
        sess_sh["cwd"] = target
        await safe_edit(event, f"📂 İşçi qovluq: <code>{escape(target)}</code>")

    @ub.command("pwd", help=("işçi qovluq", "Cari işçi qovluğu göstərir."))
    async def on_pwd(event):
        await safe_edit(event, f"📂 <code>{escape(sess_sh['cwd'])}</code>")

    @ub.command("env", pattern=r"^\.env(?:\s+([\s\S]+))?$",
                help=("mühit dəyişəni",
                      "Növbəti komandalar üçün dəyişən təyin edir / siyahısını göstərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.env</code> — siyahı\n• <code>.env TOKEN=abc</code>\n• <code>.env -TOKEN</code> — sil"))
    async def on_env(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            if not sess_sh["env"]:
                await safe_edit(event, "ℹ️ Əlavə mühit dəyişəni yoxdur.")
                return
            lines = "\n".join(f"<code>{escape(k)}={escape(v)}</code>" for k, v in sess_sh["env"].items())
            await safe_edit(event, f"🌱 <b>Mühit dəyişənləri:</b>\n{lines}")
            return
        if arg.startswith("-"):
            key = arg[1:].strip()
            sess_sh["env"].pop(key, None)
            await safe_edit(event, f"🗑 <code>{escape(key)}</code> silindi.")
            return
        if "=" not in arg:
            await safe_edit(event, "❌ Format: <code>.env KEY=dəyər</code>")
            return
        key, _, val = arg.partition("=")
        sess_sh["env"][key.strip()] = val.strip()
        await safe_edit(event, f"🌱 <code>{escape(key.strip())}</code> təyin edildi.")

    # ═════════════════════════════════════════════════════════
    # F) .stop Komandası
    # ═════════════════════════════════════════════════════════
    @ub.command("stop", help=("işləyəni dayandır",
                              "Bu çatdakı <code>.fastfetch</code>, <code>.speedtest</code>, <code>.purge</code>, "
                              "<code>.clear</code> işlərini dayandırır.\n\n<b>İstifadə:</b> <code>.stop</code>"))
    async def on_stop(event):
        n = ub.stop_tasks(event.chat_id)
        body = [("Nəticə", f"● {n} iş dayandırıldı" if n else "○ işləyən komanda yoxdur")]
        out = await ub.out(event, ff(ub.title("stop"), body))
        await out.close(3)

    # ═════════════════════════════════════════════════════════
    # G) .bmenu / .botmenu Komandası
    # ═════════════════════════════════════════════════════════
    @ub.command("bmenu", pattern=r"^\.(?:bmenu|botmenu)$",
                help=("botun /menu paneli",
                      "Botun admin menyusunu bu çatda inline kart kimi açır.\n\n"
                      "<b>İstifadə:</b> <code>.bmenu</code> və ya <code>.botmenu</code>"))
    async def on_bmenu(event):
        view = getattr(ub.context, "menu_main_view", None)
        if view is None:
            from common.menu import main_text, main_kb
            view = lambda: (main_text(ub.context), main_kb(ub.context))
        text, kb = compact(*view())
        out = Out(ub, event)

        async def refresh(o):
            t, k = compact(*view())
            await o.update(t, rows=k.inline_keyboard)
        out.refresh = refresh

        await out.open(text, kb.inline_keyboard)
        if out.mode != "inline":
            try:
                await ub.bot.send_message(ub.creator_id, text, parse_mode="HTML", reply_markup=kb,
                                          disable_web_page_preview=True)
                await out.update(text + "\n\n📬 <i>Bu çatda inline düymə göndərmək olmur — menyu botun "
                                        "şəxsi çatına göndərildi.</i>")
            except Exception as e:
                logger.info(f".bmenu bot çatına göndərilmədi: {e}")

    # ═════════════════════════════════════════════════════════
    # H) .install / .plugins / .reload / .uninstall Komandaları
    # ═════════════════════════════════════════════════════════
    def fmt_info(name, info):
        parts = []
        if info["commands"]:
            parts.append("komanda: " + ", ".join("." + c for c in info["commands"]))
        if info["sections"]:
            parts.append("panel: " + ", ".join(info["sections"]))
        if info["raw"]:
            parts.append(f"{info['raw']} fon handler")
        return f"<b>{escape(name)}</b> — " + (" · ".join(parts) if parts else "boş")

    @ub.command("install", pattern=r"^\.install(?:\s+(\S+))?$",
                help=("plugin yüklə (.py)",
                      "Bir <code>.py</code> sənədinə <b>reply</b> edib <code>.install</code> yaz. "
                      "Fayl yüklənir və dərhal aktiv olur."))
    async def on_install(event):
        plugins_dir = ub.state.get("plugins_dir")
        load_fn = ub.state.get("load_plugin_file")
        if not plugins_dir or not load_fn:
            await safe_edit(event, "❌ Host plugin yükləyicisi hazır deyil.")
            return
        if not event.is_reply:
            await safe_edit(event, "ℹ️ Bir <code>.py</code> faylına <b>reply</b> edib <code>.install</code> yaz.")
            return
        reply = await event.get_reply_message()
        doc = getattr(reply, "document", None) if reply else None
        fname = None
        if doc:
            for a in getattr(doc, "attributes", None) or []:
                if getattr(a, "file_name", None):
                    fname = a.file_name
                    break
        override = event.pattern_match.group(1)
        name = safe_name(override or fname or "")
        if not name:
            await safe_edit(event, "❌ Fayl adı tapılmadı. Ad göstər: <code>.install myplugin</code>")
            return
        path = os.path.join(plugins_dir, f"{name}.py")
        tmp = os.path.join(plugins_dir, f".{name}.install.tmp")   # .py deyil — avtomatik yüklənmir
        await safe_edit(event, f"⏳ <code>{name}.py</code> endirilir...")
        old_code = None                                 # eyniadlı mövcud plugin — xətada geri qaytarılır
        replaced = False
        try:
            downloaded = await event.client.download_media(reply, file=tmp)
            if not downloaded or not os.path.isfile(tmp):
                raise RuntimeError("fayl endirilmədi")
            with open(tmp, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
            from common.plugins import validate_plugin
            val = validate_plugin(code)
            if not val["valid"]:
                await safe_edit(event, f"❌ <b>Sintaksis xətası:</b> <code>{escape(val['error'])}</code>")
                return
            if not val["has_register"]:
                await safe_edit(event, "❌ Faylda <code>def register(ub):</code> funksiyası yoxdur.")
                return
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    old_code = f.read()
            os.replace(tmp, path)
            replaced = True
            loaded_name, info = load_fn(path, attach=True)
            await safe_edit(event, f"✅ <b>{escape(loaded_name)}.py</b> uğurla yükləndi!\n"
                                   f"• Komandalar: <code>{', '.join('.' + c for c in info['commands']) or '—'}</code>\n"
                                   f"• Panel: <code>{', '.join(info['sections']) or '—'}</code>")
        except Exception as e:
            if replaced:
                with suppress(OSError):
                    if old_code is not None:            # köhnə versiyanı bərpa et və yenidən yüklə
                        with open(path, "wb") as f:
                            f.write(old_code)
                    else:
                        os.remove(path)
                if old_code is not None:
                    with suppress(Exception):
                        load_fn(path, attach=True)
            logger.error(f".install xətası ({name}): {e}", exc_info=True)
            await safe_edit(event, f"❌ <b>Yüklənmədi:</b> <code>{escape(str(e))[:200]}</code>"
                                   + ("\n↩️ Köhnə versiya bərpa olundu." if old_code is not None and replaced else ""))
        finally:
            with suppress(OSError):
                os.remove(tmp)

    @ub.command("plugins", pattern=r"^\.plugins$",
                help=("yüklü pluginlər", "Yüklənmiş plugin faylları və onların komandaları."))
    async def on_plugins(event):
        L = [f"🧩 <b>Userbot pluginləri ({len(ub.plugin_files)}):</b>", ""]
        for name in sorted(ub.plugin_files):
            L.append(fmt_info(name, ub.plugin_summary(name)))
        await safe_edit(event, "\n".join(L))

    @ub.command("reload", pattern=r"^\.reload(?:\s+(\S+))?$",
                help=("plugini yenidən yüklə",
                      "Diskdəki faylı təkrar oxuyur (komandaları yeniləyir).\n\n"
                      "<b>İstifadə:</b>\n• <code>.reload system</code>\n• <code>.reload all</code>"))
    async def on_reload(event):
        target = (event.pattern_match.group(1) or "").strip().lower()
        if not target:
            await safe_edit(event, "ℹ️ Hansı plugin? <code>.reload system</code> və ya <code>.reload all</code>")
            return
        load_fn = ub.state.get("load_plugin_file")
        if not load_fn:
            await safe_edit(event, "❌ Host yükləyicisi hazır deyil.")
            return
        if target == "all":
            done, err = [], []
            for name, path in list(ub.plugin_files.items()):
                try:
                    load_fn(path, attach=False)
                    done.append(name)
                except Exception as e:
                    err.append(f"{name}: {e}")
            ub.attach_pending()
            msg = f"🔄 <b>Hamısı yeniləndi:</b> {len(done)} plugin."
            if err:
                msg += "\n⚠️ Xətalar:\n" + "\n".join(f"• <code>{escape(x)}</code>" for x in err[:5])
            await safe_edit(event, msg)
            return
        path = ub.plugin_files.get(target)
        if not path:
            plugins_dir = ub.state.get("plugins_dir", "")
            cand = os.path.join(plugins_dir, f"{target}.py")
            if os.path.isfile(cand):
                path = cand
        if not path or not os.path.isfile(path):
            await safe_edit(event, f"❌ <code>{escape(target)}</code> adlı plugin tapılmadı.")
            return
        try:
            name, info = load_fn(path, attach=True)
            await safe_edit(event, f"✅ <b>{escape(name)}.py</b> yeniləndi!\n{fmt_info(name, info)}")
        except Exception as e:
            await safe_edit(event, f"❌ <code>{escape(target)}</code> yenilənmədi: <code>{escape(str(e))[:200]}</code>")

    @ub.command("uninstall", pattern=r"^\.uninstall\s+(\S+)$",
                help=("plugini sil", "Plugini söndürür və .py faylını silir."))
    async def on_uninstall(event):
        name = event.pattern_match.group(1).strip()
        path = ub.plugin_files.get(name)
        if not path or not os.path.isfile(path):
            await safe_edit(event, f"❌ <code>{escape(name)}</code> tapılmadı.")
            return
        try:
            ub.remove_plugin(name)
            os.remove(path)
            await safe_edit(event, f"🗑 <b>{escape(name)}.py</b> söndürüldü və faylı silindi.")
        except Exception as e:
            await safe_edit(event, f"❌ Silinmədi: <code>{escape(str(e))[:200]}</code>")

    # ═════════════════════════════════════════════════════════
    # I) 📦 Depo Paneli
    # ═════════════════════════════════════════════════════════
    ub.add_help("p_depo", "📦 Depo", "depo doldurucu",
                "Vəziyyət, növbə, statistika; ▶️/⏸, 🔁 növbəni yenidən qur, ⏹ skanı dayandır.", "panel")

    _WAIT_INPUT = {}

    def depo_view(sctx, note=""):
        filler = getattr(ub.context, "depo_filler", None)
        rows = []
        if filler is None:
            L = ["📦 <b>Depo</b>", "", "❌ depo_filler_plugin yüklənməyib."]
        else:
            st = filler.stats
            on = filler.running
            L = ["📦 <b>Depo doldurucu</b>", "",
                 f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>",
                 f"⬆️ Yükləndi: <b>{st.get('done', 0)}</b> · ⏭ depoda idi: {st.get('cached', 0)} · ❌ {st.get('failed', 0)}",
                 f"📋 Növbə: <b>{len(filler.queue)}</b>"]
            if st.get("current"):
                L.append(f"⏳ İndi: <i>{escape(str(st['current'])[:60])}</i>")
            if st.get("note"):
                L.append(f"ℹ️ {escape(str(st['note']))}")
            try:
                L.append(f"🗂 Mənbə: {len(filler.sources())} · ✈️ Telegram: {len(filler.tg_source_ids())}")
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/system.py:1041]: %r", _e)
            fix_count = getattr(ub.context, "fix_artist_count", None)
            try:
                bad = fix_count() if fix_count else 0
            except Exception:
                bad = 0
            if bad:
                L.append(f"🩹 Təmizlik gözləyir: {bad} <i>(botda /fix)</i>")
            scan = getattr(ub.context, "depo_scan_state", {}) or {}
            rows.append([sctx.btn(sctx.tr("⏸ Söndür"), "off") if on else sctx.btn(sctx.tr("▶️ İşə sal"), "on"),
                         sctx.btn(sctx.tr("🔁 Növbəni qur"), "rebuild")])
            rows.append([sctx.btn(sctx.tr(f"⏱ Fasilə: {filler.delay} san."), "pick_d"),
                         sctx.btn(sctx.tr(f"🧵 Worker: {filler.workers}"), "pick_w")])
            if scan.get("running"):
                L.append("🔎 <i>Skan gedir</i>")
                rows.append([sctx.btn(sctx.tr("⏹ Skanı dayandır"), "scanstop")])
            rows.append([sctx.btn(sctx.tr("🔄 Yenilə"), "r")])
        if note:
            L += ["", note]
        rows.append(sctx.nav())
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def section_picker_view(sctx, target: str, note: str = ""):
        filler = getattr(ub.context, "depo_filler", None)
        if target == "d":
            val = filler.delay if filler else 0
            cur_str = f"{val} san."
            title = sctx.tr("⏱ Gözləmə intervalı")
        else:
            val = filler.workers if filler else 1
            cur_str = f"{val} işçi"
            title = sctx.tr("🧵 Worker sayı")

        L = [
            f"<b>{title}</b>",
            "",
            f"🔢 <b>{sctx.tr('Cari dəyər')}:</b> <b>{cur_str}</b>",
            "",
            f"<i>{sctx.tr('Dəyəri dəyişmək üçün aşağıdakı düymələrdən istifadə edin:')}</i>",
            f"• <b>+</b> — {sctx.tr('maksimum')}",
            f"• <b>-</b> — {sctx.tr('minimum')}",
        ]
        if note:
            L += ["", note]

        rows = [
            [
                sctx.btn("-5", "vstep", target, "-5"),
                sctx.btn("-1", "vstep", target, "-1"),
                sctx.btn("+1", "vstep", target, "1"),
                sctx.btn("+5", "vstep", target, "5"),
            ],
            [sctx.btn(sctx.tr("✏️ Öz rəqəmini yaz"), "vinp", target)],
            [sctx.btn(sctx.tr("⬅️ Geri"), "back")],
        ]
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.section("depo", "📦 Depo", order=40)
    async def render_sec_depo(sctx):
        return depo_view(sctx)

    @ub.on_section("depo")
    async def handle_sec_depo(sctx):
        filler = getattr(ub.context, "depo_filler", None)
        a = sctx.action
        note = ""
        if filler is not None and a == "on":
            filler.start()
            note = "🟢 İşə salındı"
        elif filler is not None and a == "off":
            await filler.stop()
            note = "🔴 Söndürüldü"
        elif filler is not None and a == "rebuild":
            filler.queue.clear()
            filler.seen.clear()
            filler.stats["queue_built"] = 0
            filler.stats["last_build"] = None
            note = "🔁 Növbə növbəti addımda yenidən qurulacaq"
        elif a == "pick_d":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.show(*section_picker_view(sctx, "d"))
            return
        elif a == "pick_w":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.show(*section_picker_view(sctx, "w"))
            return
        elif a == "vinp" and len(sctx.args) >= 1:
            target = sctx.args[0]
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT[chat_id] = {"target": target, "sctx": sctx}
            cur_title = sctx.tr("⏱ Gözləmə intervalı") if target == "d" else sctx.tr("🧵 Worker sayı")
            cur_val = f"{filler.delay} san." if target == "d" else f"{filler.workers} işçi"
            max_val = "3600" if target == "d" else "128"
            min_val = "0" if target == "d" else "1"
            L = [
                f"<b>{cur_title}</b>",
                "",
                f"🔢 <b>{sctx.tr('Cari dəyər')}:</b> <b>{cur_val}</b>",
                "",
                f"✍️ <b>{sctx.tr('Dəyəri daxil edin')}:</b>",
                f"• <b>{sctx.tr('Rəqəm')}</b> ({min_val} ilə {max_val} arası)",
                f"• <b>+</b> — {sctx.tr('maksimum')} ({max_val})",
                f"• <b>-</b> — {sctx.tr('minimum')} ({min_val})",
            ]
            rows = [[sctx.btn(sctx.tr("✖️ Ləğv et"), f"pick_{target}")]]
            await sctx.show("\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows))
            return
        elif a == "vstep" and len(sctx.args) >= 2:
            target = sctx.args[0]
            try:
                delta = int(sctx.args[1])
            except ValueError:
                delta = 1
            if filler:
                if target == "d":
                    val = filler.step_delay_delta(delta)
                    note = f"⏱ {val} san."
                else:
                    val = filler.step_workers_delta(delta)
                    note = f"🧵 {val} işçi"
            else:
                note = "❌ Filler yoxdur"
            await sctx.answer(note)
            await sctx.show(*section_picker_view(sctx, target, note))
            return
        elif a == "back":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.show(*depo_view(sctx))
            return
        elif a == "scanstop":
            scan = getattr(ub.context, "depo_scan_state", None)
            if scan and scan.get("running"):
                scan["stop"] = True
                note = "⏹ Skan dayandırılır"
        await sctx.answer(note or None)
        text, kb = depo_view(sctx, f"<i>{note}</i>" if note else "")
        await sctx.show(text, kb)

    from telethon import events
    @ub.raw(events.NewMessage(outgoing=True))
    async def _on_depo_val(event):
        chat_id = event.chat_id
        st = _WAIT_INPUT.pop(chat_id, None)
        if not st:
            return
        target = st["target"]
        sctx = st["sctx"]
        raw = (event.raw_text or "").strip().lower()
        filler = getattr(ub.context, "depo_filler", None)
        if not filler:
            return
        with suppress(Exception):
            await event.delete()

        note = ""
        if target == "d":
            if raw in ("-", "min", "off", "0"):
                v = filler.set_delay(0)
                note = "✅ Gözləmə intervalı: 0 san. (minimum)"
            elif raw in ("+", "max", "sonsuz"):
                v = filler.set_delay(3600)
                note = "✅ Gözləmə intervalı: 3600 san. (maksimum)"
            elif raw.isdigit():
                v = filler.set_delay(int(raw))
                note = f"✅ Gözləmə intervalı: {v} san."
            else:
                note = "⚠️ Düzgün rəqəm deyil"
        else:
            if raw in ("-", "min"):
                v = filler.set_workers(1)
                note = "✅ Worker sayı: 1 işçi (minimum)"
            elif raw in ("+", "max"):
                v = filler.set_workers(128)
                note = "✅ Worker sayı: 128 işçi (maksimum)"
            elif raw.isdigit():
                v = filler.set_workers(int(raw))
                note = f"✅ Worker sayı: {v} işçi"
            else:
                note = "⚠️ Düzgün rəqəm deyil"

        await sctx.show(*section_picker_view(sctx, target, note))

    @ub.command("depo", pattern=r"^\.depo(?:\s+(on|off|status|start|stop))?$",
                help=("depo idarəsi",
                      "Depo doldurucunu idarə edir: <code>.depo</code>, <code>.depo on</code>, <code>.depo off</code>."))
    async def on_depo_cmd(event):
        arg = (event.pattern_match.group(1) or "").strip().lower()
        filler = getattr(ub.context, "depo_filler", None)
        if filler is None:
            await safe_edit(event, "📦 <b>Depo:</b> doldurucu aktiv deyil.")
            return
        if arg in ("on", "start"):
            filler.start()
            await safe_edit(event, "🟢 <b>Depo doldurucu işə salındı.</b>")
        elif arg in ("off", "stop"):
            await filler.stop()
            await safe_edit(event, "🔴 <b>Depo doldurucu dayandırıldı.</b>")
        else:
            state_str = "🟢 işləyir" if filler.running else "🔴 dayanıb"
            await safe_edit(event, f"📦 <b>Depo Statusu:</b> {state_str} · 📋 <b>Növbə:</b> {len(filler.queue)}")

