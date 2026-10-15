"""
📋 Yığcam admin paneli — bot/plugins/compact_ui_plugin.py

/menu və bütün admin panelləri (📦 depo, 💾 yedək, 🧩 plugin-lər, 🎛 İmkanlar, 📜 loglar ...) üçün
bot sessiyasına request-middleware qoyur — heç bir panelin kodunu dəyişmədən:

  📋 Kod bloku   — mesajın başlığı qalın sətir, qalanı <pre> blokunda (Telegram-da "Kopyala" düyməsi ilə,
                   monoşrift, sıx). Ayırıcı xətlər (──── / ━━━━) atılır, boş sətirlər yığılır.
                   Linkli sətirlər (profil, kanal) blokun altında klikli qalır.
  🧩 Yığcam naviqasiya — "🏠 Menyu" → 🏠, "✖️ Bağla" → ✖️, "🔄 Yenilə" → 🔄
                   (sətir düzülüşü panelin özünündür — bot/core/panel.py qaydaları)

📂 KATEQORİYALAR — /ui (və /menu → 🖥 Sistem → 📋 Görünüş) hər birini ayrıca açıb-bağlayır:
  🛠 admin     — admin panelləri (klaviaturadakı BÜTÜN callback-lər admin prefiksli: inline_panels.py)
  ⬇️ progress  — toplu yükləmənin canlı prosesi (⏹ Dayandır / 📊 Proses düymələri)
  📊 report    — yükləmə hesabatları (yekun, 🔁 təkrar, ♻️ restartdan sonra davam)
  🎙 cards     — ifaçı (/artist), 🎶 playlist və 🔗 link siyahısı kartları
  🔔 notice    — bildirişlər: qrup flood sualı, köməkçi botlar
Düyməsiz mesajlar üçün göndərən kod `styled("report")` kontekstini açır.
Mahnılar, axtarış nəticələri, /start, broadcast önizləməsi və şablon nümunələri heç vaxt dəyişmir.

Creator: /ui — hər iki rejimi aç / bağla (settings: ui_box, ui_kb; default: açıq)
  + 🔔 Yeniləmə bildirişi (update_notifier_plugin, setting ui_update):
       📝 Tam · 📋 Kod bloku · ✂️ Qısa (yalnız fayllar, kod blokunda) · 🔕 Söndür (xəta olanda yenə gəlir)

🛡 Spam qoruması: kod bloku formatı fərqli mətnləri eyni göstərə bilər (məs. yalnız <b>/<i> dəyişən
canlı irəliləyiş paneli) → Telegram "message is not modified" qaytarır. Bir çox panel redaktə alınmayanda
YENİ mesaj göndərir — bu da çatı doldurub flood limitinə salır. Ona görə formatlanmış redaktədə bu xəta
uğur sayılır (True). Flood (RetryAfter) redaktədə çağırana qaytarılır — canlı panel özü gözləyib
yavaşlasın; SendMessage üçün ≤ 10 san. gözləyib bir dəfə təkrarlanır.
"""
import asyncio
import contextlib
import contextvars
import html
import logging
import re

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import panel as ui
from common.database import get_db
from common.menu import (
    get_ui_style, set_ui_style, wrap_ui_tags, cycle_ui_style,
    UI_STYLES, UI_STYLE_LABELS, UI_STYLE_DESCS,
    get_ui_bar, set_ui_bar, cycle_ui_bar,
    UI_BAR_STYLES, UI_BAR_LABELS, UI_BAR_DESCS,
)
from common.kv import next_of

logger = logging.getLogger(__name__)

PRIORITY = 6                       # tez yüklənsin — digər plugin-lərin ilk mesajları da yığcam olsun
EXTRA_PREFIXES = (
    "ui:", "kb_btn:", "test_mp3:", "mp3_u:", "mp3_g:", "codec:", "gmenu:", "grp:",
    "ubm:", "ubc:", "meta:", "shz:", "tip:", "tip_prompt:", "pm_grp:", "vj:", "vp:", "mp3_lk:", "vq:", "vr:"
)
SKIP_CALLBACKS = ("menu:bc_send",)  # 📢 broadcast önizləməsi — istifadəçilərin görəcəyi kimi qalmalıdır
SEP_RE = re.compile(r"^\s*[─━═—–\-_=·•.]{4,}\s*$")
TAG_RE = re.compile(r"<[^>]+>")
SHORT = {"🏠 Menyu": "🏠", "✖️ Bağla": "✖️", "🔄 Yenilə": "🔄", "❌ Bağla": "✖️"}
LIMIT = 4096
_RAW = contextvars.ContextVar("compact_ui_raw", default=False)

