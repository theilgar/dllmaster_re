"""
📱 Userbot hissəsi — userbot/main.py

Ümumi giriş init.py-dır. Bu fayl userbot-un öz işə salınma addımlarını saxlayır:
  setup(context)    — 1) userbot/core/accounts — vahid Telethon client + 📱 Userbotlar paneli (🔑 giriş)
                      2) userbot/core/host     — UB reyestri, userbot/plugins/*, .menu / .help paneli
                      3) /userbots             — botda paneli birbaşa açan komanda (yalnız creator)
  shutdown(context) — host və client-i səliqə ilə bağlayır

Tək işə salmaq (bot plugin-ləri olmadan):
    python -m userbot.main      ≡  python init.py userbot
Bot yenə də qoşulur, amma yalnız userbot-a xidmət edir: .menu kartları, inline düymələr, 📱 Userbotlar paneli.
/menu, musiqi, depo bu rejimdə yoxdur.

⚠️ Eyni tokenlə iki proses polling edə bilməz — ya `python init.py` (hər ikisi), ya da hissələrdən biri.
"""
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"

# (modul, plugin_status açarı) — yüklənmə sırası vacibdir: əvvəl client, sonra host
_PARTS = (("userbot.core.accounts", "accounts.py"), ("userbot.core.host", "host.py"))


def _register_bot_command(context):
    from aiogram import F, types
    from aiogram.filters import Command

    @context.dp.message(Command("userbots"), F.chat.type == "private")
    async def userbots_cmd(message: types.Message):
        if not context.creator_id or message.from_user.id != context.creator_id:
            return
        view = getattr(context, "userbots_hub_view", None)
        if view is None:
            await message.answer("❌ 📱 Userbotlar paneli yüklənməyib — logu yoxla.")
            return
        text, kb = view()
        await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


async def setup(context):
    import importlib

    # ⚙️ thread hovuzu + 🫀 donma monitoru userbot üçün də aktivləşdirilir
    try:
        from bot.core import cpu_pool
        cpu_pool.init(context)
        logger.info("⚙️ Userbot: cpu_pool aktivləşdirildi")
    except Exception as e:
        logger.warning(f"⚙️ cpu_pool userbot üçün başladılmadı: {e}")

    status = context.plugin_status
    context.userbot_parts = []
    for modname, key in _PARTS:
        try:
            mod = importlib.import_module(modname)
            result = mod.setup(context)
            if asyncio.iscoroutine(result):
                await result
            context.userbot_parts.append(mod)
            status[key] = "ok"
            logger.info(f"✅ Userbot: {modname}")
        except Exception as e:
            status[key] = f"setup xətası: {type(e).__name__}: {e}"
            logger.error(f"❌ Userbot hissəsi yüklənmədi ({modname}): {e}", exc_info=True)
    _register_bot_command(context)


async def shutdown(context):
    for mod in reversed(getattr(context, "userbot_parts", [])):
        fn = getattr(mod, "teardown", None)
        if fn is None:
            continue
        with contextlib.suppress(Exception):
            await asyncio.wait_for(fn(context), 10)

    # Bot söndürülübsə (yalnız userbot rejimi), cpu_pool işçilərini userbot bağlayır
    if not getattr(context, "bot_enabled", False):
        try:
            from bot.core import cpu_pool
            await cpu_pool.stop_pools()
        except Exception as _e:
            logger.debug("udulan xəta [userbot/main.py:88]: %r", _e)


def main():
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from init import main as run
    run("userbot")


if __name__ == "__main__":
    main()
