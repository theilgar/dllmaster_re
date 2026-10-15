from aiogram import types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from common.utilities import fetch_telegram_thumb, format_duration, sanitize_filename, get_http_session
from bot.core.youtube_handler import YoutubeManager, DownloadCancelled
from bot.core import youtube_handler as yt_handler
from bot.core import audio_cache
from common import activity          # 📡 canlı monitor (/depotop) üçün istifadəçi yükləmələri
from bot.core import song_names
from bot.core import inline_settings as IS
from bot.core.links import is_music_link, video_id_from_url, media_id_from_url
from bot.music.inline import chat_stats, inline_cache, is_cached, mix_lookup, yt_lookup
from common.database import log_download, caption_for_user
from common.delivery import AudioDeliveryService

from common.i18n import _ as tr                       # 🌐 "_" bu faylda dəyişən adı kimi də işlənir
import asyncio
import contextlib
import threading
import os
import logging
import re
import time
import aiohttp
from html import escape
from common.utilities import spawn, send_temp, delete_after

logger = logging.getLogger(__name__)

# ───────────────────────── Odesli / Songlink ─────────────────────────
ODESLI_API = "https://api.song.link/v1-alpha.1/links"
odesli_cache = {}
odesli_disabled_until = 0  # 401 gəlsə (açar tələb olunur) 30 dəq. sorğu göndərmə
odesli_warned = False
# Ad/artist bu platformaların məlumatından götürülür (YouTube-un özününkü "Official Video" və s. ilə gəlir)
META_PLATFORMS = ("spotify", "appleMusic", "itunes", "deezer", "tidal", "amazonMusic", "napster", "pandora")


async def get_songlink(url: str):
    """Odesli-dən universal səhifə və platforma linklərini qaytarır (uğursuz olsa None)."""
    global odesli_disabled_until, odesli_warned
    if url in odesli_cache:
        return odesli_cache[url]
    now = time.time()
    if now < odesli_disabled_until and not os.getenv("ODESLI_API_KEY"):
        return None
    if now >= odesli_disabled_until:
        odesli_warned = False

    params = {"url": url, "songIfSingle": "true"}
    api_key = os.getenv("ODESLI_API_KEY")  # könüllü; açarsız limit ~10 sorğu/dəq
    if api_key:
        params["key"] = api_key

    try:
        session = await get_http_session()
        async with session.get(ODESLI_API, params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status in (401, 403):
                odesli_disabled_until = time.time() + 1800
                if not odesli_warned:
                    logger.warning("Odesli API açar tələb edir (ODESLI_API_KEY). Birbaşa song.link/<url> linki istifadə olunacaq.")
                    odesli_warned = True
                return None
            if r.status != 200:
                logger.warning(f"Odesli status {r.status} ({url})")
                return None
            data = await r.json()
    except Exception as e:
        logger.warning(f"Odesli xətası: {e}")
        return None

    title, artist = odesli_meta(data)
    result = {
        "page": data.get("pageUrl") or f"https://song.link/{url}",
        "links": {k: v.get("url") for k, v in data.get("linksByPlatform", {}).items()},
        "title": title,
        "artist": artist,
    }
    if len(odesli_cache) >= 1000:                       # sadə FIFO — keş sonsuz böyüməsin
        odesli_cache.pop(next(iter(odesli_cache)))
    odesli_cache[url] = result
    return result


BOT_USERNAME = os.getenv("BOT_USERNAME", "dllmasterbot").lstrip("@")


def build_caption(url: str, sl=None, user_id=None):
    """
    via | song.link — "via" botu açıb /start edir (t.me/<bot>?start=via),
    song.link — bütün platformalar səhifəsi. Söndürülübsə None.
    """
    if not caption_for_user(user_id):   # Free: həmişə · Premium: heç vaxt · Creator: /menu
        return None
    page = sl["page"] if sl else f"https://song.link/{url}"
    start = f"https://t.me/{BOT_USERNAME}?start=via"
    return (f'<i><a href="{escape(start, quote=True)}">via</a> | '
            f'<a href="{escape(page, quote=True)}">song.link</a></i>')


def build_keyboard(url: str, sl=None, owner_id: int = 0):
    from common.database import is_audio_button_enabled
    rows = []
    vid = video_id_from_url(url)
    first = []
    if vid:
        # 🔀 Mix düyməsi
        if is_audio_button_enabled("mix"):
            try:
                from common import tiers
                if tiers.visible("mix", owner_id):
                    badge = " 💎" if tiers.locked("mix", owner_id) else ""
                    first.append(InlineKeyboardButton(text="🔀 Mix" + badge, switch_inline_query_current_chat=f"mix:{vid}"))
            except Exception:
                first.append(InlineKeyboardButton(text="🔀 Mix", switch_inline_query_current_chat=f"mix:{vid}"))
        # ➕ playlistə əlavə et (playlist_plugin) — /menu → 💎 Premium → 🎶 Playlistlər
        if is_audio_button_enabled("plus"):
            try:
                from common import tiers
                if tiers.allowed("playlists", owner_id):
                    first.append(InlineKeyboardButton(text="➕", callback_data=f"pl:a:{vid}"))
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/music_plugin.py:124]: %r", _e)
    # Düymələri gizlətmək (yalnız mahnını istəyən və ya creator)
    if is_audio_button_enabled("hide"):
        first.append(InlineKeyboardButton(text=tr("🙈 Gizlət"), callback_data=f"hidekb:{owner_id}"))
    if first:
        rows.append(first)
    if sl:
        links = sl["links"]
        row = []
        for key, label in (("spotify", "Spotify"), ("appleMusic", "Apple Music"), ("youtubeMusic", "YT Music")):
            if links.get(key):
                row.append(InlineKeyboardButton(text=label, url=links[key]))
        if row:
            rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


BATCH_MAX_DURATION = 15 * 60      # toplu yükləmədə bundan uzun videolar (mix, albom) ötürülür
PAGE_SIZE = 5                     # axtarış / mix: bir səhifədə neçə mahnı
SEARCH_STEP = 25                  # son səhifəyə çatanda bu qədər əlavə nəticə gətirilir
SEARCH_MAX = 1000                 # sonsuz səhifələmənin təhlükəsizlik həddi


def download_all_row(count: int, user_id=None) -> list:
    """Axtarış / mix siyahısının altında: hamısını yüklə (🎛 İmkanlar-da gizlidirsə — düymə yoxdur)."""
    try:
        from common import tiers
        if not tiers.visible("dlall", user_id):
            return []
        badge = " 💎" if tiers.locked("dlall", user_id) else ""
    except Exception:
        badge = ""
    return [InlineKeyboardButton(text=tr("⬇️ Hamısını yüklə ({n})", n=count) + badge, callback_data="dlall")]


def stop_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr("⏹ Yükləməni dayandır"), callback_data=callback_data)
    ]])


def odesli_meta(data: dict):
    """Odesli cavabından musiqi platformasındakı rəsmi ad və artisti çıxarır."""
    entities = data.get("entitiesByUniqueId") or {}
    links = data.get("linksByPlatform") or {}
    for platform in META_PLATFORMS:
        uid = (links.get(platform) or {}).get("entityUniqueId")
        ent = entities.get(uid) if uid else None
        if ent and ent.get("title") and ent.get("artistName"):
            return ent["title"].strip(), ent["artistName"].strip()
    return None, None


# Ad təmizləmə bot/core/song_names.py-dadır (depo, təmizlik plugin-i və s. eyni qaydanı işlədir):
# (Official Video), [HD], tarix (12.05.2024), "Yeni 2024", #hashtag, @kanal, emoji, "| Albom" ...
def clean_youtube_title(title: str) -> str:
    return song_names.clean_title(title) or title


def parse_title(title: str):
    """YouTube adını təmizləyib (artist, ad) kimi ayırır."""
    return song_names.split_title(title)


def clean_channel(name: str) -> str:
    """YouTube kanal adından artist: "Eminem - Topic" / "EminemVEVO" / "INNA Official" → "Eminem" / "INNA"."""
    return song_names.clean_artist(name or "") if name else ""


