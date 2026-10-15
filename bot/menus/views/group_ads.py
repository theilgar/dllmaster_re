"""
bot.menus.views.group_ads
📣 Qrup reklamı paneli (🎙 Canlı Yayım & VC): qrup seçimi, fasilə / gözləmə / limit, hesab, mətn, canlı gedişat.
"""
from __future__ import annotations

import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import LINE, SECTIONS, crumb
from common import group_ads as ads
from common.kv import next_of

PER_PAGE = 8
SLOT_FALLBACK = ["🥇", "🥈", "🥉"]


def _runner():
    return getattr(get_context(), "group_ads", None)


def _fmt_dur(sec: int) -> str:
    return f"{sec} san." if sec < 120 else f"{sec // 60} dəq."


def _slot_label(runner, value: str) -> str:
    if value == "all":
        return "hamısı"
    for sl in runner.slots() if runner else []:
        if str(sl.idx) == value:
            return f"{sl.icon} @{getattr(getattr(sl, 'me', None), 'username', None) or sl.idx}"
    return f"#{value}"


def _next_slot_label(runner, cur: str) -> str:
    idx = runner.slot_indexes() if runner else []
    order = ["all"] + [str(i) for i in idx]
    nxt = next_of(order, cur, "all")
    return _slot_label(runner, nxt)


