"""
bot.menus.views.invites
Dəvət (invite / referral) sistemi panelləri və istifadəçi dəvət kartları.
"""
from __future__ import annotations

import asyncio
from html import escape
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.views.base import SECTIONS, crumb
from common.database import (
    get_db,
    get_invite_reward_days,
    is_invite_enabled,
)
from common.i18n import _ as tr
from common.menu import _btn


def invite_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """👑 Admin üçün Dəvət Sistemi idarəetmə paneli."""
    enabled = is_invite_enabled()
    days = get_invite_reward_days()
    db = get_db()
    stats = (
        db.get_global_invite_stats()
        if hasattr(db, "get_global_invite_stats")
        else {"total_invites": 0, "unique_inviters": 0, "total_days": 0}
    )

    status_icon = "🟢 Aktiv" if enabled else "🔴 Deaktiv"
    text = (
        crumb(SECTIONS["biz"], "🎁 Dəvət sistemi") + "\n\n"
        f"⚙️ <b>Vəziyyət:</b> {status_icon}\n"
        f"💎 <b>1 dəvət üçün mükafat:</b> <b>{days} gün</b> Premium\n\n"
        "📊 <b>Dəvət Statistikası:</b>\n"
        f"• Cəmi uğurlu dəvətlər: <b>{stats['total_invites']}</b> nəfər\n"
        f"• Dəvət edən istifadəçilər: <b>{stats['unique_inviters']}</b> nəfər\n"
        f"• Verilmiş cəmi Premium: <b>{stats['total_days']}</b> gün\n\n"
        "<i>İstifadəçilər /invite və ya /ref yazaraq öz xüsusi dəvət linklərini əldə edə bilərlər.</i>"
    )
    if note:
        text += f"\n\nℹ️ <i>{note}</i>"

    toggle_txt = "🔴 Söndür" if enabled else "🟢 Aktivləşdir"
    rows = [
        [_btn(toggle_txt, "inv_toggle"), _btn("✏️ Gün sayını yaz", "inv_set_days")],
        [
            _btn(f"{'✅ ' if days == 1 else ''}1 gün", "inv_days:1"),
            _btn(f"{'✅ ' if days == 3 else ''}3 gün", "inv_days:3"),
            _btn(f"{'✅ ' if days == 7 else ''}7 gün", "inv_days:7"),
            _btn(f"{'✅ ' if days == 14 else ''}14 gün", "inv_days:14"),
            _btn(f"{'✅ ' if days == 30 else ''}30 gün", "inv_days:30"),
        ],
        [_btn("🏆 Ən çox dəvət edənlər", "inv_top:0")],
        nav_row("biz"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def invite_top_view(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """🏆 Ən çox dəvət edən istifadəçilərin reytinqi."""
    db = get_db()
    top = db.get_top_inviters(limit=20) if hasattr(db, "get_top_inviters") else []

    header = crumb(SECTIONS["biz"], "🎁 Dəvət sistemi", "🏆 Ən çox dəvət edənlər") + "\n\n"
    if not top:
        text = header + "<i>Hələ ki, heç bir dəvət qeydə alınmayıb.</i>"
    else:
        lines = []
        for i, row in enumerate(top, 1):
            uid = row.get("inviter_id")
            cnt = row.get("count", 0)
            days = row.get("total_days", 0)
            u = db.get_user(uid) if hasattr(db, "get_user") else None
            name = (u or {}).get("first_name") or f"İstifadəçi {uid}"
            uname = f" (@{(u or {}).get('username')})" if (u or {}).get("username") else ""
            lines.append(f"{i}. <b>{escape(name)}</b>{uname} — <b>{cnt}</b> dəvət (+{days} gün)")
        text = header + "\n".join(lines)

    rows = [nav_row("inv_cfg")]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def user_invite_data(
    user,
    bot_username: str,
    stats: dict | None = None,
    back_cb: str = "umenu:home",
) -> tuple[str, InlineKeyboardMarkup]:
    """İstifadəçi dəvət kartının mətni və klaviaturası (sinxron köməkçi)."""
    enabled = is_invite_enabled()
    days = get_invite_reward_days()
    uid = getattr(user, "id", 0)
    name = getattr(user, "first_name", None) or "Dostum"
    if stats is None:
        stats = {"count": 0, "total_days": 0}

    inv_link = f"https://t.me/{bot_username}?start=inv_{uid}"
    share_msg = tr(
        "Salam! Bu bot vasitəsilə yüksək keyfiyyətdə musiqilər yükləyə bilərsən 🎵\nLinkə daxil ol və sən də başla:"
    )
    share_url = f"https://t.me/share/url?url={quote(inv_link)}&text={quote(share_msg)}"

    if not enabled:
        text = (
            f"🎁 <b>Dəvət Sistemi</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Hörmətli {escape(name)}, dəvət sistemi hazırda müvəqqəti dayandırılıb.\n"
            "Tezliklə yenidən aktiv olunacaq!"
        )
        back_text = "⬅️ Geri" if back_cb.startswith("sg:") else "🔙 Əsas Menyu"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=back_text, callback_data=back_cb)]]
        )
        return text, kb

    text = (
        f"🎁 <b>Dostlarını dəvət et — Pulsuz Premium qazan!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Dəvət etdiyin hər <b>1 dostun</b> üçün sənə <b>+{days} gün</b> 💎 <b>Premium</b> hədiyyə olunur!\n\n"
        f"🔗 <b>Sənin xüsusi dəvət linkin:</b>\n"
        f"<code>{inv_link}</code>\n\n"
        "📊 <b>Sənin statistikan:</b>\n"
        f"• Dəvət etdiyin dostlar: <b>{stats['count']}</b> nəfər\n"
        f"• Qazandığın cəmi Premium: <b>{stats['total_days']}</b> gün\n\n"
        "<i>Dostun linkə klikləyib botu başlatdıqda Premium dərhal hesabına yüklənir!</i>"
    )

    is_sg = back_cb.startswith("sg:")
    refresh_cb = "uinv:refresh:start" if is_sg else "uinv:refresh"
    back_label = "⬅️ Geri" if is_sg else "🔙 Menyu"

    sub_row = [
        InlineKeyboardButton(text="🔄 Yenilə", callback_data=refresh_cb),
        InlineKeyboardButton(text=back_label, callback_data=back_cb),
    ]
    if is_sg:
        sub_row.append(InlineKeyboardButton(text="✖️ Ləğv et", callback_data="sg:close"))

    rows = [
        [InlineKeyboardButton(text="📲 Dostlarla paylaş", url=share_url)],
        sub_row,
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def user_invite_view(
    user,
    bot_username: str,
    back_cb: str = "umenu:home",
) -> tuple[str, InlineKeyboardMarkup]:
    """İstifadəçinin öz dəvət kartı: linki, statistika və paylaşma düyməsi."""
    uid = getattr(user, "id", 0)
    db = get_db()
    stats = (
        await asyncio.to_thread(db.get_user_invite_stats, uid)
        if hasattr(db, "get_user_invite_stats")
        else {"count": 0, "total_days": 0}
    )
    return user_invite_data(user, bot_username, stats, back_cb=back_cb)
