"""
bot.menus.navigation
Menyu naviqasiyası, geri qayıtma yığını (back stack) və naviqasiya düymələri.
"""


from aiogram.types import InlineKeyboardButton

from common.menu import menu_cb
from common.menu import nav_row as _menu_nav_row

# İstifadəçi üzrə naviqasiya yığını (user_id -> [page_keys])
_USER_NAV_STACK: dict[int, list[str]] = {}
MAX_STACK_DEPTH = 10


def push_nav(user_id: int, page_key: str):
    """Naviqasiya yığınına səhifə açarını əlavə edir."""
    if not page_key or page_key == "main":
        _USER_NAV_STACK[user_id] = ["main"]
        return
    stack = _USER_NAV_STACK.setdefault(user_id, ["main"])
    if not stack or stack[-1] != page_key:
        stack.append(page_key)
        if len(stack) > MAX_STACK_DEPTH:
            _USER_NAV_STACK[user_id] = stack[-MAX_STACK_DEPTH:]


def pop_nav(user_id: int) -> str:
    """Əvvəlki səhifəni çıxarır və qaytarır."""
    stack = _USER_NAV_STACK.get(user_id, [])
    if len(stack) > 1:
        stack.pop()
        return stack[-1]
    return "main"


def current_nav(user_id: int) -> str:
    """Cari səhifə açarını qaytarır."""
    stack = _USER_NAV_STACK.get(user_id, [])
    return stack[-1] if stack else "main"


def clear_nav(user_id: int):
    """İstifadəçinin naviqasiya tarixçəsini sıfırlayır."""
    _USER_NAV_STACK.pop(user_id, None)


def back_button(target: str = "main", text: str = "⬅️ Geri") -> InlineKeyboardButton:
    """Geri qayıtma düyməsi."""
    return InlineKeyboardButton(text=text, callback_data=menu_cb("back" if target in ("auto", "back") else target))


def refresh_button(action: str = "refresh", text: str = "🔄 Yenilə") -> InlineKeyboardButton:
    """Yeniləmə düyməsi."""
    return InlineKeyboardButton(text=text, callback_data=menu_cb(action))


def home_button(text: str = "🏠 Menyu") -> InlineKeyboardButton:
    """Əsas menyuya birbaşa qayıtma düyməsi."""
    return InlineKeyboardButton(text=text, callback_data="menu:main")


def close_button(text: str = "✖️ Bağla") -> InlineKeyboardButton:
    """Menyunu bağlama düyməsi."""
    return InlineKeyboardButton(text=text, callback_data="menu:cancel")


def nav_row(
    back: str = "main",
    refresh: str = "",
    extra: InlineKeyboardButton | None = None,
    *,
    to: str = "",
    include_home: bool = True,
    include_close: bool = True,
) -> list[InlineKeyboardButton]:
    """Standart vahid naviqasiya sətri — common.menu.nav_row ilə eyni (təkrar kod silindi; fərq yalnız arqument sırası).

    - 1-ci səviyyə (back == "main"):  [🔄 Yenilə]  [🏠 Əsas Menyu]  [✖️ Bağla]  (+ extra)
    - 2+ səviyyə (back != "main"):    [🔄 Yenilə]  [⬅️ Geri]  [🏠 Menyu]  [✖️ Bağla]  (+ extra)
    """
    return _menu_nav_row(back, refresh, to, extra, include_home=include_home, include_close=include_close)


__all__ = [
    "back_button",
    "clear_nav",
    "close_button",
    "current_nav",
    "home_button",
    "nav_row",
    "pop_nav",
    "push_nav",
    "refresh_button",
]
