"""
🧠 yt-dlp işçi prosesi — bot/core/ytdl_worker.py

Botun əsas prosesi bir event loop + GIL-dir: yt-dlp-nin ağır Python işi (səhifə / JSON parse, format seçimi,
cookies deşifrəsi) thread-də işləsə də GIL-i tutur və bot "donur" (mesajlara gec cavab, düymələr ilişir).
Bu modul ayrıca Python prosesi kimi işləyir (`python -m bot.core.ytdl_worker`) — hər proses öz nüvəsində,
öz GIL-i ilə. bot/core/cpu_pool.py bu proseslərdən hovuz qurur.

Protokol (stdin / stdout, 4 bayt uzunluq + pickle):
  → (task_id, func_name, args)
  ← (task_id, "ok", result) | (task_id, "cancel", mesaj) | (task_id, "err", mesaj)
stdout yalnız protokol üçündür — yt-dlp-nin bütün çapı stderr-ə (bot konsoluna) gedir.

Bu fayldakı funksiyalar "🧵 Thread" rejimində əsas prosesdə də birbaşa çağırılır.
"""
import os
import pickle
import signal
import struct
import sys

_BIG_KEYS = ("formats", "thumbnails", "automatic_captions", "subtitles", "heatmap", "requested_formats",
             "fragments", "http_headers", "_format_sort_fields", "chapters")


def _slim(info):
    """Prosesdən qaytarılan info — böyük siyahılar atılır (pickle / RAM)."""
    if not isinstance(info, dict):
        return info
    out = {k: v for k, v in info.items() if k not in _BIG_KEYS}
    if isinstance(out.get("entries"), list):
        out["entries"] = [_slim(e) for e in out["entries"] if e]
    if isinstance(out.get("requested_downloads"), list):
        out["requested_downloads"] = [{"filepath": d.get("filepath")} for d in out["requested_downloads"]
                                      if isinstance(d, dict)]
    return out


# ───────────────────────── tapşırıqlar ─────────────────────────
class _QuietLogger:
    """yt-dlp çıxışı terminala yazılmasın ([download] 90%, [Cookies] Loading..., WARNING/ERROR sətirləri).
    Xətalar onsuz da istisna kimi əsas prosesə qayıdır və orada loglanır. DLM_YTDLP_VERBOSE=1 — köhnə davranış."""
    def debug(self, msg):
        pass

    info = debug

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _quiet(opts: dict) -> dict:
    opts = dict(opts)
    opts.setdefault("color", {"stdout": "no_color", "stderr": "no_color"})   # xəta mətnində ANSI kodları olmasın
    if os.getenv("DLM_YTDLP_VERBOSE") == "1" or opts.get("verbose"):
        return opts
    opts.setdefault("logger", _QuietLogger())
    opts["noprogress"] = True                 # [download] xx% sətirləri və cookies progress-bar
    opts.setdefault("quiet", True)
    return opts


FILE_TOO_LARGE = "Fayl ölçü limitindən böyükdür"


def ping():
    return os.getpid()


def ytdl_extract(url: str, opts: dict):
    """extract_info(download=False) — axtarış, playlist, Mix."""
    from yt_dlp import YoutubeDL
    with YoutubeDL(_quiet(opts)) as ydl:
        info = ydl.extract_info(url, download=False)
        return _slim(ydl.sanitize_info(info))