def youtube_fallback_meta(vid: str, artist: str, track: str, src: str):
    """
    song.link metadata yoxdursa: 1) YouTube Music-in rəsmi artist/track sahələri
    2) başlıqda "Artist - Ad" yoxdursa → YouTube kanalının adı artist olur.
    """
    if src == "songlink":
        return artist, track, src
    ym = getattr(yt_handler, "VIDEO_META", {}).get(vid) or {}
    if ym.get("artist") and ym.get("track"):
        return ym["artist"], ym["track"], "youtube-music"
    if not artist or artist == "YouTube":
        channel = clean_channel(ym.get("channel"))
        if channel:
            return channel, track, "kanal"
        return "Naməlum", track, src     # "YouTube" saxlanmasın — yoxsa keş hər dəfə yenidən yüklənərdi
    return artist, track, src


def resolve_meta(yt_title: str, sl=None):
    """
    (artist, ad, mənbə) qaytarır.
    Əvvəl song.link (Spotify/Apple Music və s.) — yalnız YouTube adı ilə həqiqətən uyğun gələndə;
    yoxsa təmizlənmiş YouTube adı (adlar YouTube-dakı ilə eyni qalsın).
    """
    if sl and sl.get("title") and sl.get("artist"):
        if song_names.meta_matches(sl["artist"], sl["title"], yt_title):
            return song_names.clean_artist(sl["artist"]), song_names.clean_track(sl["title"]), "songlink"
        logger.info(f"song.link uyğun gəlmədi: '{sl['artist']} - {sl['title']}' ≠ '{yt_title}'")
    artist, track = parse_title(yt_title)
    return artist, track, "youtube"


def audio_filename(artist: str, track: str, file_path: str) -> str:
    ext = os.path.splitext(file_path)[1] or ".m4a"
    name = f"{artist} - {track}" if artist and artist != "YouTube" else track
    return (sanitize_filename(name)[:100] or "audio") + ext


