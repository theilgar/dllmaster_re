"""🛡 dm_guard.py — şəxsi mesajlar: bloklama və gələn DM-lərin avtomatik silinməsi.

Komandalar:
  • .block [@user | id]         — bloklayır (reply, @username, ID; şəxsi çatda boş yazsan — həmin şəxsi)
  • .unblock <@user | id | all> — blokdan çıxarır (all — hamısını)
  • .blocked                    — bloklananların siyahısı
  • .dmdel                      — DM təmizləyicinin vəziyyəti
    .dmdel on [strangers]       — şəxsi çatda kim yazsa mesajı silinir (strangers — yalnız kontaktında olmayanlar)
    .dmdel off                  — söndürür
    .dmdel both | me            — mesaj hər iki tərəfdən silinsin / yalnız səndən (standart)
    .dmdel allow | deny [@user] — istisna siyahısına əlavə et / çıxar; .dmdel list — siyahı

Həmişə istisnadır: Telegram xidməti (777000 — giriş kodları gəlir), botlar və özün. Ayar userbot bazasında saxlanılır,
yenidən başlayanda qalır.
"""
import asyncio
import contextlib
from html import escape

from telethon import events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.contacts import BlockRequest, GetBlockedRequest, UnblockRequest
from telethon.tl.types import User

from userbot.core.userbot_api import box_text, logger, plain_name, resolve_target, safe_edit, temp_edit
from userbot.core.userbot_db import get_udb, run_bg

SERVICE_IDS = {777000}                          # Telegram xidməti: giriş kodları və təhlükəsizlik bildirişləri
NS, KEY = "dmdel", "cfg"
STRANGER_WORDS = {"strangers", "stranger", "yad", "yadlar", "tanimadiq", "tanımadıq"}
LIST_LIMIT = 30
BLOCK_PAGE = 100


class DMGuard:
    """Gələn şəxsi mesajları silmə ayarı. Vəziyyət yaddaşdadır (hər mesajda baza oxunmur), yazı fon thread-ində."""

    def __init__(self, udb=None):
        self.cfg = {"on": False, "scope": "all", "revoke": False, "allow": []}
        self.deleted = 0
        self._udb = udb
        try:
            saved = (udb or get_udb()).kv_get(NS, KEY) or {}
            for k in self.cfg:
                if k in saved:
                    self.cfg[k] = saved[k]
        except Exception as e:
            logger.warning(f"DM təmizləyici ayarı oxunmadı: {e}")
        self.cfg["allow"] = [int(x) for x in self.cfg.get("allow") or []]

    def save(self):
        snap = dict(self.cfg, allow=list(self.cfg["allow"]))
        try:
            run_bg((self._udb or get_udb()).kv_set, NS, KEY, snap)
        except Exception as e:
            logger.warning(f"DM təmizləyici ayarı yazılmadı: {e}")

    def should_delete(self, sender, me_id=None) -> bool:
        """Bu göndərənin mesajı silinsinmi? Xidmət, botlar, özün və istisna siyahısı həmişə qorunur."""
        if not self.cfg["on"] or sender is None:
            return False
        uid = getattr(sender, "id", None)
        if uid is None or uid in SERVICE_IDS or uid == me_id or uid in self.cfg["allow"]:
            return False
        if getattr(sender, "bot", False):
            return False
        if self.cfg["scope"] == "strangers" and (getattr(sender, "contact", False) or getattr(sender, "mutual_contact", False)):
            return False
        return True

    def status_text(self) -> str:
        c = self.cfg
        return "\n".join([
            f"🛡 <b>DM təmizləyici</b> — {'🟢 açıq' if c['on'] else '🔴 söndürülüb'}",
            "Kimdən: " + ("yalnız kontaktında olmayanlardan" if c["scope"] == "strangers"
                          else "hamıdan (bot və Telegram xidməti xaric)"),
            "Silmə: " + ("hər iki tərəfdən" if c["revoke"] else "yalnız səndən"),
            f"İstisna: {len(c['allow'])} nəfər",
            f"Silinib (bu açılışdan): {self.deleted}",
            "",
            "<i>.dmdel on · off · both · me · allow · deny · list</i>",
        ])


