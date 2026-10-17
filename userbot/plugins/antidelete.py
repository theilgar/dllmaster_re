"""🗑 antidelete.py — Silinən mesajları və mediaları tutub 'Saved Messages'-ə göndərmək.

Komandalar:
  • .antidelete (və ya .antidel)          — cari status və idarəetmə paneli
  • .antidelete on / off                  — anti-delete rejimini aç / bağla
  • .antidelete dm (və ya .antidel pm)    — yalnız şəxsi (DM) söhbətlərdə aktiv et (tövsiyə olunur)
  • .antidelete all                       — həm şəxsi, həm qruplarda aktiv et
  • .antidelete media on / off            — media (şəkil, səs, video qeyd) saxlanmasını aç / bağla
  • .antidelete clear                     — keşlənmiş mesajları və müvəqqəti faylları təmizlə

İnline panel: .antidelete (arqumentsiz) düymələrlə kart açır; eyni panel .menu → ⚡ Funksiyalar → 🛡 Anti-Delete bölməsindədir
(aç / söndür, DM / bütün çatlar, media, keşi təmizlə, statistikanı sıfırla).
"""
import asyncio
import collections
from html import escape
import os
import time

from aiogram.types import InlineKeyboardMarkup
from telethon import events
from telethon.tl.types import User

from userbot.core.userbot_api import Out, logger, plain_name, safe_edit, temp_edit
from userbot.core.userbot_db import get_udb, run_bg

NS, KEY = "antidelete", "cfg"
MAX_CACHE_MSGS = 3000
MAX_MEDIA_MB = 20
MEDIA_CACHE_DIR = os.path.join("data", "antidelete_media")

# Yaddaş keşi: (chat_id, msg_id) -> dict
_msg_cache = collections.OrderedDict()
# Şəxsi çat indeksləri: msg_id -> chat_id (Telegram DM silinmələrində chat_id göndərmir)
_mid_to_chat = collections.OrderedDict()


class AntiDeleteState:
    def __init__(self, udb=None):
        self._udb = udb
        self.cfg = {
            "on": True,
            "mode": "dm",          # "dm" (yalnız şəxsi) və ya "all" (bütün çatlar)
            "save_media": True,
            "caught": 0,
            "media_caught": 0,
        }
        try:
            saved = (udb or get_udb()).kv_get(NS, KEY) or {}
            for k in self.cfg:
                if k in saved:
                    self.cfg[k] = saved[k]
        except Exception as e:
            logger.debug(f"AntiDelete ayarı oxunmadı: {e}")

    def save(self):
        try:
            run_bg((self._udb or get_udb()).kv_set, NS, KEY, dict(self.cfg))
        except Exception as e:
            logger.debug(f"AntiDelete ayarı yazılmadı: {e}")


def _detect_media_kind(msg) -> str | None:
    """Mesajın media növünü müəyyən edir."""
    if getattr(msg, "voice", None):
        return "🎙 Səsli mesaj"
    if getattr(msg, "video_note", None):
        return "📹 Video qeyd (yumru)"
    if getattr(msg, "photo", None):
        return "🖼 Şəkil"
    if getattr(msg, "sticker", None):
        return "🎭 Stiker"
    if getattr(msg, "audio", None):
        return "🎵 Mahnı / Audio"
    if getattr(msg, "video", None):
        return "🎬 Video"
    if getattr(msg, "document", None):
        return "📄 Sənəd"
    return None


def _clean_old_media():
    """Media qovluğundakı 24 saatdan köhnə faylları təmizləyir."""
    if not os.path.isdir(MEDIA_CACHE_DIR):
        return
    cutoff = time.time() - 86400
    try:
        for fn in os.listdir(MEDIA_CACHE_DIR):
            fp = os.path.join(MEDIA_CACHE_DIR, fn)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                try:
                    os.remove(fp)
                except OSError:
                    pass
    except Exception as e:
        logger.debug(f"antidelete media təmizlənmədi: {e}")


def clear_cache() -> tuple:
    """Yaddaş keşini və media qovluğunu boşaldır → (mesaj sayı, fayl sayı)."""
    n_msgs, n_files = len(_msg_cache), 0
    _msg_cache.clear()
    _mid_to_chat.clear()
    if os.path.isdir(MEDIA_CACHE_DIR):
        for f in os.listdir(MEDIA_CACHE_DIR):
            try:
                os.remove(os.path.join(MEDIA_CACHE_DIR, f))
                n_files += 1
            except OSError:
                pass
    return n_msgs, n_files


def media_cache_stats() -> tuple:
    """(fayl sayı, MB) — keşdəki media faylları."""
    n = size = 0
    if os.path.isdir(MEDIA_CACHE_DIR):
        for f in os.listdir(MEDIA_CACHE_DIR):
            try:
                size += os.path.getsize(os.path.join(MEDIA_CACHE_DIR, f))
                n += 1
            except OSError:
                pass
    return n, size / 1048576


