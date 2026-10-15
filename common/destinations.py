"""
📍 Vahid Göndərmə Hədəfləri Sistemi — common/destinations.py

Həm 🤖 Bot, həm də 📱 Userbot tərəfindən göndərilən avtomatik fayl və bildirişlərin hədəfləri:
  logs    📜 periodik log faylları (logs_plugin, userbot logger)
  backup  💾 baza yedəkləri (backup_plugin, userbot backup)
  errors  🚨 ani xəta bildirişləri (destinations_plugin)
  archive 📦 kod arxivləri (archive_plugin)
  audit   🛡 qrup auditi və gün sonu hesabatı (common/audit, audit_plugin)
  vcrec   🎙 səsli yayım qeydləri (voice_assistant_plugin, vcrec)

Hədəf kanal, adi qrup və ya forum (community) qrupunun konkret MÖVZUSU ola bilər:
  {"chat_id": -100..., "thread_id": 12 | None, "title": "...", "type": "channel|supergroup|forum|private"}
"""
import re
from typing import Any, Dict, Optional, Tuple, Union

from common.database import Settings

KINDS: Dict[str, Dict[str, Any]] = {
    "logs":    {"label": "📜 Loglar",          "cmd": "setlog",    "legacy": ("logs:chat", "logs:chat_title")},
    "backup":  {"label": "💾 Yedəklər",        "cmd": "setbackup", "legacy": ("backup:chat", "backup:chat_title")},
    "errors":  {"label": "🚨 Ani xətalar",     "cmd": "seterrors", "legacy": None},
    "archive": {"label": "📦 Arxiv",           "cmd": "setarchive", "legacy": None},
    "audit":   {"label": "🛡 Audit hesabatı",   "cmd": "setaudit",   "legacy": None},
    "vcrec":   {"label": "🎙 Səs qeydləri",     "cmd": "setvcrec",   "legacy": None},
}
ORDER = ["logs", "backup", "errors", "archive", "audit", "vcrec"]

_s = Settings().get


def get(kind: str) -> Optional[Dict[str, Any]]:
    """Təyin edilmiş hədəfi qaytarır və ya None."""
    if kind not in KINDS:
        return None
    chat = _s(f"dest:{kind}:chat")
    title = _s(f"dest:{kind}:title")
    meta = KINDS[kind]
    if not chat and meta.get("legacy"):
        chat = _s(meta["legacy"][0])
        title = title or _s(meta["legacy"][1])
    if not chat:
        return None
    thread = _s(f"dest:{kind}:thread")
    return {
        "chat_id": int(chat) if str(chat).lstrip("-").isdigit() else chat,
        "thread_id": int(thread) if thread and str(thread).isdigit() else None,
        "title": title or str(chat),
        "type": _s(f"dest:{kind}:type") or "",
    }


from common.database import Settings, set_setting


def put(kind: str, chat_id: Union[int, str], thread_id: Optional[int] = None,
        title: Optional[str] = None, chat_type: Optional[str] = None) -> None:
    """Hədəfi bazaya yazır."""
    set_setting(f"dest:{kind}:chat", str(chat_id) if chat_id is not None else None)
    set_setting(f"dest:{kind}:thread", str(thread_id) if thread_id else None)
    set_setting(f"dest:{kind}:title", title)
    set_setting(f"dest:{kind}:type", chat_type)
    if kind in KINDS:
        legacy = KINDS[kind].get("legacy")
        if legacy:
            set_setting(legacy[0], str(chat_id) if chat_id is not None else None)
            set_setting(legacy[1], title)


def clear(kind: str) -> None:
    """Hədəfi sıfırlayır."""
    for k in ("chat", "thread", "title", "type"):
        set_setting(f"dest:{kind}:{k}", None)
    if kind in KINDS:
        legacy = KINDS[kind].get("legacy")
        if legacy:
            set_setting(legacy[0], None)
            set_setting(legacy[1], None)


def kwargs(kind: str) -> Dict[str, Any]:
    """Aiogram bot.send_* üçün: {"chat_id", "message_thread_id"} — yoxdursa {}."""
    d = get(kind)
    if not d:
        return {}
    out: Dict[str, Any] = {"chat_id": d["chat_id"]}
    if d["thread_id"]:
        out["message_thread_id"] = d["thread_id"]
    return out


def describe(kind: str) -> str:
    """Hədəfin vizual status mətni."""
    d = get(kind)
    if not d:
        return "— təyin edilməyib"
    icon = {"channel": "📢", "forum": "💬", "supergroup": "👥", "group": "👥", "private": "👤"}.get(d["type"], "📍")
    topic = f" › 🧵 mövzu #{d['thread_id']}" if d["thread_id"] else ""
    return f"{icon} {d['title']}{topic}"


# ───────────────────────── link / ID / yönləndirmə → hədəf ─────────────────────────
_TME = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$", re.I)


