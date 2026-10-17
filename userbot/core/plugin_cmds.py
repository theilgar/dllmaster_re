"""
▶️ Əmrlər — BÜTÜN userbot komandaları düymə ilə (icranı userbot/core/plugin_menu.py edir).

Hər komandanın rejimi pattern-dən və siyahılardan çıxarılır:
  ▶️ safe   — məlumat göstərir, təsdiqsiz birbaşa işləyir (guide.RUNNABLE);
  ✍️ input  — arqument alır: düyməyə bas, arqumenti çata yaz (reply tələb edənlər üçün hədəf mesaja reply et);
  ⚙️ bare   — arqumentsiz də işləyir, amma təsir edir → təsdiq;
  📖 none   — menyudan işləmir (.menu özü və s.), yalnız izah.
Təsir edən komandalar (LIGHT-da olmayan hər şey) işləmədən əvvəl təsdiq ekranı göstərir — işləyəcək dəqiq mətnlə.
"""
import re
from html import escape

from bot.core import panel as ui
from userbot.core import plugin_guide as guide
from userbot.core.plugin_guide import _cb, _nav

SAFE = guide.RUNNABLE
NO_RUN = {"menu", "help", "bmenu"}                       # menyunun özü — yeni menyu mesajı açardılar
LIGHT = {"info", "members", "fstat", "fedinfo", "pwd", "cd", "env", "audit_report"}   # arqumentlə oxuyur, dəyişmir
ICON = {"safe": "▶️", "input": "✍️", "bare": "⚙️", "none": "📖"}
LEGEND = "▶️ birbaşa · ✍️ arqument yazılır · ⚙️ arqumentsiz (təsdiqlə) · 📖 yalnız izah"
PER_PAGE = 10
GROUPS_PER_PAGE = 8


def info(ub, name: str) -> dict:
    """{name, mode, bare_ok, takes_args, confirm}. Komanda yüklü deyilsə mode='none'."""
    c = next((c for c in ub.commands if c["name"] == name), None)
    if c is None or not c.get("pattern"):
        return {"name": name, "mode": "none", "bare_ok": False, "takes_args": False, "confirm": False}
    rx = re.compile(c["pattern"])
    bare_ok, takes_args = bool(rx.match(f".{name}")), rx.groups > 0
    if name in NO_RUN or not (bare_ok or takes_args):
        mode = "none"
    elif name in SAFE and bare_ok:
        mode = "safe"
    else:
        mode = "input" if takes_args else "bare"
    return {"name": name, "mode": mode, "bare_ok": bare_ok, "takes_args": takes_args,
            "confirm": mode in ("input", "bare") and name not in LIGHT}


def compose(name: str, args: str = "") -> str:
    return f".{name}" + (f" {args}" if args else "")


def matches(ub, name: str, text: str) -> bool:
    c = next((c for c in ub.commands if c["name"] == name), None)
    return bool(c) and bool(re.match(c["pattern"], text))


def counts(ub, groups: dict) -> dict:
    out = {"safe": 0, "input": 0, "bare": 0, "none": 0}
    for gid, items in groups.items():
        if gid in (guide.BOT, guide.PANEL):
            continue
        for e in items:
            out[info(ub, e["name"])["mode"]] += 1
    return out


def command_groups(groups: dict) -> dict:
    """Bot / panel qrupları komanda deyil — katalogdan çıxarılır."""
    return {g: v for g, v in groups.items() if g not in (guide.BOT, guide.PANEL)}


