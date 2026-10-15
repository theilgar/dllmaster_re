"""🧩 Panel dizayn sistemi — bütün inline düymələr eyni lüğət və eyni qaydalarla qurulur.

QAYDALAR (hər panel):
  1. Naviqasiya sətri HƏMİŞƏ sonuncudur:  [⬅️ <Valideyn>] [🏠 Menyu] [✖️ Bağla]
     ⬅️ düyməsi getdiyi yeri adlandırır ("⬅️ Depo"); bilinmirsə — "⬅️ Geri".
     Bölmənin özü əsas menyuya qayıdırsa — yalnız [🏠 Menyu] [✖️ Bağla].
  2. 🔄 Yenilə — naviqasiyadan bir sətir yuxarıda (və ya naviqasiya sətrinin əvvəlində).
  3. Mətn / fayl gözləyən ekran — yalnız [✖️ Ləğv et] (lazımdırsa + ⬅️).
  4. Açar (bir funksiyanı yandır / söndür):  "<etiket>: 🟢 Aktiv" · "<etiket>: 🔴 Deaktiv"
     Yığcam sətirdə:                          "<etiket> 🟢" · "<etiket> 🔴"
  5. Rejim (avto / əl ilə):                   "<etiket>: 🤖 Avto" · "<etiket>: ✋ Manual"
  6. Tək seçim (variantlardan biri):         "✅ <variant>" · "<variant>"
     Çox seçim (siyahıda işarələmə):          "✅ <element>" · "▫️ <element>"
  7. Təhlükəli əməliyyat — təsdiq ekranı:     [✅ Bəli, <fel>] [✖️ İmtina]
  8. Səhifələmə:                              [◀️] [2/7] [▶️]
  9. Yazı — cümlə hərfi ilə ("Bütün qruplar", "Bütün Qruplar" yox), mötərizəsiz, qısa.
 10. Mətnli düymələr sətirdə ən çox 2, ikon / rəqəm düymələri ən çox 5.
"""
import logging
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# ── Lüğət — bütün layihədə yalnız bu sözlər ──
BACK = "⬅️ Geri"
HOME = "🏠 Menyu"
CLOSE = "✖️ Bağla"
CANCEL = "✖️ Ləğv et"
REFRESH = "🔄 Yenilə"
ON, OFF = "🟢 Aktiv", "🔴 Deaktiv"
AUTO, MANUAL = "🤖 Avto", "✋ Manual"
CHECKED, UNCHECKED = "✅", "▫️"
PREV, NEXT = "◀️", "▶️"

MAIN_MENU = "menu:main"          # 🏠 hədəfi (admin paneli)
CLOSE_MENU = "menu:cancel"       # ✖️ — gözləmələri ləğv edib mesajı bağlayır


def btn(text: str, data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    """Callback düyməsi (data) və ya link düyməsi (url=...). Layihədə düymə yaratmağın vahid yeri."""
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=data)


