"""
bot.menus.views.youtube_accs
YouTube hub, hesab rotasiyası, login və yükləmə formatı pəncərələri.
"""

import contextlib
import logging
import os
import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel
from bot.menus.navigation import nav_row
from bot.menus.views.base import LINE, SECTIONS, crumb
from common.menu import _btn
from common.kv import next_of

logger = logging.getLogger(__name__)

FMT_PRESETS = [
    ("⚡ Birbaşa m4a", True, True, "m4a birbaşa; yoxdursa ffmpeg"),
    ("⚡ Yalnız birbaşa m4a", True, False, "yalnız m4a; çevirmə yoxdur"),
    ("🎞 Yalnız ffmpeg ilə m4a", False, True, "ən yaxşı audio + çevirmə"),
    ("📄 Orijinal format", False, False, "çevirməsiz, olduğu kimi"),
]


def _yt_state() -> dict:
    try:
        from bot.core import webprofile as wp
        from bot.core import youtube_handler as yh
    except Exception:
        return {}
    m = yh.download_mode()
    preset = next((p for p in FMT_PRESETS if p[1] == m["direct_m4a"] and p[2] == m["ffmpeg"]), FMT_PRESETS[0])
    return {
        "yh": yh,
        "wp": wp,
        "cfg": wp.current(),
        "preset": preset,
        "src": yh.search_source(),
        "pot": yh._pot_cache.get("ok", False),
        "pot_checked": bool(yh._pot_cache.get("checked")),
        "cd": yh.cooldown_left(),
        "has_ff": yh.ffmpeg_available(),
    }


def yt_button_label() -> str:
    st = _yt_state()
    if not st:
        return "🎵 YouTube · ❌"
    if st["cd"]:
        return f"🎵 YouTube · ⏸ {st['cd'] // 60 + 1} dəq"
    if st["pot_checked"] and not st["pot"]:
        return "🎵 YouTube · ⚠️"
    return "🎵 YouTube"


def _yta_label() -> str:
    with contextlib.suppress(Exception):
        from bot.core import yt_accounts as ya

        accs = ya.accounts()
        blocked = sum(1 for a in accs if ya.block_left(a["id"]))
        return f"{len(accs)}" + (f" · ⏸ {blocked}" if blocked else "")
    return "?"


