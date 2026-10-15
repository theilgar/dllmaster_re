"""🪪 account.py — hesabın username-i və profil şəkli.

Komandalar:
  • .username [yeni]      — username-i göstərir; yeni yazılsa əvvəl boşdurmu yoxlayır, sonra dəyişir
    .username -           — username-i silir (eyni: .delusername)
  • .pfp                  — reply edilən şəkli (video / GIF də) profil şəkli edir
  • .delpfp [say | all]   — cari profil şəklini silir; say verilsə son N-ni, all — hamısını
"""
import re
from html import escape

from telethon.errors import (
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotModifiedError,
    UsernameOccupiedError,
)
from telethon.tl.functions.account import CheckUsernameRequest, UpdateUsernameRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.types import InputPhoto

from userbot.core.userbot_api import logger, safe_edit, temp_edit

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]$")      # 5–32 simvol, hərflə başlayır, "_" ilə bitmir
REMOVE_WORDS = {"-", "sil", "del", "off", "yox", "remove", "none"}
DELETE_BATCH = 100


def normalize_username(raw: str):
    """'@ali_1', 't.me/ali_1', 'https://t.me/ali_1' → 'ali_1'; yararsızdırsa None."""
    name = (raw or "").strip()
    name = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", name, flags=re.I).lstrip("@").strip("/")
    return name if USERNAME_RE.match(name) else None


async def change_username(client, name: str):
    """name — normalləşmiş ad; '' — silmək. → (uğurlu?, istifadəçiyə HTML mətn)."""
    try:
        if name:
            if not await client(CheckUsernameRequest(name)):
                return False, f"❌ <b>@{escape(name)}</b> tutulub və ya istifadə oluna bilməz."
        await client(UpdateUsernameRequest(name))
    except UsernameOccupiedError:
        return False, f"❌ <b>@{escape(name)}</b> artıq tutulub."
    except UsernameInvalidError:
        return False, "❌ Telegram bu username-i qəbul etmir (5–32 simvol, hərflə başlamalı, rəqəm və _ ola bilər)."
    except UsernameNotModifiedError:
        return False, "ℹ️ Username onsuz da belədir."
    except FloodWaitError as e:
        return False, f"⏳ Telegram limiti — {e.seconds} san. sonra yenidən cəhd et."
    if not name:
        return True, "✅ Username silindi."
    return True, f"✅ Username dəyişdi: <b>@{escape(name)}</b>\n<code>t.me/{escape(name)}</code>"


def media_kind(msg):
    """Reply edilən mesajın profil şəkli ola biləcəyi növü: 'photo' | 'video' | None."""
    if msg is None:
        return None
    if getattr(msg, "photo", None):
        return "photo"
    if getattr(msg, "video", None) or getattr(msg, "gif", None):
        return "video"
    doc = getattr(msg, "document", None)
    if doc is not None and str(getattr(doc, "mime_type", "")).startswith("image/"):
        return "photo"
    return None


async def set_profile_photo(client, msg, kind: str):
    data = await msg.download_media(file=bytes)
    if not data:
        raise RuntimeError("fayl yüklənmədi")
    uploaded = await client.upload_file(data, file_name="avatar.mp4" if kind == "video" else "avatar.jpg")
    if kind == "video":
        await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
    else:
        await client(UploadProfilePhotoRequest(file=uploaded))


async def delete_profile_photos(client, count) -> int:
    """count: int (son N) və ya None (hamısı) → silinənlərin sayı."""
    photos = await client.get_profile_photos("me", limit=count)
    items = [InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference) for p in photos]
    for i in range(0, len(items), DELETE_BATCH):
        await client(DeletePhotosRequest(id=items[i:i + DELETE_BATCH]))
    return len(items)


