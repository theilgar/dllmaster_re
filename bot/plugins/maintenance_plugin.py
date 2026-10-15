"""
🛠 Texniki iş rejimi qapısı — bot/plugins/maintenance_plugin.py

Rejim açıqkən (defolt «bağlı» rejimində) creator-dan başqa heç kim botdan istifadə edə bilmir — musiqi, linklər, 🎙 səsli yayım,
Shazam, audio redaktor, playlist, inline axtarış, qrup əmrləri (əvvəl yalnız musiqi plugin-ində ayrıca yoxlanırdı).
Bir qapı hamısını bağlayır (outer middleware: message / callback_query / inline_query).

İcazə verilənlər (rejimdə də işləyir):
  • əmrlər: /start /help /lang /premium — salamlaşma, dil, premium məlumatı
  • düymələr: dil (ln: lang:), ödəniş / bəxşiş (buy: tip: tip_prompt:), /start düymələri (sg: start:)
  • ödənişin özü: pre_checkout / successful_payment — pul heç vaxt «asılı qalmasın»
  • qruplarda — yalnız əmrlər bağlanır (adi söhbətə toxunulmur)
Bağlanan istifadəçiyə mətn göstərilir (120 san.-də bir dəfə), düymədə alert çıxır, inline-da tək nəticə görünür.

«⚠️ Xəbərdarlıq» rejimində isə HAMI botdan istifadə edir — yalnız özəl çatda 30 dəq.-də bir dəfə qısa xəbərdarlıq
mesajı görünür (bağlama, yayım dayandırma və «bitəndə xəbər» burada işləmir, çünki heç kim bloklanmır).

Əlavə: müddət (avto-bitmə), aktiv səsli yayımları dayandırmaq, depo doldurucunu pauza etmək,
bitəndə cəhd edənlərə xəbər vermək — common/maintenance.py + /menu → 🛠 Bot idarəsi → 🛠 Texniki iş rejimi.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from types import SimpleNamespace

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from common import maintenance
from common.utilities import spawn

logger = logging.getLogger(__name__)

PRIORITY = 2                                   # digər plugin-lərdən əvvəl yüklənsin

VOICE_COMMANDS = {"play", "p", "oynat", "mixplay", "mp", "radio"}      # qrupda səsli yayımı başladan əmrlər
ALLOWED_COMMANDS = {"start", "help", "lang", "language", "dil", "premium"}
ALLOWED_CB_PREFIXES = ("ln:", "lang:", "buy:", "tip:", "tip_prompt:", "sg:", "start:", "rp:")   # rp: — «Adminə bildir»
NOTICE_COOLDOWN = 120.0
NOTIFY_DELAY = 0.05                            # ~20 mesaj/san
WATCH_EVERY = 15

_CMD_RE = re.compile(r"^[/!.]([A-Za-z_]+)")


def _plain(html: str, limit: int = 190) -> str:
    return re.sub(r"<[^>]+>", "", html).replace("\n\n", "\n").strip()[:limit]


def command_of(text: str | None) -> str | None:
    m = _CMD_RE.match((text or "").strip())
    return m.group(1).lower() if m else None


DELETE_STEP = 0.05


async def _delete_later(bot, chat_id: int, message_id: int, delay: float):
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await bot.delete_message(chat_id, message_id)


def schedule_cleanup(context, sent, done: bool = False) -> None:
    """Göndərilən bildirişi seçilmiş qaydaya görə sil: gecikmə (1 dəq. / 10 dəq. / 1 saat) və ya «rejim bitəndə»
    (izlənir, rejim söndürüləndə hamısı silinir). «Bitdi» mesajı yalnız gecikməyə tabedir."""
    mode = maintenance.delete_mode()
    chat_id = getattr(getattr(sent, "chat", None), "id", None)
    mid = getattr(sent, "message_id", None)
    if mode == "0" or not isinstance(chat_id, int) or not isinstance(mid, int):
        return
    if mode == "end":
        if not done:
            maintenance.track_notice(chat_id, mid)
        return
    spawn(_delete_later(context.bot, chat_id, mid, int(mode)))


async def cleanup_notices(context) -> int:
    """İzlənən bütün bildirişləri indi silir → silinən say."""
    n = 0
    for chat_id, mid in maintenance.pop_notices():
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id, mid)
            n += 1
        await asyncio.sleep(DELETE_STEP)
    return n


class MaintenanceGate(BaseMiddleware):
    def __init__(self, context):
        self.context = context
        self.last_notice: dict = {}

    def _creator(self, uid: int) -> bool:
        return bool(self.context.is_creator(uid))

    def _notice_due(self, key, cooldown: float = NOTICE_COOLDOWN) -> bool:
        now = time.monotonic()
        if now - self.last_notice.get(key, 0) < cooldown:
            return False
        self.last_notice[key] = now
        if len(self.last_notice) > 5000:
            self.last_notice = {k: t for k, t in self.last_notice.items() if now - t < maintenance.NOTICE_EVERY}
        return True

    async def __call__(self, handler, event, data):
        if not maintenance.is_active():
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        if user is None or self._creator(user.id):
            return await handler(event, data)

        if maintenance.mode() == "notice":                      # hamı istifadə edir — yalnız xəbərdarlıq göstərilir
            return await self._notice_only(handler, event, data, user)

        if isinstance(event, Message):
            return await self._message(handler, event, data, user)
        if isinstance(event, CallbackQuery):
            return await self._callback(handler, event, data, user)
        if isinstance(event, InlineQuery):
            return await self._inline(event, user)
        return await handler(event, data)

    # ── xəbərdarlıq rejimi: heç nə bağlanmır ──
    async def _notice_only(self, handler, event, data, user):
        res = await handler(event, data)
        if isinstance(event, Message) and not getattr(event, "successful_payment", None):
            if event.chat.type == "private":
                key = ("n", user.id)
            elif command_of(event.text or event.caption) in VOICE_COMMANDS and maintenance.settings()["stream_notice"]:
                key = ("g", event.chat.id)                       # qrupda səsli yayım başlayır — qrupa eyni bildiriş
            else:
                return res
            if self._notice_due(key, maintenance.NOTICE_EVERY):
                with contextlib.suppress(Exception):
                    sent = await event.answer(maintenance.message(), parse_mode="HTML", disable_web_page_preview=True)
                    schedule_cleanup(self.context, sent)
        return res

    # ── mesajlar ──
    async def _message(self, handler, msg: Message, data, user):
        if getattr(msg, "successful_payment", None):
            return await handler(msg, data)                           # ödəniş tamamlanır — heç vaxt bloklanmır
        cmd = command_of(msg.text or msg.caption)
        if cmd in ALLOWED_COMMANDS:
            return await handler(msg, data)
        if user.id in (getattr(self.context, "tip_waiting", None) or ()):
            return await handler(msg, data)                           # bəxşiş məbləğini yazır
        if msg.chat.type != "private" and cmd is None:
            return await handler(msg, data)                           # qrupda adi söhbət — toxunulmur
        maintenance.add_waiter(user.id)
        if self._notice_due((msg.chat.id, user.id)):
            with contextlib.suppress(Exception):
                sent = await msg.answer(maintenance.message(), parse_mode="HTML", disable_web_page_preview=True)
                schedule_cleanup(self.context, sent)
        return None

    # ── düymələr ──
    async def _callback(self, handler, cb: CallbackQuery, data, user):
        if (cb.data or "").startswith(ALLOWED_CB_PREFIXES):
            return await handler(cb, data)
        maintenance.add_waiter(user.id)
        with contextlib.suppress(Exception):
            await cb.answer(_plain(maintenance.render_text()), show_alert=True)
        return None

    # ── inline ──
    async def _inline(self, q: InlineQuery, user):
        maintenance.add_waiter(user.id)
        text = maintenance.message()
        with contextlib.suppress(Exception):
            await q.answer(
                [InlineQueryResultArticle(
                    id="maintenance", title="🛠 Texniki işlər gedir",
                    description=_plain(text.split("\n\n", 1)[-1], 80) or "Bot bir azdan yenidən işləyəcək",
                    input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML",
                                                                  disable_web_page_preview=True))],
                cache_time=3, is_personal=True)
        return None


# ───────────────────────── rejimin tətbiqi ─────────────────────────
async def stop_voice(context) -> int:
    """Aktiv səsli yayımları dayandırır (qruplara xəbər gedir). Dayandırılan yayım sayını qaytarır."""
    va = getattr(context, "voice_assistant", None)
    chats = list(getattr(va, "chats", {}) or {})
    for cid in chats:
        with contextlib.suppress(Exception):
            await va._end_chat(cid, announce=True, reason="texniki iş rejimi")
    return len(chats)


def voice_active(context) -> int:
    return len(getattr(getattr(context, "voice_assistant", None), "chats", {}) or {})


async def announce_streams(context, text: str) -> list:
    """Aktiv səsli yayımı olan qruplara mətni göndərir → bildiriş çatan qrupların siyahısı."""
    va = getattr(context, "voice_assistant", None)
    sent = []
    for cid in list(getattr(va, "chats", {}) or {}):
        with contextlib.suppress(Exception):
            m = await context.bot.send_message(cid, text, parse_mode="HTML", disable_web_page_preview=True)
            sent.append(cid)
            schedule_cleanup(context, m)
    return sent


async def pause_depo(context) -> bool:
    filler = getattr(context, "depo_filler", None)
    if filler is None or not filler.running:
        return False
    maintenance.set_option(maintenance.K_DEPO_WAS, "1")
    with contextlib.suppress(Exception):
        await filler.stop(persist=False)                              # «on» bayrağı qalır — rejim bitəndə davam edir
    return True


async def start_maintenance(context, duration: int | None = None) -> dict:
    """Rejimi açır və seçilmiş əlavə əməliyyatları (yayım / depo) tətbiq edir."""
    until = maintenance.enable(duration)
    s = maintenance.settings()
    res = {"until": until, "voice": 0, "depo": False, "announced": 0}
    if s["stream_notice"]:                                              # yayım qruplarına istifadəçilərlə eyni bildiriş
        sent = await announce_streams(context, maintenance.message())
        maintenance.save_stream_chats(sent)
        res["announced"] = len(sent)
    if s["voice"]:
        res["voice"] = await stop_voice(context)
    if s["depo"]:
        res["depo"] = await pause_depo(context)
    logger.info(f"🛠 Texniki iş rejimi AÇILDI (müddət: {maintenance.duration_label(s['duration'])}; "
                f"yayım dayandı: {res['voice']}; yayım qruplarına bildiriş: {res['announced']}; depo pauza: {res['depo']})")
    return res


async def _notify_waiters(context, uids: list):
    sent = 0
    for uid in uids:
        with contextlib.suppress(Exception):
            m = await context.bot.send_message(uid, maintenance.message("done"), parse_mode="HTML")
            sent += 1
            schedule_cleanup(context, m, done=True)
        await asyncio.sleep(NOTIFY_DELAY)
    logger.info(f"🛠 Texniki işlər bitdi: {sent}/{len(uids)} istifadəçiyə xəbər verildi")


async def finish_maintenance(context, auto: bool = False) -> dict:
    """Rejimi söndürür, depo doldurucunu davam etdirir, cəhd edənlərə xəbər verir."""
    notify = maintenance.settings()["notify"]
    waiters = maintenance.disable()
    res = {"waiters": len(waiters), "notified": bool(notify and waiters), "depo": False,
           "deleted": await cleanup_notices(context)}                       # «rejim bitəndə sil» seçilibsə bildirişlər silinir
    filler = getattr(context, "depo_filler", None)
    if maintenance._get(maintenance.K_DEPO_WAS) == "1":
        maintenance.set_option(maintenance.K_DEPO_WAS, "0")
        if filler is not None and not filler.running:
            with contextlib.suppress(Exception):
                filler.start()
                res["depo"] = True
    if notify and waiters:
        spawn(_notify_waiters(context, waiters))
    stream_chats = maintenance.pop_stream_chats()
    if stream_chats:                                                    # yayım qruplarına da «bitdi»
        res["streams"] = len(stream_chats)
        spawn(_notify_waiters(context, stream_chats))
    if auto and getattr(context, "creator_id", None):
        with contextlib.suppress(Exception):
            await context.bot.send_message(
                context.creator_id,
                "🛠 <b>Texniki iş rejimi müddəti bitdi</b> — bot normal rejimə qayıtdı."
                + (f"\n🔔 {len(waiters)} istifadəçiyə xəbər verilir." if res["notified"] else ""),
                parse_mode="HTML")
    logger.info(f"🛠 Texniki iş rejimi SÖNDÜRÜLDÜ ({'avto' if auto else 'əl ilə'}); gözləyən: {len(waiters)}; "
                f"silinən bildiriş: {res['deleted']}")
    return res


async def _watch(context):
    """Müddət bitəndə rejimi özü söndürür; restartdan sonra depo pauzasını bərpa edir."""
    await asyncio.sleep(15)
    s = maintenance.settings()
    if maintenance.is_active() and s["depo"]:
        filler = getattr(context, "depo_filler", None)
        if filler is not None and filler.running:
            await pause_depo(context)
    while True:
        try:
            if maintenance.is_due():
                await finish_maintenance(context, auto=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Texniki iş izləyicisi xətası: {e}")
        await asyncio.sleep(WATCH_EVERY)


def setup(context):
    dp = context.dp
    gate = getattr(dp, "_maintenance_gate", None)
    if gate is None:                                                   # plugin yenidən yüklənəndə təkrar qoşulmasın
        gate = MaintenanceGate(context)
        dp.message.outer_middleware(gate)
        dp.callback_query.outer_middleware(gate)
        dp.inline_query.outer_middleware(gate)
        dp._maintenance_gate = gate
    else:
        gate.context = context
    context.maintenance = SimpleNamespace(
        start=lambda duration=None: start_maintenance(context, duration),
        finish=lambda auto=False: finish_maintenance(context, auto),
        stop_voice=lambda: stop_voice(context),
        cleanup=lambda: cleanup_notices(context),
        voice_active=lambda: voice_active(context),
    )
    old = getattr(context, "_maintenance_task", None)
    if old is not None and not old.done():
        old.cancel()
    context._maintenance_task = spawn(_watch(context))
    logger.info("🛠 Texniki iş rejimi qapısı hazırdır"
                + (" — REJİM AKTİVDİR" if maintenance.is_active() else ""))


def teardown(context):
    task = getattr(context, "_maintenance_task", None)
    if task is not None and not task.done():
        task.cancel()
    context._maintenance_task = None
