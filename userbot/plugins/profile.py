"""👤 profile.py — Profil məlumatları, bio, ad, görünməz ad (ghost) və profil klonlama (.clone / .revert).

Komandalar:
  • .name [ad | soyad]   — profilin adını və soyadını dəyişir
  • .delname             — adı tam görünməz / boş edir (ghost name)
  • .delsurname          — yalnız soyadı silir
  • .bio [mətn]          — bio (haqqında) məlumatını yeniləyir
  • .delbio              — bio məlumatını tam silir
  • .clone               — reply edilən istifadəçinin profilini (ad, bio, foto) klonlayır
  • .revert              — klonlanmış profili əvvəlki orijinal vəziyyətinə qaytarır
"""
import asyncio
import time
from html import escape

from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto

from userbot.core.userbot_api import User, logger, safe_edit
from userbot.core.userbot_db import get_udb
from userbot.core.userbot_api import temp_edit

# Telegram-ın "unsupported character" yazmadığı və tam görünməz qalan xüsusi kombinasiya
GHOST_NAME = "\ufff4\ufff4 \ufff4\ufff4\ufff4\ufff4\ufff4 \ufff4\ufff4"  # "￴￴ ￴￴￴￴￴ ￴￴"

DONE_VISIBLE = 3
NS = "clone"
KEY = "backup"
ABOUT_LIMIT = 70          # Premium olmayan hesab üçün bio limiti
_MEM = {}


def _load_backup() -> dict:
    """{"original": {...}, "photo_ids": [int, ...], "at": ts} — yoxdursa boş dict."""
    try:
        b = get_udb().kv_get(NS, KEY) or {}
        if b.get("original"):
            return b
    except Exception as e:
        logger.warning(f".clone ehtiyatı oxunmadı: {e}")
    return dict(_MEM) if _MEM.get("original") else {}


def _save_backup(b: dict) -> bool:
    _MEM.clear()
    _MEM.update(b)
    try:
        get_udb().kv_set(NS, KEY, b)
        return True
    except Exception as e:
        logger.warning(f".clone ehtiyatı bazaya yazılmadı (yalnız yaddaşda): {e}")
        return False


def _clear_backup():
    _MEM.clear()
    try:
        get_udb().kv_set(NS, KEY, {})
    except Exception as e:
        logger.warning(f".clone ehtiyatı bazadan silinmədi: {e}")


async def _set_profile(client, first_name, last_name, about) -> bool:
    """Profili yeniləyir. Bio limiti aşılırsa (premium olmayan hesab) qısaldıb yenidən yoxlayır."""
    try:
        await client(UpdateProfileRequest(first_name=first_name, last_name=last_name, about=about))
        return False
    except Exception as e:
        if "ABOUT_TOO_LONG" not in str(e).upper() and type(e).__name__ != "AboutTooLongError":
            raise
        await client(UpdateProfileRequest(first_name=first_name, last_name=last_name,
                                          about=(about or "")[:ABOUT_LIMIT]))
        return True


