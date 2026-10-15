"""
bot.menus.pages.groups
Re-export from bot.menus.views.groups for backwards compatibility.
"""
from bot.menus.views.groups import user_groups_view as groups_view

__all__ = ["groups_view"]