def ytdl_download(url: str, opts: dict, cancel_path: str = None):
    """Yükləmə. cancel_path faylı yarananda (⏹ Dayandır) növbəti hissədə kəsilir."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadCancelled

    prog_path = (cancel_path + ".prog") if cancel_path else None
    last = [0.0]

    def hook(d):
        if cancel_path and os.path.exists(cancel_path):
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
        if prog_path and isinstance(d, dict) and d.get("status") == "finished":
            try:                                          # ✅ yekun: orta sürət (yükləmə müddətinə görə)
                import json
                done = d.get("downloaded_bytes") or d.get("total_bytes") or 0
                el = d.get("elapsed") or 0
                tmp = prog_path + ".t"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"finished": True, "done": done, "total": done, "elapsed": el,
                               "avg": (done / el) if el else 0}, f)
                os.replace(tmp, prog_path)
            except OSError:
                pass
        if prog_path and isinstance(d, dict) and d.get("status") == "downloading":
            import time
            now = time.monotonic()
            if now - last[0] >= 0.25:                     # 📶 canlı irəliləyiş: əsas proses bu faylı oxuyur
                last[0] = now
                try:
                    import json
                    tmp = prog_path + ".t"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"done": d.get("downloaded_bytes") or 0,
                                   "total": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                                   "speed": d.get("speed") or 0, "eta": d.get("eta")}, f)
                    os.replace(tmp, prog_path)
                except OSError:
                    pass

    opts = dict(opts)
    audio_only = opts.pop("_dlm_audio_extract", False)       # 🌐 server: video faylından səsi itkisiz çıxar (az trafik)
    opts = _quiet(opts)
    opts["progress_hooks"] = [hook]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        path = downloads[0]["filepath"] if downloads else ydl.prepare_filename(info)
        if opts.get("max_filesize") and not os.path.exists(path):
            # yt-dlp max_filesize aşılanda xəta atmır, sadəcə yükləmir ("File is larger than max-filesize")
            raise RuntimeError(f"{FILE_TOO_LARGE} ({int(opts['max_filesize']) // 1048576} MB)")
        if audio_only:
            path = extract_aac(path, (downloads[0] if downloads else info).get("acodec"),
                               (downloads[0] if downloads else info).get("vcodec"))
        return {"path": path, "info": _slim(ydl.sanitize_info(info))}


def extract_aac(path: str, acodec, vcodec) -> str:
    """Video konteynerindəki (mp4) AAC səsi YENİDƏN KODLAMADAN .m4a-ya çıxarır → fayl ~4 dəfə kiçik, ev ffmpeg işlətmir.
    Səs AAC deyilsə, video yoxdursa və ya ffmpeg alınmazsa — fayl olduğu kimi qalır."""
    import shutil
    import subprocess
    if not path.lower().endswith((".mp4", ".mkv", ".mov")) or not str(acodec or "").startswith("mp4a") \
            or not vcodec or vcodec == "none" or not shutil.which("ffmpeg"):
        return path
    out = os.path.splitext(path)[0] + ".m4a"
    try:
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn", "-c:a", "copy", out],
                           capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return path
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        if os.path.exists(out):
            os.remove(out)
        return path
    os.remove(path)
    return out


_ytm = None


_ytm_proxy = None


def ytm_search(query: str, limit: int, proxy: str = None):
    """ytmusicapi axtarışı (obyekt hər prosesdə bir dəfə yaradılır; proxy dəyişəndə yenidən)."""
    global _ytm, _ytm_proxy
    if _ytm is None or proxy != _ytm_proxy:
        from ytmusicapi import YTMusic
        kw = {}
        if proxy:
            # requests SOCKS üçün PySocks istəyir; socks5h → requests-in başa düşdüyü ad
            kw["proxies"] = {"http": proxy, "https": proxy}
        try:
            _ytm = YTMusic(**kw)
        except TypeError:
            _ytm = YTMusic()
        _ytm_proxy = proxy
    return _ytm.search(query, filter="songs", limit=limit)


def upload_audio(token: str, chat_id: int, thread_id, path: str, filename: str, title: str, performer: str,
                 duration: int, caption: str, thumb: bytes = None):
    """🌐 Uzaq server: faylı birbaşa Telegram-a yükləyir (ev serveri fayla toxunmur).
    → göndərilən mesaj (dict, aiogram Message.model_dump). Telegram xətası olduğu kimi atılır (flood mətni qalır)."""
    import asyncio
    from aiogram import Bot
    from aiogram.types import BufferedInputFile, FSInputFile

    async def go():
        bot = Bot(token=token)
        try:
            kw = {"chat_id": chat_id, "audio": FSInputFile(path, filename=filename),
                  "title": (title or "")[:64] or None, "performer": (performer or "")[:64] or None,
                  "duration": int(duration or 0) or None, "caption": caption, "parse_mode": "HTML",
                  "disable_notification": True,
                  "thumbnail": BufferedInputFile(thumb, filename="cover.jpg") if thumb else None}
            if thread_id:
                kw["message_thread_id"] = thread_id
            msg = await bot.send_audio(**kw)
            return msg.model_dump(mode="json", exclude_none=True)
        finally:
            await bot.session.close()

    if not os.path.exists(path):
        raise FileNotFoundError(f"uzaq fayl tapılmadı: {path}")
    return asyncio.run(go())


def file_stat(path: str):
    return os.path.getsize(path) if os.path.exists(path) else -1


def cleanup_old(max_age: int = 6 * 3600):
    """Uzaq iş qovluğunda (dl/) qalmış köhnə fayllar (ev botu çöksə / dublikat atılsa) silinir."""
    import time
    n, now = 0, time.time()
    for d in ("dl",):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            f = os.path.join(d, name)
            try:
                if os.path.isfile(f) and now - os.path.getmtime(f) > max_age:
                    os.remove(f)
                    n += 1
            except OSError:
                pass
    return n


def _firefox_login():
    """Serverin Firefox profilində YouTube / Google girişi varmı? Yalnız cookie ADLARI yoxlanılır (dəyərlər oxunmur).
    Firefox işləyərkən yeni yazılar -wal faylındadır — üç fayl birlikdə kopyalanır. → profil adı və ya None."""
    import glob
    import shutil
    import sqlite3
    import tempfile
    want = ("SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO")
    for db in glob.glob(os.path.expanduser("~/.mozilla/firefox/*/cookies.sqlite")):
        tmp = tempfile.mkdtemp()
        try:
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    shutil.copy(db + suffix, os.path.join(tmp, "cookies.sqlite" + suffix))
            con = sqlite3.connect(os.path.join(tmp, "cookies.sqlite"))
            found = {r[0] for r in con.execute(
                "select name from moz_cookies where (host like '%google%' or host like '%youtube%') and name in (?,?,?,?)", want)}
            con.close()
        except (OSError, sqlite3.Error):
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if len(found) >= 2:
            return os.path.basename(os.path.dirname(db))
    return None


def _pot_version():
    """bgutil PO token provayderi (127.0.0.1:4416) işləyirmi → versiya və ya None."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:4416/ping", timeout=2) as r:
            return json.load(r).get("version")
    except (OSError, ValueError):
        return None