def setup(context):
    dp = context.dp
    user_searches = context.user_searches
    context.download_jobs = download_jobs = {}   # (chat_id, msg_id) -> {event, owner}; inline yükləmələr bot/music/inline.py-dədir

    if not hasattr(context, 'youtube_manager'):
        context.youtube_manager = YoutubeManager(browser="firefox")

    fmt_duration = format_duration


    def both_mode() -> bool:
        try:
            return yt_handler.search_source() == "both"
        except Exception:
            return False

    def result_button(res: dict, index: int, mp3_bitrate: int = None) -> InlineKeyboardButton:
        mark = "📦 " if res.get("depo") else ("🎵 " if res.get("source") == "ytm" and both_mode() else "")
        dur = res.get('duration', '')
        if mp3_bitrate:
            dur_part = f"{dur} · 🎧 MP3 ({mp3_bitrate}k)" if dur else f"🎧 MP3 ({mp3_bitrate}k)"
        else:
            dur_part = dur
        text = f"{mark}{res['title']} ({dur_part})" if dur_part else f"{mark}{res['title']}"
        return InlineKeyboardButton(text=text[:64], callback_data=f"choice_{index}")

    def depo_result(row: dict) -> dict:
        title = f"{row.get('performer')} - {row.get('title')}" if row.get("performer") else (row.get("title") or "")
        return {"title": title, "original_title": title, "url": f"https://www.youtube.com/watch?v={row['video_id']}",
                "duration": fmt_duration(row.get("duration")), "raw_duration": int(row.get("duration") or 0),
                "thumbnail": f"https://i.ytimg.com/vi/{row['video_id']}/hqdefault.jpg", "depo": True,
                "file_id": row.get("file_id")}

    def name_hits(results: list, skip: set = frozenset()) -> dict:
        """vid → depo sətri: video ID-si keşdə olmayan, amma depoda AD üzrə olan mahnılar
        (eyni mahnının başqa YouTube videosu / depo indeksindən gələn post). Yaddaşdakı indekslə — sürətli."""
        out = {}
        for r in results:
            v = video_id_from_url(r.get("url"))
            if not v or v in skip:
                continue
            try:
                artist, track = parse_title(r.get("original_title") or r.get("title") or "")
                if not (artist and track):
                    continue
                same = audio_cache.find_same(artist, track, int(r.get("raw_duration") or 0))
            except Exception:
                same = None
            if same and same.get("file_id"):
                out[v] = same
        return out

    def mark_named(r: dict, same: dict):
        """Ad üzrə tapılan: 📦 nişanı, depodakı təmiz ad və meta (seçiləndə song.link gözlənmir)."""
        r["depo"] = True
        if same.get("performer") and same.get("performer") != "YouTube" and same.get("title"):
            r["title"] = f"{same['performer']} - {same['title']}"
            r["meta"] = {"artist": same["performer"], "track": same["title"]}

    async def depo_first(query: str, yt_results: list, depo_limit: int = 5, hits: list = None) -> list:
        """Depoda olan mahnılar siyahının başında; YouTube nəticələrindən depoda olanlar da 📦 ilə yuxarı.
        hits verilibsə (paralel axtarılıb) depo yenidən axtarılmır."""
        if hits is None:
            try:
                hits = await asyncio.to_thread(audio_cache.search_depo, query, depo_limit) if depo_limit else []
            except Exception as e:
                logger.debug(f"Depo axtarışı: {e}")
                hits = []
        if hits:
            hits = [h for h in hits if song_names.query_score(query, h.get("performer") or "", h.get("title") or "") >= 0.50]
        yt_results = [dict(r) for r in (yt_results or [])]
        for r in yt_results:
            r.setdefault("original_title", r.get("title"))
        vids = [video_id_from_url(r.get("url")) for r in yt_results]
        cached = await asyncio.to_thread(audio_cache.cached_ids, [v for v in vids if v])
        named = await asyncio.to_thread(name_hits, yt_results, cached)
        seen_files = {h.get("file_id") for h in hits}
        top = [depo_result(h) for h in hits]
        in_depo, rest = [], []
        for r, v in zip(yt_results, vids):
            if v and v in cached:
                c = await asyncio.to_thread(audio_cache.get_cached, v)
                if c and c.get("file_id") in seen_files:
                    continue                          # eyni depo mahnısı artıq yuxarıdadır
                r["depo"] = True
                if c and c.get("performer") and c.get("performer") != "YouTube" and c.get("title"):
                    r["title"] = f"{c['performer']} - {c['title']}"     # depodakı təmiz ad
                in_depo.append(r)
            elif v in named:                          # 📦 eyni mahnı depoda başqa video ID ilə
                if named[v].get("file_id") in seen_files:
                    continue
                seen_files.add(named[v].get("file_id"))
                mark_named(r, named[v])
                in_depo.append(r)
            else:
                rest.append(r)
        merged = top + in_depo + rest
        # musiqiyə oxşamayan (xəbər, fraqment, serial ...) nəticələr siyahının sonuna
        nm = [r for r in merged if not r.get("depo") and song_names.non_music_reason(
            r.get("original_title") or r.get("title") or "", int(r.get("raw_duration") or 0), query)]
        if nm:
            merged = [r for r in merged if r not in nm] + nm
        if IS.get("chat_rank"):                       # 🤖 Avto: soruşulan mahnı (ən yüksək uyğunluq) önə
            def score(i_r):
                i, r = i_r
                artist, track = parse_title(r.get("original_title") or r.get("title") or "")
                sc = song_names.query_score(query, artist or "", track or r.get("title") or "")
                return -((sc + (0.01 if r.get("depo") else 0)) - 0.01 * i - (1.0 if any(r is x for x in nm) else 0))
            merged = [r for _, r in sorted(enumerate(merged), key=lambda ir: (score(ir), ir[0]))]
        return merged

    # ═════════════ ♾ Sonsuz səhifələmə (axtarış və /mix üçün ortaq) ═════════════
    def _rkey(r: dict):
        return video_id_from_url(r.get("url")) or r.get("url") or r.get("title")

    def results_view(user_data: dict, page: int):
        """Səhifənin mətni və klaviaturası. Son yüklənmiş səhifədə daha çox nəticə varsa ▶️ yenə görünür."""
        results = user_data['results']
        loaded = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, loaded - 1))
        more = not user_data.get('exhausted')
        start = page * PAGE_SIZE

        mp3_hook = getattr(context, "test_mp3_hook", None)
        uid = user_data.get("uid")
        cid = user_data.get("chat_id")
        is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(uid, chat_id=cid))
        tester_br = mp3_hook.get_bitrate(chat_id=cid, user_id=uid) if is_mp3_tester else None

        rows = [[result_button(r, start + i, mp3_bitrate=tester_br)] for i, r in enumerate(results[start:start + PAGE_SIZE])]
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⏮", callback_data="page_0"))
        if page > 0:
            nav.append(InlineKeyboardButton(text=tr("◀️ Geri"), callback_data=f"page_{page - 1}"))
        if page < loaded - 1 or more:
            nav.append(InlineKeyboardButton(text=tr("İrəli ▶️"), callback_data=f"page_{page + 1}"))
        if nav:
            rows.append(nav)
        dl_row = download_all_row(len(results), user_data.get("uid"))
        if dl_row:
            rows.append(dl_row)
        rows.append([InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data="cancel")])
        total = f"{loaded}+" if more else str(loaded)
        header = user_data.get('header') or tr('🎵 Tapılan Mahnılar')
        if is_mp3_tester and "🎧 MP3" not in header:
            header += f" · 🎧 MP3 ({tester_br}k)"
        text = [f"<b>{header}" + tr(" (Səhifə {page}/{pages})", page=page + 1, pages=total) + ":</b>"]
        if is_mp3_tester:
            text.append(f"🎧 <i>Seçilmiş format: <b>MP3</b> · <b>{tester_br} kbps</b></i>")
        if any(r.get("depo") for r in results[start:start + PAGE_SIZE]):
            text.append(tr("<i>📦 — depoda var, dərhal göndərilir</i>"))
        text.append("<i>" + tr("{n} nəticə", n=len(results))
                    + (tr(" · ▶️ ilə daha çox gəlir") if more else tr(" · hamısı göstərilir")) + "</i>")
        return "\n".join(text), InlineKeyboardMarkup(inline_keyboard=rows), page

    async def load_more(user_data: dict) -> int:
        """Növbəti SEARCH_STEP nəticəni gətirir. YT Music bitəndə YouTube-a, Mix bitəndə son mahnının Mix-inə keçir."""
        if user_data.get('exhausted'):
            return 0
        known = {_rkey(r) for r in user_data['results']}
        ym = context.youtube_manager
        added = []
        for _ in range(8):                                   # mənbə dəyişəndə / təkrarlar çox olanda bir neçə cəhd
            want = user_data.get('fetched', SEARCH_STEP) + SEARCH_STEP
            if want > SEARCH_MAX:
                break
            try:
                if user_data.get('command_used') == "mix":
                    seed = user_data.get('mix_seed')
                    new = [dict(r) for r in await mix_lookup(seed, want)] if seed else []
                else:
                    new = await ym.youtube_search(user_data['query'], limit=want,
                                                  source=user_data.get('src_override'),
                                                  fast=bool(IS.get("chat_fast")))
            except Exception as e:
                logger.warning(f"Daha çox nəticə alınmadı: {e}")
                new = []
            user_data['fetched'] = want
            added = [dict(r) for r in new or [] if _rkey(r) not in known]
            if added:
                break
            if len(new or []) >= want - 5:                   # mənbə hələ dolu cavab verir — sadəcə təkrarlardır
                continue
            # bu mənbə bitdi → növbəti mənbə
            if user_data.get('command_used') == "mix":
                used = user_data.setdefault('mix_used', {user_data.get('mix_seed')})
                nxt = next((video_id_from_url(r.get("url")) for r in reversed(user_data['results'])
                            if video_id_from_url(r.get("url")) and video_id_from_url(r.get("url")) not in used), None)
                if not nxt:
                    break
                used.add(nxt)
                user_data.update(mix_seed=nxt, fetched=0)
            elif user_data.get('src_override') != "yt" and user_data.get('query'):
                user_data.update(src_override="yt", fetched=0)   # YT Music bitdi → adi YouTube
            else:
                break
        if not added:
            user_data['exhausted'] = True
            return 0
        for r in added:
            r.setdefault("original_title", r.get("title"))
        try:
            vids = [video_id_from_url(r.get("url")) for r in added]
            cached = await asyncio.to_thread(audio_cache.cached_ids, [v for v in vids if v])
            for r, v in zip(added, vids):
                if v and v in cached:
                    r["depo"] = True
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/music_plugin.py:428]: %r", _e)
        user_data['results'].extend(added)
        return len(added)

    async def handle_music_search(message: types.Message, search_query: str, command_used: str = "music"):
        try:
            search_query = re.sub(r'["\'«»“”„‟`]', ' ', (search_query or "")).strip()
            search_query = re.sub(r'\s+', ' ', search_query).strip(" -:;,.")
            if not search_query:
                await send_temp(
                    message,
                    tr("<b>🎵 Musiqi axtarışı istifadə qaydası:</b>\n👉 <code>/music &lt;mahnı adı&gt;</code>\n"
                       "<i>Nümunə:</i> <code>/music Eminem Lose Yourself</code>"),
                    seconds=20,
                    parse_mode="HTML"
                )
                return
            
            user_id = message.from_user.id
            maint_note = ""
            from common.database import is_maintenance_mode
            if is_maintenance_mode() and user_id != getattr(context, "creator_id", 0):
                maint_note = tr("⚠️ <i>Botda texniki sazlama işləri aparılır. Qəfil yükləmələr və ya cavab dayana bilər.</i>") + "\n\n"

            chat_id = message.chat.id
            mp3_hook = getattr(context, "test_mp3_hook", None)
            is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(user_id, chat_id=chat_id))
            tester_br = mp3_hook.get_bitrate(chat_id=chat_id, user_id=user_id) if is_mp3_tester else None
            search_label = f"<i>🔍 Axtarılır... · 🎧 MP3 ({tester_br}k)</i>" if is_mp3_tester else tr('<i>🔍 Axtarılır...</i>')

            search_msg = await message.answer(f"{maint_note}{search_label}", parse_mode="HTML")
            # ⚡ /menu → 🎵 Musiqi → ⚡ Axtarış: sürətli rejim, ilk nəticə sayı, depo, keş
            t0 = time.monotonic()
            first_n = IS.get("chat_first")
            depo_n = IS.get("chat_depo")
            try:
                src = yt_handler.search_source()
            except Exception:
                src = getattr(yt_handler, "DEFAULT_SOURCE", "ytm")
            chat_stats["queries"] += 1
            if is_cached(search_query, src, first_n):
                chat_stats["cache_hits"] += 1

            async def depo_hits():
                if not depo_n:
                    return []
                try:
                    return await asyncio.to_thread(audio_cache.search_depo, search_query, depo_n)
                except Exception as e:
                    logger.debug(f"Depo axtarışı: {e}")
                    return []

            # YouTube + depo PARALEL (əvvəl ardıcıl idi)
            yt_res, hits = await asyncio.gather(
                yt_lookup(search_query, src, first_n, fast=bool(IS.get("chat_fast"))), depo_hits(),
                return_exceptions=True)
            if isinstance(yt_res, BaseException):
                logger.warning(f"YouTube axtarışı alınmadı: {yt_res}")
                chat_stats["errors"] += 1
                yt_res = []
            if isinstance(hits, BaseException):
                hits = []
            # 📦 əvvəl depodakı mahnılar (dərhal göndərilir), sonra YouTube
            results = await depo_first(search_query, yt_res, depo_n, hits=hits)
            took = time.monotonic() - t0
            chat_stats.update(last=took, total=chat_stats["total"] + took, answered=chat_stats["answered"] + 1)

            if not results:
                try:
                    await search_msg.delete()
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/music_plugin.py:500]: %r", _e)
                raise ValueError(tr("Nəticə tapılmadı"))
            
            user_data = {
                'results': results,
                'search_message_id': search_msg.message_id,
                'original_message_id': message.message_id,
                'uid': message.from_user.id,
                'chat_id': message.chat.id,
                'command_used': command_used,
                'current_page': 0,
                'query': search_query,
                'fetched': first_n,
            }
            text, keyboard, _ = results_view(user_data, 0)
            await search_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
            user_searches[message.from_user.id] = user_data
            
        except Exception as e:
            await send_temp(
                message,
                tr("❌ <b>Xəta:</b> <code>{e}</code>", e=escape(str(e))),
                seconds=5,
                parse_mode="HTML"
            )

    async def own_search(callback: types.CallbackQuery):
        """Düymə basanın axtarışı + düymə HƏMİN axtarış mesajındadır? Yoxsa cavab verib None qaytarır
        (əvvəl köhnə siyahının düyməsi yeni axtarışın nəticəsini seçirdi, düymə isə "fırlanırdı")."""
        user_data = user_searches.get(callback.from_user.id)
        if not user_data or not callback.message or \
                callback.message.message_id != user_data.get('search_message_id'):
            await callback.answer(tr("Bu siyahı köhnəlib — yenidən axtar"), show_alert=True)
            return None
        return user_data

    @dp.callback_query(F.data.startswith("page_"))
    async def handle_page(callback: types.CallbackQuery):
        user_data = await own_search(callback)
        if user_data is None:
            return
        new_page = int(callback.data.split("_")[1])
        loaded = (len(user_data['results']) + PAGE_SIZE - 1) // PAGE_SIZE
        if new_page >= loaded:                              # ♾ son səhifə — daha çox gətir
            if user_data.get('loading'):
                await callback.answer(tr("⏳ Yüklənir..."))
                return
            user_data['loading'] = True
            await callback.answer(tr("⏳ Daha çox nəticə axtarılır..."))
            try:
                n = await load_more(user_data)
            finally:
                user_data['loading'] = False
            if not n:
                new_page = loaded - 1
                with contextlib.suppress(Exception):
                    await callback.answer(tr("Başqa nəticə yoxdur — hamısı göstərildi"), show_alert=False)
        else:
            await callback.answer()
        text, keyboard, new_page = results_view(user_data, new_page)
        user_data['current_page'] = new_page
        with contextlib.suppress(Exception):
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML",
                                             disable_web_page_preview=True)

    context.handle_music_search = handle_music_search

    @dp.message(Command("music", "mahnı", "song"))
    async def music_cmd(message: types.Message, command: CommandObject):
        command_used = message.text.split()[0][1:]
        await handle_music_search(message, command.args, command_used)

    # ═════════════ /mix — oxşar mahnılar (inline Mix-in çat versiyası) ═════════════

    def mix_seed_from_reply(message: types.Message):
        """
        Cavab verilən mesajdan Mix toxumu → (video_id | None, axtarış/başlıq | None):
          • botun göndərdiyi mahnı (🔀 Mix düyməsi varsa — dəqiq video)
          • mətndə / caption-da YouTube linki
          • audio (artist + ad, yoxdursa fayl adı), audio-fayl sənəd, video
          • adi mətn — mahnı adı kimi axtarılır
        """
        r = message.reply_to_message
        if not r:
            return None, None

        audio_title = None
        if r.audio:
            audio_title = " ".join(filter(None, [r.audio.performer, r.audio.title])) or \
                          os.path.splitext(r.audio.file_name or "")[0] or None

        kb = getattr(r, "reply_markup", None)
        for row in (kb.inline_keyboard if kb else []):
            for b in row:
                q = getattr(b, "switch_inline_query_current_chat", None) or ""
                if q.startswith("mix:"):
                    return q[4:], (r.audio.title if r.audio and r.audio.title else audio_title)

        text = r.text or r.caption or ""
        vid = video_id_from_url(text)
        if vid:
            return vid, None
        if audio_title:
            return None, audio_title
        doc = getattr(r, "document", None)
        if doc and (doc.mime_type or "").startswith("audio/") and doc.file_name:
            return None, os.path.splitext(doc.file_name)[0]
        video = getattr(r, "video", None)
        if video and getattr(video, "file_name", None):
            return None, os.path.splitext(video.file_name)[0]
        if text and not text.startswith("/"):
            return None, text[:100]
        return None, None


    @dp.message(Command("mix"))
    async def mix_cmd(message: types.Message, command: CommandObject):
        await run_mix(message, (command.args or "").strip())

    # Mahnıya cavab olaraq sadəcə "mix" yazmaq da işləyir (slash-sız)
    MIX_WORDS = {"mix", "miks", "🔀", "mix et", "oxşar"}

    @dp.message(F.reply_to_message, F.text.func(lambda t: bool(t) and t.strip().lower() in MIX_WORDS))
    async def mix_reply_word(message: types.Message):
        await run_mix(message, "")

    async def run_mix(message: types.Message, query: str):
        if message.from_user:
            from common import tiers
            if not await asyncio.to_thread(tiers.allowed, "mix", message.from_user.id):
                kb_btn = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=tr("💎 Premium al"), callback_data="sg:premium")
                ]]) if message.chat.type == "private" else None
                await message.reply(tiers.gate_text("mix", message.from_user.id), parse_mode="HTML",
                                    reply_markup=kb_btn)
                return

        from common.database import is_maintenance_mode
        if is_maintenance_mode() and message.from_user and message.from_user.id != getattr(context, "creator_id", 0):
            with contextlib.suppress(Exception):
                await message.reply(
                    tr("⚠️ <i>Botda texniki sazlama işləri aparılır. Qəfil yükləmələr və ya cavab dayana bilər.</i>"),
                    parse_mode="HTML"
                )

        vid, seed_title = video_id_from_url(query), None
        if not query:
            vid, reply_query = mix_seed_from_reply(message)
            if vid:
                seed_title = reply_query
            else:
                query = reply_query or ""

        if not vid and not query:
            await send_temp(
                message,
                tr("<b>🔀 Mix — oxşar mahnılar</b>") + "\n\n"
                + tr("👉 <code>/mix mahnı adı</code>\n👉 <code>/mix YouTube linki</code>\n"
                     "👉 və ya istənilən mahnıya <b>cavab (reply)</b> olaraq <code>/mix</code> və ya sadəcə <code>mix</code> yaz")
                + "\n\n<i>" + tr("Nümunə:") + "</i> <code>/mix Eminem Lose Yourself</code>",
                seconds=20,
                parse_mode="HTML",
            )
            return

        chat_id = message.chat.id
        mp3_hook = getattr(context, "test_mp3_hook", None)
        is_mp3_tester = bool(mp3_hook and message.from_user and mp3_hook.is_active_for(message.from_user.id, chat_id=chat_id))
        tester_br = mp3_hook.get_bitrate(chat_id=chat_id, user_id=message.from_user.id) if is_mp3_tester else None
        mix_label = f"<i>🔀 Oxşar mahnılar axtarılır... · 🎧 MP3 ({tester_br}k)</i>" if is_mp3_tester else tr("<i>🔀 Oxşar mahnılar axtarılır...</i>")
        status = await message.answer(mix_label, parse_mode="HTML")
        try:
            if not vid:
                try:
                    src = yt_handler.search_source()
                except Exception:
                    src = "yt"
                found = await yt_lookup(query, src, 5, fast=bool(IS.get("mix_fast")))
                seed = next((r for r in found or [] if video_id_from_url(r.get("url"))), None)
                if not seed:
                    raise ValueError(tr("Mahnı tapılmadı"))
                vid, seed_title = video_id_from_url(seed["url"]), seed["title"]

            size = IS.get("mix_size")
            results = [dict(r) for r in await mix_lookup(vid, size)]
            if not results:
                raise ValueError(tr("Bu mahnı üçün Mix tapılmadı"))

            label = escape(clean_youtube_title(seed_title)[:60]) if seed_title else tr("bu mahnı")
            header = f"🔀 Mix: {label}"
            # /music ilə eyni axın: choice_ / page_ / cancel handler-ləri bunu istifadə edir
            user_data = {
                'results': results,
                'search_message_id': status.message_id,
                'original_message_id': message.message_id,
                'command_used': "mix",
                'uid': message.from_user.id,
                'chat_id': message.chat.id,
                'current_page': 0,
                'header': header,
                'mix_seed': vid,
                'mix_used': {vid},
                'fetched': size,
            }
            text, kb, _ = results_view(user_data, 0)
            await status.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            user_searches[message.from_user.id] = user_data
        except Exception as e:
            logger.warning(f"/mix xətası: {e}")
            try:
                await status.edit_text(tr("❌ <b>Xəta:</b> <code>{e}</code>", e=escape(str(e))[:300]), parse_mode="HTML")
                delete_after(status, 5)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/music_plugin.py:713]: %r", _e)

    async def not_editing_audio(message: types.Message) -> bool:
        """Bot bu istifadəçidən başqa mətn gözləyirsə (ad redaktəsi, bəxşiş, parol ...) — axtarış deyil."""
        from bot.core.input_wait import waiting_any
        return not waiting_any(context, message.from_user.id)

    # ═════════════ ✏️ Inline ilə BOTUN ÖZÜNƏ göndərilən mahnı → meta redaktə təklifi ═════════════
    def inline_vid(message: types.Message):
        """Botun inline mesajından video ID: placeholder (inl_stop:<id>) və ya hazır mahnı (mix:<id>)."""
        kb = getattr(message, "reply_markup", None)
        for row in (kb.inline_keyboard if kb else []):
            for b in row:
                data = getattr(b, "callback_data", None) or ""
                if data.startswith("inl_stop:"):
                    return data.split(":", 1)[1]
                q = getattr(b, "switch_inline_query_current_chat", None) or ""
                if q.startswith("mix:"):
                    return q[4:]
        return None

    async def is_own_inline(message: types.Message) -> bool:
        vb = getattr(message, "via_bot", None)
        return bool(vb) and vb.id == context.bot.id

    @dp.message(F.chat.type == "private", F.via_bot, is_own_inline)
    async def own_inline_in_private(message: types.Message):
        vid = inline_vid(message)
        if not vid or not getattr(context, "audio_editor_start", None):
            return                       # axtarışa getmir, sadəcə susur
        from common import tiers
        if not await asyncio.to_thread(tiers.allowed, "audio_edit", message.from_user.id):
            return                       # ✏️ Metadata redaktə bağlıdır — təklif göstərilmir
        info = inline_cache.get(vid) or {}
        title = clean_youtube_title(info.get("title") or "") or \
            (message.text or "").replace("⏳", "").split("\n")[0].strip() or "bu mahnı"
        await message.reply(
            f"✏️ <b>{escape(title[:80])}</b>\n\n"
            + tr("Bu mahnının meta məlumatlarını dəyişmək istəyirsən?\n<i>🎵 ad · 🎤 artist · 🖼 üz qabığı şəkli</i>"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=tr("✅ Bəli"), callback_data=f"meta:yes:{vid}:{message.message_id}"),
                InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data="meta:no"),
            ]]),
        )

    @dp.callback_query(F.data.startswith("meta:"))
    async def meta_offer(callback: types.CallbackQuery):
        parts = callback.data.split(":")
        if parts[1] == "no" or len(parts) < 4:
            await callback.answer(tr("Ləğv edildi"))
            with contextlib.suppress(Exception):
                await callback.message.delete()
            return

        vid, reply_to = parts[2], int(parts[3]) if parts[3].isdigit() else None
        await callback.answer()
        with contextlib.suppress(Exception):
            await callback.message.edit_text(tr("⏳ <i>Mahnı hazırlanır...</i>"), parse_mode="HTML")

        # Inline mahnı hələ yüklənirsə — hazır olana qədər gözlə (depo/keşə düşən an)
        cached = None
        for _ in range(120):
            cached = await asyncio.to_thread(audio_cache.get_cached, vid)
            if cached:
                break
            await asyncio.sleep(1)
        if not cached:
            with contextlib.suppress(Exception):
                await callback.message.edit_text(
                    tr("❌ Mahnı hazır olmadı (yükləmə dayandırılıb və ya alınmayıb).\n"
                       "<i>Mahnı gələndən sonra onu mənə göndər — redaktə paneli açılacaq.</i>"),
                    parse_mode="HTML",
                )
            return

        start = getattr(context, "audio_editor_start", None)
        performer, title = cached.get("performer") or "", cached.get("title") or "audio"
        await start(
            chat_id=callback.message.chat.id, uid=callback.from_user.id, file_id=cached["file_id"],
            file_name=f"{performer + ' - ' if performer else ''}{title}.m4a", title=title, artist=performer,
            duration=cached.get("duration"), mime="audio/mp4", size=cached.get("size"), reply_to=reply_to,
        )
        with contextlib.suppress(Exception):
            await callback.message.delete()

    @dp.message(
        F.chat.type == "private",
        F.text,
        ~F.via_bot,                      # botun öz inline mesajı axtarış sayılmasın
        not_editing_audio,
        ~F.text.startswith("/"),
        ~F.text.func(is_music_link),     # YouTube / Spotify linkləri → links_plugin
    )
    async def handle_private_music_request(message: types.Message):
        await handle_music_search(message, message.text, "music")

    # ═════════════ 🔎 Qrupda «/music» yazmadan mətnlə axtarış (qrup panelində açılan qruplarda) ═════════════
    async def is_group_text_search(message: types.Message) -> bool:
        from bot.core import group_text
        from bot.core.group_panel import is_group_text_search as enabled_for
        from bot.core.input_wait import waiting_any
        if not enabled_for(message.chat.id) or waiting_any(context, message.from_user.id):
            return False
        return group_text.is_group_search(message, True)

    @dp.message(
        F.chat.type.in_({"group", "supergroup"}),
        F.text,
        ~F.via_bot,
        ~F.text.startswith("/"),
        ~F.text.func(is_music_link),     # linklər → links_plugin
        is_group_text_search,
    )
    async def handle_group_text_music_request(message: types.Message):
        await handle_music_search(message, message.text, "music")

    # ═════════════ ⏱ Düymələrin avtomatik gizlənməsi ═════════════
    def hide_keyboard_later(chat_id=None, message_id=None, inline_message_id=None, delay=None):
        """Mahnı göndəriləndən təyin edilmiş saniyə sonra altındakı düymələri silir (0 = daimi)."""
        from common.database import get_auto_hide_duration
        sec = get_auto_hide_duration() if delay is None else delay
        if sec <= 0:
            return  # 0 saniyə = daimi qalır (avtomatik silinmir)

        async def _hide():
            await asyncio.sleep(sec)
            with contextlib.suppress(Exception):
                if inline_message_id:
                    await context.bot.edit_message_reply_markup(inline_message_id=inline_message_id,
                                                                reply_markup=None)
                elif chat_id and message_id:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id,
                                                                reply_markup=None)
        if inline_message_id or (chat_id and message_id):
            spawn(_hide())

    # ═════════════ ☕ Pulsuz istifadəçilərə bəxşiş təklifi (1 dəq sonra silinir) ═════════════
    last_tip_prompt = {}

    async def maybe_send_tip_prompt(bot, chat_id: int, user_id: int):
        """Pulsuz istifadəçiyə əməyə dəstək üçün bəxşiş təklif edir və 1 dəqiqə sonra avtomatik silir."""
        try:
            from common.database import is_creator, is_premium, is_tip_prompt_enabled
            if not is_tip_prompt_enabled():
                return
            if is_creator(user_id) or is_premium(user_id):
                return
            now = time.time()
            if user_id in last_tip_prompt and now - last_tip_prompt[user_id] < 600:
                return
            last_tip_prompt[user_id] = now

            text = tr(
                "☕ <b>Əməyə Dəstək</b>\n"
                "<i>Mahnınız uğurla göndərildi! Botun fasiləsiz işləməsi və server xərclərini "
                "dəstəkləmək üçün kiçik bir bəxşişlə dəstək ola bilərsiniz. ⭐</i>\n\n"
                "⏱ <i>Bu bildiriş 1 dəqiqə sonra avtomatik silinəcək.</i>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text=tr("⭐ Bəxşiş Göndər"), callback_data="sg:tip"),
                    InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="tip_prompt:close"),
                ]
            ])
            msg = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

            async def _auto_delete():
                await asyncio.sleep(60)
                with contextlib.suppress(Exception):
                    await bot.delete_message(chat_id, msg.message_id)

            spawn(_auto_delete())
        except Exception as e:
            logger.debug(f"Tip prompt xətası (zərərsiz): {e}")

    @dp.callback_query(F.data == "tip_prompt:close")
    async def tip_prompt_close(cb: types.CallbackQuery):
        await cb.answer()
        with contextlib.suppress(Exception):
            await cb.message.delete()

    # ═════════════ 📦 Keş + depo kanalı ═════════════
    async def fetch_thumb(thumb_url: str):
        """Üz qabığı (məs. Spotify albom şəkli) — Telegram thumbnail limiti 200 KB."""
        return await fetch_telegram_thumb(thumb_url)


    async def fetch_audio(url: str, yt_title: str, duration: int, cancel_event, **kw) -> dict:
        """📡 _fetch_audio + canlı monitor izi (çağıran activity.using(...) ilə kim/haradan olduğunu verir)."""
        tok = activity.begin(yt_title)
        try:
            res = await _fetch_audio(url, yt_title, duration, cancel_event, **kw)
        except DownloadCancelled:
            activity.end(tok, "stopped")
            raise
        except Exception:
            activity.end(tok, "failed")
            raise
        size = 0
        if res.get("path"):
            with contextlib.suppress(OSError):
                size = os.path.getsize(res["path"])
        name = f"{res.get('artist')} - {res.get('track')}" if res.get("artist") else None
        activity.end(tok, "cached" if res.get("cached") else ("done" if res.get("file_id") or size else "failed"),
                     size, name)
        return res

    async def _fetch_audio(url: str, yt_title: str, duration: int, cancel_event,
                          need_file_id: bool = False, fallback_chat=None, sem=None,
                          meta: dict = None, thumb_url: str = None, upload_depo: bool = True,
                          source: str = "ytm") -> dict:
        """
        Mahnını əvvəl keşdən (depo kanalı) götürür; yoxdursa YouTube-dan yükləyir, depoya atır, keşə yazır.
        Nəticə: {file_id, path, fname, artist, track, sl, cached, vid}
        """
        bot = context.bot
        vid = media_id_from_url(url) or video_id_from_url(url)
        sl_task = asyncio.create_task(get_songlink(url))
        async with contextlib.AsyncExitStack() as locks:
            if vid:
                await locks.enter_async_context(audio_cache.lock_for(vid))
            cached = await asyncio.to_thread(audio_cache.get_cached, vid)
            if cached and (cached.get("performer") or "").strip() in ("", "YouTube") \
                    and await asyncio.to_thread(audio_cache.refetch_once, vid):
                # köhnə keş: artist adı yoxdur — YALNIZ BİR DƏFƏ yenidən yüklənib düzgün artistlə depoya düşsün
                # (əvvəl hər istəkdə yenidən yüklənirdi → eyni video depoda dəfələrlə təkrarlanırdı)
                await asyncio.to_thread(audio_cache.invalidate, vid)
                cached = None
            if cached:
                try:
                    sl = await asyncio.wait_for(asyncio.shield(sl_task), 2)   # keşdən göndərməni ləngitməsin
                except (asyncio.TimeoutError, Exception):
                    sl = None
                artist, track, _ = resolve_meta(yt_title, sl)
                logger.info(f"📦 Keşdən: {vid}")
                return {"file_id": cached["file_id"], "path": None, "fname": None, "vid": vid, "sl": sl,
                        "artist": cached.get("performer") or artist, "track": cached.get("title") or track,
                        "cached": True}

            # 🔁 Eyni mahnı başqa video kimi depoda artıq varsa — yükləmə, depodakını ver (dublikat olmasın)
            sl_early = None
            if meta and meta.get("artist") and meta.get("track"):
                g_artist, g_track = meta["artist"], meta["track"]       # ad məlumdur — song.link gözlənmir
            else:
                try:
                    sl_early = await asyncio.wait_for(asyncio.shield(sl_task), 4)
                except (asyncio.TimeoutError, Exception):
                    sl_early = None
                g_artist, g_track, _ = resolve_meta(yt_title, sl_early)
            same = None
            if g_artist and g_artist not in ("YouTube", "Naməlum"):
                # 🔒 eyni mahnı (ad üzrə) — fərqli video ID-ləri eyni anda yüklənməsin: ikinci gözləyir,
                # sonra birincinin depoya yüklədiyini find_same ilə tapır (paralel işçilərdə dublikat olmur)
                tt = song_names.track_tokens(g_track or "")
                if tt:
                    await locks.enter_async_context(audio_cache.lock_for("song:" + "|".join(sorted(tt))))
                same = await asyncio.to_thread(audio_cache.find_same, g_artist, g_track, duration)
            if same and vid:
                await asyncio.to_thread(audio_cache.alias, vid, same, yt_title)
                return {"file_id": same["file_id"], "path": None, "fname": None, "vid": vid, "sl": sl_early,
                        "artist": same.get("performer") or g_artist, "track": same.get("title") or g_track,
                        "cached": True}

            activity.phase("DL")
            async with (sem or contextlib.nullcontext()):
                if cancel_event.is_set():
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                t_dl = time.time()
                path = await context.youtube_manager.download_track(
                    url, sanitize_filename(yt_title), cancel_event=cancel_event
                )
                with contextlib.suppress(OSError):          # 📶 sürət: YouTube → server
                    activity.stage("DL", dl_bytes=os.path.getsize(path), dl_secs=time.time() - t_dl)
            sl = await sl_task
            if cancel_event.is_set():
                with contextlib.suppress(OSError):
                    os.remove(path)
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            if meta and meta.get("artist") and meta.get("track"):
                artist, track, meta_src = meta["artist"], meta["track"], "spotify"   # dəqiq metadata verilib
            else:
                artist, track, meta_src = resolve_meta(yt_title, sl)
                artist, track, meta_src = youtube_fallback_meta(vid, artist, track, meta_src)
            track = song_names.clean_track(track)
            if artist not in ("YouTube", "Naməlum"):
                artist = song_names.clean_artist(artist)
            logger.info(f"Metadata ({meta_src}): {artist} - {track}")
            fname = audio_filename(artist, track, path)
            thumb = await fetch_thumb(thumb_url)
            file_id = None
            if upload_depo:
                if artist not in ("YouTube", "Naməlum"):
                    same_final = await asyncio.to_thread(audio_cache.find_same, artist, track, duration)
                    if same_final and same_final.get("file_id"):
                        if vid:
                            await asyncio.to_thread(audio_cache.alias, vid, same_final, yt_title)
                        with contextlib.suppress(OSError):
                            os.remove(path)
                        logger.info(f"📦 Dublikat qarşısı (yükləmə öncəsi): {vid} → depodakı '{artist} - {track}'")
                        return {"file_id": same_final["file_id"], "path": None, "fname": fname, "vid": vid, "sl": sl,
                                "artist": same_final.get("performer") or artist, "track": same_final.get("title") or track,
                                "cached": True}
                activity.phase("UP", f"{artist} - {track}")
                t_up = time.time()
                file_id = await audio_cache.upload_to_depo(bot, path, fname, track, artist, duration, url, vid, thumb,
                                                           yt_title=yt_title, source=source)
                with contextlib.suppress(OSError):              # 📶 sürət: server → depo (Telegram)
                    if file_id:
                        activity.stage("UP", up_bytes=os.path.getsize(path), up_secs=time.time() - t_up)
                if not file_id and need_file_id and fallback_chat:
                    # depo əlçatan deyil — inline üçün file_id mütləq lazımdır
                    msg = await bot.send_audio(
                        chat_id=fallback_chat, audio=FSInputFile(path, filename=fname),
                        title=track[:64], performer=artist[:64], duration=duration or None,
                    )
                    file_id = await asyncio.to_thread(audio_cache.save_from_message, vid, msg, track, artist, duration,
                                                      yt_title)
            return {"file_id": file_id, "path": path, "fname": fname, "vid": vid, "sl": sl,
                    "artist": artist, "track": track, "cached": False}

    @dp.callback_query(F.data.startswith("choice_"))
    async def handle_choice(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        user_data = await own_search(callback)
        if user_data is None:
            return
        file_path = None
        
        try:
            choice = int(callback.data.split("_")[1])
            selected = user_data['results'][choice]
            try:
                from bot.plugins.restart_plugin import is_restart_pending
                if is_restart_pending() and user_id != getattr(context, "creator_id", 0):
                    await callback.answer(tr("⏳ Bot restart üçün gözləmədədir. Zəhmət olmasa bir neçə dəqiqə sonra cəhd edin."), show_alert=True)
                    return
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/music_plugin.py:1033]: %r", _e)

            job_key = (callback.message.chat.id, user_data['search_message_id'])
            job = {"event": threading.Event(), "owner": user_id}
            download_jobs[job_key] = job

            maint_note = ""
            from common.database import is_maintenance_mode
            if is_maintenance_mode() and user_id != getattr(context, "creator_id", 0):
                maint_note = tr("⚠️ <i>Botda texniki işlər aparılır — qəfil dayana bilər.</i>\n\n")

            chat_id = callback.message.chat.id
            mp3_hook = getattr(context, "test_mp3_hook", None)
            is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(user_id, chat_id=chat_id))
            tester_br = mp3_hook.get_bitrate(chat_id=chat_id, user_id=user_id) if is_mp3_tester else None

            if is_mp3_tester:
                load_action = f"<i>🎧 MP3 ({tester_br} kbps) formatında hazırlanır...</i>\n"
            else:
                load_action = f"<i>{tr('📦 Depodan göndərilir') if selected.get('depo') else tr('⏳ Yüklənir')}:</i>\n"

            await callback.message.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=user_data['search_message_id'],
                text=(f"{maint_note}{load_action}"
                      f" <b>{escape(selected['title'])}</b> <b>{selected['duration']}</b>"),
                parse_mode="HTML",
                reply_markup=stop_keyboard(f"dlstop:{user_data['search_message_id']}"),
            )
            
            duration = int(selected.get('raw_duration', 0) or 0)
            for attempt in (1, 2):
                # 📦 əvvəl depo/keş, yoxdursa yüklə (song.link paralel)
                src = ("mix" if user_data.get('command_used') == "mix"
                       else "msg" if callback.message.chat.type == "private" else "grp")
                with activity.using(src, user_id):
                    res = await fetch_audio(selected['url'], selected.get('original_title') or selected['title'],
                                            duration, job["event"], meta=selected.get('meta'),
                                            upload_depo=not is_mp3_tester,
                                            source=selected.get('source') or "ytm")
                file_path = res["path"]
                sl, artist, track_name = res["sl"], res["artist"], res["track"]
                caption = build_caption(selected['url'], sl, callback.from_user.id)
                kb = build_keyboard(selected['url'], sl, user_id)
                try:
                    sent = await AudioDeliveryService.deliver(
                        target=callback.message,
                        res=res,
                        artist=artist,
                        track_name=track_name,
                        duration=duration,
                        caption=caption,
                        reply_markup=kb,
                        user_id=user_id,
                        context=context,
                        cancel_event=job["event"],
                        activity=activity,
                        audio_cache=audio_cache,
                        hide_keyboard_callback=hide_keyboard_later,
                    )
                    break
                except Exception as e:
                    if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                        await audio_cache.recover_bad_file(context, res["vid"])   # yenidən yükləmə — son çarə
                        continue                       # yenidən: bu dəfə YouTube-dan
                    raise
            if res["cached"]:
                await asyncio.to_thread(audio_cache.mark_hit, res["vid"])

            await log_download(callback.from_user.id, f"{artist} - {track_name}", selected['url'],
                               "mix" if user_data.get('command_used') == "mix" else "music",
                               chat_id=callback.message.chat.id)
            if getattr(sent, "chat", None) and sent.chat.id == callback.message.chat.id and getattr(sent, "audio", None):
                spawn(maybe_send_tip_prompt(callback.message.bot, callback.message.chat.id, callback.from_user.id))

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
            except Exception as e:
                logger.debug(f"Axtarış mesajı silinərkən xəta (seçim): {e}")

            try:
                await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
            except Exception as e:
                logger.debug(f"Orijinal mesaj silinərkən xəta (seçim): {e}")
            
        except DownloadCancelled:
            chat_id = callback.message.chat.id
            try:
                await callback.message.bot.edit_message_text(
                    chat_id=chat_id, message_id=user_data['search_message_id'],
                    text=tr("⏹ <b>Yükləmə dayandırıldı:</b>\n{title}", title=escape(selected['title'])), parse_mode="HTML",
                )
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/music_plugin.py:1134]: %r", _e)
            try:
                await callback.message.bot.delete_message(chat_id, user_data['original_message_id'])
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/music_plugin.py:1138]: %r", _e)

            delete_after((callback.message.bot, chat_id, user_data['search_message_id']), 5)

        except (IndexError, KeyError):
            await callback.message.answer(tr("❌ <b>Yanlış seçim!</b>"), parse_mode="HTML")
            
        except Exception as e:
            from common.delivery import is_audio_forbidden_error
            if is_audio_forbidden_error(e):      # gözlənilən: qrupda musiqi icazəsi yoxdur (delivery fallback-i işlədi)
                logger.warning(f"handle_choice: qrupda musiqi icazəsi yoxdur: {e}")
            else:
                logger.error(f"handle_choice xətası: {e}", exc_info=True)
            await callback.message.answer(
                tr("❌ <b>Xəta:</b> <code>{e}</code>", e=escape(str(e))),
                parse_mode="HTML"
            )
            if user_data.get('search_message_id') is not None:
                with contextlib.suppress(Exception):
                    await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        finally:
            if user_data.get('search_message_id') is not None:
                download_jobs.pop((callback.message.chat.id, user_data['search_message_id']), None)
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/music_plugin.py:1166]: %r", _e)
            user_searches.pop(user_id, None)

    # ── ⏹ Yükləməni dayandır (/music) ──
    @dp.callback_query(F.data.startswith("dlstop:"))
    async def handle_download_stop(callback: types.CallbackQuery):
        try:
            key = (callback.message.chat.id, int(callback.data.split(":", 1)[1]))
        except (ValueError, AttributeError):
            await callback.answer()
            return
        job = download_jobs.get(key)
        if not job:
            await callback.answer(tr("Yükləmə artıq bitib"), show_alert=True)
            return
        if callback.from_user.id not in (job["owner"], context.creator_id):
            await callback.answer(tr("⛔ Yalnız mahnını istəyən dayandıra bilər"), show_alert=True)
            return
        job["event"].set()
        await callback.answer(tr("⏹ Dayandırılır..."))
        try:
            await callback.message.edit_text(tr("⏹ <i>Dayandırılır...</i>"), parse_mode="HTML")
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/music_plugin.py:1189]: %r", _e)

    # ── 🙈 Düymələri gizlət (mahnının altındakı Mix və s.) ──
    @dp.callback_query(F.data.startswith("hidekb:"))
    async def handle_hide_keyboard(callback: types.CallbackQuery):
        try:
            owner = int(callback.data.split(":", 1)[1])
        except ValueError:
            owner = 0
        if owner and callback.from_user.id not in (owner, context.creator_id):
            await callback.answer(tr("⛔ Yalnız mahnını göndərən gizlədə bilər"), show_alert=True)
            return
        try:
            if callback.inline_message_id:
                await callback.bot.edit_message_reply_markup(
                    inline_message_id=callback.inline_message_id, reply_markup=None
                )
            elif callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer(tr("🙈 Gizlədildi"))
        except Exception as e:
            logger.warning(f"Düymələr gizlədilmədi: {e}")
            await callback.answer(tr("Gizlətmək alınmadı"), show_alert=True)

    @dp.callback_query(F.data == "cancel")
    async def handle_cancel(callback: types.CallbackQuery):
        user_data = await own_search(callback)
        if user_data is None:
            return
        await callback.answer()
        cancel_msg = await callback.message.answer(tr("❌ <b>Axtarış ləğv edildi.</b>"), parse_mode="HTML")
        
        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['search_message_id'])
        except Exception as e:
            logger.debug(f"Axtarış mesajı silinərkən xəta (ləğv): {e}")

        try:
            await callback.message.bot.delete_message(callback.message.chat.id, user_data['original_message_id'])
        except Exception as e:
            logger.debug(f"Orijinal mesaj silinərkən xəta (ləğv): {e}")
        
        delete_after(cancel_msg, 5)
        
        user_searches.pop(callback.from_user.id, None)


    # ═════════════ ⬇️ Hamısını yüklə (💎 premium) ═════════════
    batch_jobs = {}      # (chat_id, msg_id) -> {"event", "owner"} — hər siyahı mesajı üçün ayrıca iş

    BAD_WORDS = ("live", "cover", "karaoke", "instrumental", "8d", "sped up", "slowed", "reverb",
                 "nightcore", "remix", "1 hour", "1 saat", "lyrics video")

    async def resolve_youtube(query: str, duration: int = 0):
        """Spotify mahnısı üçün ən uyğun YouTube videosu: müddət yaxınlığı + "cover/live" kimi sözlərdən qaçış."""
        try:
            results = await context.youtube_manager.youtube_search(query, limit=8)
        except TypeError:                       # köhnə youtube_handler (limit parametri yoxdur)
            results = await context.youtube_manager.youtube_search(query)
        q = query.lower()
        best, best_score = None, None
        for rank, r in enumerate(results or []):
            if not video_id_from_url(r.get("url")):
                continue
            title = (r.get("title") or "").lower()
            score = rank * 2
            if duration and r.get("raw_duration"):
                diff = abs(int(r["raw_duration"]) - int(duration))
                score += 0 if diff <= 5 else (diff / 3 if diff <= 30 else 40)
            score += sum(25 for w in BAD_WORDS if w in title and w not in q)
            if best_score is None or score < best_score:
                best, best_score = r, score
        return best["url"] if best else None

    async def prepare_track(selected: dict, event, user_id: int = None, chat_id: int = None) -> dict:
        """Yükləmə hissəsi (paralel işləyə bilər): YouTube linki tapılır, mahnı keşdən / YouTube-dan hazırlanır."""
        duration = int(selected.get('raw_duration', 0) or 0)
        mp3_hook = getattr(context, "test_mp3_hook", None)
        is_mp3_tester = bool(mp3_hook and mp3_hook.is_active_for(user_id, chat_id=chat_id))
        if not selected.get('url'):
            # ⚡ Spotify / playlist elementi: əvvəl depoda AD üzrə axtar — varsa YouTube axtarışı da lazım deyil
            meta = selected.get('meta') or {}
            if meta.get('artist') and meta.get('track'):
                same = await asyncio.to_thread(audio_cache.find_same, meta['artist'], meta['track'], duration)
                vid = (same or {}).get('video_id') or ""
                if same and same.get('file_id') and vid and not vid.startswith("depo:"):
                    selected['url'] = f"https://www.youtube.com/watch?v={vid}"
                    logger.info(f"📦 Keşdən (ad üzrə, YouTube axtarışısız): {meta['artist']} - {meta['track']}")
                    ctx = activity.current_src()
                    if ctx:
                        activity.instant(ctx[0], ctx[1], f"{meta['artist']} - {meta['track']}", "cached")
                    return {"file_id": same["file_id"], "path": None, "fname": None, "vid": vid, "sl": None,
                            "artist": same.get("performer") or meta['artist'],
                            "track": same.get("title") or meta['track'], "cached": True}
            selected['url'] = await resolve_youtube(selected.get('query') or selected['title'], duration)
            if not selected['url']:
                raise ValueError(f"YouTube-da tapılmadı: {selected['title']}")
        return await fetch_audio(selected['url'], selected['title'], duration, event,
                                 meta=selected.get('meta'), thumb_url=selected.get('thumb'),
                                 upload_depo=not is_mp3_tester,
                                 source=selected.get('source') or "ytm")

    async def deliver_track(target: types.Message, selected: dict, res: dict, user_id: int, event) -> tuple:
        """Göndərmə hissəsi (həmişə ardıcıl — siyahının sırası qorunur) → (artist, ad)."""
        duration = int(selected.get('raw_duration', 0) or 0)
        chat_id = target.chat.id if hasattr(target, "chat") else None
        for attempt in (1, 2):
            path = res.get("path")
            try:
                caption = build_caption(selected['url'], res["sl"], user_id)
                kb = build_keyboard(selected['url'], res["sl"], user_id)
                await AudioDeliveryService.deliver(
                    target=target,
                    res=res,
                    artist=res["artist"],
                    track_name=res["track"],
                    duration=duration,
                    caption=caption,
                    reply_markup=kb,
                    user_id=user_id,
                    context=context,
                    cancel_event=event,
                    activity=activity,
                    audio_cache=audio_cache,
                    hide_keyboard_callback=hide_keyboard_later,
                )
                if res["cached"]:
                    await asyncio.to_thread(audio_cache.mark_hit, res["vid"])
                return res["artist"], res["track"]
            except Exception as e:
                if attempt == 1 and res["cached"] and audio_cache.is_bad_file_id(e):
                    await audio_cache.recover_bad_file(context, res["vid"])       # yenidən yükləmə — son çarə
                    res = await prepare_track(selected, event, user_id=user_id, chat_id=chat_id)   # köhnə file_id — yenidən yüklə
                    continue
                raise
            finally:
                if path:
                    with contextlib.suppress(OSError):
                        os.remove(path)



    @dp.callback_query(F.data == "dlall")
    async def handle_download_all(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        user_data = user_searches.get(user_id)
        if not user_data or callback.message.message_id != user_data.get('search_message_id'):
            await callback.answer(tr("Bu siyahı köhnəlib — yenidən axtar"), show_alert=True)
        try:
            from bot.plugins.restart_plugin import is_restart_pending
            if is_restart_pending() and user_id != context.creator_id:
                await callback.answer(tr("⏳ Bot restart üçün gözləmədədir. Zəhmət olmasa bir neçə dəqiqə sonra cəhd edin."), show_alert=True)
                return
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/music_plugin.py:1334]: %r", _e)
        from common import tiers
        if not await asyncio.to_thread(tiers.allowed, "dlall", user_id):
            await callback.answer(tr("💎 Hamısını birdən yükləmək yalnız Premium üçündür"), show_alert=False)
            with contextlib.suppress(Exception):
                await callback.message.answer(tiers.gate_text("dlall"), parse_mode="HTML",
                                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                                  InlineKeyboardButton(text=tr("💎 Premium al"),
                                                                       callback_data="sg:premium")]]))
            return
        # Eyni anda yalnız 1 toplu yükləmə — creator üçün limit yoxdur
        if user_id != context.creator_id and any(j["owner"] == user_id for j in batch_jobs.values()):
            await callback.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"),
                                  show_alert=True)
            return

        # dublikatları və çox uzun videoları çıxar
        seen, items, skipped = set(), [], 0
        for r in user_data['results']:
            vid = video_id_from_url(r.get('url'))
            if not vid or vid in seen:
                continue
            seen.add(vid)
            if (r.get('raw_duration') or 0) > BATCH_MAX_DURATION:
                skipped += 1
                continue
            if song_names.non_music_reason(r.get('original_title') or r.get('title') or "", r.get('raw_duration') or 0,
                                           user_data.get('query') or "", min_sec=60):
                skipped += 1                       # xəbər, film fraqmenti, serial ... "Hamısını yüklə"-yə düşməsin
                continue
            items.append(r)
        if not items:
            await callback.answer(tr("Yüklənəcək mahnı yoxdur"), show_alert=True)
            return

        await callback.answer(tr("⬇️ {n} mahnı yüklənir...", n=len(items)))
        source = "mix" if user_data.get('command_used') == "mix" else "batch"
        header = user_data.get('header', "🎵 Axtarış")
        user_searches.pop(user_id, None)          # siyahı artıq bu iş üçündür
        await run_batch(callback.message, items, header, user_id, source,
                        skipped=skipped, original_message_id=user_data['original_message_id'])

    # ── ⬇️ Toplu yükləmə axını (bot.music.batch) ──
    from bot.music.batch import register_batch_system
    run_batch = register_batch_system(
        dp=dp,
        context=context,
        prepare_track_fn=prepare_track,
        deliver_track_fn=deliver_track,
        maybe_send_tip_prompt_fn=maybe_send_tip_prompt,
        video_id_from_url_fn=video_id_from_url,
        log_download_fn=log_download,
    )

    context.music_run_batch = run_batch
    context.music_fetch_audio = fetch_audio          # depo_filler_plugin: mahnını depoya yükləmək üçün
    context.music_resolve_youtube = resolve_youtube
    context.music_songlink = get_songlink

    # ── ⚡ İnline axtarış və çatdırılma (bot.music.inline) ──
    from bot.music.inline import register_inline_system
    register_inline_system(
        dp=dp,
        context=context,
        fetch_audio_fn=fetch_audio,
        parse_title_fn=parse_title,
        build_caption_fn=build_caption,
        build_keyboard_fn=build_keyboard,
        stop_keyboard_fn=stop_keyboard,
        hide_keyboard_later_fn=hide_keyboard_later,
        log_download_fn=log_download,
        video_id_from_url_fn=video_id_from_url,
        name_hits_fn=name_hits,
        mark_named_fn=mark_named,
    )
