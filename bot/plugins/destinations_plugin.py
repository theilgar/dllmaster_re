"""
📍 Hədəflər + 🚨 ani xəta bildirişləri — bot/plugins/destinations_plugin.py (yalnız creator)

/menu → 🖥 Sistem → 📍 Hədəflər
  📜 Loglar · 💾 Yedəklər · 🚨 Ani xətalar — hər biri kanala, qrupa və ya community MÖVZUSUNA
    ✏️ — kanal: @kanal, -100... ID, t.me linki və ya kanaldan yönləndirilmiş mesaj
    qrup / community mövzusu: həmin mövzunun içində yaz →  /setlog · /setbackup · /seterrors
        (/setlog off — söndür).  Mövzu ID-si avtomatik götürülür.
    🧪 Test — hədəfə sınaq mesajı · 🗑 Söndür
  🚨 Ani xətalar: ✅/❌ · səviyyə (❌ ERROR / ⚠️ WARNING+) · eyni xəta pəncərəsi (dəq.)

Ani xətalar: hər ERROR (və ya WARNING) log sətri dərhal hədəfə gedir — traceback ilə.
  • eyni xəta pəncərə ərzində təkrarlansa bir dəfə göndərilir, sonra "🔁 N dəfə təkrarlandı" xülasəsi
  • dəqiqədə ən çox 10 bildiriş (Telegram limiti / spam qoruması), artığı xülasəyə düşür
  • bildiriş göndərərkən baş verən xəta yenidən göndərilmir (sonsuz dövr qorunması)
"""
import asyncio
import contextlib
import logging
import threading
import time
import traceback
from collections import deque
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from common import destinations as dest
from common.database import get_db
from common.logger import redact
from bot.core.input_wait import InputWait, chain_menu_text
from bot.core import panel as ui
from bot.core.panel import btn
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 71                      # logs (70)-dan sonra, backup (72)-dən əvvəl
WAIT_TTL = 600
PER_MINUTE = 10
# müvəqqəti şəbəkə xətaları — bildiriş göndərilmir (logda qalır)
TRANSIENT = ("TelegramNetworkError", "ServerDisconnectedError", "ClientConnectorError", "TimeoutError",
             "Request timeout", "Connection reset", "Bad Gateway", "Gateway Timeout", "Service Unavailable")
# tanınmış xətalar üçün izah
HINTS = {
    "TelegramConflictError": "🔁 Eyni bot İKİ YERDƏ işləyir. Serverdə: <code>pgrep -af 'init.py|app.py'</code> → "
                             "artığını <code>kill PID</code> və ya köhnə systemd servisini söndür.",
    "query is too old": "⏱ Düyməyə gec cavab (restart zamanı basılıb) — zərərsizdir.",
    "Forbidden: bot was blocked by the user": "🚫 İstifadəçi botu bloklayıb — zərərsizdir.",
}
_SENDING = threading.local()       # bildiriş göndərərkən yaranan loglar yenidən göndərilməsin




def _setting(key, default=None):
    try:
        v = get_db().get_setting(key)
    except Exception:
        v = None
    return default if v is None else v


class AlertHandler(logging.Handler):
    """ERROR (və ya WARNING) qeydlərini asyncio növbəsinə ötürür — istənilən thread-dən təhlükəsiz."""

    def __init__(self, loop, queue):
        super().__init__(level=logging.WARNING)
        self.loop, self.queue = loop, queue

    def emit(self, record):
        if getattr(_SENDING, "on", False) or record.name.startswith(__name__):
            return
        try:
            level = logging.WARNING if _setting("alerts:level", "error") == "warning" else logging.ERROR
            if record.levelno < level or _setting("alerts:on", "1") != "1":
                return
            msg = redact(record.getMessage())          # 🔒 token / sirlər hədəfə getməsin
            if record.name.startswith("aiogram") and any(t in msg for t in TRANSIENT):
                return                       # qısa şəbəkə qırılması — aiogram özü yenidən qoşulur
            tb = redact("".join(traceback.format_exception(*record.exc_info))) if record.exc_info else ""
            item = {"ts": record.created, "level": record.levelname, "name": record.name,
                    "msg": msg, "tb": tb, "where": f"{record.pathname}:{record.lineno}"}
            self.loop.call_soon_threadsafe(self.queue.put_nowait, item)
        except Exception:
            pass


