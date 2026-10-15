"""👥 chat_tools.py — Çat, mesajlar, moderasiya, qrup üzvləri və kəşfiyyat alətləri.

Komandalar:
  1. Mesaj Əməliyyatları:
     • .del / .d / .sil                — reply edilən mesajı silir
     • .edit / .ed <mətn>             — öz mesajını redaktə edir
     • .purge [say] / .purgeme [say]   — mesajları yığcam silir (2 san. sonra silinir)
     • .clear [all]                   — qrupun ban siyahısını təmizləyir (unban)

  2. Moderasiya & Mute:
     • .mute [müddət] / .unmute       — şəxsi bu çatda mute edir (yazan kimi silinir)
     • .mutelist                      — çatdakı mute siyahısı
     • .kickme / .leave / .skickme    — qrupdan çıxmaq (normal və səssiz)

  3. Qrup Üzvləri & İdarəetmə:
     • .members [filtr]               — üzvləri siyahılayır (.tagall, admins, bots, etc.)
     • .kick                          — istifadəçini qrupdan çıxardır
     • .add                           — istifadəçini qrupa əlavə edir
     • .promote / .promote all        — admin təyin edir (yetki paneli ilə)
     • .demote / .demote all          — adminliyi geri alır
     • .invite <qrup>                 — başqa qrupdan üzvləri cari qrupa dəvət edir

  4. İnfo & Yoxlama:
     • .info / .whois                 — istifadəçi/çat haqqında ətraflı məlumat
     • .id                            — cari çat, reply və ya hədəf ID-si
     • .inline / .noinline            — cari və ya seçilmiş qrupda inline söndürmək/açmaq
     • .fstat / .fedinfo              — Rose federasiya ban statusu yoxlaması
"""
import asyncio
import logging
import contextlib
import html
import inspect
import json
import re
import time
from html import escape

from telethon import events
from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    MessageDeleteForbiddenError,
    MessageNotModifiedError,
    PeerFloodError,
    UserAlreadyParticipantError,
    UserChannelsTooMuchError,
    UserKickedError,
    UserNotMutualContactError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.channels import EditAdminRequest, EditBannedRequest, InviteToChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest, CheckChatInviteRequest, DeleteChatUserRequest
from telethon.tl.types import Channel, ChannelParticipantsAdmins, Chat, ChatAdminRights, ChatBannedRights, ChatInviteAlready, User

from userbot.core.userbot_api import (
    Bar,
    build_info,
    check_clear_rights,
    ff,
    fmt_left,
    fmt_span,
    get_db,
    logger,
    mention,
    parse_duration,
    plain_name,
    resolve_target,
    run_clear,
    safe_edit,
)
from userbot.core.userbot_db import get_udb, run_bg
from userbot.core.userbot_api import temp_edit
from common.utilities import spawn

# ─────────────────────────────────────────────────────────────
# 1. Köməkçi Funksiyalar (Purge, Mute, Info, Invite, Members)
# ─────────────────────────────────────────────────────────────
PURGE_MAX_MESSAGES = 5000
PURGE_BATCH = 100

MUTE_NS = "mute"
MUTE_KEY = "userbot:mutes"


async def can_delete_others(client, chat) -> bool:
    if isinstance(chat, User):
        return True
    perms = await client.get_permissions(chat, "me")
    return bool(
        perms.is_creator
        or getattr(perms, "delete_messages", False)
        or (isinstance(chat, Chat) and perms.is_admin)
    )


class MuteRegistry:
    """Hər mute ayrıca sətir: (chat_id, user_id) → {until, name, at}."""

    def __init__(self):
        self.data = self._load()

    def _load(self):
        data = {}
        try:
            udb = get_udb()
            rows = udb.rec_list(MUTE_NS)
            if not rows and not (udb.kv_get(MUTE_NS, "migrated") or {}).get("at"):
                rows = self._migrate(udb)
            now = time.time()
            for cid, uid, v in rows:
                if not isinstance(v, dict):
                    continue
                if v.get("until") and now > v["until"]:
                    with contextlib.suppress(Exception):
                        udb.rec_del(MUTE_NS, cid, uid)
                    continue
                data.setdefault(int(cid), {})[int(uid)] = v
        except Exception as e:
            logger.warning(f"Mute siyahısı yüklənmədi: {e}")
        return data

    @staticmethod
    def _migrate(udb):
        rows = []
        try:
            raw = json.loads(get_db().get_setting(MUTE_KEY) or "{}")
            rows = [(int(c), str(int(u)), v) for c, us in raw.items() if isinstance(us, dict)
                    for u, v in us.items() if isinstance(v, dict)]
            if rows:
                udb.rec_replace(MUTE_NS, rows)
                logger.info(f"🗄 .mute: {len(rows)} mute userbot bazasına köçürüldü")
        except Exception as e:
            logger.warning(f".mute köçürülmədi: {e}")
            return rows
        udb.kv_set(MUTE_NS, "migrated", {"at": int(time.time())})
        return rows

    # mute siyahısı yaddaşda (self.data) saxlanır, baza yalnız davamlılıq üçündür → yazı fon thread-ində, sıra ilə
    # (vaxtı bitmiş mute mesaj gələndə silinir — SQLite commit-i event loop-u dondururdu)
    def _put(self, chat_id, uid, entry):
        run_bg(get_udb().rec_put, MUTE_NS, chat_id, str(uid), entry)

    def _del(self, chat_id, uid):
        run_bg(get_udb().rec_del, MUTE_NS, chat_id, str(uid))

    def get(self, chat_id, uid):
        e = self.data.get(chat_id, {}).get(uid)
        if e and e.get("until") and time.time() > e["until"]:
            self.remove(chat_id, uid)
            return None
        return e

    def add(self, chat_id, uid, name, seconds=None):
        entry = {"until": time.time() + seconds if seconds else 0, "name": name, "at": time.time()}
        self.data.setdefault(chat_id, {})[uid] = entry
        self._put(chat_id, uid, entry)

    def remove(self, chat_id, uid) -> bool:
        ok = self.data.get(chat_id, {}).pop(uid, None) is not None
        if chat_id in self.data and not self.data[chat_id]:
            self.data.pop(chat_id)
        if ok:
            self._del(chat_id, uid)
        return ok

    def items(self, chat_id):
        for uid in list(self.data.get(chat_id, {})):
            self.get(chat_id, uid)
        return list(self.data.get(chat_id, {}).items())


async def check_mute_rights(client, chat):
    if isinstance(chat, User):
        return None
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator or getattr(perms, "delete_messages", False) or (isinstance(chat, Chat) and perms.is_admin):
        return None
    return "⛔ Bu çatda <b>mesaj silmə</b> admin icazən yoxdur — mute işləməyəcək."


# ─────────────────────────────────────────────────────────────
# 2. Info və SangMata Köməkçiləri
# ─────────────────────────────────────────────────────────────
def estimate_reg_date(uid: int) -> str:
    if uid < 100_000_000:
        return "2013 – 2015"
    elif uid < 300_000_000:
        return "2016 – 2017"
    elif uid < 700_000_000:
        return "2018 – 2019"
    elif uid < 1_500_000_000:
        return "2020 – 2021"
    elif uid < 3_000_000_000:
        return "2021 – 2022"
    elif uid < 5_500_000_000:
        return "2022 – 2023"
    elif uid < 7_000_000_000:
        return "2023 – 2024"
    elif uid < 8_500_000_000:
        return "2024 – 2025"
    return "2025 – 2026+"


async def fetch_history_from_sangmata(client, user_id: int) -> str | None:
    try:
        async with client.conversation("@SangMata_BOT", timeout=4) as conv:
            msg = await conv.send_message(f"/search {user_id}")
            resp = await conv.get_response()
            await client.delete_messages("@SangMata_BOT", [msg.id, resp.id])
            if resp.text and "No data" not in resp.text:
                return resp.text.strip()
    except Exception as _e:
        logger.debug("udulan xəta [userbot/plugins/chat_tools.py:216]: %r", _e)
    return None