def register(ub):
    # ==========================
    # 1. Ad və Soyad Dəyişmək (.name)
    # ==========================
    @ub.command(
        "name",
        pattern=r"^\.name(?:\s+(.+))?$",
        help=(
            "ad və soyad dəyişmək",
            "Hesabın ad və soyadını yeniləyir (2 san. sonra silinir).\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.name Murad</code>\n"
            "• <code>.name Murad | Əliyev</code>"
        )
    )
    async def on_name(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await temp_edit(event, "❌ <i>Yeni adı qeyd edin!</i>\n<b>Nümunə:</b> <code>.name Murad | Əliyev</code>", delay=2)
            return

        await safe_edit(event, "⏳ <i>Ad yenilənir...</i>")
        parts = [p.strip() for p in arg.split("|", 1)]
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        try:
            await event.client(UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name
            ))
            fullname = f"{first_name} {last_name}".strip()
            await temp_edit(event, f"✅ <b>Ad uğurla yeniləndi:</b> <code>{escape(fullname)}</code>", delay=2)
        except Exception as e:
            logger.info(f".name xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>", delay=2)

    # ==========================
    # 2. Adı Görünməz Etmək (.delname)
    # ==========================
    @ub.command(
        "delname",
        pattern=r"^\.(?:delname|clearname|adsil)$",
        help=(
            "adı silmək / görünməz etmək",
            "Profili tamamilə görünməz və adsız edir (2 san. sonra silinir).\n\n"
            "<b>İstifadə:</b>\n• <code>.delname</code>"
        )
    )
    async def on_delname(event):
        await safe_edit(event, "⏳ <i>Ad silinir (görünməz edilir)...</i>")
        try:
            await event.client(UpdateProfileRequest(
                first_name=GHOST_NAME,
                last_name=""
            ))
            await temp_edit(event, "✅ <b>Profil adı uğurla silindi (tam görünməz edildi)!</b>", delay=2)
        except Exception as e:
            logger.info(f".delname xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>", delay=2)

    # ==========================
    # 3. Yalnız Soyadı Silmək (.delsurname)
    # ==========================
    @ub.command(
        "delsurname",
        pattern=r"^\.(?:delsurname|clearsoyad|soyadsil)$",
        help=(
            "yalnız soyadı silmək",
            "Mövcud adı saxlayır, yalnız soyadı silir (2 san. sonra silinir).\n\n"
            "<b>İstifadə:</b>\n• <code>.delsurname</code>"
        )
    )
    async def on_delsurname(event):
        await safe_edit(event, "⏳ <i>Soyad təmizlənir...</i>")
        try:
            me = await event.client.get_me()
            await event.client(UpdateProfileRequest(
                first_name=me.first_name,
                last_name=""
            ))
            await temp_edit(event, "✅ <b>Soyad uğurla silindi!</b>", delay=2)
        except Exception as e:
            logger.info(f".delsurname xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>", delay=2)

    # ==========================
    # 4. Bio Dəyişmək (.bio)
    # ==========================
    @ub.command(
        "bio",
        pattern=r"^\.bio(?:\s+(.+))?$",
        help=(
            "bio (haqqında) dəyişmək",
            "Hesabın bio bölməsini yeniləyir (2 san. sonra silinir).\n\n"
            "<b>İstifadə:</b>\n• <code>.bio Kod yazmaq sənətdir.</code>"
        )
    )
    async def on_bio(event):
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await temp_edit(event, "❌ <i>Yeni bio mətnini daxil edin!</i>\n<b>Nümunə:</b> <code>.bio Salam Dünya!</code>", delay=2)
            return

        await safe_edit(event, "⏳ <i>Bio yenilənir...</i>")
        try:
            await event.client(UpdateProfileRequest(about=arg))
            await temp_edit(event, f"✅ <b>Bio uğurla yeniləndi:</b>\n<code>{escape(arg)}</code>", delay=2)
        except Exception as e:
            logger.info(f".bio xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>", delay=2)

    # ==========================
    # 5. Bio Silmək (.delbio)
    # ==========================
    @ub.command(
        "delbio",
        pattern=r"^\.(?:delbio|clearbio|biosil)$",
        help=(
            "bio-nu tam silmək",
            "Hesabın bio bölməsini təmizləyir (2 san. sonra silinir).\n\n"
            "<b>İstifadə:</b>\n• <code>.delbio</code>"
        )
    )
    async def on_delbio(event):
        await safe_edit(event, "⏳ <i>Bio təmizlənir...</i>")
        try:
            await event.client(UpdateProfileRequest(about=""))
            await temp_edit(event, "✅ <b>Bio uğurla təmizləndi!</b>", delay=2)
        except Exception as e:
            logger.info(f".delbio xətası: {e}")
            await temp_edit(event, f"❌ <b>Xəta:</b> <code>{escape(str(e))[:200]}</code>", delay=2)

    # ==========================
    # 6. Profil Klonlama (.clone)
    # ==========================
    @ub.command("clone", pattern=r"^\.clone$",
                help=("istifadəçinin profilini klonla",
                      "Mesaja reply edib <code>.clone</code> yazdıqda hədəf şəxsin "
                      "<b>adı</b>, <b>soyadı</b>, <b>bioqrafiyası</b> və <b>profil şəkli</b> "
                      "hesabınıza kopyalanır.\n\n"
                      "Əvvəlki vəziyyətə qayıtmaq üçün: <code>.revert</code>"))
    async def on_clone(event):
        client = event.client

        if not event.is_reply:
            await safe_edit(event, "ℹ️ Klonlamaq istədiyin istifadəçinin mesajına <b>reply</b> et.")
            return

        reply_msg = await event.get_reply_message()
        user = await reply_msg.get_sender()

        if not isinstance(user, User):
            await safe_edit(event, "❌ Yalnız istifadəçi profilləri klonlana bilər (kanal və ya qrup yox).")
            return

        if getattr(user, "is_self", False):
            await safe_edit(event, "ℹ️ Öz hesabınızı klonlaya bilməzsiniz.")
            return

        await safe_edit(event, "🔄 <i>İstifadəçi məlumatları toplanır...</i>")

        try:
            backup = _load_backup()
            persisted = True
            if not backup:
                me = await client.get_me()
                full_me = await client(GetFullUserRequest(me.id))
                backup = {
                    "original": {
                        "first_name": me.first_name or "",
                        "last_name": me.last_name or "",
                        "about": getattr(full_me.full_user, "about", "") or "",
                    },
                    "photo_ids": [],
                    "at": int(time.time()),
                }
                persisted = _save_backup(backup)

            full_user_data = await client(GetFullUserRequest(user.id))
            bio = getattr(full_user_data.full_user, "about", "") or ""

            first_name = user.first_name or ""
            last_name = user.last_name or ""

            cut = await _set_profile(client, first_name, last_name, bio)

            await safe_edit(event, "🖼 <i>Profil şəkli yüklənir...</i>")
            photo_bytes = await client.download_profile_photo(user, file=bytes)

            if photo_bytes:
                uploaded_photo = await client.upload_file(photo_bytes, file_name="avatar.jpg")
                res = await client(UploadProfilePhotoRequest(fallback=False, file=uploaded_photo))
                photo_obj = getattr(res, "photo", None)
                if photo_obj:
                    backup.setdefault("photo_ids", []).append(photo_obj.id)
                    persisted = _save_backup(backup) and persisted

            target_name = escape(first_name + (" " + last_name if last_name else ""))
            notes = []
            if cut:
                notes.append(f"✂️ Bio {ABOUT_LIMIT} simvola qısaldıldı (premium olmayan hesab limiti)")
            if not persisted:
                notes.append("⚠️ Ehtiyat bazaya yazılmadı — restartdan əvvəl .revert et")
            await safe_edit(
                event,
                f"✅ <b>{target_name}</b> hesabı uğurla klonlandı!\n"
                "<i>Geri qaytarmaq üçün: <code>.revert</code></i>"
                + ("\n\n" + "\n".join(notes) if notes else "")
            )

            if not notes:
                await asyncio.sleep(DONE_VISIBLE)
                try:
                    await event.delete()
                except Exception as _e:
                    logger.debug("udulan xəta [userbot/plugins/profile.py:293]: %r", _e)

        except Exception as e:
            logger.info(f".clone xətası: {e}")
            await safe_edit(event, f"❌ <b>.clone xətası:</b> <code>{escape(str(e))[:200]}</code>"
                                   + ("\n<i>Orijinal profil saxlanılıb — .revert ilə qaytara bilərsən.</i>"
                                      if _load_backup() else ""))

    # ==========================
    # 7. Profil Bərpası (.revert)
    # ==========================
    @ub.command("revert", pattern=r"^\.revert$",
                help=("klonlanmış profili geri qaytar",
                      "Klonlanmış profili əvvəlki orijinal halına (ad, soyad, bio və avatar) qaytarır."))
    async def on_revert(event):
        client = event.client
        backup = _load_backup()

        if not backup:
            await safe_edit(
                event,
                "ℹ️ Saxlanılmış orijinal profil məlumatı tapılmadı "
                "(əvvəlcə <code>.clone</code> istifadə edilməlidir)."
            )
            return

        await safe_edit(event, "🔄 <i>Orijinal profil bərpa olunur...</i>")

        try:
            orig = backup["original"]

            await _set_profile(client, orig["first_name"], orig["last_name"], orig["about"])

            ids = set(backup.get("photo_ids") or [])
            photo_note = ""
            if ids:
                try:
                    current = await client.get_profile_photos("me", limit=100)
                    to_delete = [InputPhoto(id=p.id, access_hash=p.access_hash, file_reference=p.file_reference)
                                 for p in current if p.id in ids]
                    if to_delete:
                        await client(DeletePhotosRequest(id=to_delete))
                    missing = len(ids) - len(to_delete)
                    if missing:
                        photo_note = f"\n<i>ℹ️ {missing} klon şəkli artıq profildə yox idi (əl ilə silinib).</i>"
                except Exception as pe:
                    logger.info(f".revert şəkil silinmədi: {pe}")
                    photo_note = "\n⚠️ <i>Klon şəkli silinmədi — profil şəkillərindən əl ilə sil.</i>"

            _clear_backup()

            await safe_edit(event, "✅ Profiliniz uğurla əvvəlki vəziyyətinə qaytarıldı!" + photo_note)

            if not photo_note:
                await asyncio.sleep(DONE_VISIBLE)
                try:
                    await event.delete()
                except Exception as _e:
                    logger.debug("udulan xəta [userbot/plugins/profile.py:351]: %r", _e)

        except Exception as e:
            logger.info(f".revert xətası: {e}")
            await safe_edit(event, f"❌ <b>.revert xətası:</b> <code>{escape(str(e))[:200]}</code>\n"
                                   "<i>Ehtiyat saxlanılıb — yenidən .revert yoxla.</i>")
