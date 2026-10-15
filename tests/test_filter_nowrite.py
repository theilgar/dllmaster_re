"""Userbot filter: yaza bilmədiyi çatları susdurur."""
import time

import pytest

import userbot.plugins.filter as F


@pytest.fixture(autouse=True)
def _clean():
    F._NOWRITE.clear()
    yield
    F._NOWRITE.clear()


@pytest.mark.parametrize("msg", [
    "You can't write in this chat (caused by SendMessageRequest)",
    "ChatWriteForbiddenError",
    "CHAT_SEND_PLAIN_FORBIDDEN",
])
def test_write_forbidden_mutes_chat(msg):
    assert F._mark_nowrite(-10, Exception(msg)) is True
    assert F._NOWRITE[-10] > time.time() + F.NOWRITE_TTL - 5


def test_other_errors_are_not_muted():
    assert F._mark_nowrite(-11, Exception("FloodWait 5")) is False
    assert -11 not in F._NOWRITE
