"""
bot.menus.state
Mərkəzi FSM vəziyyətləri, mətn gözləmə reyestrləri və panel redaktə köməkçiləri.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

# Qlobal Bot Context
_CTX: Any = None


def set_context(ctx: Any):
    """Qlobal bot context-ini təyin edir."""
    global _CTX
    _CTX = ctx


def get_context() -> Any:
    """Cari bot context-ini qaytarır."""
    return _CTX


def is_creator(user_id: int) -> bool:
    """İstifadəçinin bot sahibi olub-olmadığını yoxlayır."""
    from common.permissions import is_creator as _is_creator
    return _is_creator(user_id, _CTX)


# ───────────── Mərkəzi FSM Vəziyyət Reyestrləri ─────────────
bc: dict[str, Any] = {"stage": None}
prm: dict[str, Any] = {"stage": None}
tp: dict[str, Any] = {"stage": None}        # ☕ bəxşiş mesajı: istifadəçi gözləmə
mt: dict[str, Any] = {"stage": None}        # 🛠 texniki iş rejimi: mətn gözləmə
ad: dict[str, Any] = {"stage": None}        # 📣 qrup reklamı: mətn gözləmə
wm: dict[str, Any] = {"stage": None}
tx: dict[str, Any] = {"stage": None}
stp: dict[str, Any] = {"stage": None}
us: dict[str, Any] = {"stage": None}
sp_input: dict[str, Any] = {"stage": None, "chat_id": None, "panel_id": None}
ab_inp: dict[str, Any] = {"stage": None, "chat_id": None, "panel_id": None}
yt_inp: dict[str, Any] = {"stage": None, "chat_id": None, "panel_id": None}
perf_inp: dict[str, Any] = {"stage": None, "key": None, "chat_id": None, "panel_id": None}
rec_inp: dict[str, Any] = {"stage": None, "chat_id": None, "panel_id": None}
REC_INP = rec_inp  # Geriyə uyğunluq üçün alias
yta_inp: dict[str, Any] = {"stage": None, "acc": None, "chat_id": None, "panel_id": None}
upd_inp: dict[str, Any] = {"stage": None, "chat_id": None, "panel_id": None, "back": "main"}
grp_inp: dict[int, Any] = {}

# Mətn gözləmələri zənciri: (vəziyyət lüğəti, icazə verilən mərhələlər)
TEXT_WAITS: tuple[tuple[dict[str, Any], set[str] | None], ...] = (
    (bc, {"await_text"}),
    (prm, {"await_user"}),
    (tp, {"await_user", "await_group"}),
    (mt, {"await_text"}),
    (ad, {"await_text"}),
    (wm, {"await_media"}),
    (tx, {"await_text"}),
    (us, {"await_query"}),
    (stp, {"await_price"}),
    (sp_input, {"await_id", "await_sec", "await_oauth"}),
    (ab_inp, {"await_sec"}),
    (yt_inp, {"await_bp"}),
    (perf_inp, None),
    (rec_inp, None),
    (yta_inp, {"await_name"}),
    (upd_inp, {"await_sudo"}),
)


def menu_waiting_text(user_id: int) -> bool:
    """Yoxlayır: Hazırda bot bu istifadəçidən parametr və ya mətn gözləyirmi?
    
    Aktivdirsə, istifadəçinin yazdığı mətn musiqi axtarışı kimi qəbul edilmir.
    """
    if user_id in grp_inp:
        return True
    if not is_creator(user_id):
        return False
    return any(st.get("stage") and (stages is None or st.get("stage") in stages) for st, stages in TEXT_WAITS)


def bc_reset():
    """Broadcast vəziyyətini sıfırlayır."""
    bc.clear()
    bc["stage"] = None


def reset_inputs():
    """Bütün aktiv parametr gözləmə mərhələlərini sıfırlayır."""
    for st, _ in TEXT_WAITS:
        if st is not bc:
            st["stage"] = None
    grp_inp.clear()


async def delete_preview(context: Any = None):
    """Broadcast önbaxış mesajını silir."""
    ctx = context or _CTX
    if ctx and bc.get("preview_id") and bc.get("chat_id"):
        try:
            await ctx.bot.delete_message(bc["chat_id"], bc["preview_id"])
        except Exception as _e:
            logger.debug("Broadcast önbaxış mesajı silinərkən xəta: %r", _e)
        bc["preview_id"] = None


async def answer_and_edit(cb: CallbackQuery, text: str, kb=None):
    """cb.answer() və mesajı yeniləməni paralel göndərir (iki Telegram sorğusu ardıcıl yox, eyni anda).
    Qeyd: cb.answer() coroutine yox, aiogram metod obyektidir — gather-ə birbaşa verilmir (unhashable)."""
    async def _answer():
        await cb.answer()

    await asyncio.gather(_answer(), safe_edit_cb(cb, text, kb))


async def safe_edit_cb(cb: CallbackQuery, text: str, kb=None):
    """Menyu mesajını təhlükəsiz yeniləyir (həm normal, həm də inline mesajları dəstəkləyir)."""
    from bot.core.panel import show
    await show(cb, text, kb)


def panel_editor(context: Any, state: dict, label: str = "") -> Callable:
    """Mətn gözləyən panel mesajını yeniləyən köməkçi funksiya qaytarır."""
    ctx = context or _CTX

    async def _edit(text: str, kb=None):
        try:
            await ctx.bot.edit_message_text(
                chat_id=state["chat_id"],
                message_id=state["panel_id"],
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning("Panel mesajı (%s) yenilənmədi: %s", label, e)

    return _edit


__all__ = [
    "REC_INP",
    "ad",
    "TEXT_WAITS",
    "ab_inp",
    "bc",
    "bc_reset",
    "delete_preview",
    "get_context",
    "grp_inp",
    "is_creator",
    "menu_waiting_text",
    "mt",
    "panel_editor",
    "perf_inp",
    "prm",
    "rec_inp",
    "reset_inputs",
    "safe_edit_cb",
    "set_context",
    "sp_input",
    "stp",
    "tp",
    "tx",
    "upd_inp",
    "us",
    "wm",
    "yt_inp",
    "yta_inp",
]