CATS = {
    "admin":    ("🛠 Admin panelləri", None),
    "user":     ("👤 İstifadəçi salamı (/start)", ("sg:", "lang:", "buy:")),
    "progress": ("⬇️ Yükləmə prosesi", ("dlall_stop", "bview:")),
    "report":   ("📊 Yükləmə hesabatları", ("bretry:", "bresume:")),
    "cards":    ("🎵 Kartlar (ifaçı · playlist · link)", ("art:", "pl:", "lk:")),
    "notice":   ("🔔 Bildirişlər (flood · köməkçi)", ("hgs:", "dh:")),
    "voice":    ("🎙 Canlı yayım", ("vp:", "vj:")),
}
_FORCE = contextvars.ContextVar("compact_ui_force", default=None)

VOICE_SIGNATURES = (
    "İndi oxunur", "Fasilə", "Yayım bitdi", "Növbə bitdi",
    "Mix başladı:", "Mix:", "Səsli yayım", "Səsli söhbət",
    "Aktiv yayımlar", "vctest"
)


def cat_on(cat: str) -> bool:
    if cat == "admin":
        return _on("ui_box")                         # köhnə ayar (geriyə uyğunluq)
    return _on(f"ui_cat:{cat}")


@contextlib.contextmanager
def styled(cat: str):
    """Düyməsiz mesaj da bu kateqoriyada kod blokuna düşsün (məs. yekun hesabat)."""
    token = _FORCE.set(cat)
    try:
        yield
    finally:
        _FORCE.reset(token)


def classify(markup, raw_text: str = None) -> str:
    """Mesajın kateqoriyası və ya None."""
    if is_admin_kb(markup):
        return "admin"
    if isinstance(markup, InlineKeyboardMarkup):
        datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
        if any(d in SKIP_CALLBACKS for d in datas):
            return None
        for cat, (_, pref) in CATS.items():
            if pref and any(d == p or d.startswith(p) for d in datas for p in pref):
                return cat
    f = _FORCE.get()
    if f:
        return f
    if raw_text and any(sig in raw_text for sig in VOICE_SIGNATURES):
        return "voice"
    return None


UPDATE_STYLES = ("box", "script", "quote", "short", "full", "off")
UPDATE_LABEL = {
    "box": "📋 Kod bloku",
    "script": "📜 Script sitat",
    "quote": "💬 Sitat",
    "short": "✂️ Qısa",
    "full": "📝 Tam (bloksuz)",
    "off": "🔕 Söndür"
}


def update_style() -> str:
    try:
        v = get_db().get_setting("ui_update")
    except Exception:
        v = None
    return v if v in UPDATE_STYLES else "box"



@contextlib.contextmanager
def raw():
    """Bu blokda göndərilən mesajlara middleware toxunmur (mətni çağıran özü formatlayıb)."""
    token = _RAW.set(True)
    try:
        yield
    finally:
        _RAW.reset(token)


def _on(key: str) -> bool:
    try:
        return (get_db().get_setting(key) or "1") == "1"
    except Exception:
        return True


def _prefixes():
    try:
        from bot.core.inline_panels import ADMIN_PREFIXES
        return tuple(ADMIN_PREFIXES) + EXTRA_PREFIXES
    except Exception:
        return ("menu:",) + EXTRA_PREFIXES


def is_admin_kb(markup) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup) or not markup.inline_keyboard:
        return False
    pref = _prefixes()
    seen = False
    for row in markup.inline_keyboard:
        for b in row:
            data = b.callback_data
            if data is None:
                continue                                # url / switch_inline düymələri — neytral
            if data in SKIP_CALLBACKS or not data.startswith(pref):
                return False
            seen = True
    return seen


