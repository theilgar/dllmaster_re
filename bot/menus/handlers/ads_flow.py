"""
bot.menus.handlers.ads_flow
📣 Qrup reklamı paneli: düymələr, qrup seçimi, mətn daxil edilməsi, canlı gedişat.
"""
import contextlib
import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.menus.state import ad as ad_state
from bot.menus.state import get_context, is_creator, panel_editor, reset_inputs, safe_edit_cb
from bot.menus.views.group_ads import ads_view, ask_text_view, groups_view
from common import group_ads as ads

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 3.0


def _progress_editor(ctx, chat_id: int, panel_id: int):
    """Gedişat panelini seyrək yeniləyən funksiya (flood olmasın); dövr bitəndə həmişə yeniləyir."""
    last = {"t": 0.0}
    edit = panel_editor(ctx, {"chat_id": chat_id, "panel_id": panel_id}, "Qrup reklamı")

    async def on_progress():
        runner = getattr(ctx, "group_ads", None)
        running = bool(runner and runner.state.get("running"))
        if running and time.time() - last["t"] < PROGRESS_EVERY:
            return
        last["t"] = time.time()
        await edit(*ads_view())

    return on_progress


async def _mcb_ads(cb: CallbackQuery, parts: list, action: str):
    """menu:ads[:go | :stop | :grp:<səhifə> | :t:<qrup>:<səhifə> | :all | :none | :ref | :dly | :cool | :max | :slot | :text | :textdef | :prev]"""
    sub = parts[2] if len(parts) > 2 else ""
    ctx = get_context()
    runner = getattr(ctx, "group_ads", None)
    if runner is None:
        await cb.answer("📣 Qrup reklamı plugin-i yüklənməyib", show_alert=True)
        return
    note = ""

    if sub == "":
        ad_state["stage"] = None
    elif sub == "go":
        res = await runner.start(_progress_editor(ctx, cb.message.chat.id, cb.message.message_id))
        note = (f"▶️ Başladı: {res['total']} qrup" if res["ok"] else f"❌ {res['error']}")
    elif sub == "stop":
        note = "⏹ Dayandırılır..." if runner.stop() else "ℹ️ Dövr getmir"
    elif sub == "grp":
        await cb.answer()
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        await safe_edit_cb(cb, *await groups_view(page))
        return
    elif sub == "ref":
        await cb.answer("🔄 Siyahı yenilənir...")
        await safe_edit_cb(cb, *await groups_view(0, refresh=True))
        return
    elif sub == "t" and len(parts) > 3 and parts[3].lstrip("-").isdigit():
        on = ads.toggle_group(int(parts[3]))
        await cb.answer("✅ Seçildi" if on else "▫️ Seçim götürüldü")
        await safe_edit_cb(cb, *await groups_view(int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0))
        return
    elif sub in ("all", "none"):
        cat = await runner.catalog()
        ads.set_selected([g["id"] for g in cat] if sub == "all" else [])
        await cb.answer(f"✅ {len(cat)} qrup seçildi" if sub == "all" else "▫️ Seçim təmizləndi")
        await safe_edit_cb(cb, *await groups_view(0))
        return
    elif sub == "dly":
        ads.cycle(ads.K_DELAY, ads.DELAYS, 90)
        note = f"⏱ Qruplar arası fasilə ~{ads.settings()['delay']} san. (±30%)"
    elif sub == "cool":
        ads.cycle(ads.K_COOL, ads.COOLDOWNS, 24)
        note = f"🔁 Bir qrupa təkrar göndərmə: {ads.settings()['cooldown']} saat sonra"
    elif sub == "max":
        ads.cycle(ads.K_MAX, ads.MAXES, 20)
        note = f"🔢 Bir dövrdə ən çox {ads.settings()['max']} qrup"
    elif sub == "slot":
        v = ads.cycle_slot(runner.slot_indexes())
        note = "🎙 Bütün asistan hesabları" if v == "all" else f"🎙 Yalnız #{v} hesab"
    elif sub == "textdef":
        ads._set(ads.K_TEXT, None)
        note = "♻️ Standart mətn bərpa olundu"
    elif sub == "text":
        await cb.answer()
        reset_inputs()
        ad_state.update(stage="await_text", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await safe_edit_cb(cb, *ask_text_view())
        return
    elif sub == "prev":
        await cb.answer()
        with contextlib.suppress(Exception):
            username = (await ctx.bot.me()).username
            await ctx.bot.send_message(cb.from_user.id, ads.render(None, username), parse_mode="HTML",
                                       disable_web_page_preview=True)
        return

    await cb.answer(note.replace("<b>", "").replace("</b>", "")[:190] if note else None)
    await safe_edit_cb(cb, *ads_view(note))


async def waiting_ads_text(message: Message) -> bool:
    return ad_state.get("stage") == "await_text" and bool(message.from_user) and is_creator(message.from_user.id)


async def ads_text_input(message: Message):
    ctx = get_context()
    html = message.html_text or ""
    with contextlib.suppress(Exception):
        await message.delete()
    edit = panel_editor(ctx, ad_state, "Qrup reklamı")
    if not html.strip() or len(html) > ads.TEXT_LIMIT:
        await edit(*ask_text_view(f"❌ Mətn boş və ya çox uzundur (maks. {ads.TEXT_LIMIT} simvol). Yenidən yaz:"))
        return
    try:                                                   # Telegram bu HTML-i qəbul edirmi? (creator-a göndərilir)
        username = (await ctx.bot.me()).username
        await ctx.bot.send_message(message.from_user.id, ads.render(html, username), parse_mode="HTML",
                                   disable_web_page_preview=True)
    except Exception as e:
        await edit(*ask_text_view(f"❌ Mətn göndərilə bilmir: <code>{escape(str(e))[:200]}</code>"))
        return
    ads._set(ads.K_TEXT, html)
    ad_state["stage"] = None
    await edit(*ads_view("✅ Mətn saxlanıldı — yuxarıdakı mesaj qruplarda belə görünəcək"))


async def ads_text_not_text(message: Message):
    await message.reply("✍️ Mətn kimi yaz.")


def register_ads_handlers(router: Router):
    router.message.register(ads_text_input, F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_ads_text)
    router.message.register(ads_text_not_text, F.chat.type == "private", ~F.text, waiting_ads_text)


__all__ = ["_mcb_ads", "register_ads_handlers"]