async def build_fallback_info(client, user_id: int) -> str:
    approx_date = estimate_reg_date(user_id)
    history = await fetch_history_from_sangmata(client, user_id)
    lines = [
        "👤 <b>İstifadəçi Məlumatı (Məhdud Rejim)</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🆔 <b>ID:</b> <code>{user_id}</code>",
        f"📅 <b>Təxmini Qeydiyyat:</b> <code>{approx_date}</code>",
    ]
    if history:
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📜 <b>Tapılan Arxiv Qeydləri:</b>")
        lines.append(f"<code>{escape(history[:400])}</code>")
    else:
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            "ℹ️ <i>Telegram tətbiqi ortaq əlaqə olmayan yad ID-ləri birbaşa açmağa icazə vermir. "
            "Profil və ad tarixçəsini aşağıdakı botlardan yoxlaya bilərsiniz:</i>\n"
            f"• <a href=\"https://t.me/SangMata_BOT?start={user_id}\">SangMata Bot</a>\n"
            f"• <a href=\"https://t.me/telesint_bot?start={user_id}\">Telesint Bot</a>"
        )
    return "\n".join(lines)


async def resolve_entity(client, event, target: str):
    target = target.strip("'\" \n\t")
    match_tg_link = re.match(r"(?:https?://)?t\.me/([a-zA-Z0-9_]+)", target)
    if match_tg_link:
        target = match_tg_link.group(1)
    elif target.startswith("tg://user?id="):
        target = target.replace("tg://user?id=", "")

    is_digit = target.lstrip("-").isdigit()
    entity_key = int(target) if is_digit else target
    try:
        return await client.get_entity(entity_key)
    except Exception as _e:
        logger.debug("udulan xəta [userbot/plugins/chat_tools.py:257]: %r", _e)

    if is_digit:
        target_id = int(target)
        if event.is_group or event.is_channel:
            try:
                async for member in client.iter_participants(event.chat_id, search=target):
                    if member.id == target_id:
                        return member
            except Exception as _e:
                logger.debug("udulan xəta [userbot/plugins/chat_tools.py:267]: %r", _e)
        try:
            await client.get_dialogs(limit=30)
            return await client.get_entity(target_id)
        except Exception as _e:
            logger.debug("udulan xəta [userbot/plugins/chat_tools.py:272]: %r", _e)
        return target_id
    return None


# ─────────────────────────────────────────────────────────────
# 3. Rose Federasiya Köməkçiləri (fstat)
# ─────────────────────────────────────────────────────────────
ROSE = "MissRose_bot"
ROSE_WAIT = 25
ROSE_EXTRA_WAIT = 4


async def ask_rose(client, command: str) -> tuple:
    out = []
    async with client.conversation(ROSE, timeout=ROSE_WAIT, exclusive=False) as conv:
        sent = await conv.send_message(command)
        first = await conv.get_response()
        out.append(first)
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            while True:
                more = await conv.get_response(timeout=ROSE_EXTRA_WAIT)
                out.append(more)
        with contextlib.suppress(Exception):
            fresh = await client.get_messages(ROSE, ids=[m.id for m in out])
            out = [m for m in fresh if m] or out
    return out, [sent.id] + [m.id for m in out]


# ─────────────────────────────────────────────────────────────
# 4. Invite Köməkçiləri
# ─────────────────────────────────────────────────────────────
INVITE_DELAY = 2.5


async def resolve_source_chat(client, target_str: str):
    raw = target_str.strip()
    invite_match = re.search(r"(?:joinchat/|\+)([a-zA-Z0-9_-]+)", raw)
    if invite_match:
        invite_hash = invite_match.group(1)
        try:
            res = await client(CheckChatInviteRequest(invite_hash))
            if isinstance(res, ChatInviteAlready):
                return res.chat, None
            return None, "❌ Hesabınız bu qrupda üzv deyil. Üzvləri çəkmək üçün əvvəlcə qrupa qoşulmalısınız."
        except Exception as e:
            return None, f"❌ Dəvət linki yoxlanıla bilmədi: <code>{escape(str(e))[:100]}</code>"

    cleaned = re.sub(r"^(?:https?://)?(?:t\.me/|telegram\.me/)", "", raw).strip("/").lstrip("@")
    try:
        query = int(cleaned) if (cleaned.lstrip("-").isdigit()) else cleaned
        entity = await client.get_entity(query)
        if isinstance(entity, (Channel, Chat)):
            return entity, None
        return None, "❌ Göstərilən ünvan qrup və ya kanal deyil."
    except Exception as e:
        return None, f"❌ Çat tapılmadı: <code>{escape(str(e))[:100]}</code>"


# ─────────────────────────────────────────────────────────────
# 5. Members & Promote Köməkçiləri
# ─────────────────────────────────────────────────────────────
PRESETS = {
    "full": ("🛡 Tam", None),
    "mod": ("🧹 Moderator", {"delete_messages", "ban_users", "invite_users", "pin_messages",
                            "manage_call", "manage_topics", "post_messages", "edit_messages"}),
    "min": ("📌 Minimal", {"invite_users", "pin_messages"}),
}
PRESET_ALIASES = {"full": "full", "tam": "full", "all": "full",
                  "mod": "mod", "moderator": "mod",
                  "min": "min", "minimal": "min"}
RANK_MAX = 16
BULK_DELAY = 0.6

MEM_ERRORS = {
    "ChatAdminRequiredError": "⛔ Bunun üçün admin icazən çatmır (və ya bu admini başqası təyin edib).",
    "RightForbiddenError": "⛔ Özündə olmayan yetkini verə bilməzsən.",
    "UserCreatorError": "👑 Qrupun yaradıcısının yetkiləri dəyişdirilə bilməz.",
    "AdminsTooMuchError": "📛 Admin limiti dolub (superqrupda maks. 50).",
    "UserNotParticipantError": "🚪 İstifadəçi qrupda deyil.",
    "UserPrivacyRestrictedError": "🔒 İstifadəçi qrupda deyil, gizlilik ayarları əlavə olunmağa icazə vermir.",
    "UserNotMutualContactError": "🔒 İstifadəçi qrupda deyil, gizlilik ayarları əlavə olunmağa icazə vermir.",
    "AdminRankEmojiNotAllowedError": "🏷 Titulda emoji ola bilməz.",
    "AdminRankInvalidError": f"🏷 Titul yanlışdır (maks. {RANK_MAX} simvol).",
    "BotChannelsNaError": "🤖 Bu botu admin etmək olmur.",
    "UserRestrictedError": "🔇 İstifadəçi məhdudlaşdırılıb (spam-blok) — admin edilə bilmir.",
    "UserIdInvalidError": "❌ İstifadəçi yanlışdır.",
    "UserBlockedError": "🚫 İstifadəçi bloklanıb.",
}


def mem_err_text(e) -> str:
    if isinstance(e, FloodWaitError):
        return f"⏳ Telegram limiti: {e.seconds} san. gözləyin."
    return MEM_ERRORS.get(type(e).__name__) or f"❌ <code>{escape(str(e))[:200]}</code>"


def _rights_fields() -> set:
    return set(inspect.signature(ChatAdminRights.__init__).parameters) - {"self"}


def make_rights(sel: dict) -> ChatAdminRights:
    allowed = _rights_fields()
    return ChatAdminRights(**{k: bool(v) for k, v in (sel or {}).items() if k in allowed})


def avail_keys(chat) -> list:
    if not isinstance(chat, Channel):
        return []
    if getattr(chat, "broadcast", False):
        keys = ["change_info", "post_messages", "edit_messages", "delete_messages", "invite_users",
                "manage_call", "post_stories", "edit_stories", "delete_stories", "add_admins"]
    else:
        keys = ["change_info", "delete_messages", "ban_users", "invite_users", "pin_messages"]
        if getattr(chat, "forum", False):
            keys.append("manage_topics")
        keys += ["manage_call", "add_admins", "anonymous"]
    allowed = _rights_fields()
    return [k for k in keys if k in allowed]


