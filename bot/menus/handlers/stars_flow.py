"""
bot.menus.handlers.stars_flow
Telegram Stars ödənişləri, plan qiymətləri və geri qaytarma (refund) axını.
"""

import asyncio
import logging
from html import escape

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from bot.core import panel
from bot.core.stars import MAX_STARS, MIN_STARS, set_price
from bot.menus.navigation import nav_row
from bot.menus.state import get_context, safe_edit_cb, stp
from bot.menus.views.base import fmt_dt
from bot.menus.views.premium_stars import (
    PLAN_MAP,
    payment_card,
    stars_plan_panel,
)
from common.database import get_db

logger = logging.getLogger(__name__)


async def _mcb_st_plan(cb: CallbackQuery, parts: list, action: str):
    key = parts[2] if len(parts) > 2 else ""
    if key not in PLAN_MAP:
        await cb.answer()
        return
    if action == "st_off":
        await asyncio.to_thread(set_price, key, None)
        await cb.answer("Satışdan çıxarıldı")
        text, kb = stars_plan_panel(key)
    elif action == "st_set":
        await cb.answer()
        stp.update(
            stage="await_price",
            plan=key,
            chat_id=cb.message.chat.id,
            panel_id=cb.message.message_id,
        )
        days, label = PLAN_MAP[key]
        text = (
            f"⭐ <b>{escape(label)}</b>\n\n"
            f"✍️ <b>Qiyməti ulduzla yazın ({MIN_STARS}–{MAX_STARS}):</b>\n"
            "• <b>Rəqəm</b> (məs: <code>50</code>) — ulduz qiyməti\n"
            "• <b>-</b> — <b>off</b> (satışdan çıxarılsın)\n"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_plan:{key}")])
    else:
        await cb.answer()
        text, kb = stars_plan_panel(key)
    await safe_edit_cb(cb, text, kb)


async def _mcb_st_p(cb: CallbackQuery, parts: list, action: str):
    ctx = get_context()
    try:
        pid = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        await cb.answer()
        return
    if action == "st_p":
        await cb.answer()
        text, kb = await payment_card(pid, page)
    elif action == "st_ref":
        await cb.answer()
        r = await asyncio.to_thread(get_db().get_payment, pid)
        if not r or r["refunded"]:
            text, kb = await payment_card(pid, page)
        else:
            days = PLAN_MAP.get(r["plan"], (None, ""))[0]
            effect = (
                "premiuma toxunulmayacaq (bəxşişdir)"
                if r["plan"] == "tip"
                else "premium tam silinəcək"
                if days is None
                else f"premiumdan {days} gün çıxılacaq"
            )
            text = (
                f"⚠️ <b>Ödəniş #{pid} geri qaytarılsın?</b>\n\n"
                f"⭐ {r['stars']} ulduz istifadəçiyə qaytarılacaq, {effect}."
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    panel.confirm(
                        f"menu:st_refy:{pid}:{page}",
                        f"menu:st_p:{pid}:{page}",
                        "qaytar",
                    ),
                ]
            )
    else:
        r = await asyncio.to_thread(get_db().get_payment, pid)
        if not r or r["refunded"]:
            await cb.answer("Artıq qaytarılıb", show_alert=True)
            text, kb = await payment_card(pid, page)
        else:
            try:
                await ctx.bot.refund_star_payment(
                    user_id=r["user_id"], telegram_payment_charge_id=r["charge_id"]
                )
            except Exception as e:
                await cb.answer("Geri qaytarma alınmadı", show_alert=True)
                text, kb = await payment_card(pid, page, f"❌ <code>{escape(str(e))[:250]}</code>")
                await safe_edit_cb(cb, text, kb)
                return
            await asyncio.to_thread(get_db().mark_refunded, pid)
            days = PLAN_MAP.get(r["plan"], (None, ""))[0]
            if r["plan"] == "tip":
                res = "tip"
            else:
                res = await asyncio.to_thread(get_db().shorten_premium, r["user_id"], days)
            await cb.answer("↩️ Ulduzlar qaytarıldı")
            try:
                await ctx.bot.send_message(
                    r["user_id"],
                    f"↩️ <b>{r['stars']}⭐ hesabınıza qaytarıldı.</b>\n"
                    + (
                        "Premium ləğv edildi."
                        if res == "revoked"
                        else "Premium müddəti uyğun olaraq qısaldıldı."
                        if isinstance(res, int)
                        else ""
                    ),
                    parse_mode="HTML",
                )
            except Exception as _e:
                logger.debug("refund bildiriş göndərmə xətası: %r", _e)
            note = {
                "revoked": "💎 Premium silindi.",
                "lifetime": "💎 Ömürlük premium toxunulmadı.",
                "tip": "💝 Bəxşiş idi — premiuma toxunulmadı.",
                None: "ℹ️ İstifadəçinin aktiv premiumu yox idi.",
            }.get(res, f"💎 Premium yeni bitmə: {fmt_dt(res)}" if isinstance(res, int) else "")
            text, kb = await payment_card(pid, page, f"✅ Qaytarıldı. {note}")
    await safe_edit_cb(cb, text, kb)