async def target_of(event, args):
    """reply > @username / ID > şəxsi çatda həmin şəxs. → (entity | None, qalan arqumentlər)."""
    ent, rest = await resolve_target(event, args)
    if ent is None and event.is_private:
        ent = await event.get_chat()
    return ent, rest


def render_blocked(users, total: int) -> str:
    if not total:
        return "✅ <b>Bloklananlar</b>: siyahı boşdur."
    lines = [f"🚫 <b>Bloklananlar</b> — cəmi {total}", ""]
    for i, u in enumerate(users, 1):
        lines.append(f"{i}. {escape(plain_name(u))} · {u.id}")
    if total > len(users):
        lines.append(f"... və daha {total - len(users)} nəfər")
    lines += ["", ".unblock @user — çıxar · .unblock all — hamısını"]
    return "\n".join(lines)


async def _flood_safe(call):
    while True:
        try:
            return await call()
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)


async def unblock_all(client, pause: float = 0.2) -> tuple[int, int]:
    """Bütün bloklananları çıxarır → (çıxarılan, alınmayan). Hər çıxarma siyahını qısaltdığı üçün offset ilə səhifələmək
    nəfərləri atlayardı: hər dəfə başdan oxunur, alınmayanlar (siyahıda qalanlar) offset ilə keçilir."""
    done = failed = 0
    for _ in range(1000):                                   # sonsuz dövrədən qorunma
        res = await client(GetBlockedRequest(offset=failed, limit=BLOCK_PAGE))
        if not res.users:
            break
        for u in res.users:
            try:
                await _flood_safe(lambda: client(UnblockRequest(u)))
                done += 1
            except RPCError as e:
                failed += 1
                logger.debug(f"unblock {u.id}: {e}")
            await asyncio.sleep(pause)
    return done, failed


