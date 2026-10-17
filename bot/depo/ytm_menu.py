"""
🔎 /depo → 🔎 Skaner — YT Music skanerinin paralel ifaçı sayı (🤖 Avto / ✋ Manual).

Skaner YT Music ifaçılarını oxuyub mahnıları doldurucunun növbəsinə yığır (bot/depo/engine_ytm.py). Növbə (işçi × 4)
həddindən azdırsa, işçilər mahnı gözləyir və sürəti skaner məhdudlaşdırır — paralel say bunu tənzimləyir.
Görünüş: panel.py qaydaları; nəticə `view()` (test üçün sadə), klik `handle()`.
"""
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel as ui
from bot.depo.crawler import YTM_LOW_WATER
from bot.depo.engine_ytm import YTM_PAR_DEFAULT, YTM_PAR_MAX, YTM_PAR_RECOVER

ACTIONS = ("yp", "ypm", "yps", "ypd")
PRESETS = (1, 2, 3, 4, 6)
BACK = "df:panel"


def button_text(filler) -> str:
    auto = filler.ytm_par_mode == "auto"
    return f"🔎 Skaner: {'🤖' if auto else '✋'} {filler.ytm_parallel} paralel"


def view(filler, note: str = ""):
    auto = filler.ytm_par_mode == "auto"
    n = filler.ytm_parallel
    low = max(YTM_LOW_WATER, filler.workers * 4)
    q = len(filler.ytm_queue)
    stats = filler.ytm.stats if filler.ytm else {}
    lines = ["📦 <b>Depo doldurucu › YT Music skaneri</b>\n━━━━━━━━━━━━━━━━━━",
             f"🔎 <b>Eyni anda oxunan ifaçı:</b> {n} · {'🤖 Avto' if auto else '✋ Manual'}",
             f"📋 Növbə: <b>{q}</b> / {low} <i>(işçi × 4; altındadırsa skaner növbəti ifaçını açır)</i>"]
    if stats.get("artist"):
        lines.append(f"🔎 Oxunur: <i>{escape(str(stats['artist'])[:80])}</i>")
    if stats.get("err"):
        lines.append(f"⚠️ Bağlantı / limit xətası: {stats['err']}")
    lines += ["",
              "<i>Növbə həmişə boşdursa, işçilər mahnı gözləyir — skaner tavan olur: say artır. "
              f"Çox artırsan YT Music IP limitinə düşmək riski var (maks. {YTM_PAR_MAX}).</i>",
              f"<i>🤖 Avto {YTM_PAR_DEFAULT}-dən başlayır, xəta alanda 1 azalır, {int(YTM_PAR_RECOVER // 60)} dəq. sakit keçəndə "
              "geri qalxır. ✋ Manual yazdığın sayı saxlayır.</i>"]
    if note:
        lines.append(f"\n{note}")
    rows = [[ui.mode("🔎 Paralel", auto, f"df:ypm:{'manual' if auto else 'auto'}")]]
    if not auto:
        rows.append([ui.btn("➖", "df:ypd:-1"), ui.btn("➕", "df:ypd:1")])
        rows.append([ui.btn(f"✅ {p}" if p == n else str(p), f"df:yps:{p}") for p in PRESETS])
    rows.append(ui.nav(BACK, "Doldurucu", close_data="df:close", refresh_data="df:yp"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def handle(filler, action: str, parts: list, cb, show) -> None:
    """df:yp (aç) · df:ypm:auto|manual · df:yps:<n> · df:ypd:<±n>. show(text, kb) — mesajı yeniləyir."""
    note = ""
    arg = parts[2] if len(parts) > 2 else ""
    if action == "ypm" and arg in ("auto", "manual"):
        if arg == "manual":
            filler.set_ytm_par(filler.ytm_parallel)            # manuala keçəndə cari say qalır
            note = "✋ Manual — sayı özün seç"
        else:
            filler.set_mode("ytm_par", "auto")
            filler._ytm_auto = YTM_PAR_DEFAULT
            note = f"🤖 Avto — {filler.ytm_parallel} paralel"
    elif action == "yps" and arg.lstrip("-").isdigit():
        note = f"✅ {filler.set_ytm_par(int(arg))} paralel"
    elif action == "ypd" and arg.lstrip("+-").isdigit():
        note = f"✅ {filler.set_ytm_par(filler.ytm_parallel + int(arg))} paralel"
    await cb.answer(note[:190] if note else None)
    await show(*view(filler, note))
