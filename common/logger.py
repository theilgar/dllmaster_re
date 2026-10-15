import atexit
import datetime
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
import queue
import re
import sys

import colorlog

# Windows üçün rəng dəstəyi
if os.name == 'nt':
    from colorama import init
    init()

_log_queue: queue.Queue = queue.Queue(-1)
_log_listener: QueueListener | None = None
_console_handler: logging.Handler | None = None

# 🖥 Terminal (konsol) loqları — /menu → 📜 Bot logları → 🖥 Terminal. Yalnız konsola təsir edir:
# fayl (bot_logs/bot(tarix).log), 📜 Telegram loqları və 🚨 xəbərdarlıqlar öz səviyyəsində qalır.
#   🌊 all    — hər şey, kitabxanaların (aiogram, telethon, pytgcalls) DEBUG-u da
#   ✋ manual — səviyyələr ayrıca ✅/❌ (istənilən kombinasiya); 🐞 DEBUG — yalnız botun öz modulları
CONSOLE_LEVELS = {
    "DEBUG":   ("🔵", "Debug", "botun öz modullarının ətraflı sətirləri"),     # ikon = terminaldakı rəng
    "INFO":    ("🟢", "Info", "adi iş axını"),
    "WARNING": ("🟡", "Warning", "xəbərdarlıqlar"),
    "ERROR":   ("🔴", "Error", "xətalar (CRITICAL da)"),
}
_LEGACY_MODES = {"error": ["ERROR"], "warning": ["WARNING", "ERROR"], "info": ["INFO", "WARNING", "ERROR"],
                 "debug": ["DEBUG", "INFO", "WARNING", "ERROR"]}
_OWN_LOGGERS = ("bot", "userbot", "common", "__main__", "init", "app")
_CONSOLE_CFG = os.path.join("data", "log_console.json")
LEVEL_COLORS = {
    'DEBUG': 'cyan',
    'INFO': 'green',
    'WARNING': 'bold_yellow',
    'ERROR': 'bold_red',
    'CRITICAL': 'bold_white,bg_red',
}

# 🔒 Sirlər loga düşməsin: aiohttp xətası URL-i olduğu kimi yazır (api.telegram.org/bot<TOKEN>/sendAudio),
# loglar isə kanala (logs_plugin) və 🚨 ani xəta hədəfinə (destinations_plugin) gedir.
_BOT_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")   # URL-də "bot<id>:..." — \b işləmir
_SECRET_ENVS = ("TELEGRAM_TOKEN", "TELETHON_API_HASH", "SPOTIFY_CLIENT_SECRET", "GITHUB_TOKEN")
_DSN_PASS_RE = re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@")


def redact(text: str) -> str:
    """Bot tokenlərini, config.env sirlərini və DSN parolunu *** ilə əvəz edir."""
    if not text:
        return text
    text = _BOT_TOKEN_RE.sub("<bot-token>", text)
    for name in _SECRET_ENVS:
        val = (os.getenv(name) or "").strip()
        if len(val) >= 8 and val in text:
            text = text.replace(val, "***")
    return _DSN_PASS_RE.sub(r"\1***@", text)


class _RedactingQueueHandler(QueueHandler):
    """QueueHandler.prepare() mesajı (traceback daxil) formatlayır — sirləri burada təmizləyirik."""

    def prepare(self, record):
        record = super().prepare(record)
        record.msg = redact(record.msg)
        return record


def cleanup_old_logs(log_dir: str = "bot_logs", max_days: int = 7):
    """Köhnə (max_days gündən çox) log fayllarını təmizləyir."""
    if not os.path.isdir(log_dir):
        return
    cutoff = datetime.datetime.now() - datetime.timedelta(days=max_days)
    for fname in os.listdir(log_dir):
        if not fname.startswith("bot(") or ".log" not in fname:
            continue
        fpath = os.path.join(log_dir, fname)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            if mtime < cutoff:
                os.remove(fpath)
        except OSError:
            pass


