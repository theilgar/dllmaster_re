"""
📢 Depo kanalı üçün ORTAQ yoxlama və daxiletmə.

Kanal təyin edən bütün yerlər eyni qaydanı işlədir:
  • /menu → Depo → 📢 Depo kanalı   (əsas depo, YouTube deposu) — bot/plugins/depo_channel_plugin.py
  • ➕ Əlavə kanal (yükü bölən kanal) — bot/depo/shard_menu.py, /depo shard add

Kanal necə göstərilə bilər: @istifadəçi_adı, -100… id, t.me/ad, t.me/ad/mövzu, t.me/c/…/mövzu,
kanaldan / mövzudan yönləndirilmiş mesaj. Dəvət linki (t.me/+…) kanalı tanıtmır.
Bot orada yazmaq hüququ olan admin (kanalda "Post messages") olmalıdır — səbəbi ilə birlikdə deyilir.
"""
import re
from html import escape
from types import SimpleNamespace

from common.destinations import extract_topic_id, parse_target

_BARE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,}")


def post_access(chat, member) -> tuple:
    """Bot bu çatda audio göndərə bilərmi → (ok, səbəb HTML). Kanalda admin + Post messages, qrupda üzv də olar."""
    ctype = getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", ""))
    status = getattr(getattr(member, "status", ""), "value", getattr(member, "status", ""))
    if status == "creator":
        return True, ""
    if ctype == "channel":
        if status != "administrator":
            return False, "Bot bu kanalda <b>administrator</b> deyil."
        if getattr(member, "can_post_messages", None) is False:
            return False, "Botun kanalda <b>\"Mesaj göndərmək\"</b> (Post messages) icazəsi yoxdur."
        return True, ""
    if status == "administrator":
        return True, ""
    if status == "member":
        if getattr(member, "can_send_messages", True) is False:
            return False, "Botun bu qrupda mesaj göndərmək hüququ yoxdur."
        return True, ""
    if status == "restricted":
        if getattr(member, "can_send_messages", False) and getattr(member, "can_send_audios", True):
            return True, ""
        return False, "Botun bu qrupda mesaj/audio göndərmək hüququ məhdudlaşdırılıb."
    return False, f"Bot bu qrupda deyil və ya uyğun statusda deyil (status: {status})."


def _denied(chat, reason: str) -> str:
    ctype = getattr(getattr(chat, "type", ""), "value", getattr(chat, "type", ""))
    text = f"❌ {reason}\n\n" if reason else "❌ Bot bu çatda mesaj göndərə bilmir.\n\n"
    text += ("Kanal ayarlarına daxil olun, botu <b>Administrator</b> edin və <b>\"Mesaj göndərmək\"</b> icazəsi verin."
             if ctype == "channel" else
             "Botu qrupa əlavə edin və mesaj göndərmə icazəsi verin (tövsiyə olunur: admin edin).")
    return text


async def inspect(bot, target) -> tuple:
    """Kanalı tapır və botun hüququnu yoxlayır → (SimpleNamespace(chat, ref, title) | None, xəta HTML)."""
    try:
        target = int(target) if str(target).lstrip("-").isdigit() else target
        chat = await bot.get_chat(target)
        member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
    except Exception as e:
        return None, (f"❌ Kanal/qrup tapılmadı və ya bot orada deyil: <code>{escape(str(e))[:200]}</code>\n"
                      "<i>Botu əvvəlcə həmin kanala/qrupa əlavə edin.</i>")
    ok, reason = post_access(chat, member)
    if not ok:
        return None, _denied(chat, reason)
    ref = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
    return SimpleNamespace(chat=chat, ref=ref, title=getattr(chat, "title", None) or ref), ""


async def from_message(bot, message) -> tuple:
    """Mətn / yönləndirmə → (Found, mövzu, xəta HTML). Found = inspect() nəticəsi; mövzu link / mesajdan çıxarılır."""
    text = (getattr(message, "text", None) or "").strip()
    fwd_thread = extract_topic_id(message)
    origin = getattr(message, "forward_origin", None)
    fchat = (getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
             or getattr(message, "forward_from_chat", None))
    target, parsed_thread = None, None
    if fchat:
        target = fchat.id
    elif text.lstrip("-").isdigit():
        target = int(text)
    elif text.startswith("@") or "t.me/" in text:
        target, parsed_thread = parse_target(text)
        if target is None:
            return None, None, ("❌ Dəvət linki (t.me/+...) ilə kanalı tapa bilmirəm — "
                                "kanaldan bir mesajı <b>yönləndir</b> və ya -100... ID göndər.")
    elif _BARE.fullmatch(text):
        target = "@" + text
    if target is None:
        return None, None, "❌ @username, -100... ID, t.me linki göndər və ya kanaldan mesaj yönləndir."
    found, err = await inspect(bot, target)
    return found, (parsed_thread or fwd_thread), err
