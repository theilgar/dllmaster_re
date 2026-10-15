"""
📈 /htop — serverin canlı vəziyyəti (yalnız creator).

htop kimi: hər nüvənin CPU yükü, RAM / Swap, load average, uptime və ən çox resurs
istifadə edən prosesler. Mesaj hər 3 saniyədən bir yenilənir.

  ⏸ Fasilə · ▶️ Davam · 🔀 CPU / RAM-a görə sırala · ⏹ Dayandır
Telegram-ı yükləməmək üçün 3 dəqiqədən sonra avtomatik dayanır (▶️ ilə davam etdirmək olar).

psutil quraşdırılıbsa onu, yoxdursa birbaşa /proc-u oxuyur (Linux).
/menu-dan da açılır: 📈 htop düyməsi.
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from bot.core import panel as ui
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.diagnostics import ProcSampler
from common.utilities import format_bytes, format_uptime, get_tz, progress_bar

logger = logging.getLogger(__name__)

INTERVAL = 3            # yenilənmə (san.)
MAX_RUNTIME = 180       # avtomatik dayanma (san.)
TOP_N = 10
BAR = 14


def bar(pct: float, width: int = BAR) -> str:
    return progress_bar(pct, width=width)


def human(n) -> str:                       # monoşrift sütunlar — boşluqsuz: '1.5MB'
    return format_bytes(n, space=False)
fmt_uptime = format_uptime




class PsutilSampler:
    def __init__(self, psutil):
        self.ps = psutil
        self.ps.cpu_percent(percpu=True)
        for p in self.ps.process_iter():
            try:
                p.cpu_percent(None)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/htop_plugin.py:157]: %r", _e)

    def sample(self) -> dict:
        ps = self.ps
        cores = ps.cpu_percent(percpu=True)
        vm, sw = ps.virtual_memory(), ps.swap_memory()
        procs = []
        for p in ps.process_iter(["pid", "name", "username", "memory_info", "cmdline"]):
            try:
                procs.append({"pid": p.info["pid"], "cpu": p.cpu_percent(None),
                              "rss": p.info["memory_info"].rss if p.info["memory_info"] else 0,
                              "name": p.info["name"] or "", "user": p.info["username"] or "",
                              "cmd": " ".join(p.info["cmdline"] or []) or (p.info["name"] or "")})
            except Exception:
                continue
        load = [f"{x:.2f}" for x in os.getloadavg()] if hasattr(os, "getloadavg") else ["-"] * 3
        return {"cores": cores, "total": sum(cores) / max(len(cores), 1), "procs": procs,
                "mem_total": vm.total, "mem_used": vm.total - vm.available,
                "swap_total": sw.total, "swap_used": sw.used, "load": load, "tasks": f"{len(procs)}",
                "uptime": time.time() - ps.boot_time()}


def make_sampler():
    try:
        import psutil
        return PsutilSampler(psutil)
    except ImportError:
        return ProcSampler()


def render(s: dict, sort: str, paused: bool, left: int) -> str:
    lines = []
    cores = s["cores"]
    for i, pct in enumerate(cores):
        lines.append(f"{i:>2} [{bar(pct, 10)}] {pct:5.1f}%")
    mem_pct = s["mem_used"] / s["mem_total"] * 100 if s["mem_total"] else 0
    swp_pct = s["swap_used"] / s["swap_total"] * 100 if s["swap_total"] else 0
    lines.append(f"Mem[{bar(mem_pct, 10)}] {human(s['mem_used'])}/{human(s['mem_total'])}")
    lines.append(f"Swp[{bar(swp_pct, 10)}] {human(s['swap_used'])}/{human(s['swap_total'])}")
    lines.append(f"Load: {' '.join(s['load'])}  Tasks: {s['tasks']}")
    lines.append(f"Uptime: {fmt_uptime(s['uptime'])}")
    lines.append("")
    key = (lambda p: p["cpu"]) if sort == "cpu" else (lambda p: p["rss"])
    procs = sorted(s["procs"], key=key, reverse=True)[:TOP_N]
    lines.append(f"{'PID':>7} {'USER':<7} {'CPU%':>5} {'MEM':>6} CMD")
    for p in procs:
        cmd = p["cmd"].split("/")[-1] if p["cmd"].startswith("/") else p["cmd"]
        lines.append(f"{p['pid']:>7} {p['user'][:7]:<7} {p['cpu']:5.1f} {human(p['rss']):>6} {cmd[:22]}")

    now = datetime.now(get_tz()).strftime("%H:%M:%S")
    state = "⏸ fasilə" if paused else (f"🔄 hər {INTERVAL} san." + (f" · {left} san. qalıb" if left else ""))
    head = f"📈 <b>htop</b> · CPU <b>{s['total']:.0f}%</b> · RAM <b>{mem_pct:.0f}%</b>\n<i>{now} · {state}</i>"
    from common.menu import wrap_ui_text
    return f"{head}\n{wrap_ui_text(escape(chr(10).join(lines)))}"[:4000]


def setup(context):
    dp = context.dp
    bot = context.bot
    sessions = {}          # (chat_id, msg_id) -> {"task", "paused", "sort", "stop", "until"}

    is_creator = context.is_creator


    def keyboard(sess, running=True):
        if not running:
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Yenidən başlat", callback_data="htop:start")],
                ui.nav("menu:srv", "Server", close_data="htop:close"),
            ])
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Davam" if sess["paused"] else "⏸ Fasilə", callback_data="htop:pause"),
             InlineKeyboardButton(text="🔀 RAM-a görə" if sess["sort"] == "cpu" else "🔀 CPU-ya görə",
                                  callback_data="htop:sort"),
             InlineKeyboardButton(text="⏹ Dayandır", callback_data="htop:stop")],
            ui.nav("menu:srv", "Server", close_data="htop:close"),
        ])

    async def run(key, sess):
        chat_id, msg_id = key
        sampler = make_sampler()
        await asyncio.to_thread(sampler.sample)       # ilk ölçü (CPU% fərq üçün)
        await asyncio.sleep(1)
        last_text = None
        try:
            while not sess["stop"]:
                left = int(sess["until"] - time.time())
                if left <= 0:
                    break
                if not sess["paused"]:
                    sess.pop("paused_text", None)
                    data = await asyncio.to_thread(sampler.sample)
                    text = render(data, sess["sort"], False, left)
                    sess["last"] = data
                elif sess.get("last"):
                    # fasilədə mesaj yalnız bir dəfə dəyişir (saat da donur)
                    if not sess.get("paused_text") or sess.get("paused_sort") != sess["sort"]:
                        sess["paused_text"] = render(sess["last"], sess["sort"], True, 0)
                        sess["paused_sort"] = sess["sort"]
                    text = sess["paused_text"]
                else:
                    text = last_text
                if text and text != last_text:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                    parse_mode="HTML", reply_markup=keyboard(sess))
                        last_text = text
                    except Exception as e:
                        err = str(e).lower()
                        wait = getattr(e, "retry_after", None)
                        if wait:
                            await asyncio.sleep(wait)
                        elif "not modified" in err:
                            pass
                        elif "not found" in err or "can't be edited" in err:
                            break                       # mesaj silinib
                        else:
                            logger.warning(f"htop yenilənmədi: {e}")
                await asyncio.sleep(INTERVAL)
        finally:
            sessions.pop(key, None)
            if not sess.get("closed"):
                text = (render(sess["last"], sess["sort"], True, 0).replace("⏸ fasilə", "⏹ dayandı")
                        if sess.get("last") else "📈 <b>htop</b> · ⏹ dayandı")
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                parse_mode="HTML", reply_markup=keyboard(sess, running=False))
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/htop_plugin.py:286]: %r", _e)

    def start(key):
        old = sessions.get(key)
        if old:
            old["stop"] = True
        # eyni çatdakı başqa htop-ları dayandır (bir anda bir dənə)
        for k, s in list(sessions.items()):
            if k[0] == key[0]:
                s["stop"] = True
        sess = {"paused": False, "sort": "cpu", "stop": False, "until": time.time() + MAX_RUNTIME}
        sessions[key] = sess
        sess["task"] = asyncio.create_task(run(key, sess))
        return sess

    @dp.message(Command("htop"))
    async def htop_cmd(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        msg = await message.answer("📈 <i>htop başlayır...</i>", parse_mode="HTML")
        start((msg.chat.id, msg.message_id))

    @dp.callback_query(F.data.startswith("htop:"))
    async def htop_callback(cb: types.CallbackQuery):
        action = cb.data.split(":", 1)[1]
        key = (cb.message.chat.id, cb.message.message_id)
        sess = sessions.get(key)

        if action == "start":
            await cb.answer("📈 htop")
            try:
                await cb.message.edit_text("📈 <i>htop başlayır...</i>", parse_mode="HTML")
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/htop_plugin.py:322]: %r", _e)
            start(key)
            return
        if action == "close":
            if sess:
                sess["closed"] = sess["stop"] = True
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/htop_plugin.py:332]: %r", _e)
            return
        if not sess:
            await cb.answer("htop dayanıb — ▶️ ilə yenidən başlat")
            return
        if action == "pause":
            sess["paused"] = not sess["paused"]
            if not sess["paused"]:
                sess["until"] = max(sess["until"], time.time() + 60)
            await cb.answer("⏸ Fasilə" if sess["paused"] else "▶️ Davam")
        elif action == "sort":
            sess["sort"] = "mem" if sess["sort"] == "cpu" else "cpu"
            await cb.answer("RAM-a görə" if sess["sort"] == "mem" else "CPU-ya görə")
        elif action == "stop":
            sess["stop"] = True
            await cb.answer("⏹ Dayandırıldı")
        else:
            await cb.answer()

    # menu_plugin-in "⬅️ Menyu" / "❌ Ləğv et" düymələri basılanda həmin mesajdakı htop dayansın
    class StopOnLeave:
        async def __call__(self, handler, event, data):
            if str(getattr(event, "data", None) or "").startswith("menu:") and event.message:
                s = sessions.get((event.message.chat.id, event.message.message_id))
                if s:
                    s["closed"] = s["stop"] = True
            return await handler(event, data)

    dp.callback_query.outer_middleware(StopOnLeave())
    context.htop_sessions = sessions
    logger.info("✅ htop plugin-i yükləndi (/htop)")


async def teardown(context):
    """plugin_manager söndürəndə / yenidən yükləyəndə işləyən htop-ları dayandırır."""
    for s in list(getattr(context, "htop_sessions", {}).values()):
        s["stop"] = True
