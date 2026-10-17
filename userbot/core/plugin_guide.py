"""
📖 Userbot bələdçisi — .menu → 📖 Bələdçi və .help.

Bütün komandalar canlı reyestrdən (ub.commands + ub.help) oxunur: plugin əlavə / söndürüləndə bələdçi özü yenilənir,
əl ilə sənəd saxlamaq lazım deyil. Hər komandaya help=(qısa, ətraflı) yazmaq məcburidir (tests/test_userbot_plugin_menu.py yoxlayır).

Qruplar: 🏠 Əsas (host komandaları) · 🧩 hər plugin ayrıca · 🤖 Bot komandaları · 🛠 Panel bölmələri.
"""
import re
from html import escape

from bot.core import panel as ui

HOST, BOT, PANEL, ALL, RUN = "_host", "_bot", "_panel", "_all", "_run"
PER_PAGE = 10
GROUPS_PER_PAGE = 8
LIMIT = 3400                       # Telegram mətn limiti 4096 — nişanlar (HTML) üçün ehtiyat

# Düymə ilə işlədilə bilən komandalar: arqumentsiz yazılanda yalnız məlumat göstərir və ya zərərsiz iş görür.
# (ban, purge, sh, restore, clearfilters kimi təsir edənlər burada YOXDUR — onlar yalnız əl ilə yazılır.)
RUNNABLE = ("alive", "status", "fastfetch", "speedtest", "perf", "storage", "plugins", "filters", "backups",
            "amsgs", "mutelist", "blocked", "id", "locks")

INTRO = (
    "<b>Necə işləyir</b>\n"
    "• Komanda <code>.</code> ilə başlayır və <b>yalnız sizin hesabdan</b> istənilən çatda yazılır.\n"
    "• Bəzi komandalar <b>reply</b> tələb edir (<code>.ban</code>, <code>.mute</code>, <code>.info</code> — cavab "
    "verdiyiniz şəxsə tətbiq olunur).\n"
    "• Nəticə inline kart (düymələrlə) və ya sadə mətn kimi gəlir; inline qadağan olan çatda mətn göstərilir.\n"
    "• <code>.menu</code> — bu menyu, <code>.help</code> — bələdçi, <code>.bmenu</code> — botun menyusu.\n"
    "• Hər komanda bir plugin faylındadır: 🧩 Pluginlər bölməsində plugini və ya ayrı komandanı söndürə bilərsiniz."
)


# ───────────── reyestrdən qruplar ─────────────
def aliases(pattern: str, name: str) -> list:
    """'^\\.(?:storage|disk|size)(?:\\s+(.+))?$' → ['storage', 'disk', 'size']; tapılmasa — [name]."""
    m = re.match(r"\^\\\.\(\?:([A-Za-z0-9_|]+)\)", pattern or "")
    names = [a for a in (m.group(1).split("|") if m else []) if a]
    return names or [name]


def _entry(ub, name, owner, pattern=""):
    button, short, long, cat = ub.help.get(name) or (f".{name}", "təsvir yoxdur", "", "acc")
    return {"name": name, "button": button, "short": short, "long": long, "cat": cat, "owner": owner,
            "aliases": aliases(pattern, name) if pattern else [name]}


def group_label(gid: str) -> str:
    return {HOST: "🏠 Əsas", BOT: "🤖 Bot komandaları", PANEL: "🛠 Panel bölmələri", ALL: "🔤 Əlifba sırası",
            RUN: "▶️ Düymə ilə işlədilənlər"}.get(gid, f"🧩 {gid}")


def short_label(gid: str) -> str:
    """Emojisiz ad — '⬅️ <ad>' düyməsi üçün."""
    return group_label(gid).split(" ", 1)[-1]


