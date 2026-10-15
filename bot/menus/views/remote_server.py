"""
bot.menus.views.remote_server
🌐 Uzaq server paneli: yükləmə və upload evdə, serverdə və ya hər ikisində.

Səhifələr (hər biri ayrı pəncərə, callback-lər menu:rmt[:...]):
  hub        menu:rmt              vəziyyət + əsas ayarlar (açar, yükləmə, upload, xəta rejimi)
  yükləmə    menu:rmt:dl           ev / server / ikisi
  upload     menu:rmt:up           ev / server / ikisi
  slotlar    menu:rmt:slots        serverdə eyni anda neçə iş
  bağlantı   menu:rmt:conn         ünvan, port, açar, test, quraşdırma
  hesablar   menu:rmt:acc          serverdə hansı YouTube hesabları işləyə bilər

Görünüşlər yalnız remote.snapshot()-ı formatlayır (məntiq bot/core/remote.py-dadır). Düymə qaydaları — bot/core/panel.py.
"""
from __future__ import annotations

import time
from html import escape

from aiogram.types import InlineKeyboardMarkup

from bot.core import panel, remote
from bot.menus.navigation import nav_row
from bot.menus.views.base import LINE, SECTIONS, crumb

L = remote.MODE_LABEL


def _head(*path: str) -> list[str]:
    return [crumb(SECTIONS["sys"], "⚙️ Performans", "🌐 Uzaq server", *path), LINE]


