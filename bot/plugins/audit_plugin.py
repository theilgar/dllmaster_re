"""🛡 audit_plugin.py — Telegram Qrup ChatMember Dinləyicisi və Audit Hesabat Sistemi.

Bu plugin:
1. Qruplarda baş verən bütün üzv və admin status dəyişikliklərini (ChatMemberUpdated) passiv dinləyir:
   - Ban, Unban, Mute, Unmute, Kick, Promote, Demote, Admin hüquq dəyişiklikləri.
   - İcraçı admin (from_user) və hədəf istifadəçini (new_chat_member.user) mərkəzi audit bazasında qeyd edir.
2. /audit və /hesabat əmrləri ilə istənilən an günün tam interaktiv HTML hesabatını ixrac edir.
3. Gün sonunda (23:59 AZT) avtomatik HTML hesabat göndərən fon xidmətini (scheduler) idarə edir.
Qeyd: Botda admin/moderasiya komandaları yoxdur; bütün komandalar və hadisələr fon rejimi və userbot ilə qeydə alınır.
"""
import asyncio
import contextlib
import html
import logging
import os
import re
import time

from aiogram import types
from aiogram.filters import Command

from common.audit import (
    is_daily_report_enabled,
    record_audit_event,
    send_daily_audit_report,
    set_daily_report_enabled,
    start_audit_scheduler,
)
from common.utilities import spawn
from common.database import is_creator

logger = logging.getLogger(__name__)

PRIORITY = 45


from common.permissions import is_admin as is_admin_or_creator


LIVE_STREAM = {"ban": "kick", "kick": "kick", "unban": "unban", "unmute": "unban", "mute": "mute",
               "promote": "promo", "demote": "promo", "admin_edit": "promo", "join": "join", "left": "left"}
LIVE_KIND = {"promote": "adm+", "demote": "adm-", "admin_edit": "adm~", "unmute": "unban"}      # logger._seen açarları ilə eyni
LIVE_TITLE = {"ban": "Ban", "unban": "Ban götürüldü", "mute": "Məhdudlaşdırıldı", "unmute": "Məhdudiyyət götürüldü",
              "kick": "Qrupdan Qovulma", "promote": "Admin təyin edildi", "demote": "Adminlikdən alındı",
              "admin_edit": "Admin hüquqları dəyişdi", "join": "Üzv Qoşuldu", "left": "Üzv Qrupdan Çıxdı"}
LIVE_ICON = {"ban": "⛔", "kick": "⛔", "unban": "🔓", "unmute": "🔊", "mute": "🔇", "promote": "👮", "demote": "👤",
             "admin_edit": "⚙️", "join": "📥", "left": "📤"}


_NOT_MEMBER = {"UserNotParticipantError", "ChannelPrivateError", "ChatForbiddenError", "ChannelInvalidError",
               "PeerIdInvalidError", "ChatIdInvalidError", "ValueError"}     # ValueError: entity heç vaxt görülməyib
_MEMBER_CACHE: dict = {}


async def _is_member(client, chat_id) -> bool:
    """Telethon hesabı bu qrupun üzvüdürmü (10 dəq. keş). Qəti «üzv deyil» xətaları False verir;
    şəbəkə / flood kimi qeyri-müəyyən xətalarda True (izlənir sayılır) — bot təkrar göndərməsin."""
    key = (id(client), chat_id)
    hit = _MEMBER_CACHE.get(key)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    try:
        await asyncio.wait_for(client.get_permissions(chat_id, "me"), 5)
        ok, ttl = True, 600
    except Exception as e:
        ok = type(e).__name__ not in _NOT_MEMBER
        ttl = 600 if not ok else 30
    _MEMBER_CACHE[key] = (ok, time.monotonic() + ttl)
    return ok


