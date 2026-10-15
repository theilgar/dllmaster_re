"""
bot.menus.pages.settings
Re-export from bot.menus.views.system_hub for backwards compatibility.
"""
from bot.menus.views.system_hub import sys_text, sys_kb


def settings_view(ctx=None, note: str = ""):
    text = sys_text()
    if note:
        text += f"\n\nℹ️ {note}"
    return text, sys_kb()


__all__ = ["settings_view"]
