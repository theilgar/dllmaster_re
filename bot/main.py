"""
🤖 Bot hissəsi — bot/main.py

Ümumi giriş init.py-dır. Bu fayl botun öz işə salınma addımlarını saxlayır:
  setup(context)    — servislər (Spotify, YouTube), middleware, ⚙️ cpu_pool, bot/plugins/*
  shutdown(context) — yt-dlp işçi proseslərini bağlayır

Tək işə salmaq (userbot olmadan):
    python -m bot.main          ≡  python init.py bot
Bu rejimdə Telethon client yaradılmır: depo yalnız public kanalları veb səhifədən oxuyur,
📱 Userbotlar paneli və .komandalar işləmir. 🎙 Canlı yayım asistanı (2-ci hesab) isə işləyir.
"""
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"
PACKAGE = "bot.plugins"

# ".alive", ".backup now", ".ff 30" — userbot komandası (nöqtə + hərf ilə başlayan söz)
DOT_CMD = re.compile(r"^\.[^\W\d_]\w*(?:\s|$)", re.S)


def ignore_stale_callbacks(context):
    """Gec cavablanan düymə (callback) xətası handler-i yarımçıq qoymasın.

    Telegram callback-ə cavab üçün ~15 san. verir. Bot restart olarkən basılan düymələr qalxanda birdən
    gəlir, ya da bot məşğul olanda cavab gecikir → cb.answer() "query is too old" atır, handler dayanır,
    panel yenilənmir və 🚨 bildirişi gəlir. Bu sessiya middleware-i yalnız HƏMİN xətanı udur: cavab
    "verilmiş" sayılır, handler davam edir (panel yenə yenilənir). Digər xətalara toxunulmur.
    """
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import AnswerCallbackQuery

    STALE = ("query is too old", "query id is invalid", "response timeout expired")

    class IgnoreStale(BaseRequestMiddleware):
        async def __call__(self, make_request, bot, method):
            if not isinstance(method, AnswerCallbackQuery):
                return await make_request(bot, method)
            try:
                return await make_request(bot, method)
            except TelegramBadRequest as e:
                if any(s in str(e).lower() for s in STALE):
                    logger.debug(f"Köhnə callback cavablanmadı (zərərsiz): {e}")
                    return True
                raise

    context.bot.session.middleware(IgnoreStale())


def set_language(context):
    """🌐 Hər update üçün istifadəçinin dilini təyin et (common/i18n.py). _() bu dili götürür.
    Seçim yoxdursa Telegram-ın language_code-u (avtomatik) — /lang ilə dəyişdirilir."""
    from common import i18n

    async def mw(handler, event, data):
        inner = getattr(event, "event", None) or event
        user = getattr(inner, "from_user", None)
        lang = i18n.get_lang(user.id, getattr(user, "language_code", None)) if user else i18n.default_lang()
        token = i18n.use(lang)
        try:
            return await handler(event, data)
        finally:
            i18n.reset(token)

    context.dp.update.outer_middleware(mw)


def ignore_userbot_commands(context):
    """Creator-un (və ya userbot hesabının) yazdığı .komandalara bot reaksiya verməsin.

    Userbot öz hesabından botun çatına ".alive" yazanda əvvəl music_plugin bunu mahnı axtarışı sayırdı.
    Outer middleware bütün handler-lərdən əvvəl işləyir: belə mesaj heç bir plugin-ə çatmır.
    Bot mətn gözləyirsə (giriş parolu, broadcast və s.) mesaj həmişəki kimi keçir.
    """
    from aiogram import BaseMiddleware

    class IgnoreDotCommands(BaseMiddleware):
        async def __call__(self, handler, event, data):
            user = getattr(event, "from_user", None)
            text = getattr(event, "text", None) or ""
            if user and text.startswith(".") and DOT_CMD.match(text):
                me = getattr(context, "telethon_me", None)
                own = user.id == context.creator_id or (me is not None and user.id == getattr(me, "id", None))
                from bot.core.input_wait import waiting_any
                if own and not waiting_any(context, user.id):
                    return None                              # userbot-un işidir, bot susur
            return await handler(event, data)

    context.dp.message.outer_middleware(IgnoreDotCommands())


async def init_services(context):
    """Spotify və YouTube menecerləri — plugin-lər setup() zamanı bunları context-dən götürür."""
    logger.debug("⚙️ Servislər başladılır...")
    from bot.core.spotify_handler import SpotifyManager
    from bot.core.youtube_handler import YoutubeManager

    from common.database import get_db
    db = get_db()
    sp_id = db.get_setting("spotify:client_id") or os.getenv("SPOTIFY_CLIENT_ID")
    sp_sec = db.get_setting("spotify:client_secret") or os.getenv("SPOTIFY_CLIENT_SECRET")
    context.spotify = SpotifyManager(sp_id, sp_sec)
    logger.info("✅ Spotify service initialized")

    browser = os.getenv("YOUTUBE_BROWSER", "chrome")
    context.youtube = YoutubeManager(browser=browser)
    context.youtube_manager = context.youtube
    logger.info(f"✅ YouTube services initialized ({browser})")


async def setup(context):
    from common.utilities import CommandLoggerMiddleware, load_plugins

    await init_services(context)
    context.dp.message.middleware(CommandLoggerMiddleware())
    ignore_userbot_commands(context)            # ".alive" kimi userbot komandalarına bot cavab verməsin
    ignore_stale_callbacks(context)             # "query is too old" — gec düymə cavabı handler-i qırmasın
    from bot.core import latency
    latency.install(context)                    # ⏱ düymə / komanda cavab müddəti (⚙️ Performans-da görünür)
    set_language(context)                       # 🌐 istifadəçinin dili (common/i18n.py)
    from bot.core import inline_panels
    inline_panels.install_creator_guard(context)   # ⛔ sahib panelləri — hər handler-də ayrıca yoxlamaq lazım deyil
    try:
        inline_panels.install(context)          # 🪟 .bmenu — admin panelləri inline kartda da işləsin
    except Exception as e:
        logger.warning(f"🪟 inline panellər qoşulmadı: {e}")

    from bot.core import group_panel
    group_panel.setup(context)                  # 👥 qrup ayarları paneli (grp:*) — menu / mp3 / vrec ortaq

    # ⚙️ thread hovuzu + 🫀 donma monitoru plugin-lərdən ƏVVƏL (menu_plugin yüklənməsə də işləsin)
    try:
        from bot.core import cpu_pool
        cpu_pool.init(context)
    except Exception as e:
        logger.warning(f"⚙️ cpu_pool başladılmadı: {e}")

    logger.info("🔌 Bot plugin-ləri yüklənir...")
    context.bot_plugins = await load_plugins(context, PLUGINS_DIR, PACKAGE, label="🤖")


async def shutdown(context):
    # 🧠 yt-dlp işçi prosesləri (shutdown hook işləməyibsə — məs. polling xəta ilə düşüb)
    try:
        from bot.core import cpu_pool
        await cpu_pool.stop_pools()
    except Exception as _e:
        logger.debug("udulan xəta [bot/main.py:152]: %r", _e)


def main():
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from init import main as run
    run("bot")


if __name__ == "__main__":
    main()
