"""
🔍 bot/links/parsers.py - Spotify, VK, YouTube oEmbed və qısa link parsers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from html import unescape

import aiohttp

from bot.core.links import parse_url
from common.i18n import _ as tr
from common.utilities import format_duration

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def pick_image(images, target=300):
    """Spotify şəkilləri (640/300/64) → thumbnail üçün ~300px."""
    if not images:
        return None
    imgs = sorted(images, key=lambda i: i.get("width") or 0)
    fit = [i for i in imgs if (i.get("width") or 0) <= 320]
    chosen = (fit[-1] if fit else imgs[0])
    return (chosen.get("url") or "").split("?")[0] or None


def sp_item(name: str, artists: list, duration_ms: int, image_url=None, sp_id=None) -> dict:
    artist = ", ".join([a for a in artists if a][:3]) or "Naməlum"
    first = artists[0] if artists else ""
    return {
        "title": f"{artist} - {name}",
        "query": f"{first} {name}".strip(),
        "url": None,                              # music_plugin YouTube-da tapacaq
        "raw_duration": int((duration_ms or 0) // 1000),
        "meta": {"artist": artist, "track": name},
        "thumb": image_url,
        "sp_id": sp_id,                           # song.link ilə dəqiq YouTube uyğunluğu üçün
    }


_VERSION_RE = re.compile(
    r"\s*[-–(\[]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|deluxe|bonus track|single version|album version|"
    r"radio edit|explicit|clean)[^)\]]*[)\]]?", re.I)


def track_key(name: str) -> str:
    """"Song - Remastered 2011" / "Song (Deluxe)" → "song" — təkrarları tutmaq üçün."""
    base = _VERSION_RE.sub("", name or "")
    return re.sub(r"[^\w]+", " ", base.lower()).strip()


def total_duration(items) -> str:
    return format_duration(sum(int(i.get("raw_duration") or 0) for i in items))


async def http_get(url: str, as_text=True, allow_redirects=True):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        async with session.get(url, allow_redirects=allow_redirects) as r:
            body = await (r.text() if as_text else r.read())
            return r.status, str(r.url), body


async def _public_host(url: str) -> bool:
    """URL-in hostu ictimai IP-yə açılırmı (localhost / daxili şəbəkə — yox). SSRF qarşısı."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for *_, sa in infos:
        ip = ipaddress.ip_address(sa[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def _get_public(url: str, hops: int = 5):
    """Yönləndirmələri əl ilə izləyir — hər addımda host yoxlanılır (qısa link daxili ünvana apara bilməsin)."""
    from urllib.parse import urljoin
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        for _ in range(hops):
            if not await _public_host(url):
                raise ValueError(f"icazəsiz ünvan: {url[:80]}")
            async with session.get(url, allow_redirects=False) as r:
                loc = r.headers.get("Location")
                if r.status in (301, 302, 303, 307, 308) and loc:
                    url = urljoin(url, loc)
                    continue
                return r.status, str(r.url), await r.text()
    raise ValueError("çox yönləndirmə")


async def resolve_short(url: str):
    """spotify.link/... və ya vk.cc/... → tam link."""
    try:
        status, final, body = await _get_public(url)
    except Exception as e:
        logger.warning(f"Qısa link açılmadı: {e}")
        return None
    parsed = parse_url(final)
    if parsed and parsed["kind"] not in ("sp_short", "vk_short"):
        return parsed
    m = re.search(r"https://open\.spotify\.com/[a-z\-/]*(?:track|album|playlist|artist)/[A-Za-z0-9]+", body or "")
    if m:
        return parse_url(m.group(0))
    m_vk = re.search(r"https?://(?:[a-z0-9-]+\.)?vk\.(?:com|ru)/(?:audio|music|video|clip|wall|artist)[^\s\"'<>]+", body or "")
    if m_vk:
        return parse_url(m_vk.group(0))
    return None


async def youtube_oembed(video_id: str) -> dict:
    """Videonun adı (yt-dlp-siz, sürətli)."""
    try:
        status, _, body = await http_get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json")
        if status == 200:
            data = json.loads(body)
            return {"title": data.get("title"), "author": data.get("author_name")}
    except Exception as e:
        logger.debug(f"oEmbed alınmadı: {e}")
    return {}


def vk_item(name: str, artist: str, duration_sec: int, image_url=None, direct_url=None) -> dict:
    artist = (artist or "Naməlum").strip()
    name = (name or "Naməlum").strip()
    return {
        "title": f"{artist} - {name}" if artist and artist != "Naməlum" else name,
        "query": f"{artist} {name}".strip(),
        "url": direct_url,
        "raw_duration": int(duration_sec or 0),
        "meta": {"artist": artist, "track": name},
        "thumb": image_url,
    }


async def vk_al_request(data: dict) -> dict:
    """VK daxili al_audio.php sorğusu."""
    headers = {
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://vk.com/",
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.post("https://vk.com/al_audio.php", data=data) as r:
            text = await r.text()
            return json.loads(text)


async def vk_track_data(owner_id: str, audio_id: str, access_hash: str = None) -> dict:
    owner_id = str(owner_id).strip()
    audio_id = str(audio_id).strip()
    access_hash = str(access_hash).strip() if access_hash else None

    # 1. reload_audios ilə sorğu (həm istifadəçi, həm qrup, həm rəsmi kataloq trekləri üçün ən etibarlı metod)
    candidate_ids = []
    if access_hash:
        candidate_ids.append(f"{owner_id}_{audio_id}_{access_hash}")
    candidate_ids.append(f"{owner_id}_{audio_id}")

    for cid in candidate_ids:
        try:
            res = await vk_al_request({"act": "reload_audios", "al": "1", "audio_ids": cid})
            p = res.get("payload", [])
            if len(p) > 1 and isinstance(p[1], list) and p[1] and isinstance(p[1][0], list) and p[1][0]:
                raw_track = p[1][0][0]
                if isinstance(raw_track, list) and len(raw_track) > 5:
                    title = unescape(str(raw_track[3] or ""))
                    artist = unescape(str(raw_track[4] or ""))
                    duration = int(raw_track[5] or 0)
                    thumb = None
                    if len(raw_track) > 14 and raw_track[14]:
                        thumb = str(raw_track[14]).split(",")[0]
                    return vk_item(title, artist, duration, thumb)
        except Exception as e:
            logger.debug(f"VK reload_audios ({cid}) xətası: {e}")

    # 2. act: "track" ehtiyat metodu (rəsmi kataloq trekləri üçün)
    try:
        data = {"act": "track", "al": "1", "owner_id": owner_id, "audio_id": audio_id}
        if access_hash:
            data["hash"] = access_hash
        res = await vk_al_request(data)
        p = res.get("payload", [])
        p1 = p[1] if len(p) > 1 else []
        html_body = p1[1] if isinstance(p1, list) and len(p1) > 1 and isinstance(p1[1], str) else ""
        m = re.search(r'data-audio=\"([^\"]+)\"', html_body)
        if m:
            t = json.loads(unescape(m.group(1)))
            title = unescape(str(t[3] or ""))
            artist = unescape(str(t[4] or ""))
            duration = int(t[5] or 0)
            thumb = str(t[14]).split(",")[0] if len(t) > 14 and t[14] else None
            return vk_item(title, artist, duration, thumb)
    except Exception as e:
        logger.debug(f"VK act:track xətası: {e}")

    # 3. act: "reload_audio" ehtiyat metodu
    if access_hash:
        try:
            res = await vk_al_request({"act": "reload_audio", "al": "1", "ids": f"{owner_id}_{audio_id}_{access_hash}"})
            p = res.get("payload", [])
            if len(p) > 1 and isinstance(p[1], list) and p[1] and isinstance(p[1][0], list):
                raw_track = p[1][0]
                if isinstance(raw_track, list) and len(raw_track) > 5:
                    title = unescape(str(raw_track[3] or ""))
                    artist = unescape(str(raw_track[4] or ""))
                    duration = int(raw_track[5] or 0)
                    thumb = str(raw_track[14]).split(",")[0] if len(raw_track) > 14 and raw_track[14] else None
                    return vk_item(title, artist, duration, thumb)
        except Exception as e:
            logger.debug(f"VK reload_audio xətası: {e}")

    raise RuntimeError(tr("VK mahnısı tapılmadı"))


async def vk_playlist_data(owner_id: str, playlist_id: str, access_hash: str = None) -> dict:
    data = {
        "act": "load_section",
        "al": "1",
        "owner_id": str(owner_id),
        "playlist_id": str(playlist_id),
        "type": "playlist",
    }
    if access_hash:
        data["access_hash"] = str(access_hash)
    res = await vk_al_request(data)
    p = res.get("payload", [])[1]
    p0 = p[0] if isinstance(p, list) and len(p) > 0 and isinstance(p[0], dict) else {}
    html_body = p[1] if isinstance(p, list) and len(p) > 1 and isinstance(p[1], str) else ""

    title = unescape(p0.get("title") or "")
    author = unescape(p0.get("authorName") or "")
    cover = p0.get("coverUrl") or ""

    items = []
    raw_list = p0.get("list") or []
    if not raw_list and html_body:
        for m in re.finditer(r'data-audio=\"([^\"]+)\"', html_body):
            try:
                raw_list.append(json.loads(unescape(m.group(1))))
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/links_plugin.py:248]: %r", _e)

    for t in raw_list:
        if not isinstance(t, list) or len(t) < 6:
            continue
        t_title = unescape(str(t[3]))
        t_artist = unescape(str(t[4]))
        t_dur = int(t[5] or 0)
        t_cover = t[14].split(",")[0] if len(t) > 14 and t[14] else cover
        items.append(vk_item(t_title, t_artist, t_dur, t_cover))

    name = f"{author} — {title}" if author and title else (title or author or "VK Playlist")
    return {"name": name, "items": items}


async def vk_artist_data(slug: str) -> dict:
    data = {"act": "section", "al": "1", "artist_id": slug, "section": "artist"}
    res = await vk_al_request(data)
    p = res.get("payload", [])[1]
    html_str = p[0] if isinstance(p, list) and len(p) > 0 and isinstance(p[0], str) else ""
    meta_dict = p[1] if isinstance(p, list) and len(p) > 1 and isinstance(p[1], dict) else {}

    name = unescape(meta_dict.get("title") or slug)
    items = []
    seen = set()
    for m in re.finditer(r'data-audio=\"([^\"]+)\"', html_str):
        try:
            t = json.loads(unescape(m.group(1)))
            t_title = unescape(str(t[3]))
            t_artist = unescape(str(t[4]))
            t_dur = int(t[5] or 0)
            t_cover = t[14].split(",")[0] if len(t) > 14 and t[14] else None
            k = (t_title.lower(), t_artist.lower())
            if k in seen:
                continue
            seen.add(k)
            items.append(vk_item(t_title, t_artist, t_dur, t_cover))
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/links_plugin.py:286]: %r", _e)
    return {"name": name, "items": items}


async def vk_video_data(url: str) -> dict:
    from bot.core.youtube_handler import YoutubeManager
    from bot.core import cpu_pool
    try:
        info = await cpu_pool.fast_extract(url, YoutubeManager.fast_opts())
        if info:
            title = info.get("title") or "VK Video"
            artist = info.get("uploader") or "VK"
            dur = int(info.get("duration") or 0)
            thumb = (info.get("thumbnails") or [{}])[-1].get("url") if info.get("thumbnails") else info.get("thumbnail")
            return vk_item(title, artist, dur, thumb, direct_url=url)
    except Exception as e:
        logger.info(f"VK video məlumatı alınmadı ({url}): {e}")
    return vk_item("VK Video", "VK", 0, None, direct_url=url)


async def vk_wall_data(url: str) -> dict:
    from bot.core import cpu_pool
    opts = {'extract_flat': True, 'quiet': True, 'no_warnings': True, 'socket_timeout': 15, 'skip_download': True}
    info = await cpu_pool.ytdl_extract(url, opts)
    entries = info.get("entries") or []
    items = []
    for e in entries:
        title = e.get("title") or e.get("track") or "Audio"
        artist = e.get("artist") or e.get("uploader") or ""
        dur = int(e.get("duration") or 0)
        thumb = (e.get("thumbnails") or [{}])[-1].get("url") if e.get("thumbnails") else e.get("thumbnail")
        e_url = e.get("url")
        is_video = bool(e_url and ("/video" in e_url or "/clip" in e_url))
        items.append(vk_item(title, artist, dur, thumb, direct_url=e_url if is_video else None))
    title = info.get("title") or "VK Wall Post"
    return {"title": title, "items": items}


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


async def spotify_embed(kind: str, sid: str) -> dict:
    """
    API-siz ehtiyat yol: open.spotify.com/embed səhifəsindən siyahı.
    Spotify Web API Spotify-a məxsus siyahıları (Radio, Daily Mix, editorial) yeni tətbiqlərə vermir —
    embed səhifəsi isə verir (adətən ilk ~100 mahnı).
    """
    status, _, body = await http_get(f"https://open.spotify.com/embed/{kind}/{sid}")
    if status != 200:
        raise RuntimeError(f"Spotify səhifəsi açılmadı ({status})")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    if not m:
        raise RuntimeError("Spotify səhifəsində məlumat tapılmadı")
    data = json.loads(m.group(1))
    entity = _find_key(data, "entity") or {}
    name = entity.get("name") or entity.get("title") or "Spotify"
    cover = None
    sources = _find_key(entity.get("coverArt") or entity.get("visualIdentity") or {}, "sources") or []
    if sources:
        cover = pick_image([{"url": s.get("url"), "width": s.get("width") or s.get("maxWidth")} for s in sources])

    items = []
    if kind == "track":
        artists = [a.get("name") for a in (entity.get("artists") or [])] or \
                  [s.strip() for s in (entity.get("subtitle") or "").split(",")]
        items.append(sp_item(entity.get("title") or name, artists, entity.get("duration") or 0, cover))
    else:
        for t in entity.get("trackList") or []:
            artists = [s.strip() for s in (t.get("subtitle") or "").replace("\u00a0", " ").split(",")]
            uri = t.get("uri") or ""
            items.append(sp_item(t.get("title") or "", artists, t.get("duration") or 0, cover,
                                 uri.split(":")[-1] if uri.startswith("spotify:track:") else None))
    return {"name": name, "items": [i for i in items if i["meta"]["track"]]}