async def _watched_by_account(lg, ub, context, chat_id) -> bool:
    """Qrupu artıq asistan / userbot HƏQİQƏTƏN izləyirsə (işləyir VƏ qrupun üzvüdür) bot təkrar göndərməsin.
    Seçilmiş izləyici üzv deyilsə, qrup izlənməz qalmasın deyə bot özü göndərir."""
    if lg.group_source(ub, chat_id) == "assistant":
        slots = lg._assistant_slots(ub)
        if slots:
            for sl in slots:
                if await _is_member(sl.client, chat_id):
                    return True
            return False                                     # asistan işləyir, amma qrupda deyil → userbot da susur
    client = getattr(context, "telethon_client", None)
    try:
        connected = bool(client is not None and client.is_connected())
    except Exception:
        connected = False
    return connected and await _is_member(client, chat_id)


async def push_live(context, event_chat, actor, target, action: str, details: list, ts: float) -> bool:
    """Bot tərəfindən görülən qrup hadisəsini canlı olaraq qrupun audit hədəfinə (kanal / qrup / topic) göndərir.
    Yalnız audit açıq qrupda və qrupu heç bir hesab (asistan / userbot) izləmədikdə işləyir. True — göndərildi."""
    try:
        from types import SimpleNamespace

        from userbot.core.userbot_db import get_udb
        from userbot.plugins import logger as lg

        stream = LIVE_STREAM.get(action)
        if stream is None:
            return False
        ub = SimpleNamespace(db=get_udb(), context=context)
        cid = event_chat.id
        if not lg.is_audit_active_for(ub, cid) or await _watched_by_account(lg, ub, context, cid):
            return False
        if lg._seen((cid, target.id, LIVE_KIND.get(action, action if action != "kick" else "ban"))):
            return False
        if not lg.stream_on(ub, stream, cid):
            return False
        rows = [(f"# {LIVE_TITLE[action]}", ""), ("Qrup", f"{(event_chat.title or str(cid))[:18]} ({cid})"),
                ("Üzv", lg.U(target.full_name, target.id, target.username))]
        if actor and actor.id != target.id:
            rows.append(("İcra edən", lg.U(actor.full_name, actor.id, actor.username)))
        rows += [(d.split(":", 1)[0], d.split(":", 1)[1].strip()) if ":" in d else ("", d) for d in details]
        rows.append(("Tarix", time.strftime("%d.%m %H:%M:%S", time.localtime(ts))))
        text = lg.acard(rows, LIVE_ICON.get(action, "🛡"))

        tc, tt, _ = lg.get_stream_target(ub, stream, cid)
        chat = tc if tc else getattr(context, "creator_id", None)         # Saved / hədəf yoxdur → bot sahibi
        if isinstance(chat, str) and chat.lstrip("-").isdigit():
            chat = int(chat)
        if not chat:
            return False
        kw = {"message_thread_id": int(tt)} if tc and tt and str(tt).isdigit() else {}
        await context.bot.send_message(chat, text, parse_mode="HTML", disable_web_page_preview=True, **kw)
        return True
    except Exception as e:
        logger.debug(f"canlı audit göndərilmədi: {e}")
        return False


