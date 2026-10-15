"""
bot.menus.pagination
Standart 5 düyməli səhifələmə (pagination) sistemi.

Dəstəklənən düymələr:
[⏮ İlk] [◀ Əvvəlki] [ 📄 {page}/{total} ] [Növbəti ▶] [Sonuncu ⏭]
"""

import math
from collections.abc import Sequence
from typing import Any

from aiogram.types import InlineKeyboardButton


class Pagination:
    """Siyahılar üçün təhlükəsiz səhifələmə idarəedicisi."""

    def __init__(self, total_items: int, page: int = 1, per_page: int = 8):
        self.total_items = max(0, total_items)
        self.per_page = max(1, per_page)
        self.total_pages = max(1, math.ceil(self.total_items / self.per_page)) if self.total_items > 0 else 1
        self.page = max(1, min(page, self.total_pages))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def start_idx(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def end_idx(self) -> int:
        return min(self.start_idx + self.per_page, self.total_items)

    def slice(self, items: Sequence[Any]) -> list[Any]:
        """Verilən siyahıdan cari səhifəyə aid elementləri seçir."""
        if not items:
            return []
        return list(items[self.start_idx : self.end_idx])

    def keyboard_row(
        self,
        prefix: str,
        extra: str = "",
        first_label: str = "⏮ İlk",
        prev_label: str = "◀ Əvvəlki",
        next_label: str = "Növbəti ▶",
        last_label: str = "Sonuncu ⏭",
        noop_prefix: str = "noop",
    ) -> list[InlineKeyboardButton]:
        """5 düyməli naviqasiya sətrini qurur."""
        if self.total_pages <= 1:
            return []

        def make_cb(target_page: int) -> str:
            suffix = f":{extra}" if extra else ""
            return f"{prefix}:{target_page}{suffix}"

        row: list[InlineKeyboardButton] = []

        # 1. İlk səhifə
        if self.page > 2:
            row.append(InlineKeyboardButton(text=first_label, callback_data=make_cb(1)))

        # 2. Əvvəlki səhifə
        if self.has_prev:
            row.append(InlineKeyboardButton(text=prev_label, callback_data=make_cb(self.page - 1)))

        # 3. İndikator (cari / cəmi)
        indicator_text = f"📄 {self.page}/{self.total_pages}"
        row.append(InlineKeyboardButton(text=indicator_text, callback_data=f"{noop_prefix}:{self.page}"))

        # 4. Növbəti səhifə
        if self.has_next:
            row.append(InlineKeyboardButton(text=next_label, callback_data=make_cb(self.page + 1)))

        # 5. Sonuncu səhifə
        if self.page < self.total_pages - 1:
            row.append(InlineKeyboardButton(text=last_label, callback_data=make_cb(self.total_pages)))

        return row


def paginate_list(
    items: Sequence[Any],
    page: int,
    per_page: int = 8,
    prefix: str = "pg",
    extra: str = "",
) -> tuple[list[Any], list[InlineKeyboardButton], int, int]:
    """Siyahını kəsir və səhifələmə sətrini qaytarır.
    
    Qaytarır: (page_items, pagination_buttons, current_page, total_pages)
    """
    p = Pagination(len(items), page=page, per_page=per_page)
    return p.slice(items), p.keyboard_row(prefix=prefix, extra=extra), p.page, p.total_pages


__all__ = ["Pagination", "paginate_list"]
