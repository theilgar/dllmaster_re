"""📢 tagall.py — Qrup üzvlərini bir dəfəyə tək bir mesajda tag etmək.

Komandalar:
  • .tagall [mesaj]            — qrupdakı bütün üzvləri bir dəfəyə, tək bir mesaja tag edir
  • .tagall -h [mesaj]         — üzvləri gizli (gözəgörünməz linklərlə) tag edir
  • .tagadmins [mesaj]         — yalnız adminləri bir dəfəyə tək bir mesaja tag edir
  • .all [mesaj] / .tag [mesaj] — .tagall üçün qısa ləqəblər

Qeyd:
  - Mesaja reply edib .tagall yazsanız, tag bildirişi həmin mesaja cavab olaraq gedər.
  - Hər kəsə bildiriş getməsi üçün yeni mesaj göndərilir və .tagall komanda mesajı avtomatik silinir.
"""
from html import escape
import re

from telethon.errors import (
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    UserBannedInChannelError,
)
from telethon.tl.types import Channel, ChannelParticipantsAdmins, Chat, User

from userbot.core.userbot_api import logger, safe_edit

MAX_TAGS_PER_MESSAGE = 100
MAX_MSG_LEN = 4000
ZERO_WIDTH_JOINER = "\u2060"


def clean_display_name(user, max_len: int = 16) -> str:
    """İstifadəçinin qısa, təmizlənmiş adını qaytarır."""
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    name = f"{first} {last}".strip()
    if not name:
        uname = getattr(user, "username", "")
        name = f"@{uname}" if uname else "İstifadəçi"
    # Çoxsətirli və ya artıq boşluqları təmizlə
    name = " ".join(name.split())
    if len(name) > max_len:
        name = name[:max_len - 1] + "…"
    return name or "İstifadəçi"


def build_tag_message(
    members: list,
    custom_text: str = "",
    hidden: bool = False,
    is_admin_mode: bool = False,
    total_found: int = 0,
) -> str:
    """Tək bir mesaj üçün HTML mətni formalaşdırır."""
    count = len(members)

    if is_admin_mode:
        header_text = escape(custom_text) if custom_text else "Adminlərin nəzərinə!"
        title = f"👮 <b>{header_text}</b>"
        label = f"🛡 <b>Adminlər ({count}):</b>"
    else:
        header_text = escape(custom_text) if custom_text else "Diqqət hamı!"
        title = f"📢 <b>{header_text}</b>"
        label = f"👥 <b>Tag edilən üzvlər ({count}):</b>"

    if hidden:
        # Gizli rejim: görünməz simvollara tg://user linki bağlanır
        hidden_tags = "".join(f'<a href="tg://user?id={u.id}">{ZERO_WIDTH_JOINER}</a>' for u in members)
        note = f"\n\n<i>ℹ️ {count} nəfər gizli tag edildi.</i>"
        return f"{title}{hidden_tags}{note}"

    # Açıq (görünən) rejim: adlar vergüllə düzülür
    tags = [f'<a href="tg://user?id={u.id}">{escape(clean_display_name(u))}</a>' for u in members]

    # Telegram 4096 simvol limitini aşmamaq üçün təhlükəsiz qısaltma
    base_text = f"{title}\n\n{label}\n"
    suffix = ""
    if total_found > count:
        suffix = f"\n\n<i>ℹ️ Cəmi {total_found} üzvdən ilk {count} nəfər tag edildi.</i>"

    while tags:
        rendered = base_text + ", ".join(tags) + suffix
        if len(rendered) <= MAX_MSG_LEN:
            return rendered
        tags.pop()

    return base_text + ", ".join(tags) + suffix


