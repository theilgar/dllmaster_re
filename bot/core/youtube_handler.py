import os
import logging
import asyncio
import re
import shutil
import socket
import time
import uuid
from yt_dlp.utils import DownloadCancelled   # plugin-lər "dayandırıldı" halını tanımaq üçün import edir
from common.utilities import format_duration
from bot.core import webprofile
from bot.core import yt_accounts   # 🔑 bir neçə YouTube hesabı — rollar və blokda avtomatik keçid
from bot.core import cpu_pool  # ⚙️ yt-dlp ayrıca proseslərdə — bot donmur, bütün nüvələr işləyir

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "download"
# Bir yükləmənin yuxarı həddi: link / axtarışla gələn 10 saatlıq video disk və trafiki doldurmasın
MAX_DOWNLOAD_BYTES = int(float(os.getenv("DL_MAX_MB", "200")) * 1024 * 1024)


def get_download_dir() -> str:
    """Aktiv yükləmə qovluğu: /menu-da RAM diski aktivdirsə /dev/shm, deaktivdirsə fiziki disk."""
    try:
        from common.utilities import get_temp_dir, is_ram_disk_enabled
        if is_ram_disk_enabled():
            return get_temp_dir("download")
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/youtube_handler.py:28]: %r", _e)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return DOWNLOAD_DIR

# YouTube artıq formatları açmaq üçün JS challenge həll etməyi tələb edir:
# yt-dlp-yə JS runtime (deno tövsiyə olunur, ya node>=20) və yt-dlp-ejs lazımdır.
def _build_js_opts() -> dict:
    opts = {'remote_components': ['ejs:github']}
    runtimes = {}
    for name in ('deno', 'node', 'bun'):
        path = shutil.which(name)
        if path:
            runtimes[name] = {'path': path}
    if runtimes:
        opts['js_runtimes'] = runtimes
        logger.info("JS runtime tapıldı: " + ", ".join(f"{n}={v['path']}" for n, v in runtimes.items()))
    else:
        logger.error("JS runtime TAPILMADI (deno/node/bun). YouTube yükləməsi işləməyəcək — deno və ya node>=20 quraşdırın.")
    return opts


JS_OPTS = _build_js_opts()


def _px():
    from common import proxy
    return proxy


def cookie_opts() -> dict:
    """Cookies — bu zolağın (📦 depo / 🎵 istifadəçi) hazırkı 🔑 YouTube hesabına görə
    (⭐ əsas: /menu → 🌐 Brauzer / cookies ayarı). İşlək hesab yoxdursa — cookiesiz."""
    return yt_accounts.opts(yt_accounts.pick(cpu_pool._lane()))


def remove_partial(base_name: str):
    """Dayandırılmış yükləmədən qalan faylları silir (base_name.*)."""
    check_dirs = {DOWNLOAD_DIR}
    try:
        check_dirs.add(get_download_dir())
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/youtube_handler.py:69]: %r", _e)
    for d in check_dirs:
        try:
            if not os.path.exists(d):
                continue
            for name in os.listdir(d):
                if name.startswith(base_name + "."):
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass
        except OSError:
            pass


_SMART_COPY_OVERRIDE: bool | None = None
_PARALLEL_DL_OVERRIDE: bool | None = None


def is_smart_copy_enabled() -> bool:
    """Audio axını AAC olduqda təkrar transcode etmədən birbaşa kopyalama (-c:a copy)."""
    try:
        from common.utilities import is_accel_auto_enabled
        if is_accel_auto_enabled():
            return True
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/youtube_handler.py:95]: %r", _e)
    if _SMART_COPY_OVERRIDE is not None:
        return _SMART_COPY_OVERRIDE
    try:
        from common.database import get_db
        val = get_db().get_setting("opt:smart_copy", "1")
        return val != "0"
    except Exception:
        return True


def set_smart_copy_enabled(enabled: bool):
    global _SMART_COPY_OVERRIDE
    _SMART_COPY_OVERRIDE = enabled
    try:
        from common.database import get_db
        get_db().set_setting("opt:smart_copy", "1" if enabled else "0")
    except Exception as e:
        logger.warning(f"Smart copy ayarı bazaya saxlanmadı: {e}")


def is_parallel_dl_enabled() -> bool:
    """yt-dlp DASH/HLS audio fraqmentlərini paralel axınlarla endirsinmi (4x sürət)."""
    try:
        from common.utilities import is_accel_auto_enabled
        if is_accel_auto_enabled():
            return True
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/youtube_handler.py:123]: %r", _e)
    if _PARALLEL_DL_OVERRIDE is not None:
        return _PARALLEL_DL_OVERRIDE
    try:
        from common.database import get_db
        val = get_db().get_setting("opt:parallel_dl", "1")
        return val != "0"
    except Exception:
        return True


def set_parallel_dl_enabled(enabled: bool):
    global _PARALLEL_DL_OVERRIDE
    _PARALLEL_DL_OVERRIDE = enabled
    try:
        from common.database import get_db
        get_db().set_setting("opt:parallel_dl", "1" if enabled else "0")
    except Exception as e:
        logger.warning(f"Parallel DL ayarı bazaya saxlanmadı: {e}")