def yt_hub_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    st = _yt_state()
    if not st:
        return "❌ youtube_handler / webprofile yüklənmədi", InlineKeyboardMarkup(inline_keyboard=[nav_row("mus")])
    yh, preset = st["yh"], st["preset"]
    st["pot"] = yh.pot_provider_up()

    ps = yh.pot_status()
    pot_txt = (f"PO token provider ✅ v{ps['version'] or '?'}" if ps["http"]
               else "PO token provider ⚠️ port açıqdır, /ping cavab vermir" if ps["ok"]
               else "PO token provider ❌ işləmir")
    if st["cd"]:
        reason = ("IP / hesab səviyyəsində blok — cookies-i yenilə, proxy / Tor sına və ya gözlə"
                  if st["pot"] else "PO token provider işləmir — docker konteynerini yoxla")
        health = f"⏸ <b>403 cooldown:</b> {st['cd']} san qalıb — {reason}\n{pot_txt}"
    elif not st["pot"]:
        health = (
            f"⚠️ <b>{pot_txt}</b> — çoxlu yükləmədən sonra 403 ehtimalı yüksəkdir\n"
            "<i>Yoxla:</i> <code>docker ps</code> (konteyner <code>brainicism/bgutil-ytdlp-pot-provider</code>, port 4416)"
        )
    else:
        health = f"✅ <b>Hər şey qaydasındadır</b> — {pot_txt}, cooldown yoxdur"

    lines = [
        crumb(SECTIONS["mus"], "🎵 YouTube"),
        LINE,
        health,
        "",
        f"🔑 <b>Hesablar:</b> {_yta_label()} <i>(rotasiya / limitsiz yükləmə)</i>",
        f"🎧 <b>Format:</b> {preset[0]}",
        f"🔎 <b>Axtarış mənbəyi:</b> {yh.SEARCH_SOURCES[st['src']]}",
        f"⬇️ <b>Toplu yükləmə:</b> {yh.batch_parallel()} paralel",
        "",
        "<i>Hesablar 🔑, yükləmə formatı 🎧 və göndərilən fayl formatı (MP3 / M4A) 🎛 bu ekrandan idarə olunur.</i>",
    ]
    if note:
        lines.append(f"\n{note}")

    rows = [
        [_btn(f"🔑 Hesablar ({_yta_label()})", "yta"), _btn("🎧 Yükləmə formatı", "dlf")],
        [_btn("🔎 Axtarış mənbəyi", "srch"), _btn(f"⬇️ Toplu: {yh.batch_parallel()} paralel", "menu:yt:pick:bp")],
        [panel.btn("🎛 MP3 / M4A kodek", "codec:panel")],
        nav_row("mus", refresh="yt"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_list_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts as ya

    accs = ya.accounts()
    m = ya.mode()
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları"),
        LINE,
        f"Rotasiya: <b>{ya.MODES[m]}</b> — "
        + {
            "auto": "prioritet sırası ilə, limit olanda avtomatik növbəti hesaba keçir",
            "rr": "hər yükləmə növbəti hesabla (yük paylanır), limitli hesab ötürülür",
            "manual": "hesabı sən seçirsən, limitdə keçid yoxdur",
        }[m],
        f"Hazırda: {escape(ya.summary_line())}",
        "",
    ]
    for n, a in enumerate(accs, 1):
        st = ya.stats(a["id"])
        roles = " ".join(ya.ROLES[r].split()[0] for r in a["roles"]) or "—"
        lines.append(
            f"{n}. <b>{escape(a['name'])}</b> — {ya.status(a)} · {roles}\n"
            f"     {escape(ya.describe(a))}"
            + (f" · ✅ {st['ok']} ❌ {st['fail']}" if st["ok"] or st["fail"] else "")
        )
    if not accs:
        lines.append("<i>Heç bir YouTube hesabı əlavə edilməyib.</i>")
    if note:
        lines.append(f"\n{note}")

    rows = []
    for a in accs:
        roles_s = "".join(ya.ROLES[r].split()[0] for r in a["roles"]) or "—"
        st_icon = ya.status(a).split()[0]
        rows.append([_btn(f"{st_icon} {a['name'][:24]} · {roles_s}", f"yta:a:{a['id']}")])
    rows.insert(0, [_btn(f"🔁 Rotasiya: {ya.MODES[m]}", "yta:mode")])
    rows.append([_btn("🔐 Google hesabı ilə giriş", "yta:login")])
    rows.append([_btn("➕ cookies.txt göndər", "yta:addf"), _btn("➕ Brauzer profili", "yta:addb")])
    if any(ya.block_left(a["id"]) for a in accs):
        rows.append([_btn("⏭ Bütün blokları götür", "yta:ub:all")])
    rows.append([_btn(f"🧪 Hamısını test et ({len(accs)})", "yta:tall")])
    rows.append([_btn("📖 Necə əlavə edim?", "yta:help")])
    rows.append(nav_row("yt", refresh="yta"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_acc_view(acc_id: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts as ya

    a = ya.get(acc_id)
    if not a:
        return yta_list_view("⚠️ Hesab tapılmadı")
    st, test = ya.stats(acc_id), ya.LAST_TEST.get(acc_id)
    lines = [
        crumb(
            SECTIONS["mus"],
            "🔑 YouTube hesabları",
            f"{ya.KINDS[a['kind']].split()[0]} {escape(a['name'])}",
        ),
        LINE,
        f"Növ: {ya.KINDS[a['kind']]}"
        + (
            ""
            if ya.describe(a) == ya.KINDS[a["kind"]]
            else f" — {escape(ya.describe(a))}"
        ),
        f"Vəziyyət: <b>{ya.status(a)}</b>"
        + (
            f" — {escape(st['reason'])}"
            if ya.block_left(acc_id) and st["reason"]
            else ""
        ),
        "Rollar: "
        + (
            ", ".join(ya.ROLES[r] for r in a["roles"])
            or "<i>yoxdur — heç nə üçün istifadə olunmur</i>"
        ),
        f"Bu sessiyada: ✅ {st['ok']} uğurlu · ❌ {st['fail']} rate-limit",
    ]
    if test:
        lines.append(
            f"🧪 Son test ({time.strftime('%H:%M', time.localtime(test['ts']))}): "
            + (
                f"✅ girişli · {test['yt']} YouTube cookie"
                if test["ok"]
                else f"❌ {escape(str(test['error'])[:150])}"
            )
        )
    if a["kind"] == "main":
        lines.append(
            "\n<i>⭐ Əsas hesab — 🎵 YouTube → 📖 Brauzer bölməsindəki cookie ayarıdır.</i>"
        )
    if ya.test_failed(acc_id) or not ya.usable(a):
        lines.append(
            "\n⚠️ <i>Google sessiyası bitib və ya hesaba giriş yoxdur. Rotasiyaya qaytarmaq üçün <b>🔐 Yenidən daxil ol</b> düyməsinə bas.</i>"
        )
    if ya.can_open(a):
        lines.append("\n🔘 <i>Aşağıdakı düymələrə basanda bu hesabın brauzer profili serverin ekranında açılır "
                     "(məs. Gmail-ə baxmaq üçün).</i>")
    if note:
        lines.append(f"\n{note}")
    on = a.get("on", True)
    rows = [
        [
            panel.check("📦 Depo", "depo" in a["roles"], f"menu:yta:r:{acc_id}:depo"),
            panel.check("🎵 İstifadəçi", "user" in a["roles"], f"menu:yta:r:{acc_id}:user"),
        ],
        [panel.toggle("Hesab", on, f"menu:yta:on:{acc_id}"), _btn("🧪 Test", f"yta:t:{acc_id}")],
    ]
    r = []
    if ya.block_left(acc_id):
        r.append(_btn("⏭ Bloku götür", f"yta:ub:{acc_id}"))
    ids = [x["id"] for x in ya.accounts()]
    if acc_id != "main" and acc_id in ids and ids.index(acc_id) > 1:  # ⭐ əsasdan yuxarı keçmir
        r.append(_btn("⬆️ Prioriteti qaldır", f"yta:up:{acc_id}"))
    if r:
        rows.append(r)
    if a.get("login") == "pending":
        rows.insert(0, [_btn("🖥 Server girişini davam etdir", f"yta:lr:{acc_id}")])
    elif ya.test_failed(acc_id) or not ya.usable(a):
        btn_label = (
            "🔐 Google ilə yenidən daxil ol"
            if a["kind"] == "browser"
            else (
                "🔄 Yeni cookies.txt göndər"
                if a["kind"] == "file"
                else "🔐 Brauzer / cookies ayarı"
            )
        )
        rows.insert(0, [_btn(btn_label, f"yta:relogin:{acc_id}")])
    if ya.mode() == "manual":
        pr = []
        for role, lane in (("depo", "depo"), ("user", "download")):
            cur = (ya.pinned(lane) or {}).get("id") == acc_id
            pr.append(panel.choice(f"📌 {ya.ROLES[role]}", cur, f"menu:yta:pin:{acc_id}:{role}"))
        rows.insert(2, pr)
    if ya.can_open(a):                                  # 🔘 sahibin adlandırdığı düymələr — basanda profil serverdə açılır
        rows += panel.grid([_btn(x["name"], f"yta:ol:{acc_id}:{i}") for i, x in enumerate(ya.links(a))], 2)
        rows.append([_btn("🔘 Düymələri idarə et", f"yta:lk:{acc_id}")])
    r = [_btn("✏️ Adı dəyiş", f"yta:nm:{acc_id}")]
    if a["kind"] == "file":
        r.append(_btn("🔄 cookies.txt yenilə", f"yta:rf:{acc_id}"))
    elif a["kind"] == "browser" and ya.can_server_login(a):
        r.append(_btn("🔐 Yenidən daxil ol", f"yta:relogin:{acc_id}"))
    rows.append(r)
    if acc_id != "main":
        rows.append([_btn("🗑 Hesabı sil", f"yta:del:{acc_id}")])
    rows.append(nav_row("yta"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _yta_browser_profiles() -> list:
    from bot.core import webprofile as wp

    out = []
    for key, profs in wp.detect().items():
        for i, p in enumerate(profs):
            out.append((key, i, p))
    return out


def yta_browser_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import webprofile as wp
    from bot.core import yt_accounts as ya

    cfg = wp.current()
    used = {(a.get("browser"), a.get("profile")) for a in ya.accounts() if a["kind"] == "browser"}
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "➕ Brauzer profili"),
        LINE,
        "Serverdə tapılan brauzer profilləri. 2-ci YouTube hesabına <b>ayrı profildə</b> girilməlidir — "
        "⭐ əsas hesabın profilini seçmək eyni hesabı ikinci dəfə əlavə edir.",
        "",
    ]
    profs = _yta_browser_profiles()
    if not profs:
        lines.append(
            "<i>Profil tapılmadı. Serverdə məs. <code>firefox -P</code> ilə yeni profil yarat, "
            "orada youtube.com-a 2-ci hesabla gir, sonra 🔄.</i>"
        )
    if note:
        lines.append(f"\n{note}")
    rows = []
    for key, i, p in profs[:16]:
        b = wp.BROWSERS[key]
        tag = (
            " ⭐"
            if key == cfg["browser"] and (p["path"] == cfg["profile"] or (not cfg["profile"] and i == 0))
            else ""
        )
        tag += " ✔️" if (key, p["path"]) in used else ""
        rows.append(
            [_btn(f"{b['emoji']} {b['label']} · {p['name'][:28]}{tag}", f"yta:bp:{key}:{i}")]
        )
    rows.append([_btn("🔄 Yenidən axtar", "yta:addb")])
    rows.append(nav_row("yta"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_login_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts as ya

    ch = ya.chrome_bin()
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "🔐 Google hesabı ilə giriş"),
        LINE,
        "Yeni YouTube (Google) hesabını necə qoşmaq istəyirsən?",
        "",
        "🖥 <b>Server cihazında</b> — serverin ekranında ayrıca, boş brauzer pəncərəsi açılır "
        "(Google giriş səhifəsi). Ekran qarşısında və ya uzaqdan masaüstü ilə orada daxil olursan, "
        "pəncərəni bağlayıb ✅ basırsan. Cookie-lər sonra özü təzələnir.",
        "     "
        + (
            f"<i>brauzer: {escape(os.path.basename(ch[0]))}</i>"
            if ch
            else "⚠️ <i>serverdə Chrome / Chromium yoxdur</i>"
        ),
        "",
        "📱 <b>Öz cihazımla</b> — kompüterində / telefonunda brauzerdə daxil olursan, "
        "cookies.txt faylını çıxarıb bura göndərirsən.",
        "",
        "🔒 <i>Parolunu heç vaxt bota yazma — giriş yalnız Google-un öz səhifəsində olur.</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = [
        [_btn("🖥 Server cihazında giriş", "yta:ls")],
        [_btn("📱 Öz cihazımla giriş", "yta:lo")],
        nav_row("yta"),
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_links_view(acc_id: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    """🔘 Hesabın düymələri: ad dəyiş / sil / hazır linklərdən və ya öz linkindən əlavə et."""
    from bot.core import yt_accounts as ya

    a = ya.get(acc_id)
    if not a or not ya.can_open(a):
        return yta_list_view("⚠️ Bu hesabın brauzer profili yoxdur — düymələr yalnız brauzer profilli hesablarda olur")
    items = ya.links(a)
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", escape(a["name"]), "🔘 Düymələr"),
        LINE,
        f"Düymələr: <b>{len(items)}/{ya.MAX_LINKS}</b> — hesabın səhifəsində görünür; basanda profil serverin "
        "ekranında həmin linklə açılır.",
        "",
    ]
    lines += [f"{i + 1}. <b>{escape(x['name'])}</b> — <code>{escape(x['url'][:60])}</code>" for i, x in enumerate(items)] \
        or ["<i>Düymə yoxdur — aşağıdan əlavə et.</i>"]
    lines.append("\n<i>✏️ adı dəyişir · 🗑 silir. Əlavə edəndən sonra ✏️ ilə öz adını ver.</i>")
    if note:
        lines.append(f"\n{note}")
    rows = [[_btn(f"✏️ {x['name']}", f"yta:ln:{acc_id}:{i}"), _btn("🗑", f"yta:ld:{acc_id}:{i}")]
            for i, x in enumerate(items)]
    if len(items) < ya.MAX_LINKS:
        rows += panel.grid([_btn(f"➕ {label}", f"yta:la:{acc_id}:{key}") for key, (label, _) in ya.LINK_PRESETS.items()], 2)
        rows.append([_btn("🔗 Öz linkim", f"yta:la:{acc_id}:own")])
    rows.append(panel.nav(f"menu:yta:a:{acc_id}", escape(a["name"])[:20], close_data="menu:cancel"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_server_login_view(acc_id: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts as ya

    a = ya.get(acc_id)
    if not a:
        return yta_list_view("⚠️ Giriş hesabı tapılmadı")
    running = ya.login_open(acc_id)
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "🖥 Server girişi"),
        LINE,
        f"Hesab: <b>{escape(a['name'])}</b> — {ya.status(a)}",
        "Pəncərə: " + ("🟢 serverin ekranında açıqdır" if running else "⚫ bağlıdır"),
        "",
        "1. Serverin ekranında açılan brauzerdə Google hesabına daxil ol "
        "(lazım olsa 2 addımlı təsdiqi telefonda et).",
        "2. YouTube açılandan sonra 5–10 san. gözlə və pəncərəni <b>özün bağla</b> (✕, çıxış etmə!) — "
        "Chrome bağlananda cookie-ləri diskə yazır.",
        "3. Aşağıda ✅ <b>Girişi bitirdim</b> bas — bot profili yoxlayıb hesabı aktiv edəcək.",
        "",
        "<i>Ekran yanında deyilsənsə: KDE «Uzaq masaüstü» (Systemsettings → Remote Desktop) "
        "və ya istənilən VNC / RDP ilə qoşul.</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = [[_btn("✅ Girişi bitirdim", f"yta:lf:{acc_id}")]]
    if running:
        rows.append([_btn("⚠️ Məcburi bağla və yoxla", f"yta:lfx:{acc_id}")])
    rows += [
        [
            _btn("🔁 Pəncərəni yenidən aç", f"yta:lr:{acc_id}"),
            _btn("✖️ Ləğv et", f"yta:lc:{acc_id}"),
        ],
        [_btn("⬅️ Hesablar", "yta")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def yta_own_device_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "📱 Öz cihazımla giriş"),
        LINE,
        "💻 <b>Kompüterdə (Chrome / Firefox / Edge):</b>",
        "1. «Get cookies.txt LOCALLY» uzantısını quraşdır.",
        "2. <b>Gizli (inkoqnito) pəncərə</b> aç, uzantıya orada icazə ver.",
        "3. youtube.com → yeni Google hesabına daxil ol.",
        "4. youtube.com səhifəsində uzantı → <b>Export</b> → <code>cookies.txt</code>.",
        "5. Pəncərəni <b>çıxış etmədən</b> bağla.",
        "",
        "📱 <b>Android-də:</b> Firefox (və ya Kiwi / Lemur) → əlavələrdən «Get cookies.txt» → "
        "youtube.com-a daxil ol → Export → faylı Telegram-dan bura göndər.",
        "",
        "📎 <b>İndi faylı bu çata sənəd kimi göndər</b> — caption-a hesabın adını yaza bilərsən. "
        "Fayl yoxlanılır, serverdə gizli saxlanılır, mesaj çatdan silinir.",
        "",
        "<i>iPhone-da uzantı yoxdur — kompüterdən istifadə et.</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Ləğv et", "yta:login")]])


def yta_help_view() -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        crumb(SECTIONS["mus"], "🔑 YouTube hesabları", "📖 Necə əlavə edim?"),
        LINE,
        "<b>📄 cookies.txt ilə (server brauzeri lazım deyil):</b>",
        "1. Kompüterdə brauzerin <b>gizli / ayrı pəncərəsində</b> youtube.com-a 2-ci hesabla gir.",
        "2. «Get cookies.txt LOCALLY» uzantısı ilə youtube.com üçün faylı ixrac et.",
        "3. ➕ cookies.txt göndər → faylı bu çata <b>sənəd kimi</b> göndər (caption = hesabın adı).",
        "4. Pəncərəni <b>çıxış etmədən bağla</b> — «Sign out» etsən cookie-lər etibarsız olur.",
        "",
        "<b>🌐 Brauzer profili ilə (cookie-lər özü təzələnir):</b>",
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[nav_row("yta")])


def dl_format_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    try:
        from bot.core.youtube_handler import download_mode, ffmpeg_available
    except Exception as e:
        return f"❌ youtube_handler yüklənmədi: <code>{escape(str(e))[:200]}</code>", InlineKeyboardMarkup(
            inline_keyboard=[nav_row("yt")]
        )
    m = download_mode()
    has_ff = ffmpeg_available()
    direct, ff = m["direct_m4a"], m["ffmpeg"]
    text = (
        "🎧 <b>Yükləmə formatı</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Birbaşa m4a:</b> {'✅ açıq' if direct else '❌ bağlı'}\n"
        f"🎞 <b>ffmpeg çevirmə:</b> {'✅ açıq' if ff else '❌ bağlı'}"
        f"{'' if has_ff else ' <i>(⚠️ ffmpeg sistemdə tapılmadı)</i>'}\n"
    )
    if note:
        text += f"\nℹ️ {note}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                panel.toggle("⚡ Birbaşa m4a", direct, "menu:dlf_direct", compact=True),
                panel.toggle("🎞 ffmpeg", ff, "menu:dlf_ffmpeg", compact=True),
            ],
            nav_row("yt", refresh="dlf"),
        ]
    )
    return text, kb


