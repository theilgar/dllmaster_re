import logging
"""
bot.menus.views.music
Musiqi parametrləri, axtarış rejimləri, yükləmə formatları və teqlər.

Düzəlişlər:
- 🎶 Playlistlər (pla:panel) ana musiqi menyusuna əlavə edilib.
- 🎧 Qrup MP3 Testi ana musiqi menyusundan çıxarılıb (inkişaf menyusuna yönləndirilib).
- 📝 Mahnı İmzası (cap) cari status indikatoru (🟢/🔴) ilə təmin edilib.
- Axtarış mənbəyi və yükləmə formatı rejim toqquşmaları aradan qaldırılıb.
"""

from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from bot.menus.keyboards import btn, menu_btn
from bot.menus.navigation import nav_row

logger = logging.getLogger(__name__)


def _caption_status() -> str:
    """Creator üçün mahnı imzasının (caption) cari vəziyyətini qaytarır."""
    try:
        from common.database import creator_caption_enabled
        return "🟢" if creator_caption_enabled() else "🔴"
    except Exception:
        return "⚪"


def mus_view(ctx: Any = None, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Əsas musiqi menyusu paneli."""
    try:
        from common.menu import music_overview_text
        text = music_overview_text(ctx)
    except Exception:
        text = "🎵 <b>Musiqi və Yükləmə Paneli</b>\n──────────────────────────────────────"

    if note:
        text += f"\n\nℹ️ {note}"

    # Düymə etiketləri
    try:
        from bot.plugins.menu_plugin import _spotify_button_label, yt_button_label
        yt_lbl = yt_button_label()
        sp_lbl = _spotify_button_label()
    except Exception:
        yt_lbl = "▶️ YouTube"
        sp_lbl = "🟢 Spotify"

    isl = "⚡ İnline axtarış"
    if ctx and hasattr(ctx, "inline_settings_label"):
        try:
            isl = ctx.inline_settings_label()
        except Exception as _e:
            logger.debug("udulan xəta [bot/menus/views/music.py:54]: %r", _e)

    cap_icon = _caption_status()

    rows = [
        [menu_btn(yt_lbl, "yt"), menu_btn(sp_lbl, "sp:panel")],
        [btn(isl, "is:panel"), btn("🎛 Mahnı düymələri", "kb_btn:panel")],
        [menu_btn("🏷 Teq Redaktoru", "tags_info"), menu_btn("🎧 Shazam Tanıma", "shazam_info")],
        [btn("🎶 Playlistlər", "pla:panel"), menu_btn(f"📝 Mahnı İmzası: {cap_icon}", "cap")],
        nav_row(back="main", refresh="mus"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def shazam_info_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Shazam musiqi tanıma məlumat pəncərəsi."""
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    text = (
        "🎧 <b>Shazam Musiqi Tanıma</b>\n"
        "──────────────────────────────────────\n"
        "Bot səsli mesajları (voice) və video faylları Shazam vasitəsilə dərhal analiz edir.\n\n"
        "✨ <b>İstifadə:</b>\n"
        "• Bota şəxsi çatda və ya qrupda səsli mesaj (🎙) göndərin və ya yönləndirin.\n"
        "• Video (📹) və ya audio parçası göndərin.\n"
        "• Bot saniyələr ərzində mahnının adını, ifaçısını tapacaq və audio fayl kimi çatdıracaq.\n\n"
        f"🕒 <i>Status: Aktiv ({now_str})</i>"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        nav_row(back="mus", refresh="shazam_info"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def tags_info_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """ID3 / Audio teq redaktoru məlumat pəncərəsi."""
    text = (
        "🏷 <b>Audio Teq Redaktoru</b>\n"
        "──────────────────────────────────────\n"
        "Yüklənən və göndərilən mahnıların ID3 teqlərini və üz qabığını tənzimləyir.\n\n"
        "✨ <b>İmkanlar:</b>\n"
        "• Mahnının adı (Title) və İfaçısı (Artist) avtomatik təmizlənir.\n"
        "• Yüksək keyfiyyətli albom üz qabığı (Cover art) audioya daxil edilir.\n"
        "• Reklam və lazımsız keçidlər təmizlənir.\n\n"
        "ℹ️ <i>Şəxsi çatda bota audio fayl göndərərək interaktiv audio redaktorunu aça bilərsiniz.</i>"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        nav_row(back="mus", refresh="tags_info"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "mus_view",
    "shazam_info_view",
    "tags_info_view",
]