# ───────────────────────── 📋 mətn ─────────────────────────
def boxify(text: str, is_html: bool, limit: int = LIMIT, style: str = None):
    """→ yeni HTML mətn və ya None (toxunulmasın)."""
    if not text:
        return None
    if style is None:
        style = get_ui_style()
    if "<pre" in text or "<blockquote" in text:
        from common.menu import adapt_ui_markup
        adapted = adapt_ui_markup(text, style)
        if len(adapted) <= limit:
            return adapted
        return None
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None

    def plain(s):
        return html.unescape(TAG_RE.sub("", s)) if is_html else s

    link_m = re.search(r'<a\s+href="([^"]+)">([\s\S]*?)</a>', lines[0]) if is_html else None
    if link_m:
        href, inner = link_m.group(1), html.unescape(TAG_RE.sub("", link_m.group(2)))
        tokenized = re.sub(r'<a\s+href="[^"]+">[\s\S]*?</a>', "\x00", lines[0], count=1)
        plain_t = html.unescape(TAG_RE.sub("", tokenized))
        p0, p1 = (plain_t.split("\x00", 1) + [""])[:2]
        mid = f" <a href=\"{href}\">{html.escape(inner.strip())}</a>" if p0.strip() else f"<a href=\"{href}\">{html.escape(inner.strip())}</a>"
        tail = f" {html.escape(p1.strip())}" if p1.strip() else ""
        title_head = f"<b>{html.escape(p0.rstrip())}{mid}{tail}</b>".strip()
    else:
        title = plain(lines[0]).strip()
        title_head = f"<b>{html.escape(title)}</b>"

    body, links = [], []
    for line in lines[1:]:
        if SEP_RE.match(plain(line)):
            continue
        if is_html and "<a " in line:
            links.append(line.strip())
            continue
        body.append(plain(line).rstrip())
    out = []                                            # boş sətirləri yığ, kənarları kəs
    for line in body:
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()

    tag_open, tag_close = wrap_ui_tags(style)

    res = title_head
    if out:
        res += f"\n{tag_open}{html.escape(chr(10).join(out))}{tag_close}"
    if links:
        res += "\n" + "\n".join(links)
    if len(res) <= limit:
        return res
    avail = limit - len(f"{title_head}\n{tag_open}{tag_close}") - (len("\n" + "\n".join(links)) if links else 0) - 25
    if avail > 100:
        cur_body = ""
        trimmed_out = []
        for line in out:
            cand = cur_body + ("\n" if cur_body else "") + line
            if len(html.escape(cand)) + 20 > avail:
                trimmed_out.append("… (davamı kəsildi)")
                break
            cur_body = cand
            trimmed_out.append(line)
        res = title_head
        if trimmed_out:
            res += f"\n{tag_open}{html.escape(chr(10).join(trimmed_out))}{tag_close}"
        if links:
            res += "\n" + "\n".join(links)
        return res
    return None



# ───────────────────────── 🧩 düymələr ─────────────────────────
def _short(b: InlineKeyboardButton) -> InlineKeyboardButton:
    new = SHORT.get(b.text)
    return b.model_copy(update={"text": new}) if new else b


