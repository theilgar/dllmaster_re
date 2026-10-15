"""⌨️ Panel daxiletmə gözləməsi — bot sahibdən mətn / fayl gözləyəndə (kanal ID-si, interval, fayl ...).

Əvvəl hər plugin eyni kodu ayrıca yazırdı: {"await", "since", "panel"} lüğəti, 10 dəqiqəlik TTL ilə
waiting(), panel mesajını redaktə edən to_panel() və context.menu_waiting_text zəncirinə əl ilə qoşulma.

    state = InputWait(WAIT_TTL, pending=None)         # əlavə sahələr də olur
    state.start("chat") / state.clear()
    state.waiting() / state.waiting("chat") / state.waiting("val", prefix=True)
    await state.to_panel(bot, message, text, kb)
    @dp.message(F.chat.type == "private", state.for_creator(is_creator))   # mətn handler-i
    chain_menu_text(context, lambda uid: state.waiting() and is_creator(uid))

InputWait dict-dir — state["await"] / state["panel"] birbaşa da oxunur.
"""
import time


class InputWait(dict):
    def __init__(self, ttl: float = 600, **extra):
        super().__init__({"await": None, "since": 0, "panel": None, **extra})
        self.ttl = ttl

    def start(self, kind: str):
        self.update({"await": kind, "since": time.time()})

    def clear(self):
        self["await"] = None

    def waiting(self, kind: str = None, prefix: bool = False) -> bool:
        """Gözləmə aktivdir? Vaxtı (ttl) keçibsə — sıfırlanır. kind — yalnız bu növ; prefix — "val:..." kimi."""
        cur = self["await"]
        if not cur or time.time() - self["since"] > self.ttl:
            self["await"] = None
            return False
        if kind is None:
            return True
        return cur == kind or (prefix and str(cur).startswith(kind))

    def for_creator(self, is_creator):
        """aiogram filtri: mesaj sahibdəndir və bu gözləmə aktivdir."""
        async def flt(message) -> bool:
            return bool(message.from_user) and is_creator(message.from_user.id) and self.waiting()
        return flt

    async def show(self, cb, text: str, kb):
        """Callback mesajını yenilə və onu panel kimi yadda saxla (sonrakı mətn cavabları ora yazılsın)."""
        import contextlib
        with contextlib.suppress(Exception):
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            self["panel"] = (cb.message.chat.id, cb.message.message_id)

    async def to_panel(self, bot, message, text: str, kb):
        """Cavabı əvvəlki panel mesajında göstər; alınmasa yeni mesaj göndər və onu panel kimi yadda saxla."""
        if self["panel"]:
            try:
                await bot.edit_message_text(text, chat_id=self["panel"][0], message_id=self["panel"][1],
                                            parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
                return
            except Exception:
                pass
        sent = await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        self["panel"] = (sent.chat.id, sent.message_id)


def chain_menu_text(context, predicate):
    """music_plugin gözlənilən mətni (kanal adı, rəqəm ...) mahnı axtarışı saymasın:
    context.menu_waiting_text zəncirinə bu pluginin yoxlamasını əlavə et."""
    prev = getattr(context, "menu_waiting_text", None)
    context.menu_waiting_text = lambda uid: bool(predicate(uid)) or bool(prev and prev(uid))


def waiting_any(context, uid) -> bool:
    """Bot həmin istifadəçidən mətn gözləyirmi (parol, broadcast, kanal adı, mahnı adı redaktəsi ...)?
    Belə mətn nə mahnı axtarışına, nə də userbot .komandası kimi udulmamalıdır.
    Plugin-lər öz yoxlamasını chain_menu_text ilə qoşur; ayrıca adla verilənlər də burada yoxlanılır."""
    for name in ("menu_waiting_text", "va_waiting", "depo_waiting"):
        fn = getattr(context, name, None)
        try:
            if fn and fn(uid):
                return True
        except Exception:
            pass
    if uid in (getattr(context, "tip_waiting", None) or ()):
        return True
    s = (getattr(context, "audio_edit_sessions", None) or {}).get(uid)
    return bool(s and s.get("awaiting") in ("title", "artist"))
