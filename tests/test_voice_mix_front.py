"""🔀 Mixplay açıqkən istifadəçinin /play mahnısı növbənin sonuna yox, Mix mahnılarının önünə düşür."""

import bot.menus  # noqa: F401
from bot.voice import engine as E
from bot.voice.models import ChatState, Track


def make_assistant():
    va = object.__new__(E.Assistant)
    va.chats = {}
    va.prefetch = lambda t: None
    va.save_active_sessions = lambda now=False: None
    va.state = lambda cid, title="": va.chats.setdefault(cid, ChatState(cid, title))
    va.ensure_depo = lambda t: None
    return va


def titles(st):
    return [t.title for t in st.queue]


async def test_user_track_goes_before_mix_tracks_and_keeps_user_order():
    va = make_assistant()
    st = va.chats[1] = ChatState(1, "Q")
    st.current = Track("cari", file_id="f0")
    st.radio = {"seed": "x", "seen": set()}
    st.queue.extend([Track("mix1", file_id="a", src="mix"), Track("mix2", file_id="b", src="mix")])
    assert await va.enqueue(1, "Q", Track("u1", file_id="c"), front=True) == ("queued", 1)
    assert await va.enqueue(1, "Q", Track("u2", file_id="d"), front=True) == ("queued", 2)   # u1-dən sonra, Mix-dən əvvəl
    assert titles(st) == ["u1", "u2", "mix1", "mix2"]


async def test_without_front_flag_track_is_appended_as_before():
    va = make_assistant()
    st = va.chats[1] = ChatState(1, "Q")
    st.current = Track("cari", file_id="f0")
    st.queue.extend([Track("n1", file_id="a")])
    assert await va.enqueue(1, "Q", Track("n2", file_id="b")) == ("queued", 2)
    assert titles(st) == ["n1", "n2"]
    assert await va.enqueue(1, "Q", Track("n3", file_id="c"), front=True) == ("queued", 3)   # Mix tracki yoxdur: sonda qalır
    assert titles(st) == ["n1", "n2", "n3"]
