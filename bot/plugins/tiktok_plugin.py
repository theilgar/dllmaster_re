"""
🎵 TikTok → tam mahnı — bot/plugins/tiktok_plugin.py

TikTok linki atılanda (şəxsi çatda və ya qrupda /tt <link>) videonun mahnısı tapılır və TAM versiyası yüklənir:
  Mərhələlər (ilk uğurlu olanda dayanır, hər mərhələ istifadəçiyə göstərilir):
  1) yt-dlp: TikTok məlumatından mahnı adı / ifaçı (track, artist);
  2) səhifə: TikTok səhifəsinin JSON-undakı `music` bloku (ad, müəllif, səsin ayrıca faylı);
  3) səsin ayrıca faylı (musiqi, danışıqsız) → çoxmərhələli Shazam;
  4) videonun səsi → çoxmərhələli Shazam (bot/plugins/shazam_plugin.py);
  5) tapılan "ifaçı - ad" depoda axtarılır, yoxdursa YouTube / YT Music-də ən uyğun TAM mahnı seçilir
     (klip fraqmenti, remix, xəbər və s. süzülür) və adi /music axını ilə (depo, keş, metadata) göndərilir.
"""
import asyncio
import contextlib
import logging
import os
import re
import threading
import time
import uuid
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject

from bot.core import audio_cache, song_names
from bot.core.links import video_id_from_url
from common.i18n import _ as tr
from common.utilities import get_http_session, spawn

logger = logging.getLogger(__name__)

PRIORITY = 12                      # links_plugin (10)-dən sonra, music_plugin-dən ƏVVƏL: link axtarış sayılmasın

TT_RE = re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/[^\s]+", re.I)
GENERIC = ("original sound", "orijinal ses", "orijinal səs", "orijinal ses", "оригинальный звук", "son original",
           "sonido original", "som original", "original audio", "suara asli")
MIN_FULL, MAX_FULL = 100, 600      # "tam mahnı" müddəti (san.): TikTok fraqmentindən fərqli olaraq


def find_url(text: str):
    m = TT_RE.search(text or "")
    return m.group(0).rstrip(").,]") if m else None


def is_generic_sound(track: str, uploader: str = "") -> bool:
    low = (track or "").strip().lower()
    if not low:
        return True
    if any(g in low for g in GENERIC):
        return True
    return bool(uploader) and low.startswith(uploader.strip().lower()) and "-" not in low


def info_to_query(info: dict):
    """yt-dlp məlumatından (artist, ad) və ya None."""
    track = (info.get("track") or "").strip()
    artist = (info.get("artist") or "").strip()
    if not artist and info.get("artists"):
        artist = ", ".join(a for a in info["artists"][:2] if a)
    if track and not is_generic_sound(track, info.get("uploader") or ""):
        return artist, track
    return None


