import re
import os
import time
import logging
import importlib
import asyncio
import contextlib
import threading
from pathlib import Path
from aiogram import BaseMiddleware
from aiogram.types import Message
from typing import Callable, Dict, Any


# 🔴 Proses dayanır — arxa plan thread-lərindəki uzun işlər (ölkə chartları, skan ...) bunu yoxlayıb dayanmalıdır.
# Əks halda asyncio.run() onları gözləyir və dayandırma watchdog-a (20 san.) qədər uzanır.
SHUTDOWN = threading.Event()

logger = logging.getLogger(__name__)

# common/utilities.py -> layihənin kökü (bot/, userbot/, download/ burada yerləşir)
BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = "download"

# Fayl adında qadağan olunmuş simvollar + nəzarət simvolları + '%'
# ('%' yt-dlp-nin outtmpl şablonunu poza bilir)
_INVALID_CHARS = re.compile(r'[\\/*?:"<>|%\x00-\x1f]')


try:
    import orjson

    def fast_json_dumps(data, ensure_ascii: bool = False) -> str:
        s = orjson.dumps(data).decode("utf-8")
        return s

    def fast_json_loads(data):
        return orjson.loads(data)
except ImportError:
    import json

    def fast_json_dumps(data, ensure_ascii: bool = False) -> str:
        return json.dumps(data, ensure_ascii=ensure_ascii)

    def fast_json_loads(data):
        return json.loads(data)


