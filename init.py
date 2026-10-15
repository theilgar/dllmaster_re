#!/usr/bin/env python3
"""
🚀 DLLMaster — ümumi giriş nöqtəsi (init.py)

    python init.py            — 🤖 bot + 📱 userbot (default, əvvəlki app.py kimi)
    python init.py bot        — yalnız bot              (≡ python -m bot.main)
    python init.py userbot    — yalnız userbot          (≡ python -m userbot.main)
    DLM_MODE=bot python init.py   — rejim mühit dəyişəni ilə də verilə bilər (systemd üçün rahat)

Ardıcıllıq:
  1) asılılıq yoxlaması (common/bootstrap — yalnız standart kitabxana; DLM_SKIP_DEPS=1 ilə keçilir)
  2) config.env, köhnə `core.*` importları üçün uyğunluq qatı, loglar
  3) AppContext (common/context) — Bot + Dispatcher hər rejimdə yaradılır
  4) userbot/main.setup  — Telethon client + .komandalar (bot plugin-lərindən ƏVVƏL: depo onun client-ini görsün)
  5) bot/main.setup      — servislər + bot/plugins
  6) polling; dayananda hissələr tərs sıra ilə bağlanır

Layihə quruluşu:
  common/   ümumi nüvə (baza, log, yükləyici, yeniləmələr)
  bot/      core/ + plugins/   — aiogram botu
  userbot/  core/ + plugins/   — Telethon userbot
"""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)                                   # data/, download/, config.env həmişə kökə nisbətən

MODES = {"all": "🤖 bot + 📱 userbot", "bot": "🤖 yalnız bot", "userbot": "📱 yalnız userbot"}
_ALIASES = {"both": "all", "hamısı": "all", "ub": "userbot", "user": "userbot"}

logger = logging.getLogger("dlm.init")

# ⏱ Dayandırma mühafizəçisi: "🔴 Dayandırılır..."-dan sonra proses bu qədər saniyədə çıxmasa —
# bütün thread-lərin yığını bot_logs/shutdown_hang_*.txt-ə yazılır və proses məcburi bağlanır.
# Səbəb: asyncio.run() sonda arxa plan thread-lərini (to_thread: yt-dlp, ffmpeg, baza) 300 san.-yə qədər
# gözləyir, uvloop isə vaxt limitsiz — ilişmiş bir yükləmə restartı dəqiqələrlə saxlayırdı (10.10 18:49).
SHUTDOWN_GRACE = float(os.getenv("DLM_SHUTDOWN_GRACE", "20"))
_watchdog_armed = False


