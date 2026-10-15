"""
🌐 Uzaq server — bot/core/remote.py

Əsas bot (menyu, baza, ffmpeg, Shazam...) evdə qalır; yükləmə (yt-dlp) və depoya upload uzaq serverdə işləyir.
Nəqliyyat — adi SSH (əlavə proqram / port yoxdur): server tərəfdə yalnız bot/core/ytdl_worker.py işləyir və evdəki
işçi proseslə eyni protokolla (stdin/stdout) danışır → cpu_pool.RemoteProcPool.

  ⬇️ Yükləmə  — yt-dlp serverdə işləyir (serverin IP-si). Nəticə:
       • ev fayla toxunacaqsa (ffmpeg, istifadəçiyə göndərmə) — fayl ssh ilə evə çəkilir;
       • 📦 depo zolağında hazır .m4a-dırsa — fayl serverdə QALIR, evdə boş yer-tutan fayl yaranır (PENDING).
  ⬆️ Upload   — PENDING fayl serverdən birbaşa Telegram-a yüklənir (ev bandwidth-i işləmir).
       Server alınmasa fayl evə çəkilir (materialize) və adi yolla göndərilir.

Server əlçatmaz olsa (ssh / worker xətası) 60 san. "dayanıb" sayılır və hər şey avtomatik evdə işləyir (fallback).
Ayarlar bazada (remote:*) — /menu → 🖥 Sistem → ⚙️ Performans → 🌐 Uzaq server.
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
DOWN_FOR = 60                         # server xətasından sonra bu qədər saniyə evdə işlənir
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:\-\[\]]*$")
_INFRA_HINTS = ("no module named", "command not found", "no such file or directory: 'yt", "permission denied",
                "cannot allocate memory", "no space left")


class RemoteUnavailable(Exception):
    """Server əlçatmazdır / işçi başlamır — iş evdə görülməlidir (sakit fallback)."""


class RemoteLocalOnly(RemoteUnavailable):
    """Bu tapşırıq serverdə görülə bilməz (məs. brauzer cookies) — server xəstə deyil, yalnız bu iş evdə görülür."""


class RemoteUploadError(Exception):
    """Telegram serverdən yükləməni rədd etdi (flood, icazə...) — mətn olduğu kimi saxlanır."""


# ───────────────────────── ayarlar ─────────────────────────
def enabled() -> bool:
    return _S.flag("on")


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


def procs() -> int:
    return _S.int("procs", 2, lo=1, hi=4)


def download_flag() -> bool:
    return _S.flag("download", "1")


def upload_flag() -> bool:
    return _S.flag("upload", "1")


def configured() -> bool:
    return bool(host()) or _local_transport()


def _local_transport() -> bool:
    return os.getenv("DLM_REMOTE_TRANSPORT") == "local"


stats = {"fallback": 0}                 # server alınmadı → iş evdə görüldü (say)
_down = {"until": 0.0, "error": "", "at": 0.0}


def available() -> bool:
    return enabled() and configured() and time.time() >= _down["until"]


def download_on() -> bool:
    return available() and download_flag()


def upload_on() -> bool:
    return available() and download_flag() and upload_flag()


def mark_down(err: str, secs: int = DOWN_FOR):
    _down.update(until=time.time() + secs, error=str(err)[:200], at=time.time())
    logger.warning(f"🌐 Uzaq server dayanıb sayıldı ({secs} san.): {str(err)[:200]} — iş evdə görülür")


def mark_up():
    _down["until"] = 0.0


def down_info() -> tuple[float, str]:
    left = _down["until"] - time.time()
    return (left if left > 0 else 0.0), _down["error"]


def set_value(name: str, value) -> str:
    """Mətn ayarları (host / port / key / dir). Yoxlanılır; səhv dəyərdə ValueError."""
    v = str(value).strip()
    if name == "host":
        if v and not HOST_RE.match(v):
            raise ValueError("Ünvan belə olmalıdır: istifadəçi@ip  (məs. root@203.0.113.5)")
    elif name == "port":
        if not v.isdigit() or not 1 <= int(v) <= 65535:
            raise ValueError("Port 1–65535 arası rəqəm olmalıdır")
    elif name == "key":
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


# ───────────────────────── nəqliyyat ─────────────────────────
def _argv(cmd: str) -> list:
    if _local_transport():
        return ["bash", "-c", cmd]
    a = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
         "-o", "StrictHostKeyChecking=accept-new",
         "-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
         "-o", f"ControlPath={os.path.join(tempfile.gettempdir(), 'dlm-ssh-%C')}",
         "-p", str(port())]
    if key():
        a += ["-i", key()]
    return a + ["--", host(), cmd]


def _in_dir(cmd: str) -> str:
    d = rdir()
    prefix = f"mkdir -p {shlex.quote(d)} && " if _local_transport() else ""
    return f"{prefix}cd {shlex.quote(d)} && {cmd}"


def worker_argv(nice: int = 0) -> list:
    return _argv(_in_dir(f"DLM_REMOTE_WORKER=1 PYTHONUNBUFFERED=1 exec {shlex.quote(py())} ytdl_worker.py {int(nice)}"))


async def run(cmd: str, timeout: float = 30, stdin_data: bytes = None, in_dir: bool = True) -> tuple[int, bytes, str]:
    """Serverdə əmr işlədir → (kod, stdout, stderr). SSH xətası / vaxt aşımı — RemoteUnavailable."""
    if not configured():
        raise RemoteUnavailable("Server ünvanı təyin edilməyib")
    argv = _argv(_in_dir(cmd) if in_dir else cmd)
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
    d = shlex.quote(os.path.dirname(rel) or ".")
    rc, _, err = await run(f"mkdir -p {d} && cat > {q}.part && mv -f {q}.part {q}", timeout, data)
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


def _worker_bytes() -> bytes:
    with open(WORKER_SRC, "rb") as f:
        return f.read()


async def ensure_synced(force: bool = False):
    """ytdl_worker.py serverdə evdəkinin eynisi olsun (bot yeniləndikdə avtomatik; bot başına bir dəfə)."""
    data = _worker_bytes()
    h = hashlib.sha256(data).hexdigest()
    if not force and _synced["hash"] == h:
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
        elif not info.get("aiogram") and upload_flag():
            res["error"] = "aiogram serverdə yoxdur (upload üçün) — «🛠 Qur / yenilə»"
        else:
            res["ok"] = True
    except RemoteUnavailable as e:
        res["error"] = str(e)
    except (ValueError, IndexError) as e:
        res["error"] = f"diaqnostika oxunmadı: {e}"
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


# ───────────────────────── yükləmə ─────────────────────────
_cookie_cache = {}                     # yerli yol → (mtime, ölçü, uzaq yol)
PENDING = {}                           # yerli yer-tutan fayl (abspath) → (uzaq yol, ölçü): fayl hələ serverdədir


def _loopback(url: str) -> bool:
    return bool(re.search(r"//(?:[^@/]*@)?(?:localhost|127\.|\[?::1)", url or ""))


async def prepare_opts(opts: dict) -> tuple[dict, str]:
    """Yerli yt-dlp opsiyalarını serverə uyğunlaşdırır → (opsiyalar, yerli qovluq). Alınmasa RemoteLocalOnly."""
    o = dict(opts)
    if o.get("cookiesfrombrowser"):
        raise RemoteLocalOnly("brauzer cookies-i yalnız evdə oxunur")
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
        await asyncio.sleep(1.5)
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


def _sync_pool(pool):
    n = procs()
    if pool.size != n:
        pool.size = pool.max_size = n
        pool.kick()


async def download(args: tuple, timeout: float, cancel_event, local_cancel_path: str, lane: str) -> dict:
    """ytdl_download-un uzaq variantı → {"path", "info"} (cpu_pool._run ilə eyni nəticə). Alınmasa RemoteUnavailable."""
    from bot.core import cpu_pool as cp
    url, opts = args[0], args[1]
    ropts, local_dir = await prepare_opts(opts)
    pool = cp.REMOTE
    _sync_pool(pool)
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
def pending(path) -> bool:
    return isinstance(path, str) and os.path.abspath(path) in PENDING


def pending_size(path) -> int:
    ent = PENDING.get(os.path.abspath(path)) if isinstance(path, str) else None
    return ent[1] if ent else 0


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


# ───────────────────────── vəziyyət ─────────────────────────
def status_line() -> str:
    if not enabled():
        return "⚪ söndürülüb"
    if not configured():
        return "🟡 ünvan təyin edilməyib"
    left, err = down_info()
    if left:
        return f"🔴 dayanıb (~{int(left)} san.) · {err[:80]}"
    return "🟢 aktiv"
