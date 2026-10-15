"""
bot.menus.views.premium_stars
Premium idarəetməsi, Telegram Stars qiymət və ödəniş panelləri.
"""

import asyncio
import logging
import re
import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel
from bot.core.stars import (
    MAX_STARS,
    MIN_STARS,
    PLAN_MAP,
    PLANS,
    buy_keyboard,
    get_price,
    per_month,
    set_price,
)
from bot.menus.navigation import nav_row
from bot.menus.views.base import (
    PREMIUM_DURATIONS,
    PREMIUM_PAGE_SIZE,
    SECTIONS,
    crumb,
    display_name,
    fmt_dt,
)
from bot.menus.views.stats import fmt_until, user_label
from common.database import get_db
from common.menu import _btn

logger = logging.getLogger(__name__)


def short_name(u: dict, user_id: int) -> str:
    if not u:
        return str(user_id)
    n = (u.get("first_name") or "").strip()
    return (n[:18] + "…") if len(n) > 18 else (n or str(user_id))


def parse_user_ref(text: str):
    """
    "123456789" → ("id", 123456789)
    "@name" / "name" / "t.me/name" → ("username", "name")
    """
    t = (text or "").strip()
    t = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", t, flags=re.IGNORECASE).strip("/ ")
    if re.fullmatch(r"\d{1,20}", t):
        return "id", int(t)
    t = t.lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", t):
        return "username", t
    return None, None