def status_text(state: AntiDeleteState, note: str = "") -> str:
    c = state.cfg
    n_media, mb = media_cache_stats()
    text = (
        "🛡 <b>Anti-Delete Monitoru</b>\n\n"
        f"• <b>Status:</b> {'🟢 Aktiv' if c.get('on', False) else '🔴 Söndürülüb'}\n"
        f"• <b>İzləmə Sahəsi:</b> {'🔒 Yalnız Şəxsi (DM)' if c.get('mode') == 'dm' else '🌐 Bütün Çatlar'}\n"
        f"• <b>Media Tutma:</b> {'✅ Aktiv' if c.get('save_media', True) else '🔴 Söndürülüb'}\n"
        f"• <b>Keşdəki Mesajlar:</b> {len(_msg_cache)} · media: {n_media} fayl ({mb:.1f} MB)\n"
        f"• <b>Tutulan Silinmiş Mesajlar:</b> {c.get('caught', 0)} ədəd\n"
        f"• <b>Tutulan Medialar:</b> {c.get('media_caught', 0)} ədəd\n\n"
        "<i>Silinən mesajlar «Saved Messages»-ə göndərilir.</i>"
    )
    return text + (f"\n\n{note}" if note else "")


def layout(state: AntiDeleteState) -> list:
    """[[(yazı, əməl), ...], ...] — həm .menu bölməsi, həm .antidelete kartı eyni düymələri çəkir."""
    c = state.cfg
    dm = c.get("mode") == "dm"
    return [
        [(f"Anti-Delete: {'🟢 Aktiv' if c.get('on', False) else '🔴 Deaktiv'}", "tg")],
        [(("✅ " if dm else "") + "🔒 Yalnız DM", "dm"), (("" if dm else "✅ ") + "🌐 Bütün çatlar", "all")],
        [(f"📎 Media: {'🟢' if c.get('save_media', True) else '🔴'}", "media"), ("🧹 Keşi təmizlə", "clear")],
        [("♻️ Statistikanı sıfırla", "stats0"), ("🔄 Yenilə", "r")],
    ]


def apply(state: AntiDeleteState, action: str) -> str:
    """Düymə əməli → bildiriş mətni (tanınmayan / «r» — boş). Ayarlar dərhal bazaya yazılır."""
    c = state.cfg
    if action == "tg":
        c["on"] = not c.get("on", False)
        note = "✅ Anti-Delete aktivləşdirildi" if c["on"] else "🔴 Anti-Delete söndürüldü"
    elif action in ("dm", "all"):
        c["mode"] = action
        note = "🔒 Rejim: yalnız şəxsi (DM) söhbətlər" if action == "dm" else "🌐 Rejim: bütün çatlar (DM + qruplar)"
    elif action == "media":
        c["save_media"] = not c.get("save_media", True)
        note = "📎 Media saxlanır" if c["save_media"] else "📎 Media saxlanmır (yalnız mətn)"
    elif action == "clear":
        n_msgs, n_files = clear_cache()
        return f"🧹 Keş təmizləndi: {n_msgs} mesaj, {n_files} media faylı"
    elif action == "stats0":
        c["caught"] = c["media_caught"] = 0
        note = "♻️ Statistika sıfırlandı"
    else:
        return ""
    state.save()
    return note