def setup(context):
    dp, bot = context.dp, context.bot
    state = InputWait(WAIT_TTL)
    stats = {"sent": 0, "suppressed": 0, "last": 0.0}

    is_creator = context.is_creator


    waiting = state.waiting

    chain_menu_text(context, lambda uid: waiting() and is_creator(uid))

    # ───────────────────────── 🚨 ani xətalar ─────────────────────────
    queue = asyncio.Queue()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    old = getattr(context, "alert_handler", None)
    root = logging.getLogger()
    if old:
        root.removeHandler(old)
    handler = AlertHandler(loop, queue)
    root.addHandler(handler)
    context.alert_handler = handler

    def window() -> int:
        return int(_setting("alerts:window", "10") or 10)

    async def send_alert(item: dict, repeats: int = 0):
        kw = dest.kwargs("errors")
        if not kw:
            return
        icon = "🚨" if item["level"] in ("ERROR", "CRITICAL") else "⚠️"
        head = (f"{icon} <b>{item['level']}</b> · <code>{escape(item['name'])}</code>\n"
                f"🕒 {time.strftime('%d.%m %H:%M:%S', time.localtime(item['ts']))}"
                + (f" · 🔁 əvvəlki {repeats} dəfə təkrarlandı" if repeats else "") + "\n"
                f"📍 <code>{escape(item['where'][-80:])}</code>\n\n"
                f"<b>{escape(item['msg'][:600])}</b>")
        hint = next((h for k, h in HINTS.items() if k in item["msg"] or k in item["tb"]), None)
        if hint:
            head += f"\n\n💡 {hint}"
        tb = item["tb"].strip()
        _SENDING.on = True
        try:
            if tb and len(head) + len(tb) < 3800:
                from common.menu import wrap_ui_text
                await bot.send_message(text=head + f"\n{wrap_ui_text(escape(tb[-3000:]))}", parse_mode="HTML",
                                       disable_web_page_preview=True, **kw)
            elif tb:
                await bot.send_document(document=BufferedInputFile(tb.encode("utf-8", "replace"),
                                                                   filename=f"traceback_{int(item['ts'])}.txt"),
                                        caption=head[:1020], parse_mode="HTML", **kw)
            else:
                await bot.send_message(text=head, parse_mode="HTML", disable_web_page_preview=True, **kw)
            stats["sent"] += 1
            stats["last"] = time.time()
        except Exception as e:
            print(f"[error_alerts] göndərilmədi: {e}")           # loga yox — dövr olmasın
        finally:
            _SENDING.on = False

    async def worker():
        seen = {}                          # açar → {"first": ts, "count": n, "item": item}
        recent = deque()                   # son dəqiqədə göndərilənlər
        while True:
            try:
                try:
                    item = await asyncio.wait_for(queue.get(), 30)
                except asyncio.TimeoutError:
                    item = None
                now = time.time()
                # pəncərəsi bitmiş təkrarlar → xülasə
                for key in [k for k, v in seen.items() if now - v["first"] > window() * 60]:
                    v = seen.pop(key)
                    if v["count"] > 1:
                        await send_alert(v["item"], repeats=v["count"] - 1)
                if item is None:
                    continue
                key = (item["name"], item["msg"].splitlines()[0][:200] if item["msg"] else "")
                if key in seen:
                    seen[key]["count"] += 1
                    seen[key]["item"] = item
                    stats["suppressed"] += 1
                    continue
                seen[key] = {"first": now, "count": 1, "item": item}
                while recent and now - recent[0] > 60:
                    recent.popleft()
                if len(recent) >= PER_MINUTE:
                    stats["suppressed"] += 1
                    continue
                recent.append(now)
                await send_alert(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[error_alerts] işçi xətası: {e}")

    old_task = getattr(context, "alert_task", None)
    if old_task and not old_task.done():
        old_task.cancel()
    context.alert_task = loop.create_task(worker())

    # ───────────────────────── panel ─────────────────────────
    def label() -> str:
        missing = [k for k in dest.ORDER if not dest.get(k)]
        return "📍 Hədəflər · " + ("✅" if not missing else f"⚠️ {len(missing)} boş")

    context.destinations_label = label

    def panel(note: str = ""):
        on = _setting("alerts:on", "1") == "1"
        lvl = _setting("alerts:level", "error")
        lines = ["<i>🏠 › 🖥 Sistem</i>", "📍 <b>Göndərmə hədəfləri</b>", "━━━━━━━━━━━━━━━━━━"]
        for k in dest.ORDER:
            lines.append(f"{dest.KINDS[k]['label']}: <b>{escape(dest.describe(k))}</b>")
        lines += [
            "━━━━━━━━━━━━━━━━━━",
            f"🚨 <b>Ani xətalar:</b> {'✅ açıq' if on else '❌ bağlı'} · "
            f"{'⚠️ WARNING+' if lvl == 'warning' else '❌ ERROR'} · eyni xəta {window()} dəq.-də bir dəfə",
            f"📊 Göndərilib: {stats['sent']} · birləşdirilib: {stats['suppressed']}",
            "━━━━━━━━━━━━━━━━━━",
            "<b>Qrup / community mövzusu üçün:</b> botu qrupa əlavə et, lazımi mövzunun içində yaz:",
            "<code>/setlog</code> · <code>/setbackup</code> · <code>/seterrors</code>  (söndürmək: "
            "<code>/setlog off</code>)",
            "<b>Kanal üçün:</b> ✏️ düyməsi → @kanal / -100... / yönləndirilmiş mesaj",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = []
        for k in dest.ORDER:
            short = dest.KINDS[k]["label"].split(" ", 1)[0]
            r = [btn(f"{short} ✏️", f"ds:set:{k}")]
            if dest.get(k):
                r += [btn(f"{short} 🧪", f"ds:test:{k}"), btn(f"{short} 🗑", f"ds:off:{k}")]
            rows.append(r)
        rows.append([ui.toggle("🚨 Xəbərdarlıq", on, "ds:alerts", compact=True),
                     btn(f"Səviyyə: {'⚠️+❌' if lvl == 'warning' else '❌'}", "ds:level"),
                     btn(f"⏱ {window()} dəq", "ds:window")])
        rows.append(ui.nav("menu:arch", "Arxiv", close_data="ds:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    show = state.show                 # 🧩 InputWait: yenilə + panel kimi yadda saxla

    async def to_panel(message, text, kb):
        await state.to_panel(bot, message, text, kb)

    async def test_send(kind: str) -> str:
        kw = dest.kwargs(kind)
        if not kw:
            return "❌ Hədəf yoxdur"
        try:
            await bot.send_message(text=f"🧪 <b>Sınaq</b> — {dest.KINDS[kind]['label']} bura gələcək.",
                                   parse_mode="HTML", disable_notification=True, **kw)
            return "✅ Sınaq mesajı göndərildi"
        except Exception as e:
            return f"❌ Göndərilmədi: <code>{escape(str(e))[:200]}</code>"

    @dp.callback_query(F.data.startswith("ds:"))
    async def ds_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act, arg = parts[1], (parts[2] if len(parts) > 2 else "")
        note = ""
        if act == "close":
            state.clear()
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        if act == "set" and arg in dest.KINDS:
            state.start(arg)
            await cb.answer()
            cmd = dest.KINDS[arg]["cmd"]
            await show(cb, (f"<i>🏠 › 🖥 Sistem › 📍 Hədəflər</i>\n✏️ <b>{dest.KINDS[arg]['label']} — hədəf</b>\n\n"
                            "<b>Kanal:</b> göndər <code>@kanal</code> · <code>-100...</code> · "
                            "<code>https://t.me/kanal</code> və ya kanaldan mesaj <b>yönləndir</b>\n\n"
                            f"<b>Qrup / community mövzusu:</b> həmin mövzunun içində <code>/{cmd}</code> yaz\n\n"
                            "<i>Bot hədəfdə mesaj göndərə bilməlidir (kanalda admin).</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "ds:panel")]]))
            return
        if act == "test" and arg in dest.KINDS:
            await cb.answer("🧪")
            note = await test_send(arg)
        elif act == "off" and arg in dest.KINDS:
            dest.clear(arg)
            await cb.answer("🗑 Söndürüldü")
        elif act == "alerts":
            get_db().set_setting("alerts:on", "0" if _setting("alerts:on", "1") == "1" else "1")
            await cb.answer()
        elif act == "level":
            get_db().set_setting("alerts:level", "error" if _setting("alerts:level", "error") == "warning" else "warning")
            await cb.answer()
        elif act == "window":
            steps = [1, 5, 10, 30, 60]
            cur = window()
            nxt = next_of(steps, cur, 10)
            get_db().set_setting("alerts:window", str(nxt))
            await cb.answer(f"⏱ {nxt} dəq")
        else:
            state.clear()
            await cb.answer()
        await show(cb, *panel(note))

    # ── kanal (şəxsi çatda mətn / yönləndirmə) ──
    @dp.message(F.chat.type == "private", state.for_creator(is_creator))
    async def on_channel_input(message: types.Message):
        kind = state["await"]
        try:
            # t.me/qrup/5 → @qrup + 5-ci mövzu (forum) · t.me/c/123/5 · @kanal · -100... · yönləndirmə
            r = await dest.resolve_message(bot, message)
        except ValueError as e:
            await message.reply(f"❌ {escape(str(e))[:300]}\n\n<i>Alternativ: həmin mövzunun içində "
                                f"<code>/{dest.KINDS[kind]['cmd']}</code> yaz.</i>", parse_mode="HTML")
            return

        try:
            kw = {"message_thread_id": r["thread_id"]} if r["thread_id"] else {}
            await bot.send_message(r["chat_id"], f"✅ {dest.KINDS[kind]['label']} bura göndəriləcək.",
                                   disable_notification=True, **kw)
        except Exception as e:
            await message.reply(f"❌ Bu çata yaza bilmirəm: <code>{escape(str(e))[:200]}</code>\n"
                                "<i>Bot qrupda üzv / kanalda admin olmalıdır; bağlı mövzuda admin.</i>",
                                parse_mode="HTML")
            return
        dest.put(kind, r["chat_id"], r["thread_id"], r["title"], r["type"])
        state.clear()
        with contextlib.suppress(Exception):
            await message.delete()
        await to_panel(message, *panel(f"✅ {dest.KINDS[kind]['label']}: <b>{escape(dest.describe(kind))}</b>"))

    # ── /setlog · /setbackup · /seterrors — qrupda / community mövzusunda ──
    @dp.message(Command(*[v["cmd"] for v in dest.KINDS.values()]))
    async def set_here(message: types.Message):
        if not message.from_user or not is_creator(message.from_user.id):
            return                                      # başqasına cavab vermə (anonim admin da keçmir)
        cmd = (message.text or "").split()[0].lstrip("/").split("@")[0].lower()
        kind = next((k for k, v in dest.KINDS.items() if v["cmd"] == cmd), None)
        if not kind:
            return
        arg = ((message.text or "").split(maxsplit=1)[1:] or [""])[0].strip().lower()
        if arg in ("off", "söndür", "sil"):
            dest.clear(kind)
            await message.reply(f"🗑 {dest.KINDS[kind]['label']} hədəfi silindi.")
            return
        chat = message.chat
        thread = message.message_thread_id if getattr(message, "is_topic_message", False) else None
        title = chat.title or chat.full_name or str(chat.id)
        topic = None
        rt = getattr(message, "reply_to_message", None)
        ftc = getattr(rt, "forum_topic_created", None) if rt else None
        if thread and ftc is not None:
            topic = ftc.name
        if topic:
            title = f"{title} › {topic}"
        ctype = "forum" if getattr(chat, "is_forum", False) else getattr(chat.type, "value", chat.type)
        dest.put(kind, chat.id, thread, title, ctype)
        await message.reply(f"✅ {dest.KINDS[kind]['label']} artıq bura göndəriləcək"
                            + (f" (mövzu #{thread})" if thread else "") + ".\n"
                            f"<i>Söndürmək: /{dest.KINDS[kind]['cmd']} off</i>", parse_mode="HTML")
        logger.info(f"📍 {kind} hədəfi → {title} ({chat.id}, mövzu {thread})")

    logger.info("✅ Hədəflər + ani xəta bildirişləri yükləndi")


async def teardown(context):
    h = getattr(context, "alert_handler", None)
    if h:
        logging.getLogger().removeHandler(h)
    t = getattr(context, "alert_task", None)
    if t and not t.done():
        t.cancel()