def register(ub):
    guard = DMGuard()
    ub.state["dm_guard"] = guard

    # ═════════════ 🚫 bloklama ═════════════
    @ub.command(
        "block",
        pattern=r"^\.block(?:\s+(.+))?$",
        help=(
            "istifadəçini blokla",
            "İstifadəçini bloklayır.\n\n<b>İstifadə:</b>\n• şəxsi çatda <code>.block</code> — həmin şəxsi\n"
            "• mesaja reply → <code>.block</code>\n• <code>.block @username</code> və ya <code>.block 123456789</code>\n\n"
            "Şəxsi çatda blokdan sonra çatda yazmaq mümkün olmur, nəticə <b>Saxlanmış mesajlara</b> gəlir. "
            "Çıxarmaq: <code>.unblock @user</code>",
        ),
    )
    async def on_block(event):
        client = event.client
        try:
            ent, _ = await target_of(event, (event.pattern_match.group(1) or "").split())
        except Exception as e:
            await temp_edit(event, f"❌ <b>.block:</b> <code>{escape(str(e))[:150]}</code>", delay=3)
            return
        if ent is None:
            await temp_edit(event, "ℹ️ Kimi? Mesaja <b>reply</b> et, <code>.block @username</code> yaz və ya şəxsi çatda "
                                   "<code>.block</code> yaz.", delay=4)
            return
        if not isinstance(ent, User):
            await temp_edit(event, "ℹ️ Yalnız istifadəçi bloklana bilər (qrup / kanal yox).", delay=3)
            return
        me = await client.get_me()
        if ent.id == me.id:
            await temp_edit(event, "🙃 Özünü bloklaya bilməzsən.", delay=3)
            return
        if ent.id in SERVICE_IDS:
            await temp_edit(event, "⛔ Telegram xidmətini bloklamaq olmaz (giriş kodları ondan gəlir).", delay=4)
            return
        private = event.is_private
        if private:                                    # blokdan sonra bu çatda redaktə / yazı mümkün olmur
            with contextlib.suppress(Exception):
                await event.delete()
        try:
            await _flood_safe(lambda: client(BlockRequest(ent)))
        except Exception as e:
            logger.info(f".block xətası: {e}")
            err = f"❌ <b>.block:</b> <code>{escape(str(e))[:150]}</code>"
            await (client.send_message("me", err, parse_mode="html") if private else temp_edit(event, err, delay=4))
            return
        text = f"🚫 <b>{escape(plain_name(ent))}</b> bloklandı. <i>Çıxarmaq: .unblock {ent.id}</i>"
        if private:
            await client.send_message("me", text, parse_mode="html")
        else:
            await temp_edit(event, text, delay=3)

    @ub.command(
        "unblock",
        pattern=r"^\.unblock(?:\s+(.+))?$",
        help=(
            "blokdan çıxar",
            "Bloklanmış istifadəçini çıxarır.\n\n<b>İstifadə:</b>\n• <code>.unblock @username</code> / <code>.unblock 123456789</code>\n"
            "• mesaja reply → <code>.unblock</code>\n• <code>.unblock all</code> — hamısını\n\n"
            "Bloklu şəxsin çatında yazmaq olmadığı üçün komandanı başqa çatda və ya Saxlanmış mesajlarda yaz.",
        ),
    )
    async def on_unblock(event):
        client = event.client
        args = (event.pattern_match.group(1) or "").split()
        try:
            if args and args[0].lower() == "all":
                n, failed = await unblock_all(client)
                await temp_edit(event, (f"✅ {n} nəfər blokdan çıxarıldı." + (f" ❌ alınmadı: {failed}" if failed else ""))
                                if n or failed else "ℹ️ Bloklanan yoxdur.", delay=3)
                return
            ent, _ = await target_of(event, args)
            if ent is None:
                await temp_edit(event, "ℹ️ Kimi? <code>.unblock @username</code>, ID, reply və ya <code>.unblock all</code>", delay=4)
                return
            await _flood_safe(lambda: client(UnblockRequest(ent)))
        except Exception as e:
            logger.info(f".unblock xətası: {e}")
            await temp_edit(event, f"❌ <b>.unblock:</b> <code>{escape(str(e))[:150]}</code>", delay=4)
            return
        await temp_edit(event, f"✅ <b>{escape(plain_name(ent))}</b> blokdan çıxarıldı.", delay=3)

    @ub.command("blocked", pattern=r"^\.blocked$",
                help=("bloklananların siyahısı", "Bloklanmış istifadəçilərin siyahısı.\n\n<b>İstifadə:</b> <code>.blocked</code>"))
    async def on_blocked(event):
        client = event.client
        try:
            res = await client(GetBlockedRequest(offset=0, limit=LIST_LIMIT))
        except Exception as e:
            await temp_edit(event, f"❌ <b>.blocked:</b> <code>{escape(str(e))[:150]}</code>", delay=4)
            return
        total = int(getattr(res, "count", None) or len(res.users))
        await safe_edit(event, box_text(render_blocked(res.users, total)))

    # ═════════════ 🛡 gələn DM-ləri sil ═════════════
    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_dm(event):
        if event.out or not guard.cfg["on"] or not event.is_private:
            return
        try:
            sender = await event.get_sender()
            me_id = (ub.state.get("me_id") or getattr(await event.client.get_me(), "id", None))
            ub.state["me_id"] = me_id
        except Exception as e:
            logger.debug(f"DM təmizləyici: göndərən oxunmadı: {e}")
            return
        if not guard.should_delete(sender, me_id):
            return
        try:
            await event.client.delete_messages(event.chat_id, [event.id], revoke=bool(guard.cfg["revoke"]))
            guard.deleted += 1
        except FloodWaitError as e:
            if e.seconds > 300:
                logger.warning(f"DM təmizləyici: FloodWait {e.seconds} san. — mesaj silinmədi ({event.chat_id})")
                return
            await asyncio.sleep(e.seconds + 1)
            with contextlib.suppress(Exception):
                await event.client.delete_messages(event.chat_id, [event.id], revoke=bool(guard.cfg["revoke"]))
                guard.deleted += 1
        except Exception as e:
            logger.info(f"DM təmizləyici: mesaj silinmədi ({event.chat_id}): {e}")

    @ub.command(
        "dmdel",
        pattern=r"^\.dmdel(?:\s+(\S+))?(?:\s+(.+))?$",
        help=(
            "şəxsi çatda yazanların mesajını sil",
            "Açıq olanda şəxsi çatda sənə kim yazsa, mesajı avtomatik silinir. Telegram xidməti (giriş kodları), botlar, "
            "özün və istisna siyahısı silinmir.\n\n<b>İstifadə:</b>\n• <code>.dmdel</code> — vəziyyət\n"
            "• <code>.dmdel on</code> — hamıdan · <code>.dmdel on strangers</code> — yalnız kontaktında olmayanlardan\n"
            "• <code>.dmdel off</code> — söndür\n• <code>.dmdel both</code> — hər iki tərəfdən sil · <code>.dmdel me</code> — yalnız səndən\n"
            "• <code>.dmdel allow @user</code> / <code>.dmdel deny @user</code> — istisna · <code>.dmdel list</code>",
        ),
    )
    async def on_dmdel(event):
        client = event.client
        sub = (event.pattern_match.group(1) or "").lower()
        rest = (event.pattern_match.group(2) or "").split()
        c = guard.cfg
        note = None
        if sub in ("", "status", "durum", "vəziyyət"):
            await safe_edit(event, box_text(guard.status_text()))
            return
        if sub in ("on", "aç", "ac"):
            c["on"] = True
            c["scope"] = "strangers" if rest and rest[0].lower() in STRANGER_WORDS else "all"
            note = "🟢 Açıldı: " + ("kontaktında olmayanların" if c["scope"] == "strangers" else "hamının") + " mesajı silinəcək."
        elif sub in ("off", "bağla", "bagla"):
            c["on"] = False
            note = "🔴 Söndürüldü."
        elif sub in ("both", "iki"):
            c["revoke"] = True
            note = "🗑 Mesajlar hər iki tərəfdən silinəcək."
        elif sub in ("me", "mene", "mən"):
            c["revoke"] = False
            note = "🗑 Mesajlar yalnız səndən silinəcək."
        elif sub in ("allow", "icazə", "icaze", "deny", "sil"):
            try:
                ent, _ = await target_of(event, rest)
            except Exception as e:
                await temp_edit(event, f"❌ <b>.dmdel:</b> <code>{escape(str(e))[:150]}</code>", delay=3)
                return
            if ent is None:
                await temp_edit(event, "ℹ️ Kimi? Reply et, <code>.dmdel allow @user</code> yaz və ya həmin şəxsin çatında yaz.", delay=4)
                return
            if sub in ("allow", "icazə", "icaze"):
                if ent.id not in c["allow"]:
                    c["allow"].append(ent.id)
                note = f"✅ <b>{escape(plain_name(ent))}</b> istisnadır — mesajı silinməyəcək."
            else:
                with contextlib.suppress(ValueError):
                    c["allow"].remove(ent.id)
                note = f"↩️ <b>{escape(plain_name(ent))}</b> istisnadan çıxarıldı."
        elif sub in ("list", "siyahi", "siyahı"):
            names = []
            for uid in c["allow"][:LIST_LIMIT]:
                try:
                    names.append(f"{escape(plain_name(await client.get_entity(uid)))} · {uid}")
                except Exception:
                    names.append(str(uid))
            body = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1)) or "boşdur"
            await safe_edit(event, box_text(f"🛡 <b>DM istisnaları</b> — {len(c['allow'])}\n\n{body}"))
            return
        else:
            await temp_edit(event, "ℹ️ <code>.dmdel</code> · <code>on [strangers]</code> · <code>off</code> · <code>both</code> · "
                                   "<code>me</code> · <code>allow</code> · <code>deny</code> · <code>list</code>", delay=5)
            return
        guard.save()
        await temp_edit(event, note, delay=3)