def compact_kb(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Yalnız naviqasiya qısalır (🏠 ✖️ 🔄 — ikon). Sətir düzülüşü panelin özünündür (bot/core/panel.py
    qaydaları) — əvvəlki avtomatik cütləşdirmə düzülüşü gözlənilməz edirdi, ona görə çıxarıldı."""
    return InlineKeyboardMarkup(inline_keyboard=[[_short(b) for b in row] for row in markup.inline_keyboard])


def ui_view(from_menu: bool = False):
    from common.menu import render_card, format_table, _bar_box
    kb, upd = _on("ui_kb"), update_style()
    style = get_ui_style()
    bar = get_ui_bar()

    t_rows = [
        ("STY", style.upper(), f"{UI_STYLE_LABELS.get(style, '')}"),
        ("BAR", _bar_box(0.5, 10), f"{UI_BAR_LABELS.get(bar, '')}"),
        ("NAV", "🟢 aktiv" if kb else "🔴 deaktiv", "yığcam ikonlar (🏠 ✖️ 🔄)"),
        ("UPD", upd.upper(), f"{UPDATE_LABEL.get(upd, upd)}"),
    ]
    for cat, (label, _) in CATS.items():
        on = cat_on(cat)
        t_rows.append((cat[:3].upper(), "🟢 açıq" if on else "⚪ bağlı", label))

    head = "📋 <b>Görünüş & İnterfeys Tənzimləmələri</b>"
    card_lines = [
        "Görünüş: 🟢 aktiv · vahid üslub",
        "──────────────────────────────────────",
        "BÖLMƏ  STAT/BAR        TƏSVİR",
        "─────  ─────────────── ────────────────",
    ] + format_table(t_rows)
    legend = f"{UI_STYLE_DESCS.get(style, '')}\n{UI_BAR_DESCS.get(bar, '')}"
    text = render_card(head, card_lines, legend)

    rows = []
    for cat, (label, _) in CATS.items():
        on = cat_on(cat)
        rows.append([ui.check(label, on, f"ui:c:{cat}")])
    rows.append([ui.btn("✅ Hamısını seç", "ui:all:1"), ui.btn("▫️ Heç biri", "ui:all:0")])
    rows.append([
        ui.btn(f"🎨 {UI_STYLE_LABELS.get(style, 'Kod bloku')}", "ui:style"),
        ui.btn(f"{UI_BAR_LABELS.get(bar, 'Şaquli')}", "ui:bar"),
    ])
    rows.append([
        ui.toggle("🧩 Yığcam", kb, "ui:kb", compact=True),
        ui.btn(f"🔔 {UPDATE_LABEL.get(upd, upd)}", "ui:upd"),
    ])
    rows.append(ui.nav("menu:dev", "Bot idarəsi", close_data="ui:x"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def setup(context):
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    from aiogram.methods import (
        SendMessage, EditMessageText,
        SendPhoto, SendAnimation, SendVideo, EditMessageCaption,
    )

    bot, dp = context.bot, context.dp

    TEXT_METHODS = (SendMessage, EditMessageText)
    CAPTION_METHODS = (SendPhoto, SendAnimation, SendVideo, EditMessageCaption)
    ALL_METHODS = TEXT_METHODS + CAPTION_METHODS

    def resolve_mode(method):
        pm = getattr(method, "parse_mode", None)
        if isinstance(pm, str):
            return pm
        default = getattr(getattr(bot, "default", None), "parse_mode", None)
        return default if isinstance(default, str) else None

    class CompactPanels(BaseRequestMiddleware):
        async def __call__(self, make_request, b, method):
            is_matched = False
            if _RAW.get():
                return await make_request(b, method)
            try:
                if isinstance(method, ALL_METHODS):
                    reply_markup = getattr(method, "reply_markup", None)
                    is_caption = isinstance(method, CAPTION_METHODS)
                    has_entities = bool(method.caption_entities if is_caption else method.entities)
                    raw_text = method.caption if is_caption else method.text
                    cat = classify(reply_markup, raw_text)
                    if cat and not has_entities and raw_text:
                        is_matched = True
                        upd = {}
                        if cat_on(cat):
                            mode = (resolve_mode(method) or "").upper()
                            if mode in ("HTML", ""):
                                max_limit = 1024 if is_caption else LIMIT
                                is_html_text = mode == "HTML" or "<" in raw_text
                                style = get_ui_style()
                                new = boxify(raw_text, is_html_text, limit=max_limit, style=style)
                                if new and new != raw_text:
                                    if is_caption:
                                        upd["caption"], upd["parse_mode"] = new, "HTML"
                                    else:
                                        upd["text"], upd["parse_mode"] = new, "HTML"
                                elif mode != "HTML" and ("<" in raw_text or (new and "<" in new)):
                                    upd["parse_mode"] = "HTML"
                        if cat == "admin" and _on("ui_kb") and reply_markup:
                            upd["reply_markup"] = compact_kb(reply_markup)
                        if upd:
                            method = method.model_copy(update=upd)
            except Exception as e:                      # heç vaxt paneli sındırmasın
                logger.debug(f"compact_ui: {e}")
            if not is_matched:
                return await make_request(b, method)
            try:
                return await make_request(b, method)
            except TelegramBadRequest as e:
                if isinstance(method, (EditMessageText, EditMessageCaption)) and "not modified" in str(e):
                    return True                         # görünüş onsuz da eynidir — uğur say
                if "can't parse entities" in str(e).lower() and is_matched:
                    logger.warning("compact_ui: Telegram HTML parse xətası, orijinal mətnlə təkrar cəhd edilir: %s", e)
                    orig_upd = {}
                    if is_caption:
                        orig_upd["caption"] = raw_text
                    else:
                        orig_upd["text"] = raw_text
                    try:
                        return await make_request(b, method.model_copy(update=orig_upd))
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/plugins/compact_ui_plugin.py:388]: %r", _e)
                raise
            except TelegramRetryAfter as e:
                # Redaktə: xəta ÇAĞIRANA qaytarılır — canlı panellər (📊 /depotop) özləri gözləyib yavaşlayır.
                # (Əvvəl burada udulurdu → monitor flood-u görmür, hər saniyə yenə yoxlayıb limiti uzadırdı.)
                if isinstance(method, (EditMessageText, EditMessageCaption)):
                    raise
                if e.retry_after <= 10:
                    await asyncio.sleep(e.retry_after + 1)
                    return await make_request(b, method)
                raise

    old = getattr(context, "_compact_ui_mw", None)      # 🔄 yenidən yükləmədə köhnəsini çıxar
    if old is not None:
        try:
            bot.session.middleware.unregister(old)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/compact_ui_plugin.py:330]: %r", _e)
    mw = CompactPanels()
    bot.session.middleware(mw)
    context._compact_ui_mw = mw

    is_creator = context.is_creator


    @dp.message(Command("ui"), F.chat.type == "private")
    async def ui_cmd(message: types.Message):
        if not is_creator(message.from_user.id):
            return
        text, kb = ui_view()
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

    @dp.callback_query(F.data.startswith("ui:"))
    async def ui_cb(cb: types.CallbackQuery):
        act = cb.data.split(":", 1)[1]
        if act == "x":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/compact_ui_plugin.py:376]: %r", _e)
            return
        if act == "open":                               # /menu → 🖥 Sistem → 📋 Görünüş
            await cb.answer()
            text, kb = ui_view()
            try:
                await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception as _e:
                logger.exception("Görünüş paneli açılmadı: %s", _e)
            return
        if act == "style":
            next_style = cycle_ui_style()
            await cb.answer(f"🎨 Üslub: {UI_STYLE_LABELS[next_style]}")
        elif act.startswith("style:"):
            s = act[6:]
            if s in UI_STYLES:
                set_ui_style(s)
                await cb.answer(f"🎨 Üslub: {UI_STYLE_LABELS[s]}")
        elif act == "bar":
            next_bar = cycle_ui_bar()
            await cb.answer(f"📊 Bar: {UI_BAR_LABELS[next_bar]}")
        elif act.startswith("bar:"):
            b = act[4:]
            if b in UI_BAR_STYLES:
                set_ui_bar(b)
                await cb.answer(f"📊 Bar: {UI_BAR_LABELS[b]}")
        elif act.startswith("c:"):
            cat = act[2:]
            if cat in CATS:
                key = "ui_box" if cat == "admin" else f"ui_cat:{cat}"
                get_db().set_setting(key, "0" if cat_on(cat) else "1")
        elif act.startswith("all:"):
            v = act[4:]
            for cat in CATS:
                get_db().set_setting("ui_box" if cat == "admin" else f"ui_cat:{cat}", v)
            get_db().set_setting("ui_update", "box" if v == "1" else "off")
        elif act == "upd":
            cur = update_style()
            get_db().set_setting("ui_update", next_of(UPDATE_STYLES, cur))
        key = {"box": "ui_box", "kb": "ui_kb"}.get(act)
        if key:
            get_db().set_setting(key, "0" if _on(key) else "1")
        if not cb.data.endswith(":style") and not act.startswith("style:") and not cb.data.endswith(":bar") and not act.startswith("bar:"):
            await cb.answer()
        text, kb = ui_view()
        try:
            await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception as _e:
            logger.exception("Görünüş paneli redaktə edilmədi: %s", _e)

    from common.utilities import spawn, temp_cleaner_loop
    context._temp_cleaner_task = spawn(temp_cleaner_loop())
    logger.info("📋 Yığcam admin paneli aktivdir (/ui) · Avto-keş təmizləyici işə salındı")


def teardown(context):
    cl_t = getattr(context, "_temp_cleaner_task", None)
    if cl_t and not cl_t.done():
        cl_t.cancel()
    context._temp_cleaner_task = None

    mw = getattr(context, "_compact_ui_mw", None)
    if mw is not None:
        try:
            context.bot.session.middleware.unregister(mw)
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/compact_ui_plugin.py:418]: %r", _e)
        context._compact_ui_mw = None
