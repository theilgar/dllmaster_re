"""
bot.menus.views.tipmsg
☕ Bəxşiş mesajı: istədiyin istifadəçiyə göndər və ya broadcast et (📣 Yayım & Bildirişlər bölməsi).
"""
import asyncio
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.views.base import LINE, SECTIONS, crumb
from common.database import get_db
from bot.menus.state import tp
from common.menu import _btn


def kind() -> str:
    """Hazırkı axın: 'tip' (☕ bəxşiş) və ya 'premmsg' (💎 premium təklifi) — callback önəkidir."""
    return "premmsg" if tp.get("kind") == "prem" else "tipmsg"


def label() -> str:
    return "💎 Premium təklifi" if kind() == "premmsg" else "☕ Bəxşiş mesajı"


async def bot_username() -> str:
    from bot.menus.views.broadcast import get_bot_username
    return await get_bot_username()


async def tip_menu_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    db = get_db()
    free = len(await asyncio.to_thread(db.broadcast_targets, "free"))
    allu = len(await asyncio.to_thread(db.broadcast_targets, "users"))
    groups = len(await asyncio.to_thread(db.broadcast_targets, "groups"))
    lines = [
        crumb(SECTIONS["com"], label()),
        LINE,
        ("İstifadəçiyə <b>Premium təklifi</b> göndər: mətn + plan düymələri (Telegram Stars). Basanda ödəniş açılır."
         if kind() == "premmsg" else
         "İstifadəçiyə <b>əməyə dəstək</b> mesajı göndər: mətn + ⭐ məbləğ düymələri (Telegram Stars). "
         "Basanda ödəniş pəncərəsi açılır."),
        ("<i>Mətni 🎨 Mesaj şablonları → 💎 Premium bölməsindən dəyişə bilərsən.</i>" if kind() == "premmsg" else
         "<i>Mətni 🎨 Mesaj şablonları → ⭐ Bəxşiş bölməsindən dəyişə bilərsən.</i>"),
        "",
        f"🆓 Free istifadəçilər: <b>{free}</b> · 👤 Bütün istifadəçilər: <b>{allu}</b> · 👥 Qruplar: <b>{groups}</b>",
        "<i>Qrupda düymə üzvləri botun şəxsi çatına aparır (ödəniş orada olur).</i>",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = [
        [_btn("👤 İstədiyim adama göndər", f"{kind()}:to"), _btn("👁 Mənə önizləmə", f"{kind()}:prev")],
        [_btn(f"📢 Free-yə broadcast ({free})", f"{kind()}:bc:free")],
        [_btn(f"📢 Hamıya broadcast ({allu})", f"{kind()}:bc:users")],
        [_btn("👥 Qrupa göndər", f"{kind()}:g"), _btn(f"📢 Qruplara broadcast ({groups})", f"{kind()}:bc:groups")],
        nav_row("com", refresh="tipmsg"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def ask_group_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        crumb(SECTIONS["com"], label(), "👥 Qrupa göndər"),
        LINE,
        "✍️ Qrupun <b>ID</b>-sini (məs. <code>-1001234567890</code>) və ya <b>@username</b>-ini yaz:",
        "<i>Bot həmin qrupda olmalı və mesaj yaza bilməlidir. Qrupu 👥 siyahıdan seçib kartındakı "
        "☕ düyməsi ilə də göndərə bilərsən.</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[nav_row(kind())])


def ask_user_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        crumb(SECTIONS["com"], label(), "👤 Adama göndər"),
        LINE,
        "✍️ İstifadəçinin <b>ID</b>-sini və ya <b>@username</b>-ini yaz:",
        "<i>məs. <code>123456789</code> və ya <code>@username</code></i>",
        "<i>Username ilə göndərmək üçün istifadəçi əvvəlcə botu /start etməlidir "
        "(Telegram botlara username-i ID-yə çevirməyə icazə vermir).</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[nav_row(kind())])


def result_text(status: str, who: str, group: bool = False) -> str:
    """send_one nəticəsi → istifadəçiyə başa düşülən qeyd."""
    return {
        "ok": f"✅ {label()} göndərildi: {who}",
        "removed": (f"🚫 {who} qrupundan bot çıxarılıb / yazmaq qadağandır — mesaj çatmadı" if group
                    else f"🚫 {who} botu bloklayıb — mesaj çatmadı"),
        "failed": (f"❌ {who} qrupuna göndərilmədi — bot orada yazmaq icazəsinə malik deyil" if group
                   else f"❌ {who} üçün göndərilmədi — istifadəçi botu /start etməyib və ya mesaj qəbul etmir"),
    }.get(status, f"❌ {who}: {escape(status)}")


__all__ = ["ask_group_view", "ask_user_view", "bot_username", "result_text", "tip_menu_view"]
