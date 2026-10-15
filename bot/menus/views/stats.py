"""
bot.menus.views.stats
Statistika, istifadəçi siyahıları, qrafiklər və top mahnılar pəncərələri.
"""

import asyncio
import time
from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.views.base import (
    SOURCE_LABELS,
    bar_chart,
    pct,
    sparkline,
    trend,
    tz_offset_seconds,
)
from common.menu import wrap_ui_text
from common.utilities import get_tz_with_name
from common.kv import next_of


def fmt_until(until: int | None) -> str:
    if not until:
        return "♾ Ömürlük"
    diff = int(until - time.time())
    if diff <= 0:
        return "vaxtı bitib"
    return f"{diff // 86400} gün qalıb ({datetime.fromtimestamp(until, tz=get_tz_with_name()[0]).strftime('%d.%m.%Y')})"


def user_label(u: dict, user_id: int) -> str:
    name = (u or {}).get("first_name") or (u or {}).get("username")
    uname = f"@{u['username']}" if (u or {}).get("username") else ""
    if name and uname:
        return f'<a href="tg://user?id={user_id}">{escape(name)}</a> ({escape(uname)})'
    if name:
        return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'
    return f"<code>{user_id}</code>"


async def stats_view(view: str = "stats", db=None) -> tuple[str, InlineKeyboardMarkup]:
    if not isinstance(view, str) and hasattr(view, "stats_overview"):
        # Legacy positional caller passed (db, view)
        db, view = view, (db or "stats")
    if db is None:
        from common.database import get_db
        db = get_db()
    if not isinstance(view, str):
        view = "stats"

    now = int(time.time())
    tz, tz_name = get_tz_with_name()
    now_dt = datetime.now(tz)
    day_start = int(datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=tz).timestamp())
    st = await asyncio.to_thread(db.stats_overview, day_start, now, tz_offset_seconds())
    dl = st.get("downloads") or st.get("dl") or {"today": 0, "week": 0, "prev_week": 0, "month": 0, "total": 0}
    du = st.get("dl_users") or {"today": 0, "week": 0, "month": 0}
    us_ = st.get("users") or {"total": 0, "blocked": 0, "new_today": 0, "new_week": 0, "active_today": 0, "active_week": 0}
    cc = st.get("chats") or {"groups": 0, "channels": 0}
    pc = st.get("premium_count", 0)
    grp_dl = st.get("group_downloads", 0)

    # tək düymə: hər basışda 📊 Ümumi → 📈 Qrafik → 🏆 Top → 📊 ... (tab sırası əvəzinə)
    order = ("stats", "stats_chart", "stats_top")
    names = {"stats": "📊 Ümumi", "stats_chart": "📈 Qrafik", "stats_top": "🏆 Top"}
    cur = view if view in order else "stats"
    nxt = next_of(order, cur)
    tabs = [[panel.btn(f"{names[cur]}  ›  {names[nxt]}", f"menu:{nxt}")], nav_row("aud", refresh=cur)]

    if cur == "stats":
        src_total = sum(st["sources"].values())
        src_lines = "\n".join(
            f"   {SOURCE_LABELS.get(k, k)}: <b>{v}</b> ({pct(v, src_total)})" for k, v in st["sources"].items()
        ) or "   <i>məlumat yoxdur</i>"
        text = (
            "📊 <b>Statistika</b>\n\n"
            "🎵 <b>Yükləmələr</b>\n"
            f"   Bu gün: <b>{dl['today']}</b> · {du['today']} nəfər\n"
            f"   Son 7 gün: <b>{dl['week']}</b> · {du['week']} nəfər{trend(dl['week'], dl['prev_week'])}\n"
            f"   Son 30 gün: <b>{dl['month']}</b> · {du['month']} nəfər\n"
            f"   Gündəlik orta (30 gün): <b>{dl['month'] / 30:.1f}</b>\n"
            f"   Cəmi: <b>{dl['total']}</b>\n\n"
            "👥 <b>İstifadəçilər</b>\n"
            f"   Cəmi: <b>{us_['total']}</b> · 🚫 bloklayan {us_['blocked']} ({pct(us_['blocked'], us_['total'])})\n"
            f"   🆕 Yeni: bu gün <b>{us_['new_today']}</b> · 7 gün <b>{us_['new_week']}</b>\n"
            f"   🟢 Aktiv: bu gün <b>{us_['active_today']}</b> · 7 gün <b>{us_['active_week']}</b>\n"
            f"   💎 Premium: <b>{pc}</b> · 🆓 Free: <b>{max(us_['total'] - pc, 0)}</b>\n\n"
            f"💬 <b>Çatlar:</b> 👥 {cc['groups']} qrup · 📣 {cc['channels']} kanal\n"
            f"   Qruplarda yükləmə (30 gün): <b>{grp_dl}</b> ({pct(grp_dl, dl['month'])})\n\n"
            f"📥 <b>Mənbələr (30 gün)</b>\n{src_lines}\n\n"
            f"<i>🕒 {now_dt.strftime('%d.%m.%Y %H:%M')} · {escape(tz_name)}</i>"
        )
        return text, InlineKeyboardMarkup(inline_keyboard=tabs)

    if cur == "stats_chart":
        days = len(st["daily_dl"])
        labels = [datetime.fromtimestamp(st["chart_start"] + i * 86400, tz).strftime("%d.%m") for i in range(days)]
        hours = st["hours"]
        text = (
            f"📈 <b>Qrafik — son {days} gün</b>\n\n"
            f"🎵 <b>Yükləmələr</b> (cəmi {sum(st['daily_dl'])})\n"
            f"{wrap_ui_text(bar_chart(labels, st['daily_dl']))}\n"
            f"🆕 <b>Yeni istifadəçilər</b> (cəmi {sum(st['daily_new'])})\n"
            f"{wrap_ui_text(bar_chart(labels, st['daily_new']))}\n"
            "🕒 <b>Saatlara görə yükləmə (30 gün)</b>\n"
            f"{wrap_ui_text('00    06    12    18   23' + chr(10) + sparkline(hours))}"
        )
        return text, InlineKeyboardMarkup(inline_keyboard=tabs)

    # stats_top
    song_lines = []
    for i, sg in enumerate(st["top_songs"], 1):
        title = escape((sg["title"] or "—")[:45])
        song_lines.append(f"{i}. {title} — <b>{sg['n']}</b> dəfə · {sg['users']} nəfər")
    user_lines = []
    for i, u in enumerate(st["top_users"], 1):
        user_lines.append(f"{i}. {user_label(u, u['user_id'])} — <b>{u['n']}</b> mahnı")
    text = (
        "🏆 <b>Top — son 30 gün</b>\n\n"
        "🎵 <b>Ən çox yüklənən mahnılar:</b>\n" + ("\n".join(song_lines) or "<i>məlumat yoxdur</i>") + "\n\n"
        "👤 <b>Ən aktiv istifadəçilər:</b>\n" + ("\n".join(user_lines) or "<i>məlumat yoxdur</i>")
    )
    return text, InlineKeyboardMarkup(inline_keyboard=tabs)


__all__ = [
    "fmt_until",
    "stats_view",
    "user_label",
]