def ads_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    runner = _runner()
    s = ads.settings()
    sel = ads.selected()
    cat = runner._catalog[1] if runner else []
    st = runner.state if runner else {}
    running = bool(st.get("running"))                      # dövrün özünün bayrağı (task bitməmiş son yeniləmədə də düzgündür)
    accounts = runner.slots() if runner else []
    ready = len(runner.targets(cat)) if runner and cat else 0
    cooling = sum(1 for g in cat if g["id"] in sel and ads.on_cooldown(g["id"], s["cooldown"]))

    lines = [crumb(SECTIONS["live"], "📣 Qrup reklamı"), LINE,
             "🎙 Canlı yayım userbotu seçilmiş <b>qruplara</b> botun reklam mesajını göndərir "
             "(şəxslərə mesaj yoxdur).",
             ""]
    lines.append("🎙 Hesab: " + (", ".join(f"{sl.icon} @{getattr(getattr(sl, 'me', None), 'username', None) or sl.idx}"
                                          for sl in accounts) if accounts else "<b>❌ işləyən asistan yoxdur</b>"))
    lines.append(f"👥 Seçilmiş qruplar: <b>{len(sel)}</b>"
                 + (f" · siyahıda <b>{len(cat)}</b> qrup · indi göndəriləcək: <b>{ready}</b>" if cat else
                    " · <i>siyahı yüklənməyib — 👥 Qruplar</i>")
                 + (f" · ⏳ gözləmədə: {cooling}" if cooling else ""))
    lines.append(f"⏱ Fasilə: <b>~{_fmt_dur(s['delay'])}</b> · 🔁 Qrupa təkrar: <b>{s['cooldown']} saat</b> · "
                 f"🔢 Dövr limiti: <b>{s['max']}</b> · hesab: <b>{_slot_label(runner, s['slot'])}</b>")
    lines.append(f"📝 Mətn: <b>{'✏️ sənin mətnin' if s['text'] else 'standart'}</b>")

    if running:
        lines += ["", f"▶️ <b>Gedir:</b> {st['done']}/{st['total']} · ✅ {st['sent']} · ⏭ {st['skipped']} · ❌ {st['failed']}",
                  f"📍 Hazırda: {escape(st.get('current') or '—')}"]
    else:
        r = ads.last_report()
        if r:
            lines += ["", f"🕘 Son dövr ({time.strftime('%d.%m %H:%M', time.localtime(r.get('ts', 0)))}): "
                          f"✅ {r.get('sent', 0)} · ⏭ {r.get('skipped', 0)} · ❌ {r.get('failed', 0)}"
                          + (" · ⏹ dayandırılıb" if r.get("stopped") else "")
                          + (f"\n⚠️ {escape(r['aborted'])}" if r.get("aborted") else "")]
    lines += ["", "<i>⚠️ Çox tez / çox qrupa göndərmək hesabı Telegram-da məhdudlaşdıra bilər — defolt ayarlar mülayimdir. "
                  "Qrupların qaydalarına riayət et.</i>"]
    if note:
        lines.append(f"\nℹ️ {note}")

    rows = []
    if running:
        rows.append([panel.btn("⏹ Dayandır", "menu:ads:stop")])
    else:
        rows.append([panel.btn(f"▶️ Başlat ({ready} qrup)" if cat else "▶️ Başlat", "menu:ads:go")])
    rows += [
        [panel.btn(f"👥 Qruplar ({len(sel)} seçilib)", "menu:ads:grp:0"), panel.btn("👁 Önizləmə", "menu:ads:prev")],
        [panel.btn(f"⏱ Fasilə: ~{_fmt_dur(s['delay'])}  ›  ~{_fmt_dur(next_of(ads.DELAYS, s['delay']))}",
                   "menu:ads:dly")],
        [panel.btn(f"🔁 Qrupa təkrar: {s['cooldown']} saat  ›  "
                   f"{next_of(ads.COOLDOWNS, s['cooldown'])} saat", "menu:ads:cool"),
         panel.btn(f"🔢 Limit: {s['max']}  ›  {next_of(ads.MAXES, s['max'])}", "menu:ads:max")],
    ]
    if len(accounts) > 1:
        rows.append([panel.btn(f"🎙 Hesab: {_slot_label(runner, s['slot'])}  ›  {_next_slot_label(runner, s['slot'])}",
                               "menu:ads:slot")])
    rows.append([panel.btn("✏️ Mətni dəyiş", "menu:ads:text")] + ([panel.btn("♻️ Standart", "menu:ads:textdef")] if s["text"] else []))
    rows.append(nav_row("live", refresh="ads"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def groups_view(page: int = 0, refresh: bool = False, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    runner = _runner()
    cat = await runner.catalog(force=refresh) if runner else []
    sel = ads.selected()
    s = ads.settings()
    chunk, page, pages = panel.paginate(cat, page, PER_PAGE)
    lines = [crumb(SECTIONS["live"], "📣 Qrup reklamı", "👥 Qruplar"), LINE,
             f"Asistan hesabının yaza biləcəyi qruplar: <b>{len(cat)}</b> · seçilmiş: <b>{len(sel & {g['id'] for g in cat})}</b>",
             "✅ — reklam göndəriləcək · ▫️ — göndərilməyəcək · ⏳ — təkrar gözləməsi var",
             "<i>Defolt heç bir qrup seçilmir — yalnız seçdiyin qruplara göndərilir.</i>"]
    if not cat:
        lines.append("\n<i>Qrup tapılmadı — asistan hesabı işləyirmi? 🔄 ilə yenilə.</i>")
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = []
    for g in chunk:
        mark = "✅" if g["id"] in sel else "▫️"
        wait = " ⏳" if ads.on_cooldown(g["id"], s["cooldown"]) else ""
        rows.append([panel.btn(f"{mark} {g['title'][:34]}{wait}", f"menu:ads:t:{g['id']}:{page}")])
    if (nav := panel.page_row(page, pages, lambda p: f"menu:ads:grp:{p}")):
        rows.append(nav)
    rows.append([panel.btn("✅ Hamısını seç", "menu:ads:all"), panel.btn("▫️ Təmizlə", "menu:ads:none"),
                 panel.btn("🔄 Yenilə", "menu:ads:ref")])
    rows.append(panel.nav("menu:ads", "Reklam"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def ask_text_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    cur = ads.settings()["text"]
    lines = [crumb(SECTIONS["live"], "📣 Qrup reklamı", "✏️ Mətn"), LINE,
             "✍️ Qruplara göndəriləcək reklam mətnini yaz (qalın, kursiv, link formatı saxlanılır).",
             "<code>{bot}</code> — botun @istifadəçi_adı, <code>{link}</code> — t.me linki (avtomatik əvəz olunur).",
             f"<i>Maksimum {ads.TEXT_LIMIT} simvol. Asistan hesabı düymə göndərə bilmir — linki mətnə yaz.</i>",
             "", "<b>Hazırkı mətn:</b>", cur if cur else "<i>standart</i>"]
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[nav_row("ads")])


__all__ = ["ads_view", "ask_text_view", "groups_view"]