def script_json(data) -> str:
    """JSON-u <script> içinə təhlükəsiz qoymaq üçün: "</script>", "<!--" və & ilə çıxış mümkün olmasın."""
    return (fast_json_dumps(data, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def js_arg(value) -> str:
    """HTML atributundakı JS çağırışı üçün arqument: onclick="f({js_arg(ad)})" — ad nə olursa olsun."""
    import html
    return html.escape(script_json(str(value)), quote=True)

def sanitize_filename(filename: str, max_bytes: int = 120) -> str:
    name = _INVALID_CHARS.sub("", filename or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    # Fayl sisteminin limiti simvol yox, bayta görədir (kirill/ərəb/emoji uzun olur)
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.strip(" .") or "track"


# ───────────────────────── Saat qurşağı və vaxt ─────────────────────────


# ───────────────────────── Ölçü, zolaq və mətn formatlama ─────────────────────────


_BG_TASKS: set = set()


def spawn(coro):
    """Arxa plan tapşırığı başlat və güclü istinadla saxla: istinadsız create_task-ı Python zibil
    yığımında yarıda silə bilər. Tapşırıq bitəndə siyahıdan çıxır; xətası loqa yazılır (əvvəl itirdi)."""
    task = asyncio.ensure_future(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_bg_done)
    return task


def _bg_done(task):
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"Arxa plan tapşırığı xəta ilə bitdi ({task.get_coro().__qualname__ if hasattr(task.get_coro(), '__qualname__') else task.get_name()}): {exc!r}")


async def _delayed_delete(target, delay: float):
    try:
        await asyncio.sleep(delay)
        await safe_delete(target)
    except Exception as e:
        logger.debug(f"_delayed_delete xətası: {e}")


async def safe_delete(target) -> bool:
    """Mesajı xətasız silir (artıq silinmiş və ya tapılmayan mesaj xətalarını udur).

    target ola bilər: Message, CallbackQuery, və ya (bot, chat_id, message_id) tuple.
    """
    if target is None:
        return False
    try:
        if hasattr(target, "delete"):
            await target.delete()
            return True
        elif hasattr(target, "message") and getattr(target, "message", None):
            await target.message.delete()
            return True
        elif isinstance(target, (tuple, list)) and len(target) == 3:
            bot, chat_id, message_id = target
            if bot and chat_id and message_id:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
                return True
    except Exception as e:
        err = str(e).lower()
        if "not found" not in err and "can't be deleted" not in err:
            logger.debug(f"safe_delete xətası: {e}")
    return False


def delete_after(target, delay: float = 5.0):
    """Mesajı təyin olunmuş saniyədən sonra arxa planda silir (əsas prosesi bloklamır)."""
    if target and delay >= 0:
        spawn(_delayed_delete(target, delay))


async def send_temp(target, text: str, seconds: float = 5.0, **kwargs):
    """Müvəqqəti mesaj göndərir və saniyədən sonra arxa planda avtomatik silir.

    target: Message və ya Bot/Chat instance (answer və ya send_message dəstəkləyən).
    """
    try:
        if hasattr(target, "answer"):
            msg = await target.answer(text, **kwargs)
        elif hasattr(target, "send_message"):
            msg = await target.send_message(text, **kwargs)
        else:
            return None
        if seconds > 0 and msg:
            delete_after(msg, seconds)
        return msg
    except Exception as e:
        logger.debug(f"send_temp xətası: {e}")
        return None


def tg_name(ent, unknown: str = "?") -> str:
    """Telegram istifadəçi / çat adı: ad soyad → başlıq → @username → ID."""
    if not ent:
        return unknown
    name = " ".join(x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x)
    return (name or getattr(ent, "title", None) or getattr(ent, "username", None)
            or f"ID: {getattr(ent, 'id', '?')}")


def tg_media_label(msg) -> str:
    """Telethon mesajının media növü ('🖼 Şəkil', '🎤 Səsli mesaj', ...); media yoxdursa ''."""
    m = getattr(msg, "media", None)
    if not m:
        return ""
    from telethon.tl import types as t
    if isinstance(m, t.MessageMediaPhoto):
        return "🖼 Şəkil"
    if isinstance(m, t.MessageMediaPoll):
        return "📊 Sorğu"
    if isinstance(m, t.MessageMediaContact):
        return "👤 Əlaqə"
    if isinstance(m, (t.MessageMediaGeo, t.MessageMediaGeoLive, t.MessageMediaVenue)):
        return "📍 Məkan"
    if isinstance(m, t.MessageMediaWebPage):
        return "🔗 Keçid"
    if isinstance(m, t.MessageMediaDocument) and m.document:
        for attr in getattr(m.document, "attributes", ()):
            if isinstance(attr, t.DocumentAttributeAudio):
                return "🎤 Səsli mesaj" if attr.voice else "🎵 Audio"
            if isinstance(attr, t.DocumentAttributeVideo):
                return "📹 Dairəvi video" if attr.round_message else "🎥 Video"
            if isinstance(attr, t.DocumentAttributeSticker):
                return "🏷 Stiker"
        for attr in getattr(m.document, "attributes", ()):
            if isinstance(attr, t.DocumentAttributeFilename):
                return f"📄 Sənəd ({attr.file_name})"
        return "📄 Sənəd"
    return "📎 Media"


def ui_styled(cat: str = "user"):
    """📋 compact_ui_plugin: mesajı kod bloku kateqoriyasına sal (plugin yoxdursa — heç nə etmir)."""
    try:
        from bot.plugins.compact_ui_plugin import styled
        return styled(cat)
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def safe_plugin_name(filename: str, prefix_numeric: bool = False) -> str:
    """Plugin fayl adını təhlükəsiz modul adına çevirir (məs: 'my-plugin.py' -> 'my_plugin')."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0].lower()
    stem = re.sub(r"[^a-z0-9_]", "_", stem).strip("_")
    stem = re.sub(r"_+", "_", stem)
    if prefix_numeric and (not stem or stem[0].isdigit()):
        stem = "p_" + stem
    return stem[:40] if prefix_numeric else stem


# ───────────────────────── Sistem (/proc) oxuyucuları ─────────────────────────
def read_proc_rss() -> int:
    """Cari prosesin VmRSS yaddaşını baytla qaytarır (oxunmasa 0)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception as _e:
        logger.debug("udulan xəta [common/utilities.py:234]: %r", _e)
    return 0


def read_sys_uptime() -> float:
    """Sistemin uptime müddətini saniyə ilə qaytarır (oxunmasa 0.0)."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0


def read_sys_meminfo() -> dict:
    """Sistem yaddaşı haqqında (/proc/meminfo) məlumatları baytla qaytarır."""
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                val = v.strip().split()
                if val:
                    mem[k.strip()] = int(val[0]) * 1024
    except Exception as _e:
        logger.debug("udulan xəta [common/utilities.py:258]: %r", _e)
    return mem


# ───────────────────────── Media və Şəbəkə (HTTP Pool) ─────────────────────────
_SHARED_HTTP_SESSION = None


async def get_http_session():
    """Bütün layihə üçün ortaq, TCP keep-alive hovuzlu aiohttp ClientSession qaytarır."""
    global _SHARED_HTTP_SESSION
    if _SHARED_HTTP_SESSION is None or _SHARED_HTTP_SESSION.closed:
        import aiohttp
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300, keepalive_timeout=30)
        _SHARED_HTTP_SESSION = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15),
        )
    return _SHARED_HTTP_SESSION


async def close_http_session():
    """Bağlanışda paylaşılan HTTP sessiyasını bağlayır."""
    global _SHARED_HTTP_SESSION
    if _SHARED_HTTP_SESSION is not None and not _SHARED_HTTP_SESSION.closed:
        await _SHARED_HTTP_SESSION.close()
        _SHARED_HTTP_SESSION = None


async def fetch_telegram_thumb(url_or_id: str, session=None) -> bytes | None:
    """Telegram üçün audio üz qabığı (max 200 KB) yükləyir. Video ID və ya URL qəbul edir."""
    if not url_or_id:
        return None
    url = f"https://i.ytimg.com/vi/{url_or_id}/mqdefault.jpg" if ("/" not in url_or_id and len(url_or_id) == 11) else url_or_id
    try:
        sess = session or await get_http_session()
        async with sess.get(url) as r:
            data = await r.read() if r.status == 200 else None
        return data if data and len(data) <= 200 * 1024 else None
    except Exception as e:
        logger.debug(f"Thumbnail yüklənmədi ({url_or_id}): {e}")
        return None


async def load_plugins(context, plugins_dir=None, package: str = "bot.plugins", label: str = "🤖"):
    """<qovluq>/*.py plugin-lərini import edib PRIORITY sırası ilə setup(context) çağırır.

    Default — bot plugin-ləri (bot/plugins, paket adı bot.plugins). Nəticə context.plugin_status-a
    yazılır (update_notifier creator-a göndərir); bot və userbot hissələri eyni lüğəti paylaşır.
    """
    plugins_dir = Path(plugins_dir) if plugins_dir else BASE_DIR / Path(*package.split("."))
    logger.info(f"{label} 🔍 Plugin-lər axtarılır: {plugins_dir}")

    status = getattr(context, "plugin_status", None)
    if status is None:
        status = context.plugin_status = {}

    # 1) Hamısını import et
    modules = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"{package}.{path.stem}"
        logger.info(f"🔄 Loading: {module_name}")
        try:
            modules.append(importlib.import_module(module_name))
        except Exception as e:
            status[path.name] = f"import xətası: {type(e).__name__}: {e}"
            logger.error(f"❌ Failed to import {module_name}: {e}", exc_info=True)

    # 2) PRIORITY-yə görə sırala (kiçik = əvvəl). Plugin-də PRIORITY yoxdursa 50.
    #    Ümumi ("catch-all") handler-i olan plugin PRIORITY yüksək qoyub sona keçir,
    #    beləcə digər plugin-lərin (məs. audio editor) handler-lərini kəsmir.
    modules.sort(key=lambda m: getattr(m, "PRIORITY", 50))

    # 3) setup() çağır
    for module in modules:
        name = module.__name__
        if not hasattr(module, "setup"):
            logger.warning(f"⚠️ No setup() in: {name}")
            continue
        try:
            if asyncio.iscoroutinefunction(module.setup):
                await module.setup(context)
            else:
                module.setup(context)
            status[f"{name.split('.')[-1]}.py"] = "ok"
            logger.info(f"✅ Loaded: {name}")
        except Exception as e:
            status[f"{name.split('.')[-1]}.py"] = f"setup xətası: {type(e).__name__}: {e}"
            logger.error(f"❌ Failed to load {name}: {e}", exc_info=True)
    return modules


def restart_argv() -> list:
    """Prosesi eyni əmrlə yenidən başlatmaq üçün argv.

    sys.argv "python -m bot.main" əmrində modulu fayl yolu kimi verir (paket importları qırılır),
    sys.orig_argv isə əmri olduğu kimi saxlayır: [python, -m, bot.main] / [python, init.py, bot].
    """
    import sys
    args = getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv]
    return [sys.executable, *args[1:]]


async def run_shell(cmd: str, timeout: int = 600, env: dict = None, input_data: str = None) -> tuple[int, str]:
    """Asinxron shell əmrini təhlükəsiz icra edir və (returncode, stdout+stderr) qaytarır."""
    environ = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if env:
        environ.update(env)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=environ,
        )
        inp_bytes = input_data.encode("utf-8") if input_data is not None else None
        out, _ = await asyncio.wait_for(proc.communicate(input=inp_bytes), timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):      # yetim proses (git pull / pip) qalmasın
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)
        return 124, "⏱ vaxt bitdi"
    except Exception as e:
        return 1, str(e)[:200]


async def run_exec(*cmd: str, timeout: int = 60, env: dict = None) -> tuple[int, str, str]:
    """Asinxron prosesi təhlükəsiz başladır və (returncode, stdout, stderr) qaytarır."""
    environ = {**os.environ, "LC_ALL": "C"}
    if env:
        environ.update(env)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environ,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception as _e:
            logger.debug("udulan xəta [common/utilities.py:398]: %r", _e)
        return 124, "", f"⏱ vaxt bitdi ({timeout} san.)"
    except Exception as e:
        return 1, "", str(e)


def safe_remove(*paths: str) -> int:
    """Verilmiş fayl yollarını təhlükəsiz şəkildə silir (xəta vermədən). Silinən sayını qaytarır."""
    deleted = 0
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
                deleted += 1
            except OSError:
                pass
    return deleted


async def cleanup_downloads(dirs: tuple[str, ...] = None) -> tuple[int, int]:
    """Müvəqqəti yükləmə və işçi qovluqları (download/, data/shazam_tmp/, /dev/shm/dllmaster, data/voice_cache) təmizləyir."""
    target_dirs = dirs or (DOWNLOAD_DIR, os.path.join("data", "shazam_tmp"), "/dev/shm/dllmaster", os.path.join("data", "temp"), os.path.join("data", "voice_cache"))
    freed_bytes, freed_files = 0, 0
    for d in target_dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    sz = os.path.getsize(file_path)
                    os.unlink(file_path)
                    freed_bytes += sz
                    freed_files += 1
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
    return freed_bytes, freed_files


cleanup_temp_files = cleanup_downloads


async def cleanup_records() -> tuple[int, int]:
    """data/records/ qovluğundakı göndərilmiş bütün hazır faylları təmizləyir.
    Hələ göndərilməyən (outbox.json-da olan) faylları saxlayır.
    (freed_bytes, freed_files) qaytarır.
    """
    import json
    import shutil

    records_dir = os.path.join(str(BASE_DIR), "data", "records")
    outbox_file = os.path.join(records_dir, "outbox.json")
    if not os.path.isdir(records_dir):
        return 0, 0

    keep_paths = {os.path.abspath(outbox_file)}
    if os.path.isfile(outbox_file):
        try:
            with open(outbox_file, encoding="utf-8") as f:
                entries = json.load(f)
                if isinstance(entries, list):
                    for x in entries:
                        if isinstance(x, dict) and x.get("path"):
                            keep_paths.add(os.path.abspath(x["path"]))
        except Exception as e:
            logger.debug(f"outbox.json oxunmadı: {e}")

    freed_bytes = 0
    freed_files = 0
    for name in os.listdir(records_dir):
        p = os.path.join(records_dir, name)
        abs_p = os.path.abspath(p)
        if abs_p in keep_paths:
            continue
        try:
            if os.path.isfile(p):
                sz = os.path.getsize(p)
                os.remove(p)
                freed_bytes += sz
                freed_files += 1
            elif os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        except OSError as e:
            logger.debug(f"cleanup_records fayl silinmədi ({p}): {e}")

    return freed_bytes, freed_files


# ─────────────────────────────────────────────────────────────
# 🤖 Sürətləndirmə Avto / Manual Rejim & ⚡ RAM Diski İdarəsi
# ─────────────────────────────────────────────────────────────
_ACCEL_AUTO_OVERRIDE: bool | None = None
_RAM_DISK_OVERRIDE: bool | None = None


def is_accel_auto_enabled() -> bool:
    """Sürətləndirmə və resurs idarəsinin Avtomatik rejimdə olub-olmaması (susmaya görə Avto aktivdir)."""
    if _ACCEL_AUTO_OVERRIDE is not None:
        return _ACCEL_AUTO_OVERRIDE
    try:
        from common.database import get_db
        val = get_db().get_setting("opt:accel_auto", "1")
        if val is not None:
            return val != "0"
    except Exception as _e:
        logger.debug("udulan xəta [common/utilities.py:457]: %r", _e)
    return True


def set_accel_auto_enabled(enabled: bool):
    """Sürətləndirmə avto rejimini açır və ya manual rejimə keçirir."""
    global _ACCEL_AUTO_OVERRIDE
    _ACCEL_AUTO_OVERRIDE = enabled
    try:
        from common.database import get_db
        get_db().set_setting("opt:accel_auto", "1" if enabled else "0")
    except Exception as e:
        logger.warning(f"Accel auto ayarı bazaya saxlanmadı (yaddaşda tətbiq edildi): {e}")


def is_ram_disk_enabled() -> bool:
    """RAM Diski (/dev/shm) funksiyasının aktiv olub-olmaması (susmaya görə Linux-da aktivdir)."""
    avail = os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK)
    if not avail:
        return False
    if _RAM_DISK_OVERRIDE is not None:
        return _RAM_DISK_OVERRIDE
    if is_accel_auto_enabled():
        # Avto rejim: /dev/shm-də ən azı 300MB boş yer varsa avtomatik aktivdir
        try:
            import shutil
            usage = shutil.disk_usage("/dev/shm")
            if usage.free < 300 * 1024 * 1024:
                return False
        except Exception as _e:
            logger.debug("udulan xəta [common/utilities.py:487]: %r", _e)
        return True
    try:
        from common.database import get_db
        val = get_db().get_setting("opt:ram_disk", "1")
        if val is not None:
            return val != "0"
    except Exception as _e:
        logger.debug("udulan xəta [common/utilities.py:495]: %r", _e)
    return True


def set_ram_disk_enabled(enabled: bool):
    """RAM Diski ayarını yadda saxlayır."""
    global _RAM_DISK_OVERRIDE
    _RAM_DISK_OVERRIDE = enabled
    try:
        from common.database import get_db
        get_db().set_setting("opt:ram_disk", "1" if enabled else "0")
    except Exception as e:
        logger.warning(f"RAM disk ayarı bazaya saxlanmadı (yaddaşda tətbiq edildi): {e}")


def get_temp_dir(sub: str = "") -> str:
    """Müvəqqəti fayllar üçün qovluq qaytarır: RAM diski açıqsa /dev/shm/dllmaster/<sub_dir>, yoxsa data/temp/<sub_dir>."""
    if is_ram_disk_enabled():
        base = "/dev/shm/dllmaster"
    else:
        base = os.path.join("data", "temp")
    path = os.path.join(base, sub) if sub else base
    os.makedirs(path, exist_ok=True)
    return path


def ram_disk_stats() -> dict:
    """RAM diskinin (/dev/shm) ümumi vəziyyəti və müvəqqəti fayl statistikası."""
    avail = os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK)
    enabled = is_ram_disk_enabled()
    shm_total, shm_free = 0, 0
    if avail:
        try:
            import shutil
            usage = shutil.disk_usage("/dev/shm")
            shm_total, shm_free = usage.total, usage.free
        except Exception as _e:
            logger.debug("udulan xəta [common/utilities.py:532]: %r", _e)

    temp_dir = "/dev/shm/dllmaster" if enabled else os.path.join("data", "temp")
    file_cnt, size_bytes = 0, 0
    if os.path.isdir(temp_dir):
        for root, _, files in os.walk(temp_dir):
            for f in files:
                file_cnt += 1
                try:
                    size_bytes += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass

    return {
        "available": avail,
        "enabled": enabled,
        "path": temp_dir,
        "total_gb": shm_total / (1024 ** 3),
        "free_gb": shm_free / (1024 ** 3),
        "files_count": file_cnt,
        "used_mb": size_bytes / (1024 ** 2),
    }


async def cleanup_ram_disk() -> tuple[int, int]:
    """RAM diski və bütün müvəqqəti qovluqları təmizləyir."""
    return await cleanup_downloads(("/dev/shm/dllmaster", os.path.join("data", "temp"), DOWNLOAD_DIR, os.path.join("data", "shazam_tmp")))


async def auto_cleanup_temp_files(max_age_seconds: int = 7200, max_ramdisk_percent: float = 80.0) -> tuple[int, int]:
    """
    RAM diski və müvəqqəti qovluqlar üçün ağıllı avtomatik təmizləyici:
    1. Yaşı `max_age_seconds`-dan (standart: 2 saat) çox olan köhnə müvəqqəti faylları silir.
    2. Əgər RAM diski doluluğu `max_ramdisk_percent`-i (standart: 80%) aşırsa, ən köhnə faylları hədd altına düşənə qədər təmizləyir.
    """
    target_dirs = (DOWNLOAD_DIR, os.path.join("data", "shazam_tmp"), "/dev/shm/dllmaster", os.path.join("data", "temp"))
    now = time.time()
    freed_bytes, freed_files = 0, 0
    all_files: list[tuple[float, int, str]] = []

    for d in target_dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    stat = os.stat(file_path)
                    all_files.append((stat.st_mtime, stat.st_size, file_path))
                    if now - stat.st_mtime > max_age_seconds:
                        os.unlink(file_path)
                        freed_bytes += stat.st_size
                        freed_files += 1
                except Exception as _e:
                    logger.debug("udulan xəta [common/utilities.py:586]: %r", _e)

    # RAM diski hədd qoruyucusu
    if os.path.exists("/dev/shm"):
        try:
            import shutil
            usage = shutil.disk_usage("/dev/shm")
            pct = (usage.used / max(usage.total, 1)) * 100
            if pct > max_ramdisk_percent:
                all_files.sort(key=lambda x: x[0])
                for mtime, sz, fpath in all_files:
                    if os.path.exists(fpath):
                        try:
                            os.unlink(fpath)
                            freed_bytes += sz
                            freed_files += 1
                            usage = shutil.disk_usage("/dev/shm")
                            if (usage.used / max(usage.total, 1)) * 100 <= max_ramdisk_percent:
                                break
                        except Exception as _e:
                            logger.debug("udulan xəta [common/utilities.py:606]: %r", _e)
        except Exception as _e:
            logger.debug("udulan xəta [common/utilities.py:608]: %r", _e)

    return freed_bytes, freed_files


async def temp_cleaner_loop(interval_seconds: int = 1800):
    """Hər 30 dəqiqədən bir arxa fonda köhnə temp faylları təmizləyən və RAM diskini qoruyan vəzifə."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            freed_b, freed_cnt = await auto_cleanup_temp_files()
            if freed_cnt > 0:
                logger.info("🧹 Avto-təmizləyici: %d müvəqqəti fayl (%s) təmizləndi", freed_cnt, format_bytes(freed_b))
            try:                                           # log qovluğu yalnız başlanğıcda deyil, dövri də məhdudlaşdırılsın
                from common.logger import enforce_log_limits
                n = await asyncio.to_thread(enforce_log_limits)
                if n:
                    logger.info("🧹 Köhnə log faylları təmizləndi: %d", n)
            except Exception as e:
                logger.debug("log təmizliyi xətası: %r", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Avto-təmizləyici xətası: %r", e)


class CommandLoggerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable,
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        if event.text and event.text.startswith("/") and event.from_user:
            logger.info(f"User {event.from_user.id} executed command: {event.text}")
        return await handler(event, data)


def update_config_env(key: str, value: str, filepath: str = None) -> bool:
    """config.env faylında açarı etibarlı şəkildə yeniləyir və ya əlavə edir."""
    target = Path(filepath) if filepath else (BASE_DIR / "config.env")
    try:
        lines = []
        found = False
        if target.exists():
            with open(target, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines = []
        pattern = re.compile(r"^(\s*" + re.escape(key) + r"\s*=\s*).*$")
        for line in lines:
            if pattern.match(line):
                new_lines.append(f'{key} = "{value}"\n')
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f'{key} = "{value}"\n')
        with open(target, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    except Exception as e:
        logger.error(f"config.env yenilənmədi ({key}): {e}")
        return False


# common.formatting — tək mənbə; köhnə importlar (from common.utilities import ...) üçün re-export.
# Əvvəl eyni adlı yerli funksiyalar da var idi və bu blok onları səssizcə üstələyirdi
# (truncate_text(..., normalize_spaces=True) TypeError atırdı).
from common.formatting import (  # noqa: E402,F401
    ago,
    clean_title,
    fmt_bytes,
    fmt_date,
    render_box,
    truncate_text,
    bar,
    fmt_span,
    fmt_uptime,
    format_bytes,
    format_clock,
    format_duration,
    format_ts,
    format_until,
    format_uptime,
    get_tz,
    get_tz_name,
    get_tz_with_name,
    human,
    human_bytes_short,
    progress_bar,
    vertical_bar,
    sparkline_zero as sparkline,         # köhnə ad: 0-dan maksimuma
)