def _arm_exit_watchdog():
    global _watchdog_armed
    if _watchdog_armed or SHUTDOWN_GRACE <= 0:
        return
    _watchdog_armed = True
    import threading
    import time

    def guard():
        time.sleep(SHUTDOWN_GRACE)
        path = None
        try:
            import faulthandler
            os.makedirs("bot_logs", exist_ok=True)
            path = os.path.join("bot_logs", time.strftime("shutdown_hang_%Y%m%d_%H%M%S.txt"))
            with open(path, "w", encoding="utf-8") as f:
                names = {t.ident: t.name for t in threading.enumerate()}
                f.write(f"Dayandırma {SHUTDOWN_GRACE:.0f} san.-dən çox çəkdi. Canlı thread-lər:\n")
                for ident, name in names.items():
                    f.write(f"  {ident}  {name}\n")
                f.write("\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
        except Exception as _e:
            logger.debug("udulan xəta [init.py:69]: %r", _e)
        try:
            logger.warning(f"⏱ Dayandırma {SHUTDOWN_GRACE:.0f} san.-dən çox çəkdi — proses məcburi bağlanır"
                           + (f" (thread yığını: {path})" if path else ""))
            from common.logger import _log_listener
            if _log_listener is not None:
                _log_listener.stop()                      # növbədəki loglar diskə yazılsın
        except Exception as _e:
            logger.debug("udulan xəta [init.py:77]: %r", _e)
        os._exit(0)

    threading.Thread(target=guard, name="shutdown-watchdog", daemon=True).start()


async def _drain_tasks(timeout: float = 5.0):
    """Qalan arxa plan tapşırıqlarını ləğv et və qısa gözlə — baza bağlanandan sonra işləməsinlər
    (əvvəl onlarla «Keş oxunmadı: the pool is closed» yazılırdı)."""
    me = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        done, still = await asyncio.wait(pending, timeout=timeout)
        if still:
            logger.info(f"⏱ {len(still)} tapşırıq {timeout:.0f} san.-də dayanmadı — gözlənilmir")


def _get_executor_cls():
    try:
        from bot.core.cpu_pool import ManagedThreadPoolExecutor
        return ManagedThreadPoolExecutor
    except Exception:
        class _FallbackExecutor(__import__("concurrent.futures").futures.ThreadPoolExecutor):
            def shutdown(self, wait=True, *, cancel_futures=False):
                super().shutdown(wait=False, cancel_futures=True)
        return _FallbackExecutor


_NoWaitExecutor = _get_executor_cls()


def _install_executor():
    loop = asyncio.get_running_loop()
    cls = _get_executor_cls()
    loop.set_default_executor(cls(thread_name_prefix="dlm-thread"))


def _exit_if_stuck_threads():
    """asyncio.run() qayıtdı, amma ilişmiş işçi thread-ləri qalıbsa — Python çıxışda onları da gözləyir.
    Loglar yazılıb — proses dərhal bağlanır (mühafizəçinin 20 san.-ni gözləmədən)."""
    import threading
    import time
    deadline = time.monotonic() + 2                 # boş işçilər bir anda özləri çıxır — onlara qısa vaxt
    for t in threading.enumerate():
        if t is not threading.main_thread() and not t.daemon:
            t.join(max(0.0, deadline - time.monotonic()))
    stuck = [t for t in threading.enumerate()
             if t is not threading.main_thread() and t.is_alive() and not t.daemon]
    if not stuck:
        return
    logger.info(f"⏱ {len(stuck)} işçi thread hələ işləyir ({', '.join(t.name for t in stuck[:5])}) — "
                "gözlənilmədən çıxılır")
    try:
        from common.logger import _log_listener
        if _log_listener is not None:
            _log_listener.stop()
    except Exception as _e:
        logger.debug("udulan xəta [init.py:136]: %r", _e)
    os._exit(0)


def parse_mode(argv=None) -> str:
    argv = sys.argv[1:] if argv is None else argv
    raw = (argv[0] if argv else os.getenv("DLM_MODE") or "all").strip().lower().lstrip("-")
    mode = _ALIASES.get(raw, raw)
    if mode not in MODES:
        print(f"Naməlum rejim: {raw!r}. İstifadə: python init.py [all|bot|userbot]", file=sys.stderr)
        sys.exit(2)
    return mode


async def run(mode: str):
    from common.context import AppContext
    from common.utilities import cleanup_downloads

    context = None
    parts = []
    _install_executor()                               # ilişmiş thread restartı saxlamasın
    try:
        logger.info(f"🤖 Başlayır — {MODES[mode]}")
        await cleanup_downloads()
        context = AppContext(mode)

        if context.userbot_enabled:
            import userbot.main as userbot_main
            await userbot_main.setup(context)
            parts.append(userbot_main)

        if context.bot_enabled:
            import bot.main as bot_main
            await bot_main.setup(context)
            parts.append(bot_main)

        try:                                        # 🩺 başlanğıc sağlamlıq hesabatı (loga)
            from common import doctor
            doctor.log_report(await asyncio.to_thread(doctor.run_all), logger)
        except Exception as e:
            logger.warning(f"🩺 Sağlamlıq yoxlaması alınmadı: {e}")

        logger.info("🚀 Polling başlayır...")
        # my_chat_member daxil olmaqla bütün qeydiyyatdan keçmiş hadisələr
        await context.dp.start_polling(context.bot, allowed_updates=context.dp.resolve_used_update_types())
        logger.info("🛑 Polling dayandı")

    except Exception as e:
        logger.critical(f"💥 Fatal error: {e}", exc_info=True)
    finally:
        logger.info("🔴 Dayandırılır...")
        _arm_exit_watchdog()
        try:                                          # arxa plan thread-lərinə «dayan» siqnalı
            from common.utilities import SHUTDOWN
            SHUTDOWN.set()
            filler = getattr(context, "depo_filler", None)
            ev = getattr(filler, "stop_event", None)
            if ev is not None:
                ev.set()
        except Exception as _e:
            logger.debug("shutdown siqnalı verilmədi: %r", _e)
        for part in reversed(parts):
            try:
                await part.shutdown(context)
            except Exception as e:
                logger.warning(f"{part.__name__}.shutdown: {e}")
        if context:
            try:
                await context.bot.session.close()
            except Exception as _e:
                logger.debug("udulan xəta [init.py:192]: %r", _e)
        await cleanup_downloads()
        try:
            from common.utilities import close_http_session
            await close_http_session()
        except Exception as _e:
            logger.debug("udulan xəta [init.py:212]: %r", _e)
        await _drain_tasks()                          # baza bağlanmazdan ƏVVƏL
        try:
            from common.database import close_db
            close_db()
        except Exception as _e:
            logger.debug("udulan xəta [init.py:199]: %r", _e)
        try:
            from userbot.core.userbot_db import close_udb
            close_udb()
        except Exception as _e:
            logger.debug("udulan xəta [init.py:204]: %r", _e)


_LOCK_FD = None


def single_instance(token: str):
    """Eyni tokenlə ikinci nüsxə başlamasın (TelegramConflictError-un qarşısı).

    Kilid faylı tokenə bağlıdır (qovluğa yox) — layihənin iki kopyası eyni tokenlə işə salınsa da tutulur.
    ♻️ restart (os.execv) zamanı fayl avtomatik bağlanır, yeni proses kilidi yenidən alır.
    DLM_ALLOW_MULTI=1 — yoxlamanı söndür.
    """
    global _LOCK_FD
    if os.getenv("DLM_ALLOW_MULTI") == "1" or not token:
        return
    try:
        import fcntl
    except ImportError:                                  # Windows — yoxlama yoxdur
        return
    import hashlib
    import tempfile
    tag = hashlib.sha256(token.encode()).hexdigest()[:12]
    # XDG_RUNTIME_DIR terminalda var, systemd servisində yoxdur → iki nüsxə fərqli kilid faylı görürdü
    run_dir = tempfile.gettempdir()
    path = os.path.join(run_dir, f"dllmaster-{tag}.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            other = os.read(fd, 64).decode().strip() or "?"
        except Exception:
            other = "?"
        print(f"\n❌ Bu bot artıq işləyir (PID {other}). Eyni tokenlə iki nüsxə TelegramConflictError verir.\n"
              f"   Yoxla:  pgrep -af 'init.py|app.py|bot.main'\n"
              f"   Dayandır: kill {other}   (və ya systemd servisini söndür)\n"
              f"   Bilərəkdən iki nüsxə lazımdırsa: DLM_ALLOW_MULTI=1\n", file=sys.stderr)
        sys.exit(1)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _LOCK_FD = fd


def loop_kwargs() -> dict:
    """⚡ uvloop (könüllü): asyncio-nun C-də yazılmış sürətli dövrəsi — şəbəkə / Telegram I/O 2–4 dəfə sürətli.
    Quraşdırılıbsa avtomatik işlənir (pip install uvloop), yoxdursa və ya DLM_UVLOOP=0 — adi asyncio."""
    if os.getenv("DLM_UVLOOP", "1") == "0" or sys.platform == "win32" or sys.version_info < (3, 12):
        return {}
    try:
        import uvloop
    except ImportError:
        return {}
    logger.info(f"⚡ uvloop aktivdir ({uvloop.__version__})")
    return {"loop_factory": uvloop.new_event_loop}


def main(mode: str = None):
    mode = mode or parse_mode()
    os.environ["PYTHONUNBUFFERED"] = "1"

    # 1) asılılıqlar — aiogram/telethon/dotenv hələ olmaya bilər, ona görə hər şeydən əvvəl.
    #    dotenv artıq varsa config.env-i indi oxu ki, DLM_SKIP_DEPS / DLM_AUTO_INSTALL nəzərə alınsın.
    try:
        from dotenv import load_dotenv
        load_dotenv("config.env")
    except ImportError:
        pass
    from common.bootstrap import ensure_dependencies
    ensure_dependencies()

    # 2) mühit + uyğunluq + loglar (digər importlardan əvvəl: modullar env-i import vaxtı oxuyur)
    from dotenv import load_dotenv
    load_dotenv("config.env")
    single_instance(os.getenv("TELEGRAM_TOKEN", ""))   # ikinci nüsxə → aydın mesajla çıxış
    from common import compat
    compat.install()
    from common.logger import setup_logging
    setup_logging()

    try:
        asyncio.run(run(mode), **loop_kwargs())
    except KeyboardInterrupt:
        logger.info("🛑 İstifadəçi dayandırdı")
    except Exception as e:
        logger.critical(f"💥 Unhandled exception: {e}", exc_info=True)
    finally:
        logger.info("🔴 Bot stopped")
        _exit_if_stuck_threads()


if __name__ == "__main__":
    main()
