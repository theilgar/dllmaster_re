"""
📜 Bot logları — kanala avtomatik göndərmə (yalnız creator).

/menu → 🖥 Sistem → 📜 Loglar:
  • 📢 Kanal: logların gedəcəyi kanal / qrup (@username, -100... ID və ya oradan yönləndirilmiş mesaj)
  • ⏱ Interval: 15 dəq · 1 saat · 6 saat · 12 saat · 24 saat · söndür
  • 📊 Səviyyə: hamısı və ya yalnız ⚠️ xəbərdarlıq + ❌ xəta
  • 📤 İndi göndər

Hər göndərişdə: .log faylı (son göndərişdən bəri) + qısa xülasə (INFO / WARNING / ERROR sayı, son xətalar).
Bot kanalda "Mesaj göndərmək" icazəsi olan admin olmalıdır.
"""
import asyncio
import contextlib
import logging
import time
from collections import deque
from datetime import datetime
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from bot.core import panel as ui

from common import destinations as dest

from common.utilities import get_tz
from common.logger import redact
from common import logger as clog                 # 🖥 terminal loq səviyyəsi + rəng
from common.utilities import format_ts as fmt_ts
from common.database import Settings

logger = logging.getLogger(__name__)

# menu_plugin-dən SONRA yüklənsin (onun "creator mətn yazır" yoxlamasına qoşulur)
PRIORITY = 70

