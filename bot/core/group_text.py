"""
🔎 bot/core/group_text.py — qrupda «/music» yazmadan mətnlə mahnı axtarışı (şəxsi çatdakı kimi).

Qrup adminləri bunu /menu qrup panelində («🔎 Mətnlə axtarış») HƏR QRUP ÜÇÜN ayrıca açır; defolt — bağlı.
Açıq qrupda adi mesaj («Eminem Lose Yourself») = «/music Eminem Lose Yourself». Adi söhbətə qarışmamaq üçün:
  • yalnız sadə mətn: əmr, link, @qeyd, e-poçt, forward, bot mesajı sayılmır
  • başqasının mesajına cavab (reply) axtarış deyil — söhbətdir
  • 2–100 simvol, ən azı 2 hərf / rəqəm
  • eyni istifadəçi eyni qrupda 8 san.-də bir dəfə

⚠️ Bot qrupda adi mesajları görməlidir: qrupda ADMİN olmalı (və ya BotFather → Group Privacy söndürülməli).
"""
from __future__ import annotations

import time

MIN_LEN, MAX_LEN = 2, 100
USER_COOLDOWN = 8.0
SKIP_ENTITIES = {"mention", "text_mention", "url", "text_link", "email", "phone_number", "bot_command", "cashtag"}

_last: dict = {}                                   # (chat_id, user_id) -> son axtarış vaxtı


def looks_like_query(text: str | None) -> bool:
    t = (text or "").strip()
    return MIN_LEN <= len(t) <= MAX_LEN and sum(ch.isalnum() for ch in t) >= 2 and not t.startswith(("/", ".", "!"))


def is_conversation_reply(message) -> bool:
    """Başqasına cavab (reply) — söhbətdir. Forum mövzusunun başlığına «reply» isə sayılmır."""
    rt = getattr(message, "reply_to_message", None)
    if rt is None:
        return False
    thread = getattr(message, "message_thread_id", None)
    return not (getattr(message, "is_topic_message", False) and getattr(rt, "message_id", None) == thread)


def cooldown_ok(chat_id: int, user_id: int, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    if len(_last) > 5000:
        _last.clear()
    if now - _last.get((chat_id, user_id), -1e9) < USER_COOLDOWN:
        return False
    _last[(chat_id, user_id)] = now
    return True


def is_group_search(message, enabled: bool, now: float | None = None) -> bool:
    """Bu mesaj qrupda mahnı axtarışı sayılmalıdırmı? (cooldown yalnız hər şey uyğundursa işləyir.)"""
    user = getattr(message, "from_user", None)
    if not enabled or user is None or getattr(user, "is_bot", False):
        return False
    if getattr(message, "forward_origin", None) or getattr(message, "via_bot", None) or getattr(message, "sender_chat", None):
        return False
    if not looks_like_query(getattr(message, "text", None)) or is_conversation_reply(message):
        return False
    if any(getattr(e, "type", None) in SKIP_ENTITIES for e in (getattr(message, "entities", None) or [])):
        return False
    return cooldown_ok(message.chat.id, user.id, now)
