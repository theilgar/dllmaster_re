"""
bot.music
Musiqi alt-sistemləri: Toplu yükləmə növbəsi və İnline axtarış/çatdırılma mühərriki.
"""

from bot.music.batch import register_batch_system
from bot.music.inline import register_inline_system

__all__ = ["register_batch_system", "register_inline_system"]