def build(ub) -> dict:
    """{gid: [entry, ...]} — sıra: Əsas, pluginlər (əlifba), Bot, Panel. Boş qrup yoxdur."""
    groups, seen = {}, set()
    for c in ub.commands:
        owner = c.get("owner") or HOST
        groups.setdefault(owner, []).append(_entry(ub, c["name"], owner, c.get("pattern", "")))
        seen.add(c["name"])
    for key, (button, short, long, cat) in sorted(ub.help.items()):
        if key in seen or cat not in ("bot", "panel"):
            continue
        gid = BOT if cat == "bot" else PANEL
        groups.setdefault(gid, []).append(_entry(ub, key, gid))
    for items in groups.values():
        items.sort(key=lambda e: e["name"])
    order = [HOST] + sorted(g for g in groups if g not in (HOST, BOT, PANEL)) + [BOT, PANEL]
    return {g: groups[g] for g in order if g in groups}


def find(groups: dict, name: str):
    for gid, items in groups.items():
        for e in items:
            if e["name"] == name:
                return gid, e
    return None, None


def runnable(ub, name: str) -> bool:
    c = next((c for c in ub.commands if c["name"] == name), None)
    return bool(c) and name in RUNNABLE and bool(re.match(c["pattern"], f".{name}"))


def _fit(text: str, limit: int = LIMIT) -> str:
    """Uzun help mətnini abzas sərhəddində kəsir (HTML nişanları yarımçıq qalmasın)."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n\n", 1)[0]
    return cut + "\n\n<i>… (davamı üçün komandanı yazıb baxın)</i>"


# ───────────── görünüşlər ─────────────
def _cb(tok: str, act: str) -> str:
    return f"ubp:{tok}:{act}"[:64]


def _nav(tok, back="", to="", refresh=""):
    return ui.nav(_cb(tok, back) if back else "", to, close_data=_cb(tok, "close"), home_data=_cb(tok, "main"),
                  refresh_data=_cb(tok, refresh) if refresh else "")


def home_view(groups: dict, tok: str, page: int = 0, off_plugins=()):
    total = sum(len(v) for g, v in groups.items() if g not in (BOT, PANEL))
    lines = ["<i>🏠 › 📖 Bələdçi</i>", "📖 <b>Userbot bələdçisi</b>", "━━━━━━━━━━━━━━━━━━",
             f"Yüklü komanda: <b>{total}</b> · qrup: <b>{len(groups)}</b>"]
    if page == 0:
        lines += ["", INTRO]
    if off_plugins:
        lines.append(f"\n🔴 Söndürülüb (komandaları bələdçidə yoxdur): {', '.join(escape(p) for p in off_plugins[:8])}")
    lines.append("\n<i>Qrupu seç — komandaların siyahısı və izahı.</i>")
    items, page, pages = ui.paginate(list(groups.items()), page, GROUPS_PER_PAGE)
    rows = ui.grid([ui.btn(f"{group_label(g)} {len(v)}", _cb(tok, f"gp:{g}:0")) for g, v in items], 2)
    rows.append(ui.pager(f"ubp:{tok}:g:", page, pages, _cb(tok, "nop")))
    rows.append([ui.btn(group_label(ALL), _cb(tok, f"gp:{ALL}:0"))])
    rows.append(_nav(tok, "main", "Menyu", refresh=f"g:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def group_view(groups: dict, tok: str, gid: str, page: int = 0, only=()):
    if gid == ALL:
        items = sorted((e for v in groups.values() for e in v), key=lambda e: e["name"])
    elif gid == RUN:                                   # ▶️ Əmrlər səhifəsindəki komandaların izahı
        items = [e for v in groups.values() for e in v if e["name"] in only]
    else:
        items = groups.get(gid)
    if not items:
        return home_view(groups, tok)
    page_items, page, pages = ui.paginate(items, page, PER_PAGE)
    lines = [f"<i>🏠 › 📖 Bələdçi › {escape(group_label(gid))}</i>", f"<b>{escape(group_label(gid))}</b> · {len(items)}",
             "━━━━━━━━━━━━━━━━━━"]
    lines += [f"• <code>{escape(e['button'])}</code> — {escape(e['short'])}" for e in page_items]
    lines.append("\n<i>Komandanı seç — nə üçündür, necə işlədilir.</i>")
    rows = ui.grid([ui.btn(e["button"][:24], _cb(tok, f"gc:{e['name']}")) for e in page_items], 2)
    rows.append(ui.pager(f"ubp:{tok}:gp:{gid}:", page, pages, _cb(tok, "nop")))
    rows.append(_nav(tok, "qs" if gid == RUN else "g:0", "Sürətli" if gid == RUN else "Bələdçi", refresh=f"gp:{gid}:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def command_view(groups: dict, tok: str, name: str, *, ran_ok: bool = False, off: bool = False, protected: bool = False,
                 note: str = "", extra=(), back=None, quiet=None, ctx: str = "g", options: bool = False):
    """extra — əlavə düymə sətirləri (Əmrlər bölməsi); back=(act, ad) — ⬅️ düyməsinin hədəfi (defolt: komandanın qrupu);
    quiet — komandanın cavabı gizlədilibmi (None = düymə yoxdur), ctx — 'g' bələdçi / 'q' Əmrlər (geri qayıdış səhifəsi)."""
    gid, e = find(groups, name)
    if e is None:
        return home_view(groups, tok)
    lines = [f"<i>🏠 › 📖 Bələdçi › {escape(group_label(gid))} › {escape(e['button'])}</i>",
             f"📖 <b>{escape(e['button'])}</b> — {escape(e['short'])}", "━━━━━━━━━━━━━━━━━━"]
    if len(e["aliases"]) > 1:
        lines.append("Eyni komanda: " + " ".join(f"<code>.{escape(a)}</code>" for a in e["aliases"]))
    lines.append(f"Qrup: {escape(group_label(gid))}" + (f" · vəziyyət: <b>{ui.state(not off)}</b>" if gid not in (BOT, PANEL) else ""))
    if e["long"]:
        lines += ["", _fit(e["long"])]
    if ran_ok:
        lines += ["", "▶️ <i>Düymə ilə işləyir: funksiya birbaşa çağırılır, nəticə bu mesajda çıxır (çata mesaj yazılmır). "
                      "Arqumentli variantları (məsələn <code>.storage clean</code>) əl ilə yazın.</i>"]
    mid = []
    if quiet is not None and gid not in (HOST, BOT, PANEL):
        lines += ["", f"💬 <b>Cavab mesajı:</b> {'🔴 gizlədilib' if quiet else '🟢 göstərilir'}",
                  "<i>Gizlədiləndə komanda yazılanda çatda status mətni (məs. «mesajlar toplanır…») və kart çıxmır, "
                  "komanda mesajı iş bitəndə silinir. Menyudan işlədəndə nəticə həmişə menyuda çıxır.</i>"]
        mid.append(ui.toggle("💬 Cavab", not quiet, _cb(tok, f"cq:{ctx}:{name}"), compact=True))
    if options and gid not in (HOST, BOT, PANEL):
        mid.append(ui.btn("⚙️ Ayarlar", _cb(tok, f"so:{ctx}:{name}")))
        lines += ["", "⚙️ <i>Bu komandanın ayarları var — silmə sırası və s.</i>"]
    if note:
        lines += ["", note]
    top = []
    if ran_ok:
        top.append(ui.btn("▶️ İşlət", _cb(tok, f"run:{name}")))
    if gid not in (HOST, BOT, PANEL) and not protected:
        top.append(ui.toggle("Komanda", not off, _cb(tok, f"cg:{name}"), compact=True))
    back_act, back_to = back or (f"gp:{gid}:0", short_label(gid))
    return "\n".join(lines), ui.kb(top, mid, *extra, _nav(tok, back_act, back_to))


def text_view(groups: dict) -> str:
    """Inline düymə qadağan olan çat üçün mətn bələdçisi."""
    out = ["📖 Userbot bələdçisi", ""]
    for gid, items in groups.items():
        out.append(f"{group_label(gid)}:")
        out += [f"  {e['button']} — {e['short']}" for e in items]
        out.append("")
    return "\n".join(out).strip()