def my_rights(perms, keys) -> dict:
    if perms.is_creator:
        return {k: True for k in keys}
    ar = getattr(getattr(perms, "participant", None), "admin_rights", None)
    return {k: bool(getattr(ar, k, False)) for k in keys}


def preset_sel(name, keys, mine) -> dict:
    want = PRESETS[name][1]
    sel = {k: bool(mine.get(k)) and (k != "anonymous" if want is None else k in want) for k in keys}
    if keys and not any(sel.values()):
        first = next((k for k in keys if mine.get(k) and k != "anonymous"), None)
        if first:
            sel[first] = True
    return sel


async def check_rights_action(client, chat, action="kick"):
    if isinstance(chat, User):
        return "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər."
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator:
        return None
    if action in ("kick", "ban"):
        if getattr(perms, "ban_users", False) or (isinstance(chat, Chat) and perms.is_admin):
            return None
        return "⛔ Bu qrupda <b>istifadəçiləri çıxarmaq / banlamaq</b> admin icazəniz yoxdur."
    if action == "add":
        if getattr(perms, "invite_users", True):
            return None
        return "⛔ Bu qrupda <b>istifadəçi əlavə etmək</b> icazəniz yoxdur."
    return None


async def admin_check(client, chat):
    if not isinstance(chat, (Channel, Chat)):
        return None, "❌ Bu əmr yalnız <b>qruplarda / kanallarda</b> işlədilə bilər."
    perms = await client.get_permissions(chat, "me")
    if perms.is_creator:
        return perms, None
    if isinstance(chat, Chat):
        return None, "⛔ Adi qrupda adminləri yalnız <b>yaradıcı</b> təyin edə / götürə bilər."
    if not getattr(perms, "add_admins", False):
        return None, "⛔ Bu qrupda <b>admin təyin etmə</b> icazən yoxdur."
    return perms, None


# ─────────────────────────────────────────────────────────────
# Əsas Qeydiyyat (register)
# ─────────────────────────────────────────────────────────────
def box_info(text: str) -> str:
    """📋 botun /ui ayarlarına görə mətn → kod bloku."""
    try:
        from bot.plugins import compact_ui_plugin as cui
        if cui._on("ui_box"):
            return cui.boxify(text, True) or text
    except Exception as _e:
        logger.debug("udulan xəta [userbot/plugins/chat_tools.py:450]: %r", _e)
    return text


