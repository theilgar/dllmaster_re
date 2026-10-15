"""
🎬 Vahid Media və FFMPEG Emal Mühərriki — common/media.py

Həm 🤖 Bot (audio redaktoru, səs asistanı), həm də 📱 Userbot (stikerlər, TTS, media) üçün:
  - run_ffmpeg(): alt-proses kimi ffmpeg əmrlərinin təhlükəsiz icrası.
  - duration_of(): ffprobe ilə media müddətinin dəqiq hesablanması.
  - media_dimensions(): video/şəkil ölçülərinin (en x hündürlük) öyrənilməsi.
  - prepare_cover(): audio üçün JPEG qapaq (max 800px) və Telegram thumbnail (max 320px).
  - write_audio_tags(): audio fayla title, artist və qapaq şəklinin (ID3) daxil edilməsi.
  - to_static_sticker(): şəkil / gif-in 512x512 WebP stikerinə çevrilməsi.
  - to_video_sticker(): videonun 512x512 WebM (VP9, ≤256 KB, ≤3 san.) video stikerinə çevrilməsi.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

SIDE = 512
VIDEO_MAX_SEC = 3
VIDEO_MAX_BYTES = 256 * 1024
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma"}
EMBED_COVER_EXTS = {".mp3", ".m4a", ".flac"}
MIME_EXT = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".m4a", "audio/m4a": ".m4a",
    "audio/flac": ".flac", "audio/x-flac": ".flac",
    "audio/ogg": ".ogg", "audio/opus": ".ogg",
    "audio/wav": ".wav", "audio/x-wav": ".wav",
}


async def run_ffmpeg(*args: str, timeout: int = 120) -> Tuple[int, str]:
    """FFmpeg əmrini təhlükəsiz şəkildə başladır (cpu_pool nice prioritetini nəzərə alaraq)."""
    prefix = []
    try:
        from bot.core import cpu_pool
        prefix = cpu_pool.ffmpeg_prefix()
    except Exception as _e:
        logger.debug("udulan xəta [common/media.py:43]: %r", _e)
    proc = await asyncio.create_subprocess_exec(
        *prefix, "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, err.decode(errors="ignore").strip()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "ffmpeg icra vaxtı bitdi (timeout)"


async def duration_of(path: str) -> float:
    """ffprobe ilə media faylının dəqiq müddətini qaytarır."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        return float(out.decode().strip() or 0)
    except asyncio.TimeoutError:
        if proc:
            with contextlib.suppress(Exception):
                proc.kill()
    except Exception as _e:
        logger.debug("udulan xəta [common/media.py:73]: %r", _e)
    return 0.0


async def duration_sec(path: str) -> int:
    """ffprobe ilə media faylının tam saniyə sayını (int) qaytarır."""
    return int(round(await duration_of(path)))