INTERVALS = [(15, "15 dəq"), (60, "1 saat"), (360, "6 saat"), (720, "12 saat"), (1440, "24 saat")]
MAX_LINES = 30000                     # yaddaşda saxlanan maksimum sətir
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class BufferHandler(logging.Handler):
    """Bütün logları son göndərişə qədər yaddaşda saxlayır."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = deque(maxlen=MAX_LINES)          # (səviyyə, sətir)
        self.since = time.time()
        self.dropped = 0
        self.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        try:
            if len(self.lines) == self.lines.maxlen:
                self.dropped += 1
            line = redact(self.format(record))          # 🔒 token / sirlər kanala getməsin
            self.lines.append((record.levelno, line))
        except Exception:
            pass

    def take(self, min_level: int):
        """Buferi boşaldır → (sətirlər, sayğaclar, başlanğıc vaxtı)."""
        items = list(self.lines)
        self.lines.clear()
        since, self.since = self.since, time.time()
        dropped, self.dropped = self.dropped, 0
        counts = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        errors = []
        out = []
        for lvl, line in items:
            if lvl >= logging.ERROR:
                counts["ERROR"] += 1
                errors.append(line)
            elif lvl >= logging.WARNING:
                counts["WARNING"] += 1
            else:
                counts["INFO"] += 1
            if lvl >= min_level:
                out.append(line)
        return out, counts, errors, since, dropped


def setup(context):
    dp = context.dp
    bot = context.bot

    # Eyni handler iki dəfə qoşulmasın (plugin_manager ilə yenidən yükləmədə)
    root = logging.getLogger()
    old = getattr(context, "logs_buffer_handler", None)
    if old:
        root.removeHandler(old)
    handler = BufferHandler()
    root.addHandler(handler)
    context.logs_buffer_handler = handler

    state = {"await_chat": False, "panel": None, "task": None, "stop": False}

    is_creator = context.is_creator

    # ── ayarlar ──
    _cfg = Settings("logs:")
    get, put = _cfg.get, _cfg.put

    def interval() -> int:
        v = get("interval")
        return int(v) if v and str(v).isdigit() else 0

    def min_level() -> int:
        return logging.WARNING if get("level") == "warn" else logging.INFO

    # music_plugin creator-un yazdığı kanal adını mahnı axtarışı saymasın
    from bot.core.input_wait import chain_menu_text
    chain_menu_text(context, lambda uid: (state["await_chat"] or state.get("await_iv")) and is_creator(uid))

    # ── göndərmə ──
    async def send_logs(manual: bool = False) -> str:
        kw = dest.kwargs("logs")                     # kanal / qrup / community mövzusu (📍 Hədəflər)
        if not kw:
            return "❌ Əvvəlcə hədəf təyin et (📍 Hədəflər və ya qrup mövzusunda /setlog)"
        lines, counts, errors, since, dropped = handler.take(min_level())
        now = time.time()
        put("last_sent", str(int(now)))
        if not lines and not manual:
            return "boş"
        caption = (
            f"📜 <b>Bot logları</b>\n🕒 {fmt_ts(since)} → {fmt_ts(now)}\n\n"
            f"ℹ️ INFO: <b>{counts['INFO']}</b> · ⚠️ WARNING: <b>{counts['WARNING']}</b> · "
            f"❌ ERROR: <b>{counts['ERROR']}</b>"
            + (f"\n<i>⏭ yaddaş limitinə görə {dropped} köhnə sətir atıldı</i>" if dropped else "")
        )
        if errors:
            last = "\n".join(escape(e[-150:]) for e in errors[-3:])
            caption += f"\n\n<b>Son xətalar:</b>\n<code>{last}</code>"
        caption = caption[:1020]
        try:
            if lines:
                stamp = datetime.fromtimestamp(now, get_tz()).strftime("%Y%m%d_%H%M")
                data = ("\n".join(lines) + "\n").encode("utf-8", errors="replace")
                await bot.send_document(document=BufferedInputFile(data, filename=f"bot_logs_{stamp}.log"),
                                        caption=caption, parse_mode="HTML", disable_notification=True, **kw)
            else:
                await bot.send_message(text=caption + "\n\n<i>Bu müddətdə yeni log yoxdur.</i>", parse_mode="HTML",
                                       disable_notification=True, **kw)
            return f"✅ Göndərildi ({len(lines)} sətir)"
        except Exception as e:
            # alınmadısa sətirləri geri qaytar ki itməsin
            for line in lines:
                handler.lines.appendleft((logging.INFO, line))
            return f"❌ Göndərilmədi: {str(e)[:200]}"

    async def scheduler():
        await asyncio.sleep(20)
        while not state["stop"]:
            try:
                iv = interval()
                last = int(get("last_sent") or 0)
                if iv and dest.get("logs") and time.time() - last >= iv * 60:
                    result = await send_logs()
                    if result.startswith("❌"):
                        print(f"[logs_plugin] {result}")      # loga yazsaq, yenidən buferə düşər
            except Exception as e:
                print(f"[logs_plugin] planlayıcı xətası: {e}")
            await asyncio.sleep(30)

    state["task"] = asyncio.create_task(scheduler())
    context.logs_state = state

    # ── panel ──
    def panel(note: str = ""):
        chat_title = dest.describe("logs") if dest.get("logs") else None
        iv = interval()
        iv_label = next((l for m, l in INTERVALS if m == iv), f"{iv} dəq") if iv else "❌ söndürülüb"
        last = int(get("last_sent") or 0)
        nxt = ""
        if iv and dest.get("logs"):
            nxt = f" · növbəti: {fmt_ts(max(last + iv * 60, time.time()))}"
        lvl_warn = get("level") == "warn"
        text = (
            "📜 <b>Bot logları</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"📍 Hədəf: <b>{escape(str(chat_title)) if chat_title else '— təyin edilməyib'}</b>\n"
            "<i>Qrup / community mövzusu üçün həmin mövzuda /setlog yaz</i>\n"
            f"⏱ Interval: <b>{iv_label}</b>{nxt}\n"
            f"📊 Səviyyə: <b>{'⚠️ xəbərdarlıq + ❌ xəta' if lvl_warn else 'hamısı (INFO+)'}</b>\n"
            f"📦 Buferdə: <b>{len(handler.lines)}</b> sətir · son göndəriş: {fmt_ts(last)}\n"
            f"🖥 Terminal: <b>{console_label()}</b>\n"
            "━━━━━━━━━━━━━━━━━━"
            + (f"\n\n{note}" if note else "")
        )
        rows = [
            [InlineKeyboardButton(text="📢 Kanalı təyin et", callback_data="lg:setchat"),
             InlineKeyboardButton(text="📤 İndi göndər", callback_data="lg:send")],
            [InlineKeyboardButton(text=f"⏱ İnterval: {iv_label}", callback_data="lg:pick:iv"),
             InlineKeyboardButton(text=f"📊 Səviyyə: {'⚠️+❌' if lvl_warn else 'Hamısı'}", callback_data="lg:level")],
            [InlineKeyboardButton(text=f"🖥 Terminal: {console_label()}", callback_data="lg:con")],
            ui.nav("menu:arch", "Arxiv", close_data="lg:close", refresh_data="lg:panel"),
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    # ── 🖥 terminal (konsol): hansı loqlar və rəng ──
    def console_label() -> str:
        cfg = clog.console_settings()
        if cfg["mode"] == "all":
            label = "🌊 Hamısı"
        elif not cfg["levels"]:
            label = "🔇 heç nə"
        else:
            label = " ".join(clog.CONSOLE_LEVELS[lv][0] for lv in cfg["levels"])
        return label + ("" if cfg["color"] != "off" else " · ⚪ rəngsiz")

    def console_view(note: str = ""):
        cfg = clog.console_settings()
        all_on = cfg["mode"] == "all"
        lines = ["🖥 <b>Terminal loqları</b>\n━━━━━━━━━━━━━━━━━━",
                 "<code>screen -r dlmaster</code> terminalında hansı loqlar görünsün:", ""]
        if all_on:
            lines += ["🌊 <b>Hamısı</b> — hər şey görünür, kitabxanaların (aiogram, telethon, pytgcalls) "
                      "DEBUG-u da. Axın çox olur.",
                      "<i>Səviyyələri ayrıca seçmək üçün ✋ Əl ilə seç.</i>"]
        else:
            lines.append("✋ <b>Əl ilə seçim</b> — hər səviyyəni ayrıca aç / söndür:")
            for lv, (icon, name, desc) in clog.CONSOLE_LEVELS.items():
                lines.append(f"{'✅' if lv in cfg['levels'] else '▫️'} {icon} <b>{name}</b> — {desc}")
            if not cfg["levels"]:
                lines.append("\n⚠️ <b>Heç bir səviyyə seçilməyib — terminal susur.</b>")
        lines += ["",
                  "🎨 <b>Rənglər:</b> " + {"on": "🟢 aktiv", "off": "🔴 deaktiv",
                                           "auto": "🤖 avto (yalnız birbaşa terminalda)"}[cfg["color"]],
                  "   🔵 DEBUG · 🟢 INFO · 🟡 WARNING · 🔴 ERROR · 🟥 CRITICAL (qırmızı fonda)",
                  "",
                  "<i>Dəyişiklik dərhal tətbiq olunur və yadda qalır. Fayl loqu (bot_logs/), "
                  "📜 Telegram loqları və 🚨 xəbərdarlıqlar bundan asılı deyil.</i>"]
        if note:
            lines.append(f"\n{note}")
        B = InlineKeyboardButton
        if all_on:                                      # 🌊 seçilib — səviyyə düymələri gizlənir
            rows = [[ui.btn("📌 Seçim: 🌊 Hamısı  ›  ✋ Əl ilə seç", "lg:conset:manual")]]
        else:
            lv_btn = lambda lv: ui.check(  # noqa: E731
                f"{clog.CONSOLE_LEVELS[lv][0]} {clog.CONSOLE_LEVELS[lv][1]}", lv in cfg["levels"], f"lg:contg:{lv}")
            rows = [[ui.btn("📌 Seçim: ✋ Əl ilə seç  ›  🌊 Hamısı", "lg:conset:all")],
                    [lv_btn("ERROR"), lv_btn("WARNING")],
                    [lv_btn("INFO"), lv_btn("DEBUG")]]
        rows += [[B(text="🎨 Rənglər: " + {"on": ui.ON, "off": ui.OFF, "auto": ui.AUTO}[cfg["color"]],
                    callback_data="lg:concol"),
                  B(text="🧪 Test sətirləri", callback_data="lg:contest")],
                 ui.nav("lg:panel", "Loglar", close_data="lg:close")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def logs_picker_view(note: str = ""):
        iv = interval()
        iv_label = next((l for m, l in INTERVALS if m == iv), f"{iv} dəq") if iv else "❌ Söndürülüb"
        text = (
            "📜 <b>Bot logları › Interval</b>\n━━━━━━━━━━━━━━━━━━\n"
            "⏱ <b>Logların kanala avtomatik göndəriş intervalı:</b>\n"
            f"Hazırkı dəyər: <b>{iv_label}</b>\n"
            "<i>(0 olduqda söndürülür, dəqiqə seçildikdə həmin müddətdən bir loglar avtomatik kanala göndərilir)</i>"
            + (f"\n\n{note}" if note else "")
        )
        rows = ui.stepper("lg:vstep:iv:", "lg:vinp:iv") + [ui.nav("lg:panel", "Loglar", close_data="lg:close")]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    async def show(cb_or_ids, text, kb):
        await ui.show(cb_or_ids, text, kb, bot=bot)

    @dp.message(Command("logs"))
    async def logs_cmd(message: types.Message):
        if message.from_user and is_creator(message.from_user.id):
            text, kb = panel()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("lg:"))
    async def logs_callback(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        action = parts[1]
        if action != "setchat":
            state["await_chat"] = False
        note = ""
        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/logs_plugin.py:316]: %r", _e)
            return
        if action in ("con", "conset", "contg", "concol", "contest"):
            note = ""
            if action == "conset" and len(parts) > 2 and parts[2] in ("all", "manual"):
                cfg = clog.apply_console(mode=parts[2])
                note = "🌊 Hamısı — bütün loqlar" if parts[2] == "all" else "✋ Əl ilə seçim — səviyyələri ✅/❌ et"
                logging.getLogger(__name__).info(f"🖥 Terminal loqları: {console_label()}")
            elif action == "contg" and len(parts) > 2 and parts[2] in clog.CONSOLE_LEVELS:
                cfg = clog.apply_console(toggle=parts[2])
                on = parts[2] in cfg["levels"]
                note = f"{clog.CONSOLE_LEVELS[parts[2]][0]} {clog.CONSOLE_LEVELS[parts[2]][1]}: " + ("✅ görünür" if on else "❌ gizləndi")
            elif action == "concol":
                cur = clog.console_settings()["color"]
                nxt = {"on": "off", "off": "auto", "auto": "on"}[cur]
                clog.apply_console(color=nxt)
                note = "🎨 Rənglər: " + {"on": "açıq", "off": "söndürülüb", "auto": "avto"}[nxt]
            elif action == "contest":
                # yalnız terminala: 🚨 xəbərdarlıq, 📜 Telegram loqu və fayl bu sətirləri görmür
                ch = clog._console_handler
                for lvl, txt in ((logging.DEBUG, "DEBUG sətri (🐞 Debug / 🌊 Hamısı rejimində görünür)"),
                                 (logging.INFO, "INFO sətri"), (logging.WARNING, "WARNING sətri"),
                                 (logging.ERROR, "ERROR sətri (əsl xəta deyil)")):
                    if ch is not None:
                        ch.handle(logging.LogRecord("bot.plugins.logs_plugin", lvl, __file__, 0,
                                                    f"🧪 test — {txt}", None, None))
                note = "🧪 4 test sətri terminala yazıldı — seçilmiş səviyyədən aşağı olanlar görünməyəcək"
            await cb.answer(note[:190] if note else None)
            await show(cb, *console_view(note))
            return
        if action == "setchat":
            state["await_chat"] = True
            state["panel"] = (cb.message.chat.id, cb.message.message_id)
            await cb.answer()
            await show(cb, "📢 <b>Log kanalı</b>\n\nBunlardan birini göndər:\n"
                           "• kanalın <code>@username</code>-i\n• rəqəmli ID: <code>-100...</code>\n"
                           "• və ya həmin kanaldan istənilən mesajı bura <b>yönləndir</b>\n\n"
                           "<i>Bot kanalda mesaj göndərə bilən admin olmalıdır.</i>",
                       InlineKeyboardMarkup(inline_keyboard=[[ui.cancel("lg:panel")]]))
            return
        if action == "pick" and len(parts) > 2 and parts[2] == "iv":
            state["await_iv"] = False
            await cb.answer()
            await show(cb, *logs_picker_view())
            return
        if action == "vstep" and len(parts) > 2 and parts[2] == "iv":
            delta = int(parts[3]) if len(parts) > 3 else 1
            STEPS = [0, 15, 30, 60, 120, 180, 360, 720, 1440]
            cur = interval()
            idx = min(range(len(STEPS)), key=lambda i: abs(STEPS[i] - cur))
            step_by = 2 if abs(delta) == 5 else 1
            new_idx = max(0, min(len(STEPS) - 1, idx + (step_by if delta > 0 else -step_by)))
            new_v = STEPS[new_idx]
            put("interval", str(new_v))
            if new_v != 0:
                put("last_sent", str(int(time.time())))
            lbl = next((l for m, l in INTERVALS if m == new_v), f"{new_v} dəq") if new_v else "❌ Söndürüldü"
            await cb.answer(f"⏱ {lbl}")
            await show(cb, *logs_picker_view(f"✓ {lbl}"))
            return
        if action == "vinp" and len(parts) > 2 and parts[2] == "iv":
            state["await_iv"] = True
            state["panel"] = (cb.message.chat.id, cb.message.message_id)
            await cb.answer()
            iv = interval()
            iv_lbl = next((l for m, l in INTERVALS if m == iv), f"{iv} dəq") if iv else "❌ Söndürüldü"
            text = (
                "📜 <b>Bot logları › Interval</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"Hazırkı interval: <b>{iv_lbl}</b>\n\n"
                "✍️ <b>Göndəriş intervalını daxil edin:</b>\n"
                "• <b>Rəqəm</b> yazın (məs: <code>30</code>) — dəqiqə ilə təyin edilsin\n"
                "• <b>+</b> yazın — maksimum (<code>1440</code> — 24 saat)\n"
                "• <b>-</b> yazın — <b>off (söndür)</b>\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Ləğv et", callback_data="lg:pick:iv")]])
            await show(cb, text, kb)
            return
        if action == "iv" and len(parts) > 2 and parts[2].isdigit():
            put("interval", parts[2])
            if parts[2] != "0":
                put("last_sent", str(int(time.time())))     # sayma indidən başlasın
            await cb.answer("❌ Avtomatik göndəriş söndürüldü" if parts[2] == "0" else "⏱ Interval yadda saxlandı")
        elif action == "level":
            put("level", "all" if get("level") == "warn" else "warn")
            await cb.answer("📊 Səviyyə dəyişdi")
        elif action == "send":
            await cb.answer("Göndərilir...")
            note = await send_logs(manual=True)
        else:
            await cb.answer()
        text, kb = panel(note)
        await show(cb, text, kb)

    async def waiting_log_iv(message: types.Message) -> bool:
        return bool(state.get("await_iv")) and bool(message.from_user) and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, waiting_log_iv)
    async def set_log_iv_text(message: types.Message):
        raw = message.text.strip().lower()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/logs_plugin.py:419]: %r", _e)
        note = ""
        panel_coords = state.get("panel")
        if raw in ("-", "off", "bagli", "bağlı", "0"):
            put("interval", "0")
            note = "❌ Avtomatik göndəriş söndürüldü"
        elif raw in ("+", "max", "sonsuz"):
            put("interval", "1440")
            put("last_sent", str(int(time.time())))
            note = "✅ Interval: 24 saat (1440 dəq)"
        elif raw.isdigit():
            val = max(0, min(10080, int(raw)))
            put("interval", str(val))
            if val != 0:
                put("last_sent", str(int(time.time())))
            lbl = next((l for m, l in INTERVALS if m == val), f"{val} dəq") if val else "söndürüldü"
            note = f"✅ Interval: {lbl}"
        else:
            text = (
                "📜 <b>Bot logları › Interval</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                "• <b>Rəqəm</b> (məs: <code>30</code>)\n"
                "• <b>+</b> (maksimum 24 saat)\n"
                "• <b>-</b> (söndür)\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Ləğv et", callback_data="lg:pick:iv")]])
            if panel_coords:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=panel_coords[0], message_id=panel_coords[1], text=text, parse_mode="HTML", reply_markup=kb)
            return

        state["await_iv"] = False
        text, kb = logs_picker_view(note)
        if panel_coords:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(chat_id=panel_coords[0], message_id=panel_coords[1], text=text, parse_mode="HTML", reply_markup=kb)

    async def waiting_chat(message: types.Message) -> bool:
        return state["await_chat"] and bool(message.from_user) and is_creator(message.from_user.id)

    @dp.message(F.chat.type == "private", waiting_chat)
    async def set_log_chat(message: types.Message):
        try:
            r = await dest.resolve_message(bot, message)      # t.me/qrup/5 → forum mövzusu
        except ValueError as e:
            await message.reply(f"❌ {escape(str(e))[:300]}\n<i>Alternativ: mövzunun içində /setlog yaz.</i>",
                                parse_mode="HTML")
            return

        try:
            kw = {"message_thread_id": r["thread_id"]} if r["thread_id"] else {}
            await bot.send_message(r["chat_id"], "✅ <b>Bot logları bura göndəriləcək.</b>", parse_mode="HTML",
                                   disable_notification=True, **kw)
        except Exception as e:
            await message.reply(f"❌ Bu çata yaza bilmirəm: <code>{escape(str(e))[:200]}</code>\n"
                                "<i>Bot qrupda üzv / kanalda admin olmalıdır.</i>", parse_mode="HTML")
            return
        dest.put("logs", r["chat_id"], r["thread_id"], r["title"], r["type"])
        state["await_chat"] = False
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/logs_plugin.py:482]: %r", _e)
        text, kb = panel(f"✅ Hədəf təyin edildi: <b>{escape(r['title'])}</b>")
        if state["panel"]:
            await show(state["panel"], text, kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

    logger.info("✅ Logs plugin yükləndi (/logs)")


async def teardown(context):
    st = getattr(context, "logs_state", None)
    if st:
        st["stop"] = True
        if st.get("task"):
            st["task"].cancel()
    h = getattr(context, "logs_buffer_handler", None)
    if h:
        logging.getLogger().removeHandler(h)