def register(ub):
    # ═════════════════════════════════════════════════════════
    # A) Mesaj Silmə & Redaktə (.del, .edit)
    # ═════════════════════════════════════════════════════════
    @ub.command(
        "del",
        pattern=r"^\.(?:del|d|sil)$",
        help=(
            "mesajı silmək",
            "Reply edilən mesajı və komandanın özünü silir.\n\n"
            "<b>İstifadə:</b>\n• Mesaja reply → <code>.del</code>\n• Mesaja reply → <code>.d</code>"
        )
    )
    async def on_del(event):
        if not event.is_reply:
            await safe_edit(event, "❌ <i>Silmək üçün bir mesaja reply edin.</i>")
            return
        reply = await event.get_reply_message()
        if not reply:
            await safe_edit(event, "❌ <i>Mesaj tapılmadı.</i>")
            return
        try:
            await reply.delete()
            await event.delete()
        except MessageDeleteForbiddenError:
            await safe_edit(event, "⛔ <b>Xəta:</b> Bu mesajı silmək hüququnuz yoxdur.")
        except Exception as e:
            logger.info(f".del xətası: {e}")
            await safe_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "edit",
        pattern=r"^\.(?:edit|ed)(?:\s+([\s\S]+))?$",
        help=(
            "öz mesajını redaktə etmək",
            "Reply etdiyiniz öz mesajınızı yeni mətnlə dəyişir.\n\n"
            "<b>İstifadə:</b>\n• Öz mesajına reply → <code>.edit yeni mətn</code>"
        )
    )
    async def on_edit(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not event.is_reply:
            await safe_edit(event, "ℹ️ Redaktə etmək üçün öz mesajınıza reply edib <code>.edit yeni mətn</code> yazın.")
            return
        if not arg:
            await safe_edit(event, "❌ <i>Yeni mətni daxil edin!</i>")
            return
        reply = await event.get_reply_message()
        if not reply or not reply.out:
            await safe_edit(event, "❌ <i>Yalnız öz yazdığınız mesajları redaktə edə bilərsiniz.</i>")
            return
        try:
            await reply.edit(arg)
            await event.delete()
        except MessageNotModifiedError:
            await safe_edit(event, "ℹ️ <i>Mesaj mətni eynidir.</i>")
        except Exception as e:
            logger.info(f".edit xətası: {e}")
            await safe_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "pin",
        pattern=r"^\.pin(?:\s+(.+))?$",
        help=(
            "mesajı bərkitmək",
            "Reply edilən mesajı qrupda bərkidir (pin).\n\n"
            "<b>İstifadə:</b>\n• Mesaja reply → <code>.pin</code> (və ya <code>.pin loud</code> bildirişlə)"
        )
    )
    async def on_pin(event):
        if not event.is_reply:
            await safe_edit(event, "ℹ️ Bərkitmək istədiyiniz mesaja reply edib <code>.pin</code> yazın.")
            return
        arg = (event.pattern_match.group(1) or "").strip().lower()
        notify = arg in ("loud", "səsli", "notify")
        reply = await event.get_reply_message()
        try:
            await event.client.pin_message(event.chat_id, reply.id, notify=notify)
            await safe_edit(event, "📌 Mesaj bərkidildi.")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await event.client.get_me()
                chat = await event.get_chat()
                record_audit_event(
                    chat_id=event.chat_id, chat_title=getattr(chat, "title", str(event.chat_id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=reply.sender_id, target_name="", target_username="",
                    action="pin", details=f"Mesaj ID: {reply.id}"
                )
        except Exception as e:
            await safe_edit(event, f"❌ <b>.pin xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "unpin",
        pattern=r"^\.unpin$",
        help=(
            "bərkidilmiş mesajı açmaq",
            "Reply edilən mesajın (və ya sonuncu pin-in) bərkidilməsini ləğv edir.\n\n"
            "<b>İstifadə:</b>\n• Mesaja reply → <code>.unpin</code>"
        )
    )
    async def on_unpin(event):
        mid = (await event.get_reply_message()).id if event.is_reply else None
        try:
            await event.client.unpin_message(event.chat_id, mid)
            await safe_edit(event, "📍 Bərkitmə ləğv edildi.")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await event.client.get_me()
                chat = await event.get_chat()
                record_audit_event(
                    chat_id=event.chat_id, chat_title=getattr(chat, "title", str(event.chat_id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=None, target_name="", target_username="",
                    action="unpin", details=f"Mesaj ID: {mid or 'Sonuncusu'}"
                )
        except Exception as e:
            await safe_edit(event, f"❌ <b>.unpin xətası:</b> <code>{escape(str(e))[:200]}</code>")

    # ═════════════════════════════════════════════════════════
    # B) Toplu Mesaj Silmə (.purge, .purgeme)
    # ═════════════════════════════════════════════════════════
    async def handle_purge(event, force_mine: bool = False):
        client = event.client
        raw_args = (event.pattern_match.group(1) or "").strip().split()
        mine_only = force_mine or any(arg.lower() == "me" for arg in raw_args)
        count = None
        for arg in raw_args:
            if arg.isdigit():
                count = int(arg)
                break

        if count is None and (not event.is_reply or not event.reply_to_msg_id):
            cmd = ".purgeme" if force_mine else ".purge"
            await temp_edit(event, f"ℹ️ <b>İstifadə:</b> <code>{cmd} 50</code> və ya mesaja reply edib <code>{cmd}</code> yazın.", delay=3)
            return

        if count is not None and count > PURGE_MAX_MESSAGES:
            await temp_edit(event, f"❌ <b>Limit aşıldı:</b> Maksimum {PURGE_MAX_MESSAGES} mesaj.", delay=2)
            return

        chat = await event.get_chat()
        start_id = event.reply_to_msg_id
        cmd_id = event.id
        chat_id = event.chat_id

        if not mine_only:
            try:
                others_ok = await can_delete_others(client, chat)
            except Exception:
                others_ok = False
            if not others_ok:
                mine_only = True

        await safe_edit(event, "🧹 <i>Mesajlar toplanır...</i>")
        ids = []
        total_seen = 0
        try:
            if count is not None:
                async for m in client.iter_messages(chat_id, max_id=cmd_id, limit=None if mine_only else count):
                    total_seen += 1
                    if not mine_only or getattr(m, "out", False):
                        ids.append(m.id)
                        if len(ids) >= count:
                            break
                    if total_seen >= PURGE_MAX_MESSAGES:
                        break
            else:
                async for m in client.iter_messages(chat_id, min_id=start_id - 1, max_id=cmd_id):
                    total_seen += 1
                    if not mine_only or getattr(m, "out", False):
                        ids.append(m.id)
                    if total_seen > PURGE_MAX_MESSAGES:
                        await temp_edit(event, f"⚠️ Aralıq çox böyükdür (maks. {PURGE_MAX_MESSAGES} mesaj).", delay=2)
                        return

            to_delete = [i for i in ids if i != cmd_id]
            if not to_delete:
                await temp_edit(event, "ℹ️ <i>Silinəcək mesaj tapılmadı.</i>", delay=2)
                return

            total = len(to_delete)
            deleted = 0
            for i in range(0, total, PURGE_BATCH):
                chunk = to_delete[i:i + PURGE_BATCH]
                while True:
                    try:
                        await client.delete_messages(chat_id, chunk, revoke=True)
                        deleted += len(chunk)
                        break
                    except FloodWaitError as e:
                        await safe_edit(event, f"⏳ <i>Telegram limiti: {e.seconds} san. gözlənilir...</i>")
                        await asyncio.sleep(e.seconds + 1)
                    except Exception as e:
                        logger.info(f".purge silmə xətası: {e}")
                        break
                if total > PURGE_BATCH and (i + PURGE_BATCH) < total:
                    await safe_edit(event, f"🧹 <i>Silinir... ({deleted}/{total})</i>")
                await asyncio.sleep(0.2)

            note = " (yalnız öz mesajların)" if mine_only and not force_mine else ""
            await temp_edit(event, f"✅ <b>{deleted} mesaj silindi{note}.</b>", delay=2)
        except Exception as e:
            logger.info(f".purge xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:150]}</code>", delay=3)

    @ub.command("purge", pattern=r"^\.purge(?:\s+(.+))?$",
                help=("mesajları yığcam silmək",
                      "Mesajları sayla və ya reply ilə silir (2 san. sonra silinir).\n\n"
                      "<b>İstifadə:</b>\n• <code>.purge 50</code>\n• reply → <code>.purge</code>"))
    async def on_purge(event):
        await handle_purge(event, force_mine=False)

    @ub.command("purgeme", pattern=r"^\.(?:purgeme|pme)(?:\s+(.+))?$",
                help=("yalnız öz mesajlarını silmək",
                      "Cari çatdan yalnız sizin göndərdiyiniz mesajları silir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.purgeme 50</code>\n• reply → <code>.purgeme</code>"))
    async def on_purgeme(event):
        await handle_purge(event, force_mine=True)

    # ═════════════════════════════════════════════════════════
    # C) Qrupun Banlarını Təmizləmək (.clear)
    # ═════════════════════════════════════════════════════════
    @ub.command("clear", pattern=r"^\.clear(?:\s+(all))?$",
                help=("qrupun banlarını təmizlə",
                      "Superqrupun ban siyahısındakı hər kəsi unban edir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.clear</code> — banlar\n• <code>.clear all</code> — banlar + məhdudiyyətlər"))
    async def on_clear(event):
        client = event.client
        mode = (event.pattern_match.group(1) or "").strip().lower()
        chat = await event.get_chat()
        err = await check_clear_rights(client, chat)
        if err:
            await safe_edit(event, err)
            return

        async def report(text, final):
            await safe_edit(event, text + ("" if final else "\n<i>Dayandırmaq: .stop</i>"))

        async def runner():
            try:
                await run_clear(client, chat, mode, report)
            except asyncio.CancelledError:
                await safe_edit(event, "⏹ <b>.clear</b> dayandırıldı.")
                raise
            except Exception as e:
                logger.info(f".clear xətası: {e}")
                await safe_edit(event, f"❌ <b>.clear:</b> <code>{escape(str(e))[:200]}</code>")

        ub.track(event.chat_id, "clear", asyncio.create_task(runner()))

    # ═════════════════════════════════════════════════════════
    # D) Mute Sistemi (.mute, .unmute, .mutelist)
    # ═════════════════════════════════════════════════════════
    reg_mute = MuteRegistry()
    ub.state["mute"] = reg_mute
    ub.state["mute_check_rights"] = check_mute_rights

    @ub.command("mute", pattern=r"^\.mute(?:\s+(.+))?$",
                help=("yazan kimi mesajı silinsin",
                      "Şəxs bu çatda nə yazsa, mesajı dərhal silinir.\n\n"
                      "<b>İstifadə:</b>\n• reply → <code>.mute</code>\n• <code>.mute @username 1h</code>"))
    async def on_mute(event):
        client = event.client
        args = (event.pattern_match.group(1) or "").split()
        try:
            ent, rest = await resolve_target(event, args)
            if ent is None:
                await safe_edit(event, "ℹ️ Kimi? Mesajına <b>reply</b> et və ya <code>.mute @username [10m|2h|1d]</code>")
                return
            me = await client.get_me()
            if ent.id == me.id:
                await safe_edit(event, "🙃 Özünü mute edə bilməzsən.")
                return
            chat = await event.get_chat()
            err = await check_mute_rights(client, chat)
            if err:
                await safe_edit(event, err)
                return
            seconds = None
            if rest:
                seconds = parse_duration(rest[0])
                if seconds is None:
                    await safe_edit(event, "ℹ️ Vaxt formatı: <code>30m</code>, <code>2h</code>, <code>1d</code>")
                    return
            reg_mute.add(event.chat_id, ent.id, plain_name(ent), seconds)
            await safe_edit(event, f"🔇 {mention(ent)} mute edildi — "
                                   f"{'həmişəlik' if not seconds else fmt_span(seconds)}.\n"
                                   f"<i>Bu çatda yazdığı hər mesaj silinəcək. Götürmək: .unmute</i>")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                record_audit_event(
                    chat_id=event.chat_id, chat_title=getattr(chat, "title", str(event.chat_id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="mute", details=f"Müddət: {'həmişəlik' if not seconds else fmt_span(seconds)}"
                )
        except Exception as e:
            await safe_edit(event, f"❌ <b>.mute:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command("unmute", pattern=r"^\.unmute(?:\s+(.+))?$",
                help=("mute-u götür", "<b>İstifadə:</b>\n• reply → <code>.unmute</code>\n• <code>.unmute @username</code>"))
    async def on_unmute(event):
        args = (event.pattern_match.group(1) or "").split()
        try:
            ent, _ = await resolve_target(event, args)
            if ent is None:
                await safe_edit(event, "ℹ️ Kimi? Reply et və ya <code>.unmute @username</code>")
                return
            if reg_mute.remove(event.chat_id, ent.id):
                await safe_edit(event, f"🔊 {mention(ent)} artıq mute deyil.")
                with contextlib.suppress(Exception):
                    from common.audit import record_audit_event
                    me = await event.client.get_me()
                    chat = await event.get_chat()
                    record_audit_event(
                        chat_id=event.chat_id, chat_title=getattr(chat, "title", str(event.chat_id)),
                        admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                        target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                        action="unmute", details="Mute götürüldü"
                    )
            else:
                await safe_edit(event, f"ℹ️ {mention(ent)} bu çatda mute edilməyib.")
        except Exception as e:
            await safe_edit(event, f"❌ <b>.unmute:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command("mutelist", help=("bu çatdakı mute edilənlər", "Mute edilənləri və qalan vaxtı göstərir."))
    async def on_mutelist(event):
        items = reg_mute.items(event.chat_id)
        if not items:
            await safe_edit(event, "🔊 Bu çatda mute edilən yoxdur.")
            return
        L = [f"🔇 <b>Mute edilənlər</b> ({len(items)}):"]
        L += [f'• <a href="tg://user?id={uid}">{escape(e.get("name") or str(uid))}</a> — '
              f"<i>{fmt_left(e.get('until'))}</i>" for uid, e in items]
        await safe_edit(event, "\n".join(L))

    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_mute(event):
        if event.out or not reg_mute.data.get(event.chat_id):
            return
        uid = event.sender_id
        if uid is None or not reg_mute.get(event.chat_id, uid):
            return
        try:
            await event.delete()
        except FloodWaitError as e:
            # Telethon ≤120 san.-ni özü gözləyib; daha uzun FloodWait-də handler saatlarla asılı qalmasın
            if e.seconds > 300:
                logger.warning(f"Mute: FloodWait {e.seconds} san. — mesaj silinmədi ({event.chat_id}/{uid})")
                return
            await asyncio.sleep(e.seconds + 1)
            with contextlib.suppress(Exception):
                await event.delete()
        except Exception as e:
            logger.info(f"Mute: mesaj silinmədi ({event.chat_id}/{uid}): {e}")

    # ═════════════════════════════════════════════════════════
    # E) Qrupdan Çıxmaq (.kickme, .leave, .skickme)
    # ═════════════════════════════════════════════════════════
    @ub.command(
        "kickme",
        pattern=r"^\.(?:(s)?(?:kickme|leave)|(?:kickme|leave)(?:\s+(-s|s|silent|sessiz|səssiz))?)$",
        help=(
            "qrupdan çıxmaq (normal və səssiz)",
            "Cari qrup və ya kanaldan çıxır.\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.kickme</code> / <code>.leave</code> — mesaj yazaraq çıxır\n"
            "• <code>.skickme</code> / <code>.sleave</code> — heç bir iz qoymadan tam səssiz çıxır"
        )
    )
    async def on_kickme(event):
        if event.is_private:
            await safe_edit(event, "❌ <i>Bu komanda yalnız qrup və ya kanallarda işləyir.</i>")
            return
        is_silent = bool(event.pattern_match.group(1)) or bool(event.pattern_match.group(2))
        client = event.client
        chat_id = event.chat_id
        if is_silent:
            with contextlib.suppress(Exception):
                await event.delete()
        else:
            await safe_edit(event, "👋 <i>Sağ olun, mən getdim...</i>")
        try:
            if event.is_channel:
                await client(LeaveChannelRequest(chat_id))
            else:
                await client(DeleteChatUserRequest(chat_id=chat_id, user_id="me"))
        except Exception as e:
            logger.info(f".kickme xətası: {e}")
            if not is_silent:
                await safe_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>")

    # ═════════════════════════════════════════════════════════
    # F) İstifadəçi Məlumatı & ID (.info, .id)
    # ═════════════════════════════════════════════════════════
    @ub.command("info", pattern=r"^\.(?:info|whois|userinfo|uinfo)(?:\s+(.+))?$",
                help=("şəxs haqqında məlumat",
                      "Ad, username, ID, bio, status, ortaq qruplar, profil şəkli.\n\n"
                      "<b>İstifadə:</b>\n• reply → <code>.whois</code>\n• <code>.whois @username</code>"))
    async def on_info(event):
        client = event.client
        arg = (event.pattern_match.group(1) or "").strip()
        await safe_edit(event, "🔎 <i>Məlumat toplanır...</i>")
        try:
            ent = None
            if event.is_reply:
                reply = await event.get_reply_message()
                if reply:
                    ent = await reply.get_sender()
                    if ent is None and reply.forward:
                        if reply.forward.sender_id:
                            with contextlib.suppress(Exception):
                                ent = await client.get_entity(reply.forward.sender_id)
                        elif reply.forward.chat:
                            ent = reply.forward.chat
                    if ent is None and reply.sender_id:
                        with contextlib.suppress(Exception):
                            ent = await client.get_entity(reply.sender_id)
            elif arg:
                ent = await resolve_entity(client, event, arg)
            elif event.is_private:
                ent = await event.get_chat()
            else:
                ent = await client.get_me()

            if isinstance(ent, int):
                fallback_text = await build_fallback_info(client, ent)
                await safe_edit(event, box_info(fallback_text))
                return
            if ent is None:
                await safe_edit(event, "❌ <b>İstifadəçi tapılmadı.</b>")
                return
            chat = await event.get_chat() if event.is_group else None
            info_text = await build_info(client, ent, chat)
            await safe_edit(event, box_info(info_text))
        except Exception as e:
            logger.info(f".info xətası: {e}")
            await safe_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:250]}</code>")

    @ub.command("id", pattern=r"^\.id(?:\s+(.+))?$",
                help=("ID çıxarır", "Cari çat, reply edilən və ya arqumentdəki istifadəçi ID-si."))
    async def on_id(event):
        client = event.client
        arg = (event.pattern_match.group(1) or "").strip()
        try:
            if event.is_reply:
                reply = await event.get_reply_message()
                sender_id = reply.sender_id or "Naməlum"
                text = (
                    f"👤 <b>İstifadəçi ID:</b> <code>{sender_id}</code>\n"
                    f"💬 <b>Çat ID:</b> <code>{event.chat_id}</code>\n"
                    f"📩 <b>Mesaj ID:</b> <code>{reply.id}</code>"
                )
            elif arg:
                res = await resolve_entity(client, event, arg)
                uid = res if isinstance(res, int) else getattr(res, "id", "Naməlum")
                text = f"🆔 <b>Hədəf ID:</b> <code>{uid}</code>"
            else:
                text = (
                    f"💬 <b>Cari çat ID:</b> <code>{event.chat_id}</code>\n"
                    f"👤 <b>Sizin ID:</b> <code>{event.sender_id}</code>"
                )
            await safe_edit(event, text)
        except Exception as e:
            await safe_edit(event, f"❌ <b>.id:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "inline",
        pattern=r"^\.(?:inline|noinline)(?:\s+(.+))?$",
        help=("qrupda inline rejimi idarə etmək",
              "Bu və ya göstərilən çatda userbot inline kartlarını söndürüb-yandırır.\n\n"
              "<b>İstifadə:</b>\n"
              "• <code>.inline off</code> və ya <code>.noinline</code> — cari çatda inline söndürülür (sadə mətn rejimi)\n"
              "• <code>.inline on</code> — cari çatda inline aktiv edilir\n"
              "• <code>.inline status</code> — cari çatın inline statusu\n"
              "• <code>.inline list</code> — söndürülmüş bütün çatların siyahısı\n"
              "• <code>.inline off &lt;chat_id&gt;</code> / <code>.inline on &lt;chat_id&gt;</code> — ID ilə idarəetmə"),
        category="acc",
    )
    async def on_inline(event):
        raw_cmd = (event.raw_text or "").split()[0].lower()
        arg = (event.pattern_match.group(1) or "").strip()
        chat_id = event.chat_id

        if "noinline" in raw_cmd and not arg:
            arg = "off"

        tokens = arg.split()
        subcmd = tokens[0].lower() if tokens else "status"

        target_chat_id = chat_id
        if len(tokens) > 1:
            with contextlib.suppress(ValueError):
                target_chat_id = int(tokens[1])

        from common.database import (
            get_userbot_inline_disabled_chats,
            is_userbot_inline_disabled,
            set_userbot_inline_disabled,
        )

        if subcmd in ("list", "siyahı", "all"):
            chats = get_userbot_inline_disabled_chats()
            if not chats:
                body = [("İnline Statusu", "Bütün çatlarda aktivdir"), ("Söndürülən", "0 çat")]
                await safe_edit(event, ff(ub.title("inline"), body, footer="Söndürülmüş çat yoxdur"))
                return
            body = [("Cəmi Söndürülən", f"{len(chats)} çat"), "# Çat ID-ləri"]
            for cid in chats[:20]:
                body.append(("Çat ID", str(cid)))
            if len(chats) > 20:
                body.append(("Digər", f"+{len(chats) - 20} çat"))
            await safe_edit(event, ff(ub.title("inline"), body, footer=".inline on <chat_id> ilə aça bilərsiniz"))
            return

        if subcmd in ("off", "söndür", "disable", "stop", "0"):
            set_userbot_inline_disabled(target_chat_id, True)
            body = [
                ("Status", "🔴 SÖNDÜRÜLDÜ"),
                ("Çat ID", str(target_chat_id)),
                ("Rejim", "Yalnız mətn / Fastfetch"),
                ("İnline kartlar", "Deaktiv"),
            ]
            await safe_edit(event, ff(ub.title("inline"), body, footer="Açmaq üçün: .inline on"))
            return

        if subcmd in ("on", "aç", "enable", "start", "1"):
            set_userbot_inline_disabled(target_chat_id, False)
            body = [
                ("Status", "🟢 AKTİV EDİLDİ"),
                ("Çat ID", str(target_chat_id)),
                ("Rejim", "İnline kartlar & menyu"),
                ("İnline kartlar", "Aktiv"),
            ]
            await safe_edit(event, ff(ub.title("inline"), body, footer="Söndürmək üçün: .inline off"))
            return

        # Status yoxlaması
        is_dis = is_userbot_inline_disabled(target_chat_id)
        st_text = "🔴 Söndürülüb (Mətn rejimi)" if is_dis else "🟢 Aktivdir (İnline rejimi)"
        body = [
            ("Status", st_text),
            ("Çat ID", str(target_chat_id)),
            ("Komandalar", ".inline on · .inline off · .inline list"),
        ]
        await safe_edit(event, ff(ub.title("inline"), body, footer=".inline [on|off|status|list]"))

    # ═════════════════════════════════════════════════════════
    # G) Rose Federasiya Yoxlaması (.fstat, .fedinfo)
    # ═════════════════════════════════════════════════════════
    async def resolve_rose_target(event, arg: str) -> str:
        if arg:
            return arg.strip()
        if event.is_reply:
            r = await event.get_reply_message()
            if r and r.sender_id:
                return str(r.sender_id)
        return ""

    async def run_rose(event, command: str, title: str):
        await safe_edit(event, f"🛡 <i>Rose yoxlayır: <code>{html.escape(command)}</code>...</i>")
        try:
            msgs, cleanup = await ask_rose(event.client, command)
        except asyncio.TimeoutError:
            await safe_edit(event, "⏱ <b>Rose cavab vermədi</b> (Rose bot işləmir və ya bloklanıb).")
            return
        except Exception as e:
            logger.info(f".fstat xətası: {e}")
            await safe_edit(event, f"❌ <b>Xəta:</b> <code>{html.escape(str(e))[:200]}</code>")
            return

        async def sweep():
            with contextlib.suppress(Exception):
                await event.client.delete_messages(ROSE, cleanup)
        spawn(sweep())

        if not msgs:
            await safe_edit(event, "○ <i>Cavab yoxdur.</i>")
            return

        first = msgs[0]
        text = html.escape((first.text or first.raw_text or "").strip())[:3500]
        extra_doc = None
        for m in msgs:
            if getattr(m, "document", None):
                extra_doc = m
                break

        head = f"🛡 <b>{html.escape(title)}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        if not text and not extra_doc:
            await safe_edit(event, head + "○ <i>Məlumat yoxdur.</i>")
            return

        from common.menu import wrap_ui_text
        await safe_edit(event, head + (wrap_ui_text(text) if text else "<i>Fayl göndərildi ↓</i>"))
        if extra_doc:
            with contextlib.suppress(Exception):
                await event.client.send_file(event.chat_id, extra_doc.media, reply_to=event.id,
                                             caption=f"📎 {html.escape(title)} — tam siyahı")

    @ub.command("fstat", pattern=r"^\.fstat(?:\s+(\S+))?(?:\s+(\S+))?$",
                help=("Rose federasiya ban statusu",
                      "İstifadəçinin Rose federasiyalarındakı ban vəziyyətini yoxlayır.\n\n"
                      "<b>İstifadə:</b>\n• <code>.fstat</code> (özün və ya reply)\n"
                      "• <code>.fstat @user</code>\n• <code>.fstat @user fed_id</code>"))
    async def on_fstat(event):
        target = await resolve_rose_target(event, event.pattern_match.group(1) or "")
        fed = (event.pattern_match.group(2) or "").strip()
        cmd = "/fstat"
        if target:
            cmd += f" {target}"
        if fed:
            cmd += f" {fed}"
        await run_rose(event, cmd, f"Federasiya statusu ({target or 'özünüz'})")

    @ub.command("fedinfo", pattern=r"^\.fedinfo(?:\s+(\S+))?$",
                help=("Rose federasiya məlumatı", "Federasiya haqqında məlumatı çıxarır."))
    async def on_fedinfo(event):
        arg = (event.pattern_match.group(1) or "").strip()
        cmd = f"/fedinfo {arg}".strip()
        await run_rose(event, cmd, f"Federasiya: {arg or 'cari'}")

    # ═════════════════════════════════════════════════════════
    # H) Üzv Dəvəti (.invite)
    # ═════════════════════════════════════════════════════════
    @ub.command("invite", pattern=r"^\.invite(?:\s+(\S+))?$",
                help=("üzvləri başqa qrupdan çək",
                      "Göstərilən qrupdakı üzvləri cari qrupa dəvət edir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.invite @qrup_linki</code>\n• Dayandırmaq: <code>.stop</code>"))
    async def on_invite(event):
        if event.is_private:
            await safe_edit(event, "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər.")
            return

        client = event.client
        cur_chat = await event.get_chat()
        target_arg = event.pattern_match.group(1)
        if not target_arg:
            await safe_edit(event, "ℹ️ <b>Qrup ünvanını qeyd edin:</b>\n<code>.invite @qrup_username</code>")
            return

        source_chat, err = await resolve_source_chat(client, target_arg)
        if err:
            await safe_edit(event, err)
            return

        if source_chat.id == cur_chat.id:
            await safe_edit(event, "❌ Mənbə qrup cari qrupla eynidir!")
            return

        await safe_edit(event, f"🔍 <b>{escape(source_chat.title)}</b> qrupundan üzvlər toplanır...")
        try:
            target_members = []
            async for user in client.iter_participants(source_chat):
                if not getattr(user, "bot", False) and not getattr(user, "is_self", False) and not getattr(user, "deleted", False):
                    target_members.append(user)
        except Exception as e:
            logger.info(f".invite üzv toplama xətası: {e}")
            await safe_edit(event, f"❌ Üzvlər oxuna bilmədi: <code>{escape(str(e))[:100]}</code>")
            return

        if not target_members:
            await safe_edit(event, "ℹ️ Dəvət ediləcək uyğun üzv tapılmadı.")
            return

        total = len(target_members)
        async def worker():                      # ⏹ .stop — ub.track() tapşırığı ləğv edir
            success = 0
            failed = 0
            for i, user in enumerate(target_members, 1):
                try:
                    if isinstance(cur_chat, Channel):
                        await client(InviteToChannelRequest(channel=cur_chat, users=[user]))
                    elif isinstance(cur_chat, Chat):
                        await client(AddChatUserRequest(chat_id=cur_chat.id, user_id=user.id, fwd_limit=0))
                    success += 1
                except UserAlreadyParticipantError:
                    pass
                except (UserPrivacyRestrictedError, UserNotMutualContactError, UserChannelsTooMuchError, UserKickedError):
                    failed += 1
                except PeerFloodError:
                    await safe_edit(event, f"⛔ <b>Telegram Limiti (PeerFlood):</b> Əməliyyat dayandırıldı.\n"
                                           f"✅ Uğurlu: {success} · ❌ Xəta: {failed}")
                    return
                except FloodWaitError as e:
                    await safe_edit(event, f"⏳ <i>Telegram limiti: {e.seconds} san. gözlənilir...</i>")
                    await asyncio.sleep(e.seconds + 1)
                except ChatAdminRequiredError:
                    await safe_edit(event, "⛔ Cari qrupda üzv əlavə etmək icazəniz yoxdur.")
                    return
                except Exception:
                    failed += 1

                if i % 5 == 0 or i == total:
                    pct = int(i / total * 100)
                    body = [("İrəliləyiş", Bar(f"{i}/{total}", pct, f"{pct}%")),
                            ("Uğurlu", f"● {success}"), ("Qoşulmayan", f"○ {failed}")]
                    await safe_edit(event, ff(ub.title("invite"), body, footer="dayandırmaq üçün: .stop"))
                await asyncio.sleep(INVITE_DELAY)

            await safe_edit(event, f"✅ <b>Dəvət tamamlandı!</b>\n"
                                   f"• Əlavə edildi: <b>{success}</b>\n• Qoşulmayan: <b>{failed}</b>\n• Cəmi: <b>{total}</b>")

        task = asyncio.create_task(worker())
        ub.track(event.chat_id, "invite", task)

    # ═════════════════════════════════════════════════════════
    # I) Qrup Üzvləri İdarəetməsi (.members, .kick, .add, .promote, .demote)
    # ═════════════════════════════════════════════════════════
    @ub.command(
        "kick",
        pattern=r"^\.kick(?:\s+(.+))?$",
        help=(
            "istifadəçini qrupdan çıxartmaq",
            "İstifadəçini qrupdan çıxardır (banlamır).\n\n"
            "<b>İstifadə:</b>\n"
            "• Mesaja reply → <code>.kick</code>\n"
            "• <code>.kick @username</code> / <code>.kick 123456789</code>",
        )
    )
    async def on_kick(event):
        client = event.client
        chat = await event.get_chat()
        err = await check_rights_action(client, chat, action="kick")
        if err:
            await safe_edit(event, err)
            return

        raw_arg = (event.pattern_match.group(1) or "").strip()
        args = [raw_arg] if raw_arg else []
        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimi çıxarmaq istəyirsiniz? Reply edin və ya <code>.kick @username</code> yazın.")
            return

        if getattr(ent, "is_self", False):
            await safe_edit(event, "🙃 Özünüzü qrupdan çıxara bilməzsiniz (özünüz çıxmaq üçün: <code>.kickme</code>).")
            return

        try:
            await client.kick_participant(chat, ent)
            await safe_edit(event, f"👢 {mention(ent)} qrupdan çıxarıldı.")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await client.get_me()
                record_audit_event(
                    chat_id=chat.id, chat_title=getattr(chat, "title", str(chat.id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="kick", details="Qrupdan çıxarıldı"
                )
        except Exception as e:
            logger.info(f".kick xətası: {e}")
            await safe_edit(event, f"❌ <b>.kick xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "ban",
        pattern=r"^\.ban(?:\s+(.+))?$",
        help=(
            "istifadəçini banlamaq",
            "İstifadəçini qrupdan banlayır.\n\n"
            "<b>İstifadə:</b>\n"
            "• Mesaja reply → <code>.ban</code>\n"
            "• Mesaja reply → <code>.ban [vaxt] [səbəb]</code> (məs. <code>.ban 1d Spam</code>)\n"
            "• <code>.ban @username [vaxt] [səbəb]</code>",
        )
    )
    async def on_ban(event):
        client = event.client
        chat = await event.get_chat()
        err = await check_rights_action(client, chat, action="ban")
        if err:
            await safe_edit(event, err)
            return

        raw_arg = (event.pattern_match.group(1) or "").strip()
        args = raw_arg.split() if raw_arg else []
        try:
            ent, rest = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimi banlamaq istəyirsiniz? Reply edin və ya <code>.ban @username</code> yazın.")
            return

        if getattr(ent, "is_self", False):
            await safe_edit(event, "🙃 Özünüzü banlaya bilməzsiniz.")
            return

        seconds = None
        reason = ""
        if rest:
            seconds = parse_duration(rest[0])
            if seconds is not None:
                reason = " ".join(rest[1:])
            else:
                reason = " ".join(rest)

        until_date = None
        if seconds:
            from datetime import datetime, timedelta, timezone as dt_tz
            until_date = datetime.now(dt_tz.utc) + timedelta(seconds=seconds)

        rights = ChatBannedRights(
            until_date=until_date,
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True
        )

        try:
            if isinstance(chat, Channel):
                await client(EditBannedRequest(channel=chat, user_id=ent, banned_rights=rights))
            elif isinstance(chat, Chat):
                await client.kick_participant(chat, ent)
            
            dur_str = f" ({fmt_span(seconds)})" if seconds else ""
            reason_str = f" | Səbəb: {reason}" if reason else ""
            await safe_edit(event, f"🚫 {mention(ent)} qrupdan banlandı{dur_str}.{reason_str}")

            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await client.get_me()
                record_audit_event(
                    chat_id=chat.id, chat_title=getattr(chat, "title", str(chat.id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="ban", details=f"Müddət: {fmt_span(seconds) if seconds else 'Həmişəlik'}{reason_str}"
                )
        except Exception as e:
            logger.info(f".ban xətası: {e}")
            await safe_edit(event, f"❌ <b>.ban xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "unban",
        pattern=r"^\.unban(?:\s+(.+))?$",
        help=(
            "istifadəçinin banını qaldırmaq",
            "İstifadəçinin qrupdakı banını ləğv edir.\n\n"
            "<b>İstifadə:</b>\n"
            "• Mesaja reply → <code>.unban</code>\n"
            "• <code>.unban @username</code> / <code>.unban 123456789</code>",
        )
    )
    async def on_unban(event):
        client = event.client
        chat = await event.get_chat()
        err = await check_rights_action(client, chat, action="ban")
        if err:
            await safe_edit(event, err)
            return

        raw_arg = (event.pattern_match.group(1) or "").strip()
        args = [raw_arg] if raw_arg else []
        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimin banını qaldırmaq istəyirsiniz? Reply edin və ya <code>.unban @username</code> yazın.")
            return

        rights = ChatBannedRights(until_date=None, view_messages=False)
        try:
            if isinstance(chat, Channel):
                await client(EditBannedRequest(channel=chat, user_id=ent, banned_rights=rights))
            await safe_edit(event, f"🔓 {mention(ent)} istifadəçisinin banı qaldırıldı.")

            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await client.get_me()
                record_audit_event(
                    chat_id=chat.id, chat_title=getattr(chat, "title", str(chat.id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="unban", details="Ban götürüldü"
                )
        except Exception as e:
            logger.info(f".unban xətası: {e}")
            await safe_edit(event, f"❌ <b>.unban xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "add",
        pattern=r"^\.add(?:\s+(.+))?$",
        help=(
            "istifadəçini qrupa əlavə etmək",
            "İstifadəçini qrupa əlavə edir / dəvət edir.\n\n"
            "<b>İstifadə:</b>\n• reply → <code>.add</code>\n• <code>.add @username</code>",
        )
    )
    async def on_add(event):
        client = event.client
        chat = await event.get_chat()
        err = await check_rights_action(client, chat, action="add")
        if err:
            await safe_edit(event, err)
            return

        raw_arg = (event.pattern_match.group(1) or "").strip()
        args = [raw_arg] if raw_arg else []
        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimi əlavə etmək istəyirsiniz? Reply edin və ya <code>.add @username</code> yazın.")
            return

        try:
            if isinstance(chat, Channel):
                await client(InviteToChannelRequest(channel=chat, users=[ent]))
            elif isinstance(chat, Chat):
                await client(AddChatUserRequest(chat_id=chat.id, user_id=ent.id, fwd_limit=0))
            await safe_edit(event, f"✅ {mention(ent)} qrupa əlavə edildi.")
        except UserAlreadyParticipantError:
            await safe_edit(event, f"ℹ️ {mention(ent)} artıq bu qrupdadır.")
        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            await safe_edit(event, f"🔒 {mention(ent)} gizlilik ayarları qrupa əlavə olunmağa icazə vermir.")
        except FloodWaitError as fe:
            await safe_edit(event, f"⏳ Telegram limiti: {fe.seconds} saniyə gözləyin.")
        except Exception as e:
            logger.info(f".add xətası: {e}")
            await safe_edit(event, f"❌ <b>.add xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command(
        "promote",
        pattern=r"^\.promote(?:\s+(.+))?$",
        help=("istifadəçini admin etmək",
              "Admin hüquqları təyin edir.\n\n"
              "<b>İstifadə:</b>\n• reply → <code>.promote</code>\n• <code>.promote @user mod</code>\n• <code>.promote all</code>"))
    async def on_promote(event):
        client = event.client
        chat = await event.get_chat()
        perms, err = await admin_check(client, chat)
        if err:
            await safe_edit(event, err)
            return

        raw = (event.pattern_match.group(1) or "").strip()
        args = raw.split() if raw else []

        # .promote all
        if args and args[0].lower() in ("all", "hərkəs", "hamı"):
            keys = avail_keys(chat)
            mine = my_rights(perms, keys)
            preset_name = PRESET_ALIASES.get(args[1].lower(), "mod") if len(args) > 1 else "mod"
            sel = preset_sel(preset_name, keys, mine)
            rights = make_rights(sel)
            await safe_edit(event, "⏳ <i>Qrup üzvləri yoxlanılır...</i>")
            done, fail = 0, 0
            async for u in client.iter_participants(chat):
                if u.bot or u.is_self or u.deleted:
                    continue
                try:
                    await client(EditAdminRequest(channel=chat, user_id=u, admin_rights=rights, rank=""))
                    done += 1
                    await asyncio.sleep(BULK_DELAY)
                except Exception:
                    fail += 1
            await safe_edit(event, f"✅ <b>Bütün üzvlər admin edildi:</b> {done} uğurlu, {fail} xəta.")
            return

        try:
            ent, rest = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimi admin etmək istəyirsiniz? Reply edin və ya <code>.promote @username</code> yazın.")
            return

        keys = avail_keys(chat)
        mine = my_rights(perms, keys)
        preset_name = PRESET_ALIASES.get(rest[0].lower(), "mod") if rest else "mod"
        sel = preset_sel(preset_name, keys, mine)
        rights = make_rights(sel)

        try:
            await client(EditAdminRequest(channel=chat, user_id=ent, admin_rights=rights, rank="Admin"))
            await safe_edit(event, f"👮 {mention(ent)} uğurla admin edildi ({PRESETS[preset_name][0]}).")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await client.get_me()
                record_audit_event(
                    chat_id=chat.id, chat_title=getattr(chat, "title", str(chat.id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="promote", details=f"Admin edildi ({PRESETS[preset_name][0]})"
                )
        except Exception as e:
            logger.info(f".promote xətası: {e}")
            await safe_edit(event, mem_err_text(e))

    @ub.command(
        "demote",
        pattern=r"^\.demote(?:\s+(.+))?$",
        help=("adminliyi geri almaq",
              "İstifadəçinin admin hüquqlarını silir.\n\n"
              "<b>İstifadə:</b>\n• reply → <code>.demote</code>\n• <code>.demote all</code>"))
    async def on_demote(event):
        client = event.client
        chat = await event.get_chat()
        perms, err = await admin_check(client, chat)
        if err:
            await safe_edit(event, err)
            return

        raw = (event.pattern_match.group(1) or "").strip()
        args = raw.split() if raw else []

        # .demote all
        if args and args[0].lower() in ("all", "hərkəs", "hamı"):
            empty_rights = make_rights({})
            await safe_edit(event, "⏳ <i>Adminlər çıxarılır...</i>")
            done, fail = 0, 0
            async for u in client.iter_participants(chat, filter=ChannelParticipantsAdmins):
                if u.is_self:
                    continue
                try:
                    await client(EditAdminRequest(channel=chat, user_id=u, admin_rights=empty_rights, rank=""))
                    done += 1
                    await asyncio.sleep(BULK_DELAY)
                except Exception:
                    fail += 1
            await safe_edit(event, f"✅ <b>Bütün adminlər çıxarıldı:</b> {done} uğurlu, {fail} xəta.")
            return

        try:
            ent, _ = await resolve_target(event, args)
        except Exception as e:
            await safe_edit(event, f"❌ İstifadəçi tapılmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if ent is None:
            await safe_edit(event, "ℹ️ Kimin adminliyini almaq istəyirsiniz? Reply edin və ya <code>.demote @username</code> yazın.")
            return

        empty_rights = make_rights({})
        try:
            await client(EditAdminRequest(channel=chat, user_id=ent, admin_rights=empty_rights, rank=""))
            await safe_edit(event, f"👤 {mention(ent)} artıq admin deyil.")
            with contextlib.suppress(Exception):
                from common.audit import record_audit_event
                me = await client.get_me()
                record_audit_event(
                    chat_id=chat.id, chat_title=getattr(chat, "title", str(chat.id)),
                    admin_id=me.id, admin_name=plain_name(me), admin_username=getattr(me, "username", "") or "",
                    target_id=ent.id, target_name=plain_name(ent), target_username=getattr(ent, "username", "") or "",
                    action="demote", details="Adminlikdən çıxarıldı"
                )
        except Exception as e:
            logger.info(f".demote xətası: {e}")
            await safe_edit(event, mem_err_text(e))

    @ub.command(
        "members",
        pattern=r"^\.(?:members|tagall|users)(?:\s+(.+))?$",
        help=("qrup üzvlərini siyahılamaq",
              "Qrupdakı üzvləri çıxarır və ya tag edir.\n\n"
              "<b>İstifadə:</b>\n• <code>.members</code>\n• <code>.tagall</code>\n• <code>.members bots</code>\n• <code>.members admins</code>"))
    async def on_members(event):
        if event.is_private:
            await safe_edit(event, "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər.")
            return

        client = event.client
        chat = await event.get_chat()
        filter_arg = (event.pattern_match.group(1) or "").strip().lower()

        await safe_edit(event, "👥 <i>Üzvlər toplanır...</i>")
        members = []
        try:
            async for user in client.iter_participants(chat):
                if user.deleted:
                    continue
                if filter_arg in ("bot", "bots") and not user.bot:
                    continue
                if filter_arg in ("admin", "admins") and not (getattr(user, "participant", None) and hasattr(user.participant, "admin_rights")):
                    continue
                members.append(user)
        except Exception as e:
            logger.info(f".members xətası: {e}")
            await safe_edit(event, f"❌ Üzvlər oxunmadı: <code>{escape(str(e))[:100]}</code>")
            return

        if not members:
            await safe_edit(event, "○ <i>Uyğun üzv tapılmadı.</i>")
            return

        lines = [f"👥 <b>Qrup Üzvləri ({len(members)} nəfər):</b>\n"]
        for i, u in enumerate(members[:100], 1):
            name = escape(plain_name(u))
            lines.append(f"{i}. <a href=\"tg://user?id={u.id}\">{name}</a> (<code>{u.id}</code>)")

        if len(members) > 100:
            lines.append(f"\n<i>...və daha {len(members) - 100} nəfər.</i>")

        await safe_edit(event, "\n".join(lines))