def setup(context):
    dp = context.dp
    bot = context.bot

    # ── 1. GÜN SONU AUDIT SCHEDULER BAŞLADILMASI ──
    context.audit_task = spawn(start_audit_scheduler(context))
    logger.info("🛡 Bot audit scheduler aktivləşdirildi.")

    # ── 2. NATIVE TELEGRAM CHATMEMBERUPDATED DİNLƏYİCİSİ ──
    @dp.chat_member()
    async def on_chat_member_updated(event: types.ChatMemberUpdated):
        try:
            chat = event.chat
            if not chat or chat.type not in ("group", "supergroup"):
                return

            actor = event.from_user
            old = event.old_chat_member
            new = event.new_chat_member
            target = new.user if new else (old.user if old else None)

            if not actor or not target:
                return

            old_st = str(getattr(old.status, "value", old.status)).lower()
            new_st = str(getattr(new.status, "value", new.status)).lower()

            action = None
            details_parts = []

            # A) Ban (Qovuldu və bloklandı)
            if new_st == "kicked":
                action = "ban"
                details_parts.append("Qrupdan banlandı")
                until = getattr(new, "until_date", None)
                if until:
                    details_parts.append(f"Bitmə tarixi: {until.strftime('%d.%m.%Y %H:%M')}")

            # B) Unban
            elif old_st == "kicked" and new_st in ("member", "left"):
                action = "unban"
                details_parts.append("Ban götürüldü")

            # C) Mute / Məhdudlaşdırma
            elif new_st == "restricted":
                action = "mute"
                blocked = []
                if not getattr(new, "can_send_messages", True):
                    blocked.append("mesaj")
                if not getattr(new, "can_send_media_messages", True):
                    blocked.append("media")
                if not getattr(new, "can_send_other_messages", True):
                    blocked.append("stiker/gif")
                if not getattr(new, "can_add_web_page_previews", True):
                    blocked.append("link")
                if blocked:
                    details_parts.append(f"Qadağalar: {', '.join(blocked)}")
                until = getattr(new, "until_date", None)
                if until:
                    details_parts.append(f"Müddət: {until.strftime('%d.%m.%Y %H:%M')}")

            # D) Unmute
            elif old_st == "restricted" and new_st in ("member", "administrator"):
                action = "unmute"
                details_parts.append("Məhdudiyyət götürüldü")

            # E) Admin təyinatı (Promote)
            elif new_st == "administrator" and old_st != "administrator":
                action = "promote"
                rights = []
                for attr, name in (
                    ("can_delete_messages", "silmə"),
                    ("can_restrict_members", "ban/mute"),
                    ("can_promote_members", "admin"),
                    ("can_change_info", "məlumat"),
                    ("can_pin_messages", "sancaq"),
                    ("can_manage_video_chats", "yayım"),
                ):
                    if getattr(new, attr, False):
                        rights.append(name)
                details_parts.append(f"Hüquqlar: {', '.join(rights) or 'Standart'}")
                if getattr(new, "custom_title", None):
                    details_parts.append(f"Titul: {new.custom_title}")

            # F) Adminlikdən çıxarılma (Demote)
            elif old_st == "administrator" and new_st != "administrator":
                action = "demote"
                details_parts.append("Admin hüquqları ləğv edildi")

            # G) Admin hüquqlarının redaktəsi
            elif old_st == "administrator" and new_st == "administrator":
                action = "admin_edit"
                details_parts.append("Admin icazələri yeniləndi")

            # H) Kick (yalnız qovulma)
            elif new_st == "left" and old_st == "member" and actor.id != target.id:
                action = "kick"
                details_parts.append("Qrupdan çıxarıldı")

            # I) Qoşulma / çıxma (yalnız canlı hədəf üçün — HTML hesabatda yoxdur)
            elif old_st == "left" and new_st == "member":
                action = "join"
            elif old_st == "member" and new_st == "left" and actor.id == target.id:
                action = "left"

            if not action:
                return

            ts_ev = event.date.timestamp() if event.date else time.time()
            spawn(push_live(context, chat, actor, target, action, details_parts, ts_ev))
            if action in ("join", "left"):
                return

            record_audit_event(
                chat_id=chat.id,
                chat_title=chat.title or str(chat.id),
                admin_id=actor.id if actor.id != target.id else None,
                admin_name=actor.full_name or "",
                admin_username=actor.username or "",
                target_id=target.id,
                target_name=target.full_name or "",
                target_username=target.username or "",
                action=action,
                details=" | ".join(details_parts),
                ts=int(event.date.timestamp() if event.date else time.time())
            )
        except Exception as e:
            logger.debug(f"on_chat_member_updated xətası: {e}")

    # ── 3. /audit VƏ /hesabat ƏMRLƏRİ (HTML HESABAT İXRACI) ──
    @dp.message(Command("audit", "hesabat", "audit_report"))
    async def cmd_audit_report(msg: types.Message):
        # Yalnız creator və ya qrup adminləri istifadə edə bilər
        if msg.chat.type in ("group", "supergroup"):
            if not await is_admin_or_creator(bot, msg.chat.id, msg.from_user.id):
                return
        elif not is_creator(msg.from_user.id):
            return

        parts = (msg.text or "").split()
        subcmd = parts[1].lower() if len(parts) > 1 else ""

        if subcmd in ("off", "söndür", "sondur", "disable", "stop"):
            if msg.chat.type in ("group", "supergroup") and not is_creator(msg.from_user.id):
                await msg.reply("⚠️ Gündəlik avtomatik hesabat parametrini yalnız bot sahibi (creator) dəyişə bilər.")
                return
            set_daily_report_enabled(False)
            await msg.reply(
                "🔴 <b>23:59 Gündəlik avtomatik audit hesabatı söndürüldü.</b>\n"
                "<i>(Baza audit qeydləri aparılmağa davam edir, lakin hər gecə göndərilən avtomatik HTML hesabat dayandırıldı. "
                "İstədiyiniz vaxt <code>/audit</code> əmri ilə əl ilə hesabat ala bilərsiniz.)</i>",
                parse_mode="HTML"
            )
            return

        if subcmd in ("on", "aç", "ac", "enable", "start"):
            if msg.chat.type in ("group", "supergroup") and not is_creator(msg.from_user.id):
                await msg.reply("⚠️ Gündəlik avtomatik hesabat parametrini yalnız bot sahibi (creator) dəyişə bilər.")
                return
            set_daily_report_enabled(True)
            await msg.reply(
                "🟢 <b>23:59 Gündəlik avtomatik audit hesabatı aktivləşdirildi.</b>\n"
                "<i>Hər gecə saat 23:59-da (AZT) günün yekun HTML hesabatı şəxsi çatınıza göndəriləcək.</i>",
                parse_mode="HTML"
            )
            return

        if subcmd in ("status", "vəziyyət", "veziyyet", "info"):
            st = is_daily_report_enabled()
            st_text = "🟢 Aktiv" if st else "🔴 Söndürülüb"
            await msg.reply(
                f"📊 <b>Gündəlik Avtomatik Audit Hesabatı:</b> {st_text}\n"
                f"• Vaxt: <code>23:59 AZT</code>\n"
                f"• Dəyişmək üçün: <code>/audit on</code> və ya <code>/audit off</code>",
                parse_mode="HTML"
            )
            return

        date_arg = parts[1] if len(parts) > 1 and re.match(r"^\d{4}-\d{2}-\d{2}$", parts[1]) else None

        wait_msg = await msg.reply("⏳ <i>HTML audit hesabatı hazırlanır...</i>", parse_mode="HTML")
        try:
            # Hesabat qrupa yox, şəxsi çata (istifadəçinin / creator-un PM-inə) göndərilir
            in_group = msg.chat.type in ("group", "supergroup")
            # qrupda: yalnız HƏMİN qrupun hadisələri, istifadəçinin şəxsi çatına; PM-də (creator): hamısı
            fp, total, dest = await send_daily_audit_report(
                context, date_str=date_arg, force=True,
                target_chat=msg.from_user.id if in_group else msg.chat.id,
                chat_id=msg.chat.id if in_group else None,
            )
            with contextlib.suppress(Exception):
                await wait_msg.delete()

            if fp:
                fn = os.path.basename(fp)
                if msg.chat.type in ("group", "supergroup"):
                    await msg.reply(
                        f"🛡 <b>Audit hesabatı hazırlandı və şəxsi çatınıza göndərildi!</b>\n"
                        f"• Cəmi əməl: <b>{total}</b>\n• Fayl: <code>{fn}</code>\n"
                        f"ℹ️ <i>Qeyd: Təhlükəsizlik üçün hesabat qrupa deyil, yalnız şəxsi çata çatdırılır.</i>",
                        parse_mode="HTML"
                    )
                else:
                    await msg.reply(
                        f"✅ <b>Audit hesabatı göndərildi!</b>\n• Cəmi əməl: <b>{total}</b>\n• Fayl: <code>{fn}</code>",
                        parse_mode="HTML"
                    )
            else:
                await msg.reply("ℹ️ Qeyd olunan tarix üçün heç bir audit qeydi tapılmadı.")
        except Exception as e:
            with contextlib.suppress(Exception):
                await wait_msg.delete()
            await msg.reply(f"❌ Hesabat xətası: <code>{html.escape(str(e)[:200])}</code>")
