"""
bot.menus.keyboards
Təkrar istifadə olunan Inline klaviatura və düymə şablonları.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


from bot.core.panel import btn  # noqa: E402,F401  (vahid düymə fabriki)


def menu_btn(text: str, menu_action: str) -> InlineKeyboardButton:
    """'menu:<action>' formatında menyu düyməsi yaradır."""
    return btn(text, f"menu:{menu_action}")


def toggle_btn(label: str, is_on: bool, action: str, on_icon: str = "🟢", off_icon: str = "🔴") -> InlineKeyboardButton:
    """Status ikonlu (🟢/🔴) toggle düyməsi yaradır."""
    icon = on_icon if is_on else off_icon
    return InlineKeyboardButton(text=f"{label} · {icon}", callback_data=f"menu:{action}")


def grid(buttons: list[InlineKeyboardButton], per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    """Düymələr siyahısını sətirlərə (grid) bölür — bot/core/panel.grid."""
    from bot.core import panel

    return panel.grid(buttons, per_row)


def choice_row(
    options: list[tuple[str, str]],
    current: str,
    prefix: str,
    per_row: int = 3,
) -> list[list[InlineKeyboardButton]]:
    """Radio-tipli seçim cədvəli yaradır (seçilmiş seçimdə ✅ göstərir)."""
    buttons = []
    for key, label in options:
        sel = "✅ " if key == current else ""
        buttons.append(InlineKeyboardButton(text=f"{sel}{label}", callback_data=f"{prefix}:{key}"))
    return grid(buttons, per_row=per_row)


def make_markup(
    rows: list[list[InlineKeyboardButton]],
    nav: list[InlineKeyboardButton] | None = None,
) -> InlineKeyboardMarkup:
    """Düymə sətirlərindən və naviqasiya sətrindən InlineKeyboardMarkup yaradır."""
    final_rows = list(rows)
    if nav:
        final_rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=final_rows)


def mode_btn(label: str, auto: bool, prefix: str):
    """🤖 Avto / ✋ Manual — bir düymə: basanda rejim dəyişir (callback: <prefix>:auto|manual).
    Avtoda bot dəyəri özü seçir və variantlar gizlənir; manualda variantlar ayrıca düymələrdir."""
    from bot.core import panel

    return panel.mode(label, auto, f"menu:{prefix}:{'manual' if auto else 'auto'}")


__all__ = [
    "btn",
    "choice_row",
    "grid",
    "make_markup",
    "menu_btn",
    "mode_btn",
    "toggle_btn",
]