def _kb(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "—"


def _mb(n) -> str:
    return f"{n / 1024:.1f} GB" if isinstance(n, (int, float)) and n >= 1024 else f"{n} MB"


def _info_bits(info: dict) -> list[str]:
    """Son yoxlamadan serverin qısa xülasəsi: RAM · disk · yt-dlp · JS · ffmpeg."""
    bits = []
    if info.get("ram_mb"):
        bits.append(f"RAM {_mb(info['ram_mb'])}")
    if info.get("free_mb") is not None:
        bits.append(f"boş disk {_mb(info['free_mb'])}")
    if info.get("yt_dlp"):
        bits.append(f"yt-dlp {info['yt_dlp']}")
    if info:
        bits.append("JS " + (", ".join(info.get("js") or []) or "❌ yoxdur"))
        bits.append("ffmpeg " + ("✅" if info.get("ffmpeg") else "—"))
    return bits


# ───────────────────────── 🌐 hub ─────────────────────────
def remote_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = remote.snapshot()
    dl, up = s["dl"], s["up"]
    up_eff = up if dl != "home" else "home"                     # yükləmə evdədirsə fayl evdədir → upload da evdə
    lines = _head()
    lines.append(f"{s['icon']} <b>{escape(s['status'])}</b>")
    if s["host"]:
        bits = [f"<code>{escape(s['host'])}</code>"]
        info = s["info"]
        if info.get("ram_mb"):
            bits.append(f"RAM {_mb(info['ram_mb'])}")
        lines.append(" · ".join(bits))
    if not s["configured"]:
        lines.append("Başlamaq üçün 🔌 Bağlantı bölməsində server ünvanını daxil edin.")
    elif not s["enabled"]:
        lines.append("<i>Ayarlar saxlanıb, açanda işə düşəcək.</i>")
    elif dl == "home" and up_eff == "home":
        lines.append("<i>Yükləmə də, upload da evdədir, server istifadə olunmur.</i>")
    lines += ["",
              f"⬇️ Yükləmə: <b>{L[dl]}</b>",
              f"⬆️ Upload: <b>{L[up_eff]}</b>" + (" <i>yükləmə evdə olduğundan</i>" if dl == "home" and up != "home" else ""),
              "🛟 Xəta olsa: " + ("<b>🤖 iş evdə davam edir</b>" if s["auto"] else "<b>✋ iş gözləyir, sizdən soruşur</b>")]
    r = s["running"]
    if any(r.values()) or s["pending_files"]:
        lines.append(f"İndi: 📦 {r['depo']} · 👤 {r['user']} · ⬆️ {r['up']}"
                     + (f" · 📎 {s['pending_files']} fayl serverdə" if s["pending_files"] else ""))
    if s["fallback"] or s["ip_retry"]:
        lines.append(f"<i>Xətaya görə evdə görülən: {s['fallback']} · serverin IP-si rədd: {s['ip_retry']}</i>")
    if s["waiting"]:
        lines += ["", f"⏸ <b>{s['waiting']} iş server üçün gözləyir.</b> Nə edək?"]
    if note:
        lines += ["", note]
    rows = []
    if s["waiting"]:
        rows += [[panel.btn("🔁 Yenidən yoxla", "menu:rmt:fix:retry")],
                 [panel.btn("🏠 Evə keç", "menu:rmt:fix:home"), panel.btn("🤖 Avto", "menu:rmt:fix:auto")]]
    rows += [
        [panel.toggle("🌐 Uzaq server", s["enabled"], "menu:rmt:on")],
        [panel.btn(f"⬇️ Yükləmə: {L[dl]}", "menu:rmt:dl"), panel.btn(f"⬆️ Upload: {L[up]}", "menu:rmt:up")],
        [panel.mode("🛟 Xəta olsa", s["auto"], "menu:rmt:auto"), panel.btn("👥 Slotlar", "menu:rmt:slots")],
        [panel.btn("🍪 Hesablar", "menu:rmt:acc"), panel.btn("🔌 Bağlantı", "menu:rmt:conn")],
        nav_row("perf", refresh="rmt", to="Performans"),
    ]
    return "\n".join(lines)[:4000], _kb(rows)


# ───────────────────────── ⬇️ ⬆️ ev / server / ikisi ─────────────────────────
_MODE_PAGES = {
    "download": {
        "title": "⬇️ Yükləmə", "cb": "dl",
        "ask": "Mahnı harada yüklənsin: YouTube-dan fayla?",
        "opts": {"home": "hamısı ev internetində", "server": "hamısı serverin internetində",
                 "both": "server boşdursa orada, dolu olanda evdə"},
        "foot": "Server üçün açılmamış hesablar və localhost proxy ilə yükləmələr həmişə evdə gedir. "
                "Hesabları 🍪 Hesablar bölməsində açın.",
    },
    "upload": {
        "title": "⬆️ Upload", "cb": "up",
        "ask": "Fayl depoya, yəni Telegram-a harada göndərilsin?",
        "opts": {"home": "fayl serverdən evə çəkilir, evdən göndərilir",
                 "server": "fayl serverdən birbaşa Telegram-a gedir, ev interneti işləmir",
                 "both": "server boşdursa serverdən, dolu olanda evdən"},
        "foot": "Yalnız 📦 depo doldurucusu üçündür, istifadəçi mahnısı həmişə evdən göndərilir. "
                "Yükləmə evdədirsə fayl zatən evdədir və upload da evdə olur.",
    },
}


def remote_mode_view(kind: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    page = _MODE_PAGES[kind]
    cur = remote.mode(kind)
    lines = _head(page["title"])
    lines += [page["ask"], ""]
    lines += [f"{L[m]} — {page['opts'][m]}" for m in remote.MODES]
    lines += ["", f"<i>{page['foot']}</i>"]
    if kind == "upload" and remote.mode("download") == "home" and cur != "home":
        lines.append("⚠️ <i>Yükləmə hazırda evdədir, ona görə upload da evdə olur.</i>")
    if note:
        lines += ["", note]
    c = page["cb"]
    rows = [[panel.choice(L["home"], cur == "home", f"menu:rmt:{c}:home"),
             panel.choice(L["server"], cur == "server", f"menu:rmt:{c}:server")],
            [panel.choice(L["both"], cur == "both", f"menu:rmt:{c}:both")],
            nav_row("rmt", refresh=f"rmt:{c}", to="Server")]
    return "\n".join(lines)[:4000], _kb(rows)


# ───────────────────────── 👥 slotlar ─────────────────────────
def remote_slots_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = remote.snapshot()
    adv = remote.slot_advice()
    lines = _head("👥 Slotlar")
    lines += ["Serverdə eyni anda neçə iş getsin.", "",
              f"📦 Depo: <b>{s['depo_slots']}</b> — yükləmə və eyni sayda upload",
              f"👤 İstifadəçi: <b>{s['user_slots']}</b> — ayrıdır, depo bu slotları tutmur", "",
              f"Ən çoxu eyni anda: {s['depo_slots']} + {s['depo_slots']} + {s['user_slots']} = <b>{adv['total']}</b> proses, "
              f"təxminən {_mb(adv['need_mb'])}."]
    if adv["ram_mb"]:
        lines.append(f"Serverin RAM-ı: {_mb(adv['ram_mb'])}.")
        lines.append(("⚠️ <b>Çoxdur</b> — server yaddaşı çatmaya və ya swap-a düşə bilər. "
                      f"Tövsiyə: 📦 depo {adv['rec_depo']}.") if adv["warn"] else "✅ Serverin yaddaşına uyğundur.")
    else:
        lines.append("<i>Serverin RAM-ı bilinmir — 🔌 Bağlantı › 🧪 Test edin, tövsiyə çıxsın.</i>")
    if note:
        lines += ["", note]
    rows = [
        [panel.btn("➖", "menu:rmt:slots:d-"), panel.btn(f"📦 Depo: {s['depo_slots']}", "menu:rmt:slots:di"),
         panel.btn("➕", "menu:rmt:slots:d+")],
        [panel.btn("➖", "menu:rmt:slots:u-"), panel.btn(f"👤 İstifadəçi: {s['user_slots']}", "menu:rmt:slots:ui"),
         panel.btn("➕", "menu:rmt:slots:u+")],
    ]
    if adv["rec_depo"] and adv["warn"]:
        rows.append([panel.btn(f"💡 Tövsiyəni tətbiq et: 📦 {adv['rec_depo']}", "menu:rmt:slots:rec")])
    rows.append(nav_row("rmt", refresh="rmt:slots", to="Server"))
    return "\n".join(lines)[:4000], _kb(rows)


# ───────────────────────── 🔌 bağlantı ─────────────────────────
def remote_conn_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = remote.snapshot()
    lines = _head("🔌 Bağlantı")
    lines += [f"Ünvan: <code>{escape(s['host'])}</code>" if s["host"] else "Ünvan: <i>daxil edilməyib</i>",
              f"Port: <code>{s['port']}</code>",
              f"Açar: <code>{escape(s['key'])}</code>" if s["key"] else "Açar: <i>standart</i>",
              f"Qovluq serverdə: <code>{escape(s['dir'])}</code>"]
    chk = s["check"]
    if chk["at"]:
        lines += ["", f"Son yoxlama {_clock(chk['at'])}: " + ("✅ <b>uğurlu</b>" if chk["ok"] else f"❌ {escape(chk['error'][:120])}")]
        bits = _info_bits(s["info"])
        if bits:
            lines.append(" · ".join(bits))
        if s["info"] and not s["info"].get("js"):
            lines.append("⚠️ <i>YouTube üçün serverdə deno və ya node lazımdır: sudo apt install nodejs</i>")
    if not (chk["at"] and chk["ok"]):
        lines += ["", "<b>Qurmaq üçün</b>",
                  "1. ✏️ Ünvanı yazın: <code>istifadəçi@ip</code>",
                  "2. 🔑 SSH açarının yolunu yazın: parolsuz giriş üçün <code>ssh-copy-id</code> edin",
                  "3. 🛠 Qur — serverdə yt-dlp qurulur, sonra 🧪 Test"]
    if note:
        lines += ["", note]
    rows = [
        [panel.btn("✏️ Ünvan", "menu:rmt:edit:host"), panel.btn("🔌 Port", "menu:rmt:edit:port")],
        [panel.btn("🔑 Açar", "menu:rmt:edit:key"), panel.btn("🧪 Test", "menu:rmt:test")],
        [panel.btn("🛠 Qur və ya yenilə", "menu:rmt:setup")],
        nav_row("rmt", refresh="rmt:conn", to="Server"),
    ]
    return "\n".join(lines)[:4000], _kb(rows)


_PROMPTS = {
    "host": ("✏️ Ünvan", "<code>istifadəçi@ip</code> yazın, məsələn <code>root@203.0.113.5</code>.\n"
             "Silmək üçün <b>-</b> yazın.\n<i>Evdən bu serverə parolsuz qoşula bilməlisiniz: "
             "<code>ssh-copy-id istifadəçi@ip</code></i>"),
    "port": ("🔌 Port", "Rəqəm yazın, standart <code>22</code>."),
    "key": ("🔑 Açar", "Gizli açarın yolunu yazın, məsələn <code>~/.ssh/dlm_vps</code> "
            "(<code>.pub</code> yox).\nStandart açarlar üçün <b>-</b> yazın."),
}


def remote_input_view(what: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    title, hint = _PROMPTS[what]
    lines = _head("🔌 Bağlantı", title) + [hint]
    if note:
        lines += ["", note]
    return "\n".join(lines), _kb([[panel.cancel("menu:rmt:conn")]])


# ───────────────────────── 🍪 hesablar ─────────────────────────
def remote_accounts_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts
    accs = yt_accounts.accounts()
    on_n = sum(1 for a in accs if remote.acc_allowed(a["id"]))
    lines = _head("🍪 Hesablar")
    lines += ["Cookies ilə yükləmə serverdə yalnız burada açılmış hesablarla gedir. "
              "Hesabın <b>yalnız YouTube və Google</b> cookies-i süzülüb serverə göndərilir, 30 dəqiqədən bir yenilənir.",
              "Bağlı hesabla yükləmə evdə gedir, cookies-siz cəhdlər isə həmişə serverdə.", "",
              "⚠️ <i>Eyni hesab həm ev IP-sindən, həm server IP-sindən işləyəndə YouTube sessiyanı şübhəli sayıb "
              "bloklaya bilər. Əsas hesabı açmayın, əlavə hesabları seçin.</i>",
              "", f"Serverdə açıq: <b>{on_n}</b> / {len(accs)}"]
    if note:
        lines += ["", note]
    rows = []
    names = [a["name"] for a in accs]
    for a in accs:
        star = "⭐ " if a["kind"] == "main" else ""
        kind = "fayl" if a["kind"] == "file" else "brauzer"
        tag = f" #{a['id'][:4]}" if names.count(a["name"]) > 1 else ""         # eyni adlı hesablar ayırd olunsun
        rows.append([panel.toggle(f"{star}{a['name'][:20]}{tag} · {kind}", remote.acc_allowed(a["id"]),
                                  f"menu:rmt:acc:{a['id']}", compact=True)])
    rows.append(nav_row("rmt", refresh="rmt:acc", to="Server"))
    return "\n".join(lines)[:4000], _kb(rows)