def register(ub):
    state = AntiDeleteState(getattr(ub, "_udb", None))

    @ub.raw(events.NewMessage(incoming=True))
    async def on_incoming_message(event):
        if not state.cfg.get("on", False):
            return

        msg = event.message
        if not msg:
            return

        chat_id = event.chat_id
        is_private = event.is_private

        # Əgər rejim "dm"-dirsə və qrup mesajıdırsa — keşləmə
        if state.cfg.get("mode") == "dm" and not is_private:
            return

        # Botlardan və ya Telegram xidmətindən (777000) gələn servis bildirişlərini keşləməmək üçün
        sender = await event.get_sender()
        if getattr(sender, "id", None) == 777000 or (getattr(sender, "bot", False) and getattr(sender, "id", None) == 777000):
            return

        sender_id = getattr(sender, "id", 0) if sender else 0
        s_first = getattr(sender, "first_name", "") or ""
        s_last = getattr(sender, "last_name", "") or ""
        sender_name = " ".join(x for x in (s_first, s_last) if x).strip() or getattr(sender, "title", None) or "Naməlum"
        sender_username = f"@{sender.username}" if getattr(sender, "username", None) else ""

        chat = await event.get_chat()
        chat_title = getattr(chat, "title", None) or ("Şəxsi Söhbət" if is_private else str(chat_id))

        media_kind = _detect_media_kind(msg)
        media_path = None

        # Media endirilməsi (əgər icazə verilibsə və fayl çox böyük deyilsə)
        if state.cfg.get("save_media", True) and media_kind:
            file_size = getattr(msg.file, "size", 0) if hasattr(msg, "file") else 0
            if file_size <= MAX_MEDIA_MB * 1024 * 1024:
                try:
                    os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)
                    fname = f"{chat_id}_{msg.id}"
                    media_path = await event.download_media(file=os.path.join(MEDIA_CACHE_DIR, fname))
                    _clean_old_media()
                except Exception as e:
                    logger.debug(f"antidelete media endirmə xətası: {e}")

        # Keşə yaz
        data = {
            "chat_id": chat_id,
            "msg_id": msg.id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_username": sender_username,
            "chat_title": chat_title,
            "is_private": is_private,
            "text": msg.raw_text or "",
            "media_kind": media_kind,
            "media_path": media_path,
            "date_str": time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(time.time())),
            "ts": time.time(),
        }

        # Keş limitini idarə et
        while len(_msg_cache) >= MAX_CACHE_MSGS:
            old_k, old_v = _msg_cache.popitem(last=False)
            if old_v.get("media_path") and os.path.isfile(old_v["media_path"]):
                try:
                    os.remove(old_v["media_path"])
                except OSError:
                    pass

        _msg_cache[(chat_id, msg.id)] = data

        if is_private:
            while len(_mid_to_chat) >= MAX_CACHE_MSGS:
                _mid_to_chat.popitem(last=False)
            _mid_to_chat[msg.id] = chat_id

    @ub.raw(events.MessageDeleted())
    async def on_message_deleted(event):
        if not state.cfg.get("on", False):
            return

        del_ids = getattr(event, "deleted_ids", None) or ([event.deleted_id] if getattr(event, "deleted_id", None) else [])
        if not del_ids:
            return

        client = event.client or getattr(ub, "client", None)
        if not client:
            return

        cid = getattr(event, "chat_id", None)

        for mid in del_ids:
            target_cid = cid or _mid_to_chat.get(mid)
            _mid_to_chat.pop(mid, None)
            if not target_cid:
                continue

            cached = _msg_cache.pop((target_cid, mid), None)
            if not cached:
                continue

            # Əgər rejim DM-dirsə və qrup mesajıdırsa atla
            if state.cfg.get("mode") == "dm" and not cached["is_private"]:
                if cached.get("media_path") and os.path.isfile(cached["media_path"]):
                    try:
                        os.remove(cached["media_path"])
                    except OSError:
                        pass
                continue

            state.cfg["caught"] = state.cfg.get("caught", 0) + 1
            if cached.get("media_path"):
                state.cfg["media_caught"] = state.cfg.get("media_caught", 0) + 1
            state.save()

            sender_name = escape(cached["sender_name"])
            sender_id = cached["sender_id"]
            uname = f" ({escape(cached['sender_username'])})" if cached.get("sender_username") else ""
            sender_link = f'<a href="tg://user?id={sender_id}">{sender_name}</a>{uname}' if sender_id else sender_name

            chat_title = escape(cached["chat_title"])
            date_str = cached["date_str"]
            media_info = f"📎 <b>Media:</b> {cached['media_kind']}\n" if cached.get("media_kind") else ""
            msg_text = escape(cached["text"]).strip()
            text_block = f"\n📝 <b>Mətn:</b>\n<code>{msg_text}</code>" if msg_text else ""

            report = (
                "🗑 <b>Silinən Mesaj Aşkarlandı!</b>\n\n"
                f"👤 <b>Göndərən:</b> {sender_link}\n"
                f"💬 <b>Çat:</b> {chat_title}\n"
                f"📅 <b>Tarix:</b> <code>{date_str}</code>\n"
                f"{media_info}"
                f"{text_block}"
            )

            media_file = cached.get("media_path")
            try:
                if media_file and os.path.isfile(media_file):
                    # Telegram media caption limiti 1024 simvoldur
                    if len(report) > 1024:
                        short_caption = (
                            "🗑 <b>Silinən Mesaj Aşkarlandı!</b>\n\n"
                            f"👤 <b>Göndərən:</b> {sender_link}\n"
                            f"💬 <b>Çat:</b> {chat_title}\n"
                            f"📅 <b>Tarix:</b> <code>{date_str}</code>\n"
                            f"{media_info}"
                        )
                        await client.send_file(
                            "me",
                            media_file,
                            caption=short_caption,
                            parse_mode="html",
                        )
                        if text_block:
                            await client.send_message("me", text_block.strip(), parse_mode="html")
                    else:
                        await client.send_file(
                            "me",
                            media_file,
                            caption=report,
                            parse_mode="html",
                        )
                    try:
                        os.remove(media_file)
                    except OSError:
                        pass
                else:
                    await client.send_message("me", report, parse_mode="html")
            except Exception as e:
                logger.warning(f"antidelete bildirişi göndərilmədi: {e}")

    @ub.command(
        "antidelete",
        pattern=r"^\.(?:antidelete|antidel)(?:\s+(.+))?$",
        help=(
            "silinən mesajları tutmaq",
            "Şəxsi çatlarda və ya qruplarda silinən mesajları dərhal Saved Messages-ə göndərir.\n\n"
            "<b>İstifadə:</b>\n"
            "• <code>.antidelete</code> — vəziyyət və statistikalar\n"
            "• <code>.antidelete on / off</code> — aktivləşdir / söndür\n"
            "• <code>.antidelete dm</code> — yalnız şəxsi (DM) çatlarda işləsin\n"
            "• <code>.antidelete all</code> — bütün çatlarda işləsin\n"
            "• <code>.antidelete media on / off</code> — şəkil və səslərin saxlanmasını tənzimlə\n"
            "• <code>.antidelete clear</code> — keşi təmizlə",
        ),
        category="acc",
    )
    async def on_antidelete_cmd(event):
        arg = (event.pattern_match.group(1) or "").strip().lower()

        if arg in ("on", "aktiv", "aç", "ac"):
            state.cfg["on"] = True
            state.save()
            await temp_edit(event, "✅ <b>Anti-Delete aktivləşdirildi!</b>\nSilinən mesajlar 'Saved Messages'-ə göndəriləcək.", delay=3)
            return

        if arg in ("off", "deaktiv", "bağla", "bagla"):
            state.cfg["on"] = False
            state.save()
            await temp_edit(event, "🔴 <b>Anti-Delete söndürüldü.</b>", delay=3)
            return

        if arg in ("dm", "pm", "şəxsi", "sexsi"):
            state.cfg["mode"] = "dm"
            state.cfg["on"] = True
            state.save()
            await temp_edit(event, "🔒 <b>Anti-Delete rejimi:</b> Yalnız şəxsi (DM) söhbətlər.", delay=3)
            return

        if arg in ("all", "hərkəs", "hamısı"):
            state.cfg["mode"] = "all"
            state.cfg["on"] = True
            state.save()
            await temp_edit(event, "🌐 <b>Anti-Delete rejimi:</b> Bütün çatlar (DM + Qruplar).", delay=3)
            return

        if arg in ("media on", "media_on"):
            state.cfg["save_media"] = True
            state.save()
            await temp_edit(event, "📎 <b>Media yaddaşı:</b> Aktiv (Şəkillər, səslər, video qeydlər tutulacaq).", delay=3)
            return

        if arg in ("media off", "media_off"):
            state.cfg["save_media"] = False
            state.save()
            await temp_edit(event, "📎 <b>Media yaddaşı:</b> Söndürüldü (Yalnız mətnlər tutulacaq).", delay=3)
            return

        if arg in ("clear", "təmizlə", "temizle"):
            clear_cache()
            await temp_edit(event, "🧹 <b>Anti-Delete keşi təmizləndi!</b>", delay=2)
            return

        # Arqumentsiz: düymələrlə kart (inline mümkün deyilsə sadə mətn)
        if getattr(ub, "cards", None) is None:
            await safe_edit(event, status_text(state))
            return
        out = Out(ub, event)

        def rows_for(o):                                  # 🔄 kartın öz sətrindədir (Out.kb) — təkrar düymə yox
            return [r for r in ([o.btn(label, action) for label, action in row if action != "r"] for row in layout(state)) if r]

        def make(action):
            async def handler(o, cb):
                await o.update(status_text(state, apply(state, action)), rows=rows_for(o))
            return handler
        for _, action in (b for row in layout(state) for b in row if b[1] != "r"):
            out.on(action)(make(action))
        out.refresh = lambda o: o.update(status_text(state), rows=rows_for(o))
        await out.open(status_text(state), rows_for(out))

    # ───────── 🛡 .menu → ⚡ Funksiyalar bölməsi (eyni düymələr) ─────────
    def section_view(sctx, note=""):
        rows = [[sctx.btn(sctx.tr(label), action) for label, action in row] for row in layout(state)]
        rows.append(sctx.nav())
        return status_text(state, note), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.section("antidelete", "🛡 Anti-Delete", order=43)
    async def render_section(sctx):
        return section_view(sctx)

    @ub.on_section("antidelete")
    async def handle_section(sctx):
        note = apply(state, sctx.action)
        await sctx.answer()
        await sctx.show(*section_view(sctx, note))
