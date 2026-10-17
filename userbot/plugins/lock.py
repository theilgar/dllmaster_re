"""🔒 lock.py — çatda seçilmiş mesaj növlərini bağla: gələn belə mesaj avtomatik silinir.

Komandalar:
  • .lock sticker                — şəxsi çatda həmin şəxs, qrupda hamı (admin istisna) stiker göndərə bilməz
  • .lock sticker gif photo      — bir neçə növ birlikdə; .lock media — bütün media; .lock all — hər şey (mətn də)
  • .lock sticker @user          — qrupda yalnız həmin şəxs üçün (və ya onun mesajına reply et)
  • .unlock sticker | media | all  — açır;   .locks — inline PANEL: düymələrlə növləri bağla / aç, başqa çat və qrup seç,
                                   şəxs üzrə kilidlərə bax, fasilə ver, ümumi ayarlar (userbot/core/lock_card.py)

Növlər: sticker · gif · photo · video · voice · videonote · audio · document · contact · location · poll · dice · game ·
link · forward · inline · text. Azərbaycanca adlar da işləyir (stiker, şəkil, səs, fayl, link ...).
Qrupda silmək üçün «Mesajları sil» admin icazəsi lazımdır (komanda yoxlayır). Özün, botlar və Telegram xidməti (777000)
toxunulmazdır. Qrup üzrə kilid adminlərə tətbiq olunmur; konkret şəxsə kilid isə admin olsa da işləyir.
Ayar userbot bazasındadır (restartdan sonra qalır). .menu → komanda → ⚙️ Ayarlar: silinəndə qısa bildiriş.
"""
import asyncio
import contextlib
import time
from html import escape

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.types import User

from userbot.core import lock_card
from userbot.core.lock_core import KINDS, SERVICE_IDS, LockState, can_delete, kinds_of, label, parse_kinds, status_text
from userbot.core.userbot_api import box_text, logger, plain_name, resolve_target, safe_edit, temp_edit

ADMIN_TTL = 300.0                 # san. — «göndərən admindir?» cavabı keşdə
NOTICE_GAP = 30.0                 # san. — eyni çat / şəxs üçün bildiriş bu qədər tez-tez deyil
NOTICE_LIFETIME = 6               # san. — bildiriş özü silinir