def paginate(items, page: int, per_page: int):
    """→ (cari səhifənin elementləri, düzəldilmiş səhifə (0-dan), cəmi səhifə sayı). Səhifə sərhəddən çıxsa sıxılır."""
    items = list(items)
    pages = max(1, -(-len(items) // per_page))
    page = max(0, min(int(page or 0), pages - 1))
    return items[page * per_page:(page + 1) * per_page], page, pages


def page_row(page: int, pages: int, cb, *, wrap: bool = False, center_cb: str | None = None):
    """[◀️] [n/m] [▶️] sətri; pages ≤ 1 — None. cb(p) → həmin səhifənin callback-i.
    wrap=True — ilk / son səhifədən dövrə (son → ilk). center_cb — ortadakı sayğac düyməsi (defolt: cari səhifə)."""
    if pages <= 1:
        return None
    prev_p = (page - 1) % pages if wrap else max(0, page - 1)
    next_p = (page + 1) % pages if wrap else min(pages - 1, page + 1)
    return [btn(PREV, cb(prev_p)), btn(f"{page + 1}/{pages}", center_cb if center_cb is not None else cb(page)),
            btn(NEXT, cb(next_p))]


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


# ── Standart düymələr ──
def back(data: str, to: str = "") -> InlineKeyboardButton:
    """⬅️ <to> — getdiyi pəncərənin adı (ikonsuz: "Depo", "Sistem"); boşdursa "⬅️ Geri"."""
    return btn(f"⬅️ {to}" if to else BACK, data)


def home(data: str = MAIN_MENU) -> InlineKeyboardButton:
    return btn(HOME, data)


def close(data: str = CLOSE_MENU) -> InlineKeyboardButton:
    return btn(CLOSE, data)


def cancel(data: str) -> InlineKeyboardButton:
    return btn(CANCEL, data)


def refresh(data: str) -> InlineKeyboardButton:
    return btn(REFRESH, data)


def nav(back_data: str = "", to: str = "", *, close_data: str = CLOSE_MENU,
        home_data: str = MAIN_MENU, refresh_data: str = "") -> list:
    """Naviqasiya sətri: [🔄] [⬅️ <to>] [🏠 Menyu] [✖️ Bağla]. back_data əsas menyudursa ⬅️ əvəzinə yalnız 🏠."""
    row = [refresh(refresh_data)] if refresh_data else []
    if back_data and back_data != home_data:
        row.append(back(back_data, to))
    if home_data:
        row.append(home(home_data))
    if close_data:
        row.append(close(close_data))
    return row


# ── Vəziyyət düymələri ──
def toggle(label: str, on: bool, data: str, compact: bool = False) -> InlineKeyboardButton:
    if compact:
        return btn(f"{label} {'🟢' if on else '🔴'}", data)
    return btn(f"{label}: {ON if on else OFF}", data)


def mode(label: str, auto: bool, data: str) -> InlineKeyboardButton:
    return btn(f"{label}: {AUTO if auto else MANUAL}", data)


def choice(label: str, selected: bool, data: str) -> InlineKeyboardButton:
    return btn(f"✅ {label}" if selected else label, data)


def check(label: str, checked: bool, data: str) -> InlineKeyboardButton:
    return btn(f"{CHECKED if checked else UNCHECKED} {label}", data)


def state(on: bool) -> str:
    """Mətn sətirləri üçün: '🟢 aktiv' / '🔴 deaktiv'."""
    return "🟢 aktiv" if on else "🔴 deaktiv"


# ── Quruluş ──
def grid(buttons: list, cols: int = 2) -> list:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def pager(prefix: str, page: int, pages: int, noop: str = "noop") -> list:
    """[◀️] [2/7] [▶️] — prefix + səhifə nömrəsi callback olur; tək səhifədə boş sətir."""
    if pages <= 1:
        return []
    row = [btn(PREV, f"{prefix}{page - 1}")] if page > 0 else []
    row.append(btn(f"{page + 1}/{pages}", noop))
    if page < pages - 1:
        row.append(btn(NEXT, f"{prefix}{page + 1}"))
    return row


def stepper(step_prefix: str, input_data: str, steps=(-5, -1, 1, 5)) -> list:
    """Rəqəm tənzimləyicisi: [-5] [-1] [+1] [+5] + [✏️ Öz rəqəmini yaz] — callback step_prefix + "+1"."""
    return [[btn(f"{d:+d}", f"{step_prefix}{d:+d}") for d in steps],
            [btn("✏️ Öz rəqəmini yaz", input_data)]]


def confirm(yes_data: str, no_data: str, verb: str = "təsdiqlə") -> list:
    return [btn(f"✅ Bəli, {verb}", yes_data), btn("✖️ İmtina", no_data)]


def premium_btn(text: str = "💎 Premium al", data: str = "sg:premium", url: str = None) -> InlineKeyboardButton:
    return url_btn(text, url) if url else btn(text, data)


def stop_btn(text: str = "⏹ Dayandır", data: str = "dlall_stop") -> InlineKeyboardButton:
    return btn(text, data)


def progress_btn(text: str = "📊 Proses", data: str = "bview:prog") -> InlineKeyboardButton:
    return btn(text, data)


def kb(*rows) -> InlineKeyboardMarkup:
    """Boş sətirləri ataraq klaviatura: kb([..], cond and [..], nav(...))."""
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


async def show(target, text: str, kb=None, disable_preview: bool = True, bot=None) -> bool:
    """Mesajı və ya CallbackQuery-ni təhlükəsiz yeniləyir ('not modified' və silinmiş mesaj xətalarını udur).

    target: CallbackQuery, Message, (chat_id, message_id) və ya (bot, chat_id, message_id).
    """
    try:
        if hasattr(target, "message") and getattr(target, "message", None):
            await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=disable_preview)
            return True
        elif hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=disable_preview)
            return True
        elif isinstance(target, (tuple, list)):
            if len(target) == 3:
                bot, chat_id, message_id = target
            elif len(target) == 2:
                if bot is None:
                    from bot.menus.state import get_context
                    ctx = get_context()
                    bot = getattr(ctx, "bot", None)
                chat_id, message_id = target
            else:
                return False
            if bot and chat_id and message_id:
                await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=disable_preview)
                return True
        elif hasattr(target, "inline_message_id") and target.inline_message_id:
            await target.bot.edit_message_text(text=text, inline_message_id=target.inline_message_id, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=disable_preview)
            return True
    except Exception as e:
        err = str(e).lower()
        ign = ("not modified", "message to edit not found", "message_id_invalid", "bot was blocked", "chat not found")
        if not any(k in err for k in ign):
            if "can't parse entities" in err:
                logger.warning("show() HTML parse xətası: %s | text: %r", e, text)
                plain_text = re.sub(r"<[^>]+>", "", text)
                try:
                    if hasattr(target, "message") and getattr(target, "message", None):
                        await target.message.edit_text(plain_text, parse_mode=None, reply_markup=kb, disable_web_page_preview=disable_preview)
                        return True
                    elif hasattr(target, "edit_text"):
                        await target.edit_text(plain_text, parse_mode=None, reply_markup=kb, disable_web_page_preview=disable_preview)
                        return True
                    elif isinstance(target, (tuple, list)) and bot and chat_id and message_id:
                        await bot.edit_message_text(text=plain_text, chat_id=chat_id, message_id=message_id, parse_mode=None, reply_markup=kb, disable_web_page_preview=disable_preview)
                        return True
                    elif hasattr(target, "inline_message_id") and target.inline_message_id:
                        await target.bot.edit_message_text(text=plain_text, inline_message_id=target.inline_message_id, parse_mode=None, reply_markup=kb, disable_web_page_preview=disable_preview)
                        return True
                except Exception as e2:
                    logger.warning("show() fallback (təmiz mətn) də alınmadı: %s", e2)
            else:
                logger.warning("show() mesaj yenilənmədi: %s", e)
        else:
            logger.debug("show() mesaj yenilənmədi (gözlənilən): %s", e)
    return False
