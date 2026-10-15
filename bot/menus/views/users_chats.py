"""
bot.menus.views.users_chats
İstifadəçi və qrup/kanal detalları, siyahıları, kartları və canlı çat məlumatları.
"""

import asyncio
import logging
import time
from html import escape

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context, is_creator
from bot.menus.views.base import (
    BOT_STATUS,
    CHAT_FILTERS,
    CHAT_TYPES,
    SOURCE_LABELS,
    USER_FILTERS,
    USERS_PAGE,
    ago,
    display_name,
    fmt_date,
    fmt_dt,
)
from bot.menus.views.stats import fmt_until, user_label
from common.database import get_db
from common.menu import _btn

logger = logging.getLogger(__name__)


def users_list_kb(rows, flt: str, page: int, pages: int, counts: dict) -> InlineKeyboardMarkup:
    back = f"users:{flt}:{page}"
    kb = []
    for u in rows:
        marks = ("💎" if u["premium"] else "") + ("🚫" if u["blocked"] else "")
        kb.append(
            [
                _btn(
                    f"{marks}{' ' if marks else ''}{display_name(u)[:22]} · 🎵{u['dl_total']}",
                    f"user:{u['user_id']}:{back}",
                )
            ]
        )
    if pages > 1:
        kb.append(panel.pager(f"menu:users:{flt}:", page, pages, "menu:noop"))
    f_btns = [
        panel.choice(
            f"{label} {counts.get(key, 0)}",
            key == flt,
            f"menu:users:{key}:0",
        )
        for key, label in USER_FILTERS
    ]
    kb += [f_btns[0:3], f_btns[3:6]]
    kb.append(nav_row("aud"))
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def users_view(flt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    now = int(time.time())
    db = get_db()
    counts = await asyncio.to_thread(db.filter_counts, now)
    total = counts.get(flt, 0)
    pages = max(1, -(-total // USERS_PAGE))
    page = min(max(page, 0), pages - 1)
    rows, total = await asyncio.to_thread(
        db.users_filtered, flt, page * USERS_PAGE, USERS_PAGE, now
    )
    label = dict(USER_FILTERS).get(flt, flt)

    if rows:
        lines = []
        for i, u in enumerate(rows, start=page * USERS_PAGE + 1):
            marks = ("💎 " if u["premium"] else "") + ("🚫 " if u["blocked"] else "")
            lines.append(
                f"{i}. {marks}{user_label(u, u['user_id'])}\n"
                f"    🎵 {u['dl_total']} · 🆕 {fmt_date(u['first_seen'])} · 🕒 {ago(u['last_seen'], now)}"
            )
        body = "\n".join(lines) + "\n\n<i>Ətraflı məlumat üçün istifadəçiyə bas.</i>"
    else:
        body = "<i>Bu siyahıda heç kim yoxdur.</i>"
    hint = {
        "active": "son 7 gündə botla işləyənlər",
        "new": "son 7 gündə qoşulanlar",
        "top": "ən çox mahnı yükləyənlər",
    }.get(flt)
    text = (
        f"👥 <b>İstifadəçilər — {label}</b> (<b>{total}</b>)"
        + (f"\n<i>{hint}</i>" if hint else "")
        + f"\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"
    )
    return text, users_list_kb(rows, flt, page, pages, counts)


async def chats_view(flt: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """💬 Qrup / kanal siyahısı — filtr + səhifələmə (users_view ilə eyni quruluş)."""
    now = int(time.time())
    db = get_db()
    if flt not in dict(CHAT_FILTERS):
        flt = "groups"
    counts = await asyncio.to_thread(db.chat_filter_counts)
    pages = max(1, -(-counts.get(flt, 0) // USERS_PAGE))
    page = min(max(page, 0), pages - 1)
    rows, total = await asyncio.to_thread(db.chats_filtered, flt, page * USERS_PAGE, USERS_PAGE, now)
    pages = max(1, -(-total // USERS_PAGE))
    label = dict(CHAT_FILTERS).get(flt, flt)

    back = f"grp:{flt}:{page}"
    kb = []
    if rows:
        lines = []
        for i, r in enumerate(rows, start=page * USERS_PAGE + 1):
            title = escape((r.get("title") or str(r["chat_id"]))[:40])
            kind = CHAT_TYPES.get(r.get("type"), r.get("type") or "")
            lines.append(
                f"{i}. <b>{title}</b> · {kind}\n"
                f"    🎵 {r['dl_total']} (30 gün: {r['dl_30d']}) · 🕒 {ago(r['last_seen'], now)}"
            )
            kb.append([_btn(f"{(r.get('title') or str(r['chat_id']))[:26]} · 🎵{r['dl_30d']}",
                            f"chat:{r['chat_id']}:{back}")])
        body = "\n".join(lines) + "\n\n<i>Ətraflı məlumat üçün çata bas.</i>"
    else:
        body = "<i>Bu siyahıda heç nə yoxdur.</i>"
    if pages > 1:
        kb.append(panel.pager(f"menu:grp:{flt}:", page, pages, "menu:noop"))
    kb.append([
        panel.choice(f"{lbl} {counts.get(key, 0)}", key == flt, f"menu:grp:{key}:0")
        for key, lbl in CHAT_FILTERS
    ])
    kb.append(nav_row("aud", refresh=back))
    text = f"💬 <b>{label}</b> (<b>{total}</b>)\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def user_card(uid: int, back: str) -> tuple[str, InlineKeyboardMarkup]:
    now = int(time.time())
    db = get_db()
    d = await asyncio.to_thread(db.user_detail, uid, now)
    prem = await asyncio.to_thread(db.get_premium, uid)
    u, dl = d["user"], d["dl"]

    if not u and not dl["total"]:
        return (
            f"❌ <code>{uid}</code> bazada tapılmadı.",
            InlineKeyboardMarkup(inline_keyboard=[nav_row(back)]),
        )

    if is_creator(uid):
        role = "👑 Creator"
    elif prem:
        role = f"💎 Premium ({fmt_until(prem['until'])})"
    else:
        role = "🆓 Free"
    lines = [f"👤 <b>{escape(display_name(u, uid))}</b>"]
    if u and u.get("username"):
        lines.append(f"🔗 @{escape(u['username'])}")
    lines += [
        f'🆔 <code>{uid}</code> · <a href="tg://user?id={uid}">profil</a>',
        f"🏷 Status: <b>{role}</b>",
    ]
    if u:
        state = "🚫 botu bloklayıb" if u["blocked"] else "✅ aktiv"
        lines += [
            f"📶 Vəziyyət: {state}",
            f"🆕 Qoşulub: {fmt_dt(u['first_seen'])} ({ago(u['first_seen'], now)})",
            f"🕒 Son aktivlik: {fmt_dt(u['last_seen'])} ({ago(u['last_seen'], now)})",
        ]
    else:
        lines.append("<i>ℹ️ Botu şəxsi çatda başlatmayıb (yalnız inline/qrup istifadəsi)</i>")

    lines.append("\n🎵 <b>Yükləmələr</b>")
    if dl["total"]:
        rank = f" · reytinq: <b>#{d['rank']}</b>" if d.get("rank") else ""
        lines += [
            f"   Cəmi: <b>{dl['total']}</b>{rank}",
            f"   Son 7 gün: <b>{dl['week']}</b> · son 30 gün: <b>{dl['month']}</b>",
            f"   İlk: {fmt_dt(dl['first_ts'])} · son: {ago(dl['last_ts'], now)}",
            "   Mənbə: "
            + " · ".join(f"{SOURCE_LABELS.get(k, k)} {v}" for k, v in d["sources"].items()),
            "\n🕘 <b>Son mahnılar</b>",
        ]
        for r in d["recent"]:
            title = escape((r["title"] or "—")[:45])
            link = f'<a href="{escape(r["url"], quote=True)}">{title}</a>' if r.get("url") else title
            lines.append(f"   • {link} <i>({ago(r['ts'], now)})</i>")
    else:
        lines.append("   <i>Hələ mahnı yükləməyib</i>")

    kb = []
    if not is_creator(uid):
        if prem:
            kb.append([_btn("🗑 Premiumu al", f"prm_rm:{uid}:0")])
        else:
            kb.append([_btn("💎 Premium ver", f"prm_gv:{uid}:{back}")])
        if u and not u["blocked"]:
            kb.append([_btn("☕ Bəxşiş mesajı göndər", f"tipmsg:u:{uid}:{back}")])
    kb.append(nav_row(back))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def chat_live(chat_id: int) -> dict:
    """Telegram-dan canlı məlumat: ad, üzv sayı, botun statusu və yetkiləri."""
    ctx = get_context()
    bot = getattr(ctx, "bot", None)
    if not bot:
        return {}
    info = {}
    try:
        ch = await bot.get_chat(chat_id)
        info["title"], info["username"] = ch.title, getattr(ch, "username", None)
        info["type"] = str(getattr(ch, "type", "") or "")
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        info["gone"] = str(e)
        await asyncio.to_thread(get_db().set_chat_active, chat_id, False)
        return info
    except Exception as e:
        info["error"] = str(e)
        return info
    try:
        info["members"] = await bot.get_chat_member_count(chat_id)
    except Exception as _e:
        logger.debug("chat_live get_chat_member_count xətası: %r", _e)
    try:
        me = await bot.me()
        m = await bot.get_chat_member(chat_id, me.id)
        info["status"] = str(getattr(m.status, "value", m.status))
        info["can_delete"] = bool(getattr(m, "can_delete_messages", False))
        info["can_pin"] = bool(getattr(m, "can_pin_messages", False))
        info["can_post"] = bool(getattr(m, "can_post_messages", False))
    except Exception as _e:
        logger.debug("chat_live get_chat_member xətası: %r", _e)
    await asyncio.to_thread(
        get_db().update_chat_info,
        chat_id,
        info.get("title"),
        info.get("username"),
        info.get("members"),
    )
    return info


async def chat_card(chat_id: int, back: str, live: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    now = int(time.time())
    db = get_db()
    row = await asyncio.to_thread(db.get_chat, chat_id)
    if not row:
        return (
            f"❌ <code>{chat_id}</code> bazada tapılmadı.",
            InlineKeyboardMarkup(inline_keyboard=[nav_row(back)]),
        )
    info = await chat_live(chat_id) if live else {}
    if info.get("title") or info.get("gone"):
        row = await asyncio.to_thread(db.get_chat, chat_id)
    d = await asyncio.to_thread(db.chat_detail, chat_id, now)
    dl = d["dl"]
    is_channel = row["type"] == "channel"

    lines = [f"{'📣' if is_channel else '👥'} <b>{escape(row['title'] or str(chat_id))}</b>"]
    if row.get("username"):
        lines.append(f'🔗 <a href="https://t.me/{escape(row["username"])}">@{escape(row["username"])}</a>')
    lines.append(f"🆔 <code>{chat_id}</code> · {CHAT_TYPES.get(row['type'], row['type'])}")

    if info.get("gone"):
        lines.append('⛔ <b>Bot artıq bu çatda deyil</b> <i>(siyahıda "Çıxılmış" kimi qeyd olundu)</i>')
    elif info.get("error"):
        lines.append(f"⚠️ Canlı məlumat alınmadı: <i>{escape(info['error'][:120])}</i>")
    else:
        status = info.get("status")
        if status:
            s_line = f"🤖 Bot: <b>{BOT_STATUS.get(status, status)}</b>"
            if status == "administrator":
                if is_channel:
                    s_line += f" · ✍️ post {'✅' if info.get('can_post') else '❌'}"
                else:
                    s_line += (
                        f" · 🗑 {'✅' if info.get('can_delete') else '❌'}"
                        f" · 📌 {'✅' if info.get('can_pin') else '❌'}"
                    )
            lines.append(s_line)
    members = info.get("members", d["members"])
    if members is not None:
        lines.append(
            f"👤 Üzv sayı: <b>{members}</b>"
            + ("" if "members" in info else " <i>(son yoxlama)</i>")
        )
    lines += [
        f"📅 Qeydə alınıb: {fmt_dt(row['first_seen'])} ({ago(row['first_seen'], now)})",
        f"🕒 Son aktivlik: {ago(row['last_seen'], now)}",
        f"📶 Vəziyyət: {'✅ aktiv' if row['active'] else '🚪 bot çıxıb'}",
    ]

    if not is_channel:
        lines.append("\n🎵 <b>Yükləmələr</b>")
        if dl["total"]:
            lines += [
                f"   Cəmi: <b>{dl['total']}</b> · 7 gün <b>{dl['week']}</b> · 30 gün <b>{dl['month']}</b>",
                f"   Yükləyən: <b>{dl['users']}</b> nəfər · son: {ago(dl['last_ts'], now)}",
                "\n🏆 <b>Ən aktiv üzvlər</b>",
            ]
            medals = ["🥇", "🥈", "🥉"]
            for i, u in enumerate(d["top_users"], 1):
                mark = medals[i - 1] if i <= 3 else f"{i}."
                lines.append(f"   {mark} {user_label(u, u['user_id'])} — <b>{u['n']}</b>")
            lines.append("\n🕘 <b>Son mahnılar</b>")
            for r in d["recent"]:
                title = escape((r["title"] or "—")[:45])
                link = f'<a href="{escape(r["url"], quote=True)}">{title}</a>' if r.get("url") else title
                lines.append(f"   • {link} <i>({ago(r['ts'], now)})</i>")
        else:
            lines.append("   <i>Bu qrupda hələ /music ilə mahnı yüklənməyib</i>")

    me_back = f"chat:{chat_id}:{back}"
    kb = []
    user_btns = [
        _btn(f"👤 {display_name(u, u['user_id'])[:16]}", f"user:{u['user_id']}:{me_back}")
        for u in d["top_users"][:4]
    ]
    user_btns = [b for b in user_btns if len(b.callback_data.encode()) <= 64]
    if user_btns:
        kb += [user_btns[i : i + 2] for i in range(0, len(user_btns), 2)]
    if row["active"] and not info.get("gone"):
        kb.append([_btn("🚪 Çatdan çıx", f"chat_leave:{chat_id}:{back}")])
    if chat_id < 0 and row["active"] and not info.get("gone"):
        kb.append([InlineKeyboardButton(text="⚙️ Qrup ayarları", callback_data=f"grp:view:{chat_id}")])
        tip_btn = _btn("☕ Bəxşiş mesajı göndər", f"tipmsg:c:{chat_id}:{back}")
        if not is_channel and len(tip_btn.callback_data.encode()) <= 64:
            kb.append([tip_btn])
    ctx = get_context()
    if chat_id < 0 and len(f"hg:v:{chat_id}:{back}") <= 64:
        pool = getattr(ctx, "depo_helpers", None)
        n_sel = len(pool.group_selected(chat_id)) if pool else 0
        kb.append(
            [
                InlineKeyboardButton(
                    text="🤖 Köməkçi botlar" + (f" · {n_sel}" if n_sel else ""),
                    callback_data=f"hg:v:{chat_id}:{back}",
                )
            ]
        )
    kb.append(nav_row(back, refresh=f"chat:{chat_id}:{back}"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


__all__ = [
    "chat_card",
    "chat_live",
    "user_card",
    "users_list_kb",
    "users_view",
]