def register(ub):
    async def _handle_tag(event, is_admin_mode: bool = False):
        if event.is_private:
            await safe_edit(event, "❌ Bu əmr yalnız <b>qruplarda</b> işlədilə bilər.")
            return

        chat = await event.get_chat()
        if not isinstance(chat, (Channel, Chat)):
            await safe_edit(event, "❌ Bu çat növündə üzvləri tag etmək mümkün deyil.")
            return

        client = event.client
        raw_text = (event.pattern_match.group(1) or "").strip()

        # Gizli tag bayrağını yoxla: -h və ya -gizli
        hidden = False
        if raw_text.startswith(("-h", "-gizli")):
            hidden = True
            raw_text = re.sub(r"^-(?:h|gizli)\s*", "", raw_text).strip()

        # "admins" sözü ilə çağırılıbsa (.tagall admins [mətn])
        if not is_admin_mode and raw_text.lower().startswith(("admins", "admin")):
            is_admin_mode = True
            raw_text = re.sub(r"^(?:admins|admin)\s*", "", raw_text, flags=re.I).strip()

        try:
            me = await client.get_me()
            my_id = me.id if me else None
        except Exception:
            my_id = None

        # Üzvləri topla
        members = []
        try:
            if is_admin_mode:
                async for u in client.iter_participants(chat, filter=ChannelParticipantsAdmins):
                    if getattr(u, "deleted", False) or getattr(u, "bot", False):
                        continue
                    if my_id and u.id == my_id:
                        continue
                    members.append(u)
            else:
                async for u in client.iter_participants(chat):
                    if getattr(u, "deleted", False) or getattr(u, "bot", False):
                        continue
                    if my_id and u.id == my_id:
                        continue
                    members.append(u)
        except ChatAdminRequiredError:
            await safe_edit(event, "❌ Bu qrupda üzvlər siyahısı gizlədilib (admin hüququ tələb olunur).")
            return
        except FloodWaitError as e:
            await safe_edit(event, f"⏳ Telegram limiti: {e.seconds} saniyə sonra təkrar cəhd edin.")
            return
        except Exception as e:
            logger.warning(f"tagall üzv toplama xətası: {e}")
            await safe_edit(event, f"❌ Üzvlər toplanarkən xəta baş verdi: <code>{escape(str(e))[:120]}</code>")
            return

        if not members:
            target_desc = "admin" if is_admin_mode else "üzv"
            await safe_edit(event, f"ℹ️ Tag ediləcək uyğun {target_desc} tapılmadı.")
            return

        total_found = len(members)
        selected_members = members[:MAX_TAGS_PER_MESSAGE]

        msg_content = build_tag_message(
            members=selected_members,
            custom_text=raw_text,
            hidden=hidden,
            is_admin_mode=is_admin_mode,
            total_found=total_found,
        )

        reply_to = getattr(event, "reply_to_msg_id", None)
        if not reply_to and getattr(event, "is_reply", False):
            reply_msg = await event.get_reply_message()
            reply_to = getattr(reply_msg, "id", None)

        # Əvvəlcə yeni mesaj olaraq göndəririk ki, Telegram hər kəsə bildiriş (push/mention) göndərsin
        try:
            await client.send_message(
                chat,
                msg_content,
                reply_to=reply_to,
                parse_mode="html",
                link_preview=False,
            )
        except (ChatWriteForbiddenError, UserBannedInChannelError):
            await safe_edit(event, "❌ Bu qrupda mesaj göndərmək hüququnuz yoxdur.")
            return
        except FloodWaitError as e:
            await safe_edit(event, f"⏳ Mesaj göndərmə limiti: {e.seconds} saniyə gözləyin.")
            return
        except Exception as e:
            logger.warning(f"tagall göndərmə xətası: {e}")
            await safe_edit(event, f"❌ Mesaj göndərilmədi: <code>{escape(str(e))[:120]}</code>")
            return

        # Komanda mesajını təmizləyirik (çatda artıq iz qalmasın)
        try:
            await event.delete()
        except Exception as _e:
            logger.debug(f"tagall komanda mesajı silinmədi: {_e}")

    @ub.command(
        "tagall",
        pattern=r"^\.(?:tagall|all|tag)(?:\s+([\s\S]+))?$",
        help=(
            "hamını bir mesajda tag et",
            "Qrupdakı bütün üzvləri bir dəfəyə, tək bir mesaja tag edir.\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.tagall</code> — hamını standart başlıqla tag edir\n"
            "• <code>.tagall [mesaj]</code> — xüsusi bildiriş mətni ilə tag edir\n"
            "• <code>.tagall -h [mesaj]</code> — üzvləri gizli (görünməz) tag edir\n"
            "• <code>.tagall admins [mesaj]</code> — yalnız adminləri tag edir\n"
            "• Mesaja reply edib <code>.tagall</code> — həmin mesaja cavab olaraq tag edir",
        ),
        category="acc",
    )
    async def on_tagall(event):
        await _handle_tag(event, is_admin_mode=False)

    @ub.command(
        "tagadmins",
        pattern=r"^\.(?:tagadmins|tagadmin)(?:\s+([\s\S]+))?$",
        help=(
            "adminləri bir mesajda tag et",
            "Qrupdakı bütün adminləri bir dəfəyə, tək bir mesaja tag edir.\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.tagadmins</code>\n"
            "• <code>.tagadmins [mesaj]</code>\n"
            "• <code>.tagadmins -h [mesaj]</code> (gizli)",
        ),
        category="acc",
    )
    async def on_tagadmins(event):
        await _handle_tag(event, is_admin_mode=True)