def search_source_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    try:
        from bot.core.youtube_handler import SEARCH_SOURCES, search_source
    except Exception as e:
        return f"❌ youtube_handler yüklənmədi: <code>{escape(str(e))[:200]}</code>", InlineKeyboardMarkup(
            inline_keyboard=[nav_row("yt")]
        )
    from bot.core.youtube_handler import search_mode
    cur = search_source()
    auto = search_mode() == "auto"
    text = (f"🔎 <b>Axtarış mənbəyi</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Hazırda:</b> {SEARCH_SOURCES.get(cur, cur)}\n"
            f"⚙️ <b>Rejim:</b> {'🤖 Avto' if auto else '✋ Manual'}\n\n"
            + ("<i>🤖 Avto: əvvəl YouTube Music; nəticələr sorğuya uyğun deyilsə YouTube göstərilir.</i>" if auto else
               "<i>✋ Manual: yalnız seçdiyiniz mənbə (YT Music-də heç nə tapılmasa YouTube ehtiyat kimi göstərilir).</i>"))
    if note:
        text += f"\n\nℹ️ {note}"
    skeys = list(SEARCH_SOURCES)                                     # tək düymə: hər basışda növbəti mənbə (→ Manual)
    nxt = next_of(skeys, cur)
    btn_cycle = panel.btn(f"{SEARCH_SOURCES.get(cur, cur)}  ›  {SEARCH_SOURCES[nxt]}", f"menu:srch:{nxt}")
    btn_mode = (panel.btn(f"✋ Manual-a keç ({SEARCH_SOURCES.get(cur, cur)})", f"menu:srch:{cur}") if auto
                else panel.btn("🤖 Avto-ya keç", "menu:srch:auto"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn_cycle], [btn_mode], nav_row("yt", refresh="srch")])
    return text, kb