def register(ub):
    @ub.command(
        "username",
        pattern=r"^\.username(?:\s+(\S+))?$",
        help=(
            "username göstər / dəyiş / sil",
            "Hesabın username-ini göstərir və ya dəyişir. Dəyişməzdən əvvəl boşdurmu yoxlanılır.\n\n"
            "<b>İstifadə:</b>\n• <code>.username</code> — hazırkı\n• <code>.username yeni_ad</code> — dəyiş\n"
            "• <code>.username -</code> və ya <code>.delusername</code> — sil",
        ),
    )
    async def on_username(event):
        client = event.client
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            me = await client.get_me()
            cur = getattr(me, "username", None)
            await safe_edit(event, f"🪪 Username: <b>@{escape(cur)}</b>" if cur else "🪪 Username yoxdur.\n"
                                   "<i>Təyin etmək: .username yeni_ad</i>")
            return
        if arg.lower() in REMOVE_WORDS:
            ok, text = await change_username(client, "")
        else:
            name = normalize_username(arg)
            if name is None:
                await temp_edit(event, "❌ Username 5–32 simvol olmalı, hərflə başlamalı, yalnız hərf, rəqəm və <code>_</code> "
                                       "ola bilər, <code>_</code> ilə bitməməlidir.", delay=4)
                return
            await safe_edit(event, "⏳ <i>Yoxlanılır...</i>")
            ok, text = await change_username(client, name)
        if not ok:
            logger.info(f".username: {text}")
        await temp_edit(event, text, delay=4)

    @ub.command("delusername", pattern=r"^\.(?:delusername|usernamesil)$",
                help=("username-i sil", "Hesabın username-ini silir.\n\n<b>İstifadə:</b> <code>.delusername</code>"))
    async def on_delusername(event):
        _, text = await change_username(event.client, "")
        await temp_edit(event, text, delay=3)

    @ub.command(
        "pfp",
        pattern=r"^\.pfp$",
        help=(
            "reply edilən şəkli profil şəkli et",
            "Reply etdiyin şəkli (və ya qısa video / GIF-i) hesabın profil şəkli edir.\n\n"
            "<b>İstifadə:</b> şəkilə reply → <code>.pfp</code>\n"
            "<i>Silmək: .delpfp · hamısını: .delpfp all</i>",
        ),
    )
    async def on_pfp(event):
        reply = await event.get_reply_message() if event.is_reply else None
        kind = media_kind(reply)
        if kind is None:
            await temp_edit(event, "ℹ️ Şəkilə (və ya videoya) <b>reply</b> edib <code>.pfp</code> yaz.", delay=3)
            return
        await safe_edit(event, "⏳ <i>Profil şəkli yenilənir...</i>")
        try:
            await set_profile_photo(event.client, reply, kind)
        except Exception as e:
            logger.info(f".pfp xətası: {e}")
            await temp_edit(event, f"❌ <b>.pfp:</b> <code>{escape(str(e))[:200]}</code>", delay=4)
            return
        await temp_edit(event, "✅ <b>Profil şəkli dəyişdi.</b>", delay=2)

    @ub.command(
        "delpfp",
        pattern=r"^\.delpfp(?:\s+(all|\d+))?$",
        help=(
            "profil şəklini sil",
            "Cari profil şəklini silir.\n\n<b>İstifadə:</b>\n• <code>.delpfp</code> — cari şəkil\n"
            "• <code>.delpfp 3</code> — son 3 şəkil\n• <code>.delpfp all</code> — hamısı",
        ),
    )
    async def on_delpfp(event):
        arg = (event.pattern_match.group(1) or "1").lower()
        try:
            n = await delete_profile_photos(event.client, None if arg == "all" else max(1, int(arg)))
        except Exception as e:
            logger.info(f".delpfp xətası: {e}")
            await temp_edit(event, f"❌ <b>.delpfp:</b> <code>{escape(str(e))[:200]}</code>", delay=4)
            return
        await temp_edit(event, f"🗑 <b>{n}</b> profil şəkli silindi." if n else "ℹ️ Silinəcək profil şəkli yoxdur.", delay=2)