async def get_audio_codec(path: str) -> str:
    """Faylın daxili audio axınının kodekini tapır (aac, opus, vorbis, mp3...)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0",
            path, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        return out.decode().strip().lower()
    except Exception:
        return ""


async def convert_to_m4a(src_path: str, base_name: str, cancel_event=None) -> str:
    """Yüklənmiş video/audio faylını ffmpeg ilə m4a (AAC) formatına çevirir."""
    dl_dir = os.path.dirname(src_path) or get_download_dir()
    dst_path = os.path.join(dl_dir, f"{base_name}.m4a")

    # Mənbə artıq m4a-dırsa, təkrar çevirməyə ehtiyac yoxdur
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        return dst_path

    # ⚡ Ağıllı axın kopyalama: audio axını artıq aac-dirsə, yenidən encode etmə, birbaşa kopyala (-c:a copy)!
    codec = await get_audio_codec(src_path) if is_smart_copy_enabled() else ""
    if codec == "aac":
        audio_args = ["-c:a", "copy"]
        DL_STATS["stream_copied"] = DL_STATS.get("stream_copied", 0) + 1
        logger.debug(f"⚡ Ağıllı stream copy tətbiq edildi: {os.path.basename(src_path)} (aac)")
    else:
        audio_args = ["-threads", "0", "-c:a", "aac", "-b:a", "192k"]

    proc = await asyncio.create_subprocess_exec(
        *cpu_pool.ffmpeg_prefix(), "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vn", "-map", "0:a:0",
        *audio_args,
        "-movflags", "+faststart",
        dst_path,
        stdin=asyncio.subprocess.DEVNULL,           # terminal girişi bağlı qalmasın (SIGTTIN → donma)
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # Çevirmə zamanı "dayandır" basılarsa ffmpeg dərhal söndürülür
    while True:
        try:
            await asyncio.wait_for(proc.wait(), 0.5)
            break
        except asyncio.TimeoutError:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                await proc.wait()
                for p in (src_path, dst_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
    stderr = await proc.stderr.read()

    # Mənbə fayl artıq lazım deyil
    try:
        os.remove(src_path)
    except OSError:
        pass

    if proc.returncode != 0 or not os.path.exists(dst_path):
        raise RuntimeError(f"ffmpeg çevirmə xətası: {stderr.decode(errors='ignore').strip()[-300:]}")
    return dst_path


# ───────────────────────── Yükləmə rejimi (/menu → 🖥 Sistem → 🎧 Yükləmə formatı) ─────────────────────────
# ⚡ direct_m4a: YouTube-un hazır m4a (AAC) audio axını birbaşa endirilir — çevirmə yoxdur, ən sürətli yol
# 🎞 ffmpeg:     m4a axını yoxdursa (və ya birbaşa rejim söndürülübsə) ən yaxşı audio endirilib m4a-ya çevrilir
DL_STATS = {"direct": 0, "converted": 0, "original": 0, "stream_copied": 0}

# Son yüklənən videoların YouTube metadata-sı (video_id → artist / track / kanal) — music_plugin oxuyur
VIDEO_META = {}


def remember_meta(info: dict):
    vid = info.get("id")
    if not vid:
        return
    artists = info.get("artists") or []
    VIDEO_META[vid] = {
        "artist": info.get("artist") or (", ".join(artists[:3]) if artists else None),   # YouTube Music metadata
        "track": info.get("track"),
        "channel": info.get("channel") or info.get("uploader"),
        "title": info.get("title"),
    }
    if len(VIDEO_META) > 2000:
        for k in list(VIDEO_META)[:1000]:
            VIDEO_META.pop(k, None)


def _setting_on(key: str, default: bool = True) -> bool:
    try:
        from common.database import get_db
        value = get_db().get_setting(key)
    except Exception:
        value = None
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "on", "true", "yes")


# ⬇️ Toplu yükləmə (Hamısını yüklə, Spotify playlist / albom): eyni anda neçə mahnı hazırlansın
BATCH_PARALLEL_SETTING = "dl:batch_parallel"
BATCH_PARALLEL_STEPS = [1, 2, 3, 4, 5, 6, 8, 10]
BATCH_PARALLEL_DEFAULT = 2


# ── 🤖 Avto / ✋ Manual rejimləri (/menu → 🎵 YouTube): avtoda dəyəri bot özü seçir ──
# Rejim açarı hələ yazılmayıbsa: əvvəl əl ilə FƏRQLİ dəyər seçilibsə — manual (seçim qorunur), yoxsa avto.
def _mode(key: str, value_key: str, auto_value: str) -> str:
    from common.database import get_setting
    m = get_setting(key)
    if m in ("auto", "manual"):
        return m
    v = get_setting(value_key)
    return "manual" if v not in (None, "", auto_value) else "auto"


def set_mode_setting(key: str, mode: str):
    if mode in ("auto", "manual"):
        from common.database import set_setting
        set_setting(key, mode)


BATCH_MODE_SETTING, SEARCH_MODE_SETTING, FMT_MODE_SETTING = "dl:batch_mode", "search:mode", "dl:fmt_mode"


def batch_mode() -> str:
    return _mode(BATCH_MODE_SETTING, BATCH_PARALLEL_SETTING, str(BATCH_PARALLEL_DEFAULT))


def batch_auto_value() -> int:
    """🤖 Avto: istifadəçi proses sayının yarısı, 2–4 arası (4-dən çoxu YouTube 403 riskini artırır)."""
    try:
        from bot.core import cpu_pool
        return max(2, min(4, cpu_pool.user_procs() // 2))
    except Exception:
        return BATCH_PARALLEL_DEFAULT


def batch_parallel() -> int:
    if batch_mode() == "auto":
        return batch_auto_value()
    try:
        from common.database import get_db
        v = int(get_db().get_setting(BATCH_PARALLEL_SETTING) or BATCH_PARALLEL_DEFAULT)
    except Exception:
        v = BATCH_PARALLEL_DEFAULT
    return max(1, min(BATCH_PARALLEL_STEPS[-1], v))


def step_batch_parallel(direction: int) -> int:
    cur = batch_parallel()
    if direction > 0:
        nxt = next((x for x in BATCH_PARALLEL_STEPS if x > cur), BATCH_PARALLEL_STEPS[-1])
    else:
        nxt = next((x for x in reversed(BATCH_PARALLEL_STEPS) if x < cur), 1)
    from common.database import get_db
    get_db().set_setting(BATCH_PARALLEL_SETTING, str(nxt))
    return nxt


def set_batch_parallel(v: int) -> int:
    val = max(1, min(BATCH_PARALLEL_STEPS[-1], int(v)))
    from common.database import get_db
    get_db().set_setting(BATCH_PARALLEL_SETTING, str(val))
    return val


def step_batch_parallel_delta(delta: int) -> int:
    cur = batch_parallel()
    return set_batch_parallel(cur + delta)


FMT_MODES = {
    "hybrid": "🤖 Hibrid",
    "direct": "⚡ Birbaşa m4a",
    "ffmpeg": "🎞 ffmpeg",
}


def set_fmt_mode(mode: str):
    try:
        from common.database import get_db
        get_db().set_setting(FMT_MODE_SETTING, mode)
    except Exception as e:
        logger.warning(f"Fmt mode bazaya saxlanmadı: {e}")


def set_download_preset(preset: str):
    try:
        from common.database import get_db
        if preset == "direct":
            get_db().set_setting("dl:direct_m4a", "1")
            get_db().set_setting("dl:ffmpeg", "0")
        elif preset == "ffmpeg":
            get_db().set_setting("dl:direct_m4a", "0")
            get_db().set_setting("dl:ffmpeg", "1")
        elif preset == "hybrid":
            get_db().set_setting("dl:direct_m4a", "1")
            get_db().set_setting("dl:ffmpeg", "1")
    except Exception as e:
        logger.warning(f"Download preset bazaya saxlanmadı: {e}")


def fmt_mode() -> str:
    from common.database import get_setting
    m = get_setting(FMT_MODE_SETTING)
    if m in ("auto", "manual"):
        return m
    return "auto" if _setting_on("dl:direct_m4a") and _setting_on("dl:ffmpeg") else "manual"


def download_mode() -> dict:
    if fmt_mode() == "auto":                          # 🤖 hibrid: m4a varsa birbaşa, yoxdursa ffmpeg
        return {"direct_m4a": True, "ffmpeg": True}
    return {"direct_m4a": _setting_on("dl:direct_m4a"), "ffmpeg": _setting_on("dl:ffmpeg")}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _format_string(direct: bool, ffmpeg: bool) -> str:
    if direct:
        # əvvəl hazır m4a audio, olmasa istənilən audio; birləşdirmə (video+audio) yalnız ffmpeg ilə
        return "bestaudio[ext=m4a]/bestaudio/best" + ("/bestvideo+bestaudio" if ffmpeg else "")
    return "bestaudio/best" + ("/bestvideo+bestaudio" if ffmpeg else "")


# ───────────────────────── PO token provider + 403 cooldown ─────────────────────────
# bgutil-ytdlp-pot-provider (pip + docker, port 4416) işləyirsə yt-dlp GVS PO Token-i avtomatik alır.
# Onda mweb/web klientləri 403 vermədən işləyir.
POT_HOST, POT_PORT = "127.0.0.1", int(os.getenv("BGUTIL_POT_PORT", "4416"))
_pot_cache = {"ok": False, "checked": 0.0, "version": None, "http": False}


def pot_status() -> dict:
    """bgutil provider vəziyyəti: {'up': port açıqdır, 'http': /ping cavab verir, 'version': ...} (60 san keşlənir).
    Yalnız port yoxlaması başqa prosesi də «provider» sayırdı — indi /ping ilə təsdiqlənir."""
    now = time.monotonic()
    if now - _pot_cache["checked"] < 60:
        return dict(_pot_cache)
    ok = http = False
    version = None
    try:
        with socket.create_connection((POT_HOST, POT_PORT), timeout=0.3):
            ok = True
    except OSError:
        pass
    if ok:
        try:
            import json as _json
            import urllib.request
            with urllib.request.urlopen(f"http://{POT_HOST}:{POT_PORT}/ping", timeout=1.5) as r:
                data = _json.loads(r.read().decode("utf-8", "replace") or "{}")
            http, version = True, str(data.get("version") or "") or None
        except Exception:
            http = False
    if (ok, http) != (_pot_cache["ok"], _pot_cache["http"]) or not _pot_cache["checked"]:
        state = f"✅ aktiv (v{version})" if http else ("⚠️ port açıqdır, /ping cavab vermir" if ok else "❌ yoxdur")
        logger.info(f"PO token provider ({POT_HOST}:{POT_PORT}): {state}")
    _pot_cache.update(ok=ok, http=http, version=version, checked=now)
    return dict(_pot_cache)


def pot_provider_up() -> bool:
    """bgutil HTTP serveri işləyir (port açıq + /ping). Nəticə 60 san keşlənir."""
    st = pot_status()
    return bool(st["ok"] and st["http"])


# Bütün cəhdlər 403 ilə bitəndə YouTube IP/hesabı müvəqqəti flag-ləyib.
# Davam edib vurmaq flag-i uzadır → növbə bir müddət gözlədilir.
COOLDOWN_SECONDS = int(os.getenv("YT_403_COOLDOWN", "300"))
_cooldown_until = 0.0

# 🍪 Cookies hesabı bloku: YouTube flag-lədiyi hesaba hər videonu "Video unavailable" kimi göstərir
# (və ya açıq "Your account has been rate-limited" yazır), halbuki eyni video cookies-siz açılır.
# Belə olanda cookies cəhdləri bir müddət ümumiyyətlə sınanmır — hər mahnı 4 boş cəhd itirmir,
# hesabı vurmağa davam edib bloku uzatmırıq.
COOKIE_BLOCK_SECONDS = yt_accounts.BLOCK_SECONDS     # blok hər 🔑 hesab üçün ayrıca (yt_accounts)

# 📦 Depo zolağı bloklananda ayrıca, daha uzun pauza — doldurucunun onlarla işçisi YouTube-u
# dayanmadan vurub hesabı / IP-ni yandırmasın (istifadəçilərin yükləmələri bundan asılı deyil).
DEPO_BLOCK_PAUSE = int(os.getenv("YT_DEPO_BLOCK_PAUSE", "900"))
_depo_pause_until = 0.0
# Depo yükləmələri arasında təsadüfi fasilə (san.) — "-t sleep" tövsiyəsi; 0 — söndür
DEPO_SLEEP = float(os.getenv("YT_DEPO_SLEEP", "3"))

ACCOUNT_BLOCK = ("Your account has been rate-limited", "This content isn't available, try again later")
UNAVAILABLE = "Video unavailable"
# Videonun özünə aid xətalar — üsul (klient) xarab deyil, ona görə 🎯 birinci üsul bunlara görə dəyişmir.
# ("Video unavailable" cookies ilə hesabın o videonu görməməsi də ola bilər — yenə üsulun günahı deyil.)
FILE_TOO_LARGE = "Fayl ölçü limitindən böyükdür"      # ytdl_worker.FILE_TOO_LARGE ilə eyni — bütün üsullarda eyni nəticə
VIDEO_ERRORS = (FILE_TOO_LARGE, UNAVAILABLE, "age-restricted", "Private video", "This video is unavailable",
                "live stream recording is not available", "members-only", "This video has been removed")


def cooldown_left(kind: str = "depo") -> int:
    """Qalan gözləmə (san.). kind="download" — yalnız ümumi 403 cooldown; "depo" — depo pauzası da daxil."""
    now = time.monotonic()
    left = _cooldown_until - now
    if kind == "depo":
        left = max(left, _depo_pause_until - now)
    return max(0, int(left))


def cookie_block_left(kind: str = "download") -> int:
    """Bu zolağın hesablarının hamısı bloklu olanda — ən tez açılanın qalan vaxtı; işlək hesab varsa 0."""
    if yt_accounts.pick(kind):
        return 0
    role = yt_accounts.role_of(kind)
    lefts = [yt_accounts.block_left(a["id"]) for a in yt_accounts.accounts()
             if a.get("on", True) and role in a["roles"] and yt_accounts.block_left(a["id"])]
    return min(lefts) if lefts else 0


def reset_cooldown():
    """/menu → 🎵 YouTube → ⏭ cooldown-u əl ilə sıfırlamaq üçün (öyrənilmiş üsul sırası da sıfırlanır)."""
    global _cooldown_until, _depo_pause_until
    _cooldown_until = _depo_pause_until = 0.0
    yt_accounts.unblock()
    reset_attempts()


def recheck_pot() -> bool:
    """Keşi atıb PO token provider-i yenidən yoxlayır (bloklayır — to_thread ilə çağır)."""
    _pot_cache["checked"] = 0.0
    return pot_provider_up()


async def _wait_cooldown(cancelled, kind: str = "download"):
    left = cooldown_left(kind)
    if not left:
        return
    logger.warning(f"YouTube cooldown aktivdir ({kind}) — {left} san gözlənilir")
    while cooldown_left(kind):
        if cancelled():
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
        await asyncio.sleep(1)


def _is_403(err) -> bool:
    text = str(err)
    return "403" in text or "Forbidden" in text


def _is_account_block(err) -> bool:
    text = str(err)
    return any(x in text for x in ACCOUNT_BLOCK)


def _block_cookies(reason: str, seconds: int = COOKIE_BLOCK_SECONDS, acc_id: str = ""):
    """🔑 Hesabı kənara çək — növbəti yükləmələr bu zolağın növbəti hesabı ilə (yoxdursa cookiesiz)."""
    yt_accounts.block(acc_id, reason, seconds)


def _is_blocked(errors: list) -> bool:
    """Uğursuzluq videonun özündən yox, IP / hesab blokundandır?
    Silinmiş video cookies-siz də "Video unavailable" verir; blokda isə cookies-siz klient videonu açır,
    amma yükləmədə 403 alır, ya da açıq "rate-limited" xəbəri gəlir."""
    if not errors:
        return False
    hard = any(_is_403(e) or _is_account_block(e) for _, e in errors)
    soft_ok = all(_is_403(e) or _is_account_block(e) or (UNAVAILABLE in str(e) and lbl.startswith("cookies +"))
                  for lbl, e in errors)
    return hard and soft_ok


# 🎯 Öyrənən sıra: son uğurlu üsul birinci; "format yoxdur" verən üsul 6 saat sıranın sonuna.
# Əvvəl PO token klientləri (mweb / web) HƏR mahnıda birinci sınanırdı və "Only images are available"
# ilə uğursuz olurdu → hər yükləmə 2 boş cəhd itirirdi. Vəziyyət settings-də saxlanır (restartdan sonra da).
# Birinci üsul zolaq üzrə ayrıca saxlanır (👤 istifadəçi / 📦 depo) və yalnız hazırkı birinci ÖZÜ uğursuz
# olanda dəyişir — əvvəl paralel işçilər onu bir-birinin əlindən alırdı (1500-ə yaxın dəyişiklik + baza yazısı).
FORMAT_ERRORS = ("Requested format is not available", "Only images are available", "No video formats found")
PENALTY_SECONDS = int(os.getenv("YT_ATTEMPT_PENALTY", str(6 * 3600)))
_att = {"loaded": False, "winners": {}, "penalty": {}}


def _att_load():
    if _att["loaded"]:
        return
    _att["loaded"] = True
    try:
        import json
        from common.database import get_db
        v = json.loads(get_db().get_setting("yt:attempts") or "{}")
        winners = v.get("winners")
        if not isinstance(winners, dict):
            old = v.get("winner")                      # köhnə format — bir ümumi "winner"
            winners = {"download": old, "depo": old} if old else {}
        _att["winners"] = {k: w for k, w in winners.items() if w}
        _att["penalty"] = {k: float(t) for k, t in (v.get("penalty") or {}).items()}
    except Exception as _e:
        logger.debug("udulan xəta [bot/core/youtube_handler.py:342]: %r", _e)


def _att_snapshot() -> str:
    import json
    now = time.time()
    return json.dumps({"winners": dict(_att["winners"]),
                       "penalty": {k: t for k, t in list(_att["penalty"].items()) if t > now}})


def _att_save_sync(payload: str = None):
    try:
        from common.database import get_db
        get_db().set_setting("yt:attempts", payload or _att_snapshot())
    except Exception as _e:
        logger.debug("udulan xəta [_att_save_sync]: %r", _e)


_ATT_SAVE_PENDING = False


def _att_save():
    """Debounce: hər uğurlu cəhddə ayrıca DB thread-i yox, 3 san.-də bir yazı. Snapshot loop-da
    götürülür — thread _att-ı oxuyarkən loop onu dəyişib "dictionary changed size" verməsin."""
    global _ATT_SAVE_PENDING
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _att_save_sync()
        return
    if _ATT_SAVE_PENDING:
        return
    _ATT_SAVE_PENDING = True

    def _flush():
        global _ATT_SAVE_PENDING
        _ATT_SAVE_PENDING = False
        from common.utilities import spawn
        spawn(asyncio.to_thread(_att_save_sync, _att_snapshot()))

    loop.call_later(3, _flush)


def attempt_success(label: str, kind: str = "download", failed: frozenset = frozenset()):
    _att_load()
    cur = _att["winners"].get(kind)
    changed = label in _att["penalty"]
    _att["penalty"].pop(label, None)
    # yalnız birinci üsul yoxdursa və ya BU yükləmədə özü uğursuz olubsa dəyiş
    if cur != label and (cur is None or cur in failed):
        _att["winners"][kind] = label
        changed = True
        logger.info(f"🎯 Yükləmə üsulu ({kind}): '{label}' — növbəti mahnılarda birinci sınanacaq")
    if changed:
        _att_save()


def attempt_failed(label: str, err):
    if not any(x in str(err) for x in FORMAT_ERRORS):
        return                                         # 403 / şəbəkə — müvəqqəti, cəzalandırma
    _att_load()
    if _att["penalty"].get(label, 0) < time.time():
        logger.info(f"🎯 '{label}' format vermir — {PENALTY_SECONDS // 3600} saat sıranın sonuna keçirildi")
    _att["penalty"][label] = time.time() + PENALTY_SECONDS
    for k, w in list(_att["winners"].items()):
        if w == label:
            _att["winners"].pop(k, None)
    _att_save()


def reset_attempts():
    _att.update(loaded=True, winners={}, penalty={})
    _att_save()


def attempt_order_info() -> str:
    _att_load()
    now = time.time()
    pen = [f"{k} ({int((t - now) / 60)} dəq.)" for k, t in _att["penalty"].items() if t > now]
    w = _att["winners"]
    text = f"🎯 birinci: 👤 {w.get('download') or '—'} · 📦 {w.get('depo') or '—'}"
    if pen:
        text += f" · ⏬ arxada: {', '.join(pen)}"
    for lane, icon in (("download", "👤"), ("depo", "📦")):
        cb = cookie_block_left(lane)
        if cb:
            text += f" · 🍪 {icon} bütün hesablar bloklu ({cb // 60 + 1} dəq.)"
    return text


def _build_attempts(kind: str = "download") -> list:
    """(təsvir, cookies istifadə olunsun?, player_client) — öyrənilmiş sıra ilə."""
    attempts = []
    pot = pot_provider_up()
    if pot:
        attempts.append(("cookies + mweb (PO token)", True, ['mweb']))
        attempts.append(("cookies + web (PO token)", True, ['web']))
    attempts += [
        ("cookies + default client", True, None),
        ("cookies + tv/web_safari", True, ['tv', 'web_safari']),
        # hesab videonu "Video unavailable" görəndə (flag) cookies-siz default açır və yükləyir;
        # android_vr isə PO token-siz 403 alır (13.10: 152 cəhddən 135-i) — son çarə
        ("cookies-siz + default client", False, None),
        ("cookies-siz + android_vr", False, ['android_vr']),
    ]
    if yt_accounts.pick(kind) is None:
        # 🍪 bu zolağın bütün hesabları bloklu (və ya hesab yoxdur) — cookies cəhdləri boşunadır
        attempts = [a for a in attempts if not a[1]]
        if pot:
            attempts.insert(0, ("cookies-siz + mweb (PO token)", False, ['mweb']))
    _att_load()
    now = time.time()
    winner = _att["winners"].get(kind)
    good = [a for a in attempts if _att["penalty"].get(a[0], 0) <= now]
    bad = [a for a in attempts if a not in good]
    good.sort(key=lambda a: 0 if a[0] == winner else 1)      # sabit sıralama: qalanları yerində
    return good + bad


async def download_as_m4a(url: str, base_name: str, browser: str, cancel_event=None, on_progress=None) -> str:
    """Videonu yükləyir (mümkün olarsa yalnız audio axını, yoxdursa video) və m4a-ya çevirir.

    YouTube tərəfi tez-tez "page needs to be reloaded" / "format not available" verir,
    ona görə bir neçə strategiya ardıcıl sınanır.
    """
    dl_dir = get_download_dir()
    os.makedirs(dl_dir, exist_ok=True)
    # 🔒 Unikal ad: başlıq 120 bayta kəsilir → "… Pt. 5" / "… Pt. 6" kimi fərqli videolar eyni ada düşür,
    # paralel işçilər bir-birinin faylını üstələyir / remove_partial() ilə silirdi (Errno 2, səhv audio depoda).
    base_name = f"{base_name}_{uuid.uuid4().hex[:8]}"

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    mode = download_mode()
    use_ffmpeg = mode["ffmpeg"] and ffmpeg_available()
    fmt = _format_string(mode["direct_m4a"], use_ffmpeg)

    global _cooldown_until, _depo_pause_until
    kind = "depo" if cpu_pool._lane() == "depo" else "download"
    await _wait_cooldown(cancelled, kind)

    attempts = _build_attempts(kind)
    acc = yt_accounts.pick(kind, advance=True)   # 🔑 bu yükləmənin hesabı (🔄 növbəli rejimdə növbə irəliləyir)
    tried = {acc["id"]} if acc else set()
    errors = []          # (label, xəta) — bütün cəhdlər loglanır
    last_error = None
    px = _px()
    proxy_url = px.current(kind)              # 🌐 bu yükləmənin proxy-si (rotasiya — yükləmə başına)
    retried = False
    i = 0
    while i < len(attempts):
        label, use_cookies, clients = attempts[i]
        i += 1
        if use_cookies and (acc is None or yt_accounts.block_left(acc["id"])):
            nxt = yt_accounts.pick(kind, exclude=tried)   # paralel işçi bu hesabı blokladı → növbəti hesab
            if nxt is None:
                continue
            acc = nxt
            tried.add(acc["id"])
        if cancelled():
            remove_partial(base_name)
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
        parallel_on = is_parallel_dl_enabled()
        ydl_opts = {
            'format': fmt,
            'outtmpl': os.path.abspath(os.path.join(dl_dir, f"{base_name}.%(ext)s")),
            'noplaylist': True,
            'nopart': True,
            'max_filesize': MAX_DOWNLOAD_BYTES,
            'retries': 3,
            'fragment_retries': 5,
            'quiet': True,
            # ua_opts() BURADA YOXDUR: googlevideo URL-ləri klientin öz UA-sına bağlıdır,
            # təsadüfi masaüstü UA (xüsusən tv / android_vr ilə) 403 verir. yt-dlp UA-nı özü seçir.
            **JS_OPTS,          # ⏹ dayandırma: cpu_pool bayraq faylı ilə (proses daxilində progress_hook)
        }
        if parallel_on:
            ydl_opts['concurrent_fragment_downloads'] = 4
            ydl_opts['http_chunk_size'] = 10485760
        if kind == "depo":
            ydl_opts['sleep_interval_requests'] = 1          # 📦 depo: sorğular arası pauza — rate-limit-i yumşaldır
                                                              # (👤 istifadəçi yükləməsi gözləmir — hər sorğuda +1 san. itirirdi)
        if kind == "depo" and DEPO_SLEEP > 0:
            ydl_opts['sleep_interval'] = DEPO_SLEEP              # 📦 depo: yükləmələr arası təsadüfi fasilə
            ydl_opts['max_sleep_interval'] = DEPO_SLEEP * 2
        if use_cookies:
            ydl_opts.update(yt_accounts.opts(acc))
        if clients:
            ydl_opts['extractor_args'] = {'youtube': {'player_client': clients}}
        if proxy_url:
            ydl_opts['proxy'] = proxy_url

        try:
            logger.info(f"Yükləmə cəhdi: {label}" + (f" · 🔑 {acc['name']}" if use_cookies and acc else "")
                        + (f" · 🌐 {px.mask(proxy_url)}" if proxy_url else ""))
            res = await cpu_pool.ytdl_download(url, ydl_opts, cancel_event, on_progress=on_progress)
            info = res["info"]
            remember_meta(info)
            src_path = res["path"]

            if not os.path.exists(src_path):
                raise FileNotFoundError("Yüklənən fayl tapılmadı")

            attempt_success(label, kind, frozenset(lbl for lbl, e in errors
                                                   if not any(x in str(e) for x in VIDEO_ERRORS)))
            px.report(proxy_url, True)
            if use_cookies and acc:
                yt_accounts.report(acc["id"], True)
            if errors:
                logger.info(f"Yükləmə '{label}' ilə alındı (əvvəlki uğursuz: {', '.join(l for l, _ in errors)})")
                # cookies "Video unavailable" dedi, cookies-siz isə açıldı → video var, hesab flag-lənib
                if not use_cookies and any(lbl.startswith("cookies +") and UNAVAILABLE in str(e)
                                           for lbl, e in errors):
                    for a_id in tried:
                        _block_cookies("cookies ilə 'Video unavailable', cookies-siz açıldı",
                                       COOKIE_BLOCK_SECONDS // 2, a_id)

            if cancelled():
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

            if src_path.lower().endswith(".m4a"):
                DL_STATS["direct"] += 1                  # ⚡ çevirməsiz
                return src_path
            if use_ffmpeg:
                DL_STATS["converted"] += 1               # 🎞 ffmpeg ilə m4a
                return await convert_to_m4a(src_path, base_name, cancel_event)
            DL_STATS["original"] += 1                    # ffmpeg söndürülüb — olduğu kimi (webm/opus və s.)
            logger.info(f"ffmpeg söndürülüb, fayl çevrilmədən göndərilir: {os.path.basename(src_path)}")
            return src_path

        except DownloadCancelled:
            logger.info(f"Yükləmə dayandırıldı: {url}")
            remove_partial(base_name)
            raise
        except Exception as e:
            if cancelled():
                remove_partial(base_name)
                raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
            last_error = e
            errors.append((label, e))
            if FILE_TOO_LARGE in str(e):               # videonun özündəndir: başqa üsul / hesab kömək etməz
                logger.info(f"Yükləmə ləğv: {str(e)[:120]}")
                remove_partial(base_name)
                break
            attempt_failed(label, e)
            logger.info(f"Cəhd uğursuz ({label}): {str(e)[:200]}")       # növbəti üsul sınanır — WARNING deyil
            remove_partial(base_name)
            if use_cookies and acc and _is_account_block(e):
                yt_accounts.report(acc["id"], False)
                _block_cookies("rate-limited", acc_id=acc["id"])
                nxt = yt_accounts.pick(kind, exclude=tried)  # 🔑 növbəti hesab — cookies üsulları əvvəldən
                if nxt is not None:
                    logger.info(f"🔑 '{acc['name']}' rate-limit aldı → '{nxt['name']}' hesabı ilə davam")
                    acc = nxt
                    tried.add(acc["id"])
                    i = 0
            # bütün üsullar bloklu → proxy / Tor varsa: gözləmək əvəzinə yeni IP ilə bir dəfə də
            if i >= len(attempts) and proxy_url and not retried and _is_blocked(errors):
                px.report(proxy_url, False, blocked=True)
                action = await asyncio.to_thread(px.on_blocked)
                nxt = px.current(kind)
                if action or (nxt and nxt != proxy_url):
                    logger.info(f"🌐 403 — {action or 'növbəti proxy'}, yenidən cəhd" +
                                (f" · {px.mask(nxt)}" if nxt else ""))
                    proxy_url, retried, i, errors = nxt, True, 0, []

    if last_error is None:
        last_error = RuntimeError("Heç bir yükləmə üsulu qalmadı (cookies bloklu, cookies-siz üsul yoxdur)")
    if _is_blocked(errors):
        _cooldown_until = time.monotonic() + COOLDOWN_SECONDS
        if kind == "depo":
            _depo_pause_until = time.monotonic() + DEPO_BLOCK_PAUSE
        hint = (" | PO token provider aktivdir — blok IP / hesab səviyyəsindədir (cookies yenilə, proxy / Tor və ya gözlə)"
                if pot_provider_up() else
                " | PO token provider işləmir — docker konteynerini yoxlayın (brainicism/bgutil-ytdlp-pot-provider, port 4416)")
        pause = f", 📦 depo {DEPO_BLOCK_PAUSE} san pauza" if kind == "depo" else ""
        logger.error(f"YouTube blokladı (403 / hesab) — {COOLDOWN_SECONDS} san cooldown{pause}{hint}: "
                     + " || ".join(f"[{l}] {str(e)[:120]}" for l, e in errors))
    else:
        # video özü əlçatmazdır (silinib, yaş məhdudiyyəti ...) — sistem xətası deyil
        logger.warning("Bütün cəhdlər uğursuz: " + " || ".join(f"[{l}] {str(e)[:160]}" for l, e in errors))

    raise last_error


# ───────────────────────── Axtarış mənbəyi (/menu → 🖥 Sistem → 🔎 Axtarış mənbəyi) ─────────────────────────
# yt   — adi YouTube axtarışı (klip, cover, live — hamısı)
# ytm  — YouTube Music, yalnız mahnılar (rəsmi audio, təmiz ad); nəticə olmasa YouTube-a keçir
# both — ikisi paralel, nəticələr növbə ilə qarışdırılır, təkrarlar silinir
SEARCH_SOURCES = {
    "yt": "▶️ YouTube",
    "ytm": "🎵 YouTube Music",
    "both": "🔀 Hər ikisi",
}
SEARCH_SETTING = "search:source"
DEFAULT_SOURCE = "ytm"          # /menu-dan heç nə seçilməyibsə: əvvəl YouTube Music

# ── YT Music nəticəsi sorğuya həqiqətən uyğundurmu? ──
# YTM demək olar həmişə "nəsə" qaytarır; mahnı orada yoxdursa da oxşar adlı başqa mahnılar gəlir.
# Ona görə ilk nəticələrdə sorğunun sözlərinin çoxu yoxdursa → "tapılmadı" sayılır və YouTube göstərilir.
_AZ_MAP = str.maketrans("əışçğöüƏIŞÇĞÖÜİ", "eiscgouEISCGOUI")
_RELEVANT_TOP = 5               # yoxlanılan ilk nəticələr
_RELEVANT_MIN = 0.6             # sorğu sözlərinin ən az 60%-i nəticədə olmalıdır


def _words(text: str) -> list:
    import unicodedata
    text = unicodedata.normalize("NFKD", (text or "").translate(_AZ_MAP).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.findall(r"\w+", text)


def _word_match(word: str, words: list) -> bool:
    from difflib import SequenceMatcher
    for w in words:
        if w == word or (len(word) >= 3 and (w.startswith(word) or word.startswith(w) and len(w) >= 3)):
            return True
        if len(word) >= 4 and SequenceMatcher(None, word, w).ratio() >= 0.8:   # kiçik yazı xətaları
            return True
    return False


def ytm_relevant(query: str, results: list) -> bool:
    q = [w for w in _words(query) if len(w) >= 2] or _words(query)
    if not q:
        return bool(results)
    for r in results[:_RELEVANT_TOP]:
        words = _words(r.get("title"))
        if sum(_word_match(w, words) for w in q) / len(q) >= _RELEVANT_MIN:
            return True
    return False
SEARCH_STATS = {"yt": 0, "ytm": 0, "fallback": 0}
_VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})")


def search_mode() -> str:
    return _mode(SEARCH_MODE_SETTING, SEARCH_SETTING, DEFAULT_SOURCE)


def search_source() -> str:
    if search_mode() == "auto":                       # 🤖 YT Music; uyğun nəticə yoxdursa YouTube-a keçir
        return DEFAULT_SOURCE
    try:
        from common.database import get_db
        value = (get_db().get_setting(SEARCH_SETTING) or DEFAULT_SOURCE).strip().lower()
    except Exception:
        value = DEFAULT_SOURCE
    return value if value in SEARCH_SOURCES else DEFAULT_SOURCE


def set_search_source(source: str):
    if source in SEARCH_SOURCES:
        try:
            from common.database import get_db
            get_db().set_setting(SEARCH_SETTING, source)
        except Exception as e:
            logger.warning(f"Search source bazaya saxlanmadı: {e}")


def set_search_mode(mode: str):
    try:
        from common.database import get_db
        get_db().set_setting(SEARCH_MODE_SETTING, mode)
    except Exception as e:
        logger.warning(f"Search mode bazaya saxlanmadı: {e}")


try:                                    # pip install ytmusicapi — artist + müddət ilə dəqiq nəticələr
    from ytmusicapi import YTMusic
except Exception:
    YTMusic = None
_ytmusic = None


def ytmusicapi_available() -> bool:
    return YTMusic is not None


def _get_ytmusic():
    global _ytmusic
    if _ytmusic is None and YTMusic is not None:
        _ytmusic = YTMusic()
    return _ytmusic


class YoutubeManager:
    def __init__(self, browser: str = "firefox"):
        self.browser = browser

    def get_ydl_opts(self):
        return {
            'extract_flat': True,
            'quiet': True,
            'socket_timeout': 30,
            **cookie_opts(),
            **_px().ytdlp_opts("search"),          # 🌐 Proxy / 🧅 Tor — /menu → 🖥 Sistem
            **webprofile.ua_opts(),
            **JS_OPTS,
        }

    @staticmethod
    def fast_opts() -> dict:
        """Axtarış üçün yüngül parametrlər: extract_flat axtarışa cookies / JS runtime lazım deyil.
        (Brauzer cookies-ini hər dəfə oxumaq + EJS yükləmək 0.5–2 san. əlavə edirdi.)"""
        return {'extract_flat': True, 'quiet': True, 'no_warnings': True, 'socket_timeout': 10,
                'skip_download': True, 'noplaylist': True, **_px().ytdlp_opts("search")}

    async def _yt_search(self, query: str, limit: int, fast: bool = False) -> list:
        """Adi YouTube axtarışı (ytsearch). fast=True — inline: növbəsiz, cookies/JS-siz."""
        url = f"ytsearch{int(limit)}:{query}"
        info = await (cpu_pool.fast_extract(url, self.fast_opts()) if fast
                      else cpu_pool.ytdl_extract(url, self.get_ydl_opts()))
        return [{
            'title': entry.get('title', 'Naməlum Mahnı'),
            'url': entry.get('url', ''),
            'duration': format_duration(entry.get('duration', 0)),
            'raw_duration': entry.get('duration', 0),
            'source': 'yt',
        } for entry in info.get('entries', []) if entry]

    async def _ytm_search(self, query: str, limit: int, fast: bool = False) -> list:
        """YouTube Music — yalnız mahnılar. ytmusicapi varsa onu, yoxdursa yt-dlp-ni istifadə edir."""
        yt_music = _get_ytmusic()
        if yt_music is not None:
            try:
                items = await (cpu_pool.fast_ytm_search(query, limit) if fast
                               else cpu_pool.ytm_search(query, limit))
            except Exception as e:                           # işçi prosesdə alınmadı — əsas prosesdə
                logger.debug(f"YTM prosesdə: {e}")
                items = await asyncio.to_thread(yt_music.search, query, filter="songs", limit=limit)
            results = []
            for it in items or []:
                vid = it.get('videoId')
                if not vid:
                    continue
                artists = ", ".join(a['name'] for a in (it.get('artists') or [])[:3] if a.get('name'))
                title = it.get('title') or 'Naməlum Mahnı'
                duration = int(it.get('duration_seconds') or 0)
                results.append({
                    'title': f"{artists} - {title}" if artists else title,
                    'url': f"https://www.youtube.com/watch?v={vid}",
                    'duration': format_duration(duration),
                    'raw_duration': duration,
                    'source': 'ytm',
                })
            return results[:limit]

        # yt-dlp yolu (#songs bölməsi): çox vaxt yalnız mahnı adı gəlir, artist / müddət olmaya bilər
        from urllib.parse import quote_plus
        opts = self.fast_opts() if fast else self.get_ydl_opts()
        opts['playlistend'] = limit
        url = f"https://music.youtube.com/search?q={quote_plus(query)}#songs"
        info = await (cpu_pool.fast_extract(url, opts) if fast else cpu_pool.ytdl_extract(url, opts))
        results = []
        for entry in info.get('entries') or []:
            if not entry:
                continue
            vid = entry.get('id') or ''
            if len(vid) != 11:
                continue
            title = entry.get('title') or 'Naməlum Mahnı'
            artist = entry.get('artist') or entry.get('channel') or entry.get('uploader')
            if artist and artist.lower() not in title.lower():
                title = f"{artist} - {title}"
            duration = entry.get('duration') or 0
            results.append({
                'title': title,
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
                'source': 'ytm',
            })
        return results[:limit]

    async def youtube_search(self, query: str, limit: int = 25, source: str = None, fast: bool = False) -> list:
        """
        Axtarış — mənbə /menu-dan seçilir (search_source()): yt | ytm | both.
        Nəticə formatı hər mənbədə eynidir: {title, url, duration, raw_duration, source}.
        """
        explicit = source in SEARCH_SOURCES            # çağıran mənbəni özü seçibsə 🤖 uyğunluq süzgəci tətbiq olunmur
        source = source if explicit else search_source()
        query = re.sub(r'["\'«»“”„‟`]', ' ', query or '').strip()
        query = re.sub(r'\s+', ' ', query).strip(" -:;,.")
        try:
            if source == "yt":
                SEARCH_STATS["yt"] += 1
                return await self._yt_search(query, limit, fast)

            if source == "ytm":
                try:
                    results = await self._ytm_search(query, limit, fast)
                except Exception as e:
                    logger.warning(f"YouTube Music axtarışı alınmadı: {e}")
                    results = []
                if results and not explicit and search_mode() == "auto" and not ytm_relevant(query, results):
                    logger.info(f"YT Music nəticələri sorğuya uyğun deyil, YouTube göstərilir: {query!r}")
                    results = []
                if results:
                    SEARCH_STATS["ytm"] += 1
                    return results
                # YTM-də heç bir nəticə tapılmadıqda (0 nəticə) → ehtiyat fallback olaraq YouTube
                SEARCH_STATS["fallback"] += 1
                logger.info(f"YT Music-də heç bir nəticə tapılmadı, YouTube göstərilir: {query!r}")
                try:
                    return await self._yt_search(query, limit, fast)
                except Exception as e:
                    logger.warning(f"YouTube ehtiyat axtarışı da alınmadı: {e}")
                    return []

            # both — paralel; növbə ilə qarışdırılır (🎵, ▶️, 🎵, ▶️ ...), eyni video bir dəfə
            ytm_res, yt_res = await asyncio.gather(
                self._ytm_search(query, limit, fast), self._yt_search(query, limit, fast), return_exceptions=True)
            if isinstance(ytm_res, Exception):
                logger.warning(f"YouTube Music axtarışı alınmadı: {ytm_res}")
                ytm_res = []
            if isinstance(yt_res, Exception):
                if not ytm_res:
                    raise yt_res
                logger.warning(f"YouTube axtarışı alınmadı: {yt_res}")
                yt_res = []
            SEARCH_STATS["ytm" if ytm_res else "fallback"] += 1
            merged, seen = [], set()
            for i in range(max(len(ytm_res), len(yt_res))):
                for lst in (ytm_res, yt_res):
                    if i >= len(lst):
                        continue
                    r = lst[i]
                    m = _VID_RE.search(r.get('url') or '')
                    key = m.group(1) if m else r.get('url')
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(r)
            return merged[:limit]
        except Exception as e:
            logger.error(f"Axtarış xətası ({source}): {e}")
            raise RuntimeError(f"Axtarış xətası: {str(e)}")

    async def playlist_entries(self, url: str, limit: int = 5000) -> dict:
        """
        İstənilən YouTube / YouTube Music siyahısı: playlist, albom (OLAK5uy_), Mix/Radio (RD...).
        Nəticə: {"title": ..., "entries": [{title, url, duration, raw_duration}]}
        """
        opts = self.get_ydl_opts()
        opts['playlistend'] = limit
        opts['noplaylist'] = False
        try:
            info = await cpu_pool.ytdl_extract(url, opts, timeout=600 if opts.get('playlistend', 0) > 500 else 180)
        except Exception as e:
            logger.error(f"Playlist xətası: {e}")
            raise RuntimeError(f"Playlist açılmadı: {str(e)}")

        entries, seen = [], set()
        for entry in (info.get('entries') or []):
            if not entry:
                continue
            vid = entry.get('id')
            if not vid or vid in seen or len(vid) != 11:
                continue
            seen.add(vid)
            duration = entry.get('duration') or 0
            entries.append({
                'title': entry.get('title') or 'Naməlum Mahnı',
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
            })
        return {"title": info.get('title') or "YouTube siyahısı", "entries": entries}

    async def youtube_mix(self, video_id: str, limit: int = 25, fast: bool = False) -> list:
        """
        YouTube "Mix" (RD radio playlist) — verilən mahnıya oxşar mahnıları qaytarır.
        İlk element mahnının özüdür, ona görə süzülür.
        """
        url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        opts = self.fast_opts() if fast else self.get_ydl_opts()
        opts['playlistend'] = limit + 10
        opts['noplaylist'] = False                       # Mix — playlist-dir
        try:
            if fast:                                     # ⚡ növbəsiz, cookies/JS-siz (flat siyahı)
                info = await cpu_pool.fast_extract(url, opts, timeout=max(15.0, cpu_pool._fast_timeout() * 2))
            else:
                info = await cpu_pool.ytdl_extract(url, opts, timeout=600 if opts.get('playlistend', 0) > 500 else 180)
        except Exception as e:
            logger.error(f"Mix xətası: {e}")
            raise RuntimeError(f"Mix xətası: {str(e)}")

        results, seen = [], {video_id}
        for entry in (info.get('entries') or []):
            if not entry:
                continue
            vid = entry.get('id')
            if not vid or vid in seen:
                continue
            seen.add(vid)
            duration = entry.get('duration') or 0
            results.append({
                'title': entry.get('title') or 'Naməlum Mahnı',
                'url': f"https://www.youtube.com/watch?v={vid}",
                'duration': format_duration(duration),
                'raw_duration': duration,
            })
            if len(results) >= limit:
                break
        return results

    async def download_track(self, query: str, base_name: str, cancel_event=None, on_progress=None) -> str:
        try:
            return await download_as_m4a(
                query, base_name, self.browser,
                cancel_event=cancel_event, on_progress=on_progress,
            )
        except DownloadCancelled:
            raise
        except Exception as e:
            logger.debug(f"Yükləmə xətası: {e}")          # download_as_m4a artıq səbəbi ilə yazıb
            raise RuntimeError(f"Yükləmə xətası: {str(e)}")