def parse_target(text: str) -> Tuple[Optional[Union[int, str]], Optional[int]]:
    """
    Mətndən çat istinadını və mövzu ID-sini çıxarır.
      @qrup                      → ("@qrup", None)
      -1001234567890             → (-1001234567890, None)
      t.me/qrup                  → ("@qrup", None)
      t.me/qrup/5                → ("@qrup", 5)
      t.me/c/1234567890/5        → (-1001234567890, 5)
    """
    t = (text or "").strip()
    # Dırnaqları təmizlə
    t = re.sub(r'["\'«»“”„‟`]', '', t).strip()
    if not t:
        return None, None
    if t.lstrip("-").isdigit():
        return int(t), None
    if t.startswith("@"):
        return "@" + t[1:].split("/")[0], None
    m = _TME.match(t)
    if not m:
        return None, None
    parts = [p for p in m.group(1).split("?")[0].split("/") if p]
    if not parts or parts[0].startswith("+") or parts[0] in ("joinchat", "addlist"):
        return None, None
    if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
        chat = int("-100" + parts[1])
        rest = parts[2:]
    else:
        chat = "@" + parts[0]
        rest = parts[1:]
    thread = int(rest[0]) if rest and rest[0].isdigit() else None
    return chat, thread


def extract_topic_id(msg: Any) -> Optional[int]:
    """Telethon və ya Aiogram mesajından forum topic (thread) ID-sini təhlükəsiz çıxarır."""
    if not msg:
        return None
    # Aiogram 3 Message
    thread_id = getattr(msg, "message_thread_id", None)
    if thread_id:
        return int(thread_id)
    # Telethon Message
    rep = getattr(msg, "reply_to", None)
    if rep:
        top_id = getattr(rep, "reply_to_top_id", None)
        if top_id:
            return int(top_id)
        if getattr(rep, "forum_topic", False):
            m_id = getattr(rep, "reply_to_msg_id", None)
            if m_id:
                return int(m_id)
    return None


def parse_destination_target(raw: str) -> Tuple[Optional[Union[int, str]], Optional[int], str]:
    """İstifadəçinin daxil etdiyi mətndən (link, ID, topic, me) hədəfi və təsvirini çıxarır."""
    s = (raw or "").strip()
    if not s:
        return None, None, "Boş dəyər daxil edildi."

    parts = s.split()
    target_str = parts[0]
    topic_str = parts[1] if len(parts) > 1 else None

    if target_str.lower() in ("me", "özüm", "ozum", "saved"):
        return "me", None, "Saved Messages (Özünə)"

    chat_ref, thread_ref = parse_target(target_str)
    if topic_str and str(topic_str).isdigit():
        thread_ref = int(topic_str)

    if chat_ref is not None:
        desc = f"{chat_ref}" + (f" (Mövzu: {thread_ref})" if thread_ref else "")
        return chat_ref, thread_ref, desc

    return None, None, "Format düzgün deyil (@kanal, -100..., link və ya 'me' yazın)."


async def resolve(bot_or_client, text: str = "", forward_chat=None, allow_thread: bool = True) -> dict:
    """Həm Aiogram bot, həm də Telethon client üçün hədəfi resolve edir."""
    if forward_chat is not None:
        ref, thread = getattr(forward_chat, "id", forward_chat), None
    else:
        ref, thread = parse_target(text)
        if ref is None:
            if "t.me/+" in (text or "") or "joinchat" in (text or ""):
                raise ValueError("Dəvət linki (t.me/+...) ilə çatı tapmaq olmur — qrupun @username linkini, "
                                 "-100... ID-ni göndər və ya həmin mövzunun içində komandanı yaz.")
            raise ValueError("@username, -100... ID, t.me linki göndər və ya mesaj yönləndir.")

    # Telethon client yoxsa Aiogram bot?
    if hasattr(bot_or_client, "get_entity"):
        # Telethon client
        try:
            entity = await bot_or_client.get_entity(ref)
            chat_id = getattr(entity, "id", None)
            if hasattr(entity, "broadcast") and entity.broadcast:
                chat_id = int(f"-100{chat_id}") if not str(chat_id).startswith("-100") else chat_id
                ctype = "channel"
            elif getattr(entity, "megagroup", False):
                chat_id = int(f"-100{chat_id}") if not str(chat_id).startswith("-100") else chat_id
                ctype = "forum" if getattr(entity, "forum", False) else "supergroup"
            else:
                ctype = "group"
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "") or str(chat_id)
            return {
                "chat": entity, "chat_id": chat_id, "thread_id": thread,
                "title": title, "type": ctype
            }
        except Exception as e:
            raise ValueError(f"Çat tapılmadı ({ref}): {e}")

    # Aiogram bot
    try:
        chat = await bot_or_client.get_chat(ref)
    except Exception as e:
        raise ValueError(f"Çat tapılmadı ({ref}): {e}. Bot həmin qrupa / kanala əlavə olunubmu?")
    ctype = getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", ""))
    is_forum = bool(getattr(chat, "is_forum", False))
    if not (allow_thread and is_forum) or thread == 1:
        thread = None
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)
    return {
        "chat": chat, "chat_id": chat.id, "thread_id": thread,
        "title": title, "type": "forum" if is_forum else ctype
    }


async def resolve_message(bot, message, allow_thread: bool = True) -> dict:
    """Yönləndirilmiş və ya mətn kimi verilmiş mesajdan hədəf çatı/mövzunu tapır."""
    origin = getattr(message, "forward_origin", None)
    fchat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None) or getattr(message, "forward_from_chat", None)
    return await resolve(bot, message.text or "", fchat, allow_thread=allow_thread)