def diag():
    """Quraşdırma yoxlaması: versiyalar və lazımi alətlər."""
    import shutil
    path = os.path.join(os.getcwd(), "bin") + os.pathsep + os.environ.get("PATH", "")
    info = {"python": sys.version.split()[0], "ffmpeg": bool(shutil.which("ffmpeg")),
            "js": [n for n in ("deno", "node", "bun") if shutil.which(n, path=path)],
            "deno": bool(shutil.which("deno", path=path)),        # yt-dlp YouTube yoxlaması üçün Deno (Node 20 dəstəklənmir)
            "pot": _pot_version(), "firefox_login": _firefox_login()}
    for mod in ("yt_dlp", "aiogram"):
        try:
            m = __import__(mod)
            info[mod] = getattr(getattr(m, "version", m), "__version__", None) or getattr(m, "__version__", "ok")
        except ImportError:
            info[mod] = None
    st = os.statvfs(".")
    info["free_mb"] = st.f_bavail * st.f_frsize // 1048576
    try:
        info["ram_mb"] = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 1048576
    except (ValueError, OSError):
        info["ram_mb"] = None
    return info


TASKS = {"ping": ping, "ytdl_extract": ytdl_extract, "ytdl_download": ytdl_download, "ytm_search": ytm_search,
         "upload_audio": upload_audio, "file_stat": file_stat, "cleanup_old": cleanup_old, "diag": diag}


# ───────────────────────── proses döngüsü ─────────────────────────
def _read(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _die_with_parent():
    """Linux: bot (ana proses) ölərsə / restart olarsa işçi də dərhal dayansın — yetim yt-dlp yükləməsi
    davam edib sonra boş borunu (pipe) yazmağa çalışmasın."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)                 # PR_SET_PDEATHSIG
    except Exception:
        pass


def main():
    _die_with_parent()
    nice = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else 0
    if nice > 0:
        try:
            os.nice(nice)                    # botun əsas prosesi həmişə üstün olsun
        except OSError:
            pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # Ctrl+C-ni əsas bot idarə edir
    proto_in = sys.stdin.buffer
    proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # yt-dlp print()-ləri protokolu pozmasın
    sys.stdout = sys.stderr

    def send(obj):
        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            proto_out.write(struct.pack(">I", len(data)) + data)
        except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
            # 🔌 bot bu işçini artıq bağlayıb (⏹ dayandırma, boş proses yığımı, ♻️ yeniləmə, restart) —
            # nəticəni qəbul edən yoxdur: səssiz çıx, terminala traceback tökmə
            os._exit(0)

    if os.getenv("DLM_REMOTE_WORKER") == "1":         # 🌐 uzaq server: açılışda köhnə qalıqları təmizlə
        os.makedirs("dl", exist_ok=True)
        try:
            cleanup_old()
        except Exception:
            pass
    send((0, "ok", "ready"))
    while True:
        if os.getppid() == 1 and os.getenv("DLM_REMOTE_WORKER") != "1":                          # ana proses yoxdur (yetim qalmışıq)
            os._exit(0)
        try:
            size = struct.unpack(">I", _read(proto_in, 4))[0]
            task_id, name, args = pickle.loads(_read(proto_in, size))
        except (EOFError, OSError, ValueError):
            return
        try:
            result = TASKS[name](*args)
            try:
                send((task_id, "ok", result))
            except Exception as e:           # nəticə pickle olunmadı
                send((task_id, "err", f"nəticə ötürülmədi: {type(e).__name__}: {e}"))
        except Exception as e:
            cancelled = type(e).__name__ == "DownloadCancelled"
            send((task_id, "cancel" if cancelled else "err", str(e) or type(e).__name__))


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        os._exit(0)
