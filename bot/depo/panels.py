"""
📋 bot/depo/panels.py - Depo panelləri: görünüş (view) funksiyaları handlers.py-dən ayrılıb.

V1 — asılılıqsız funksiyalar (modul səviyyəsində).
make_views(...) — filler / helpers / tg kimi obyektlərə bağlı panellər (handlers.setup bir dəfə çağırır).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import audio_cache, depo_helpers, panel as ui
from bot.core.panel import btn
from bot.depo.crawler import COUNTRIES, DEFAULT_COUNTRY_SEARCH_QUERIES, HAS_YTM
from bot.depo.engine import (
    MAX_WORKERS,
    Q_PAGE,
    SOURCE_KIND_LABELS,
    hkey,
    search_depo_for_deletion,
)
from bot.depo.monitor import (
    get_top_interval,
)
from common.database import get_db
from common.kv import next_of

try:
    from telethon import events
except ImportError:
    events = None

logger = logging.getLogger(__name__)


def helpers_guide_view():
    text = (
        "🤖 <b>Köməkçi botlar › Bələdçi</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<b>Nə üçündür?</b>\n"
        "Telegram bir botun qrupa / kanala göndərə biləcəyi mesaj sayını məhdudlaşdırır (≈ 20 mahnı/dəq.). "
        "Köməkçi botlar depoya yükləməni öz aralarında bölüşür — <b>N köməkçi ≈ N dəfə sürətli</b> yükləmə. "
        "Köməkçi yoxdursa və ya alınmasa, əsas bot özü yükləyir, heç nə itmir.\n\n"
        "<b>📌 Rejim</b> <i>(düyməyə basdıqca dəyişir)</i>\n"
        "• 🔴 <b>Söndürülüb</b> — köməkçilər işləmir, hər şeyi əsas bot yükləyir.\n"
        "• 📦 <b>Yalnız doldurucu</b> — yalnız Depo Doldurucunun mahnıları köməkçilərə gedir; "
        "istifadəçi yükləmələri əsas botda qalır.\n"
        "• 🌐 <b>Bütün depo yükləmələri</b> — istifadəçilərin yükləməsi də köməkçilərlə depoya yazılır.\n\n"
        "<b>⏱ Sürət limiti</b> — hər köməkçinin dəqiqədə neçə mahnı göndərəcəyi. Telegram həddi ≈ 20; "
        "bundan çox qoysan flood (müvəqqəti blok) riski artır.\n\n"
        "<b>🔁 Çevirmə</b> — mahnının <code>file_id</code>-si hər bot üçün fərqlidir, əsas bot köməkçinin "
        "<code>file_id</code>-si ilə göndərə bilmir. Ona görə mahnı əsas botun <code>file_id</code>-sinə çevrilir:\n"
        "• 🐢 <b>Lazy</b> — yalnız mahnı istənəndə çevrilir (köməkçilərin tam tutumu qalır, tövsiyə olunur).\n"
        "• ⚡ <b>Dərhal</b> — yükləmə anında çevrilir (daha çox yük, tutum sink çatına bağlıdır).\n\n"
        "<b>Düymələr</b>\n"
        "• ➕ <b>Köməkçi bot əlavə et</b> — BotFather tokenini göndər (və ya <code>/depo helper add &lt;token&gt;</code>).\n"
        "• 🩺 <b>Hamısını yoxla</b> — hər botun depoda olub-olmadığını və icazəsini yenidən yoxlayır.\n"
        "• 🗑 <b>№. ad</b> — botu siyahıdan silir (<code>config.env</code>-dəkilər yalnız oradan silinir).\n"
        "• 👥 <b>Köməkçi qrupları</b> — qruplarda mahnı göndərərkən flood-a qarşı köməkçiləri qruplara bağlayır.\n"
        "• 📦 <b>Doldurucu</b> — Depo Doldurucu panelinə keçid.\n\n"
        "<b>Bot vəziyyətləri</b>\n"
        "• 🟢 hazır · ⚠️ depoda admin deyil / «Post messages» icazəsi yoxdur · "
        "⏳ gözləyir (depo təyin olunmayıb və ya flood) · ❌ xəta.\n"
        "• 🌊 flood — bot müvəqqəti limitə düşüb, növbə digər köməkçiyə keçir.\n\n"
        "<b>Vacib</b>\n"
        "Köməkçi bot depo kanalında <b>admin</b> olmalı və «Post messages» icazəsinə malik olmalıdır; "
        "yoxsa ⚠️ statusu görünür və o bot istifadə edilmir."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[ui.nav("df:hp", "Köməkçi botlar", close_data="df:close")])


def df_top_interval_picker_view(sess: dict | None = None, note: str = "", back: str = "dt:start"):
    cur = float(sess.get("interval", get_top_interval())) if sess else get_top_interval()
    lines = [
        "📊 <b>Canlı monitor › Yenilənmə intervalı</b>\n━━━━━━━━━━━━━━━━━━",
        "⏱ <b>Monitorun yenilənmə tezliyi:</b>\n",
        f"Hazırkı interval: <b>{cur:g} san.</b>\n",
        "<i>(Canlı monitor mesajının neçə saniyədən bir redaktə ediləcəyini müəyyən edir:\n"
        "• <b>1 san.</b> — Real-vaxt htop dinamikası (ən dəqiq)\n"
        "• <b>2–3 san.</b> — Optimal və sabit (Telegram flood-a qarşı ən rahat)\n"
        "• <b>5–10 san.</b> — Zəif internet və az trafik üçün ən təhlükəsiz)</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    bk = "dt" if back == "dt:start" else "p"
    rows = [
        [btn(f"{'⚡ ' if cur == 1 else ''}1 san.", "dt:set:i:1"),
         btn(f"{'⚡ ' if cur == 2 else ''}2 san.", "dt:set:i:2"),
         btn(f"{'⚡ ' if cur == 3 else ''}3 san.", "dt:set:i:3")],
        [btn(f"{'⚡ ' if cur == 5 else ''}5 san.", "dt:set:i:5"),
         btn(f"{'⚡ ' if cur == 10 else ''}10 san.", "dt:set:i:10"),
         btn(f"{'⚡ ' if cur == 15 else ''}15 san.", "dt:set:i:15")],
        [btn(f"{d:+d}s", f"df:vstep:i:{d:+d}:{bk}") for d in (-5, -1, 1, 5)],
        [btn("✏️ Öz saniyəni yaz", f"df:vinp:i:{bk}")],
        ui.nav(back, "Monitor" if bk == "dt" else "Doldurucu", close_data="dt:close")
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def dd_result_view(st: dict, note: str = ""):
    nd, no = len(st["dups"]), len(st["orphans"])
    text = (f"🧬 <b>Yoxlama {'dayandırıldı' if st['stopped'] else 'bitdi'}</b> "
            f"<i>({int(time.time() - st['started'])} san.)</i>\n━━━━━━━━━━━━━━━━━━\n"
            f"📨 Depoda audio post: <b>{st['total']}</b> · bazada: {st['in_db']}"
            + (f" · ID-siz: {st['novid']}" if st["novid"] else "") + "\n"
            f"🔁 Eyni video ID — artıq post: <b>{st['same_vid']}</b>\n"
            f"🎵 Eyni mahnı, fərqli ID — artıq post: <b>{st['same_song']}</b>\n"
            f"🧩 Bazada olmayan video: <b>{no}</b>\n"
            + (f"↪️ Dublikat posta baxan baza sətri: {st['repoint']}\n" if st["repoint"] else "")
            + (f"🗑 Bazada var, kanalda yoxdur: {st['missing_db']} <i>(🧹 sinxron ilə təmizlə)</i>\n"
               if st["missing_db"] else ""))
    if st["samples"]:
        text += "\n<b>Nümunə dublikatlar:</b>\n" + "\n".join(
            "• " + " ⇄ ".join(escape(x) for x in g) for g in st["samples"][:5])
    if st["stopped"]:
        text += "\n\n⚠️ <i>Yoxlama tam deyil — nəticələr yalnız oxunan hissə üçündür</i>"
    nn = len(st.get("novid_posts") or [])
    if nn:
        text += (f"\n🔎 ID-siz, bazada olmayan post: <b>{nn}</b> "
                 "<i>(axtarışda görünmür — YouTube-da tapıb bağlamaq olar)</i>")
    rows = []
    if nd or no or st["repoint"]:
        rows.append([btn(f"🔗 Bazaya bağla ({no + nd})", "df:ddlink")])
    if nn:
        rows.append([btn(f"🔎 ID-sizləri YouTube-da tap və bağla ({nn})", "df:ddnovid")])
    if nd:
        rows.append([btn(f"🗑 Dublikat postları depodan sil ({nd})", "df:dddel")])
    if not rows:
        text += "\n\n✅ <b>Depo və baza tam uyğundur, dublikat yoxdur.</b>"
    if note:
        text += f"\n\n{note}"
    rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close", refresh_data="df:ddgo"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def sync_result_view(st: dict, note: str = ""):
    miss = len(st["missing"])
    pct = miss / st["checked"] * 100 if st["checked"] else 0
    text = (f"🧹 <b>Yoxlama {'dayandırıldı' if st['stopped'] else 'bitdi'}</b> "
            f"<i>({st.get('method', 'userbot')}, "
            f"{int(time.time() - st['started'])} san.)</i>\n━━━━━━━━━━━━━━━━━━\n"
            f"📨 Bazada depo postu: <b>{st['total']}</b> · yoxlandı: {st['checked']}\n"
            f"🗑 Kanalda yoxdur (silinib): <b>{miss}</b> ({pct:.1f}%)\n"
            + (f"❔ Yoxlanıla bilmədi: {st['unknown']}\n" if st["unknown"] else ""))
    rows = []
    if miss:
        text += ("\n<b>Nə edək?</b>\n"
                 "🗑🚫 — bazadan sil, doldurucu bunları <b>yenidən yükləməsin</b> (sən bilərəkdən silmisənsə)\n"
                 "🗑♻️ — bazadan sil, doldurucu <b>yenidən yükləsin</b> (təsadüfən silinibsə)\n"
                 "<i>İstifadəçi istəsə, mahnı hər iki halda normal yüklənib göndərilir.</i>")
        if pct > 50:
            text += f"\n\n⚠️ <b>Diqqət:</b> postların {pct:.0f}%-i tapılmadı — depo kanalının düzgün olduğundan əmin ol."
        rows += [[btn(f"🗑🚫 Sil, yenidən yükləmə ({miss})", "df:syncapply:b")],
                 [btn(f"🗑♻️ Sil, yenidən yüklə ({miss})", "df:syncapply:r")]]
    else:
        text += "\n✅ Baza depo ilə sinxrondur."
    if note:
        text += f"\n\n{note}"
    rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def hp_add_view(note: str = ""):
    text = ("➕ <b>Köməkçi bot əlavə et</b>\n━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ @BotFather → /newbot → tokeni kopyala\n"
            f"2️⃣ Botu depo kanalına ({escape(audio_cache.depo_setting())}) <b>admin</b> et — "
            "<i>\"Post messages\"</i> icazəsi ilə\n"
            "3️⃣ Tokeni bura göndər (adi mesaj kimi)\n\n"
            "<i>Bir mesajda bir neçə token da göndərə bilərsən. Mesaj dərhal silinir. "
            "Gözləmə 5 dəq. davam edir.</i>"
            + (f"\n\n{note}" if note else ""))
    return text, InlineKeyboardMarkup(inline_keyboard=[ui.nav("df:hp", "Köməkçi botlar", close_data="df:close")])


def mode_rows(label: str, auto: bool, prefix: str, manual_btn) -> list:
    """🤖 Avto / ✋ Manual — bir düymə (basanda dəyişir); manualda əl ilə seçim düyməsi də görünür."""
    rows = [[ui.mode(label, auto, f"{prefix}:{'manual' if auto else 'auto'}")]]
    if not auto:
        rows.append([manual_btn])
    return rows


def workers_label(n: int) -> str:
    return "tək-tək (1)" if n == 1 else f"paralel × {n}"


def clean_prompt_view():
    text = (
        "🗑 <b>Depodan mahnıları sorğu ilə sil</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Depodan və keş bazasından silmək istədiyiniz mahnıların açar sözünü yazın.\n"
        "<i>(Məsələn: <code>azeri bass music</code>, <code>bass remix</code>, <code>meyxana</code> və s.)</i>\n\n"
        "⚠️ <b>Qeyd:</b>\n"
        "• Uyğun gələn bütün mahnılar əvvəlcə axtarılacaq və siyahı təsdiq üçün göstəriləcək.\n"
        "• Təsdiqlədikdə həm <b>Telegram depo kanalından</b>, həm də <b>keş bazasından</b> silinəcək.\n"
        "• Doldurucunun <b>qara siyahısına</b> salınacaq ki, bir daha depoya yüklənməsin."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "df:panel")]])
    return text, kb


def _ago(ts) -> str:
    sec = int(abs(time.time() - (ts or 0)))
    return f"{sec // 3600} saat" if sec >= 3600 else (f"{sec // 60} dəq." if sec >= 60 else f"{sec} san.")


def kb_sig(kb) -> str:
    return "|".join(b.text for row in kb.inline_keyboard for b in row)


def make_views(filler, helpers, tg, context, scan_state, clean_pending, clean_wait, hp_page, HP_PER_PAGE):
    """handlers.setup-dakı paylaşılan obyektlərə bağlı panellər (əvvəl setup daxilində closure idi)."""
    def ytm_line() -> str:
        if not HAS_YTM:
            return "🎵 YT Music kataloqu: <i>ytmusicapi yoxdur —</i> <code>pip install ytmusicapi</code>"
        if not filler.ytm_on:
            return "🎵 YT Music kataloqu: 🔴 söndürülüb"
        c = filler.ytm.counts()
        ys = filler.ytm.stats
        line = (f"🎵 YT Music: 🟢 ifaçı ✅ {c['done']} · ⏳ {c['pending']}"
                + (f" · ❌ {c['failed']}" if c["failed"] else "")
                + f" · 🎶 tapıldı {c['songs']} · 📋 növbədə {len(filler.ytm_queue)}")
        if ys["artist"]:
            line += f"\n   🔎 <i>{escape(str(ys['artist'])[:40])}</i> oxunur"
        if ys["note"]:
            line += f"\n   ℹ️ <i>{escape(ys['note'])}</i>"
        return line

    def sources_view(note: str = ""):
        srcs = filler.sources()
        text = "📋 <b>Doldurucu mənbələri</b>\n\n" + ("\n".join(
            f"{i}. {escape(s.get('label') or s['id'])} <i>({SOURCE_KIND_LABELS.get(s['kind'], s['kind'])})</i>"
            for i, s in enumerate(srcs, 1))
            or "<i>Mənbə yoxdur — yalnız istifadəçilərin sevdikləri və Mix işlənir.</i>") \
            + (f"\n\n{note}" if note else "")
        rows = [[btn(f"🗑 {(s.get('label') or s['id'])[:30]}", f"df:rm:{i}")] for i, s in enumerate(srcs)]
        rows.append([btn("♻️ Standart mənbələr", "df:defsrc")])
        rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def df_delay_picker_view(note: str = "", back: str = "df:panel"):
        lines = [
            "📦 <b>Depo doldurucu › Gözləmə intervalı</b>\n━━━━━━━━━━━━━━━━━━",
            "⏱ <b>Mahnılar arası gözləmə intervalı:</b>\n",
            f"Hazırkı dəyər: <b>{filler.delay} san.</b>\n",
            "<i>(Hər işçinin mahnılar arası fasiləsi. Sürətli yükləmə üçün azaldın, Telegram flood riskinə qarşı artırın)</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        bk = "dt" if back == "dt:start" else "p"
        rows = [[btn(f"{d:+d}", f"df:vstep:d:{d:+d}:{bk}") for d in (-5, -1, 1, 5)],
                [btn("✏️ Öz rəqəmini yaz", f"df:vinp:d:{bk}")],
                ui.nav(back, "Monitor" if bk == "dt" else "Doldurucu", close_data="df:close")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def df_worker_picker_view(note: str = "", back: str = "df:panel"):
        lines = [
            "📦 <b>Depo doldurucu › Worker sayı</b>\n━━━━━━━━━━━━━━━━━━",
            "🧵 <b>Paralel işçi (worker) sayı:</b>\n",
            f"Hazırkı dəyər: <b>{workers_label(filler.workers)}</b>\n",
            f"<i>(Eyni anda neçə mahnının yüklənəcəyini təyin edir. 1 = tək-tək, maksimum {MAX_WORKERS}. Çox yüksək dəyər CPU/RAM və flood riski yarada bilər)</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        bk = "dt" if back == "dt:start" else "p"
        rows = [[btn(f"{d:+d}", f"df:vstep:w:{d:+d}:{bk}") for d in (-5, -1, 1, 5)],
                [btn("✏️ Öz rəqəmini yaz", f"df:vinp:w:{bk}")],
                ui.nav(back, "Monitor" if bk == "dt" else "Doldurucu", close_data="df:close")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def df_rate_picker_view(note: str = ""):
        lines = [
            "🤖 <b>Köməkçi botlar › Sürət limiti</b>\n━━━━━━━━━━━━━━━━━━",
            "⏱ <b>Köməkçi botların sürət limiti:</b>\n",
            f"Hazırkı dəyər: <b>{helpers.rate} mahnı/dəq. hər bot</b>\n",
            "<i>(Bir köməkçi botun dəqiqədə maksimum yönləndirə biləcəyi mahnı sayı. 20-dən çox Telegram flood riski yarada bilər)</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = ui.stepper("df:vstep:hpr:", "df:vinp:hpr") + [ui.nav("df:hp", "Köməkçi botlar", close_data="df:close")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def fail_view(note: str = ""):
        n, rows = filler.failed_list(12)
        lines = ["❌ <b>Alınmayan mahnılar</b>\n━━━━━━━━━━━━━━━━━━",
                 "<i>Müvəqqəti xətalar (flood, şəbəkə, YouTube 429) avtomatik 5 dəfəyə qədər təkrarlanır: "
                 "5 dəq. → 30 dəq. → 2 saat → 6 saat → 24 saat. Daimi xətalar (video silinib, özəl, region) "
                 "və 5 cəhddən sonra da alınmayanlar bura düşür.</i>\n",
                 f"🔁 Təkrar növbəsində: <b>{len(filler.retry)}</b> · ❌ siyahıda: <b>{n}</b>\n"]
        for title, err, tries in rows:
            lines.append(f"• <b>{escape((title or '')[:50])}</b> <i>({tries} cəhd)</i>\n"
                         f"   <code>{escape((err or '')[:90])}</code>")
        if n > len(rows):
            lines.append(f"<i>... və daha {n - len(rows)}</i>")
        if note:
            lines.append(f"\n{note}")
        rows_kb = []
        if n:
            rows_kb.append([btn(f"🔁 Hamısını yenidən sına ({n})", "df:failre"), btn("🗑 Siyahını təmizlə", "df:failclr")])
        if filler.retry:
            rows_kb.append([btn(f"⚡ Təkrar növbəsini indi işlə ({len(filler.retry)})", "df:retrynow")])
        rows_kb.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows_kb)

    def queue_view(tab: str = "r", page: int = 0, note: str = ""):
        """📋 Gözləyənlər — 🔁 təkrar növbəsi və ❌ alınmayanlar, səhifələrlə, hər mahnı üçün ayrıca düymə."""
        nr, nf = len(filler.retry), filler.failed_list(0)[0]
        tabs = [btn(f"🔁 Təkrar ({nr})  ›  ❌ Alınmayan ({nf})", "df:q:f:0") if tab == "r"
                else btn(f"❌ Alınmayan ({nf})  ›  🔁 Təkrar ({nr})", "df:q:r:0")]
        lines = ["📋 <b>Gözləyənlər</b>\n━━━━━━━━━━━━━━━━━━"]
        rows_kb = [tabs]
        now = time.time()
        if tab == "r":
            total, items = filler.retry_page(page)
            lines.append("<i>Flood, şəbəkə, 403/429 kimi müvəqqəti xətalar avtomatik təkrarlanır "
                         "(5 dəq. → 30 dəq. → 2 saat → 6 saat → 24 saat). ⚡ — indi sına, 🗑 — sil.</i>\n")
            for n, r in enumerate(items, page * Q_PAGE + 1):
                it = r["item"]
                when = "indi" if r["next"] <= now else f"{_ago(r['next'])} sonra"
                lines.append(f"{n}. <b>{escape((it.get('title') or '')[:48])}</b>\n"
                             f"   🔁 {r.get('tries', 0)} cəhd · ⏳ {when}\n"
                             f"   <code>{escape((r.get('err') or '')[:80])}</code>")
                h = hkey(filler._key(it) or it.get("title"))
                rows_kb.append([btn(f"⚡ {n}. {(it.get('title') or '')[:26]}", f"df:qa:rn:{h}:{page}"),
                                btn("🗑", f"df:qa:rd:{h}:{page}")])
            if total:
                rows_kb.append([btn(f"⚡ Hamısını indi ({total})", "df:qb:rnow"),
                                btn("❌ Hamısı siyahıya", "df:qb:r2f")])
                rows_kb.append([btn(f"🗑 Hamısını sil ({total})", "df:qb:rdall")])
        else:
            total, items = filler.failed_page(page)
            lines.append("<i>Daimi xətalar (video silinib, özəl, region) və 5 cəhddən sonra da alınmayanlar. "
                         "🔁 — növbənin önünə qaytar, 🗑 — sil.</i>\n")
            for n, (key, title, err, tries, ts) in enumerate(items, page * Q_PAGE + 1):
                lines.append(f"{n}. <b>{escape((title or key or '')[:48])}</b>\n"
                             f"   ❌ {tries} cəhd · {_ago(ts)} əvvəl\n"
                             f"   <code>{escape((err or '')[:80])}</code>")
                h = hkey(key)
                rows_kb.append([btn(f"🔁 {n}. {(title or key or '')[:26]}", f"df:qa:fr:{h}:{page}"),
                                btn("🗑", f"df:qa:fd:{h}:{page}")])
            if total:
                rows_kb.append([btn(f"🔁 Hamısını yenidən ({total})", "df:qb:frall"),
                                btn("🗑 Siyahını təmizlə", "df:qb:fdall")])
        if not total:
            lines.append("✅ <i>Bu siyahı boşdur</i>")
        pages = max(1, (total + Q_PAGE - 1) // Q_PAGE)
        if (nav := ui.page_row(page, pages, lambda p: f"df:q:{tab}:{p}", wrap=True, center_cb="df:noop")):
            rows_kb.append(nav)
        if note:
            lines.append(f"\n{note}")
        rows_kb.append([btn("⚡ Məcburi davam", "df:force")])
        rows_kb.append(ui.nav("df:panel", "Doldurucu", close_data="df:close", refresh_data=f"df:q:{tab}:{page}"))
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "…"
        return text, InlineKeyboardMarkup(inline_keyboard=rows_kb)

    def country_queries_list_view():
        lines = [
            "🔍 <b>Ölkələr üzrə axtarış sorğuları</b>\n━━━━━━━━━━━━━━━━━━",
            "YouTube və YT Music-dən playlist və ifaçı tapmaq üçün istifadə olunan açar sözlər.",
            "Burada istəmədiyiniz sorğuları (məs: <b>Azeri Bass Music</b>) söndürə bilərsiniz.\n",
            "Ölkə seçin:",
        ]
        c_list = list(DEFAULT_COUNTRY_SEARCH_QUERIES.keys())
        for c in filler.countries():
            if c not in c_list and c in COUNTRIES:
                c_list.append(c)

        rows = []
        row = []
        for cc in c_list:
            cname = COUNTRIES.get(cc, cc).split(" ", 1)[0]
            q_all = filler.get_country_queries(cc)
            q_active = sum(1 for q in q_all if q.get("enabled", True))
            btn_text = f"{cname} {cc} ({q_active}/{len(q_all)})"
            row.append(btn(btn_text, f"df:cq:{cc}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn("🌍 Ölkə prioriteti", "df:ctry")])
        rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def country_queries_view(cc: str, note: str = ""):
        cname = COUNTRIES.get(cc, cc)
        queries = filler.get_country_queries(cc)
        active_count = sum(1 for q in queries if q.get("enabled", True))
        lines = [
            f"🔍 <b>{cname} ({cc}) › Axtarış sorğuları</b>\n━━━━━━━━━━━━━━━━━━",
            f"Aktiv sorğular: <b>{active_count}/{len(queries)}</b>\n",
            "<i>Düyməyə basaraq sorğunu yandırın (✅) və ya söndürün (▫️). Söndürülən sorğular üzrə playlistlər axtarılmayacaq.</i>\n",
        ]
        if note:
            lines.append(f"ℹ️ {note}\n")

        rows = []
        for i, q in enumerate(queries):
            en = q.get("enabled", True)
            qtext = q.get("query", "")
            rows.append([ui.check(qtext[:36], en, f"df:cqt:{cc}:{i}")])

        rows.append([btn("➕ Yeni sorğu", f"df:cqadd:{cc}"), btn("♻️ Standart sorğular", f"df:cqrst:{cc}")])
        rows.append(ui.nav("df:cqlist", "Ölkələr", close_data="df:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def countries_view(note: str = ""):
        cs = filler.countries()
        applied = filler._get("countries_applied")
        lines = ["<i>/depo › 🌍 Ölkələr</i>", "🌍 <b>Ölkə prioriteti</b>", "━━━━━━━━━━━━━━━━━━",
                 "Seçdiyin ölkələrin mahnıları <b>əvvəl</b> yüklənir:",
                 "1️⃣ ölkənin YT Music chartı (top mahnılar) → növbənin əvvəlinə",
                 "2️⃣ chartdakı ifaçıların bütün mahnıları → skanerdə prioritetlə",
                 "3️⃣ onların oxşar ifaçıları (eyni səhnə) — pillə-pillə azalan prioritetlə",
                 "Sonra köhnə qayda ilə qalan dünya.", ""]
        if cs:
            lines.append("<b>Seçilib (sıra = prioritet):</b>")
            for i, c in enumerate(cs, 1):
                lines.append(f"{i}. {COUNTRIES[c]}")
        else:
            lines.append("<i>Ölkə seçilməyib — doldurucu hamısını bərabər yükləyir.</i>")
        if cs:
            pend = filler.ytm.has_prio_pending() if filler.ytm else False
            lines.append(f"\n📋 Ölkə növbəsi: <b>{len(filler.country_queue)}</b> mahnı"
                         + (" · 🎤 prioritetli ifaçılar skan edilir" if pend else ""))
            lines.append("🎯 Yalnız bu ölkələr: " + ("<b>✅ açıq</b> — başqa ölkələrin mahnıları yüklənmir"
                                                    if filler._get("country_strict") == "1"
                                                    else "❌ — ölkələr bitəndə qalan dünya yüklənir"))
        if applied:
            lines.append(f"\n🕓 Son tətbiq: {int((time.time() - int(applied)) / 60)} dəq. əvvəl "
                         "<i>(növbə hər yeniləndikcə chartlar avtomatik yenidən oxunur)</i>")
        if note:
            lines.append(f"\n{note}")
        rows, row = [], []
        for c, label in COUNTRIES.items():
            mark = f"{cs.index(c) + 1}️⃣ " if c in cs else ""
            row.append(btn(mark + label.split(" ", 1)[0] + " " + c, f"df:ctryt:{c}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        if len(cs) > 1:
            rows.append([btn(f"⬆️ {c}", f"df:ctryup:{c}") for c in cs[1:5]])
        if cs:
            strict = filler._get("country_strict") == "1"
            rows.append([ui.toggle("🎯 Yalnız bu ölkələr", strict, "df:ctrystrict")])
            rows.append([btn("⚡ Tətbiq et — indi yüklə", "df:ctrygo"), btn("🗑 Sıfırla", "df:ctryclr")])
        rows.append([btn("🔍 Axtarış sorğuları", "df:cqlist")])
        rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def prepare_clean_view(user_id: int, query: str, chat_id: int = None, msg_id: int = None):
        tracks = await asyncio.to_thread(search_depo_for_deletion, query)
        clean_wait["until"] = 0
        if not tracks:
            clean_pending.pop(user_id, None)
            text = (
                f"🔍 <b>Axtarış sorğusu:</b> \"<code>{escape(query)}</code>\"\n━━━━━━━━━━━━━━━━━━\n\n"
                f"❌ <b>Uyğun mahnı tapılmadı!</b>\n"
                f"Depoda və keş bazasında bu açar sözlərinə uyğun heç bir mahnı qeydə alınmayıb."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [btn("🔍 Başqa sorğu yaz", "df:cleanprompt")],
                ui.nav("df:panel", "Doldurucu", close_data="df:close"),
            ])
            return text, kb

        clean_pending[user_id] = {
            "query": query,
            "tracks": tracks,
            "ts": time.time(),
        }

        samples = []
        for i, t in enumerate(tracks[:8], 1):
            p = t.get("performer") or ""
            ti = t.get("title") or t.get("yt_title") or t.get("video_id")
            name = f"{p} - {ti}" if p and p not in ti else ti
            samples.append(f"{i}. <i>{escape(name[:65])}</i>")
        sample_text = "\n".join(samples)
        if len(tracks) > 8:
            sample_text += f"\n<i>... və daha {len(tracks) - 8} mahnı</i>"

        text = (
            f"🔍 <b>Axtarış sorğusu:</b> \"<code>{escape(query)}</code>\"\n━━━━━━━━━━━━━━━━━━\n"
            f"📊 Tapılan mahnı sayı: <b>{len(tracks)}</b> ədəd\n\n"
            f"<b>Nümunələr:</b>\n{sample_text}\n\n"
            f"⚠️ <b>Bu mahnıları silmək istədiyinizə əminsiniz?</b>\n"
            f"1️⃣ Telegram depo kanalından silinəcək\n"
            f"2️⃣ Keş bazasından (audio_cache) silinəcək\n"
            f"3️⃣ Qara siyahıya salınacaq (doldurucu bir daha yükləməyəcək)"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn(f"🗑 Bəli, sil ({len(tracks)} mahnı)", "df:cleango")],
            [btn("🔍 Başqa sorğu yaz", "df:cleanprompt"), btn("✖️ Ləğv et", "df:panel")],
        ])
        return text, kb

    def helpers_view(note: str = "", page: int = None):
        p = helpers
        now = time.monotonic()
        pages = max(1, -(-len(p.helpers) // HP_PER_PAGE))
        if page is not None:
            hp_page["n"] = page
        hp_page["n"] = max(0, min(hp_page["n"], pages - 1))
        start = hp_page["n"] * HP_PER_PAGE
        lines = ["🤖 <b>Köməkçi botlar</b>\n━━━━━━━━━━━━━━━━━━",
                 "<i>Depoya yükləmə köməkçilərə paylanır — Telegram flood limiti hər bota ayrıdır.</i>\n",
                 f"📌 <b>Rejim:</b> {depo_helpers.SCOPES[p.scope]}",
                 f"⏱ <b>Temp:</b> hər bot dəqiqədə <b>{p.rate}</b> mahnı",
                 f"🔁 <b>file_id çevirmə:</b> {depo_helpers.CONV_MODES[p.conv_mode]}",
                 f"   <i>{len(p.sinks())} sink çatı · {p.sink_interval():g} san. · "
                 f"⏳ çevrilməyib: {p.pending()} · ✅ istənəndə: {p.cstats['lazy']} · "
                 f"🔥 fonda: {p.cstats['warm']}"
                 + (f" · ⏭ inline-da ötürüldü: {p.cstats['inline_skip']}" if p.cstats["inline_skip"] else "")
                 + (f" · ❌ {p.cstats['fail']}" if p.cstats["fail"] else "") + "</i>"]
        if p.helpers:
            lines.append(f"🚀 <b>Nəzəri tutum:</b> ~{p.capacity()} mahnı/dəq. <i>(tək əsas bot ≈ 20)</i>")
        if p.helpers:
            ok, fl = len(p.active()), p.flooded()
            bad = len(p.helpers) - ok
            lines.append(f"🤖 <b>Cəmi:</b> {len(p.helpers)} bot · 🟢 {ok}"
                         + (f" · ⚠️ {bad} problemli" if bad else "") + (f" · ⏳ {fl} flood-da" if fl else ""))
        lines.append("")
        if pages > 1:
            lines.append(f"<i>Səhifə {hp_page['n'] + 1}/{pages}</i>")
        for i, h in enumerate(p.helpers[start:start + HP_PER_PAGE], start + 1):
            cool = int(h.cool_until - now)
            lines.append(f"{i}. <b>{escape(h.name)}</b>{' <i>(config.env)</i>' if h.from_env else ''}\n"
                         f"   {escape(h.status)} · ⬆️ {h.sent}"
                         + (f" · 🌊 flood {h.floods}" if h.floods else "")
                         + (f" · ❌ {h.fails}" if h.fails else "")
                         + (f" · ⏳ {cool} san." if cool > 0 else ""))
        if not p.helpers:
            lines.append("<i>Köməkçi bot yoxdur.</i>")
        st = p.stats
        lines += ["", f"📊 Köməkçi ilə: <b>{st['helper']}</b> · əsas bot (ehtiyat): {st['fallback']}"
                  + (f" · çevirmə xətası: {st['conv_fail']}" if st["conv_fail"] else "")]
        if p.last_error:
            lines.append(f"ℹ️ <i>{escape(p.last_error)}</i>")
        if p.helpers:
            lines.append(f"👋 Köməkçilərə yazanlar əsas bota yönləndirilir: "
                         f"{'🟢' if p.polling else '🔴'} · {p.redirects} dəfə")
        lines += ["", "<i>➕ düyməsi ilə və ya</i> <code>/depo helper add &lt;token&gt;</code> "
                  "<i>ilə əlavə et. config.env-dəki botlar yalnız oradan silinir.</i>"]
        if note:
            lines.append(f"\n{note}")
        order = list(depo_helpers.SCOPES)                       # off → filler → all → off (tək düymə, hər basışda növbəti)
        nxt = next_of(order, p.scope)
        rows = [[btn(f"📌 Rejim: {depo_helpers.SCOPES[p.scope]}  ›  {depo_helpers.SCOPES[nxt].split(' ', 1)[1]}",
                     f"df:hps:{nxt}")]]
        if p.scope != "off":                                    # söndürülübsə sürət / çevirmə düymələri lazım deyil
            rows.append([btn(f"⏱ Sürət limiti: {p.rate}/dəq. hər bot", "df:pick:hpr")])
            rows.append([btn(f"🔁 Çevirmə: {depo_helpers.CONV_MODES[p.conv_mode].split(' —')[0]}", "df:hpconv")])
        else:
            lines = [ln for ln in lines if not ln.startswith(("⏱ <b>Temp:", "🔁 <b>file_id", "   <i>", "🚀 <b>Nəzəri"))]
        rows.append([btn("➕ Köməkçi bot əlavə et", "df:hpadd"), btn("🩺 Hamısını yoxla", "df:hpchk")])
        rm = [btn(f"🗑 {i + 1}. {h.name}"[:32], f"df:hprm:{i}")
              for i, h in enumerate(p.helpers[start:start + HP_PER_PAGE], start) if not h.from_env]
        rows += [rm[j:j + 2] for j in range(0, len(rm), 2)]
        if (nav := ui.page_row(hp_page["n"], pages, lambda p: f"df:hp:{p}", wrap=True, center_cb="df:noop")):
            rows.append(nav)
        rows.append([btn("👥 Köməkçi qrupları", "hg:hub:0"), btn("❓ Bələdçi", "df:hpguide")])
        rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def dd_intro(note: str = ""):
        try:
            last = json.loads(filler._get("last_dedupe") or "null")
        except ValueError:
            last = None
        text = ("🧬 <b>Dublikatlar və depo indeksi</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Depo kanalının <b>bütün</b> postları userbot ilə oxunur və baza ilə tutuşdurulur:\n\n"
                "🔁 <b>Eyni video ID</b> — bir mahnı bir neçə dəfə yüklənib\n"
                "🎵 <b>Eyni mahnı, fərqli ID</b> — ad + artist + müddət (±8 san.) eynidir\n"
                "🧩 <b>Bazada olmayan postlar</b> — kanalda var, amma bot bilmirdi → hər restartdan sonra "
                "yenidən yükləyirdi\n\n"
                "1️⃣ Əvvəl <b>yoxlama</b> — heç nə dəyişmir\n"
                "2️⃣ <b>🔗 Bazaya bağla</b> — heç nə silinmir; bütün ID-lər bir posta bağlanır, "
                "bir daha yüklənmir\n"
                "3️⃣ İstəsən <b>🗑 dublikat postları depodan sil</b>\n\n"
                "🔄 <i>Avtomatik: gündəlik sinxronla birlikdə yoxlanır və bağlanır (silinmə — yalnız əl ilə).</i>"
                + (f"\n\n🕓 Son dəfə: {int((time.time() - last['ts']) / 3600)} saat əvvəl · "
                   f"bağlandı {last['linked']} · yönəldi {last['repointed']} · dublikat {last['dups']}" if last else "")
                + ("" if tg.available else "\n\n⚠️ <b>Userbot yoxdur</b> — bu funksiya üçün lazımdır (/depo → userbot)")
                + (f"\n\n{note}" if note else ""))
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔎 Yoxlamağa başla", "df:ddgo")],
            ui.nav("df:panel", "Doldurucu", close_data="df:close")])

    def sync_intro():
        depo_ref = audio_cache.depo_setting()
        db = get_db()
        stats = None
        try:
            depo_id = int(depo_ref) if str(depo_ref).lstrip("-").isdigit() else None
            if depo_id:
                stats = db.cache_depo_checked_stats(depo_id)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:4245]: %r", _e)

        stale_cnt = stats["stale_mids"] if stats else 0
        total_cnt = stats["total_mids"] if stats else 0
        fresh_cnt = stats["fresh_mids"] if stats else 0

        info_line = ""
        if stats and total_cnt:
            info_line = (f"📊 Baza: <b>{total_cnt:,}</b> unikal post "
                         f"(✅ yoxlanmış: {fresh_cnt:,} · ⏳ yoxlanılmalı: {stale_cnt:,})\n\n")

        ubs = []
        if tg.available:
            ubs.append("Əsas userbot")
        va = getattr(context, "voice_assistant", None)
        if va:
            for sl in getattr(va, "slots", []):
                if sl.running and sl.client and sl.client is not tg.client:
                    ubs.append(f"Asistan ({sl.name()})")
        n_ub = len(ubs)
        if n_ub >= 2:
            names_str = " + ".join(f"{i+1}️⃣ {name}" for i, name in enumerate(ubs))
            ub_txt = f"⚡ <b>{n_ub} userbot aktivdir</b> ({n_ub} qat sürətli paralel rejim: {names_str}).\n"
        elif n_ub == 1:
            ub_txt = "📱 1 userbot aktivdir (ultra-sürətli axın).\n"
        else:
            ub_txt = "⚠️ Userbot yoxdur — Bot API ilə yoxlanılır.\n"
        text = ("🧹 <b>Depo ↔ baza sinxronizasiyası</b>\n━━━━━━━━━━━━━━━━━━\n"
                "Bazadakı depo postlarının kanalda mövcudluğu yoxlanılır. "
                "Silinmiş mahnılar bazadan təmizlənir ki, axtarışda xəta verməsin.\n\n"
                + info_line
                + "⚡ <b>Tez yoxlama:</b> Yalnız yeni və ya son 7 gündə yoxlanmamış postları saniyələr içində yoxlayır.\n"
                + "🔍 <b>Tam yoxlama:</b> Bütün kanalı sürətli axınla skan edib bütün bazanı tam uyğunlaşdırır.\n\n"
                + ub_txt
                + "🔄 Avtomatik: hər 24 saatdan bir fonda yoxlanılır.")

        rows = [
            [btn(f"⚡ Tez yoxla ({stale_cnt:,} post)" if stale_cnt else "⚡ Tez yoxla", "df:syncgo:quick"),
             btn("🔍 Tam yoxlama", "df:syncgo:full")],
        ]
        if filler.removed:
            rows.append([btn(f"♻️ Silinmiş blokunu təmizlə ({len(filler.removed)})", "df:unblockall")])
        rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    def userbot_line() -> str:
        if not getattr(context, "userbot1_enabled", True):
            return "📱 Userbot: 🔴 <i>söndürülüb (əl ilə dayandırılıb)</i>"
        if not tg.available:
            return "📱 Userbot: <i>qoşulmayıb (yalnız public kanallar, veb)</i>"
        if tg.client.is_connected() and tg.me:
            line = f"📱 Userbot: 🟢 @{escape(tg.me.username or str(tg.me.id))}"
        elif tg.client.is_connected():
            line = "📱 Userbot: 🟡 qoşulub, avtorizasiya yoxlanılır"
        else:
            line = "📱 Userbot: 🔴 bağlıdır"
        return line + (" · 🔎 skan gedir" if scan_state["running"] else "")

    def panel(cs: dict | None = None):
        st = filler.stats
        depo = audio_cache.depo_status()
        if cs is None:
            try:
                cs = get_db().cache_stats(depo["id"])
            except Exception:
                cs = {"songs": 0, "hits": 0, "size": 0}
        on = filler.running
        uptime = ""
        if on and st["started"]:
            m = int((time.time() - st["started"]) / 60)
            uptime = f" · {m // 60} saat {m % 60} dəq."
        lines = [
            "📦 <b>Depo doldurucu</b>\n",
            f"📶 Vəziyyət: <b>{'🟢 işləyir' if on else '🔴 söndürülüb'}</b>{uptime}",
            "⏱ Fasilə: " + ("🤖 avto → " if filler.delay_mode == "auto" else "")
            + f"<b>{filler.delay} san.</b> (hər işçi mahnılar arası)"
            + (" <i>— hesablar sağlam: 2 · limit var: 10 · işlək yoxdur: 30</i>" if filler.delay_mode == "auto" else ""),
            "🧵 Rejim: " + ("🤖 avto → " if filler.workers_mode == "auto" else "")
            + f"<b>{workers_label(filler.workers)}</b>"
            + (" <i>— depo proseslərinə bərabər (⚙️ avto-miqyas)</i>" if filler.workers_mode == "auto" else ""),
            f"📦 Depo: <b>{cs['songs']}</b> mahnı · {cs['size'] / 1048576:.0f} MB · {escape(depo['ref'])}"
            + ("" if depo["id"] or not on else " ⚠️"),
            userbot_line(),
            "",
            f"⬆️ Yükləndi: <b>{st['done']}</b> · ⏭ artıq depoda idi: {st['cached']} · ❌ {st['failed']}",
            f"📋 Növbədə: <b>{len(filler.queue)}</b>"
            + (f" / {st['queue_built']}" if st["queue_built"] else "")
            + (f" · 🌍 ölkə: <b>{len(filler.country_queue)}</b>" if filler.countries() else "")
            + (" · 🎯 yalnız ölkələr" if filler.country_strict() else ""),
        ]
        cur = sorted(filler.current.items())
        for i, title in cur[:4]:
            lines.append(f"⏳ {'İndi' if len(cur) == 1 else f'#{i + 1}'}: <i>{escape(title[:55])}</i>")
        if len(cur) > 4:
            lines.append(f"⏳ <i>+{len(cur) - 4} işçi daha işləyir — hamısı 📊 Canlı monitorda</i>")
        if st["note"]:
            lines.append(f"ℹ️ {escape(st['note'])}")
        if helpers.helpers:
            fl = helpers.flooded()
            lines.append(f"🤖 Köməkçi botlar: <b>{len(helpers.active())}/{len(helpers.helpers)}</b> · "
                         f"{depo_helpers.SCOPES[helpers.scope]} · ⬆️ {helpers.stats['helper']}"
                         + (f" · ⏳ flood {fl}" if fl else ""))
        if filler.retry:
            nxt = filler.next_retry_in()
            mins = max(0, int((nxt - time.time()) / 60)) if nxt else 0
            lines.append(f"🔁 Təkrar cəhd gözləyir: <b>{len(filler.retry)}</b>"
                         + (f" · növbəti {mins} dəq. sonra" if mins else " · indi"))
        nfail, _ = filler.failed_list(0)
        if nfail:
            lines.append(f"❌ Alınmayanlar siyahısı: <b>{nfail}</b>")
        if filler.removed:
            lines.append(f"🧹 Depodan silinib (yenidən yüklənmir): {len(filler.removed)}")
        try:
            ls = json.loads(filler._get("last_sync") or "null")
        except ValueError:
            ls = None
        if ls:
            ago = int((time.time() - ls["ts"]) / 3600)
            lines.append(f"🧹 Son sinxronizasiya: {ago} saat əvvəl · {ls['total']} post · "
                         f"yoxdur: {ls['missing']}" + (" · silindi" if ls.get("applied") else ""))
        if filler.restored:
            lines.append(f"💾 <i>Restartdan sonra {filler.restored} mahnı növbədən bərpa olundu</i>")
        stall = filler.stall_reason()
        if stall:
            lines.append(f"\n⚠️ <b>Dayanıb:</b> {escape(stall)} — <i>⚡ Məcburi davam bas</i>")
        lines.append(ytm_line())
        srcs = filler.sources()
        lines.append(f"\n📋 <b>Mənbələr</b> ({len(srcs)}) + 👥 istifadəçilərin sevdikləri + 🔀 Mix")
        for s in srcs[:8]:
            lines.append(f"   • {escape(s.get('label') or s['id'])}")
        if on:
            rows = [
                [btn("⏸ Söndür", "df:off"), btn("📊 Canlı monitor", "dt:start")],
                *mode_rows("⏱ Fasilə", filler.delay_mode == "auto", "df:dm",
                           btn(f"⏱ Fasilə: {filler.delay} san.", "df:pick:d")),
                *mode_rows("🧵 Worker", filler.workers_mode == "auto", "df:wm",
                           btn(f"🧵 Worker: {workers_label(filler.workers)}", "df:pick:w")),
                [ui.toggle("🎵 YT Music", filler.ytm_on, "df:ytm", compact=True)],
                [btn("🔁 Yenidən qur", "df:rebuild"), btn("📋 Mənbələr", "df:sources")],
                [btn(("⚡ Məcburi davam ⚠️" if stall else "⚡ Məcburi davam"), "df:force"),
                 btn(f"📋 Gözləyənlər ({len(filler.retry)}/❌{nfail})", "df:q:r:0" if filler.retry or not nfail else "df:q:f:0")],
                [btn("🧹 Depo sinxronu", "df:sync"), btn("🧬 Dublikatlar", "df:dd")],
                [btn("🗑 Sorğu ilə sil", "df:cleanprompt"), btn("🔍 Axtarış sorğuları", "df:cqlist")],
                [btn("🌍 Ölkələr: " + (", ".join(filler.countries()) or "hamısı"), "df:ctry"),
                 btn(f"🤖 Köməkçilər ({len(helpers.active())}/{len(helpers.helpers)})", "df:hp")],
            ]
        else:
            rows = [
                [btn("▶️ İşə sal", "df:on"), btn("📊 Canlı monitor", "dt:start")],
                [btn("📋 Mənbələr", "df:sources"), btn("🧹 Depo sinxronu", "df:sync")],
                [btn("🧬 Dublikatlar / indeks", "df:dd")],
                [btn("🗑 Sorğu ilə sil", "df:cleanprompt"),
                 btn(f"🤖 Köməkçilər ({len(helpers.active())}/{len(helpers.helpers)})", "df:hp")],
            ]
        if scan_state["running"]:
            rows.append([btn("⏹ Skanı dayandır", "scan:stop")])
        rows.append(ui.nav("menu:depo", "Depo", close_data="df:close", refresh_data="df:panel"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def panel_async():
        depo = audio_cache.depo_status()
        try:
            cs = await asyncio.to_thread(get_db().cache_stats, depo["id"])
        except Exception:
            cs = {"songs": 0, "hits": 0, "size": 0}
        return panel(cs)

    return SimpleNamespace(
        ytm_line=ytm_line,
        sources_view=sources_view,
        df_delay_picker_view=df_delay_picker_view,
        df_worker_picker_view=df_worker_picker_view,
        df_rate_picker_view=df_rate_picker_view,
        fail_view=fail_view,
        queue_view=queue_view,
        country_queries_list_view=country_queries_list_view,
        country_queries_view=country_queries_view,
        countries_view=countries_view,
        prepare_clean_view=prepare_clean_view,
        helpers_view=helpers_view,
        dd_intro=dd_intro,
        sync_intro=sync_intro,
        userbot_line=userbot_line,
        panel=panel,
        panel_async=panel_async,
    )
