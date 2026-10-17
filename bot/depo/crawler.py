"""
🎵 bot/depo/crawler.py - YouTube Music kataloqu, Spotify və iTunes skaneri.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import aiohttp

from bot.depo.cleaner import _toks
from common.database import get_db

try:
    from ytmusicapi import YTMusic
    HAS_YTM = True
except ImportError:
    HAS_YTM = False

logger = logging.getLogger(__name__)

# 🎵 YouTube Music kataloqu
YTM_COUNTRIES = ["ZZ", "AZ", "TR", "RU", "US", "GB", "DE", "UA", "KZ", "UZ", "GE", "IR", "IN", "BR", "FR", "KR"]
COUNTRIES = {
    "AZ": "🇦🇿 Azərbaycan", "TR": "🇹🇷 Türkiyə", "RU": "🇷🇺 Rusiya", "UA": "🇺🇦 Ukrayna", "GE": "🇬🇪 Gürcüstan",
    "KZ": "🇰🇿 Qazaxıstan", "UZ": "🇺🇿 Özbəkistan", "IR": "🇮🇷 İran", "US": "🇺🇸 ABŞ", "GB": "🇬🇧 Britaniya",
    "DE": "🇩🇪 Almaniya", "FR": "🇫🇷 Fransa", "IT": "🇮🇹 İtaliya", "ES": "🇪🇸 İspaniya", "BR": "🇧🇷 Braziliya",
    "MX": "🇲🇽 Meksika", "IN": "🇮🇳 Hindistan", "KR": "🇰🇷 Koreya", "JP": "🇯🇵 Yaponiya", "ZZ": "🌐 Qlobal",
}
COUNTRY_PRIO_TOP = 5

YTM_OFFICIAL_CHART_COUNTRIES = {
    "AR", "AU", "AT", "BE", "BO", "BR", "CA", "CL", "CO", "CR", "CZ", "DK", "DO", "EC", "EG",
    "SV", "EE", "FI", "FR", "DE", "GT", "HN", "HK", "HU", "IS", "IN", "ID", "IE", "IL", "IT",
    "JP", "KE", "LU", "MY", "MX", "NL", "NZ", "NI", "NG", "NO", "PA", "PY", "PE", "PH", "PL",
    "PT", "RO", "RU", "SA", "RS", "SG", "ZA", "KR", "ES", "SE", "CH", "TW", "TZ", "TH", "TR",
    "UG", "UA", "AE", "GB", "US", "UY", "VN", "ZW", "ZZ"
}

COUNTRY_SEARCH_QUERIES = {
    "AZ": [
        "Trend Azərbaycan",
        "Azərbaycan mahnıları",
        "Azerbaycan Pop",
        "Azeri Trend 2026",
        "Azerbaycan Hit Mahnılar",
        "Yeni Mahnilar Azeri",
        "Azerbaycan Meyxana",
        "Azeri Bass Music",
    ],
    "TR": [
        "Trend Türkiye",
        "Türkçe Pop",
        "En Çok Dinlenen Türkçe Şarkılar",
        "Trend Şarkılar Türkçe",
        "Türkçe Rap",
    ],
    "RU": [
        "Тренды Россия",
        "Русские хиты",
        "Топ чарт Россия",
        "Популярные песни Россия",
    ],
    "UA": [
        "Тренди Україна",
        "Українські хіти",
        "Топ чарт Україна",
        "Популярні українські пісні",
    ],
    "KZ": [
        "Қазақша әндер",
        "Тренды Казахстан",
        "Казахские хиты",
        "Top Kazakhstan",
    ],
    "UZ": [
        "O'zbek qo'shiqlari",
        "Trend O'zbekiston",
        "Uzbek music",
        "Top Uzbek",
    ],
    "GE": [
        "Georgian songs",
        "ქართული სიმღერები",
        "Trend Georgia",
    ],
    "IR": [
        "Persian music",
        "آهنگ های جدید",
        "Iranian top hits",
    ],
}
DEFAULT_COUNTRY_SEARCH_QUERIES = COUNTRY_SEARCH_QUERIES

YTM_LOW_WATER = 150
YTM_PAUSE = 1.0
YTM_MAX_ALBUMS = 60
YTM_MOOD_PLAYLISTS = 2
YTM_RECRAWL = 14 * 86400
YTM_FAIL_RETRY = 3 * 86400
YTM_SEEN_CAP = 300_000
MAX_DURATION = 15 * 60
ACCEPT_SCORE = 0.6


def _is_valid_chart_for(charts: dict, cc: str) -> bool:
    if not charts:
        return False
    if cc == "ZZ":
        return True
    sel = (charts.get("countries", {}).get("selected", {}).get("text") or "").strip().lower()
    if sel == "global":
        return False
    opts = charts.get("countries", {}).get("options", [])
    if opts and cc not in opts:
        return False
    return True


def match_score(artist: str, track: str, c_artist: str, c_track: str, dur: int = 0, c_dur: int = 0) -> float:
    qt, ct = _toks(track), _toks(c_track)
    if not qt or not ct:
        return 0.0
    common = len(qt & ct)
    title_sim = (common / len(qt) + common / len(ct)) / 2
    if artist:
        qa = _toks(artist)
        ca = _toks(c_artist) | ct
        artist_sim = len(qa & ca) / len(qa) if qa else 0.0
        score = 0.55 * title_sim + 0.45 * artist_sim
        if artist_sim == 0:
            score = min(score, 0.45)
    else:
        score = title_sim * 0.8 if title_sim >= 0.9 else title_sim * 0.5
    if dur and c_dur:
        diff = abs(int(dur) - int(c_dur))
        score -= 0 if diff <= 10 else (0.1 if diff <= 30 else 0.3)
    return score


async def spotify_lookup(spotify, artist: str, track: str, dur: int):
    if not spotify or not getattr(spotify, "sp", None):
        return None
    q = f"{artist} {track}".strip()
    res = await asyncio.to_thread(spotify.sp.search, q=q, type="track", limit=6)
    best, best_s = None, 0.0
    for t in (res.get("tracks") or {}).get("items") or []:
        names = [a["name"] for a in t.get("artists") or [] if a.get("name")]
        s = match_score(artist, track, " ".join(names), t.get("name", ""), dur, (t.get("duration_ms") or 0) // 1000)
        if s > best_s:
            best, best_s = t, s
    if not best or best_s < ACCEPT_SCORE:
        return None
    imgs = sorted((best.get("album") or {}).get("images") or [], key=lambda i: i.get("width") or 0)
    fit = [i for i in imgs if (i.get("width") or 0) <= 320]
    names = [a["name"] for a in best["artists"] if a.get("name")]
    return {
        "artist": ", ".join(names[:3]),
        "first": names[0] if names else "",
        "track": best["name"],
        "duration": (best.get("duration_ms") or 0) // 1000,
        "sp_id": best.get("id"),
        "link": f"https://open.spotify.com/track/{best['id']}" if best.get("id") else None,
        "thumb": ((fit[-1] if fit else imgs[0])["url"] if imgs else None),
        "src": "spotify",
    }


async def itunes_lookup(artist: str, track: str, dur: int):
    q = f"{artist} {track}".strip()
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://itunes.apple.com/search",
                               params={"term": q, "entity": "song", "limit": "6"}) as r:
            if r.status != 200:
                return None
            data = json.loads(await r.text())
    best, best_s = None, 0.0
    for t in data.get("results") or []:
        s = match_score(artist, track, t.get("artistName", ""), t.get("trackName", ""), dur,
                        (t.get("trackTimeMillis") or 0) // 1000)
        if s > best_s:
            best, best_s = t, s
    if not best or best_s < ACCEPT_SCORE:
        return None
    return {
        "artist": best.get("artistName", ""),
        "first": best.get("artistName", ""),
        "track": best.get("trackName", ""),
        "duration": (best.get("trackTimeMillis") or 0) // 1000,
        "sp_id": None,
        "link": best.get("trackViewUrl"),
        "thumb": (best.get("artworkUrl100") or "").replace("100x100bb", "300x300bb") or None,
        "src": "itunes",
    }


def _ytm_duration(t) -> int:
    if t.get("duration_seconds"):
        return int(t["duration_seconds"])
    d = str(t.get("duration") or "")
    if re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", d):
        sec = 0
        for part in d.split(":"):
            sec = sec * 60 + int(part)
        return sec
    return 0


def _ytm_thumb(thumbs):
    thumbs = sorted([x for x in thumbs or [] if x.get("url")], key=lambda x: x.get("width") or 0)
    if not thumbs:
        return None
    fit = [x for x in thumbs if (x.get("width") or 0) <= 320]
    return (fit[-1] if fit else thumbs[0])["url"]


def ytm_item(t: dict, fallback_artists=None, fallback_thumbs=None):
    vid = t.get("videoId")
    if not vid or t.get("isAvailable") is False:
        return None
    title = (t.get("title") or "").strip()
    arts = [a["name"] for a in (t.get("artists") or fallback_artists or []) if a.get("name")]
    if not title:
        return None
    artist = ", ".join(arts[:3])
    dur = _ytm_duration(t)
    if dur > MAX_DURATION:
        return None
    return {
        "title": f"{artist} - {title}" if artist else title,
        "query": f"{arts[0] if arts else ''} {title}".strip(),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "vid": vid,
        "raw_duration": dur,
        "meta": {"artist": artist, "track": title} if artist else None,
        "thumb": _ytm_thumb(t.get("thumbnails") or fallback_thumbs),
        "sp_id": None,
        "src": "ytm",
    }


def _chart_list(sec) -> list:
    if isinstance(sec, dict):
        return sec.get("items") or sec.get("results") or []
    return sec if isinstance(sec, list) else []


def _chart_tracks(yt, charts: dict, max_playlists: int = 2, limit: int = 100) -> list:
    tracks, pls = [], 0
    for key in ("songs", "videos", "trending", "daily", "weekly"):
        for e in _chart_list(charts.get(key)):
            if not isinstance(e, dict):
                continue
            if e.get("videoId"):
                tracks.append(e)
            elif e.get("playlistId") and pls < max_playlists:
                try:
                    tracks += (yt.get_playlist(e["playlistId"], limit=limit) or {}).get("tracks") or []
                    pls += 1
                except Exception as ex:
                    logger.debug(f"chart playlist {e.get('playlistId')}: {ex}")
                time.sleep(YTM_PAUSE)
    return tracks


def _chart_artists(charts: dict) -> list:
    out = []
    for a in _chart_list(charts.get("artists")):
        if isinstance(a, dict) and a.get("browseId"):
            out.append((a["browseId"], a.get("title") or a.get("artist") or ""))
    return out


class YtmCrawler:
    """YouTube Music ifaçı qrafı skaneri."""

    def __init__(self):
        self.yt = None
        self.stats = {"artist": None, "found": 0, "in_depo": 0, "note": "", "err": 0, "err_at": 0.0}
        self.countries = []
        self.filler = None
        self._inflight = {}                 # aid → ad: hazırda oxunan ifaçılar (paralel yuvalar eyni ifaçını götürmür)
        self._seed_lock = None              # tənbəl: event loop-a bağlıdır
        self._claim_lock = None

    def client(self):
        if self.yt is None:
            try:
                self.yt = YTMusic(language="en", location="AZ")
            except TypeError:
                self.yt = YTMusic()
        return self.yt

    def counts(self) -> dict:
        try:
            rows = get_db().sql_all("SELECT state, COUNT(*), SUM(songs) FROM depo_ytm GROUP BY state")
        except Exception:
            return {"pending": 0, "done": 0, "failed": 0, "songs": 0}
        c = {"pending": 0, "done": 0, "failed": 0, "songs": 0}
        for st, n, songs in rows:
            c[{0: "pending", 1: "done", 2: "failed"}.get(st, "pending")] += n
            c["songs"] += songs or 0
        return c

    def add_artists(self, pairs, prio: int = 0) -> int:
        pairs = [(a, n) for a, n in pairs if a and str(a).startswith("UC")]
        if not pairs:
            return 0
        with get_db().sql() as cur:
            cur.executemany("INSERT INTO depo_ytm (aid, name, state, ts, prio) VALUES (%s, %s, 0, 0, %s) "
                            "ON CONFLICT DO NOTHING", [(a, n, prio) for a, n in pairs])
            added = max(cur.rowcount, 0)
            if prio > 0:
                cur.executemany("UPDATE depo_ytm SET prio = %s WHERE aid = %s AND COALESCE(prio, 0) < %s",
                                [(prio, a, prio) for a, _ in pairs])
        return added

    def has_prio_pending(self) -> bool:
        try:
            return bool(get_db().sql_one(
                "SELECT 1 FROM depo_ytm WHERE state = 0 AND COALESCE(prio, 0) > 0 LIMIT 1"))
        except Exception:
            return False

    def reset_prio(self):
        with get_db().sql() as cur:
            cur.execute("UPDATE depo_ytm SET prio = 0 WHERE prio > 0")

    def _next_pending(self, busy=None):
        busy = list(self._inflight) if busy is None else list(busy)
        strict = " AND COALESCE(prio, 0) > 0" if self.filler and self.filler.country_strict() else ""
        return get_db().sql_one(
            "SELECT aid, name, COALESCE(prio, 0) FROM depo_ytm WHERE state = 0" + strict
            + " AND NOT (aid = ANY(%s::text[])) ORDER BY COALESCE(prio, 0) DESC, seq LIMIT 1", (busy,))

    async def _claim(self):
        """Növbəti ifaçını götürür və 'oxunur' işarələyir. Baza sorğusu thread-dədir (əvvəl event loop-u saniyələrlə
        bloklayırdı: «yavaş baza sorğusu 8606 ms ← _next_pending»); yuvalar eyni ifaçını götürməsin deyə sorğu + işarə
        bir kilid altındadır."""
        if self._claim_lock is None:
            self._claim_lock = asyncio.Lock()
        async with self._claim_lock:
            row = await asyncio.to_thread(self._next_pending, list(self._inflight))
            if row:
                self._inflight[row[0]] = row[1] or row[0]
                self._show_reading()
        return row

    def _release(self, aid):
        self._inflight.pop(aid, None)
        self._show_reading()

    def _show_reading(self):
        names = list(self._inflight.values())
        self.stats["artist"] = " · ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "") if names else None

    def _mark(self, aid, state, songs=0):
        with get_db().sql() as cur:
            cur.execute("UPDATE depo_ytm SET state = %s, songs = %s, ts = %s WHERE aid = %s",
                        (state, songs, int(time.time()), aid))

    def _requeue_old(self) -> int:
        now = int(time.time())
        with get_db().sql() as cur:
            return cur.execute("""UPDATE depo_ytm SET state = 0 WHERE aid IN (
                          SELECT aid FROM depo_ytm WHERE (state = 1 AND ts < %s) OR (state = 2 AND ts < %s)
                          ORDER BY ts LIMIT 2000)""", (now - YTM_RECRAWL, now - YTM_FAIL_RETRY)).rowcount

    def _seed_sync(self, user_artists, stop) -> list:
        yt, found = self.client(), {}
        if self.countries:
            order = list(self.countries)
        else:
            order = [c for c in YTM_COUNTRIES if c in YTM_OFFICIAL_CHART_COUNTRIES]
        for country in order:
            if stop.is_set():
                break
            if country in YTM_OFFICIAL_CHART_COUNTRIES:
                try:
                    charts = yt.get_charts(country=country) or {}
                    if _is_valid_chart_for(charts, country):
                        for aid, name in _chart_artists(charts):
                            found[aid] = name
                except Exception as e:
                    logger.debug(f"YTM chart {country}: {e}")
                time.sleep(YTM_PAUSE)
            if hasattr(self, "filler") and self.filler:
                queries = self.filler.get_active_country_queries(country)
            else:
                queries = COUNTRY_SEARCH_QUERIES.get(country, [])
            if queries and (country not in YTM_OFFICIAL_CHART_COUNTRIES or not self.countries):
                for q in queries[:2]:
                    if stop.is_set():
                        break
                    try:
                        pls = yt.search(q, filter="playlists", limit=2) or []
                        for pl in pls[:2]:
                            pid = pl.get("browseId") or pl.get("playlistId")
                            if not pid:
                                continue
                            if pid.startswith("VL"):
                                pid = pid[2:]
                            pldata = yt.get_playlist(pid, limit=40) or {}
                            for t in pldata.get("tracks", []):
                                for a in t.get("artists", []):
                                    if a.get("id") and a.get("name"):
                                        found[a["id"]] = a["name"]
                    except Exception as e:
                        logger.debug(f"YTM seed search {country} ({q}): {e}")
                    time.sleep(YTM_PAUSE)
        if not self.countries:
            for name in user_artists:
                if stop.is_set():
                    break
                try:
                    res = yt.search(name, filter="artists", limit=1) or []
                    if res and res[0].get("browseId"):
                        found[res[0]["browseId"]] = res[0].get("artist") or name
                except Exception as e:
                    logger.debug(f"YTM ifaçı axtarışı {name}: {e}")
                time.sleep(YTM_PAUSE)
            try:
                cats = yt.get_mood_categories() or {}
            except Exception as e:
                logger.debug(f"YTM janrlar: {e}")
                cats = {}
            for section in cats.values():
                for mood in section or []:
                    if stop.is_set():
                        break
                    try:
                        pls = yt.get_mood_playlists(mood["params"]) or []
                    except Exception:
                        continue
                    for pl in pls[:YTM_MOOD_PLAYLISTS]:
                        try:
                            tracks = (yt.get_playlist(pl["playlistId"], limit=100) or {}).get("tracks") or []
                        except Exception:
                            continue
                        for t in tracks:
                            for a in t.get("artists") or []:
                                if a.get("id"):
                                    found[a["id"]] = a.get("name") or ""
                        time.sleep(YTM_PAUSE)
        return list(found.items())

    def _artist_sync(self, aid, stop):
        yt = self.client()
        a = yt.get_artist(aid) or {}
        name = a.get("name") or ""
        tracks = []
        songs = a.get("songs") or {}
        if songs.get("browseId"):
            try:
                tracks += (yt.get_playlist(songs["browseId"], limit=None) or {}).get("tracks") or []
            except Exception as e:
                logger.debug(f"YTM {name}: mahnılar playlisti: {e}")
                tracks += songs.get("results") or []
            time.sleep(YTM_PAUSE)
        else:
            tracks += songs.get("results") or []
        me = [{"name": name, "id": aid}]
        for key in ("albums", "singles"):
            sec = a.get(key) or {}
            albums = sec.get("results") or []
            if sec.get("browseId") and sec.get("params"):
                try:
                    albums = yt.get_artist_albums(sec["browseId"], sec["params"], limit=None) or albums
                except TypeError:
                    try:
                        albums = yt.get_artist_albums(sec["browseId"], sec["params"]) or albums
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/depo/crawler.py]: %r", _e)
                except Exception as e:
                    logger.debug(f"YTM {name}: {key}: {e}")
                time.sleep(YTM_PAUSE)
            for al in albums[:YTM_MAX_ALBUMS]:
                if stop.is_set():
                    break
                bid = al.get("browseId")
                if not bid:
                    continue
                try:
                    alb = yt.get_album(bid) or {}
                except Exception:
                    continue
                for t in alb.get("tracks") or []:
                    t["_fa"] = alb.get("artists") or me
                    t["_ft"] = alb.get("thumbnails")
                    tracks.append(t)
                time.sleep(YTM_PAUSE)
        related = [(r["browseId"], r.get("title") or "") for r in (a.get("related") or {}).get("results") or []
                   if r.get("browseId")]
        for t in tracks:
            for x in t.get("artists") or []:
                if x.get("id"):
                    related.append((x["id"], x.get("name") or ""))
        return name, tracks, related

    @staticmethod
    def _is_throttle(e: Exception) -> bool:
        """Bağlantı / limit xətası (şəbəkə) — paralel sayını azaltmağa dəyər; 'tapılmadı' kimi ifaçıya aid xətalar yox."""
        text = str(e).lower()
        return isinstance(e, (ConnectionError, TimeoutError, OSError)) or any(
            s in text for s in ("429", "too many", "connection", "timed out", "timeout", "reset", "aborted", "rate"))

    async def step(self, add_items, user_artists_fn, stop) -> bool:
        row = await self._claim()
        if not row:
            if self._seed_lock is None:
                self._seed_lock = asyncio.Lock()
            async with self._seed_lock:                 # paralel yuvalardan yalnız biri seed edir / köhnələri qaytarır
                row = await self._claim()                     # gözləyərkən başqa yuva artıq doldurmuş ola bilər
                if not row and not self._inflight:      # başqa yuva ifaçı oxuyursa — onun 'əlaqəli ifaçıları' gələ bilər
                    n = self._requeue_old()
                    if not n:
                        self.stats["note"] = "seed: chartlar və janrlar oxunur..."
                        pairs = await asyncio.to_thread(self._seed_sync, user_artists_fn(), stop)
                        seed_prio = COUNTRY_PRIO_TOP if self.countries else 0
                        n = await asyncio.to_thread(self.add_artists, pairs, seed_prio)
                        logger.info(f"🎵 YT Music seed: {len(pairs)} ifaçı ({n} yeni, prio={seed_prio})")
                    self.stats["note"] = ""
                    row = await self._claim()
            if not row and self._inflight:
                await asyncio.sleep(2)                  # başqa yuva hələ oxuyur — yeni ifaçılar düşə bilər, saatlıq gözləmə yox
                return True
            if not row:
                row = await self._claim()               # son cəhd: o arada başqa yuva bitib «əlaqəli ifaçıları» əlavə etmiş ola bilər
            if not row:
                self.stats["note"] = "yeni ifaçı yoxdur — 1 saat sonra yenidən"
                return False
        aid, name, prio = row[0], row[1], int(row[2] or 0)
        try:
            try:
                name, tracks, related = await asyncio.to_thread(self._artist_sync, aid, stop)
            except Exception as e:
                logger.info(f"🎵 YTM ifaçı {name or aid}: {e}")
                self._mark(aid, 2)
                if self._is_throttle(e):
                    self.stats["err"] += 1
                    self.stats["err_at"] = time.time()
                return True
            items, vids = [], set()
            for t in tracks:
                it = ytm_item(t, t.get("_fa"), t.get("_ft"))
                if it and it["vid"] not in vids:
                    vids.add(it["vid"])
                    items.append(it)
            new_artists = await asyncio.to_thread(self.add_artists, related, max(0, prio - 1))
            added, in_depo = await add_items(items, front=prio > 0)
            self.stats["found"] += added
            self.stats["in_depo"] += in_depo
            self._mark(aid, 1, len(items))
            logger.info(f"🎵 YTM {name}: {len(items)} mahnı (+{added} növbəyə, {in_depo} artıq depoda), "
                        f"+{new_artists} yeni ifaçı")
            return True
        finally:
            self._release(aid)