def register(ub):
    state = LockState(getattr(ub, "db", None))
    admin_cache = {}                    # (chat_id, uid) → (vaxt, admin?)
    notice_at = {}                      # (chat_id, uid) → son bildiriş anı
    ub.state["lock"] = state
    ub.state.setdefault("cmd_options", {})["lock"] = [{
        "key": "notice", "label": "🔔 Silinəndə bildiriş", "default": "off",
        "choices": [("off", "🔕 Səssiz sil"), ("on", "🔔 Qısa bildiriş göndər")],
        "note": f"Bildiriş «🔒 stiker bağlıdır» yazır və {NOTICE_LIFETIME} san. sonra özü silinir (eyni şəxs üçün {int(NOTICE_GAP)} san.-də bir dəfə).",
    }]

    def notice_on() -> bool:
        try:
            return ub.db.kv_get("cmdopt", "lock:notice", "off") == "on"
        except Exception:
            return False

    async def is_admin(client, chat_id, uid) -> bool:
        key, now = (chat_id, uid), time.monotonic()
        hit = admin_cache.get(key)
        if hit and now - hit[0] < ADMIN_TTL:
            return hit[1]
        try:
            perms = await client.get_permissions(chat_id, uid)
            res = bool(perms.is_admin or perms.is_creator)
        except Exception as e:
            logger.debug(f"lock: admin yoxlanmadı ({chat_id}/{uid}): {e}")
            res = False
        admin_cache[key] = (now, res)
        if len(admin_cache) > 500:
            admin_cache.pop(next(iter(admin_cache)))
        return res

    async def targets(event, rest):
        """reply > @username / ID → (entity | None)."""
        try:
            ent, _ = await resolve_target(event, rest)
        except Exception as e:
            raise ValueError(str(e)[:120])
        return ent

    @ub.command(
        "lock", pattern=r"^\.lock(?:\s+(.+))?$",
        help=("mesaj növünü bağla (stiker, şəkil, link ...)",
              "Seçilmiş növdən gələn mesajlar avtomatik silinir. Şəxsi çatda həmin şəxs üçün, qrupda hamı (adminlər istisna) "
              "və ya konkret şəxs üçün.\n\n<b>İstifadə:</b>\n• <code>.lock sticker</code> — stiker bağla\n"
              "• <code>.lock sticker gif photo</code> — bir neçə növ\n• <code>.lock media</code> — bütün media · <code>.lock all</code> — hər şey\n"
              "• <code>.lock link @user</code> — qrupda yalnız həmin şəxs (və ya mesajına reply)\n"
              "• <code>.unlock sticker</code> / <code>.unlock all</code> · <code>.locks</code> — siyahı\n"
              "Növlər: " + " · ".join(KINDS) + "\n<i>Qrupda «Mesajları sil» admin icazəsi lazımdır.</i>"))
    async def on_lock(event):
        words = (event.pattern_match.group(1) or "").split()
        kinds, rest = parse_kinds(words)
        if not kinds:
            await safe_edit(event, box_text(status_text(state, event.chat_id)))
            return
        await apply(event, kinds, rest, lock=True)

    @ub.command(
        "unlock", pattern=r"^\.unlock(?:\s+(.+))?$",
        help=("kilidi aç", "Bağlanmış növü açır.\n\n<b>İstifadə:</b>\n• <code>.unlock sticker</code>\n"
                          "• <code>.unlock all</code> — bu çatdakı (və ya göstərilən şəxsin) bütün kilidlər\n"
                          "• <code>.unlock link @user</code> — yalnız həmin şəxs üçün"))
    async def on_unlock(event):
        words = (event.pattern_match.group(1) or "").split()
        kinds, rest = parse_kinds(words)
        if not kinds:
            await temp_edit(event, "ℹ️ Nəyi açım? <code>.unlock sticker</code> · <code>.unlock media</code> · <code>.unlock all</code>", delay=4)
            return
        every = any(w.lower() in ("all", "hamısı", "hamisi") for w in words)
        await apply(event, None if every else kinds, rest, lock=False)

    @ub.command("locks", pattern=r"^\.locks$",
                help=("kilidlərin inline paneli",
                      "İnline düymələrlə kilid paneli: bu çatda növləri bir toxunuşla bağla / aç, <b>başqa çat və ya qrup seç</b>, "
                      "şəxs üzrə kilidlərə bax, fasilə ver və ümumi ayarları idarə et (silmə sırası, admin istisnası, "
                      "botlar, bildiriş).\n\n<b>İstifadə:</b> <code>.locks</code>\n"
                      "<i>Inline qadağan olan çatda sadə mətn siyahısı göstərilir.</i>"))
    async def on_locks(event):
        await lock_card.open_card(ub, event, state)

    async def apply(event, kinds, rest, lock: bool):
        client, chat_id = event.client, event.chat_id
        try:
            chat = await event.get_chat()
            private = bool(event.is_private) or isinstance(chat, User)
            ent = None if private else await targets(event, rest)
            if lock and not private and not await can_delete(client, chat):
                await temp_edit(event, "⛔ Bu qrupda başqasının mesajını silmək icazən yoxdur — kilid işləməzdi.", delay=4)
                return
        except ValueError as e:
            await temp_edit(event, f"❌ <b>Hədəf tapılmadı:</b> <code>{escape(str(e))}</code>", delay=4)
            return
        except Exception as e:
            logger.info(f".lock xətası: {e}")
            await temp_edit(event, f"❌ <b>.{'lock' if lock else 'unlock'}:</b> <code>{escape(str(e))[:150]}</code>", delay=4)
            return
        uid = None if ent is None else ent.id
        who = "" if ent is None else f" ({escape(plain_name(ent))})"
        if lock:
            now = state.add(chat_id, kinds, uid)
            note = f"🔒 Bağlandı{who}: {label(kinds)}\nİndi bağlı: {label(now)}"
        else:
            left = state.remove(chat_id, kinds, uid)
            note = f"🔓 Açıldı{who}: {'hamısı' if kinds is None else label(kinds)}" + (f"\nQalan: {label(left)}" if left else "")
        await temp_edit(event, note, delay=4)

    async def warn(event, kind, uid):
        """Qısa bildiriş (ayar açıqdırsa): bir müddət sonra özü silinir."""
        key, now = (event.chat_id, uid), time.monotonic()
        if now - notice_at.get(key, -1e9) < NOTICE_GAP:
            return
        notice_at[key] = now
        try:
            msg = await event.client.send_message(event.chat_id, f"🔒 {KINDS[kind][0]} {KINDS[kind][1]} bu söhbətdə bağlıdır.")
            await asyncio.sleep(NOTICE_LIFETIME)
            await msg.delete()
        except Exception as e:
            logger.debug(f"lock bildirişi: {e}")

    async def check(event):
        if getattr(event, "out", False) or str(event.chat_id) not in state.chats:
            return
        uid = event.sender_id
        if uid is None or uid in SERVICE_IDS:
            return
        kind = state.blocked_kind(event.chat_id, uid, kinds_of(event.message))
        if kind is None:
            return
        chat = state.chats[str(event.chat_id)]
        personal = str(uid) in chat["users"] and kind in chat["users"][str(uid)]
        if state.cfg["admins"] and not event.is_private and not personal and await is_admin(event.client, event.chat_id, uid):
            return                                              # qrup kilidi adminlərə tətbiq olunmur (ayar: 👮 admin istisnası)
        if not state.cfg["bots"]:
            try:
                if getattr(await event.get_sender(), "bot", False):
                    return
            except Exception as e:
                logger.debug(f"lock: göndərən oxunmadı: {e}")
        revoke = state.cfg["revoke"] if event.is_private else True   # qrupda mesaj hamı üçün silinir; şəxsi çatda seçilir
        try:
            await event.client.delete_messages(event.chat_id, [event.id], revoke=revoke)
            state.deleted += 1
        except FloodWaitError as e:
            if e.seconds > 120:
                logger.warning(f"lock: FloodWait {e.seconds} san. — mesaj silinmədi ({event.chat_id})")
                return
            await asyncio.sleep(e.seconds + 1)
            with contextlib.suppress(Exception):
                await event.client.delete_messages(event.chat_id, [event.id], revoke=revoke)
                state.deleted += 1
        except Exception as e:
            logger.info(f"lock: mesaj silinmədi ({event.chat_id}): {e}")
            return
        if notice_on():
            await warn(event, kind, uid)

    # DİQQƏT: hər hadisə növü üçün AYRI funksiya. Host raw handler-ləri id(fn) ilə izləyir (UB.attach_pending) —
    # eyni funksiyanı iki dekoratorla qeyd etsək, yalnız biri client-ə qoşulur (əvvəl yeni mesajlar tutulmurdu).
    @ub.raw(events.NewMessage(incoming=True))
    async def on_new(event):
        await check(event)

    @ub.raw(events.MessageEdited(incoming=True))
    async def on_edited(event):
        await check(event)
