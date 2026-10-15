"""
bot.music.inline
Teleqram İnline Axtarış və Çatdırılma mühərriki:
- norm_query, is_cached, yt_lookup, mix_lookup
- Axtarış keşlənməsi və debouncing
- Depo mahnılarının dərhal seçilməsi (dp:<id>)
- chosen_inline_result emalı və audio çatdırılması
"""

import asyncio
import contextlib
from html import escape
import logging
import os
import re
import threading
import time

from aiogram import F, types
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputMediaAudio,
    InputTextMessageContent,
)

from common.delivery import is_audio_forbidden_error

from bot.core import audio_cache, inline_settings as IS, song_names, youtube_handler as yt_handler
from bot.core.youtube_handler import DownloadCancelled
from common import activity
from common.i18n import _ as tr
from common.utilities import spawn

logger = logging.getLogger(__name__)

search_cache = {}
search_inflight = {}
latest_inline = {}
inline_cache = {}
mix_cache = {}
mix_inflight = {}
early_stops = {}
download_jobs = {}
download_sem = asyncio.Semaphore(2)

inline_stats = {
    "queries": 0,
    "cache_hits": 0,
    "searches": 0,
    "errors": 0,
    "timeouts": 0,
    "depo_first": 0,
    "last": 0.0,
    "total": 0.0,
    "answered": 0,
}

chat_stats = {
    "queries": 0,
    "cache_hits": 0,
    "errors": 0,
    "last": 0.0,
    "total": 0.0,
    "answered": 0,
}

mix_stats = {
    "queries": 0,
    "cache_hits": 0,
    "errors": 0,
    "last": 0.0,
    "total": 0.0,
    "answered": 0,
}


IS.set_stats_provider(lambda: {"inline": inline_stats, "chat": chat_stats, "mix": mix_stats})   # 🤖 Avto ayarlar


