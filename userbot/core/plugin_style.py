"""
🎨 Userbot menyusu ↔ botun görünüşü (📋 kod bloku) — ortaq ayarlar.

Botun `bot/plugins/compact_ui_plugin.py` middleware-i admin panellərini avtomatik kod blokuna salır (ui_box, ui_style
box / script / quote, ui_bar, ui_kb). Userbot paneli də `ubp:` / `ubm:` / `ubc:` prefikslərinə görə həmin middleware-dən
keçir — yəni redaktələr onsuz da bu ayara tabedir. Bu modul iki şeyi tamamlayır:

  1. `apply()` — middleware-in toxunmadığı yerlər: inline sorğunun İLK nəticəsi (.menu / .help / komanda kartı),
     ki, menyu ilk göndərilən anda da eyni görünsün;
  2. 🎨 Görünüş səhifəsi — .menu-dan eyni ayarları dəyişmək (botun /ui paneli ilə ORTAQ: birində dəyişəndə digərində də).
"""
import logging

from bot.core import panel as ui
from userbot.core.plugin_guide import _cb, _nav

logger = logging.getLogger(__name__)


def _cui():
    from bot.plugins import compact_ui_plugin
    return compact_ui_plugin


def state() -> dict:
    """{box, kb, style, bar} — botun /ui ayarları (yoxlana bilmirsə standart: hamısı açıq)."""
    try:
        from common.menu import get_ui_bar, get_ui_style
        cui = _cui()
        return {"box": cui.cat_on("admin"), "kb": cui._on("ui_kb"), "style": get_ui_style(), "bar": get_ui_bar()}
    except Exception as e:
        logger.debug(f"görünüş ayarı oxunmadı: {e}")
        return {"box": True, "kb": True, "style": "box", "bar": "vert"}


def apply(text: str, kb=None):
    """Botun middleware-inin etdiyini inline nəticəyə də tətbiq edir: başlıq qalın, qalanı kod blokunda (seçilmiş üslubla),
    naviqasiya düymələri ikon. Ayar söndürülübsə və ya alınmasa — olduğu kimi qaytarılır."""
    st = state()
    try:
        cui = _cui()
        if st["box"]:
            new = cui.boxify(text, True, style=st["style"])
            text = new or text
        if st["kb"] and kb is not None:
            kb = cui.compact_kb(kb)
    except Exception as e:
        logger.debug(f"inline görünüş tətbiq olunmadı: {e}")
    return text, kb


def apply_text(text: str) -> str:
    """Düyməsiz mətn (inline qadağan olan çatda sadə cavab) — userbot_api.box_text ilə eyni ayar."""
    from userbot.core.userbot_api import box_text
    return box_text(text)


# ───────────── 🎨 Görünüş səhifələri ─────────────
def set_toggle(key: str) -> str:
    """'box' | 'kb' → botun ui_box / ui_kb ayarını çevirir. Qaytarır: bildiriş."""
    from common.database import get_db
    setting = {"box": "ui_box", "kb": "ui_kb"}[key]
    cur = _cui()._on(setting)
    get_db().set_setting(setting, "0" if cur else "1")
    label = "📋 Kod bloku" if key == "box" else "🧩 Yığcam naviqasiya"
    return f"{label}: {'🔴 deaktiv' if cur else '🟢 aktiv'}"


def set_style(style: str) -> str:
    from common.menu import UI_STYLE_LABELS, UI_STYLES, set_ui_style
    if style not in UI_STYLES:
        return "❌ Naməlum üslub"
    set_ui_style(style)
    return f"🎨 Üslub: {UI_STYLE_LABELS[style]}"


def set_bar(bar: str) -> str:
    from common.menu import UI_BAR_LABELS, UI_BAR_STYLES, set_ui_bar
    if bar not in UI_BAR_STYLES:
        return "❌ Naməlum bar"
    set_ui_bar(bar)
    return f"📊 Bar: {UI_BAR_LABELS[bar]}"


def view(tok: str, note: str = ""):
    from common.menu import UI_BAR_LABELS, UI_STYLE_LABELS, _bar_box
    st = state()
    lines = ["<i>🏠 › 🧩 Userbot › Görünüş</i>", "🎨 <b>Görünüş</b> — botla ortaq", "━━━━━━━━━━━━━━━━━━",
             f"Kod bloku: {ui.state(st['box'])}", f"Üslub: {UI_STYLE_LABELS.get(st['style'], st['style'])}",
             f"Bar: {UI_BAR_LABELS.get(st['bar'], st['bar'])}", f"Yığcam naviqasiya: {ui.state(st['kb'])}",
             "", "Nümunə:", "STATUS  ● alive", f"BAR     {_bar_box(0.5, 10, st['bar'])} 50%",
             "", "<i>Bu ayarlar botun /ui paneli və /menu → 🖥 Sistem → 📋 Görünüş ilə eynidir: birində dəyişəndə "
                 "digərində də dəyişir. Userbot menyusu, kartları və komanda nəticələri bu görünüşü izləyir.</i>"]
    if note:
        lines += ["", note]
    rows = [[ui.toggle("📋 Kod bloku", st["box"], _cb(tok, "uit:box"), compact=True),
             ui.toggle("🧩 Yığcam", st["kb"], _cb(tok, "uit:kb"), compact=True)],
            [ui.btn("🎨 Üslub", _cb(tok, "uis")), ui.btn("📊 Bar", _cb(tok, "uib"))],
            _nav(tok, "main", "Menyu", refresh="ui")]
    return "\n".join(lines), ui.kb(*rows)


def style_picker(tok: str, note: str = ""):
    from common.menu import UI_STYLE_DESCS, UI_STYLE_LABELS, UI_STYLES
    cur = state()["style"]
    lines = ["<i>🏠 › 🧩 Userbot › Görünüş › Üslub</i>", "🎨 <b>Kod bloku üslubu</b>", "━━━━━━━━━━━━━━━━━━"]
    lines += [f"• {UI_STYLE_LABELS[s]} — {UI_STYLE_DESCS[s].split(': ', 1)[-1]}" for s in UI_STYLES]
    if note:
        lines += ["", note]
    rows = [[ui.choice(UI_STYLE_LABELS[s], s == cur, _cb(tok, f"uis:{s}"))] for s in UI_STYLES]
    rows.append(_nav(tok, "ui", "Görünüş", refresh="uis"))
    return "\n".join(lines), ui.kb(*rows)


def bar_picker(tok: str, note: str = ""):
    from common.menu import UI_BAR_LABELS, UI_BAR_STYLES, _bar_box
    cur = state()["bar"]
    lines = ["<i>🏠 › 🧩 Userbot › Görünüş › Bar</i>", "📊 <b>Bar üslubu</b>", "━━━━━━━━━━━━━━━━━━"]
    lines += [f"• {UI_BAR_LABELS[b]} — {_bar_box(0.6, 10, b)}" for b in UI_BAR_STYLES]
    if note:
        lines += ["", note]
    rows = [[ui.choice(UI_BAR_LABELS[b], b == cur, _cb(tok, f"uib:{b}"))] for b in UI_BAR_STYLES]
    rows.append(_nav(tok, "ui", "Görünüş", refresh="uib"))
    return "\n".join(lines), ui.kb(*rows)