def home_view(ub, groups: dict, tok: str, page: int = 0, note: str = ""):
    groups = command_groups(groups)
    n = counts(ub, groups)
    total = sum(n.values())
    lines = ["<i>🏠 › 🧩 Userbot › Əmrlər</i>", "▶️ <b>Əmrlər</b> — bütün komandalar düymə ilə", "━━━━━━━━━━━━━━━━━━",
             f"Cəmi: <b>{total}</b> · ▶️ {n['safe']} · ✍️ {n['input']} · ⚙️ {n['bare']} · 📖 {n['none']}", LEGEND,
             "<i>Çata mesaj yazılmır: funksiya birbaşa çağırılır, nəticə bu mesajda çıxır.</i>"]
    if note:
        lines += ["", note]
    items, page, pages = ui.paginate(list(groups.items()), page, GROUPS_PER_PAGE)
    rows = [[ui.btn(f"⚡ Sürətli əmrlər {n['safe']}", _cb(tok, "qs"))]]
    rows += ui.grid([ui.btn(f"{guide.group_label(g)} {len(v)}", _cb(tok, f"qg:{g}:0")) for g, v in items], 2)
    rows.append(ui.pager(f"ubp:{tok}:qk:", page, pages, _cb(tok, "nop")))
    rows.append(_nav(tok, "main", "Menyu", refresh=f"qk:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def group_view(ub, groups: dict, tok: str, gid: str, page: int = 0):
    items = command_groups(groups).get(gid)
    if not items:
        return home_view(ub, groups, tok)
    page_items, page, pages = ui.paginate(items, page, PER_PAGE)
    modes = {e["name"]: info(ub, e["name"])["mode"] for e in page_items}
    lines = [f"<i>🏠 › 🧩 Userbot › Əmrlər › {escape(guide.group_label(gid))}</i>",
             f"<b>{escape(guide.group_label(gid))}</b> · {len(items)}", "━━━━━━━━━━━━━━━━━━", LEGEND, ""]
    lines += [f"• {ICON[modes[e['name']]]} <code>{escape(e['button'])}</code> — {escape(e['short'])}" for e in page_items]
    rows = ui.grid([ui.btn(f"{ICON[modes[e['name']]]} {e['button']}"[:24], _cb(tok, f"qc:{e['name']}")) for e in page_items], 2)
    rows.append(ui.pager(f"ubp:{tok}:qg:{gid}:", page, pages, _cb(tok, "nop")))
    rows.append(_nav(tok, "qk", "Əmrlər", refresh=f"qg:{gid}:{page}"))
    return "\n".join(lines), ui.kb(*rows)


def _mode_note(i: dict) -> str:
    if i["mode"] == "safe":
        return "▶️ <b>Birbaşa işləyir</b> — təsdiq soruşulmur, nəticə bu mesajda çıxır."
    if i["mode"] == "input":
        note = ("✍️ <b>Arqument alır.</b> <i>✍️ Arqument yaz</i> düyməsinə bas, sonra arqumenti bu çata yaz — mesajınız "
                "silinir, nəticə bu mesajda çıxır. Reply tələb edən komandada hədəf mesaja <b>reply</b> edib arqumenti "
                "yazın; arqumentsiz işlətmək üçün yalnız <code>.</code> yazın.")
        return note + ("\n⚠️ Təsir edən komandadır — işləməzdən əvvəl təsdiq soruşulur." if i["confirm"] else "")
    if i["mode"] == "bare":
        return "⚙️ <b>Arqumentsiz işləyir.</b> Təsir edə bilər — işləməzdən əvvəl təsdiq soruşulur."
    return "📖 Bu komanda menyudan işləmir — çata əl ilə yazın."


def command_view(ub, groups: dict, tok: str, name: str, *, off: bool = False, protected: bool = False, note: str = "",
                 quiet=None, options: bool = False):
    gid, e = guide.find(groups, name)
    if e is None:
        return home_view(ub, groups, tok)
    i = info(ub, name)
    rows = []
    if off:
        pass
    elif i["mode"] == "safe":
        rows.append([ui.btn("▶️ İşlət", _cb(tok, f"run:{name}"))])
    elif i["mode"] in ("input", "bare"):
        rows.append(([ui.btn("✍️ Arqument yaz", _cb(tok, f"in:{name}"))] if i["takes_args"] else [])
                    + ([ui.btn("▶️ Arqumentsiz", _cb(tok, f"run:{name}"))] if i["bare_ok"] else []))
    return guide.command_view(groups, tok, name, off=off, protected=protected,
                              note="\n".join(x for x in (_mode_note(i) if not off else "🔴 <b>Söndürülüb</b> — işləmir.", note) if x),
                              extra=rows, back=(f"qg:{gid}:0", guide.short_label(gid)), quiet=quiet, ctx="q", options=options)


def confirm_view(tok: str, name: str, text: str, yes: str, no: str, extra: str = ""):
    body = (f"<i>🏠 › 🧩 Userbot › Əmrlər › {escape(name)}</i>\n⚠️ <b>İşlədilsin?</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"<code>{escape(text)}</code>" + (f"\n{extra}" if extra else "") + "\n\n<i>Komanda bu çatda sizin hesabdan icra olunur; nəticə bu mesajda çıxır.</i>")
    return body, ui.kb(ui.confirm(_cb(tok, yes), _cb(tok, no), "işlət"))


def prompt_view(ub, tok: str, name: str, usage: str, same_chat: bool = True):
    body = (f"<i>🏠 › 🧩 Userbot › Əmrlər › {escape(name)}</i>\n✍️ <b>.{escape(name)}</b> üçün arqument\n"
            "━━━━━━━━━━━━━━━━━━\n"
            + ("Arqumenti <b>bu çata</b> yazın (növbəti mesajınız).\n" if same_chat else
               "Menyu çatı bilinmir — arqumenti <b>şəxsi çatda / Saved Messages-də</b> yazın.\n")
            + "• Reply tələb edirsə — hədəf mesaja <b>reply</b> edib arqumenti yazın.\n"
              "• Arqumentsiz işlətmək üçün yalnız <code>.</code> yazın.\n"
              "• Başqa komanda yazsanız gözləmə ləğv olunur.\n\n" + (usage or ""))
    return body, ui.kb([ui.btn("✖️ Ləğv et", _cb(tok, f"qc:{name}"))])
