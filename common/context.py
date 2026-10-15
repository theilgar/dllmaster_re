"""
🧠 AppContext — bot və userbot hissələrinin paylaşdığı vəziyyət — common/context.py

Bir proses, bir event loop, bir context:
  • bot hissəsi (bot/main.py)      — plugin-lər, YouTube / Spotify servisləri, depo ...
  • userbot hissəsi (userbot/main.py) — context.telethon_client, context.userbot (UB reyestri)
Hər iki hissə də aiogram Bot / Dispatcher-dən istifadə edir: userbot-un .menu kartları və 📱 Userbotlar
paneli bot vasitəsilə göstərilir. Ona görə Bot hər rejimdə yaradılır.
"""
import logging
import os

from aiogram import Bot, Dispatcher, Router

logger = logging.getLogger(__name__)

MODES = ("all", "bot", "userbot")


def create_bot_session():
    """Telegram API sorğuları üçün orjson ilə sürətləndirilmiş AiohttpSession."""
    try:
        import orjson
        from aiogram.client.session.aiohttp import AiohttpSession

        return AiohttpSession(
            json_loads=orjson.loads,
            json_dumps=lambda obj: orjson.dumps(obj).decode("utf-8"),
        )
    except Exception:
        return None


class AppContext:
    def __init__(self, mode: str = "all"):
        if mode not in MODES:
            raise ValueError(f"naməlum rejim: {mode} (olmalıdır: {', '.join(MODES)})")
        logger.info(f"🚀 Başladılır — rejim: {mode}")

        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_TOKEN config.env faylında tapılmadı")

        self.mode = mode
        self.bot_enabled = mode in ("all", "bot")
        self.userbot_enabled = mode in ("all", "userbot")
        try:
            from common.database import get_db
            self.userbot1_enabled = get_db().get_setting("userbot1:enabled", "1") == "1"
        except Exception:
            self.userbot1_enabled = True

        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        session = create_bot_session()
        bot_kwargs = {"token": token, "default": DefaultBotProperties(parse_mode=ParseMode.HTML)}
        if session:
            bot_kwargs["session"] = session
        self.bot = Bot(**bot_kwargs)
        self.dp = Dispatcher()
        self.dp.app_context = self
        self.main_router = Router()
        self.dp.include_router(self.main_router)

        self.creator_id = int(os.getenv("CREATOR_ID") or 0)
        self.user_searches = {}
        self.plugin_status = {}              # "fayl.py" → "ok" | xəta (bot + userbot birlikdə)

        # bot/main.py doldurur
        self.spotify = None
        self.youtube = None
        self.youtube_manager = None

        # Vahid Menyu (Unified Menu)
        from common.menu import set_global_context, main_text, main_kb
        set_global_context(self)
        self.menu_main_view = lambda: (main_text(self), main_kb(self))

        self.bot.data = {"app_context": self}
        os.makedirs("download", exist_ok=True)

    def is_creator(self, user_id: int) -> bool:
        """İstifadəçinin bot yaradıcısı/sahibi (CREATOR_ID) olub-olmadığını yoxlayır."""
        return bool(self.creator_id) and int(user_id or 0) == self.creator_id