def enforce_log_limits(log_dir: str = "bot_logs", max_days: int | None = None, max_total_mb: float | None = None) -> int:
    """Log qovluğunu məhdudlaşdırır (başlanğıcda və dövri çağırılır). Silinən fayl sayını qaytarır.
      • bot(<tarix>).log* — LOG_RETENTION_DAYS-dən (default 7) köhnələr silinir
      • shutdown_hang_*.txt — 14 gündən köhnələr silinir
      • ümumi ölçü LOG_TOTAL_MAX_MB-dan (default 200) çoxdursa ən köhnələr silinir (bugünkü fayl qorunur)
      • console.out — LOG_CONSOLE_MAX_MB-dan böyükdürsə arxivlənib sıfırlanır"""
    if not os.path.isdir(log_dir):
        return 0
    max_days = int(os.getenv("LOG_RETENTION_DAYS", "7")) if max_days is None else max_days
    max_total = float(os.getenv("LOG_TOTAL_MAX_MB", "200")) if max_total_mb is None else max_total_mb
    removed = 0
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    files = []
    for fname in os.listdir(log_dir):
        fpath = os.path.join(log_dir, fname)
        is_log = fname.startswith("bot(") and ".log" in fname
        is_hang = fname.startswith("shutdown_hang_") and fname.endswith(".txt")
        if not (is_log or is_hang):
            continue
        try:
            st = os.stat(fpath)
        except OSError:
            continue
        age_days = (now - datetime.datetime.fromtimestamp(st.st_mtime)).days
        if age_days > (max_days if is_log else max(max_days, 14)):
            try:
                os.remove(fpath)
                removed += 1
            except OSError:
                pass
            continue
        files.append((st.st_mtime, st.st_size, fpath, today in fname))
    total = sum(f[1] for f in files)
    for mtime, size, fpath, is_today in sorted(files):
        if total <= max_total * 1024 * 1024:
            break
        if is_today:
            continue
        try:
            os.remove(fpath)
            total -= size
            removed += 1
        except OSError:
            pass
    rotate_console_out(os.path.join(log_dir, "console.out"), float(os.getenv("LOG_CONSOLE_MAX_MB", "20")))
    return removed


def _console_formatter(color: bool) -> logging.Formatter:
    if color:
        # bütün sətir səviyyənin rəngində: zaman sönük, modul qalın, səviyyə + mesaj rəngli
        return colorlog.ColoredFormatter(
            "%(thin_white)s%(asctime)s%(reset)s %(bold)s%(name)-20s%(reset)s "
            "%(log_color)s%(levelname)-8s %(message)s%(reset)s",
            datefmt="%Y.%m.%d %H:%M:%S", log_colors=LEVEL_COLORS, reset=True, style='%')
    return logging.Formatter("%(asctime)s %(name)-20s %(levelname)-8s %(message)s", datefmt="%Y.%m.%d %H:%M:%S")


