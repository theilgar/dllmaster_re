"""
bot.menus.views.groups
Qruplar, istifadəçilər, kütləvi parametrlər və istifadəçi şəxsi menyusu (umenu).
"""

import asyncio
import logging
import time
from html import escape

logger = logging.getLogger(__name__)

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.menus.navigation import nav_row
from bot.menus.state import is_creator
from common.database import get_db
from common.menu import _btn


def groups_view(ctx=None, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """Auditoriya və qruplar paneli (system_hub.aud_view-in vahid fasadı)."""
    from bot.menus.views.system_hub import aud_view

    return aud_view(note=note, ctx=ctx)


async def user_menu_view(bot, user) -> tuple[str, InlineKeyboardMarkup]:
    """Qeyri-sahib (sadə istifadəçilər) üçün şəxsi PM idarəetmə menyusu."""
    uid = user.id
    name = user.first_name or "İstifadəçi"
    try:
        from bot.plugins.test_mp3_plugin import (
            get_user_bitrate,
            get_user_codec,
        )
        user_codec = get_user_codec(uid)
    except Exception:
        user_codec = "m4a"

    try:
        db = get_db()
        cur = await asyncio.to_thread(db.get_premium, uid)
        is_prm = bool(cur)
    except Exception:
        is_prm = False
    prm_txt = "💎 Premium" if is_prm else "🆓 Standart"

    if user_codec == "mp3":
        mp3_br = get_user_bitrate(uid)
        fmt_txt = f"🎧 MP3 ({mp3_br} kbps)"
    else:
        fmt_txt = "🎵 M4A (Standart)"

    text = (
        f"👋 <b>Xoş gəldiniz, {escape(name)}!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Bu menyudan şəxsi audio tənzimləmələrinizə, qrup idarəetməsinə və botun əsas funksiyalarına daxil ola bilərsiniz.\n\n"
        f"• Hesab Statusu: <b>{prm_txt}</b>\n"
        f"• Audio Formatı: <b>{fmt_txt}</b>\n"
        "• İnline Axtarış: <b>🟢 Aktiv</b>\n\n"
        "<i>Bölmələrdən birini seçin:</i>"
    )

    rows = []
    rows.append([InlineKeyboardButton(text="🎛 Audio formatı (/codec)", callback_data="codec:panel")])

    rows.append([
        InlineKeyboardButton(text="👥 Qruplarım", callback_data="umenu:groups"),
        InlineKeyboardButton(text="💎 Premium", callback_data="umenu:premium"),
    ])
    rows.append([InlineKeyboardButton(text="🔎 Musiqi axtar", switch_inline_query="")])
    rows.append([
        InlineKeyboardButton(text="❓ Əmrlər və bələdçi", callback_data="umenu:help"),
        InlineKeyboardButton(text="🌐 Dil", callback_data="lang:menu"),
    ])
    rows.append([InlineKeyboardButton(text="✖️ Bağla", callback_data="umenu:close")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


_USER_ADMIN_GROUPS_CACHE: dict[int, tuple[float, list[dict]]] = {}


async def get_user_admin_groups(bot, user_id: int) -> list[dict]:
    now = time.time()
    cached = _USER_ADMIN_GROUPS_CACHE.get(user_id)
    if cached and (now - cached[0] < 60):
        return cached[1]

    from common.permissions import is_group_admin, is_group_owner

    def _rows():
        with get_db()._conn() as c:
            return c.execute(
                "SELECT chat_id, title, username FROM chats "
                "WHERE active=1 AND type IN ('group', 'supergroup') "
                "ORDER BY last_seen DESC LIMIT 50"
            ).fetchall()

    try:
        rows = await asyncio.to_thread(_rows)
    except Exception as e:
        logger.warning(f"get_user_admin_groups db xətası: {e}")
        rows = []

    if is_creator(user_id):
        matched = [
            {
                "chat_id": r["chat_id"],
                "title": r["title"] or str(r["chat_id"]),
                "username": r.get("username"),
                "is_owner": True,
            }
            for r in rows
        ]
        _USER_ADMIN_GROUPS_CACHE[user_id] = (now, matched)
        return matched

    async def _check(r):
        try:
            cid = r["chat_id"]
            if await is_group_admin(bot, cid, user_id):
                own = await is_group_owner(bot, cid, user_id)
                return {
                    "chat_id": cid,
                    "title": r["title"] or str(cid),
                    "username": r.get("username"),
                    "is_owner": own,
                }
        except Exception as _e:
            logger.debug("udulan xəta [bot/menus/views/groups.py:130]: %r", _e)
        return None

    results = await asyncio.gather(*[_check(r) for r in rows], return_exceptions=True)
    matched = [r for r in results if isinstance(r, dict) and r is not None]
    _USER_ADMIN_GROUPS_CACHE[user_id] = (now, matched)
    return matched


async def all_groups_settings_view(bot, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """🌐 Bütün qruplar — standart dəyərlər (yeni qruplar da bunu alır) + hər qrupa birdən tətbiq."""
    from bot.core import panel, va_record
    from bot.core.group_panel import (
        LINK_MODE_LABELS,
        MENU_ACCESS_ADMIN,
        MENU_ACCESS_LABELS,
        MENU_ACCESS_SHORT,
        active_groups,
        is_group_menu_enabled,
        is_group_mp3_active,
    )

    rows = active_groups()
    total = len(rows)
    auto = va_record.auto_chats()
    menu_on = sum(1 for r in rows if is_group_menu_enabled(r["chat_id"]))
    mp3_on = sum(1 for r in rows if is_group_mp3_active(r["chat_id"]))
    rec_on = sum(1 for r in rows if int(r["chat_id"]) in auto)
    acc = get_db().get_setting("group_menu:access") or MENU_ACCESS_ADMIN
    lm = get_db().get_setting("group_link:default_mode") or "auto"
    text = (
        "🌐 <b>Bütün qruplar</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• Aktiv qruplar: <b>{total}</b>\n"
        f"• Menyu açıq: <b>{menu_on}/{total}</b> · icazə: {MENU_ACCESS_LABELS.get(acc, acc)}\n"
        f"• MP3: <b>{mp3_on}/{total}</b>\n"
        f"• Avto yayım qeydi: <b>{rec_on}/{total}</b>\n"
        f"• Link rejimi (standart): <b>{LINK_MODE_LABELS.get(lm, lm)}</b>\n\n"
        "<i>Düymə dəyəri bütün qruplara və yeni qoşulanlara tətbiq edir.</i>"
    )
    kb = panel.kb(
        [
            panel.toggle("📋 Menyu", menu_on > 0, "pm_grp:all_menu:toggle"),
            panel.btn(f"👥 İcazə: {MENU_ACCESS_SHORT.get(acc, acc)}", "pm_grp:all_acc:cycle"),
        ],
        [
            panel.toggle("🎧 MP3", mp3_on > 0, "pm_grp:all_mp3:toggle"),
            panel.mode("⏺ Qeyd", rec_on > 0, "pm_grp:all_vrec:toggle"),
        ],
        [
            panel.btn(f"🔗 Link: {LINK_MODE_LABELS.get(lm, lm)}", "pm_grp:all_lm:cycle"),
            panel.btn("👥 Qruplarım", "umenu:groups"),
        ],
        panel.nav(
            "menu:aud",
            "İstifadəçilər",
            refresh_data="pm_grp:all_panel",
            home_data="",
            close_data="umenu:close",
        ),
    )
    return text, kb


async def user_groups_view(bot, user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import panel
    from bot.core.group_panel import is_group_menu_enabled

    groups = await get_user_admin_groups(bot, user_id)
    if not groups:
        text = (
            "👥 <b>Qrup İdarəetməsi (Şəxsi Çat)</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Hazırda sizin admin olduğunuz aktiv qrup tapılmadı.\n\n"
            "• <b>Necə idarə etmək olar?</b>\n"
            "  1. Botu qrupunuza əlavə edin.\n"
            "  2. Bota qrupda admin hüququ verin.\n"
            "  3. Qrupunuz artıq varsa, aşağıdan qrup ID-sini daxil edin.\n"
        )
        kb_rows = []
        if is_creator(user_id):
            kb_rows.append([InlineKeyboardButton(text="🌐 Kütləvi ayarlar", callback_data="pm_grp:all_panel")])
        kb_rows.extend([
            [InlineKeyboardButton(text="➕ Qrup əlavə et", callback_data="pm_grp:input_id")],
            [InlineKeyboardButton(text="🔄 Yenilə", callback_data="umenu:groups_refresh")],
            [InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main")],
            [InlineKeyboardButton(text="✖️ Bağla", callback_data="umenu:close")],
        ])
        return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)

    page_size = 5
    total = len(groups)
    pages = max(1, -(-total // page_size))
    page = min(max(page, 0), pages - 1)
    page_groups = groups[page * page_size : (page + 1) * page_size]

    text = (
        "👥 <b>Qrup İdarəetməsi (Şəxsi Çat)</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Şəxsi çatdan idarə etmək istədiyiniz qrupu seçin:\n\n"
    )
    rows = []
    if is_creator(user_id):
        rows.append([InlineKeyboardButton(text="🌐 Kütləvi ayarlar", callback_data="pm_grp:all_panel")])
    for idx, g in enumerate(page_groups, start=page * page_size + 1):
        cid = g["chat_id"]
        role_badge = "👑 Sahib" if g.get("is_owner") else "👮 Admin"
        menu_st = "🟢 Menyu Açıq" if is_group_menu_enabled(cid) else "🔴 Menyu Bağlı"
        text += f"<b>{idx}.</b> <b>{escape(g['title'][:32])}</b> ({role_badge})\n    └ <i>{menu_st}</i>\n"
        rows.append([InlineKeyboardButton(
            text=f"👥 {g['title'][:25]}",
            callback_data=f"grp:view:{cid}"
        )])

    if pages > 1:
        rows.append(panel.pager("pm_grp:page:", page, pages))

    rows.append([
        InlineKeyboardButton(text="➕ Qrup əlavə et", callback_data="pm_grp:input_id"),
        InlineKeyboardButton(text="🔄 Yenilə", callback_data="umenu:groups_refresh")
    ])
    rows.append([
        InlineKeyboardButton(text="⬅️ İstifadəçilər və qruplar", callback_data="menu:aud")
        if is_creator(user_id)
        else InlineKeyboardButton(text="🏠 Menyu", callback_data="umenu:main"),
        InlineKeyboardButton(text="✖️ Bağla", callback_data="umenu:close"),
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def group_menu_view(bot, chat_id: int, chat_title: str, is_creator_user: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    from bot.core.group_panel import (
        is_group_mp3_active,
        get_group_bitrate,
        get_group_link_mode,
        LINK_MODE_AUTO,
        is_group_menu_enabled,
        get_group_menu_access,
        MENU_ACCESS_LABELS,
        MENU_ACCESS_SHORT,
    )
    active = is_group_mp3_active(chat_id)
    br = get_group_bitrate(chat_id)
    lmode = get_group_link_mode(chat_id)
    lmode_desc = {
        LINK_MODE_AUTO: "🟢 Avtomatik (birbaşa)",
        "confirm": "🟡 Təsdiq ilə",
        "off": "🔴 Söndürülüb",
    }.get(lmode, "🟢 Avtomatik")

    from bot.core import va_record
    auto_rec = va_record.auto_on(chat_id)
    from bot.menus.state import get_context
    ctx = get_context()
    va = getattr(ctx, "voice_assistant", None) if ctx else None
    rec_sess = va.rec.sessions.get(chat_id) if (va and getattr(va, "rec", None)) else None

    if rec_sess:
        from common.utilities import format_clock as fmt_dur
        vrec_desc = f"🔴 Qeyd gedir ({fmt_dur(rec_sess.now())})"
    elif auto_rec:
        vrec_desc = "⚡ Avtomatik (Yayım başladıqda qeyd edir)"
    else:
        vrec_desc = "✋ Manual (Yalnız əmrlə/düymə ilə)"

    menu_enabled = is_group_menu_enabled(chat_id)
    access_mode = get_group_menu_access(chat_id)
    access_label = MENU_ACCESS_LABELS.get(access_mode, "👮 Yalnız Adminlər")
    access_short = MENU_ACCESS_SHORT.get(access_mode, "👮 Adminlər")

    from bot.menus.views.broadcast import get_bot_username
    bot_uname = await get_bot_username(bot)

    text = (
        f"👥 <b>{escape(chat_title)} — Qrup İdarəetmə Menyusu</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Qrupda Aktiv Olan Bütün Ayarlar:</b>\n\n"
        f"• 📋 <b>Menyu Statusu:</b> <b>{'🟢 İcazə verilir (Aktiv)' if menu_enabled else '🔴 Deaktiv (Qadağan)'}</b>\n"
        f"• 👥 <b>Menyu İcazəsi:</b> <b>{access_label}</b>\n"
        f"• 🎧 <b>MP3 Formatı:</b> <b>{'🟢 Aktiv (MP3)' if active else '🔴 Deaktiv (Orijinal m4a)'}</b>\n"
        f"• 🎚 <b>Səs Keyfiyyəti:</b> <b>{br} kbps</b>\n"
        f"• 🔗 <b>Linklə Yükləmə:</b> <b>{lmode_desc}</b>\n"
        f"• 🎙 <b>Yayım Qeydi (vrec):</b> <b>{vrec_desc}</b>\n"
        "• 👮 <b>İdarə Hüququ:</b> 👮 <b>Qrup Adminləri</b>\n\n"
        "<i>Ayarları tənzimləmək üçün aşağıdakı düymələrdən istifadə edin:</i>"
    )

    rows = [
        [InlineKeyboardButton(text="🎧 MP3 və linklər", callback_data=f"gmenu:mp3:{chat_id}")],
        [
            InlineKeyboardButton(text="⏺ Yayım qeydi", callback_data=f"gmenu:vrec:{chat_id}"),
            InlineKeyboardButton(
                text="⏺ Qeyd: 🤖 Avto" if auto_rec else "⏺ Qeyd: ✋ Manual",
                callback_data=f"gmenu:vrec_toggle:{chat_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔘 Menyu: 🟢 Aktiv" if menu_enabled else "🔘 Menyu: 🔴 Deaktiv",
                callback_data=f"gmenu:toggle_enable:{chat_id}"
            ),
            InlineKeyboardButton(
                text=f"👥 İcazə: {access_short}",
                callback_data=f"gmenu:cycle_access:{chat_id}"
            )
        ],
        [
            InlineKeyboardButton(text="⚙️ Ayarların xülasəsi", callback_data=f"gmenu:settings:{chat_id}"),
            InlineKeyboardButton(text="❓ Qrup əmrləri", callback_data=f"gmenu:help:{chat_id}")
        ],
        [
            InlineKeyboardButton(text="🔎 Musiqi axtar", switch_inline_query_current_chat="")
        ],
    ]
    if is_creator_user:
        rows.append([
            InlineKeyboardButton(text="👑 Sahib paneli", url=f"https://t.me/{bot_uname}?start=admin_menu")
        ])
    rows.append([InlineKeyboardButton(text="✖️ Bağla", callback_data="gmenu:close")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def group_settings_view(bot, chat_id: int, chat_title: str) -> tuple[str, InlineKeyboardMarkup]:
    from bot.core.group_panel import (
        is_group_mp3_active,
        get_group_bitrate,
        get_group_link_mode,
        LINK_MODE_AUTO,
        is_group_menu_enabled,
        get_group_menu_access,
        MENU_ACCESS_LABELS,
        MENU_ACCESS_SHORT,
    )
    active = is_group_mp3_active(chat_id)
    br = get_group_bitrate(chat_id)
    lmode = get_group_link_mode(chat_id)
    lmode_desc = {
        LINK_MODE_AUTO: "🟢 Avtomatik (Birbaşa yüklənmə)",
        "confirm": "🟡 Təsdiq ilə (İstifadəçi təsdiq edir)",
        "off": "🔴 Söndürülüb (Linklər yüklənmir)",
    }.get(lmode, "🟢 Avtomatik")

    from bot.core import va_record
    auto_rec = va_record.auto_on(chat_id)
    from bot.menus.state import get_context
    ctx = get_context()
    va = getattr(ctx, "voice_assistant", None) if ctx else None
    rec_sess = va.rec.sessions.get(chat_id) if (va and getattr(va, "rec", None)) else None

    if rec_sess:
        from common.utilities import format_clock as fmt_dur
        vrec_desc = f"🔴 Qeyd gedir ({fmt_dur(rec_sess.now())})"
    elif auto_rec:
        vrec_desc = "⚡ Avtomatik (Yayım başlayan kimi qoşulub qeyd edir)"
    else:
        vrec_desc = "✋ Manual (Yalnız /vrec əmri və ya panel düyməsi ilə)"

    menu_enabled = is_group_menu_enabled(chat_id)
    access_mode = get_group_menu_access(chat_id)
    access_label = MENU_ACCESS_LABELS.get(access_mode, "👮 Yalnız Adminlər")
    access_short = MENU_ACCESS_SHORT.get(access_mode, "👮 Adminlər")

    from common.database import is_userbot_inline_disabled
    ubinl_dis = is_userbot_inline_disabled(chat_id)

    text = (
        f"⚙️ <b>{escape(chat_title)} — Qrup Aktiv Ayarları & İcazələri</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Bu qrupda tətbiq olunmuş cari parametrlər:\n\n"
        "📋 <b>MENYU İCAZƏLƏRİ:</b>\n"
        f"• <b>Qrupda menyu:</b> <b>{'🟢 İcazə verilir' if menu_enabled else '🔴 Deaktivdir (Qadağan)'}</b>\n"
        f"• <b>Kimlər istifadə edə bilər:</b> <b>{access_label}</b>\n\n"
        "🎧 <b>AUDİO VƏ LİNK AYARLARI:</b>\n"
        f"• <b>MP3 Formatı:</b> <b>{'🟢 Aktiv (MP3)' if active else '🔴 Deaktiv (m4a)'}</b>\n"
        f"• <b>Səs Keyfiyyəti:</b> <b>{br} kbps</b>\n"
        f"• <b>Link Yükləmə:</b> <b>{lmode_desc}</b>\n\n"
        "🎙 <b>CANLI YAYIM SƏS QEYDİ:</b>\n"
        f"• <b>Səs Qeydi (vrec):</b> <b>{vrec_desc}</b>\n\n"
        "📱 <b>USERBOT İNLİNE KARTLARI:</b>\n"
        f"• <b>İnline rejimi:</b> <b>{'🔴 Sönülü (Yalnız mətn)' if ubinl_dis else '🟢 Açıq (İnline kartlar)'}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Parametrləri birbaşa aşağıdakı düymələrlə dəyişə bilərsiniz:</i>"
    )

    rows = [
        [
            InlineKeyboardButton(
                text="🔘 Menyu: 🟢 Aktiv" if menu_enabled else "🔘 Menyu: 🔴 Deaktiv",
                callback_data=f"gmenu:toggle_enable_st:{chat_id}"
            ),
            InlineKeyboardButton(
                text=f"👥 İcazə: {access_short}",
                callback_data=f"gmenu:cycle_access_st:{chat_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🎧 MP3: 🟢 Aktiv" if active else "🎧 MP3: 🔴 Deaktiv",
                callback_data=f"gmenu:toggle_mp3_st:{chat_id}"
            ),
            InlineKeyboardButton(
                text=f"🎚 Bitreyt: {br} kbps",
                callback_data=f"gmenu:cycle_br_st:{chat_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"🔗 Link: {lmode}",
                callback_data=f"gmenu:cycle_lm_st:{chat_id}"
            ),
            InlineKeyboardButton(
                text="⏺ Qeyd: 🤖 Avto" if auto_rec else "⏺ Qeyd: ✋ Manual",
                callback_data=f"gmenu:vrec_toggle_st:{chat_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="📱 UB İnline: 🔴 Sönülü" if ubinl_dis else "📱 UB İnline: 🟢 Açıq",
                callback_data=f"gmenu:toggle_ubinl_st:{chat_id}"
            ),
        ],
        [
            InlineKeyboardButton(text="⬅️ Qrup menyusu", callback_data=f"gmenu:back:{chat_id}"),
            InlineKeyboardButton(text="✖️ Bağla", callback_data="gmenu:close"),
        ]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "_USER_ADMIN_GROUPS_CACHE",
    "all_groups_settings_view",
    "get_user_admin_groups",
    "group_menu_view",
    "group_settings_view",
    "groups_view",
    "user_groups_view",
    "user_menu_view",
]