async def premium_panel(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    db = get_db()
    total = await asyncio.to_thread(db.premium_count)
    pages = max(1, -(-total // PREMIUM_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    rows = await asyncio.to_thread(db.premium_page, page * PREMIUM_PAGE_SIZE, PREMIUM_PAGE_SIZE)

    if rows:
        lines = [
            f"{i}. 💎 {user_label(r, r['user_id'])} · {fmt_until(r['until'])}"
            for i, r in enumerate(rows, start=page * PREMIUM_PAGE_SIZE + 1)
        ]
        body = "\n".join(lines) + "\n\n<i>Premiumu almaq üçün aşağıda istifadəçiyə bas.</i>"
    else:
        body = "<i>Hələ premium istifadəçi yoxdur.</i>"
    text = crumb(SECTIONS["biz"], "💎 Siyahı") + f" — <b>{total}</b>\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"

    kb = [[_btn(f"🗑 {short_name(r, r['user_id'])}", f"prm_rm:{r['user_id']}:{page}")] for r in rows]
    if pages > 1:
        kb.append(panel.pager("menu:prm:", page, pages, "menu:noop"))
    kb.append(nav_row("biz"))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def duration_panel(user_id: int, u: dict, back: str = "prm:0") -> tuple[str, InlineKeyboardMarkup]:
    cur = await asyncio.to_thread(get_db().get_premium, user_id)
    status = f"💎 Premium ({fmt_until(cur['until'])})" if cur else "🆓 Free"
    note = "" if u else "\n<i>⚠️ Bu ID botda qeydiyyatda deyil (heç /start etməyib).</i>"
    extend = (
        "\n<i>Aktiv premium var — seçilən müddət üstünə əlavə olunacaq.</i>"
        if cur and cur["until"]
        else ""
    )
    text = (
        "💎 <b>Premium ver</b>\n\n"
        f"👤 {user_label(u, user_id)}\n"
        f"📌 Hazırkı status: {status}{note}{extend}\n\n"
        "<b>Müddəti seçin:</b>"
    )
    btns = [_btn(label, f"prm_g:{user_id}:{days}") for label, days in PREMIUM_DURATIONS]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            btns[:2],
            btns[2:4],
            btns[4:],
            nav_row(back),
        ]
    )
    return text, kb


def plan_line(key, days, label):
    price = get_price(key)
    if not price:
        return f"{escape(label)}: <i>satışda deyil</i>"
    return f"{escape(label)}: <b>{price}⭐</b>{per_month(days, price)}"


async def stars_panel(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    now = int(time.time())
    summ = await asyncio.to_thread(get_db().payments_summary, now)
    lines = "\n".join(plan_line(k, d, l) for k, d, l in PLANS)
    on_sale = sum(1 for k, _, _ in PLANS if get_price(k))
    text = (
        crumb(SECTIONS["biz"], "⭐ Ulduz satışı") + "\n\n"
        f"📋 <b>Planlar</b> ({on_sale}/{len(PLANS)} satışda)\n{lines}\n\n"
        "💰 <b>Gəlir</b>\n"
        f"   Cəmi: <b>{summ['stars']}⭐</b> · {summ['n']} ödəniş · {summ['buyers']} alıcı\n"
        f"   Son 30 gün: <b>{summ['stars_30d']}⭐</b> · {summ['n_30d']} ödəniş\n"
        + (
            f"   💝 O cümlədən bəxşiş: <b>{summ['tips_stars']}⭐</b> · {summ['tips_n']} dəfə\n"
            if summ.get("tips_n")
            else ""
        )
        + (
            f"   ↩️ Geri qaytarılıb: {summ['refunded_n']} ({summ['refunded_stars']}⭐)\n"
            if summ["refunded_n"]
            else ""
        )
        + (
            "\n<i>İstifadəçilər /premium yazanda satışdakı planları görüb ulduzla ala bilər.</i>"
            if on_sale
            else "\n⚠️ <i>Heç bir plan satışda deyil — qiymət təyin et.</i>"
        )
        + (f"\n\n{note}" if note else "")
    )
    rows = [[_btn(f"{'⭐' if get_price(k) else '▫️'} {l}", f"st_plan:{k}")] for k, _, l in PLANS]
    rows.append(nav_row("biz"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def stars_plan_panel(key: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    days, label = PLAN_MAP[key]
    price = get_price(key)
    text = (
        f"⭐ <b>{escape(label)}</b>\n\n"
        f"⏳ Müddət: <b>{'ömürlük' if days is None else f'{days} gün'}</b>\n"
        f"💰 Qiymət: <b>{f'{price}⭐' if price else 'satışda deyil'}</b>{per_month(days, price) if price else ''}"
        + (f"\n\n{note}" if note else "")
    )
    rows = []
    if price:
        rows.append(
            [_btn("✏️ Qiyməti dəyiş", f"st_set:{key}"), _btn("🚫 Satışdan çıxar", f"st_off:{key}")]
        )
    else:
        rows.append(
            [
                _btn("✏️ Qiymət yaz və satışa qoy", f"st_set:{key}"),
            ]
        )
    rows.append(nav_row("st"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def payments_view(page: int) -> tuple[str, InlineKeyboardMarkup]:
    per = 8
    rows, total = await asyncio.to_thread(get_db().payments_page, page * per, per)
    pages = max(1, -(-total // per))
    page = min(max(page, 0), pages - 1)
    if page and not rows:
        rows, total = await asyncio.to_thread(get_db().payments_page, page * per, per)
    if rows:
        lines = []
        for r in rows:
            label = "💝 Bəxşiş" if r["plan"] == "tip" else PLAN_MAP.get(r["plan"], (None, r["plan"]))[1]
            mark = " ↩️ <i>qaytarılıb</i>" if r["refunded"] else ""
            lines.append(
                f"#{r['id']} · {fmt_dt(r['ts'])} · <b>{r['stars']}⭐</b> · {escape(label)}\n"
                f"    {user_label(r, r['user_id'])}{mark}"
            )
        body = "\n".join(lines)
    else:
        body = "<i>Hələ ödəniş yoxdur.</i>"
    text = f"🧾 <b>Ödənişlər</b> (<b>{total}</b>)\n<i>Səhifə {page + 1}/{pages}</i>\n\n{body}"
    kb = [
        [
            _btn(
                f"{'↩️' if r['refunded'] else '🧾'} #{r['id']} · {r['stars']}⭐ · {display_name(r, r['user_id'])[:16]}",
                f"st_p:{r['id']}:{page}",
            )
        ]
        for r in rows
    ]
    if pages > 1:
        kb.append(panel.pager("menu:st_pay:", page, pages, "menu:noop"))
    kb.append(nav_row("biz"))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def payment_card(pid: int, page: int, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    r = await asyncio.to_thread(get_db().get_payment, pid)
    if not r:
        return "❌ Ödəniş tapılmadı.", InlineKeyboardMarkup(inline_keyboard=[nav_row(f"st_pay:{page}")])
    label = "💝 Bəxşiş" if r["plan"] == "tip" else PLAN_MAP.get(r["plan"], (None, r["plan"]))[1]
    prem = await asyncio.to_thread(get_db().get_premium, r["user_id"])
    text = (
        f"🧾 <b>Ödəniş #{r['id']}</b>\n\n"
        f"👤 {user_label(r, r['user_id'])}\n"
        f"💎 Plan: <b>{escape(label)}</b> · <b>{r['stars']}⭐</b>\n"
        f"🕒 {fmt_dt(r['ts'])}\n"
        f"🔖 <code>{escape(r['charge_id'] or '—')}</code>\n"
        f"📌 İstifadəçinin hazırkı statusu: {('💎 ' + fmt_until(prem['until'])) if prem else '🆓 Free'}\n"
        + (f"↩️ <b>Geri qaytarılıb</b> — {fmt_dt(r['refund_ts'])}" if r["refunded"] else "")
        + (f"\n\n{note}" if note else "")
    )
    kb = []
    if not r["refunded"] and r.get("charge_id"):
        kb.append([_btn("↩️ Ulduzları geri qaytar", f"st_ref:{pid}:{page}")])
    kb.append([_btn("👤 İstifadəçi kartı", f"user:{r['user_id']}:st_p:{pid}:{page}")])
    kb.append(nav_row(f"st_pay:{page}"))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


__all__ = [
    "MAX_STARS",
    "MIN_STARS",
    "PLANS",
    "PLAN_MAP",
    "buy_keyboard",
    "duration_panel",
    "get_price",
    "parse_user_ref",
    "payment_card",
    "payments_view",
    "per_month",
    "plan_line",
    "premium_panel",
    "set_price",
    "short_name",
    "stars_panel",
    "stars_plan_panel",
]