def _load_console_cfg() -> dict:
    """{"mode": "all"|"manual", "levels": [...], "color": "on"|"off"|"auto"} (köhnə formatı da oxuyur)."""
    try:
        import json
        with open(_CONSOLE_CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    mode = (cfg.get("mode") or os.getenv("LOG_CONSOLE", "info")).lower()
    levels = cfg.get("levels")
    if mode in _LEGACY_MODES:                      # köhnə tək-seçim rejimi → əl ilə səviyyələr
        levels, mode = _LEGACY_MODES[mode], "manual"
    if mode != "all":
        mode = "manual"
    if not isinstance(levels, list):
        levels = _LEGACY_MODES["info"]
    levels = [lv for lv in CONSOLE_LEVELS if lv in levels]
    return {"mode": mode, "levels": levels, "color": cfg.get("color", "on")}


def _save_console_cfg(cfg: dict):
    import json
    os.makedirs(os.path.dirname(_CONSOLE_CFG), exist_ok=True)
    with open(_CONSOLE_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def console_settings() -> dict:
    """{"mode": error|warning|info|debug|all, "color": on|off|auto}"""
    return _load_console_cfg()


def _color_enabled(setting: str) -> bool:
    if setting == "off":
        return False
    if setting == "auto":
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return True                       # "on" — screen / tee altında da (stdout pipe olsa belə) rəngli


class _LevelSetFilter(logging.Filter):
    """✋ əl ilə rejim: yalnız seçilmiş səviyyələr (CRITICAL — ERROR ilə birlikdə)."""

    def __init__(self):
        super().__init__()
        self.allowed = None                         # None — hamısı

    def filter(self, record):
        if self.allowed is None:
            return True
        name = "ERROR" if record.levelno >= logging.ERROR else logging.getLevelName(record.levelno)
        return name in self.allowed


_level_filter = _LevelSetFilter()


def apply_console(mode: str = None, color: str = None, save: bool = True,
                  toggle: str = None, levels: list = None) -> dict:
    """Terminal loqlarını dərhal tətbiq et (restart lazım deyil).
    mode: all | manual · toggle: bir səviyyəni ✅/❌ et (manual) · levels: siyahını tam təyin et."""
    cfg = _load_console_cfg()
    if mode in ("all", "manual"):
        cfg["mode"] = mode
    if levels is not None:
        cfg["levels"] = [lv for lv in CONSOLE_LEVELS if lv in levels]
        cfg["mode"] = "manual"
    if toggle in CONSOLE_LEVELS:
        cur = set(cfg["levels"])
        cur ^= {toggle}
        cfg["levels"] = [lv for lv in CONSOLE_LEVELS if lv in cur]
        cfg["mode"] = "manual"
    if color in ("on", "off", "auto"):
        cfg["color"] = color
    if save:
        try:
            _save_console_cfg(cfg)
        except OSError:
            pass
    root = logging.getLogger()
    file_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    want_debug = cfg["mode"] == "all" or "DEBUG" in cfg["levels"]
    # 🌊 — kökdə DEBUG (kitabxanalar da); ✋ — kök fayl səviyyəsində, 🐞 seçilibsə yalnız botun öz modulları DEBUG
    root.setLevel(logging.DEBUG if cfg["mode"] == "all" else min(file_level, logging.INFO))
    for name in _OWN_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG if want_debug else logging.NOTSET)
    if _console_handler is not None:
        if cfg["mode"] == "all":
            _level_filter.allowed = None
            _console_handler.setLevel(logging.DEBUG)
        else:
            _level_filter.allowed = set(cfg["levels"])
            lowest = min((getattr(logging, lv) for lv in cfg["levels"]), default=logging.CRITICAL + 1)
            _console_handler.setLevel(lowest)      # heç nə seçilməyibsə — terminal susur
        if _level_filter not in _console_handler.filters:
            _console_handler.addFilter(_level_filter)
        _console_handler.setFormatter(_console_formatter(_color_enabled(cfg["color"])))
    return cfg


def rotate_console_out(path: str = os.path.join("bot_logs", "console.out"), max_mb: float = 20):
    """screen / tee ilə yazılan konsol faylı özü fırlanmır — başlanğıcda böyükdürsə arxivlə və sıfırla.
    tee faylı >> (O_APPEND) ilə açır — yerində kəsiləndən sonra yazı yenidən başdan davam edir."""
    try:
        if os.path.getsize(path) <= max_mb * 1024 * 1024:
            return
        import shutil
        for i in (2, 1):                          # console.out.2 → .3, .1 → .2 (son 3 nüsxə)
            if os.path.exists(f"{path}.{i}"):
                os.replace(f"{path}.{i}", f"{path}.{i + 1}")
        shutil.copyfile(path, f"{path}.1")
        os.truncate(path, 0)
    except OSError:
        pass


class _DailyRotatingFileHandler(RotatingFileHandler):
    """bot_logs/bot(<tarix>).log — ölçüyə görə fırlanır VƏ gecə yarısı yeni tarixli fayla keçir.
    Əvvəl ad başlanğıcda bir dəfə seçilirdi: günlərlə işləyən proses hər şeyi ilk günün faylına yazırdı."""

    def __init__(self, log_dir: str, **kw):
        self._log_dir = log_dir
        self._day = self._today()
        super().__init__(self._path(self._day), **kw)

    @staticmethod
    def _today() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _path(self, day: str) -> str:
        return os.path.abspath(os.path.join(self._log_dir, f"bot({day}).log"))

    def emit(self, record):
        day = self._today()
        if day != self._day:
            self.acquire()
            try:
                if day != self._day:
                    self._day = day
                    if self.stream:
                        self.stream.close()
                        self.stream = None
                    self.baseFilename = self._path(day)
            finally:
                self.release()
        super().emit(record)


def setup_logging():
    """Rəngli və fayla yazılan asinxron/növbəli (non-blocking) log konfiqurasiyası."""
    global _log_listener
    if _log_listener is not None:
        return

    global _console_handler
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    console_handler = logging.StreamHandler()
    _console_handler = console_handler

    # Log qovluğu və fayl adı ayarları
    log_dir = "bot_logs"
    os.makedirs(log_dir, exist_ok=True)
    enforce_log_limits(log_dir)
    # 1 MB idi — yükdə 30–40 dəq.-dən bir fırlanırdı, başlanğıc sətirləri restartdan 2 dəq. sonra itirdi
    file_handler = _DailyRotatingFileHandler(
        log_dir,
        maxBytes=int(float(os.getenv("LOG_MAX_MB", "10")) * 1024 * 1024),
        backupCount=int(os.getenv("LOG_BACKUPS", "5")),
        encoding='utf-8'
    )
    file_formatter = logging.Formatter(
        "%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%Y.%m.%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_level)              # fayl terminal seçimindən asılı deyil (🐞 / 🌊 faylı doldurmasın)

    # Non-blocking QueueHandler əsas thread-də (event loop) heç vaxt disk I/O flush gözləmir
    queue_handler = _RedactingQueueHandler(_log_queue)
    # prepare() mesajı bu formatla əvvəlcədən formatlayır; format verilməsə basicConfig
    # "%(levelname)s:%(name)s:%(message)s" qoyur → hər sətirdə "INFO:modul:" təkrarlanırdı
    queue_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(
        level=log_level,
        handlers=[queue_handler],
        force=True
    )

    # QueueListener ayrı bir arxa plan thread-ində növbədən oxuyub konsola və fayla yazır
    _log_listener = QueueListener(_log_queue, console_handler, file_handler, respect_handler_level=True)
    _log_listener.start()
    atexit.register(_log_listener.stop)
    apply_console(save=False)                    # 🖥 yadda saxlanmış terminal seçimi (səviyyə + rəng)
