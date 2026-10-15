"""cb.answer() aiogram-da coroutine yox, metod obyektidir (unhashable) — asyncio.gather-ə birbaşa verilmir."""
import asyncio

from aiogram.methods import AnswerCallbackQuery

from bot.menus import state


class FakeCb:
    def __init__(self):
        self.answered = False

    def answer(self, *a, **k):
        cb = self

        class _M(AnswerCallbackQuery):         # həqiqi aiogram metod obyekti kimi: unhashable + awaitable
            def __await__(inner):
                async def go():
                    cb.answered = True
                return go().__await__()

        return _M(callback_query_id="1")


def test_answer_and_edit_runs_both(monkeypatch):
    edited = []

    async def fake_edit(cb, text, kb=None):
        edited.append((text, kb))

    monkeypatch.setattr(state, "safe_edit_cb", fake_edit)
    cb = FakeCb()
    asyncio.run(state.answer_and_edit(cb, "t", "k"))
    assert cb.answered and edited == [("t", "k")]


def test_gather_directly_would_fail():
    import pytest
    cb = FakeCb()

    async def go():
        await asyncio.gather(cb.answer(), asyncio.sleep(0))

    with pytest.raises(TypeError):
        asyncio.run(go())
