"""
🖥 Bir uzaq server — bot/core/remote_host.py

Server = bir SSH ünvanı + onun öz ayarları (slotlar, qovluq, Firefox girişi) + öz sağlamlığı (dayanıb / işləyir) + öz işçi
hovuzları. Bir neçə server ola bilər; hansına iş verilməsi bot/core/remote.py-dadır (ən az yüklü sağlam server).

Bu fayl yalnız TEK serverlə danışır: ssh əmrləri, fayl köçürmə, işçi faylının sinxronu, yoxlama (diag), quraşdırma, PO token
provayderi, işçi proses hovuzları. Siyasət (rejim, Manual / Avto, bildiriş) burada yoxdur.

sshd standart olaraq bir bağlantıda 10 sessiyaya icazə verir (MaxSessions): qısa əmrlər ortaq (multiplex) bağlantıdan keçir və
sayı məhdudlaşdırılıb, uzunömürlü işçilər isə hər biri öz bağlantısı ilə açılır.
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

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKER_SRC = os.path.join(ROOT, "bot", "core", "ytdl_worker.py")
DEFAULT_DIR = "dlm_remote"
MAX_DEPO_SLOTS, MAX_USER_SLOTS = 16, 4
PROC_MB = 100                          # bir server prosesinin yük altında təxmini RAM-ı
DOWN_FOR = 60                          # server xətasından sonra bu qədər saniyə "dayanıb" sayılır
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:\-\[\]]*$")
SHORT_SESSIONS = 4                     # eyni anda qısa əmr (yoxlama, proqres, fayl yazmaq / silmək)
PULL_SESSIONS = 3                      # eyni anda faylı evə çəkmə
SPAWN_PARALLEL = 3                     # eyni anda yeni işçi bağlantısı (sshd MaxStartups)
NAME_MAX = 24


class RemoteUnavailable(Exception):
    """Server əlçatmazdır / işçi başlamır."""


class RemoteLocalOnly(RemoteUnavailable):
    """Bu tapşırıq serverdə görülə bilməz (məs. brauzer cookies) — server xəstə deyil, yalnız bu iş evdə görülür."""


class RemoteRetryHome(RemoteLocalOnly):
    """Server IP-si YouTube tərəfindən rədd edildi (bot yoxlaması / 403 / 429) — başqa serverdə və ya evdə təkrar olunur."""


class RemoteUploadError(Exception):
    """Telegram serverdən yükləməni rədd etdi (flood, icazə...) — mətn olduğu kimi saxlanır."""


def local_transport() -> bool:
    return os.getenv("DLM_REMOTE_TRANSPORT") == "local"


def _silent_rm(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


DENO_INSTALL = ('[ -x bin/deno ] || { mkdir -p bin && A=$(uname -m) && case "$A" in x86_64) T=x86_64;; aarch64|arm64) T=aarch64;; '
                '*) exit 3;; esac && curl -fsSL -o /tmp/dlm_deno.zip '
                '"https://github.com/denoland/deno/releases/latest/download/deno-$T-unknown-linux-gnu.zip" && '
                'python3 -c "import zipfile; zipfile.ZipFile(\'/tmp/dlm_deno.zip\').extractall(\'bin\')" && chmod +x bin/deno && '
                'rm -f /tmp/dlm_deno.zip; }; bin/deno --version | head -1')
PROVIDER_INSTALL = ('V=$(venv/bin/pip show bgutil-ytdlp-pot-provider | sed -n "s/^Version: //p"); '
                    '[ -f bgutil/server/build/main.js ] || { rm -rf bgutil && git clone -q --depth 1 --branch "$V" '
                    'https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git bgutil && '
                    '(cd bgutil/server && npm ci --no-audit --no-fund >/dev/null 2>&1 && npx tsc); }; '
                    '[ -f bgutil/server/build/main.js ]')
PROVIDER_CMD = ("curl -s --max-time 2 http://127.0.0.1:4416/ping >/dev/null || { [ -f bgutil/server/build/main.js ] && "
                "(cd bgutil/server && setsid nohup node build/main.js --host 127.0.0.1 --port 4416 >> ../../bgutil.log 2>&1 "
                "< /dev/null &); true; }")


def default_cfg(sid: str, name: str = "") -> dict:
    return {"id": sid, "name": name or f"Server {sid}", "host": "", "port": 22, "key": "", "dir": DEFAULT_DIR,
            "py": "python3", "slots": 2, "user_slots": 2, "firefox": False, "on": True}


def validate(field: str, value) -> object:
    """Bağlantı və ad sahələrinin yoxlanması → təmizlənmiş dəyər; səhv olsa ValueError (istifadəçiyə mətn)."""
    v = str(value).strip()
    if field == "host":
        if v and not HOST_RE.match(v):
            raise ValueError("Ünvan belə olmalıdır: istifadəçi@ip  (məs. root@203.0.113.5)")
        return v
    if field == "port":
        if not v.isdigit() or not 1 <= int(v) <= 65535:
            raise ValueError("Port 1–65535 arası rəqəm olmalıdır")
        return int(v)
    if field == "key":
        if v.endswith(".pub"):
            raise ValueError("Bu açıq açardır (.pub). Gizli açarı yazın — eyni ad, .pub-suz (məs. ~/.ssh/dlm_vps)")
        if v and not os.path.isfile(os.path.expanduser(v)):
            raise ValueError("Bu yolda açar faylı tapılmadı (bot işləyən maşında olmalıdır)")
        return os.path.expanduser(v) if v else v
    if field == "dir":
        if v and (v.startswith("-") or ".." in v or "\n" in v):
            raise ValueError("Qovluq adı yararsızdır")
        return v or DEFAULT_DIR
    if field == "name":
        v = " ".join(v.split())[:NAME_MAX]
        if not v:
            raise ValueError("Ad boş ola bilməz")
        return v
    raise ValueError(field)


class Server:
    """Bir uzaq server. Konfiqurasiya `apply(cfg)` ilə gəlir (bazadakı siyahıdan), icra vəziyyəti (dayanıb, yük, hovuzlar)
    isə konfiqurasiya dəyişəndə də qalır."""

    def __init__(self, cfg: dict, persist=None):
        self.id = cfg["id"]
        self._persist = persist or (lambda: None)
        self.down = {"until": 0.0, "error": "", "at": 0.0}
        self.last_check = {"at": 0.0, "ok": False, "error": "", "info": {}}      # son 🧪 yoxlamanın nəticəsi
        self.inflight = {}                                                        # (iş, zolaq) → bitməmiş işlər
        self.ip = {"streak": 0, "notified_at": 0.0}                               # YouTube IP rədd zənciri
        self.cookie_cache = {}                                                    # yerli cookies faylı → (mtime, ölçü, uzaq yol)
        self._synced = None
        self._sync_lock = None
        self._provider_checked = 0.0
        self._sems = {}
        self._pools = {}
        self.apply(cfg)

    # ── konfiqurasiya ──
    def apply(self, cfg: dict):
        h = str(cfg.get("host") or "").strip()
        self.name = str(cfg.get("name") or f"Server {self.id}")[:NAME_MAX]
        self.host = h if HOST_RE.match(h) else ""
        self.port = max(1, min(65535, int(cfg.get("port") or 22)))
        self.key = str(cfg.get("key") or "").strip()
        self.dir = str(cfg.get("dir") or DEFAULT_DIR)
        self.py = str(cfg.get("py") or "python3")
        self.depo_slots = max(1, min(MAX_DEPO_SLOTS, int(cfg.get("slots") or 2)))
        self.user_slots = max(1, min(MAX_USER_SLOTS, int(cfg.get("user_slots") or 2)))
        self.firefox = bool(cfg.get("firefox"))
        self.on = cfg.get("on", True) is not False

    def to_cfg(self) -> dict:
        return {"id": self.id, "name": self.name, "host": self.host, "port": self.port, "key": self.key, "dir": self.dir,
                "py": self.py, "slots": self.depo_slots, "user_slots": self.user_slots, "firefox": self.firefox, "on": self.on}

    def save(self):
        self._persist()

    @property
    def rdir(self) -> str:
        return os.getenv("DLM_REMOTE_DIR") or self.dir

    def limit(self, lane: str) -> int:
        return self.user_slots if lane == "user" else self.depo_slots

    def worst_case_procs(self) -> int:
        """Eyni anda ən çox proses: depo yükləmələri + depo uploadları + istifadəçi."""
        return self.depo_slots * 2 + self.user_slots

    def slot_advice(self) -> dict:
        """Slot sayı serverin RAM-ına uyğundurmu (son uğurlu yoxlamadakı RAM ilə)."""
        ram = (self.last_check.get("info") or {}).get("ram_mb")
        total = self.worst_case_procs()
        out = {"total": total, "need_mb": total * PROC_MB, "ram_mb": ram, "rec_depo": None, "warn": False}
        if ram:
            budget = max(0.0, (ram - 500) * 0.7)                   # sistem və SSH üçün ~500 MB, qalanın 70%-i
            out["rec_depo"] = max(1, min(MAX_DEPO_SLOTS, int((budget / PROC_MB - self.user_slots) // 2)))
            out["warn"] = total * PROC_MB > budget
        return out

    # ── sağlamlıq ──
    def configured(self) -> bool:
        return bool(self.host) or local_transport()

    def usable(self) -> bool:
        """İş qəbul edir: açıqdır, ünvanı var və yaxın vaxtda xəta verməyib."""
        return self.on and self.configured() and time.time() >= self.down["until"]

    def mark_down(self, err, secs: int = DOWN_FOR):
        self.down.update(until=time.time() + secs, error=str(err)[:200], at=time.time())
        logger.warning(f"🌐 Uzaq server «{self.name}» dayanıb sayıldı ({secs} san.): {str(err)[:200]}")

    def mark_up(self):
        self.down["until"] = 0.0

    def down_info(self) -> tuple[float, str]:
        left = self.down["until"] - time.time()
        return (left if left > 0 else 0.0), self.down["error"]

    def load(self, kind: str, lane: str) -> int:
        return self.inflight.get((kind, lane), 0)

    def status(self) -> tuple[str, str]:
        """(ikon, qısa mətn) — bu serverin vəziyyəti."""
        if not self.on:
            return "⚪", "Söndürülüb"
        if not self.configured():
            return "🟡", "Ünvan daxil edilməyib"
        left, err = self.down_info()
        if left:
            return "🔴", "Dayanıb" + (f": {err[:70]}" if err else "")
        lc = self.last_check
        if lc["at"]:
            return ("🟢", "İşləyir") if lc["ok"] else ("🟡", lc["error"][:80] or "Yoxlama alınmadı")
        return "🟢", "Aktiv, hələ yoxlanmayıb"

    def check_is_stale(self, max_age: float = 300.0) -> bool:
        return time.time() - self.last_check["at"] > max_age

    # ── nəqliyyat ──
    def _sem(self, name: str, n: int) -> asyncio.Semaphore:
        key = (name, id(asyncio.get_running_loop()))
        if key not in self._sems:
            self._sems[key] = asyncio.Semaphore(n)
        return self._sems[key]

    def spawn_slot(self):
        """async with server.spawn_slot(): — bu serverə işçi bağlantıları bir-bir (üçlü qruplarla) açılsın."""
        return self._sem("spawn", SPAWN_PARALLEL)

    def argv(self, cmd: str, mux: bool = True) -> list:
        if local_transport():
            return ["bash", "-c", cmd]
        share = (["-o", "ControlMaster=auto", "-o", "ControlPersist=10m",
                  "-o", f"ControlPath={os.path.join(tempfile.gettempdir(), 'dlm-ssh-%C')}"] if mux
                 else ["-o", "ControlMaster=no", "-o", "ControlPath=none"])
        a = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
             "-o", "StrictHostKeyChecking=accept-new", *share, "-p", str(self.port)]
        if self.key:
            a += ["-i", self.key]
        return a + ["--", self.host, cmd]

    def in_dir(self, cmd: str) -> str:
        d = shlex.quote(self.rdir)
        return f"mkdir -p {d} && cd {d} && {cmd}"

    def worker_argv(self, nice: int = 0) -> list:
        return self.argv(self.in_dir(f'PATH="$PWD/bin:$PATH" DLM_REMOTE_WORKER=1 PYTHONUNBUFFERED=1 '
                                     f'exec {shlex.quote(self.py)} ytdl_worker.py {int(nice)}'),
                         mux=False)                                  # işçi öz bağlantısı ilə: ortaq bağlantının limitini tutmur

    async def run(self, cmd: str, timeout: float = 30, stdin_data: bytes = None, in_dir: bool = True) -> tuple[int, bytes, str]:
        """Serverdə əmr işlədir → (kod, stdout, stderr). SSH xətası / vaxt aşımı — RemoteUnavailable."""
        if not self.configured():
            raise RemoteUnavailable("Server ünvanı təyin edilməyib")
        argv = self.argv(self.in_dir(cmd) if in_dir else cmd)
        async with self._sem("short", SHORT_SESSIONS):
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
        if proc.returncode == 255 and not local_transport():
            raise RemoteUnavailable(f"ssh qoşulmadı: {err_s[-200:] or 'naməlum'}")
        return proc.returncode, out, err_s

    async def push_bytes(self, data: bytes, rel: str, timeout: float = 60):
        q = shlex.quote(rel)
        tmp = shlex.quote(f"{rel}.{uuid.uuid4().hex[:8]}.part")      # unikal ad: paralel yazılar bir-birinin faylını götürmür
        d = shlex.quote(os.path.dirname(rel) or ".")
        rc, _, err = await self.run(f"mkdir -p {d} && cat > {tmp} && mv -f {tmp} {q}", timeout, data)
        if rc:
            raise RemoteUnavailable(f"fayl serverə yazılmadı: {err[-150:]}")

    async def remote_size(self, rel: str) -> int:
        rc, out, _ = await self.run(f"wc -c < {shlex.quote(rel)}", 20)
        try:
            return int(out.strip()) if rc == 0 else -1
        except ValueError:
            return -1

    async def pull_file(self, rel: str, local: str, expect: int = -1, timeout: float = 1200):
        """Serverdəki faylı ssh ilə evə çəkir (yarımçıq fayl qalmasın deyə .part → rename; ölçü yoxlanılır)."""
        if not self.configured():
            raise RemoteUnavailable("Server ünvanı təyin edilməyib")
        os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
        async with self._sem("pull", PULL_SESSIONS):
            await self._pull(rel, local, expect, timeout)

    async def _pull(self, rel: str, local: str, expect: int, timeout: float):
        part = local + ".rpart"
        proc = await asyncio.create_subprocess_exec(
            *self.argv(self.in_dir(f"cat -- {shlex.quote(rel)}")), stdin=asyncio.subprocess.DEVNULL,
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

    async def rm(self, *rels: str):
        if not rels:
            return
        try:
            await self.run("rm -f -- " + " ".join(shlex.quote(r) for r in rels), 20)
        except RemoteUnavailable as e:
            logger.debug(f"🌐 uzaq fayl silinmədi: {e}")

    async def touch(self, rel: str):
        try:
            await self.run(f"touch {shlex.quote(rel)}", 15)
        except RemoteUnavailable as e:
            logger.debug(f"🌐 uzaq ləğv bayrağı yazılmadı: {e}")

    # ── işçi faylı, provayder, yoxlama, quraşdırma ──
    async def ensure_synced(self, force: bool = False):
        """ytdl_worker.py serverdə evdəkinin eynisi olsun (bot yeniləndikdə avtomatik; bot başına bir dəfə)."""
        with open(WORKER_SRC, "rb") as f:
            data = f.read()
        h = hashlib.sha256(data).hexdigest()
        if not force and self._synced == h:
            return
        if self._sync_lock is None:
            self._sync_lock = asyncio.Lock()
        async with self._sync_lock:
            if not force and self._synced == h:                 # gözləyərkən başqası artıq göndərib
                return
            await self.push_bytes(data, "ytdl_worker.py")
            self._synced = h

    async def ensure_provider(self, force: bool = False):
        """bgutil PO token provayderi (127.0.0.1:4416) dayanıbsa yenidən qaldırır (server restartından sonra və s.).
        Quraşdırılmayıbsa heç nə etmir. 60 saniyədə ən çox bir dəfə (çoxlu işçi birlikdə açılanda təkrar yoxlanmasın)."""
        if local_transport():
            return
        now = time.monotonic()
        if not force and now - self._provider_checked < 60:
            return
        self._provider_checked = now
        try:
            await self.run(PROVIDER_CMD, 15)
        except RemoteUnavailable as e:
            logger.debug(f"🌐 PO token provayderi yoxlanmadı ({self.name}): {e}")

    async def check(self, need_aiogram: bool = True) -> dict:
        """Test: ssh + işçi + paketlər. → {"ok", "error", "info": {...}}"""
        res = {"ok": False, "error": "", "info": {}}
        try:
            rc, out, err = await self.run("echo ok", 20)
            if rc or b"ok" not in out:
                res["error"] = f"ssh cavabı gözlənilməz: {err[-120:]}"
                return self._checked(res)
            rc, out, err = await self.run("test -f ytdl_worker.py", 15)
            if rc:
                res["error"] = "Server qurulmayıb — «🛠 Qur və ya yenilə» düyməsinə basın"
                return self._checked(res)
            await self.ensure_synced()
            code = "import json,sys;sys.path.insert(0,'.');import ytdl_worker as w;print(json.dumps(w.diag()))"
            rc, out, err = await self.run(f"{shlex.quote(self.py)} -c {shlex.quote(code)}", 40)
            if rc:
                res["error"] = f"Python işləmədi: {err[-150:]}"
                return self._checked(res)
            info = json.loads(out.decode().strip().splitlines()[-1])
            res["info"] = info
            if not info.get("yt_dlp"):
                res["error"] = "yt-dlp serverdə yoxdur — «🛠 Qur və ya yenilə»"
            elif need_aiogram and not info.get("aiogram"):
                res["error"] = "aiogram serverdə yoxdur (upload üçün) — «🛠 Qur və ya yenilə»"
            else:
                res["ok"] = True
        except RemoteUnavailable as e:
            res["error"] = str(e)
        except (ValueError, IndexError) as e:
            res["error"] = f"diaqnostika oxunmadı: {e}"
        return self._checked(res)

    def _checked(self, res: dict) -> dict:
        self.last_check.update(at=time.time(), ok=res["ok"], error=res["error"],
                               info=res["info"] or self.last_check.get("info", {}))
        if res["ok"]:
            self.mark_up()
        return res

    async def setup(self, need_aiogram: bool = True) -> list:
        """Serveri hazırlayır (təkrar işlədilə bilər): qovluq, işçi faylı, venv, yt-dlp + aiogram + PO token, Deno."""
        log = []
        rc, out, err = await self.run("mkdir -p dl cookies && command -v python3 && python3 --version", 30)
        if rc:
            return ["❌ Serverdə python3 yoxdur — əvvəl quraşdırın (məs. sudo apt install python3 python3-venv)"]
        log.append("✅ python3: " + out.decode().strip().splitlines()[-1])
        await self.ensure_synced(force=True)
        log.append("✅ ytdl_worker.py köçürüldü")
        rc, _, err = await self.run("test -x venv/bin/python || python3 -m venv venv", 120)
        if rc:
            return log + [f"❌ venv yaranmadı: {err[-200:]}", "   → sudo apt install python3-venv"]
        log.append("✅ venv hazırdır")
        rc, _, err = await self.run("venv/bin/pip install -q -U 'yt-dlp[default]' aiogram bgutil-ytdlp-pot-provider", 900)
        if rc:
            return log + [f"❌ pip xətası: {err[-300:]}"]
        log.append("✅ yt-dlp + aiogram + PO token plugin-i quruldu")
        self.py = "venv/bin/python"
        self.save()
        # YouTube yoxlamasını həll etmək üçün Deno lazımdır (yt-dlp Node 20-ni dəstəkləmir) — yalnız dlm_remote/bin daxilində
        rc, out, err = await self.run(DENO_INSTALL, 300)
        log.append(f"✅ Deno: {out.decode().strip().splitlines()[-1]}" if not rc and out.strip()
                   else f"⚠️ Deno qurulmadı (YouTube üçün lazımdır): {err[-150:] or 'naməlum xəta'}")
        rc, _, err = await self.run(PROVIDER_INSTALL, 600)          # PO token provayderi (node) — plugin-in versiyası ilə eyni
        if rc:
            log.append(f"⚠️ PO token provayderi qurulmadı (git / npm lazımdır): {err[-150:]}")
        else:
            await self.ensure_provider(force=True)
            log.append("✅ PO token provayderi quruldu və işə salındı (yalnız 127.0.0.1:4416)")
        res = await self.check(need_aiogram)
        info = res["info"]
        if info:
            log.append(f"ℹ️ RAM {info.get('ram_mb')} MB · boş disk {info.get('free_mb')} MB · yt-dlp {info.get('yt_dlp')}")
            if not info.get("deno"):
                log.append("⚠️ Deno tapılmadı — YouTube yoxlamasını həll etmək üçün lazımdır")
            if not info.get("firefox_login"):
                log.append("ℹ️ Serverin Firefox-unda YouTube girişi yoxdur — server səhifəsində izah var")
            if not info.get("ffmpeg"):
                log.append("ℹ️ ffmpeg yoxdur — normaldır: çevirmə evdə görülür (yalnız hazır m4a serverdə qalır)")
        log.append("✅ Hazırdır" if res["ok"] else f"❌ {res['error']}")
        return log

    # ── işçi proses hovuzları ──
    def pool(self, lane: str):
        """lane: 'user' | 'depo' (yükləmə) | 'up' (Telegram-a upload). Hovuz tənbəl yaranır; ölçü serverin slotlarına görə."""
        from bot.core import cpu_pool as cp
        n = self.limit("user" if lane == "user" else "depo")
        pool = self._pools.get(lane)
        if pool is None:
            icon = {"user": "👤", "depo": "📦", "up": "⬆️"}[lane]
            pool = self._pools[lane] = cp.RemoteProcPool(f"remote_{self.id}_{lane}", f"🌐 {self.name} {icon}", n, self)
        if pool.size != n:
            pool.size = pool.max_size = n
            pool.kick()
        return pool

    def pools(self) -> list:
        return list(self._pools.values())

    def running(self) -> dict:
        g = lambda lane: self._pools[lane].running if lane in self._pools else 0     # noqa: E731
        return {"depo": g("depo"), "user": g("user"), "up": g("up")}
