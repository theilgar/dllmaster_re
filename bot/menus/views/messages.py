"""
bot.menus.views.messages
Mesaj şablonları və media (salamlama, qrup bildirişi, premium təklifləri) panelləri.
"""

import asyncio
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core.welcome import (
    CAPTION_LIMIT,
    TEXT_LIMIT,
    TEXT_SLOTS,
    get_media,
    get_text_template,
    media_label,
    send_media,
)
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from common.menu import _btn

MS_WHERE = {
    "welcome": "/start yazanda göndərilir.",
    "group": "Bot qrupa əlavə olunanda və qrupda /start yazılanda göndərilir.",
    "premium": "/start → 💎 Premium al düyməsinə basanda göndərilir.",
}


def media_slot(slot: str) -> str:
    return TEXT_SLOTS[slot]["media"]


def short_media(slot: str) -> str:
    kind = get_media(media_slot(slot))["type"]
    return {
        "default": "🎞 GIF",
        "photo": "🖼 şəkil",
        "animation": "🎞 GIF",
        "video": "🎬 video",
        "none": "🚫 yoxdur",
    }.get(kind, kind)


def ms_list_panel(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{info['label']}\n    📝 {'✏️ dəyişdirilib' if get_text_template(slot) else 'default'}"
        f" · 🖼 {short_media(slot)}"
        for slot, info in TEXT_SLOTS.items()
    ]
    text = (
        "🎨 <b>Mesaj və media</b>\n\n"
        "Botun göndərdiyi mesajların mətni və şəkli / GIF-i / videosu.\n\n"
        + "\n".join(lines)
        + (f"\n\n{note}" if note else "")
        + "\n\n<i>Dəyişmək istədiyini seç:</i>"
    )
    btns = [_btn(info["label"], f"ms:{slot}") for slot, info in TEXT_SLOTS.items()]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton(text="🌐 Dillər", callback_data="ln:panel")])
    rows.append(nav_row("com"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def ms_panel(slot: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    info = TEXT_SLOTS[slot]
    mslot = media_slot(slot)
    custom = get_text_template(slot)
    shared = " <i>(salam mesajı ilə ortaq)</i>" if mslot != slot else ""
    where = MS_WHERE.get(slot, "/start altındakı düyməyə basanda göndərilir.")
    vars_text = " ".join(f"<code>{escape(k)}</code>" for k in info["vars"])
    text = (
        f"🎨 <b>{escape(info['label'])}</b>\n<i>{where}</i>\n\n"
        f"📝 <b>Mətn:</b> {'✏️ sənin mətnin' if custom else 'default'}"
        + (f" ({len(custom)} simvol)" if custom else "")
        + f"\n🖼 <b>Media:</b> {escape(media_label(mslot))}{shared}\n\n"
        f"🔤 Mətndə dəyişənlər: {vars_text}"
        + (f"\n\n{note}" if note else "")
    )
    text_row = [
        _btn("✏️ Mətni dəyiş", f"tx_set:{slot}"),
        _btn("📋 Hazırkı mətn", f"tx_show:{slot}"),
    ]
    media_row = [_btn("🖼 Media göndər", f"wm_set:{slot}"), _btn("🚫 Mediasız", f"wm_none:{slot}")]
    rows = [text_row]
    if custom:
        rows.append([_btn("♻️ Standart mətn", f"tx_def:{slot}")])
    rows.append(media_row)
    if mslot == "welcome":
        rows.append([_btn("♻️ Standart media", f"wm_def:{slot}")])
    rows.append([_btn("👁 Önizləmə", f"tx_prev:{slot}")])
    rows.append(nav_row("ms"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def send_sample(chat_id: int, slot: str, first_name: str, user_id: int) -> str:
    """Mesajı istifadəçilərin görəcəyi kimi göndərir (media ilə). Xəta olsa exception atır."""
    import bot.plugins.start_plugin as sp

    ctx = get_context()
    username = (await ctx.bot.me()).username
    text, kb = await asyncio.to_thread(sp.sample_message, slot, first_name, user_id, username)
    await send_media(ctx.bot, chat_id, text, kb, TEXT_SLOTS[slot]["media"])
    return text


__all__ = [
    "CAPTION_LIMIT",
    "MS_WHERE",
    "TEXT_LIMIT",
    "TEXT_SLOTS",
    "media_slot",
    "ms_list_panel",
    "ms_panel",
    "send_sample",
    "short_media",
]
