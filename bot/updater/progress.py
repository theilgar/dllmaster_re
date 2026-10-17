"""
📊 GitHub-a göndərmə irəliləyişi — «⏳ GitHub-a göndərilir» mesajı üçün proqres bar.

Bar /menu → 🖥 Sistem → 📋 Görünüş ilə ORTAQDIR: bar üslubu (ui_bar: xətti / blok / bütöv — common.menu._bar_box) və mətn bloku
üslubu (ui_style: kod bloku / script / sitat — common.menu.wrap_ui_text) oradakı ayardan oxunur.
  • panel_text(...)   — tam panelin mətni (bar + mərhələ + fayl sayı kod blokunda);
  • button_text(...)  — hesabatın altındakı status düyməsi (bir sətir);
  • run_with_progress — push-u thread-də işlədir, irəliləyişi 1.5 san.-dən bir `render(hissə, mərhələ)` ilə göstərir.
"""
import asyncio
import logging
from html import escape

from common.menu import _bar_box, wrap_ui_text

logger = logging.getLogger(__name__)

BAR_WIDTH = 10
MIN_GAP = 1.5          # san. — Telegram redaktə limitindən qorunma


def percent(frac: float) -> int:
    return int(round(max(0.0, min(1.0, float(frac or 0))) * 100))


def bar_line(frac: float, width: int = BAR_WIDTH) -> str:
    """`[▰▰▰▰▱▱▱▱▱▱] 40%` — bar üslubu botun görünüş ayarından."""
    return f"{_bar_box(frac, width)} {percent(frac)}%"


def panel_text(frac: float, label: str, files: int = 0) -> str:
    body = [bar_line(frac), f"Mərhələ: {label}"] + ([f"Fayl: {files}"] if files else [])
    return "⏳ <b>GitHub-a göndərilir</b>\n" + wrap_ui_text(escape("\n".join(body)))


def button_text(frac: float, label: str) -> str:
    return f"⏳ {bar_line(frac, 8)} · {label}"[:60]


async def run_with_progress(render, func, *args, interval: float = MIN_GAP, **kwargs):
    """func(*args, progress=cb, **kwargs)-i thread-də işlədir; hər dəyişiklikdə (interval-dan tez olmayaraq) render(hissə, mərhələ)."""
    state = {"frac": 0.0, "label": "Hazırlanır"}

    def cb(frac, label):
        state["frac"], state["label"] = frac, label

    async def show(frac, label):
        try:
            await render(frac, label)
        except Exception as e:
            logger.debug(f"push irəliləyişi göstərilmədi: {e}")

    async def ticker():
        last = (0.0, "Hazırlanır")
        while True:
            cur = (round(state["frac"], 2), state["label"])
            if cur != last:
                last = cur
                await show(*cur)
            await asyncio.sleep(interval)

    await show(0.0, "Hazırlanır")                   # bar iş başlamamışdan görünsün
    task = asyncio.create_task(ticker())
    try:
        return await asyncio.to_thread(func, *args, progress=cb, **kwargs)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