_JSON_SCRIPT = re.compile(r'<script[^>]+id="(?:__UNIVERSAL_DATA_FOR_REHYDRATION__|SIGI_STATE)"[^>]*>(.*?)</script>', re.S)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def find_music(obj, depth: int = 0):
    """TikTok səhifə JSON-unda `music` blokunu tap: {title, authorName, playUrl, original}."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        m = obj.get("music")
        if isinstance(m, dict) and (m.get("title") or m.get("playUrl")):
            return m
        for v in obj.values():
            r = find_music(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:50]:
            r = find_music(v, depth + 1)
            if r:
                return r
    return None


_RX_MUSIC = re.compile(r'"music":\{(.{0,1500}?)\}', re.S)
_RX_STR = lambda key: re.compile(r'"' + key + r'":"((?:[^"\\]|\\.)*)"')


def _unescape(v: str) -> str:
    import json
    try:
        return json.loads('"' + v + '"')
    except ValueError:
        return v.replace("\\u002F", "/").replace("\\/", "/")


def _parse_music_regex(html: str):
    """JSON quruluşu dəyişəndə ehtiyat: xam HTML-də `"music":{...}` blokunu regex ilə çıxar."""
    m = _RX_MUSIC.search(html or "")
    if not m:
        return None
    blk = m.group(1)
    g = lambda k: (lambda r: _unescape(r.group(1)) if r else "")(_RX_STR(k).search(blk))
    title, author, play = g("title"), g("authorName") or g("author"), g("playUrl")
    if not (title or play):
        return None
    return {"title": title.strip(), "author": author.strip(), "play_url": play,
            "original": '"original":true' in blk}


def parse_page_music(html: str):
    """HTML → {title, author, play_url, original} və ya None (video və FOTO postlar üçün)."""
    import json
    m = _JSON_SCRIPT.search(html or "")
    if m:
        try:
            mu = find_music(json.loads(m.group(1)))
        except ValueError:
            mu = None
        if mu:
            return {"title": (mu.get("title") or "").strip(),
                    "author": (mu.get("authorName") or mu.get("author") or "").strip(),
                    "play_url": mu.get("playUrl") or mu.get("play_url") or "",
                    "original": bool(mu.get("original"))}
    return _parse_music_regex(html)


def url_variants(url: str) -> list:
    """Foto postlar (/photo/ID) üçün də /video/ID variantı: TikTok eyni postu hər iki yolda verir."""
    out = [url]
    if "/photo/" in url:
        out.append(url.replace("/photo/", "/video/"))
    elif "/video/" in url:
        out.append(url.replace("/video/", "/photo/"))
    return out


def page_to_query(page: dict):
    """Səhifə məlumatından (artist, ad) — yalnız orijinal səs deyilsə."""
    if not page or not page.get("title") or is_generic_sound(page["title"], page.get("author") or ""):
        return None
    if page.get("original") and not page.get("author"):
        return None
    return page.get("author") or "", page["title"]


def pick_full(query: str, results: list):
    """Nəticələrdən TAM mahnı: uyğunluq balı yüksək, musiqiyə oxşayan, normal müddətli."""
    best, best_score = None, 0.0
    for rank, r in enumerate(results or []):
        if not video_id_from_url(r.get("url")):
            continue
        title = r.get("original_title") or r.get("title") or ""
        dur = int(r.get("raw_duration") or 0)
        if song_names.non_music_reason(title, dur, query, min_sec=60):
            continue
        artist, track = song_names.split_title(title)
        sc = song_names.query_score(query, artist or "", track or title)
        if MIN_FULL <= dur <= MAX_FULL:
            sc += 0.1                                # tam mahnı müddəti
        elif dur and dur < 60:
            sc -= 0.3                                # fraqment
        sc -= 0.01 * rank
        if r.get("source") == "ytm":
            sc += 0.05
        if sc > best_score:
            best, best_score = r, sc
    return best if best_score >= 0.6 else None


def setup(context):
    dp = context.dp

    async def _extract(url: str) -> dict:
        from bot.core import cpu_pool, youtube_handler as yh
        opts = {"quiet": True, "noplaylist": True, "skip_download": True, "socket_timeout": 20,
                **yh._px().ytdlp_opts("search")}
        return await cpu_pool.ytdl_extract(url, opts, timeout=60)

    async def _shazam_from_video(url: str):
        """Videonun səsi → Shazam. (artist, ad) və ya None."""
        from bot.core import cpu_pool, youtube_handler as yh
        from bot.plugins.shazam_plugin import recognize_file
        base = os.path.abspath(os.path.join(yh.get_download_dir(), f"tt_{uuid.uuid4().hex[:8]}"))
        opts = {"format": "bestaudio/best", "outtmpl": base + ".%(ext)s", "noplaylist": True, "quiet": True,
                "nopart": True, "max_filesize": 40 * 1024 * 1024, "socket_timeout": 20,
                **yh._px().ytdlp_opts("download")}
        for u in url_variants(url):                      # foto post: /photo/ → /video/ (yt-dlp yalnız /video/ tanıyır)
            path = None
            try:
                res = await cpu_pool.ytdl_download(u, opts, None, timeout=120)
                path = res.get("path")
                item = await recognize_file(path) if path and os.path.exists(path) else None
                if item:
                    return item["artist"], item["title"], bool(item.get("confirmed"))
            except Exception as e:
                logger.info(f"TikTok səsi tanınmadı ({u[:70]}): {str(e)[:120]}")
            finally:
                if path:
                    with contextlib.suppress(OSError):
                        os.remove(path)
        return None

    async def _page_music(url: str):
        """Səhifəni oxuyub music blokunu çıxarır: qısa linklər yönləndirilir; video və FOTO postlar; 2 fərqli UA."""
        import aiohttp
        uas = (UA, "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1")
        sess = await get_http_session()
        final = url
        for u in url_variants(url):
            for ua in uas:
                try:
                    async with sess.get(u, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
                                        allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as r:
                        if r.status != 200:
                            logger.info(f"TikTok səhifə {r.status}: {u[:80]}")
                            continue
                        final = str(r.url)
                        html = await r.text(errors="ignore")
                    page = parse_page_music(html)
                    if page:
                        return page
                    if final != u:                       # qısa link → real ünvanın variantları da sınansın
                        for v in url_variants(final):
                            if v != u:
                                async with sess.get(v, headers={"User-Agent": ua}, allow_redirects=True,
                                                    timeout=aiohttp.ClientTimeout(total=15)) as r2:
                                    if r2.status == 200:
                                        page = parse_page_music(await r2.text(errors="ignore"))
                                        if page:
                                            return page
                except Exception as e:
                    logger.info(f"TikTok səhifəsi oxunmadı ({u[:60]}): {str(e)[:100]}")
        return None

    async def _shazam_sound(play_url: str):
        """TikTok-un səs faylını endirib (≤10 MB) Shazam ilə tanıyır."""
        import aiohttp
        from bot.core import youtube_handler as yh
        from bot.plugins.shazam_plugin import recognize_file
        path = os.path.abspath(os.path.join(yh.get_download_dir(), f"tts_{uuid.uuid4().hex[:8]}.mp3"))
        try:
            sess = await get_http_session()
            async with sess.get(play_url, headers={"User-Agent": UA, "Referer": "https://www.tiktok.com/"},
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return None
                data = await r.content.read(10 * 1024 * 1024)
            with open(path, "wb") as f:
                f.write(data)
            item = await recognize_file(path)
            return (item["artist"], item["title"], bool(item.get("confirmed"))) if item else None
        except Exception as e:
            logger.info(f"TikTok səs faylı tanınmadı: {str(e)[:120]}")
            return None
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)

    decisions = {}          # token -> Future[bool]

    async def _ask_confirm(message, status, heard: str, will_get: str) -> bool:
        """Əmin olmayanda soruş: yüklənəcək mahnı düzgündürmü? (60 san. cavab yoxdursa — ləğv)."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        tok = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        decisions[tok] = (fut, message.from_user.id if message.from_user else 0)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("✅ Bəli, yüklə"), callback_data=f"tt:ok:{tok}"),
            InlineKeyboardButton(text=tr("❌ Səhvdir"), callback_data=f"tt:no:{tok}")]])
        with contextlib.suppress(Exception):
            await status.edit_text(
                tr("🤔 <b>Tam əmin deyiləm.</b>\nTanınan: <b>{heard}</b>\nYüklənəcək: <b>{will}</b>\n<i>Düzgündürmü?</i>",
                   heard=escape(heard), will=escape(will_get[:70])),
                parse_mode="HTML", reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, 60)
        except asyncio.TimeoutError:
            return False
        finally:
            decisions.pop(tok, None)

    @dp.callback_query(F.data.startswith("tt:"))
    async def tt_cb(cb: types.CallbackQuery):
        _, act, tok = (cb.data.split(":") + ["", ""])[:3]
        fut, owner = decisions.get(tok, (None, 0))
        if fut is None or fut.done():
            await cb.answer(tr("Müddət bitib"), show_alert=True)
            return
        if cb.from_user.id != owner and not context.is_creator(cb.from_user.id):
            await cb.answer(tr("⛔ Yalnız linki göndərən"), show_alert=True)
            return
        fut.set_result(act == "ok")
        await cb.answer(tr("✅ Yüklənir...") if act == "ok" else tr("Ləğv edildi"))

    async def _resolve(url: str) -> str:
        """Qısa linki (vt./vm.tiktok.com) real ünvana çevirir — /photo/ ↔ /video/ variantı yalnız real ünvanda mümkündür."""
        if re.search(r"tiktok\.com/@[^/]+/(?:video|photo)/\d+", url):
            return url
        import aiohttp
        try:
            sess = await get_http_session()
            async with sess.get(url, headers={"User-Agent": UA}, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=12)) as r:
                final = str(r.url)
            return final if "tiktok.com" in final else url
        except Exception as e:
            logger.info(f"TikTok qısa linki açılmadı: {str(e)[:100]}")
            return url

    async def handle(message: types.Message, url: str):
        status = await message.reply(tr("🎵 <i>TikTok mahnısı axtarılır...</i>"), parse_mode="HTML")
        url = await _resolve(url)

        async def say(txt: str):
            with contextlib.suppress(Exception):
                await status.edit_text(txt, parse_mode="HTML")

        found, source, page = None, "", None
        STAGES = 4

        async def stage(n: int, label: str):
            await say(tr("🎵 <i>TikTok mahnısı axtarılır — mərhələ {n}/{m}: {label}...</i>", n=n, m=STAGES, label=tr(label)))

        # 1) yt-dlp məlumatı
        await stage(1, "TikTok məlumatı")
        for u in url_variants(url):
            try:
                info = await _extract(u)
                found = info_to_query(info)
                if found:
                    source = "TikTok"
                    break
            except Exception as e:
                logger.info(f"TikTok məlumatı alınmadı ({u[:70]}): {str(e)[:120]}")
        # 2) səhifənin music bloku
        if not found:
            await stage(2, "səhifədəki musiqi bloku")
            page = await _page_music(url)
            found = page_to_query(page)
            if found:
                source = tr("TikTok səhifəsi")
        # 3) səsin ayrıca faylı (danışıqsız musiqi) → Shazam
        if not found and page and page.get("play_url"):
            await stage(3, "səsin təmiz faylı → Shazam")
            found = await _shazam_sound(page["play_url"])
            if found:
                source = tr("Shazam (səs faylı)")
        # 4) videonun səsi → Shazam
        if not found:
            await stage(4, "videonun səsi → Shazam (çoxmərhələli)")
            found = await _shazam_from_video(url)
            if found:
                source = tr("Shazam (video)")
        if not found:
            await say(tr("❌ <b>Mahnı tanınmadı.</b>\n<i>Orijinal səs, foto post (səhifə bağlıdır) və ya səs-küylü videodur. "
                         "Mahnının adını yazsanız, tapıb yükləyərəm.</i>"))
            return
        confident = True
        if len(found) == 3:                                  # Shazam nəticəsi: (ifaçı, ad, təsdiqləndi?)
            artist, track, confident = found
        else:
            artist, track = found
        query = f"{artist} {track}".strip() if artist else track
        await say(tr("🔎 <b>{q}</b>\n<i>({src}) tam versiya axtarılır...</i>", q=escape(query), src=source))

        # 1) depo
        selected = None
        hits = await asyncio.to_thread(audio_cache.search_depo, query, 5)
        for h in hits or []:
            if song_names.query_score(query, h.get("performer") or "", h.get("title") or "") >= 0.85:
                dur = int(h.get("duration") or 0)
                selected = {"title": f"{h.get('performer')} - {h.get('title')}".strip(" -"),
                            "url": f"https://www.youtube.com/watch?v={h['video_id']}",
                            "duration": f"{dur // 60}:{dur % 60:02d}", "raw_duration": dur, "depo": True,
                            "source": "ytm"}
                break
        # 2) YouTube Music / YouTube
        if not selected:
            try:
                results = await context.youtube_manager.youtube_search(query, limit=10)
            except Exception as e:
                await say(tr("❌ <b>Axtarış alınmadı:</b> <code>{e}</code>", e=escape(str(e))[:200]))
                return
            selected = pick_full(query, results)
            if not selected:
                await say(tr("❌ <b>Tam versiya tapılmadı:</b> {q}\n<i>Əl ilə axtarın:</i> <code>/music {q}</code>", q=escape(query)))
                return
            selected = dict(selected)
        selected.setdefault("original_title", selected["title"])

        if not confident:                                    # Shazam bir parçadan tanıyıb, ikinci parça təsdiq etməyib
            ok = await _ask_confirm(message, status, f"{artist} - {track}".strip(" -"), selected["title"])
            if not ok:
                await say(tr("⏹ <b>Ləğv edildi.</b>\n<i>Düzgün adı bilirsinizsə:</i> <code>/music {q}</code>", q=escape(query)))
                return

        await say(tr("⬇️ <b>{t}</b>\n<i>Yüklənir...</i>", t=escape(selected['title'][:70])))
        user_id = message.from_user.id if message.from_user else 0
        event = threading.Event()
        try:
            res = await context.music_prepare_track(selected, event, user_id=user_id, chat_id=message.chat.id)
            await context.music_deliver_track(message, selected, res, user_id, event)
        except Exception as e:
            logger.warning(f"TikTok mahnısı yüklənmədi ({query}): {e}")
            await say(tr("❌ <b>Yüklənmədi:</b> <code>{e}</code>", e=escape(str(e))[:200]))
            return
        with contextlib.suppress(Exception):
            from common.database import log_download
            await log_download(user_id, f"{artist} - {track}".strip(" -"), selected["url"], "tiktok",
                               chat_id=message.chat.id)
        with contextlib.suppress(Exception):
            await status.delete()

    async def has_tt_private(message: types.Message) -> bool:
        if not message.text or not find_url(message.text):
            return False
        from bot.core.input_wait import waiting_any
        return not (message.from_user and waiting_any(context, message.from_user.id))

    @dp.message(F.chat.type == "private", F.text, ~F.via_bot, has_tt_private)
    async def tt_private(message: types.Message):
        await handle(message, find_url(message.text))

    @dp.message(Command("tt", "tiktok"))
    async def tt_cmd(message: types.Message, command: CommandObject):
        url = find_url(command.args or "") or find_url(getattr(message.reply_to_message, "text", "") or "")
        if not url:
            await message.reply(tr("ℹ️ <code>/tt TikTok-linki</code> — videonun mahnısını tam versiya yükləyir.\n"
                                   "<i>Və ya linkə reply edib</i> <code>/tt</code>"), parse_mode="HTML")
            return
        await handle(message, url)

    logger.info("✅ TikTok plugini yükləndi (TikTok linki → tam mahnı)")
