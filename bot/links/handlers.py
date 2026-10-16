"""
🔗 bot/links/handlers.py - Musiqi linklərinin idarəsi və çatdırılması.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from html import escape

from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.links import is_music_link, list_kind_label, parse_link, yt_list_url
from bot.core.panel import btn, kb
from bot.links.parsers import (
    pick_image,
    resolve_short,
    sp_item,
    spotify_embed,
    total_duration,
    track_key,
    vk_artist_data,
    vk_playlist_data,
    vk_track_data,
    vk_video_data,
    vk_wall_data,
    youtube_oembed,
)
from common.i18n import _ as tr
from common.utilities import spawn

logger = logging.getLogger(__name__)

BATCH_MAX_DURATION = 15 * 60
MIX_LIMIT = 50
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def setup(context):
    dp = context.dp
    spotify = getattr(context, "spotify", None)
    pending = {}        # (chat_id, status_msg_id) -> təsdiq gözləyən siyahı / seçim

    def remember(key, value: dict):
        """pending-ə yaz; 1 saatdan köhnə və 500-dən artıq qeydlər atılır (yaddaş sızmasın)."""
        now = time.time()
        value["_ts"] = now
        for k in [k for k, v in pending.items() if now - v.get("_ts", now) > 3600]:
            pending.pop(k, None)
        while len(pending) >= 500:
            pending.pop(next(iter(pending)))
        pending[key] = value
    group_link_confirms = {}  # token -> {"parsed": ..., "message": ..., "uid": ..., "chat_id": ..., "ts": ..., "title": ...}
    # ── Spotify məlumatı: əvvəl API, alınmasa embed ──
    async def spotify_data(kind: str, sid: str) -> dict:
        api_error = None
        sp_inst = getattr(context, "spotify", None) or spotify
        if sp_inst and getattr(sp_inst, "sp", None):
            try:
                if kind == "track":
                    t = await asyncio.to_thread(sp_inst.get_track_data, sid)
                    return {"name": t["name"], "items": [sp_item(
                        t["name"], [a["name"] for a in t["artists"]], t["duration_ms"],
                        pick_image(t["album"].get("images")))]}
                if kind == "album":
                    a = await asyncio.to_thread(sp_inst.get_album_data, sid)
                    cover = pick_image(a.get("images"))
                    return {"name": f"{a['artist']} — {a['name']}", "items": [
                        sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"], cover)
                        for t in a["tracks"]]}
                if kind == "playlist":
                    p = await asyncio.to_thread(sp_inst.get_playlist_data, sid)
                    return {"name": p["name"], "items": [
                        sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"],
                                pick_image(t["album"].get("images")))
                        for t in p["tracks"] if t.get("name")]}
                if kind == "artist":
                    return await asyncio.to_thread(artist_full_sync, sid)
            except Exception as e:
                api_error = e
                logger.info(f"Spotify API ({kind}/{sid}) alınmadı, embed sınanır: {e}")
        try:
            return await spotify_embed(kind, sid)
        except Exception as e:
            raise RuntimeError(f"{api_error or e}")

    def ytm_top_songs(artist: str, limit: int = 10) -> list:
        """Spotify top-tracks bağlı olanda: YT Music-də ifaçının ən populyar mahnıları (adı uyğun gələnlər)."""
        try:
            from bot.core import ytdl_worker
            res = ytdl_worker.TASKS["ytm_search"](artist, 25) or []
        except Exception as e:
            logger.info(f"YT Music top mahnılar alınmadı ({artist}): {e}")
            return []
        want = artist.casefold().strip()
        out, seen = [], set()
        for r in res:
            names = [a.get("name", "") for a in (r.get("artists") or [])]
            if not any(want == n.casefold().strip() or want in n.casefold() for n in names):
                continue
            title = r.get("title") or ""
            k = track_key(title)
            if not k or k in seen:
                continue
            seen.add(k)
            dur = int(r.get("duration_seconds") or 0) * 1000
            thumbs = r.get("thumbnails") or []
            img = thumbs[-1].get("url") if thumbs else None
            out.append(sp_item(title, names, dur, img, None))
            if len(out) >= limit:
                break
        return out

    def artist_full_sync(sid: str) -> dict:
        """
        İfaçının albomları + sinqlları (yeni → köhnə), hər birinin mahnıları, təkrarsız.
        Kompilyasiyalar və "appears on" (başqasının albomunda iştirak) daxil deyil.
        """
        from bot.core.spotify_handler import endpoint_blocked, mark_forbidden
        sp_inst = getattr(context, "spotify", None) or spotify
        sp = getattr(sp_inst, "sp", None)
        if not sp:
            raise RuntimeError("Spotify API qoşulmayıb")
        art = sp.artist(sid)
        name = art.get("name", "İfaçı")
        top_items = []
        if not endpoint_blocked("artist_top_tracks"):
            try:
                top = sp.artist_top_tracks(sid, country="US").get("tracks", [])
                top_items = [sp_item(t["name"], [x["name"] for x in t["artists"]], t["duration_ms"],
                                     pick_image(t["album"].get("images")), t.get("id")) for t in top]
            except Exception as e:
                if not mark_forbidden("artist_top_tracks", e):
                    logger.info(f"Spotify top-tracks alınmadı ({sid}): {e}")
        if not top_items:
            top_items = ytm_top_songs(name)          # 403 olsa da 🔥 Top mahnılar YT Music-dən gəlsin

        albums, seen_album = [], set()
        res = None
        if not endpoint_blocked("artist_albums"):
            try:
                res = sp.artist_albums(sid, include_groups="album,single", country="US", limit=50)
            except Exception as e:
                if not mark_forbidden("artist_albums", e):
                    raise
        while res:
            for a in res.get("items", []):
                key = (a.get("name", "").lower(), a.get("album_type"))
                if key in seen_album:              # eyni albomun başqa bazar versiyası
                    continue
                seen_album.add(key)
                albums.append(a)
            res = sp.next(res) if res.get("next") else None
        # əvvəl albomlar, sonra sinqllar; hər qrupda yenidən köhnəyə
        albums = sorted([a for a in albums if a.get("album_type") == "album"],
                        key=lambda a: a.get("release_date") or "", reverse=True) + \
                 sorted([a for a in albums if a.get("album_type") != "album"],
                        key=lambda a: a.get("release_date") or "", reverse=True)

        items, seen_track, album_names = [], set(), []
        n_album = n_single = 0
        for a in albums:
            cover = pick_image(a.get("images"))
            res = sp.album_tracks(a["id"], limit=50)
            added = 0
            while res:
                for t in res.get("items", []):
                    k = track_key(t.get("name"))
                    if not k or k in seen_track:
                        continue
                    seen_track.add(k)
                    items.append(sp_item(t["name"], [x["name"] for x in t.get("artists", [])],
                                         t.get("duration_ms") or 0, cover, t.get("id")))
                    added += 1
                res = sp.next(res) if res.get("next") else None
            if added:
                if a.get("album_type") == "album":
                    n_album += 1
                    album_names.append(a.get("name"))
                else:
                    n_single += 1
        if not items and top_items:                     # albomlar bağlıdırsa — heç olmasa top mahnılar
            items = list(top_items)
        if not items:
            raise RuntimeError("ifaçının mahnıları alınmadı")
        return {"name": name, "items": items, "top": top_items, "albums": n_album, "singles": n_single,
                "album_names": album_names}

    async def show_artist_card(status: types.Message, message: types.Message, data: dict):
        items = data["items"]
        top = data.get("top") or []
        if not items and not top:
            await status.edit_text(tr("❌ Bu ifaçının mahnıları tapılmadı."))
            return
        await show_list_card(status, message, tr("🎤 Spotify ifaçısı"), f"{data['name']} — {tr('bütün mahnılar')}",
                             items or top, "spotify")
        p = pending.get((status.chat.id, status.message_id))
        if not p:
            return
        if top:
            p["alt_top"] = [it for it in top if (it.get("raw_duration") or 0) <= BATCH_MAX_DURATION]
            p["artist_name"] = data["name"]
        albums_line = ""
        if data.get("albums") is not None:
            names = ", ".join(escape(n[:30]) for n in (data.get("album_names") or [])[:6])
            albums_line = (tr("💿 {albums} albom · 🎵 {singles} sinql/EP", albums=data['albums'], singles=data.get('singles', 0))
                           + (f"\n<i>{names}{' …' if len(data.get('album_names') or []) > 6 else ''}</i>" if names else "")
                           + "\n" + tr("<i>Təkrarlar (deluxe, remaster) çıxarılıb.</i>") + "\n\n")
        else:
            albums_line = tr("<i>ℹ️ Spotify API qoşulmayıb — yalnız top mahnılar əlçatandır.</i>") + "\n\n"
        n = len(p["items"])
        dur = total_duration(p["items"])
        skip_line = "\n" + tr("<i>⏭ {skipped} uzun video (15 dəq.+) ötürüləcək</i>", skipped=p['skipped']) if p.get("skipped") else ""
        await status.edit_text(
            f"🎤 <b>{escape(data['name'])}</b>\n\n{albums_line}"
            + tr("📊 Cəmi: <b>{n}</b> mahnı · ⏳ {dur}", n=n, dur=dur)
            + skip_line,
            parse_mode="HTML",
            reply_markup=kb(
                [btn(tr("💿 Bütün mahnılar ({n})", n=n), "lk:go")],
                *([[btn(tr("🔥 Yalnız top mahnılar ({n})", n=len(p['alt_top'])), "lk:top")]] if p.get("alt_top") else []),
                [btn(tr("✖️ Ləğv et"), "lk:no")],
            ),
        )

    # ── siyahı kartı → təsdiq ──
    def save_row(uid) -> list:
        """💾 Playlist kimi saxla — 🎛 İmkanlar-da bu istifadəçiyə gizlidirsə göstərilmir."""
        if not getattr(context, "playlist_import", None):
            return []
        from common import tiers
        if not (tiers.visible("playlists", uid) and tiers.visible("playlist_import", uid)):
            return []
        badge = " 💎" if tiers.locked("playlist_import", uid) else ""
        return [[btn(tr("💾 Playlist kimi saxla") + badge, "lk:save")]]

    async def has_va_in_chat(chat_id: int, chat_type: str) -> bool:
        if chat_type not in ("group", "supergroup"):
            return False
        va = getattr(context, "voice_assistant", None)
        if not va or not getattr(va, "enabled", False) or not getattr(va, "has_session", lambda: False)():
            return False
        if chat_id in getattr(va, "chats", {}):
            return True
        try:
            if not getattr(va, "running", False):
                with contextlib.suppress(Exception):
                    await va.start()
            status = await va.member_status(chat_id)
            return status == "member"
        except Exception as e:
            logger.debug("has_va_in_chat check failed: %r", e)
            return False

    async def show_list_card(status: types.Message, message: types.Message, label: str, name: str,
                             items: list, source: str):
        seen, clean, skipped = set(), [], 0
        for it in items:
            key = it.get("url") or it.get("query")
            if not key or key in seen:
                continue
            seen.add(key)
            if (it.get("raw_duration") or 0) > BATCH_MAX_DURATION:
                skipped += 1
                continue
            clean.append(it)
        if not clean:
            await status.edit_text(tr("❌ Bu siyahıda yüklənəcək mahnı tapılmadı."))
            return
        remember((status.chat.id, status.message_id), {
            "uid": message.from_user.id, "items": clean, "header": f"{label}: {name}", "name": name,
            "source": source, "skipped": skipped, "orig": message.message_id,
        })
        preview = "\n".join(f"{i}. {escape(it['title'][:55])}" for i, it in enumerate(clean[:8], 1))
        more = "\n<i>… " + tr("və daha {n}", n=len(clean) - 8) + "</i>" if len(clean) > 8 else ""
        note = (
            "\n" + tr("<i>ℹ️ Spotify ictimai rejimdə ilk 100 mahnını təqdim edir.</i>") + "\n"
            + tr("💡 Məsləhət: Spotify API kənar playlist-lərə 100 limiti qoyur. Bütün mahnıları (100+) yükləmək üçün mahnıları Spotify-da öz yaratdığınız playlist-ə əlavə edin.")
        ) if source == "spotify" and len(clean) == 100 else ""
        from common import tiers
        uid = message.from_user.id
        dlall_badge = " 💎" if (len(clean) > 1 and not tiers.allowed("dlall", uid) and tiers.visible("dlall", uid)) else ""
        dur = total_duration(clean)
        skip_line = "\n" + tr("<i>⏭ {skipped} uzun video (15 dəq.+) ötürüləcək</i>", skipped=skipped) if skipped else ""
        chat = getattr(message, "chat", None) or status.chat
        has_va = await has_va_in_chat(chat.id, str(getattr(chat.type, "value", chat.type)))
        vplay_row = [[btn(tr("🎙 Canlı yayımda oxut"), "lk:vplay")]] if has_va else []
        await status.edit_text(
            f"{label}\n<b>{escape(name[:100])}</b>\n\n"
            + tr("📊 Cəmi: <b>{n}</b> mahnı · ⏳ {dur}", n=len(clean), dur=dur)
            + skip_line
            + f"\n\n{preview}{more}{note}",
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=kb([btn(tr("⬇️ Hamısını yüklə ({n})", n=len(clean)) + dlall_badge, "lk:go")],
                            *vplay_row,
                            *save_row(uid),
                            [btn(tr("✖️ Ləğv et"), "lk:no")]),
        )

    async def start_single(status: types.Message, message: types.Message, item: dict, source: str):
        run_batch = getattr(context, "music_run_batch", None)
        if not run_batch:
            await status.edit_text(tr("❌ Yükləmə modulu (music_plugin) yüklənməyib."))
            return
        try:
            await run_batch(status, [item], item["title"], message.from_user.id, source, single=True)
        except Exception as e:
            logger.warning(f"Link tək mahnı yükləmə xətası: {e}")
        finally:
            with contextlib.suppress(Exception):
                await status.delete()

    # ── link mesajı ──
    def premium_button(message: types.Message):
        """Şəxsi çatda — premium bələdçisi (sg:premium); qrupda — botun şəxsi çatına keçid."""
        if message.chat.type == "private":
            return kb([btn(tr("💎 Premium al"), "sg:premium")])
        username = getattr(context, "bot_username", None) or os.getenv("BOT_USERNAME", "dllmasterbot").lstrip("@")
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=tr("💎 Premium al"), url=f"https://t.me/{username}?start=premium")]])

    async def not_reserved(message: types.Message) -> bool:
        """Link başqa iş üçün gəlibsə (məs. depo doldurucusuna mənbə) — yükləmə etmə."""
        waiting = getattr(context, "depo_waiting", None)
        return not (waiting and message.from_user and waiting(message.from_user.id))

    # "/depo add <link>" kimi komandalar — öz plugin-lərinə aiddir
    @dp.message(F.text.func(is_music_link), ~F.text.startswith("/"), not_reserved)
    async def handle_link(message: types.Message):
        parsed = parse_link(message.text)
        if not parsed or not message.from_user:
            return

        # ── Qrupda linklə yükləmə tənzimləməsi (auto / confirm / off) ──
        mp3_hook = getattr(context, "test_mp3_hook", None)
        lmode = "auto"
        if message.chat.type != "private":
            chat_id = message.chat.id
            if mp3_hook and hasattr(mp3_hook, "get_group_link_mode"):
                lmode = mp3_hook.get_group_link_mode(chat_id)
            if lmode == "off":
                logger.info(f"🔗 Qrupda ({chat_id}) linklə yükləmə söndürülüb: {message.text[:50]}")
                return

        # Qısa linkləri aç
        if parsed["kind"] in ("sp_short", "vk_short"):
            is_vk_short = parsed["kind"] == "vk_short"
            resolved = await resolve_short(parsed["url"])
            if not resolved:
                err_txt = tr("❌ VK qısa linki açılmadı.") if is_vk_short else tr("❌ Spotify qısa linki açılmadı. Tam linki (open.spotify.com/...) göndər.")
                await message.reply(err_txt)
                return
            parsed = resolved

        # 💎 Spotify / YouTube / Playlist / Mix — /menu → 💎 Premium → 🎛 İmkanlar
        from common import tiers
        uid = message.from_user.id
        kind = parsed["kind"]
        feature = None

        if kind == "sp_playlist":
            feature = "spotify_playlists"
        elif kind.startswith("sp_") and kind != "sp_short":
            feature = "spotify_links"
        elif kind == "yt_list":
            list_id = parsed.get("list") or ""
            feature = "mix" if (list_id.startswith("RD") and not list_id.startswith("RDCLAK")) else "youtube_playlists"
        elif kind == "yt_video":
            feature = "youtube_links"
        elif kind in ("vk_playlist", "vk_album"):
            feature = "vk_playlists"
        elif kind.startswith("vk_") and kind != "vk_short":
            feature = "vk_links"
        elif kind == "yt_video_list":
            list_id = parsed.get("list") or ""
            list_f = "mix" if (list_id.startswith("RD") and not list_id.startswith("RDCLAK")) else "youtube_playlists"
            one_ok = await asyncio.to_thread(tiers.allowed, "youtube_links", uid)
            all_ok = await asyncio.to_thread(tiers.allowed, list_f, uid)
            if not one_ok and not all_ok:
                await message.reply(tiers.gate_text("youtube_links", uid), parse_mode="HTML",
                                    disable_web_page_preview=True, reply_markup=premium_button(message))
                return

        if feature:
            if not await asyncio.to_thread(tiers.allowed, feature, uid):
                await message.reply(tiers.gate_text(feature, uid), parse_mode="HTML", disable_web_page_preview=True,
                                    reply_markup=premium_button(message))
                return

        # ── Qrupda təsdiq mesajı (confirm) rejimi ──
        if message.chat.type != "private" and lmode == "confirm":
            title = None
            k = parsed.get("kind", "")
            try:
                if k in ("yt_video", "yt_video_list"):
                    info = await youtube_oembed(parsed["id"])
                    title = info.get("title")
                elif k == "yt_list":
                    title = "YouTube Pleylist"
                elif k.startswith("sp_"):
                    sp_k = k[3:]
                    sp_data = await spotify_data(sp_k, parsed["id"])
                    if sp_k == "track" and sp_data.get("items"):
                        title = sp_data["items"][0].get("title")
                    else:
                        title = sp_data.get("name")
                elif k == "vk_audio":
                    vk_it = await vk_track_data(parsed["owner_id"], parsed["id"], parsed.get("access_hash"))
                    title = vk_it.get("title")
                elif k in ("vk_playlist", "vk_album"):
                    vk_pl = await vk_playlist_data(parsed["owner_id"], parsed["id"], parsed.get("access_hash"))
                    title = vk_pl.get("name")
                elif k == "vk_video":
                    title = "VK Video"
            except Exception as e:
                logger.debug(f"Qrup preview xətası: {e}")

            if not title:
                title = parsed.get("url") or message.text.strip()[:60]
            title = title[:100]

            is_mp3 = bool(mp3_hook and mp3_hook.is_active_for(user_id=uid, chat_id=message.chat.id))
            if is_mp3:
                br = mp3_hook.get_bitrate(chat_id=message.chat.id, user_id=uid)
                fmt_desc = f"<b>{br} kbps MP3</b> formatında"
            else:
                fmt_desc = "audio formatında"

            token = uuid.uuid4().hex[:10]
            group_link_confirms[token] = {
                "parsed": parsed,
                "message": message,
                "uid": uid,
                "chat_id": message.chat.id,
                "ts": time.time(),
                "title": title,
            }

            confirm_text = (
                f"🎵 <b>{escape(title)}</b>\n\n"
                f"<i>Bu mahnını {fmt_desc} yükləmək istəyirsiniz?</i>\n\n"
                f"👤 Göndərən: {message.from_user.mention_html()}"
            )
            confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📥 Yüklə", callback_data=f"mp3_lk:dl:{token}"),
                    InlineKeyboardButton(text="✖️ Ləğv et", callback_data=f"mp3_lk:no:{token}")
                ]
            ])
            await message.reply(confirm_text, parse_mode="HTML", reply_markup=confirm_kb, disable_web_page_preview=True)
            return

        maint_prefix = ""
        from common.database import is_maintenance_mode
        if is_maintenance_mode() and uid != getattr(context, "creator_id", 0):
            maint_prefix = tr("⚠️ <i>Botda texniki sazlama işləri aparılır. Qəfil yükləmələr və ya cavab dayana bilər.</i>") + "\n\n"
        status = await message.reply(f"{maint_prefix}" + tr("🔎 <i>Link yoxlanılır...</i>"), parse_mode="HTML")
        try:
            await process(parsed, status, message)
        except Exception as e:
            logger.warning(f"Link xətası ({message.text[:80]}): {e}")
            try:
                await status.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/links_plugin.py:775]: %r", _e)

    async def process(parsed: dict, status: types.Message, message: types.Message):
        kind = parsed["kind"]

        if kind == "sp_jam":
            await status.edit_text(
                tr("🎧 <b>Spotify Jam</b> canlı sessiyadır — Spotify onun növbəsini kənar tətbiqlərə açmır.\n\n<i>Jam-dakı mahnının, playlistin və ya albomun linkini göndər — yükləyim.</i>"),
                parse_mode="HTML",
            )
            return

        if kind == "yt_video":
            info = await youtube_oembed(parsed["id"])
            title = info.get("title") or f"YouTube {parsed['id']}"
            await start_single(status, message, {
                "title": title, "url": f"https://www.youtube.com/watch?v={parsed['id']}", "raw_duration": 0,
            }, "youtube")
            return

        if kind == "yt_video_list":
            info = await youtube_oembed(parsed["id"])
            remember((status.chat.id, status.message_id), {
                "uid": message.from_user.id, "parsed": parsed, "title": info.get("title"), "orig": message.message_id,
            })
            list_id = parsed.get("list") or ""
            list_f = "mix" if (list_id.startswith("RD") and not list_id.startswith("RDCLAK")) else "youtube_playlists"
            from common import tiers
            uid = message.from_user.id
            one_badge = " 💎" if (not tiers.allowed("youtube_links", uid) and tiers.visible("youtube_links", uid)) else ""
            all_badge = " 💎" if (not tiers.allowed(list_f, uid) and tiers.visible(list_f, uid)) else ""
            await status.edit_text(
                f"🎵 <b>{escape(info.get('title') or tr('Bu video'))}</b>\n"
                f"{list_kind_label(parsed['list'])}.\n\n<i>" + tr("Nəyi yükləyək?") + "</i>",
                parse_mode="HTML",
                reply_markup=kb([btn(tr("🎵 Yalnız bu mahnı") + one_badge, "lk:one"), btn(tr("📃 Bütün siyahı") + all_badge, "lk:all")],
                                [btn(tr("✖️ Ləğv et"), "lk:no")]),
            )
            return

        if kind == "yt_list":
            await open_yt_list(status, message, parsed["list"], None)
            return

        if kind.startswith("sp_"):
            sp_kind = kind[3:]
            if sp_kind == "artist":
                await status.edit_text(tr("🎤 <i>İfaçının albomları toplanır... (bir az çəkə bilər)</i>"), parse_mode="HTML")
            data = await spotify_data(sp_kind, parsed["id"])
            if not data["items"] and not data.get("top"):
                raise RuntimeError("Spotify-dan mahnı siyahısı alınmadı")
            if sp_kind == "track":
                await start_single(status, message, data["items"][0], "spotify")
                return
            if sp_kind == "artist":
                await show_artist_card(status, message, data)
                return
            label = {"album": tr("💿 Spotify albomu"), "playlist": tr("🎧 Spotify playlist"),
                     "artist": tr("🎤 Spotify ifaçısı")}.get(sp_kind, tr("🎧 Spotify"))
            if sp_kind == "playlist" and parsed["id"].startswith("37i9dQZF1E"):
                label = tr("📻 Spotify Radio / Mix")
            await show_list_card(status, message, label, data["name"], data["items"], "spotify")
            return

        if kind == "vk_video":
            item = await vk_video_data(parsed["url"])
            await start_single(status, message, item, "vk")
            return

        if kind == "vk_audio":
            item = await vk_track_data(parsed["owner_id"], parsed["id"], parsed.get("access_hash"))
            await start_single(status, message, item, "vk")
            return

        if kind in ("vk_playlist", "vk_album"):
            data = await vk_playlist_data(parsed["owner_id"], parsed["id"], parsed.get("access_hash"))
            if not data.get("items"):
                raise RuntimeError(tr("VK playlistində mahnı tapılmadı"))
            label = tr("💿 VK albomu") if kind == "vk_album" else tr("🎧 VK playlisti")
            await show_list_card(status, message, label, data["name"], data["items"], "vk")
            return

        if kind == "vk_artist":
            data = await vk_artist_data(parsed["slug"])
            if not data.get("items"):
                raise RuntimeError(tr("VK ifaçısının mahnıları tapılmadı"))
            await show_list_card(status, message, tr("🎤 VK ifaçısı"), data["name"], data["items"], "vk")
            return

        if kind == "vk_wall":
            data = await vk_wall_data(parsed["url"])
            if not data.get("items"):
                raise RuntimeError(tr("VK divar postunda audio və ya video tapılmadı"))
            if len(data["items"]) == 1:
                await start_single(status, message, data["items"][0], "vk")
            else:
                await show_list_card(status, message, tr("📝 VK postu"), data["title"], data["items"], "vk")
            return

    async def open_yt_list(status, message, list_id: str, video_id):
        vid = video_id
        if not vid and list_id.startswith("RDAMVM"):
            vid = list_id[6:]
        elif not vid and list_id.startswith("RD") and len(list_id) == 13:
            vid = list_id[2:]
        limit = MIX_LIMIT if list_id.startswith("RD") and not list_id.startswith("RDCLAK") else 5000
        await status.edit_text(tr("📃 <i>Siyahı açılır...</i>"), parse_mode="HTML")
        data = await context.youtube_manager.playlist_entries(yt_list_url(list_id, vid), limit=limit)
        if not data["entries"]:
            raise RuntimeError(tr("Siyahı boşdur və ya gizlidir"))
        await show_list_card(status, message, list_kind_label(list_id), data["title"], data["entries"], "playlist")

    @dp.callback_query(F.data.startswith("mp3_lk:"))
    async def group_link_confirm_callback(cb: types.CallbackQuery):
        # 1 saatdan köhnə sorğuları təmizlə
        now = time.time()
        for k in list(group_link_confirms.keys()):
            if now - group_link_confirms[k].get("ts", 0) > 3600:
                group_link_confirms.pop(k, None)

        parts = cb.data.split(":")
        if len(parts) < 3:
            await cb.answer()
            return

        act = parts[1]
        token = parts[2]
        item = group_link_confirms.get(token)
        if not item:
            await cb.answer(tr("Bu sorğunun vaxtı bitib və ya artıq cavablandırılıb."), show_alert=True)
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        chat_id = item["chat_id"]
        sender_uid = item["uid"]
        clicker_uid = cb.from_user.id
        creator_id = getattr(context, "creator_id", 0)

        is_sender = (clicker_uid == sender_uid)
        is_creator_user = (clicker_uid == creator_id)
        is_admin = False
        if not (is_sender or is_creator_user):
            try:
                m = await cb.bot.get_chat_member(chat_id, clicker_uid)
                status_val = str(getattr(m.status, "value", m.status)).lower()
                is_admin = status_val in ("administrator", "creator")
            except Exception:
                is_admin = False

        if not (is_sender or is_admin or is_creator_user):
            await cb.answer("👮 Bu sorğunu yalnız linki göndərən və ya qrup adminləri təsdiqləyə bilər!", show_alert=True)
            return

        if act == "no":
            group_link_confirms.pop(token, None)
            await cb.answer("❌ Yükləmə ləğv edildi")
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        elif act == "dl":
            group_link_confirms.pop(token, None)
            await cb.answer("⏳ Yükləmə başladılır...")
            status = cb.message
            try:
                await status.edit_text("⏳ <i>Yüklənir...</i>", parse_mode="HTML")
                await process(item["parsed"], status, item["message"])
            except Exception as e:
                logger.warning(f"Qrup link təsdiqli yükləmə xətası: {e}")
                with contextlib.suppress(Exception):
                    await status.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")

    @dp.callback_query(F.data.startswith("lk:"))
    async def link_callback(cb: types.CallbackQuery):
        key = (cb.message.chat.id, cb.message.message_id)
        p = pending.get(key)
        action = cb.data.split(":", 1)[1]
        if not p:
            await cb.answer(tr("Bu sorğu köhnəlib — linki yenidən göndər"), show_alert=True)
            return
        is_sender = cb.from_user.id == p["uid"]
        is_creator = cb.from_user.id == getattr(context, "creator_id", 0)
        is_admin = False
        if not (is_sender or is_creator) and cb.message.chat.type in ("group", "supergroup"):
            with contextlib.suppress(Exception):
                m = await cb.bot.get_chat_member(cb.message.chat.id, cb.from_user.id)
                is_admin = str(getattr(m.status, "value", m.status)) in ("administrator", "creator")
        if not (is_sender or is_creator or is_admin):
            await cb.answer(tr("⛔ Yalnız linki göndərən seçə bilər"), show_alert=True)
            return

        if action == "vplay":
            va = getattr(context, "voice_assistant", None)
            if not va or not getattr(va, "enabled", False) or not getattr(va, "has_session", lambda: False)():
                await cb.answer(tr("🔴 Canlı yayım asistanı söndürülüb və ya qoşulmayıb"), show_alert=True)
                return
            if getattr(va, "who", "all") == "admin" and not (is_creator or is_admin):
                await cb.answer(tr("👮 Yalnız qrup adminləri idarə edə bilər."), show_alert=True)
                return
            if not getattr(va, "running", False):
                err = await va.start()
                if err:
                    await cb.answer(f"⚠️ Asistan işləmir: {err[:150]}", show_alert=True)
                    return
            items = p.get("items") or []
            if not items:
                await cb.answer(tr("❌ Siyahıda mahnı yoxdur"), show_alert=True)
                return

            pending.pop(key, None)
            await cb.answer(tr("🎙 Canlı yayıma əlavə edilir..."))
            with contextlib.suppress(Exception):
                await cb.message.edit_text(
                    tr("🎙 <b>{name}</b>\n<i>Canlı yayıma əlavə edilir...</i>", name=escape(p.get("name") or "")),
                    parse_mode="HTML",
                )

            chat_id = cb.message.chat.id
            chat_title = cb.message.chat.title or ""
            by_user = cb.from_user.first_name or ""
            uid = cb.from_user.id
            source = p.get("source") or "playlist"

            async def _enqueue_all():
                try:
                    from bot.voice.models import Track
                    from bot.voice.engine import _VID_RE
                    from bot.core import audio_cache

                    enqueued_count = 0
                    first_track = None
                    remaining_tracks = []

                    for idx, it in enumerate(items):
                        url = it.get("url")
                        raw_dur = it.get("raw_duration") or 0
                        vid = None
                        if url:
                            m = _VID_RE.search(url)
                            if m:
                                vid = m.group(1)
                        c = await asyncio.to_thread(audio_cache.get_cached, vid) if vid else None
                        file_id = (c or {}).get("file_id") if c else None
                        dur = raw_dur or ((c or {}).get("duration") or 0)
                        title = it.get("title") or (f"{c['performer']} - {c['title']}" if c and c.get("performer") else (c or {}).get("title") or vid or "Naməlum")

                        if not url and not vid:
                            if idx == 0:
                                t = await va.resolve(it.get("query") or it.get("title") or title, by_user)
                                t.user_id = uid
                                first_track = t
                                continue
                            else:
                                remaining_tracks.append(it)
                                continue

                        t = Track(
                            title=title,
                            vid=vid,
                            url=url or (f"https://www.youtube.com/watch?v={vid}" if vid else None),
                            duration=dur,
                            file_id=file_id,
                            by=by_user,
                            depo=bool(c),
                            src=source,
                            user_id=uid,
                        )
                        if idx == 0:
                            first_track = t
                        else:
                            remaining_tracks.append(t)

                    if first_track:
                        await va.enqueue(chat_id, chat_title, first_track)
                        enqueued_count += 1

                    for it_or_t in remaining_tracks:
                        try:
                            if isinstance(it_or_t, Track):
                                t = it_or_t
                            else:
                                t = await va.resolve(it_or_t.get("query") or it_or_t.get("title") or "Naməlum", by_user)
                                t.user_id = uid
                            st = va.state(chat_id, chat_title)
                            st.queue.append(t)
                            va.prefetch(st.queue[0])
                            enqueued_count += 1
                        except Exception as it_err:
                            logger.warning(f"Playlist track əlavə edilmədi: {it_err}")

                    va.save_active_sessions()
                    st = va.chats.get(chat_id)
                    if st:
                        await va.refresh_np(st)

                    with contextlib.suppress(Exception):
                        await cb.message.edit_text(
                            tr("🎙 <b>{name}</b>\n✅ <b>{n}</b> mahnı canlı yayıma əlavə edildi!",
                               name=escape(p.get("name") or ""), n=enqueued_count),
                            parse_mode="HTML",
                        )
                except Exception as e:
                    msg = str(e)
                    low = msg.lower()
                    if "groupcall" in low.replace("_", "") or "no active" in low or "not in a call" in low:
                        msg = tr("Səsli yayım başlamayıb — qrupda video chat başladın (və ya asistanı \"Manage video chats\" admini edin)")
                    logger.warning(f"🎙 Playlist Canlı yayımda oxut xətası ({chat_id}): {e}", exc_info=True)
                    with contextlib.suppress(Exception):
                        await cb.message.edit_text(f"❌ <b>Xəta:</b> <code>{escape(msg[:300])}</code>", parse_mode="HTML")

            spawn(_enqueue_all())
            return

        if action == "no":
            pending.pop(key, None)
            await cb.answer(tr("Ləğv edildi"))
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/links_plugin.py:966]: %r", _e)
            return

        if action == "one":
            pending.pop(key, None)
            from common import tiers
            if not await asyncio.to_thread(tiers.allowed, "youtube_links", cb.from_user.id):
                await cb.answer(tr("💎 Bu imkan Premium tələb edir"), show_alert=False)
                with contextlib.suppress(Exception):
                    await cb.message.answer(tiers.gate_text("youtube_links", cb.from_user.id), parse_mode="HTML",
                                            reply_markup=premium_button(cb.message))
                return
            await cb.answer()
            await _single_for(cb, p, p["parsed"]["id"])
            return

        if action == "all":
            pending.pop(key, None)
            list_id = p["parsed"].get("list") or ""
            f = "mix" if (list_id.startswith("RD") and not list_id.startswith("RDCLAK")) else "youtube_playlists"
            from common import tiers
            if not await asyncio.to_thread(tiers.allowed, f, cb.from_user.id):
                await cb.answer(tr("💎 Bu imkan Premium tələb edir"), show_alert=False)
                with contextlib.suppress(Exception):
                    await cb.message.answer(tiers.gate_text(f, cb.from_user.id), parse_mode="HTML",
                                            reply_markup=premium_button(cb.message))
                return
            await cb.answer()
            try:
                await open_yt_list(cb.message, _Sender(p["uid"], p["orig"]), p["parsed"]["list"], p["parsed"]["id"])
            except Exception as e:
                await cb.message.edit_text(f"❌ <b>Xəta:</b> <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            return

        if action == "save":                       # 🎶 playlist_plugin — siyahı öz playlisti kimi
            imp = getattr(context, "playlist_import", None)
            if not imp or not p.get("items"):
                await cb.answer()
                return
            from common import tiers
            if not await asyncio.to_thread(tiers.allowed, "playlist_import", cb.from_user.id):
                await cb.answer(tr("💎 Playlist saxlamaq yalnız Premium üçündür"), show_alert=False)
                with contextlib.suppress(Exception):
                    await cb.message.answer(tiers.gate_text("playlist_import", cb.from_user.id), parse_mode="HTML",
                                            reply_markup=premium_button(cb.message))
                return
            if p.get("saved_by") == cb.from_user.id:
                await cb.answer(tr("ℹ️ Artıq saxlanılıb — /playlist"), show_alert=True)
                return
            await cb.answer(tr("💾 Saxlanılır..."))
            ok, text = await imp(cb.from_user.id, (p.get("name") or "")[:48], p["items"])
            if ok:
                p["saved_by"] = cb.from_user.id
            with contextlib.suppress(Exception):
                await cb.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
            return

        if action == "top":
            if not p.get("alt_top"):
                await cb.answer()
                return
            p["items"] = p["alt_top"]
            p["header"] = tr("🔥 {name} — top mahnılar", name=p.get('artist_name', tr('İfaçı')))
            p["skipped"] = 0
            action = "go"

        if action == "go":
            if len(p.get("items", [])) > 1:
                from common import tiers
                if not await asyncio.to_thread(tiers.allowed, "dlall", cb.from_user.id):
                    await cb.answer(tr("💎 Bütün mahnıları birdən yükləmək yalnız Premium üçündür"), show_alert=False)
                    with contextlib.suppress(Exception):
                        await cb.message.answer(tiers.gate_text("dlall", cb.from_user.id), parse_mode="HTML",
                                                reply_markup=premium_button(cb.message))
                    return
            busy = getattr(context, "music_batch_busy", None)
            if busy and busy(cb.from_user.id):
                await cb.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"), show_alert=True)
                return
            run_batch = getattr(context, "music_run_batch", None)
            if not run_batch:
                await cb.answer(tr("Yükləmə modulu yüklənməyib"), show_alert=True)
                return
            pending.pop(key, None)
            await cb.answer(tr("⬇️ {n} mahnı yüklənir...", n=len(p['items'])))
            await run_batch(cb.message, p["items"], p["header"], p["uid"], p["source"],
                            skipped=p["skipped"], original_message_id=None)
            return

        await cb.answer()

    async def _single_for(cb, p, vid):
        run_batch = getattr(context, "music_run_batch", None)
        if not run_batch:
            await cb.message.edit_text(tr("❌ Yükləmə modulu (music_plugin) yüklənməyib."))
            return
        item = {"title": p.get("title") or f"YouTube {vid}",
                "url": f"https://www.youtube.com/watch?v={vid}", "raw_duration": 0}
        await run_batch(cb.message, [item], item["title"], p["uid"], "youtube", single=True)

    class _Sender:
        """open_yt_list üçün minimal "mesaj" — kim göndərib, orijinal mesaj ID-si."""
        def __init__(self, uid, mid):
            self.from_user = type("U", (), {"id": uid})()
            self.message_id = mid

    context.links_spotify_data = spotify_data        # depo_filler_plugin üçün
    context.links_show_list_card = show_list_card
    context.links_callback = link_callback
    logger.info("✅ Links plugin yükləndi (YouTube + Spotify + VK)")