async def media_dimensions(path: str) -> Tuple[int, int]:
    """ffprobe ilə video/şəkil ölçülərini (width, height) qaytarır."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", path, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        w, h = out.decode().strip().split("x")[:2]
        return int(w), int(h)
    except asyncio.TimeoutError:
        if proc:
            with contextlib.suppress(Exception):
                proc.kill()
    except Exception as _e:
        logger.debug("udulan xəta [common/media.py:98]: %r", _e)
    return SIDE, SIDE


def scale_filter(size: int) -> str:
    return f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease"


async def prepare_cover(src_img: str, base_out: str) -> Tuple[str, str]:
    """Audio üçün JPEG qapaq (max 800px) və Telegram thumbnail (max 320px, ≤200 KB) hazırlayır."""
    cover = f"{base_out}_cover.jpg"
    thumb = f"{base_out}_thumb.jpg"
    for out, size, q in ((cover, 800, "3"), (thumb, 320, "5")):
        rc, err = await run_ffmpeg("-i", src_img, "-vf", scale_filter(size), "-frames:v", "1", "-q:v", q, out)
        if rc != 0 or not os.path.exists(out):
            raise RuntimeError(f"Şəkil emal olunmadı: {err[-200:]}")
    if os.path.getsize(thumb) > 200 * 1024:
        await run_ffmpeg("-i", src_img, "-vf", scale_filter(320), "-frames:v", "1", "-q:v", "12", thumb)
    return cover, thumb


async def write_audio_tags(src: str, dst: str, ext: str, title: str, artist: str,
                           cover_path: Optional[str] = None) -> None:
    """Audionu yenidən qablaşdırır: title/artist teqləri və istəyə görə yeni qapaq şəkli."""
    embed = bool(cover_path) and ext in EMBED_COVER_EXTS
    args = ["-i", src]
    if embed:
        args += ["-i", cover_path, "-map", "0:a", "-map", "1:v"]
    else:
        args += ["-map", "0:a", "-map", "0:v?"]
    args += ["-c", "copy"]
    if embed:
        args += ["-disposition:v:0", "attached_pic"]
        if ext == ".mp3":
            args += ["-id3v2_version", "3",
                     "-metadata:s:v", "title=Album cover",
                     "-metadata:s:v", "comment=Cover (front)"]
    args += ["-metadata", f"title={title}", "-metadata", f"artist={artist}", dst]

    rc, err = await run_ffmpeg(*args)
    if rc != 0 or not os.path.exists(dst):
        raise RuntimeError(f"ffmpeg audio teq xətası: {err[-300:]}")


def guess_audio_ext(file_name: str, mime: str = "") -> str:
    """Faylın adı / MIME tipindən audio uzantısı (tanınmasa .mp3)."""
    ext = os.path.splitext(file_name or "")[1].lower()
    if ext in AUDIO_EXTS:
        return ext
    return MIME_EXT.get((mime or "").lower(), ".mp3")


def _pil_sticker(src: str, dst: str, side: int) -> None:
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGBA")
        w, h = im.size
        k = side / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
        im.save(dst, "WEBP", quality=95, method=6)


async def to_static_sticker(src: str, dst: str, side: int = SIDE) -> None:
    """Şəkli Telegram standartlarına uyğun 512x512 WebP stikerinə çevirir (PIL thread-də — loop bloklanmır)."""
    try:
        await asyncio.to_thread(_pil_sticker, src, dst, side)
        return
    except Exception as _e:
        logger.debug("udulan xəta [common/media.py:166]: %r", _e)

    scale = f"scale='if(gt(iw,ih),{side},-2)':'if(gt(iw,ih),-2,{side})':flags=lanczos"
    rc, err = await run_ffmpeg("-i", src, "-vf", scale, "-frames:v", "1", "-c:v", "libwebp",
                               "-lossless", "0", "-q:v", "90", dst)
    if rc != 0 or not os.path.exists(dst):
        raise RuntimeError(f"Statik stiker çevrilmədi: {err or rc}")


async def to_video_sticker(src: str, dst: str, start: float = 0.0, speed: float = 1.0,
                           side: int = SIDE) -> int:
    """Videonun 512x512 WebM (VP9, ≤256 KB, ≤3 san.) formatında stikerə çevrilməsi."""
    scale = (f"setpts=PTS/{speed:.4f},"
             f"scale='if(gt(iw,ih),{side},-2)':'if(gt(iw,ih),-2,{side})':flags=lanczos,"
             f"fps=30,format=yuva420p")
    span = f"{VIDEO_MAX_SEC * speed:.3f}"
    for br in ("400k", "250k", "150k", "90k", "60k"):
        rc, err = await run_ffmpeg("-ss", f"{start:.3f}", "-t", span, "-i", src, "-an", "-vf", scale,
                                   "-t", "2.99",
                                   "-c:v", "libvpx-vp9", "-b:v", br, "-maxrate", br, "-bufsize", "300k",
                                   "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", dst, timeout=120)
        if rc != 0 or not os.path.exists(dst):
            raise RuntimeError(f"Video stiker çevrilmədi: {err or rc}")
        if os.path.getsize(dst) <= VIDEO_MAX_BYTES:
            return VIDEO_MAX_SEC
    raise RuntimeError("Video stiker 256 KB-a sığmadı — daha qısa video seçin.")


async def to_ogg(src: str, dst: str, bitrate: str = "48k", timeout: int = 60) -> bool:
    """Audio faylı Telegram səsli mesajına uyğun OGG/Opus formatına çevirir (nice prioritetli)."""
    rc, _ = await run_ffmpeg(
        "-i", src, "-c:a", "libopus", "-b:a", bitrate, "-ar", "48000", "-ac", "1", dst,
        timeout=timeout,
    )
    return rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0



