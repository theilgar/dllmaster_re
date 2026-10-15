"""
🌐 Uzaq server — bot/core/remote.py

Bot (menyu, baza, ffmpeg, Shazam...) evdə qalır; yükləmə (yt-dlp) və depoya upload istəsəniz uzaq serverdə görülür.
Nəqliyyat — adi SSH (əlavə proqram / port yoxdur): serverdə yalnız bot/core/ytdl_worker.py işləyir və evdəki işçi
proseslə eyni protokolla (stdin/stdout) danışır → cpu_pool.RemoteProcPool.

İki sual, hər biri üçün üç cavab:
  ⬇️ Yükləmə / ⬆️ Upload harada getsin?   🏠 ev · 🌐 server · 🏠+🌐 ikisi (server boşdursa orada, dolu olanda evdə)
  🛟 Server xətası olsa nə olsun?          ✋ Manual (standart): iş evə keçmir, gözləyir, creator-a düymələrlə bildiriş gedir
                                           🤖 Avto: iş dərhal evdə görülür

Necə işləyir:
  ⬇️ Yükləmə — yt-dlp serverdə işləyir. Nəticə:
       • ev fayla toxunacaqsa (ffmpeg, istifadəçiyə göndərmə) — fayl ssh ilə evə çəkilir;
       • 📦 depo zolağında hazır .m4a-dırsa — fayl serverdə QALIR, evdə boş yer-tutan fayl yaranır (PENDING).
  ⬆️ Upload — PENDING fayl serverdən birbaşa Telegram-a yüklənir (ev internetini işlətmir).
  Server üçün uyğun olmayan işlər (brauzer cookies-i, localhost proxy) həmişə evdə görülür — bu xəta deyil.

Bölmələr: ayarlar → nəqliyyat (ssh) → quraşdırma / yoxlama → monitor izi → cookies → yükləmə → upload →
xəta qapısı (Manual) → try_download / try_upload (cpu_pool və audio_cache bunları çağırır) → vəziyyət.
Ayarlar bazada (remote:*). Menyu: /menu → 🖥 Sistem → ⚙️ Performans → 🌐 Uzaq server (bot/menus/views/remote_server.py).
Sınaq üçün DLM_REMOTE_TRANSPORT=local — ssh əvəzinə yerli bash (server = bu maşın, iş qovluğu DLM_REMOTE_DIR).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import tempfile
import time
import uuid

from common.database import Settings

logger = logging.getLogger(__name__)

_S = Settings("remote:", empty_is_default=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKER_SRC = os.path.join(ROOT, "bot", "core", "ytdl_worker.py")
DEFAULT_DIR = "dlm_remote"
MAX_PROCS = 16                        # paralel yükləmə / upload yuxarı həddi (hər biri ayrı proses)
DOWN_FOR = 60                         # server xətasından sonra bu qədər saniyə evdə işlənir
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:\-\[\]]*$")
_IP_HINTS = ("sign in to confirm", "not a bot", "http error 403", "http error 429", "too many requests",
             "this content isn't available, try again later")        # serverin IP-si ilə bağlı rədd
_INFRA_HINTS = ("no module named", "command not found", "no such file or directory: 'yt", "permission denied",
                "cannot allocate memory", "no space left")


class RemoteUnavailable(Exception):
    """Server əlçatmazdır / işçi başlamır — iş evdə görülməlidir (sakit fallback)."""


class RemoteLocalOnly(RemoteUnavailable):
    """Bu tapşırıq serverdə görülə bilməz (məs. brauzer cookies) — server xəstə deyil, yalnız bu iş evdə görülür."""


class RemoteRetryHome(RemoteLocalOnly):
    """Server IP-si YouTube tərəfindən rədd edildi (bot yoxlaması / 403 / 429) — bu iş evdə təkrar olunur."""


class RemoteUploadError(Exception):
    """Telegram serverdən yükləməni rədd etdi (flood, icazə...) — mətn olduğu kimi saxlanır."""


# ───────────────────────── ayarlar ─────────────────────────
MODES = ("home", "server", "both")
MODE_LABEL = {"home": "🏠 Ev", "server": "🌐 Server", "both": "🏠+🌐 İkisi"}
MAX_DEPO_SLOTS, MAX_USER_SLOTS = 16, 4
PROC_MB = 100                          # bir server prosesinin yük altında təxmini RAM-ı


def enabled() -> bool:
    return _S.flag("on")


def set_enabled(on: bool) -> bool:
    """Ana açar. Söndürüləndə hər şey evdə görülür — server üçün gözləyən işlər də dərhal evə buraxılır."""
    _S.put("on", "1" if on else "0")
    mark_up()
    if not on:
        resolve_gate("home")
    return on


def host() -> str:
    h = (_S.get("host", "") or "").strip()
    return h if HOST_RE.match(h) else ""


def port() -> int:
    return _S.int("port", 22, lo=1, hi=65535)


def key() -> str:
    return (_S.get("key", "") or "").strip()


def rdir() -> str:
    return os.getenv("DLM_REMOTE_DIR") or (_S.get("dir", DEFAULT_DIR) or DEFAULT_DIR)


def py() -> str:
    return _S.get("py", "python3") or "python3"


def _local_transport() -> bool:
    return os.getenv("DLM_REMOTE_TRANSPORT") == "local"


def configured() -> bool:
    return bool(host()) or _local_transport()


def mode(kind: str) -> str:
    """kind: 'download' | 'upload' → 'home' | 'server' | 'both'."""
    v = _S.get(kind + "_mode")
    if v in MODES:
        return v
    return "server" if _S.flag(kind, "1") else "home"          # köhnə aç/bağla ayarından


def set_mode(kind: str, value: str) -> str:
    if kind not in ("download", "upload") or value not in MODES:
        raise ValueError(f"{kind}/{value}")
    _S.put(kind + "_mode", value)
    return value


def dl_mode() -> str:
    return mode("download")


def up_mode() -> str:
    return mode("upload")


def auto_fallback() -> bool:
    """🤖 Avto: server xətasında iş dərhal evdə görülür. ✋ Manual (standart): iş gözləyir, creator-a bildiriş gedir."""
    return _S.flag("auto", "0")


def set_auto_fallback(on: bool) -> bool:
    _S.put("auto", "1" if on else "0")
    return on


def depo_slots() -> int:
    """📦 Depo: serverdə eyni anda neçə yükləmə (və eyni sayda upload)."""
    return _S.int("procs", 2, lo=1, hi=MAX_DEPO_SLOTS)


def user_slots() -> int:
    """👤 İstifadəçi: ayrıca slotlar — depo növbəsi istifadəçini gözlətməsin."""
    return _S.int("user_procs", 2, lo=1, hi=MAX_USER_SLOTS)


def set_slots(lane: str, n: int) -> int:
    if lane == "depo":
        n = max(1, min(MAX_DEPO_SLOTS, int(n)))
        _S.put("procs", n)
    elif lane == "user":
        n = max(1, min(MAX_USER_SLOTS, int(n)))
        _S.put("user_procs", n)
    else:
        raise ValueError(lane)
    return n


def lane_limit(lane: str) -> int:
    return user_slots() if lane == "user" else depo_slots()


def worst_case_procs() -> int:
    """Eyni anda ən çox server prosesi: depo yükləmələri + depo uploadları + istifadəçi."""
    return depo_slots() * 2 + user_slots()


def slot_advice() -> dict:
    """Slot sayı serverin RAM-ına uyğundurmu (son uğurlu yoxlamadakı RAM ilə)."""
    ram = (last_check.get("info") or {}).get("ram_mb")
    total = worst_case_procs()
    out = {"total": total, "need_mb": total * PROC_MB, "ram_mb": ram, "rec_depo": None, "warn": False}
    if ram:
        budget = max(0.0, (ram - 500) * 0.7)                   # sistem və SSH üçün ~500 MB, qalanın 70%-i
        out["rec_depo"] = max(1, min(MAX_DEPO_SLOTS, int((budget / PROC_MB - user_slots()) // 2)))
        out["warn"] = total * PROC_MB > budget
    return out


def server_selected(kind: str) -> bool:
    """Ayarlarda server seçilib (serverin hazırda sağlam olub-olmamasından asılı olmayaraq)."""
    return enabled() and configured() and mode(kind) != "home"


def set_conn(name: str, value) -> str:
    """Bağlantı ayarları (host / port / key / dir). Yoxlanılır; səhv dəyərdə ValueError."""
    v = str(value).strip()
    if name == "host":
        if v and not HOST_RE.match(v):
            raise ValueError("Ünvan belə olmalıdır: istifadəçi@ip  (məs. root@203.0.113.5)")
    elif name == "port":
        if not v.isdigit() or not 1 <= int(v) <= 65535:
            raise ValueError("Port 1–65535 arası rəqəm olmalıdır")
    elif name == "key":
        if v.endswith(".pub"):
            raise ValueError("Bu açıq açardır (.pub). Gizli açarı yazın — eyni ad, .pub-suz (məs. ~/.ssh/dlm_vps)")
        if v and not os.path.isfile(os.path.expanduser(v)):
            raise ValueError("Bu yolda açar faylı tapılmadı (bot işləyən maşında olmalıdır)")
        v = os.path.expanduser(v) if v else v
    elif name == "dir":
        if v and (v.startswith("-") or ".." in v or "\n" in v):
            raise ValueError("Qovluq adı yararsızdır")
    else:
        raise ValueError(name)
    _S.put(name, v)
    mark_up()
    return v


# ── server sağlamlığı ──
_inflight = {}                                    # (iş, zolaq) → serverə göndərilmiş, hələ bitməmiş işlər
stats = {"fallback": 0, "ip_retry": 0}            # fallback — server alınmadı, iş evdə görüldü; ip_retry — IP rədd
_down = {"until": 0.0, "error": "", "at": 0.0}
last_check = {"at": 0.0, "ok": False, "error": "", "info": {}}      # son 🧪 yoxlamanın nəticəsi (menyuda göstərilir)


def available() -> bool:
    return enabled() and configured() and time.time() >= _down["until"]


def upload_on() -> bool:
    """Serverdə qalmış fayl serverdən göndərilə bilərmi (yükləmə də serverdədir və server sağlamdır)."""
    return available() and mode("download") != "home" and mode("upload") != "home"


def mark_down(err: str, secs: int = DOWN_FOR):
    _down.update(until=time.time() + secs, error=str(err)[:200], at=time.time())
    logger.warning(f"🌐 Uzaq server dayanıb sayıldı ({secs} san.): {str(err)[:200]} — "
                   + ("iş evdə görülür" if auto_fallback() else "Manual rejim: iş gözləyir, bildiriş göndərilir"))


def mark_up():
    _down["until"] = 0.0


def down_info() -> tuple[float, str]:
    left = _down["until"] - time.time()
    return (left if left > 0 else 0.0), _down["error"]


def claim(kind: str, lane: str = "depo") -> bool:
    """İş serverdə görülsün? 'server' — həmişə; 'ikisi' — yalnız zolağın serverdə boş yeri varsa (qalan iş evdə).
    True qaytarsa çağıran finally-də release(kind, lane) etməlidir."""
    m = mode(kind)
    k = (kind, lane)
    if m == "home" or (m == "both" and _inflight.get(k, 0) >= lane_limit(lane)):
        return False
    _inflight[k] = _inflight.get(k, 0) + 1
    return True


def release(kind: str, lane: str = "depo"):
    k = (kind, lane)
    _inflight[k] = max(0, _inflight.get(k, 0) - 1)


# ───────────────────────── nəqliyyat ─────────────────────────
# sshd standart olaraq bir bağlantıda cəmi 10 sessiyaya icazə verir (MaxSessions). Qısa əmrlər ortaq (multiplex) bağlantıdan
# keçir və burada saxlanılan say həddindən aşağıdır; uzunömürlü işçilər isə hər biri ÖZ bağlantısı ilə açılır.
SHORT_SESSIONS = 4                    # eyni anda qısa əmr (yoxlama, proqres, fayl yazmaq / silmək)
PULL_SESSIONS = 3                     # eyni anda faylı evə çəkmə
SPAWN_PARALLEL = 3                    # eyni anda yeni işçi bağlantısı (sshd MaxStartups: çoxlu eyni vaxtlı qoşulma rədd olunur)
_sems = {}


def _sem(name: str, n: int) -> asyncio.Semaphore:
    key = (name, id(asyncio.get_running_loop()))
    if key not in _sems:
        _sems[key] = asyncio.Semaphore(n)
    return _sems[key]


def spawn_slot():
    """async with remote.spawn_slot(): — işçi bağlantıları bir-bir (üçlü qruplarla) açılsın."""
    return _sem("spawn", SPAWN_PARALLEL)


def _argv(cmd: str, mux: bool = True) -> list:
    if _local_transport():
        return ["bash", "-c", cmd]
    share = (["-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
              "-o", f"ControlPath={os.path.join(tempfile.gettempdir(), 'dlm-ssh-%C')}"] if mux
             else ["-o", "ControlMaster=no", "-o", "ControlPath=none"])
    a = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
         "-o", "StrictHostKeyChecking=accept-new", *share,
         "-p", str(port())]
    if key():
        a += ["-i", key()]
    return a + ["--", host(), cmd]


def _in_dir(cmd: str) -> str:
    d = shlex.quote(rdir())
    return f"mkdir -p {d} && cd {d} && {cmd}"


def worker_argv(nice: int = 0) -> list:
    return _argv(_in_dir(f"DLM_REMOTE_WORKER=1 PYTHONUNBUFFERED=1 exec {shlex.quote(py())} ytdl_worker.py {int(nice)}"),
                 mux=False)                                          # işçi öz bağlantısı ilə: ortaq bağlantının sessiya limitini tutmur


async def run(cmd: str, timeout: float = 30, stdin_data: bytes = None, in_dir: bool = True) -> tuple[int, bytes, str]:
    """Serverdə əmr işlədir → (kod, stdout, stderr). SSH xətası / vaxt aşımı — RemoteUnavailable."""
    if not configured():
        raise RemoteUnavailable("Server ünvanı təyin edilməyib")
    argv = _argv(_in_dir(cmd) if in_dir else cmd)
    async with _sem("short", SHORT_SESSIONS):
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise RemoteUnavailable(f"ssh başlamadı: {e}")
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_data), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RemoteUnavailable(f"server cavab vermədi ({timeout:.0f} san.)")
        except asyncio.CancelledError:
            proc.kill()
            raise
    err_s = err.decode("utf-8", "replace").strip()
    if proc.returncode == 255 and not _local_transport():
        raise RemoteUnavailable(f"ssh qoşulmadı: {err_s[-200:] or 'naməlum'}")
    return proc.returncode, out, err_s


async def push_bytes(data: bytes, rel: str, timeout: float = 60):
    q = shlex.quote(rel)
    tmp = shlex.quote(f"{rel}.{uuid.uuid4().hex[:8]}.part")      # unikal ad: paralel yazılar bir-birinin faylını götürmür
    d = shlex.quote(os.path.dirname(rel) or ".")
    rc, _, err = await run(f"mkdir -p {d} && cat > {tmp} && mv -f {tmp} {q}", timeout, data)
    if rc:
        raise RemoteUnavailable(f"fayl serverə yazılmadı: {err[-150:]}")


async def remote_size(rel: str) -> int:
    rc, out, _ = await run(f"wc -c < {shlex.quote(rel)}", 20)
    try:
        return int(out.strip()) if rc == 0 else -1
    except ValueError:
        return -1


async def pull_file(rel: str, local: str, expect: int = -1, timeout: float = 1200):
    """Serverdəki faylı ssh ilə evə çəkir (yarımçıq fayl qalmasın deyə .part → rename; ölçü yoxlanılır)."""
    if not configured():
        raise RemoteUnavailable("Server ünvanı təyin edilməyib")
    os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
    async with _sem("pull", PULL_SESSIONS):
        await _pull(rel, local, expect, timeout)


async def _pull(rel: str, local: str, expect: int, timeout: float):
    part = local + ".rpart"
    proc = await asyncio.create_subprocess_exec(
        *_argv(_in_dir(f"cat -- {shlex.quote(rel)}")), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 20)

    async def copy():
        n = 0
        with open(part, "wb") as f:
            while True:
                chunk = await proc.stdout.read(1 << 20)
                if not chunk:
                    break
                await asyncio.to_thread(f.write, chunk)
                n += len(chunk)
        await proc.wait()
        return n

    try:
        n = await asyncio.wait_for(copy(), timeout)
    except BaseException:
        proc.kill()
        _silent_rm(part)
        raise
    if proc.returncode != 0 or (expect >= 0 and n != expect):
        _silent_rm(part)
        err = (await proc.stderr.read()).decode("utf-8", "replace").strip()
        raise RemoteUnavailable(f"fayl evə çəkilmədi (kod {proc.returncode}, {n}/{expect} bayt) {err[-120:]}")
    os.replace(part, local)


def _silent_rm(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


async def rm(*rels: str):
    if not rels:
        return
    try:
        await run("rm -f -- " + " ".join(shlex.quote(r) for r in rels), 20)
    except RemoteUnavailable as e:
        logger.debug(f"🌐 uzaq fayl silinmədi: {e}")


async def touch(rel: str):
    try:
        await run(f"touch {shlex.quote(rel)}", 15)
    except RemoteUnavailable as e:
        logger.debug(f"🌐 uzaq ləğv bayrağı yazılmadı: {e}")


# ───────────────────────── işçi faylı / quraşdırma ─────────────────────────
_synced = {"hash": None}
_sync_lock = None                      # eyni anda bir sinxronizasiya (16 işçi birlikdə açılanda təkrar göndərməsin)


def _worker_bytes() -> bytes:
    with open(WORKER_SRC, "rb") as f:
        return f.read()


async def ensure_synced(force: bool = False):
    """ytdl_worker.py serverdə evdəkinin eynisi olsun (bot yeniləndikdə avtomatik; bot başına bir dəfə)."""
    global _sync_lock
    data = _worker_bytes()
    h = hashlib.sha256(data).hexdigest()
    if not force and _synced["hash"] == h:
        return
    if _sync_lock is None:
        _sync_lock = asyncio.Lock()
    async with _sync_lock:
        if not force and _synced["hash"] == h:                 # gözləyərkən başqası artıq göndərib
            return
        await push_bytes(data, "ytdl_worker.py")
        _synced["hash"] = h


async def check() -> dict:
    """Test: ssh + işçi + paketlər. → {"ok", "error", "info": {...}}"""
    res = {"ok": False, "error": "", "info": {}}
    try:
        rc, out, err = await run("echo ok", 20)
        if rc or b"ok" not in out:
            res["error"] = f"ssh cavabı gözlənilməz: {err[-120:]}"
            return res
        rc, out, err = await run("test -f ytdl_worker.py", 15)
        if rc:
            res["error"] = "Server qurulmayıb — «🛠 Qur / yenilə» düyməsinə basın"
            return res
        await ensure_synced()
        code = "import json,sys;sys.path.insert(0,'.');import ytdl_worker as w;print(json.dumps(w.diag()))"
        rc, out, err = await run(f"{shlex.quote(py())} -c {shlex.quote(code)}", 40)
        if rc:
            res["error"] = f"Python işləmədi: {err[-150:]}"
            return res
        info = json.loads(out.decode().strip().splitlines()[-1])
        res["info"] = info
        if not info.get("yt_dlp"):
            res["error"] = "yt-dlp serverdə yoxdur — «🛠 Qur / yenilə»"
        elif not info.get("aiogram") and mode("upload") != "home":
            res["error"] = "aiogram serverdə yoxdur (upload üçün) — «🛠 Qur / yenilə»"
        else:
            res["ok"] = True
    except RemoteUnavailable as e:
        res["error"] = str(e)
    except (ValueError, IndexError) as e:
        res["error"] = f"diaqnostika oxunmadı: {e}"
    last_check.update(at=time.time(), ok=res["ok"], error=res["error"], info=res["info"] or last_check.get("info", {}))
    if res["ok"]:
        mark_up()
    return res


async def setup() -> list:
    """Serveri hazırlayır (təkrar işlədilə bilər): qovluq, işçi faylı, venv, yt-dlp + aiogram. → addım hesabatı."""
    log = []
    rc, out, err = await run("mkdir -p dl cookies && command -v python3 && python3 --version", 30)
    if rc:
        return ["❌ Serverdə python3 yoxdur — əvvəl quraşdırın (məs. sudo apt install python3 python3-venv)"]
    log.append("✅ python3: " + out.decode().strip().splitlines()[-1])
    await ensure_synced(force=True)
    log.append("✅ ytdl_worker.py köçürüldü")
    rc, _, err = await run("test -x venv/bin/python || python3 -m venv venv", 120)
    if rc:
        return log + [f"❌ venv yaranmadı: {err[-200:]}", "   → sudo apt install python3-venv"]
    log.append("✅ venv hazırdır")
    rc, _, err = await run("venv/bin/pip install -q -U 'yt-dlp[default]' aiogram", 900)
    if rc:
        return log + [f"❌ pip xətası: {err[-300:]}"]
    log.append("✅ yt-dlp + aiogram quruldu")
    _S.put("py", "venv/bin/python")
    res = await check()
    info = res["info"]
    if info:
        log.append(f"ℹ️ RAM {info.get('ram_mb')} MB · boş disk {info.get('free_mb')} MB · yt-dlp {info.get('yt_dlp')}")
        if not info.get("js"):
            log.append("⚠️ deno/node tapılmadı — YouTube üçün lazımdır: sudo apt install nodejs  (və ya deno)")
        if not info.get("ffmpeg"):
            log.append("ℹ️ ffmpeg yoxdur — normaldır: çevirmə evdə görülür (yalnız hazır m4a serverdə qalır)")
    log.append("✅ Hazırdır" if res["ok"] else f"❌ {res['error']}")
    return log


# ───────────────────────── 📊 depo monitoru üçün iz ─────────────────────────
counts = {"dl_S": 0, "dl_E": 0, "up_S": 0, "up_E": 0}      # S — server, E — ev (cəhd / göndərmə sayı)


def mark(kind: str, where: str):
    """Depo doldurucunun cari işçisi üçün qeyd: kind 'dl'|'up', where 'S' (server) | 'E' (ev).
    Monitorda işçi sətrində 'YER' sütunu və ümumi say kimi görünür. Doldurucu konteksti yoxdursa — yalnız say."""
    counts[f"{kind}_{where}"] += 1
    try:
        from bot.core.depo_helpers import UPLOAD_CTX
        ctx = UPLOAD_CTX.get()
        if ctx:
            filler, idx = ctx
            st = filler.wstate.get(idx)
            if st is not None:
                st.setdefault("via", {})[kind] = where
    except Exception as e:
        logger.debug("remote.mark xətası: %r", e)


def via_text(st: dict) -> str:
    v = (st or {}).get("via") or {}
    return f"{v.get('dl', '-')}/{v.get('up', '-')}"


# ───────────────────────── yükləmə ─────────────────────────
_cookie_cache = {}                     # yerli yol → (mtime, ölçü, uzaq yol)
PENDING = {}                           # yerli yer-tutan fayl (abspath) → (uzaq yol, ölçü): fayl hələ serverdədir
_sizes = {}                            # yer-tutanın əsl ölçüsü (göndərildikdən sonra da) — statistikada 0 bayt görünməsin


def _loopback(url: str) -> bool:
    return bool(re.search(r"//(?:[^@/]*@)?(?:localhost|127\.|\[?::1)", url or ""))


# ── 🍪 brauzer cookies-i serverdə: yalnız YouTube / Google domenləri, hesab üçün icazə verilibsə ──
COOKIE_TTL = 30 * 60                   # ixrac olunmuş fayl bu qədər saniyə təzə sayılır
COOKIE_DIR = os.path.join(ROOT, "data", "remote_cookies")
_cookie_locks = {}


def acc_allowed(acc_id: str) -> bool:
    return _S.flag(f"acc_{acc_id}", "0")


def toggle_acc(acc_id: str) -> bool:
    return _S.toggle(f"acc_{acc_id}", "0")


def _account_for(spec) -> dict | None:
    """yt-dlp 'cookiesfrombrowser' dəyərinə uyğun YouTube hesabı (hesab açarını tapmaq üçün)."""
    from bot.core import yt_accounts
    for acc in yt_accounts.accounts():
        try:
            if tuple(yt_accounts.opts(acc).get("cookiesfrombrowser") or ()) == tuple(spec):
                return acc
        except Exception as e:
            logger.debug("hesab uyğunlaşdırması: %r", e)
    return None


def _youtube_only(jar):
    """Cookie qabından yalnız youtube / google olanlar (başqa saytların cookies-i serverə getmir) → (yeni qab, say)."""
    from yt_dlp.cookies import YoutubeDLCookieJar
    out, n = YoutubeDLCookieJar(), 0
    for c in jar:
        d = (c.domain or "").lower()
        if "youtube" in d or "google" in d:
            out.set_cookie(c)
            n += 1
    return out, n


def _export_cookies(spec, path: str) -> int:
    """Brauzer cookies-indən YouTube / Google olanları fayla yazır (rejim 600) → cookie sayı."""
    from yt_dlp import YoutubeDL
    with YoutubeDL({"cookiesfrombrowser": tuple(spec), "quiet": True, "no_warnings": True}) as ydl:
        out, n = _youtube_only(ydl.cookiejar)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.save(path, ignore_discard=True, ignore_expires=True)
    os.chmod(path, 0o600)
    return n


async def _browser_cookie_file(spec) -> str:
    acc = _account_for(spec)
    if acc is None or not acc_allowed(acc["id"]):
        raise RemoteLocalOnly("hesab serverdə istifadə üçün açılmayıb" if acc else "hesab tapılmadı")
    path = os.path.join(COOKIE_DIR, f"{acc['id']}.txt")
    lock = _cookie_locks.setdefault(acc["id"], asyncio.Lock())
    async with lock:
        fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < COOKIE_TTL
        if not fresh:
            try:
                n = await asyncio.to_thread(_export_cookies, spec, path)
            except Exception as e:
                raise RemoteLocalOnly(f"cookies ixrac olunmadı: {type(e).__name__}: {str(e)[:100]}")
            if not n:
                raise RemoteLocalOnly("brauzerdə YouTube cookies-i tapılmadı")
            logger.info(f"🍪 {acc['name']}: {n} cookie server üçün ixrac olundu")
    return path


async def prepare_opts(opts: dict) -> tuple[dict, str]:
    """Yerli yt-dlp opsiyalarını serverə uyğunlaşdırır → (opsiyalar, yerli qovluq). Alınmasa RemoteLocalOnly."""
    o = dict(opts)
    if o.get("cookiesfrombrowser"):
        o["cookiefile"] = await _browser_cookie_file(o.pop("cookiesfrombrowser"))     # → fayl, aşağıda serverə göndərilir
    if o.get("proxy") and _loopback(str(o["proxy"])):
        raise RemoteLocalOnly("proxy evin localhost-undadır")
    tmpl = o.get("outtmpl")
    if not isinstance(tmpl, str):
        raise RemoteLocalOnly("outtmpl xüsusi formatdadır")
    local_dir = os.path.dirname(tmpl) or "."
    o["outtmpl"] = "dl/" + os.path.basename(tmpl)
    for k in ("ffmpeg_location", "cachedir", "paths", "download_archive"):
        o.pop(k, None)
    if isinstance(o.get("js_runtimes"), dict):                 # yerli yollar serverdə yoxdur → yalnız adlar
        o["js_runtimes"] = {n: {} for n in o["js_runtimes"]}
    cf = o.get("cookiefile")
    if cf:
        try:
            st = os.stat(cf)
        except OSError:
            raise RemoteLocalOnly("cookies faylı tapılmadı")
        cached = _cookie_cache.get(cf)
        if not cached or cached[:2] != (st.st_mtime, st.st_size):
            rel = "cookies/" + hashlib.sha1(cf.encode()).hexdigest()[:16] + ".txt"
            with open(cf, "rb") as f:
                await push_bytes(f.read(), rel)
            cached = _cookie_cache[cf] = (st.st_mtime, st.st_size, rel)
        o["cookiefile"] = cached[2]
    return o, local_dir


async def _mirror_progress(rel_prog: str, local_prog: str):
    """Serverdəki irəliləyiş faylını evdəki .prog-a köçürür → cpu_pool-un mövcud poller-i göstərir."""
    last = None
    while True:
        await asyncio.sleep(3.0)                              # çoxlu yükləmə: hər biri ortaq bağlantıya sorğu göndərir
        try:
            rc, out, _ = await run(f"cat {shlex.quote(rel_prog)} 2>/dev/null", 10)
        except RemoteUnavailable:
            continue
        if rc or not out or out == last:
            continue
        last = out
        try:
            tmp = local_prog + ".t"
            with open(tmp, "wb") as f:
                f.write(out)
            os.replace(tmp, local_prog)
        except OSError:
            pass


async def _push_cancel(event, rel_cancel: str):
    while not event.is_set():
        await asyncio.sleep(0.3)
    await touch(rel_cancel)


def _is_infra(msg: str) -> bool:
    m = (msg or "").lower()
    return any(h in m for h in _INFRA_HINTS)


def _sync_pool(pool, n: int = None):
    n = n or depo_slots()
    if pool.size != n:
        pool.size = pool.max_size = n
        pool.kick()


async def download(args: tuple, timeout: float, cancel_event, local_cancel_path: str, lane: str) -> dict:
    """ytdl_download-un uzaq variantı → {"path", "info"} (cpu_pool._run ilə eyni nəticə). Alınmasa RemoteUnavailable."""
    from bot.core import cpu_pool as cp
    url, opts = args[0], args[1]
    ropts, local_dir = await prepare_opts(opts)
    pool = cp.REMOTE_LANES.get(lane, cp.REMOTE_LANES["depo"])      # 👤 / 📦 ayrı slotlar
    _sync_pool(pool, lane_limit(lane))
    rid = uuid.uuid4().hex[:10]
    rcancel = f"dl/.c_{rid}"
    mirror = asyncio.ensure_future(_mirror_progress(rcancel + ".prog", local_cancel_path + ".prog")) \
        if local_cancel_path else None
    canceller = asyncio.ensure_future(_push_cancel(cancel_event, rcancel)) if cancel_event is not None else None
    try:
        res = await pool.call("ytdl_download", (url, ropts, rcancel), timeout, cancel_event, None)
    except cp.WorkerCrashed as e:
        mark_down(e)
        raise RemoteUnavailable(str(e))
    except cp.WorkerError as e:
        if _is_infra(str(e)):
            mark_down(e)
            raise RemoteUnavailable(str(e))
        if any(h in str(e).lower() for h in _IP_HINTS):
            stats["ip_retry"] += 1
            rh = RemoteRetryHome(f"serverin IP-si rədd edildi: {str(e)[:90]}")
            rh.original = e                                   # ✋ Manual rejimdə evə keçmir — əsl xəta yuxarı qalxır
            raise rh
        raise
    finally:
        for t in (mirror, canceller):
            if t:
                t.cancel()
        asyncio.ensure_future(rm(rcancel, rcancel + ".prog", rcancel + ".prog.t"))
    mark_up()
    rpath = res["path"]
    lpath = os.path.join(local_dir, os.path.basename(rpath))
    size = await remote_size(rpath)
    if size < 0:
        raise RemoteUnavailable(f"yüklənən fayl serverdə tapılmadı: {rpath}")
    if lane == "depo" and upload_on() and rpath.lower().endswith(".m4a"):
        os.makedirs(local_dir, exist_ok=True)
        open(lpath, "wb").close()                          # yer-tutan: hələ serverdədir (upload oradan gedəcək)
        PENDING[os.path.abspath(lpath)] = (rpath, size)
        _sizes[os.path.abspath(lpath)] = size
        while len(_sizes) > 2000:                          # yaddaş sızmasın: ən köhnələr atılır
            _sizes.pop(next(iter(_sizes)))
        return {"path": lpath, "info": res["info"]}
    for attempt in (1, 2):
        try:
            await pull_file(rpath, lpath, size)
            break
        except RemoteUnavailable:
            if attempt == 2:
                raise
    await rm(rpath)
    return {"path": lpath, "info": res["info"]}


# ───────────────────────── upload ─────────────────────────
def file_size(path) -> int:
    """Faylın ölçüsü; serverdə qalmış (yer-tutan) fayl üçün əsl ölçü. Fayl yoxdursa OSError (os.path.getsize kimi)."""
    n = os.path.getsize(path)
    return n or _sizes.get(os.path.abspath(path), 0)


def pending(path) -> bool:
    return isinstance(path, str) and os.path.abspath(path) in PENDING


async def materialize(path: str) -> bool:
    """Fayl hələ serverdədirsə — evə çəkir (yer-tutanın yerinə). Çəkilməsə yer-tutan silinir, False."""
    if not isinstance(path, str):
        return True
    ap = os.path.abspath(path)
    ent = PENDING.get(ap)
    if not ent:
        return True
    rpath, size = ent
    try:
        await pull_file(rpath, ap, size)
    except RemoteUnavailable as e:
        logger.warning(f"🌐 Fayl serverdən çəkilmədi: {e}")
        _silent_rm(ap)
        PENDING.pop(ap, None)
        return False
    PENDING.pop(ap, None)
    await rm(rpath)
    return True


async def discard(path: str):
    """Fayl artıq lazım deyil (göndərildi / ləğv) — serverdəki nüsxə silinir. Evdəki yer-tutanı çağıran silir."""
    ent = PENDING.pop(os.path.abspath(path), None)
    if ent:
        await rm(ent[0])


async def upload_audio(path: str, token: str, chat_id: int, thread_id, filename: str, title: str, performer: str,
                       duration: int, caption: str, thumb: bytes = None):
    """Serverdəki faylı serverdən Telegram-a yükləyir → aiogram Message. Telegram xətası — RemoteUploadError,
    server xətası — RemoteUnavailable (çağıran evdə davam edir)."""
    from aiogram.types import Message
    from bot.core import cpu_pool as cp
    rpath, _ = PENDING[os.path.abspath(path)]
    pool = cp.REMOTE_UP
    _sync_pool(pool)
    try:
        d = await pool.call("upload_audio", (token, chat_id, thread_id, rpath, filename, title, performer,
                                             duration, caption, thumb), 900)
    except cp.WorkerCrashed as e:
        mark_down(e)
        raise RemoteUnavailable(str(e))
    except cp.WorkerError as e:
        if _is_infra(str(e)) or "uzaq fayl tapılmadı" in str(e):
            mark_down(e)
            raise RemoteUnavailable(str(e))
        raise RemoteUploadError(str(e))
    mark_up()
    return Message.model_validate(d)


# ───────────────────────── ✋ Manual rejim: server xətasında qərar qapısı ─────────────────────────
GATE_TIMEOUT = 900                     # gözləyən iş bu qədər saniyədən sonra xəta ilə bitir (depo təkrar növbəsinə düşür)
PROBE_EVERY = 20                       # server bərpa olunubmu — bu intervalla yoxlanır
_gate = {"ev": None, "decision": None, "waiting": 0, "notified_at": 0.0, "msg": None}


def waiting_count() -> int:
    return _gate["waiting"]


def resolve_gate(decision: str):
    """Gözləyən işləri azad edir: 'retry' — yenidən serverə, 'home' — evdə işlət."""
    ev = _gate["ev"]
    if ev is not None and not ev.is_set():
        _gate["decision"] = decision
        ev.set()


async def _notify_outage(err: str):
    """Creator-a bildiriş + düymələr: 🔁 yenidən yoxla · 🏠 evə keç · 🤖 avto."""
    if time.time() - _gate["notified_at"] < 600:              # tez-tez dəyişən xətada spam olmasın
        return None
    _gate["notified_at"] = time.time()
    try:
        from html import escape
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M
        from bot.menus.state import get_context
        ctx = get_context()
        text = (f"🔴 <b>Uzaq server cavab vermir</b>\n<code>{escape(str(err)[:200])}</code>\n\n"
                f"⬇️ Yükləmə: <b>{MODE_LABEL[dl_mode()]}</b> · ⬆️ Upload: <b>{MODE_LABEL[up_mode()]}</b>\n"
                f"Xəta rejimi <b>✋ Manual</b> olduğundan iş evə keçmir, gözləyir.\n\nNə edək?")
        kb = M(inline_keyboard=[
            [B(text="🔁 Yenidən yoxla", callback_data="menu:rmt:fix:retry")],
            [B(text="🏠 Evə keç", callback_data="menu:rmt:fix:home"),
             B(text="🤖 Avto", callback_data="menu:rmt:fix:auto")]])
        return await ctx.bot.send_message(ctx.creator_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.warning(f"🌐 Server bildirişi göndərilmədi: {type(e).__name__}: {e}")
        return None


async def _outage_loop(ev, err: str):
    """Bildiriş göndərir, sonra server bərpa olunana (və ya qərar verilənə) qədər yoxlayır."""
    msg = await _notify_outage(err)
    deadline = time.monotonic() + 1800
    while not ev.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(PROBE_EVERY)
        if ev.is_set():
            return
        res = await check()
        if res["ok"]:
            resolve_gate("retry")
            if msg is not None:
                try:
                    await msg.edit_text("✅ <b>Uzaq server bərpa olundu</b> — gözləyən işlər davam edir.", parse_mode="HTML")
                except Exception as e:
                    logger.debug("bildiriş yenilənmədi: %r", e)
            return


async def wait_decision(kind: str, err, cancel_event=None, timeout: float = None) -> str:
    """✋ Manual: server alınmadı — iş burada gözləyir. → 'retry' (yenidən serverə) | 'home' (evdə işlət).
    Gözləmə bitərsə RemoteUnavailable; ⏹ basılarsa DownloadCancelled."""
    timeout = timeout or GATE_TIMEOUT
    g = _gate
    if g["ev"] is None or g["ev"].is_set():
        g["ev"], g["decision"] = asyncio.Event(), None
        asyncio.ensure_future(_outage_loop(g["ev"], str(err)))
    ev, t0 = g["ev"], time.monotonic()
    g["waiting"] += 1
    try:
        while True:
            try:
                await asyncio.wait_for(ev.wait(), 1.0)
                return g["decision"] or "retry"
            except asyncio.TimeoutError:
                if cancel_event is not None and cancel_event.is_set():
                    from yt_dlp.utils import DownloadCancelled
                    raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")
                if time.monotonic() - t0 > timeout:
                    raise RemoteUnavailable(f"uzaq server əlçatmaz — {int(timeout)} san. gözlənildi: {str(err)[:80]}")
    finally:
        g["waiting"] = max(0, g["waiting"] - 1)


async def apply_fix(action: str) -> str:
    """Bildiriş və menyudakı qərar düymələrinin icrası → istifadəçiyə nəticə mətni (HTML)."""
    if action == "retry":
        res = await check()
        if res["ok"]:
            resolve_gate("retry")
            return "✅ Server cavab verdi — gözləyən işlər davam edir."
        from html import escape
        return f"❌ Server hələ cavab vermir: {escape(res['error'][:200])}"
    if action == "home":
        set_mode("download", "home")
        set_mode("upload", "home")
        resolve_gate("home")
        return "🏠 Yükləmə və upload evə keçirildi, gözləyən işlər evdə görülür. Geri qaytarmaq üçün aşağıdan seçin."
    if action == "auto":
        set_auto_fallback(True)
        resolve_gate("home")
        return "🤖 Avto açıldı: gözləyən işlər indi evdə görülür, bundan sonra server xətasında iş avtomatik evə keçir."
    return "?"


def _stopping() -> bool:
    from bot.core import cpu_pool as cp
    return bool(getattr(cp, "_STOPPING", False))


async def try_download(args: tuple, timeout: float, cancel_event, local_cancel_path: str, lane: str):
    """Yükləməni serverdə görməyə çalışır. → nəticə, və ya None (bu iş evdə görülməlidir).
    🤖 Avto: server xətasında dərhal None. ✋ Manual: xəta olarsa gözləyir (bildiriş + düymələr)."""
    while True:
        if not server_selected("download"):
            return None
        if not available():
            if auto_fallback():
                stats["fallback"] += 1
                return None
            if await wait_decision("download", down_info()[1] or "server dayanıb", cancel_event) == "home":
                return None
            continue
        if not claim("download", lane):
            return None                                       # 🏠+🌐 ikisi: server dolu → artıq iş evdə
        mark("dl", "S")
        try:
            return await download(args, timeout, cancel_event, local_cancel_path, lane)
        except RemoteRetryHome as e:
            if auto_fallback():
                logger.info(f"🌐 Bu yükləmə evdə: {e}")
                return None
            raise getattr(e, "original", e)                   # ✋ server IP-si rədd edildi — evə keçmir, xəta olduğu kimi
        except RemoteLocalOnly as e:
            logger.debug(f"🌐 Bu yükləmə evdə: {e}")        # server üçün uyğun deyil (brauzer cookies-i və s.)
            return None
        except RemoteUnavailable as e:
            if _stopping():
                raise asyncio.CancelledError("bot dayanır") from e
            if auto_fallback():
                stats["fallback"] += 1
                logger.warning(f"🌐 Uzaq yükləmə alınmadı: {e} — evdə yüklənir")
                return None
            if await wait_decision("download", e, cancel_event) == "home":
                return None
        finally:
            release("download", lane)


async def try_upload(path: str, token: str, chat_id: int, thread_id, filename: str, title: str, performer: str,
                     duration: int, caption: str, thumb: bytes = None):
    """Serverdəki faylı serverdən göndərməyə çalışır. → aiogram Message, və ya None (fayl evə çəkilib evdən getməlidir).
    Telegram xətası — RemoteUploadError."""
    while True:
        if not (pending(path) and server_selected("upload")):
            return None
        if not available():
            if auto_fallback():
                stats["fallback"] += 1
                return None
            if await wait_decision("upload", down_info()[1] or "server dayanıb") == "home":
                return None
            continue
        if not claim("upload"):
            return None
        try:
            msg = await upload_audio(path, token, chat_id, thread_id, filename, title, performer, duration, caption, thumb)
            mark("up", "S")
            return msg
        except RemoteUnavailable as e:
            if auto_fallback():
                stats["fallback"] += 1
                logger.info(f"🌐 Uzaq upload alınmadı ({e}) — fayl evə çəkilir")
                return None
            if await wait_decision("upload", e) == "home":
                return None
        finally:
            release("upload")


# ───────────────────────── vəziyyət ─────────────────────────
def status() -> tuple[str, str]:
    """(ikon, qısa mətn) — menyunun vəziyyət sətri."""
    if not enabled():
        return "⚪", "Söndürülüb, hər şey evdə"
    if not configured():
        return "🟡", "Ünvan daxil edilməyib"
    left, err = down_info()
    if left:
        return "🔴", "Dayanıb" + (f": {err[:70]}" if err else "")
    if last_check["at"]:
        return ("🟢", "İşləyir") if last_check["ok"] else ("🟡", last_check["error"][:80] or "Yoxlama alınmadı")
    return "🟢", "Aktiv, hələ yoxlanmayıb"


def check_is_stale(max_age: float = 300.0) -> bool:
    return time.time() - last_check["at"] > max_age


def snapshot() -> dict:
    """Menyu üçün bütün vəziyyət bir yerdə (görünüşlər yalnız bunu formatlayır)."""
    icon, text = status()
    running = {"depo": 0, "user": 0, "up": 0}
    done = 0
    try:
        from bot.core import cpu_pool as cp
        running = {"depo": cp.REMOTE_LANES["depo"].running, "user": cp.REMOTE_LANES["user"].running,
                   "up": cp.REMOTE_UP.running}
        done = sum(p.stats["done"] for p in cp.REMOTE_LANES.values()) + cp.REMOTE_UP.stats["done"]
    except Exception as e:
        logger.debug("pool vəziyyəti oxunmadı: %r", e)
    return {
        "icon": icon, "status": text, "enabled": enabled(), "configured": configured(),
        "host": host(), "port": port(), "key": key(), "dir": rdir(),
        "dl": mode("download"), "up": mode("upload"), "auto": auto_fallback(),
        "depo_slots": depo_slots(), "user_slots": user_slots(),
        "waiting": _gate["waiting"], "pending_files": len(PENDING), "running": running, "done": done,
        "fallback": stats["fallback"], "ip_retry": stats["ip_retry"],
        "check": dict(last_check), "info": last_check.get("info") or {},
    }
