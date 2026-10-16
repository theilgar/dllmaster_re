"""
bot.menus.views.remote_server
🌐 Uzaq serverlər paneli: yükləmə və upload evdə, serverlərdə və ya hər ikisində.

Səhifələr (hər biri ayrı pəncərə, callback-lər menu:rmt[:...]):
  hub        menu:rmt                  ümumi vəziyyət + ümumi ayarlar (açar, yükləmə, upload, xəta rejimi)
  yükləmə    menu:rmt:dl               ev / server / ikisi
  upload     menu:rmt:up               ev / server / ikisi
  serverlər  menu:rmt:srvs             siyahı + ➕ əlavə et
  server     menu:rmt:s:<id>           bir serverin ünvanı, açarı, Firefox girişi, test, quraşdırma, silmə
  slotlar    menu:rmt:s:<id>:slots     bu serverdə eyni anda neçə iş
  hesablar   menu:rmt:acc              evdəki hansı YouTube hesabları serverlərə göndərilə bilər

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
        bits.append("Deno " + ("✅" if info.get("deno") else "❌"))
        bits.append("PO token " + ("✅" if info.get("pot") else "❌"))
        bits.append("ffmpeg " + ("✅" if info.get("ffmpeg") else "—"))
    return bits


# ───────────────────────── 🌐 hub ─────────────────────────
def remote_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = remote.snapshot()
    dl, up = s["dl"], s["up"]
    up_eff = up if dl != "home" else "home"                     # yükləmə evdədirsə fayl evdədir → upload da evdə
    lines = _head()
    lines.append(f"{s['icon']} <b>{escape(s['status'])}</b>")
    for v in s["servers"]:
        lines.append(f"{v['icon']} <b>{escape(v['name'])}</b>"
                     + (f" · <code>{escape(v['host'])}</code>" if v["host"] else " · <i>ünvan yoxdur</i>"))
    if not s["servers"]:
        lines.append("Başlamaq üçün 🖥 Serverlər bölməsində server əlavə edin.")
    elif not s["configured"]:
        lines.append("Serverin ünvanı daxil edilməyib: 🖥 Serverlər.")
    elif not s["enabled"]:
        lines.append("<i>Ayarlar saxlanıb, açanda işə düşəcək.</i>")
    elif dl == "home" and up_eff == "home":
        lines.append("<i>Yükləmə də, upload da evdədir, serverlər istifadə olunmur.</i>")
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
    for v in s["servers"]:
        if v["ip_blocked"]:
            lines += ["", f"🟠 <b>«{escape(v['name'])}» IP-si YouTube tərəfindən rədd edilir</b>: son {v['ip_streak']} yükləmə "
                          "ardıcıl alınmadı. Cookies kömək etmir, datacenter IP-si bloklanıb."]
    if s["waiting"]:
        lines += ["", f"⏸ <b>{s['waiting']} iş server üçün gözləyir.</b> Nə edək?"]
    if note:
        lines += ["", note]
    rows = []
    if s["waiting"]:
        rows.append([panel.btn("🔁 Yenidən yoxla", "menu:rmt:fix:retry")])
    if s["waiting"] or s["ip_blocked"]:
        rows.append([panel.btn("🏠 Evə keç", "menu:rmt:fix:home"), panel.btn("🤖 Avto", "menu:rmt:fix:auto")])
    rows += [
        [panel.toggle("🌐 Uzaq server", s["enabled"], "menu:rmt:on")],
        [panel.btn(f"⬇️ Yükləmə: {L[dl]}", "menu:rmt:dl"), panel.btn(f"⬆️ Upload: {L[up]}", "menu:rmt:up")],
        [panel.mode("🛟 Xəta olsa", s["auto"], "menu:rmt:auto"), panel.btn(f"🖥 Serverlər ({len(s['servers'])})", "menu:rmt:srvs")],
        [panel.btn("🍪 Hesablar", "menu:rmt:acc")],
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


# ───────────────────────── 🖥 serverlər ─────────────────────────
def remote_servers_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    s = remote.snapshot()
    lines = _head("🖥 Serverlər")
    if s["servers"]:
        lines.append("İş ən az yüklü sağlam serverə gedir; xəta və ya IP rədd olsa başqasına keçir.")
        lines.append("")
        for v in s["servers"]:
            ld = v["load"]
            lines.append(f"{v['icon']} <b>{escape(v['name'])}</b> · {escape(v['status'])}"
                         f"\n   📦 {v['depo_slots']} · 👤 {v['user_slots']}" + (f" · işdə {ld['depo'] + ld['user'] + ld['up']}"
                                                                         if any(ld.values()) else ""))
    else:
        lines.append("Hələ server yoxdur. ➕ düyməsi ilə əlavə edin.")
    if note:
        lines += ["", note]
    rows = []
    for v in s["servers"]:
        rows.append([panel.btn(f"{v['icon']} {v['name'][:24]}", f"menu:rmt:s:{v['id']}")])
    if len(s["servers"]) < remote.MAX_SERVERS:
        rows.append([panel.btn("➕ Server əlavə et", "menu:rmt:add")])
    rows.append(nav_row("rmt", refresh="rmt:srvs", to="Server"))
    return "\n".join(lines)[:4000], _kb(rows)


def _srv(sid: str):
    return remote.get_server(sid)


def remote_server_view(sid: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    srv = _srv(sid)
    if srv is None:
        return remote_servers_view("⚠️ Server tapılmadı")
    v = remote.server_view(srv)
    lines = _head("🖥 Serverlər", v["name"])
    lines.append(f"{v['icon']} <b>{escape(v['status'])}</b>")
    lines += [f"Ünvan: <code>{escape(v['host'])}</code>" if v["host"] else "Ünvan: <i>daxil edilməyib</i>",
              f"Port: <code>{v['port']}</code>",
              f"Açar: <code>{escape(v['key'])}</code>" if v["key"] else "Açar: <i>standart</i>",
              f"Qovluq serverdə: <code>{escape(v['dir'])}</code>",
              f"Slotlar: 📦 {v['depo_slots']} · 👤 {v['user_slots']}",
              "YouTube girişi: " + ("🦊 serverin öz Firefox-u" if v["firefox"] else "🏠 evdəki hesablar (🍪 Hesablar)")]
    if v["firefox"]:
        login = v["info"].get("firefox_login")
        lines.append("Firefox-da giriş: " + ("✅ var" if login else "❌ tapılmadı" if v["check"]["at"] else "<i>hələ yoxlanmayıb</i>"))
    chk = v["check"]
    if chk["at"]:
        lines += ["", f"Son yoxlama {_clock(chk['at'])}: " + ("✅ <b>uğurlu</b>" if chk["ok"] else f"❌ {escape(chk['error'][:120])}")]
        bits = _info_bits(v["info"])
        if bits:
            lines.append(("Əvvəlki uğurlu yoxlamadan: " if not chk["ok"] else "") + " · ".join(bits))
        if v["info"] and not v["info"].get("deno"):
            lines.append("⚠️ <i>YouTube üçün serverdə Deno lazımdır (Node 20 dəstəklənmir): 🛠 Qur və ya yenilə</i>")
        elif v["info"] and not v["info"].get("pot"):
            lines.append("⚠️ <i>PO token provayderi işləmir: 🛠 Qur və ya yenilə</i>")
    if not v["info"]:
        lines += ["", "<b>Qurmaq üçün</b>",
                  "1. ✏️ Ünvanı yazın: <code>istifadəçi@ip</code>",
                  "2. 🔑 SSH açarının yolunu yazın: parolsuz giriş üçün <code>ssh-copy-id</code> edin",
                  "3. 🛠 Qur — serverdə yt-dlp qurulur, sonra 🧪 Test"]
    if note:
        lines += ["", note]
    b = f"menu:rmt:s:{sid}"
    rows = [
        [panel.toggle("Server", v["on"], f"{b}:on"), panel.btn("👥 Slotlar", f"{b}:slots")],
        [panel.btn("✏️ Ünvan", f"{b}:edit:host"), panel.btn("🔌 Port", f"{b}:edit:port")],
        [panel.btn("🔑 Açar", f"{b}:edit:key"), panel.btn("🏷 Ad", f"{b}:edit:name")],
        [panel.toggle("🦊 Firefox girişi", v["firefox"], f"{b}:ff"), panel.btn("🧪 Test", f"{b}:test")],
        [panel.btn("🛠 Qur və ya yenilə", f"{b}:setup"), panel.btn("🗑 Sil", f"{b}:del")],
        nav_row("rmt:srvs", refresh=f"rmt:s:{sid}", to="Serverlər"),
    ]
    return "\n".join(lines)[:4000], _kb(rows)


def remote_delete_view(sid: str) -> tuple[str, InlineKeyboardMarkup]:
    srv = _srv(sid)
    name = escape(srv.name) if srv else "?"
    lines = _head("🖥 Serverlər", "🗑 Sil")
    lines += [f"<b>{name}</b> siyahıdan silinsin?", "Serverin özündəki fayllara toxunulmur; yalnız bot onu unudur."]
    return "\n".join(lines), _kb([[panel.btn("🗑 Bəli, sil", f"menu:rmt:s:{sid}:delok"), panel.cancel(f"menu:rmt:s:{sid}")]])


# ───────────────────────── 👥 slotlar ─────────────────────────
def remote_slots_view(sid: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    srv = _srv(sid)
    if srv is None:
        return remote_servers_view("⚠️ Server tapılmadı")
    v = remote.server_view(srv)
    adv = v["advice"]
    lines = _head("🖥 Serverlər", v["name"], "👥 Slotlar")
    lines += ["Bu serverdə eyni anda neçə iş getsin.", "",
              f"📦 Depo: <b>{v['depo_slots']}</b> — yükləmə və eyni sayda upload",
              f"👤 İstifadəçi: <b>{v['user_slots']}</b> — ayrıdır, depo bu slotları tutmur", "",
              f"Ən çoxu eyni anda: {v['depo_slots']} + {v['depo_slots']} + {v['user_slots']} = <b>{adv['total']}</b> proses, "
              f"təxminən {_mb(adv['need_mb'])}."]
    if adv["ram_mb"]:
        lines.append(f"Serverin RAM-ı: {_mb(adv['ram_mb'])}.")
        lines.append(("⚠️ <b>Çoxdur</b> — server yaddaşı çatmaya və ya swap-a düşə bilər. "
                      f"Tövsiyə: 📦 depo {adv['rec_depo']}.") if adv["warn"] else "✅ Serverin yaddaşına uyğundur.")
    else:
        lines.append("<i>Serverin RAM-ı bilinmir — server səhifəsində 🧪 Test edin, tövsiyə çıxsın.</i>")
    if note:
        lines += ["", note]
    b = f"menu:rmt:s:{sid}:slots"
    rows = [
        [panel.btn("➖", f"{b}:d-"), panel.btn(f"📦 Depo: {v['depo_slots']}", f"{b}:di"), panel.btn("➕", f"{b}:d+")],
        [panel.btn("➖", f"{b}:u-"), panel.btn(f"👤 İstifadəçi: {v['user_slots']}", f"{b}:ui"), panel.btn("➕", f"{b}:u+")],
    ]
    if adv["rec_depo"] and adv["warn"]:
        rows.append([panel.btn(f"💡 Tövsiyəni tətbiq et: 📦 {adv['rec_depo']}", f"{b}:rec")])
    rows.append(nav_row(f"rmt:s:{sid}", refresh=f"rmt:s:{sid}:slots", to="Server"))
    return "\n".join(lines)[:4000], _kb(rows)


_PROMPTS = {
    "host": ("✏️ Ünvan", "<code>istifadəçi@ip</code> yazın, məsələn <code>root@203.0.113.5</code>.\n"
             "Silmək üçün <b>-</b> yazın.\n<i>Evdən bu serverə parolsuz qoşula bilməlisiniz: "
             "<code>ssh-copy-id istifadəçi@ip</code></i>"),
    "port": ("🔌 Port", "Rəqəm yazın, standart <code>22</code>."),
    "key": ("🔑 Açar", "Gizli açarın yolunu yazın, məsələn <code>~/.ssh/dlm_vps</code> "
            "(<code>.pub</code> yox).\nStandart açarlar üçün <b>-</b> yazın."),
    "name": ("🏷 Ad", "Serverin adını yazın (ən çox 24 simvol), məsələn <code>Frankfurt</code>."),
}


def remote_input_view(sid: str, what: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    title, hint = _PROMPTS[what]
    srv = _srv(sid)
    lines = _head("🖥 Serverlər", srv.name if srv else "?", title) + [hint]
    if note:
        lines += ["", note]
    return "\n".join(lines), _kb([[panel.cancel(f"menu:rmt:s:{sid}")]])


# ───────────────────────── 🍪 hesablar ─────────────────────────
def remote_accounts_view(note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    from bot.core import yt_accounts
    accs = yt_accounts.accounts()
    on_n = sum(1 for a in accs if remote.acc_allowed(a["id"]))
    ff = [x for x in remote.servers() if x.firefox]
    lines = _head("🍪 Hesablar")
    lines += ["Server YouTube-a cookies ilə girir. İki yol var:", "",
              "🦊 <b>Serverin öz Firefox-u</b> (tövsiyə). Hər serverdə Firefox-da YouTube-a <b>normal pəncərədə</b> daxil olun "
              "(Private yox), Firefox-u bağlayın, sonra server səhifəsində 🦊 açın. Cookies serverdə oxunur, evdən heç nə "
              "göndərilmir. Datacenter IP-sində (AWS və s.) evdən köçürülən sessiya işləmir, serverin öz IP-sində açılan giriş işləyir.",
              f"Firefox açıq olan serverlər: <b>{len(ff)}</b> / {len(remote.servers())}",
              "",
              "🏠 <b>Evdəki hesablar</b> (aşağıda). Hesabın <b>yalnız YouTube və Google</b> cookies-i süzülüb Firefox-u açıq "
              "olmayan serverlərə göndərilir, 30 dəqiqədən bir yenilənir.",
              "", "⚠️ <i>Eyni hesab həm ev IP-sindən, həm server IP-sindən işləyəndə YouTube sessiyanı şübhəli sayıb "
                  "bloklaya bilər. Əsas hesabı açmayın.</i>",
              f"\nEvdən göndərilən hesab: <b>{on_n}</b> / {len(accs)}"]
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
