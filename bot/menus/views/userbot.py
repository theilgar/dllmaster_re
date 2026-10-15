"""
bot.menus.views.userbot
Userbot idarəetmə pəncərələri və düymə inteqrasiyaları.

İnteqrasiya olunan əsas userbot funksiyaları:
- .amsg: Planlı və avtomatik mesajlar
- .filter: Açar söz avtocavabları və tağ cavabları
- .profile: Profil, bio, ad və görünməz ad (ghost) tənzimləmələri
- .backup: Userbot SQLite bazasının (.backup / .restore) idarə olunması
- Moderasiya və qrup auditi
"""

from aiogram.types import InlineKeyboardMarkup

from bot.menus.keyboards import btn, menu_btn
from bot.menus.navigation import nav_row


def userbot_hub_view(ctx=None, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Userbot mərkəzi idarəetmə paneli (system_hub.bots_view-in vahid fasadı)."""
    from bot.menus.views.system_hub import bots_view

    return bots_view(note=note, ctx=ctx)


def amsg_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Planlı və avtomatik mesajlar (.amsg) məlumat və idarəetmə pəncərəsi."""
    text = (
        "⏰ <b>Planlı və Dövri Mesajlar (.amsg)</b>\n"
        "──────────────────────────────────────\n"
        "Userbot çata və ya qlobal qruplara təyin olunmuş vaxtlarda avtomatik mesaj göndərir.\n\n"
        "📝 <b>Əsas Əmrlər:</b>\n"
        "• <code>.amsg [vaxt] [mətn]</code> — bu çata planlı mesaj qoyur\n"
        "• <code>.amsg -g [vaxt] [mətn]</code> — 🌐 bütün qruplara planlı mesaj\n"
        "• <code>.amsg [vaxt]</code> (reply) — reply edilən mesajı formatla göndərir\n"
        "• <code>.amsgs</code> — aktiv tapşırıqlar siyahısı və idarəetmə kartı\n"
        "• <code>.amstop [id]</code> — tapşırığı dayandırır və silir\n"
        "• <code>.amnow [id]</code> — tapşırığı indi dərhal göndərir\n\n"
        "⏱ <b>Vaxt nümunələri:</b> <code>30m</code>, <code>2h</code>, <code>1d</code>, <code>09:00</code>, <code>21:30</code>\n"
        "🚩 <b>Bayraqlar:</b> <code>-g</code> qlobal · <code>-l</code> lokal · <code>-once</code> bir dəfəlik · <code>-del</code> köhnəni sil"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        nav_row(back="bots", refresh="ub_amsg"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def filter_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Açar söz avtocavabları (.filter) məlumat və idarəetmə pəncərəsi."""
    text = (
        "🎯 <b>Açar Söz Cavabları və Filtrlər (.filter)</b>\n"
        "──────────────────────────────────────\n"
        "Userbot təyin olunmuş sözlərə, stikerlərə və ya tağlara avtomatik cavab verir.\n\n"
        "📝 <b>Əsas Əmrlər:</b>\n"
        "• <code>.filter [söz] [cavab]</code> — açar sözə avtomatik cavab\n"
        "• <code>.filter -g [söz] [cavab]</code> — 🌐 bütün qruplarda aktiv filtr\n"
        "• <code>.filters</code> — aktiv filtrlər siyahısı və idarəetmə paneli\n"
        "• <code>.stopfilter [söz]</code> — filtri silir\n"
        "• <code>.clearfilters</code> — bu çatdakı bütün filtrləri silir\n"
        "• <code>.tagcavab</code> — 🔔 Tağ cavabı (mention) interaktiv kartı\n"
        "• <code>.fsleep</code> — 😴 Anti-spam və yuxu tənzimləmələri\n\n"
        "🚩 <b>Rejimlər:</b> <code>-e</code> tam uyğun · <code>-c</code> içində · <code>-nr</code> reply-sız · <code>-r</code> yalnız reply"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        nav_row(back="bots", refresh="ub_filter"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def profile_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Userbot profil və ghost rejimi (.profile) məlumat pəncərəsi."""
    text = (
        "🎭 <b>Profil və Hesab Tənzimləmələri (.profile)</b>\n"
        "──────────────────────────────────────\n"
        "Userbot hesabının adını, soyadını, bio və profil şəkillərini idarə edir.\n\n"
        "📝 <b>Əsas Əmrlər:</b>\n"
        "• <code>.profile</code> — profil idarəetmə kartı\n"
        "• <code>.ghost [on/off]</code> — 👻 Görünməz ad rejimi (boşluq xarakteri)\n"
        "• <code>.setname [ad]</code> — profil adını dəyişir\n"
        "• <code>.setbio [bio]</code> — tərcümeyi-halı yeniləyir\n"
        "• <code>.pfp</code> (reply şəkil) — profil şəkli qoyur\n"
        "• <code>.clone [@user]</code> — başqa istifadəçinin profilini kopyalayır\n"
        "• <code>.revert</code> — orijinal profil məlumatlarını bərpa edir"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        nav_row(back="bots", refresh="ub_profile"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def backup_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Userbot bazası və sistem yedəkləməsi (.backup) məlumat pəncərəsi."""
    text = (
        "💾 <b>Userbot Bazası və Yedəkləmə (.backup)</b>\n"
        "──────────────────────────────────────\n"
        "Userbot SQLite məlumat bazasını (filtrlər, tapşırıqlar, ayarlar) idarə edir.\n\n"
        "📝 <b>Əsas Əmrlər:</b>\n"
        "• <code>.backup</code> — cari bazanın nüsxəsini fayl kimi göndərir\n"
        "• <code>.restore</code> (reply .db faylı) — bazanı fayldan bərpa edir\n"
        "• <code>.dbstats</code> — baza həcmi və cədvəllər barədə məlumat\n"
        "• <code>.vacuum</code> — bazanı sıxlaşdırır və artıq həcmi təmizləyir\n\n"
        "ℹ️ <i>Botun əsas bazası ilə yanaşı userbot ayrıca təhlükəsiz SQLite bazasında saxlanılır.</i>"
    )
    if note:
        text += f"\n\nℹ️ {note}"

    rows = [
        [btn("💾 Ümumi Sistem Yedəkləməsi", "bk:panel")],
        nav_row(back="bots", refresh="ub_backup"),
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "amsg_view",
    "backup_view",
    "filter_view",
    "profile_view",
    "userbot_hub_view",
]

