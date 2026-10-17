"""🕵️ anonim.py — Çatda heç bir bildiriş buraxmadan mahnı və mesajları, profil şəkillərini və bio-nu silmək.

Komanda:
  • .anonim — cari çatdakı mahnıları və mesajları sonuncu mesajdan ən başdakıya doğru,
              profil şəkillərini və bio-nu tamamilə səssiz silir.
"""
import asyncio
from html import escape

from telethon.errors import FloodWaitError
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import DeletePhotosRequest
from telethon.tl.types import Channel, Chat, InputPhoto, User

from userbot.core.userbot_api import logger

MSG_SCAN_LIMIT = 2000
PHOTO_DELETE_BATCH = 100
# Canlı olaraq ən sonuncu mesajdan yuxarıya doğru silinməsi üçün kiçik partiya ölçüsü
DELETE_CHUNK = 10


def is_song_or_audio(msg) -> bool:
    """Mesajın mahnı / audio (mp3, audio sənəd, səsli mesaj) olub-olmadığını yoxlayır."""
    if getattr(msg, "audio", None) or getattr(msg, "voice", None):
        return True
    doc = getattr(msg, "document", None)
    if doc:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        if mime.startswith("audio/") or mime in ("application/ogg", "video/ogg"):
            return True
        for attr in getattr(doc, "attributes", []):
            if type(attr).__name__ in ("DocumentAttributeAudio",):
                return True
    return False


async def check_can_delete_others(client, chat) -> bool:
    """Çatda başqalarının (məs. botun göndərdiyi mahnıların) mesajlarını silmək hüququnu yoxlayır."""
    if isinstance(chat, User) or getattr(chat, "is_private", False):
        return True
    try:
        perms = await client.get_permissions(chat, "me")
        return bool(
            perms.is_creator
            or getattr(perms, "delete_messages", False)
            or (isinstance(chat, Chat) and perms.is_admin)
        )
    except Exception:
        return False


async def delete_all_profile_photos(client) -> int:
    """Hesabın bütün profil şəkillərini silir və sayını qaytarır."""
    try:
        photos = await client.get_profile_photos("me")
        if not photos:
            return 0
        items = [
            InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)
            for p in photos
        ]
        for i in range(0, len(items), PHOTO_DELETE_BATCH):
            chunk = items[i:i + PHOTO_DELETE_BATCH]
            await client(DeletePhotosRequest(id=chunk))
        return len(items)
    except Exception as e:
        logger.warning(f".anonim profil şəkilləri silmə xətası: {e}")
        return 0


async def clear_profile_bio(client) -> bool:
    """Hesabın bio (about) məlumatını təmizləyir."""
    try:
        await client(UpdateProfileRequest(about=""))
        return True
    except Exception as e:
        logger.warning(f".anonim bio təmizləmə xətası: {e}")
        return False


async def delete_messages_top_down(client, chat_id: int, ids: list[int]) -> int:
    """Mesajları sonuncudan ən başdakıya doğru kiçik partiyalarla silir."""
    if not ids:
        return 0

    # Ən sonuncu mesajdan (ən böyük ID) ən köhnəyə (ən kiçik ID) doğru sıralanır
    ids.sort(reverse=True)

    deleted = 0
    for i in range(0, len(ids), DELETE_CHUNK):
        chunk = ids[i:i + DELETE_CHUNK]
        try:
            await client.delete_messages(chat_id, chunk, revoke=True)
            deleted += len(chunk)
        except FloodWaitError as e:
            await asyncio.sleep(min(e.seconds + 1, 5))
            try:
                await client.delete_messages(chat_id, chunk, revoke=True)
                deleted += len(chunk)
            except Exception as _e:
                logger.debug(f"anonim flood təkrar silmə xətası: {_e}")
        except Exception as e:
            logger.warning(f".anonim mesaj silmə xətası: {e}")
            break
        await asyncio.sleep(0.1)

    return deleted


def register(ub):
    @ub.command(
        "anonim",
        pattern=r"^\.anonim$",
        help=(
            "mahnıları, mesajları, pp və bio-nu səssiz sil",
            "Çatda heç bir bildiriş buraxmadan mahnıları və mesajları sonuncudan başa doğru, "
            "bütün profil şəkillərini və bio-nu silir.\n\n"
            "<b>İstifadə:</b> <code>.anonim</code>",
        ),
        category="acc",
    )
    async def on_anonim(event):
        client = event.client
        chat_id = event.chat_id
        cmd_id = event.id

        # 1. Komandanın özünü DƏRHAL silirik (çatda heç bir iz və bildiriş qalmasın)
        try:
            await event.delete()
        except Exception as _e:
            logger.debug(f"anonim komanda silinmədi: {_e}")

        try:
            me = await client.get_me()
            my_id = me.id if me else None
        except Exception:
            my_id = None

        chat = await event.get_chat()
        can_delete_all = await check_can_delete_others(client, chat)
        is_private = getattr(event, "is_private", False) or isinstance(chat, User)

        # 2. Cari çatdakı mahnıları və öz mesajlarımızı toplayırıq
        target_ids = []
        total_seen = 0
        try:
            async for m in client.iter_messages(chat_id, max_id=cmd_id):
                total_seen += 1
                if m.id == cmd_id:
                    continue

                is_own = getattr(m, "out", False) or (my_id and getattr(m, "sender_id", None) == my_id)
                is_audio = is_song_or_audio(m)

                # Öz mesajlarımız, mahnılar və ya şəxsi çatdakı mesajlar
                if is_own:
                    target_ids.append(m.id)
                elif can_delete_all and (is_private or is_audio):
                    target_ids.append(m.id)

                if total_seen >= MSG_SCAN_LIMIT:
                    break
        except Exception as e:
            logger.warning(f".anonim mesajları toplama xətası: {e}")

        # 3. Sonuncu mesajdan ən başdakıya doğru silirik (köhnələr birinci yox)
        deleted_count = await delete_messages_top_down(client, chat_id, target_ids)

        # 4. Bütün profil şəkillərini silirik
        deleted_photos = await delete_all_profile_photos(client)

        # 5. Profilin bio (about) məlumatını təmizləyirik
        bio_cleared = await clear_profile_bio(client)

        # 6. Yalnız sahibin öz 'Saved Messages' (Özünə Mesaj) bölməsinə gizli hesabat göndəririk
        try:
            chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
            report = (
                "🕵️ <b>Anonim rejim tətbiq edildi:</b>\n\n"
                f"• <b>Çat:</b> {escape(str(chat_title))} (<code>{chat_id}</code>)\n"
                f"• <b>Silinən mahnı və mesajlar:</b> {deleted_count} ədəd (sonuncudan əvvələ doğru)\n"
                f"• <b>Profil şəkilləri:</b> {deleted_photos} ədəd silindi\n"
                f"• <b>Bio:</b> {'Təmizləndi ✅' if bio_cleared else 'Xəta və ya boş idi'}\n\n"
                "<i>ℹ️ Çatda heç bir bildiriş və ya mesaj buraxılmadı.</i>"
            )
            await client.send_message("me", report, parse_mode="html")
        except Exception as e:
            logger.warning(f".anonim hesabatı göndərilmədi: {e}")
