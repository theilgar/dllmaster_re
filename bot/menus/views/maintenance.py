"""
bot.menus.views.maintenance
🛠 Texniki iş rejimi paneli: aç / söndür, müddət, yayım və depo siyasəti, istifadəçi mətni, bitəndə xəbər.
"""
from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.state import get_context
from bot.menus.views.base import LINE, SECTIONS, crumb
from common import maintenance as mt
from common.menu import UI_STYLE_LABELS, get_ui_style
from common.kv import next_of

_dur = mt.duration_label


def _next_duration(cur: int) -> int:
    d = mt.DURATIONS
    return next_of(d, cur)


def maint_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    ctx = get_context()
    s = mt.settings()
    active = mt.is_active()
    left = mt.remaining()
    va_n = len(getattr(getattr(ctx, "voice_assistant", None), "chats", {}) or {})
    lines = [
        crumb(SECTIONS["sys"], "🛠 Bot idarəsi", "🛠 Texniki iş rejimi"),
        LINE,
        f"Vəziyyət: <b>{'🟢 AKTİV' if active else '🔴 söndürülüb'}</b>"
        + (f" · qalan: <b>{left // 60} dəq.</b>" if active and left is not None else "")
        + (" · müddətsiz" if active and left is None else ""),
        "",
        f"Rejim növü: <b>{'🔒 Bağlı' if s['mode'] == 'block' else '⚠️ Xəbərdarlıq'}</b>",
        ("Rejim açıqkən creator-dan başqa heç kim botdan istifadə edə bilmir: 🎵 musiqi, 🔗 linklər, "
         "🎙 səsli yayım, Shazam, audio redaktor, playlist, inline axtarış, qrup əmrləri.\n"
         "<i>Açıq qalanlar: /start /help /lang /premium, dil seçimi, ödəniş və bəxşiş.</i>") if s["mode"] == "block" else
        ("<b>Hamı</b> botdan istifadə edə bilir (yayımlar, musiqi, inline — hamısı işləyir). Özəl çatda istifadəçi "
         "30 dəq.-də bir dəfə qısa xəbərdarlıq görür: «texniki işlər gedir, qısa fasilələr ola bilər».\n"
         "<i>Bağlama, «bitəndə xəbər» və gözləyənlər bu rejimdə işləmir.</i>"),
        "",
        f"⏱ Növbəti açılışda müddət: <b>{_dur(s['duration'])}</b>",
        f"🎙 Aktiv yayımlar: <b>{'açanda dayandırılır' if s['voice'] else 'davam edir'}</b>"
        + (f" <i>(indi {va_n} aktiv)</i>" if va_n else ""),
        f"📣 Yayım qruplarına bildiriş: <b>{'bəli' if s['stream_notice'] else 'xeyr'}</b>"
        + (" <i>(açılanda aktiv yayımı olan qruplara eyni mətn, bitəndə «bitdi»)</i>" if s["stream_notice"] else ""),
        f"📦 Depo doldurucu: <b>{'açanda pauza, bitəndə davam' if s['depo'] else 'işləyir'}</b>",
        f"🔔 Bitəndə cəhd edənlərə xəbər: <b>{'bəli' if s['notify'] else 'xeyr'}</b>"
        + (f" <i>(indi {mt.waiters_count()} nəfər gözləyir)</i>" if active and mt.waiters_count() else ""),
        f"📋 Mesaj üslubu: <b>{UI_STYLE_LABELS.get(get_ui_style(), '') if s['styled'] else 'adi mətn'}</b>"
        + (" <i>(/ui → Görünüş ayarı ilə dəyişir)</i>" if s["styled"] else ""),
        f"🗑 Bildirişin silinməsi: <b>{mt.DELETE_LABELS[s['delete']]}</b>"
        + (f" <i>(indi {mt.notices_count()} izlənir)</i>" if s["delete"] == "end" and mt.notices_count() else ""),
        f"📝 İstifadəçi mətni: <b>{'✏️ sənin mətnin' if s['text'] else 'standart'}</b>",
    ]
    if note:
        lines.append(f"\nℹ️ {note}")
    nxt = _next_duration(s["duration"])
    rows = [
        [panel.btn("🔴 Rejimi söndür" if active else "🟢 Rejimi aç", "menu:maint:tog")],
        [panel.btn("Növ: 🔒 Bağlı  ›  ⚠️ Hamı istifadə edir" if s["mode"] == "block"
                   else "Növ: ⚠️ Hamı istifadə edir  ›  🔒 Bağlı", "menu:maint:mode")],
        [panel.btn(f"⏱ Müddət: {_dur(s['duration'])}  ›  {_dur(nxt)}", "menu:maint:dur")],
        [panel.btn("🎙 Yayımlar: dayandırılır  ›  davam edir" if s["voice"] else "🎙 Yayımlar: davam edir  ›  dayandırılsın",
                   "menu:maint:voice"),
         panel.btn("📦 Depo: pauza  ›  işləyir" if s["depo"] else "📦 Depo: işləyir  ›  pauza", "menu:maint:depo")],
    ]
    if active and va_n:
        rows.append([panel.btn(f"⏹ Aktiv yayımları indi dayandır ({va_n})", "menu:maint:stopv")])
    d = mt.DELETE_MODES
    nxt_del = next_of(d, s["delete"])
    rows.append([panel.btn(f"🗑 Mesajın silinməsi: {mt.DELETE_LABELS[s['delete']]}  ›  {mt.DELETE_LABELS[nxt_del]}",
                           "menu:maint:del")])
    if mt.notices_count():
        rows.append([panel.btn(f"🗑 İzlənən bildirişləri indi sil ({mt.notices_count()})", "menu:maint:delnow")])
    rows += [
        [panel.toggle("🔔 Bitəndə xəbər ver", s["notify"], "menu:maint:notify", compact=True),
         panel.toggle("📣 Yayım qruplarına", s["stream_notice"], "menu:maint:streams", compact=True)],
        [panel.toggle("📋 Görünüş üslubu", s["styled"], "menu:maint:styled", compact=True)],
        [panel.btn("✏️ Mətni dəyiş", "menu:maint:text"), panel.btn("👁 Önizləmə", "menu:maint:prev")],
    ]
    if s["text"]:
        rows.append([panel.btn("♻️ Standart mətn", "menu:maint:textdef")])
    rows.append([panel.btn("🧪 Testlər (real yoxlama)", "menu:maint:tests")])
    rows.append(nav_row("dev", refresh="maint"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def tests_view(results: list | None = None, running: str = "", note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import selftest
    lines = [crumb(SECTIONS["sys"], "🛠 Texniki iş rejimi", "🧪 Testlər"), LINE,
             "Botun <b>real</b> işlək olduğunu yoxlayır: axtarış, YouTube, qısa video yükləmə, PO token / cookie, "
             "depo, baza, ffmpeg, Shazam və texniki iş qapısı. Heç nə dəyişmir.", ""]
    if results:
        lines.append(selftest.render(results, running))
    elif running:
        lines.append(f"⏳ <b>{selftest.TESTS[running][0]}</b> ...")
    else:
        lines.append("<i>Test seç və ya hamısını işlət.</i>")
    if note:
        lines.append(f"\nℹ️ {note}")
    rows = [[panel.btn("▶️ Hamısını işlət", "menu:maint:tr:all")]]
    keys = selftest.ORDER
    for i in range(0, len(keys), 2):
        rows.append([panel.btn(selftest.TESTS[k][0][:30], f"menu:maint:tr:{k}") for k in keys[i:i + 2]])
    rows.append([panel.btn("🚨 «Adminə bildir» sınağı (saxta xəta)", "menu:maint:tr:sim")])
    rows.append(nav_row("maint", refresh="maint:tests"))
    return "\n".join(lines)[:4000], InlineKeyboardMarkup(inline_keyboard=rows)


def ask_text_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    cur = mt.settings()["text"]
    lines = [
        crumb(SECTIONS["sys"], "🛠 Texniki iş rejimi", "✏️ Mətn"),
        LINE,
        "✍️ İstifadəçilərə göstəriləcək mətni yaz (qalın, kursiv, link formatı saxlanılır).",
        "<code>{eta}</code> yaz — təxmini bitmə sətri oraya düşəcək (müddət seçilibsə). Yazmasan, mətnin sonuna əlavə olunur.",
        "",
        "<b>Hazırkı mətn:</b>",
        cur if cur else "<i>standart</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[nav_row("maint")])


__all__ = ["ask_text_view", "escape", "maint_view", "tests_view"]