def yt_picker_view(note: str = ""):
    from bot.core import youtube_handler as yh

    cur = yh.batch_parallel()
    lines = [
        crumb(SECTIONS["mus"]) + " › 🎵 <b>YouTube › Toplu yükləmə</b>",
        LINE,
        "⬇️ <b>Toplu yükləmə paralelliyi:</b>\n",
        f"Hazırkı dəyər: <b>{cur} paralel</b>\n",
        "<i>(Hamısını yüklə, Spotify playlist / albom yüklənərkən eyni anda neçə mahnı hazırlansın. Çox paralel 403 riskini artırır)</i>",
    ]
    if note:
        lines.append(f"\n{note}")
    rows = panel.stepper("menu:yt:vstep:bp:", "menu:yt:vinp:bp") + [nav_row("yt")]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def web_profile_view(note: str = ""):
    """🌐 Brauzer / cookies: cookie mənbəyi, brauzer, profil, keyring, User-Agent, test."""
    try:
        from bot.core import webprofile as wp
    except Exception as e:
        return f"❌ webprofile yüklənmədi: <code>{escape(str(e))[:200]}</code>", InlineKeyboardMarkup(
            inline_keyboard=[nav_row("yt")]
        )
    from bot.menus.keyboards import mode_btn

    cfg = wp.current()
    found = wp.detect()
    eff = wp.effective_source(cfg)
    b = wp.BROWSERS[cfg["browser"]]
    cf = wp.cookies_file()
    if not cf:
        cf_line = "<i>təyin olunmayıb</i> (config.env → <code>YOUTUBE_COOKIES_FILE</code>)"
    elif os.path.isfile(cf):
        cf_line = f"✅ <code>{escape(cf)}</code>"
    else:
        cf_line = f"⚠️ tapılmadı: <code>{escape(cf)}</code>"
    src_line = wp.COOKIE_SOURCES[cfg["cookies"]]
    if cfg["cookies"] == "auto":
        src_line += f" → <b>{'📄 cookies.txt' if eff == 'file' else '🌐 brauzer'}</b>"
    sel_found = found.get(cfg["browser"]) or []
    lines = [
        "🌐 <b>Brauzer və cookies</b>\n━━━━━━━━━━━━━━━━━━",
        f"🍪 <b>Cookie mənbəyi:</b> {src_line}",
        "🌐 <b>Brauzer:</b> "
        + ("🤖 avto → " if cfg["mode"] == "auto" else "✋ ")
        + f"{b['emoji']} {b['label']}"
        + ("" if sel_found else " · ⚠️ <i>serverdə profili tapılmadı</i>"),
        f"👤 <b>Profil:</b> {escape(wp.profile_label(cfg))}",
    ]
    if b["family"] == "chromium":
        lines.append(
            "🔐 <b>Keyring:</b> "
            + (
                f"🤖 avto → {cfg['keyring'] or 'hələ tapılmayıb (🧪 test)'}"
                if cfg["kr_mode"] == "auto"
                else f"✋ {cfg['keyring'] or 'yt-dlp özü'}"
            )
        )
    lines += [
        f"🕵️ <b>User-Agent:</b> {wp.UA_MODES[cfg['ua']]}",
        f"📄 <b>cookies.txt:</b> {cf_line}",
        "━━━━━━━━━━━━━━━━━━",
        "<b>Serverdə tapılan brauzerlər:</b>",
    ]
    have = [k for k, v in found.items() if v]
    lines.append(
        "  "
        + (
            " · ".join(
                f"{wp.BROWSERS[k]['emoji']} {wp.BROWSERS[k]['label']} ({len(found[k])})"
                for k in have
            )
            if have
            else "<i>heç biri — cookies.txt istifadə et</i>"
        )
    )
    t = wp.LAST_TEST
    if t:
        if t.get("error") and not t.get("ok"):
            res = f"❌ {escape(str(t['error'])[:200])}"
        elif t["source"] == "none":
            res = "🚫 cookiesiz rejim — yoxlanacaq cookie yoxdur"
        else:
            res = (
                f"✅ {t['total']} cookie · YouTube: <b>{t['yt']}</b> · "
                + (
                    "🔓 hesaba <b>daxil olunub</b>"
                    if t["logged_in"]
                    else "🔒 hesaba giriş yoxdur (anonim)"
                )
            )
        lines.append(f"\n🧪 <b>Son test</b> ({escape(t['label'])}, {t.get('secs', 0):.1f} san.):\n{res}")
    lines.append(
        "\n<i>Dəyişiklik növbəti yükləmə / axtarışdan etibarən tətbiq olunur — restart lazım deyil.</i>"
    )
    if note:
        lines.append(f"\n{note}")

    src_btns = [
        panel.choice(label, k == cfg["cookies"], f"menu:web:src:{k}")
        for k, label in wp.COOKIE_SOURCES.items()
    ]
    manual = cfg["mode"] == "manual"
    br_btns = []
    for k, info in wp.BROWSERS.items() if manual else ():  # 🤖 avto — brauzer siyahısı gizli
        if k == "safari" and not found.get(k):
            continue  # macOS deyilsə göstərmə
        n = len(found.get(k) or [])
        br_btns.append(
            panel.choice(
                f"{info['emoji']} {info['label']}" + (f" · {n}" if n else ""),
                k == cfg["browser"],
                f"menu:web:b:{k}",
            )
        )
    rows = [src_btns[:2], src_btns[2:]]
    rows.append([mode_btn("🌐 Brauzer", not manual, "web:bmode")])  # bir düymə — basanda dəyişir
    rows += panel.grid(br_btns, 3)
    if manual:
        rows.append([_btn(f"👤 Profil ({len(sel_found)})", "web:prof")])
    if b["family"] == "chromium":  # 🔐 keyring: bir düymə ilə 🤖 avto ↔ ✋ manual
        kr_manual = cfg["kr_mode"] == "manual"
        rows.append([mode_btn("🔐 Keyring", not kr_manual, "web:krmode")])
        if kr_manual:  # ✋ — bütün variantlar ayrıca düymə (✅ seçilmiş)
            kbtn = [
                panel.choice(
                    k or "yt-dlp özü",
                    (cfg["keyring"] or "") == k,
                    f"menu:web:krset:{k or '-'}",
                )
                for k in wp.KEYRINGS
            ]
            rows += [kbtn[i : i + 3] for i in range(0, len(kbtn), 3)]
    rows.append(
        [_btn(f"🕵️ UA: {wp.UA_MODES[cfg['ua']]}", "web:ua"), _btn("🧪 Cookie testi", "web:test")]
    )
    rows.append(nav_row("yt", refresh="web:scan"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def web_profiles_view(note: str = ""):
    """👤 Seçilmiş brauzerin profilləri."""
    from datetime import datetime

    from bot.core import webprofile as wp
    from common.utilities import get_tz

    cfg = wp.current()
    b = wp.BROWSERS[cfg["browser"]]
    profs = wp.detect().get(cfg["browser"]) or []
    lines = [f"👤 <b>{b['emoji']} {b['label']} — profillər</b>\n━━━━━━━━━━━━━━━━━━"]
    if not profs:
        lines.append(
            "<i>Serverdə bu brauzerin cookies faylı olan profili tapılmadı.</i>\n"
            "Brauzeri serverdə aç, YouTube-a daxil ol və ya 📄 cookies.txt istifadə et."
        )
    for i, p in enumerate(profs[:10], 1):
        when = (
            datetime.fromtimestamp(p["mtime"], get_tz()).strftime("%d.%m %H:%M")
            if p["mtime"]
            else "?"
        )
        cur = " ✅" if p["path"] == cfg["profile"] else ""
        lines.append(
            f"{i}. <b>{escape(p['name'])}</b>{cur}\n   <code>{escape(p['path'])}</code>\n"
            f"   <i>cookies yenilənib: {when}</i>"
        )
    lines.append("\n<i>Avto — ən son istifadə olunan profil götürülür.</i>")
    if note:
        lines.append(f"\n{note}")
    rows = [[panel.choice("🤖 Avto (ən son profil)", not cfg["profile"], "menu:web:p:auto")]]
    for i, p in enumerate(profs[:10]):
        rows.append(
            [
                panel.choice(
                    f"{i + 1}. {p['name']}"[:38],
                    p["path"] == cfg["profile"],
                    f"menu:web:p:{i}",
                )
            ]
        )
    rows.append(nav_row("web"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "_yta_browser_profiles",
    "_yta_label",
    "dl_format_view",
    "search_source_view",
    "web_profile_view",
    "web_profiles_view",
    "yt_button_label",
    "yt_hub_view",
    "yt_picker_view",
    "yta_acc_view",
    "yta_browser_view",
    "yta_help_view",
    "yta_list_view",
    "yta_login_view",
    "yta_own_device_view",
    "yta_server_login_view",
]
