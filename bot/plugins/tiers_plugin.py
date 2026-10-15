"""
bot/plugins/tiers_plugin.py — geriyə uyğunluq (features_plugin.py-a yönləndirir)
"""
import contextlib
import logging

from aiogram import F, types
from bot.plugins.features_plugin import panel_view

logger = logging.getLogger(__name__)

PRIORITY = 74


def setup(context):
    dp = context.dp

    @dp.callback_query(F.data.startswith("tr:"))
    async def tr_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1]
        if act == "close":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        await cb.answer()
        text, kb = panel_view("prem")
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    logger.info("✅ Tiers plugin (uyğunluq) yükləndi")