def norm_query(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


def is_cached(text: str, src: str, limit: int) -> bool:
    c = search_cache.get(f"{src}:{norm_query(text)}")
    return bool(c and time.time() - c[0] < IS.get("ttl") * 60 and c[2] >= limit)


def inline_cache_clear() -> int:
    n = len(search_cache)
    search_cache.clear()
    mix_cache.clear()
    return n


_yt_lookup = None
_mix_lookup = None


async def yt_lookup(text: str, src: str, limit: int = None, fast: bool = True) -> list:
    if _yt_lookup is not None:
        return await _yt_lookup(text, src, limit=limit, fast=fast)
    return []


async def mix_lookup(seed: str, limit: int = None) -> list:
    if _mix_lookup is not None:
        return await _mix_lookup(seed, limit=limit)
    return []


def register_inline_system(
    dp,
    context,
    fetch_audio_fn,
    parse_title_fn,
    build_caption_fn,
    build_keyboard_fn,
    stop_keyboard_fn,
    hide_keyboard_later_fn,
    log_download_fn,
    video_id_from_url_fn,
    name_hits_fn,
    mark_named_fn,
):
    STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID") or context.creator_id)

    context.inline_stats = inline_stats
    context.chat_search_stats = chat_stats
    context.mix_stats = mix_stats
    context.inline_cache_clear = inline_cache_clear
    context.inline_cache_size = lambda: len(search_cache)

    async def warm_search():
        await asyncio.sleep(20)
        with contextlib.suppress(Exception):
            await context.youtube_manager.youtube_search("music", limit=1, fast=True)
            logger.info("⚡ Inline axtarış isidildi")

    with contextlib.suppress(RuntimeError):
        spawn(warm_search())

    async def yt_lookup(text: str, src: str, limit: int = None, fast: bool = True) -> list:
        limit = limit or IS.get("limit")
        key = f"{src}:{norm_query(text)}"
        cached = search_cache.get(key)
        if cached and time.time() - cached[0] < IS.get("ttl") * 60 and cached[2] >= limit:
            return cached[1][:limit]
        fkey = (key, limit, fast)
        task = search_inflight.get(fkey)
        if task is None:

            async def do():
                try:
                    inline_stats["searches"] += 1
                    res = await context.youtube_manager.youtube_search(text, limit=limit, source=src, fast=fast)
                    old = search_cache.get(key)
                    if not old or old[2] <= limit or time.time() - old[0] >= IS.get("ttl") * 60:
                        search_cache[key] = (time.time(), res, limit)
                    while len(search_cache) > 2000:
                        search_cache.pop(next(iter(search_cache)))
                    return res
                finally:
                    search_inflight.pop(fkey, None)

            task = search_inflight[fkey] = asyncio.ensure_future(do())
        return await asyncio.shield(task)

    async def mix_lookup(seed: str, limit: int = None) -> list:
        limit = limit or IS.get("mix_size")
        t0 = time.monotonic()
        mix_stats["queries"] += 1
        c = mix_cache.get(seed)
        if c and time.time() - c[0] < IS.get("mix_ttl") * 60 and c[2] >= limit:
            mix_stats["cache_hits"] += 1
            return c[1][:limit]
        fkey = (seed, limit)
        task = mix_inflight.get(fkey)
        if task is None:

            async def do():
                try:
                    res = await context.youtube_manager.youtube_mix(
                        seed, limit=limit, fast=bool(IS.get("mix_fast"))
                    )
                    max_min = IS.get("mix_long")
                    if max_min:
                        res = [r for r in res if not r.get("raw_duration") or int(r["raw_duration"]) <= max_min * 60]
                    try:
                        vids = [video_id_from_url_fn(r.get("url")) for r in res]
                        cached = await asyncio.to_thread(audio_cache.cached_ids, [v for v in vids if v])
                        named = await asyncio.to_thread(name_hits_fn, res, cached)
                        for r, v in zip(res, vids):
                            if v in cached:
                                r["depo"] = True
                            elif v in named:
                                mark_named_fn(r, named[v])
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/music/inline.py]: %r", _e)
                    mix_cache[seed] = (time.time(), res, limit)
                    while len(mix_cache) > 200:
                        mix_cache.pop(next(iter(mix_cache)))
                    return res
                except Exception:
                    mix_stats["errors"] += 1
                    raise
                finally:
                    mix_inflight.pop(fkey, None)

            task = mix_inflight[fkey] = asyncio.ensure_future(do())
        res = await asyncio.shield(task)
        took = time.monotonic() - t0
        mix_stats.update(last=took, total=mix_stats["total"] + took, answered=mix_stats["answered"] + 1)
        return res[:limit]

    global _yt_lookup, _mix_lookup
    _yt_lookup = yt_lookup
    _mix_lookup = mix_lookup

    def make_article(res, in_depo: bool = False, mp3_bitrate: int = None):
        vid = video_id_from_url_fn(res.get("url"))
        if not vid:
            return None
        inline_cache[vid] = res
        if len(inline_cache) > 3000:
            inline_cache.pop(next(iter(inline_cache)))
        title = ("📦 " if in_depo else "") + res["title"]
        desc = f"⏱ {res.get('duration', '')}"
        wait_text = f"⏳ <b>{escape(title)}</b>\n<i>Yüklənir...</i>"
        if mp3_bitrate:
            desc += f" · 🎧 MP3 ({mp3_bitrate}k)"
            wait_text = f"⏳ <b>{escape(title)}</b>\n<i>🎧 MP3 ({mp3_bitrate} kbps) formatında hazırlanır...</i>"
        return InlineQueryResultArticle(
            id=vid,
            title=title[:100],
            description=desc,
            thumbnail_url=res.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            input_message_content=InputTextMessageContent(
                message_text=wait_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
            reply_markup=stop_keyboard_fn(f"inl_stop:{vid}"),
        )

    @dp.inline_query(~F.query.startswith(("ub:", "va:")))
    async def handle_inline_query(q: types.InlineQuery):
        text = (q.query or "").strip()
        if text.lower().startswith("mix:"):
            from common import tiers

            if not tiers.allowed("mix", q.from_user.id):
                try:
                    await q.answer(
                        [],
                        cache_time=5,
                        is_personal=True,
                        switch_pm_text=tr("💎 Mix yalnız Premium üçündür"),
                        switch_pm_parameter="premium",
                    )
                except Exception as _e:
                    logger.debug("udulan xəta [bot/music/inline.py]: %r", _e)
                return

            seed = text[4:].strip()
            if not re.fullmatch(r"[\w-]{11}", seed):
                return
            try:
                page = max(0, int(q.offset or 0))
            except ValueError:
                page = 0

            try:
                mix = await mix_lookup(seed)
            except Exception as e:
                logger.error(f"Inline mix xətası: {e}")
                return
            mp3_hook = getattr(context, "test_mp3_hook", None)
            is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(q.from_user.id))
            tester_br = mp3_hook.get_bitrate(user_id=q.from_user.id) if is_mp3_tester else None
            per = IS.get("mix_page")
            start = page * per
            chunk = mix[start : start + per]
            articles = [
                a
                for a in (
                    make_article(r, in_depo=bool(r.get("depo")), mp3_bitrate=tester_br)
                    for r in chunk
                )
                if a
            ]
            has_more = start + per < len(mix)
            try:
                await q.answer(
                    articles,
                    cache_time=60,
                    is_personal=is_mp3_tester,
                    next_offset=str(page + 1) if has_more else "",
                )
            except Exception as e:
                logger.warning(f"Inline mix answer alınmadı: {e}")
            return

        text = re.sub(r'["\'«»“”„‟`]', " ", text).strip()
        text = re.sub(r"\s+", " ", text).strip(" -:;,.")
        if len(text) < 2:
            return

        try:
            src = yt_handler.search_source()
        except Exception:
            src = getattr(yt_handler, "DEFAULT_SOURCE", "ytm")
        key = f"{src}:{norm_query(text)}"
        hit = search_cache.get(key)
        t_start = time.monotonic()
        if hit and time.time() - hit[0] < IS.get("ttl") * 60 and hit[2] >= IS.get("limit"):
            inline_stats["cache_hits"] += 1
        else:
            latest_inline[q.from_user.id] = q.id
            await asyncio.sleep(IS.get("debounce"))
            if latest_inline.get(q.from_user.id) != q.id:
                return
        inline_stats["queries"] += 1

        yt_task = asyncio.ensure_future(yt_lookup(text, src))
        depo_n = IS.get("depo")
        try:
            hits = await asyncio.to_thread(audio_cache.search_depo, text, depo_n) if depo_n else []
        except Exception:
            hits = []
        try:
            results = await asyncio.wait_for(
                asyncio.shield(yt_task), IS.get("ytwait") if hits else IS.get("timeout")
            )
        except asyncio.TimeoutError:
            results = None
            inline_stats["depo_first" if hits else "timeouts"] += 1
        except Exception as e:
            logger.error(f"Inline axtarış xətası: {e}")
            inline_stats["errors"] += 1
            results = []

        mp3_hook = getattr(context, "test_mp3_hook", None)
        is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(q.from_user.id))
        tester_br = mp3_hook.get_bitrate(user_id=q.from_user.id) if is_mp3_tester else None

        # sorğuya həqiqətən uyğun olmayan depo nəticələri (trigram / ILIKE təsadüfləri) önə çıxmasın
        auto_rank = bool(IS.get("rank"))            # 🤖 Avto: soruşulan mahnı uyğunluğa görə önə; 📦: depo həmişə yuxarıda
        hits = [h for h in hits if not auto_rank or song_names.query_score(text, h.get("performer") or "", h.get("title") or "") >= 0.7]
        depo_cards, depo_vids, depo_scores = [], set(), []
        for h in hits:
            depo_vids.add(h["video_id"])
            dur_sec = int(h.get("duration", 0) or 0)
            performer, track = (h.get("performer") or "").strip(), (h.get("title") or "").strip()
            h_art = {
                "url": f"https://www.youtube.com/watch?v={h['video_id']}",
                "title": f"{performer} - {track}".strip(" -") or "Musiqi",
                "duration": f"{dur_sec // 60}:{dur_sec % 60:02d}",
                "raw_duration": dur_sec,
                "thumbnail": f"https://i.ytimg.com/vi/{h['video_id']}/hqdefault.jpg",
                "depo": True,
            }
            if performer and performer != "YouTube" and track:
                h_art["meta"] = {"artist": performer, "track": track}
            art = make_article(h_art, in_depo=True, mp3_bitrate=tester_br)
            if art:
                depo_cards.append(art)
                depo_scores.append(song_names.query_score(text, performer, track) + 0.01)   # yalnız tam bərabərlikdə depo üstün
        yt = [r for r in (results or [])[: IS.get("limit")] if video_id_from_url_fn(r.get("url")) not in depo_vids]
        cached = await asyncio.to_thread(audio_cache.cached_ids, [video_id_from_url_fn(r.get("url")) for r in yt])
        named = await asyncio.to_thread(name_hits_fn, yt, cached)
        dup_files = {h["file_id"] for h in hits}
        yt2 = []
        for r in yt:
            v = video_id_from_url_fn(r.get("url"))
            if v in named:
                if named[v]["file_id"] in dup_files:
                    continue
                dup_files.add(named[v]["file_id"])
                r = dict(r)
                mark_named_fn(r, named[v])
            yt2.append(r)
        scored = list(zip(depo_scores, depo_cards))
        for rank, r in enumerate(yt2):
            a = make_article(r, video_id_from_url_fn(r.get("url")) in cached or r.get("depo"), mp3_bitrate=tester_br)
            if not a:
                continue
            artist, track = parse_title_fn(r.get("original_title") or r.get("title") or "")
            sc = song_names.query_score(text, artist or "", track or r.get("title") or "")
            if r.get("depo"):
                sc += 0.01
            elif song_names.non_music_reason(r.get("original_title") or r.get("title") or "",
                                              int(r.get("raw_duration") or 0), text):
                sc -= 1.0                       # xəbər, film fraqmenti, serial ... — siyahının sonuna
            scored.append((sc - 0.01 * rank, a))
        # soruşulan mahnı (ən yüksək uyğunluq) önə; bərabər uyğunluqda əvvəlki sıra saxlanır
        if auto_rank:
            scored.sort(key=lambda x: -x[0])
        articles = [a for _, a in scored]

        if not articles and results is None:
            return
        try:
            await q.answer(
                articles[:50],
                cache_time=1 if results is None else 20,
                is_personal=bool(depo_cards) or results is None or is_mp3_tester,
            )
            took = time.monotonic() - t_start
            inline_stats.update(
                last=took,
                total=inline_stats["total"] + took,
                answered=inline_stats["answered"] + 1,
            )
        except Exception as e:
            logger.warning(f"Inline answer alınmadı: {e}")

    @dp.callback_query(F.data == "inl_wait")
    async def handle_inline_wait(callback: types.CallbackQuery):
        await callback.answer(tr("Yüklənir, bir az gözləyin..."))

    @dp.callback_query(F.data.startswith("inl_stop"))
    async def handle_inline_stop(callback: types.CallbackQuery):
        iid = callback.inline_message_id
        if not iid:
            await callback.answer()
            return
        job = download_jobs.get(("inline", iid))
        if not job:
            if len(early_stops) > 500:
                early_stops.pop(next(iter(early_stops)))
            early_stops[iid] = callback.from_user.id
            await callback.answer(tr("⏹ Dayandırılır..."))
            return
        if callback.from_user.id not in (job["owner"], context.creator_id):
            await callback.answer(tr("⛔ Yalnız mahnını göndərən dayandıra bilər"), show_alert=True)
            return
        job["event"].set()
        await callback.answer(tr("⏹ Dayandırılır..."))
        try:
            await callback.bot.edit_message_text(
                inline_message_id=iid, text=tr("⏹ <i>Dayandırılır...</i>"), parse_mode="HTML"
            )
        except Exception as _e:
            logger.debug("udulan xəta [bot/music/inline.py]: %r", _e)

    async def audio_forbidden_fallback(
        inline_id, owner, title, fallback, res, url, sl, artist, track, duration
    ):
        bot = context.bot
        sent_pm = False
        if res and res.get("file_id"):
            try:
                await bot.send_audio(
                    owner,
                    res["file_id"],
                    title=track[:64],
                    performer=artist[:64],
                    duration=duration or None,
                    caption=build_caption_fn(url, sl, owner),
                    parse_mode="HTML",
                    reply_markup=build_keyboard_fn(url, sl, owner),
                )
                sent_pm = True
                if res.get("cached"):
                    await asyncio.to_thread(audio_cache.mark_hit, res["vid"])
                await log_download_fn(owner, f"{artist} - {track}", url, "inline")
            except Exception as e:
                logger.info(f"🔒 Şəxsi çata da göndərilmədi ({owner}): {e}")
        elif res and res.get("path") and os.path.isfile(res["path"]):
            try:
                local_path = res["path"]
                fname = res.get("fname") or f"{artist} - {track}.m4a"
                await bot.send_audio(
                    owner,
                    FSInputFile(local_path, filename=fname),
                    title=track[:64],
                    performer=artist[:64],
                    duration=duration or None,
                    caption=build_caption_fn(url, sl, owner),
                    parse_mode="HTML",
                    reply_markup=build_keyboard_fn(url, sl, owner),
                )
                sent_pm = True
                await log_download_fn(owner, f"{artist} - {track}", url, "inline")
            except Exception as e:
                logger.info(f"🔒 Şəxsi çata da göndərilmədi ({owner}): {e}")
        try:
            me = await bot.me()
            text = (
                f"🎵 <b>{escape(title)}</b>\n"
                + tr("🔒 Bu çatda musiqi göndərmək qadağandır (adminlərin icazəsi).")
                + "\n"
                + (
                    tr("📩 Mahnı şəxsi çatına göndərildi.")
                    if sent_pm
                    else tr("📩 Almaq üçün botu şəxsi çatda başlat.")
                )
                + f"\n{fallback}"
            )
            await bot.edit_message_text(
                inline_message_id=inline_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=tr("📩 Bota keç"), url=f"https://t.me/{me.username}")]
                    ]
                ),
            )
        except Exception as e2:
            logger.debug(f"🔒 İzah mesajı yazılmadı: {e2}")

    @dp.chosen_inline_result(~F.query.startswith(("ub:", "va:")))
    async def handle_chosen_inline(chosen: types.ChosenInlineResult):
        inline_id = chosen.inline_message_id
        if not inline_id:
            return

        bot = context.bot
        vid = chosen.result_id
        if not (vid.startswith("dp:") or re.fullmatch(r"[\w-]{11}", vid)):
            return
        if vid.startswith("dp:"):
            vid = vid[3:]
            await asyncio.to_thread(audio_cache.mark_hit, vid)
            c = await asyncio.to_thread(audio_cache.get_cached, vid) or {}
            activity.instant(
                "inl",
                chosen.from_user.id,
                f"{c.get('performer') or ''} - {c.get('title') or ''}".strip(" -") or vid,
                "cached",
            )
            await log_download_fn(
                chosen.from_user.id,
                f"{c.get('performer') or ''} - {c.get('title') or ''}".strip(" -"),
                f"https://www.youtube.com/watch?v={vid}",
                "inline",
            )
            hide_keyboard_later_fn(inline_message_id=inline_id)
            return
        info = inline_cache.get(vid) or {}
        url = info.get("url") or f"https://www.youtube.com/watch?v={vid}"
        title = info.get("title") or chosen.query
        artist, track = parse_title_fn(title)
        file_path = None
        sl = None
        owner = chosen.from_user.id
        job = {"event": threading.Event(), "owner": owner}
        download_jobs[("inline", inline_id)] = job
        early = early_stops.pop(inline_id, None)
        if early is not None and early in (owner, context.creator_id):
            job["event"].set()

        mp3_hook = getattr(context, "test_mp3_hook", None)
        is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(owner))

        try:
            duration = int(info.get("raw_duration", 0) or 0)
            for attempt in (1, 2):
                with activity.using("inl", owner):
                    res = await fetch_audio_fn(
                        url,
                        title,
                        duration,
                        job["event"],
                        need_file_id=not is_mp3_tester,
                        upload_depo=not is_mp3_tester,
                        fallback_chat=STORAGE_CHAT_ID,
                        sem=download_sem,
                        meta=info.get("meta"),
                        source=info.get("source") or "ytm",
                    )
                file_path = res["path"]
                sl, artist, track = res["sl"], res["artist"], res["track"]
                if job["event"].is_set():
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                try:
                    if is_mp3_tester:
                        upload_chat = owner or STORAGE_CHAT_ID
                        mp3_fid, bitrate = await mp3_hook.prepare_mp3_file_id(
                            bot=bot,
                            res=res,
                            artist=artist,
                            track_name=track,
                            duration=duration,
                            upload_chat_id=upload_chat,
                            user_id=owner,
                        )
                        if mp3_fid:
                            caption = build_caption_fn(url, sl, chosen.from_user.id)
                            await bot.edit_message_media(
                                inline_message_id=inline_id,
                                media=InputMediaAudio(
                                    media=mp3_fid,
                                    title=track[:64],
                                    performer=artist[:64],
                                    duration=duration or None,
                                    caption=caption,
                                    parse_mode="HTML",
                                ),
                                reply_markup=build_keyboard_fn(url, sl, owner),
                            )
                            hide_keyboard_later_fn(inline_message_id=inline_id)
                            break
                    await bot.edit_message_media(
                        inline_message_id=inline_id,
                        media=InputMediaAudio(
                            media=res["file_id"],
                            title=track[:64],
                            performer=artist[:64],
                            duration=duration or None,
                            caption=build_caption_fn(url, sl, chosen.from_user.id),
                            parse_mode="HTML",
                        ),
                        reply_markup=build_keyboard_fn(url, sl, owner),
                    )
                    hide_keyboard_later_fn(inline_message_id=inline_id)
                    break
                except Exception as e:
                    if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                        await audio_cache.recover_bad_file(context, res["vid"])
                        continue
                    raise
            if res["cached"]:
                await asyncio.to_thread(audio_cache.mark_hit, res["vid"])
            await log_download_fn(chosen.from_user.id, f"{artist} - {track}", url, "inline")

        except DownloadCancelled:
            try:
                await bot.edit_message_text(
                    inline_message_id=inline_id,
                    text=tr("⏹ <b>Yükləmə dayandırıldı</b>") + f"\n🎵 {escape(title)}",
                    parse_mode="HTML",
                )
            except Exception as e2:
                logger.debug(f"Dayandırma mesajı yazılmadı: {e2}")
        except Exception as e:
            err_str = str(e).upper()
            if "MESSAGE_ID_INVALID" in err_str:
                logger.debug(f"Inline mesaj silinib və ya etibarsızdır ({inline_id}): {e}")
                return
            fallback = (sl or {}).get("page") or f"https://song.link/{url}"
            if is_audio_forbidden_error(e) or "SEND_AUDIOS_FORBIDDEN" in err_str or "SEND_MEDIA_FORBIDDEN" in err_str:
                await audio_forbidden_fallback(
                    inline_id,
                    owner,
                    title,
                    fallback,
                    locals().get("res"),
                    url,
                    sl,
                    artist,
                    track,
                    duration,
                )
                return
            logger.error(f"Inline göndərmə xətası: {e}", exc_info=True)
            try:
                await bot.edit_message_text(
                    inline_message_id=inline_id,
                    text=f"🎵 <b>{escape(title)}</b>\n{fallback}\n\n"
                    + tr("<i>⚠️ Audio yüklənmədi: {error}</i>", error=escape(str(e))[:150]),
                    parse_mode="HTML",
                )
            except Exception as e2:
                if "MESSAGE_ID_INVALID" not in str(e2).upper():
                    logger.error(f"Fallback da alınmadı: {e2}")
        finally:
            download_jobs.pop(("inline", inline_id), None)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/music/inline.py]: %r", _e)
