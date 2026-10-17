"""Userbot plugin testləri üçün saxta `ub` və hadisə: komandalar pattern ilə çağırılır, çıxış edits siyahısındadır."""
import re
import types
from datetime import datetime, timezone

T0 = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)      # sabit vaxt (şəkil / istək tarixləri üçün)


class FakeUB:
    """register(ub) üçün: komandaları və xam handler-ləri tutur."""

    def __init__(self):
        self.handlers, self.raw_handlers, self.tracked, self.state = {}, [], [], {}

    def command(self, name, pattern=None, help=None, **kw):
        def deco(fn):
            self.handlers[name] = (pattern or rf"^\.{name}$", fn)
            return fn
        return deco

    def raw(self, event_builder):
        def deco(fn):
            self.raw_handlers.append((event_builder, fn))
            return fn
        return deco

    def section(self, key, label, order=50):
        def deco(render):
            self.__dict__.setdefault("sections", {}).setdefault(key, {}).update(label=label, order=order, render=render)
            return render
        return deco

    def on_section(self, key):
        def deco(handle):
            self.__dict__.setdefault("sections", {}).setdefault(key, {})["handle"] = handle
            return handle
        return deco

    def track(self, chat_id, kind, task):
        self.tracked.append(task)

    async def settle(self):
        for t in self.tracked:
            await t
        self.tracked.clear()


class FakeEvent:
    def __init__(self, client, chat=None, *, private=False, reply=None, sender=None, chat_id=77, msg_id=1, out=False):
        self.client, self._chat, self._reply, self._sender = client, chat, reply, sender
        self.chat_id, self.is_private, self.id, self.out = chat_id, private, msg_id, out
        self.is_reply = reply is not None
        self.reply_to_msg_id = getattr(reply, "id", None)
        self.sender_id = getattr(sender, "id", None)
        self.pattern_match = None
        self.edits, self.deleted = [], False

    async def get_chat(self):
        return self._chat

    async def get_reply_message(self):
        return self._reply

    async def get_sender(self):
        return self._sender

    async def edit(self, text, **kw):
        self.edits.append(text)

    async def delete(self):
        self.deleted = True


async def run_command(ub, name, text, client, **event_kw):
    """`text` komandanın pattern-ə uyğun gəlməsini yoxlayır, handler-i çağırır, izlənən tapşırıqları gözləyir."""
    pattern, fn = ub.handlers[name]
    ev = FakeEvent(client, **event_kw)
    ev.pattern_match = re.match(pattern, text)
    assert ev.pattern_match, f"pattern uyğun gəlmədi: {text!r}"
    await fn(ev)
    await ub.settle()
    return ev


def msg(**kw):
    """Saxta mesaj (reply üçün): photo / video / gif / document, download_media()."""
    kw.setdefault("photo", None)
    kw.setdefault("video", None)
    kw.setdefault("gif", None)
    kw.setdefault("document", None)

    async def download_media(file=None):
        return kw.pop("data", b"BYTES")

    return types.SimpleNamespace(download_media=download_media, **kw)
