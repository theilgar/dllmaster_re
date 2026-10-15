"""
bot.menus.handlers.remote_flow
🌐 Uzaq server paneli: düymələrin icrası, mətn daxiletməsi (ünvan / port / açar), test və quraşdırma.
Görünüşlər — bot/menus/views/remote_server.py, məntiq — bot/core/remote.py.
"""
import contextlib
import logging
from html import escape

from aiogram.types import CallbackQuery, Message

from bot.core import remote
from bot.menus.state import get_context, is_creator, perf_inp, safe_edit_cb
from bot.menus.views import remote_server as V
from common.utilities import spawn

logger = logging.getLogger(__name__)

_busy = {"test": False, "setup": False}               # uzun əməliyyat bir dəfəyə bir


async def _mcb_remote(cb: CallbackQuery, parts: list, action: str = "rmt"):
    """menu:rmt[:on | dl[:rejim] | up[:rejim] | auto | slots[:d+|d-|u+|u-|rec] | conn | edit:host|port|key
               | test | setup | acc[:hesab] | fix:retry|home|auto]"""
    if not is_creator(cb.from_user.id):
        await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
        return
    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""

    if sub == "edit" and arg in V._PROMPTS:               # mətn gözləyən ekran: input_router → on_text()
        perf_inp.update(stage="await_val", key=f"rmt_{arg}", chat_id=cb.message.chat.id, panel_id=cb.message.message_id)
        await cb.answer()
        await safe_edit_cb(cb, *V.remote_input_view(arg))
        return
    perf_inp["stage"] = None                              # başqa səhifəyə keçəndə köhnə mətn gözləməsi qalmasın

    view = None
    if sub == "on":
        if not remote.enabled() and not remote.configured():
            await cb.answer("Əvvəl 🔌 Bağlantı bölməsində server ünvanını daxil edin", show_alert=True)
            return
        on = remote.set_enabled(not remote.enabled())
        await cb.answer("🌐 Uzaq server açıldı" if on else "⚪ Söndürüldü — hər şey evdə")
        view = V.remote_view()

    elif sub in ("dl", "up"):
        kind = "download" if sub == "dl" else "upload"
        title = "Yükləmə" if kind == "download" else "Upload"
        note = ""
        if arg in remote.MODES:
            remote.set_mode(kind, arg)
            note = f"✓ {title}: {remote.MODE_LABEL[arg]}"
            await cb.answer(note)
        else:
            await cb.answer()
        view = V.remote_mode_view(kind, note)

    elif sub == "auto":
        on = remote.set_auto_fallback(not remote.auto_fallback())
        if on:
            remote.resolve_gate("home")                   # gözləyən işlər dərhal evdə işləsin
        await cb.answer("🤖 Avto: server xətasında iş dərhal evdə görülür" if on
                        else "✋ Manual: server xətasında iş gözləyir, sizdən soruşulur")
        view = V.remote_view()

    elif sub == "slots":
        if arg in ("d+", "d-", "u+", "u-"):
            lane = "depo" if arg[0] == "d" else "user"
            cur = remote.depo_slots() if lane == "depo" else remote.user_slots()
            n = remote.set_slots(lane, cur + (1 if arg[1] == "+" else -1))
            await cb.answer(f"{'📦 Depo' if lane == 'depo' else '👤 İstifadəçi'}: {n}")
        elif arg == "rec" and remote.slot_advice()["rec_depo"]:
            await cb.answer(f"📦 Depo: {remote.set_slots('depo', remote.slot_advice()['rec_depo'])}")
        elif arg in ("di", "ui"):
            await cb.answer("Depo: depo doldurucunun eyni anda yükləmə və upload sayı. "
                            if arg == "di" else
                            "İstifadəçi: istifadəçilərin mahnı yükləmələri üçün ayrılmış slotlar, depo bunları tutmur.",
                            show_alert=True)
            return
        else:
            await cb.answer()
        view = V.remote_slots_view()

    elif sub == "conn":
        await cb.answer()
        view = V.remote_conn_view()

    elif sub == "test":
        if not remote.configured():
            await cb.answer("Əvvəl server ünvanını daxil edin", show_alert=True)
            return
        if _busy["test"]:
            await cb.answer("Yoxlama gedir...")
            return
        _busy["test"] = True
        try:
            await cb.answer("🧪 Yoxlanılır...")
            await safe_edit_cb(cb, *V.remote_conn_view("⏳ <b>Yoxlanılır...</b>"))
            await remote.check()
        finally:
            _busy["test"] = False
        view = V.remote_conn_view()

    elif sub == "setup":
        if not remote.configured():
            await cb.answer("Əvvəl server ünvanını daxil edin", show_alert=True)
            return
        if _busy["setup"]:
            await cb.answer("Quraşdırma artıq gedir...", show_alert=True)
            return
        _busy["setup"] = True
        try:
            await cb.answer("🛠 Qurulur, 1–3 dəqiqə çəkir...")
            await safe_edit_cb(cb, *V.remote_conn_view("⏳ <b>Server qurulur</b>: yt-dlp və aiogram yüklənir, gözləyin..."))
            try:
                note = escape("\n".join(await remote.setup()))
            except remote.RemoteUnavailable as e:
                note = f"❌ {escape(str(e))}"
        finally:
            _busy["setup"] = False
        view = V.remote_conn_view(note)

    elif sub == "acc":
        note = ""
        if arg:
            from bot.core import yt_accounts
            acc = yt_accounts.get(arg)
            if acc:
                on = remote.toggle_acc(acc["id"])
                name = escape(acc["name"])
                note = f"✅ <b>{name}</b> serverdə işləyəcək" if on else f"↩️ <b>{name}</b> yalnız evdə işləyəcək"
        await cb.answer()
        view = V.remote_accounts_view(note)

    elif sub == "fix":
        if arg not in ("retry", "home", "auto"):
            await cb.answer()
            return
        await cb.answer("⏳ ...")
        view = V.remote_view(await remote.apply_fix(arg))

    else:                                                 # hub
        await cb.answer()
        if remote.enabled() and remote.configured() and remote.check_is_stale():
            spawn(remote.check())                         # arxa planda: növbəti 🔄 Yenilə yeni vəziyyəti göstərər
        view = V.remote_view()

    await safe_edit_cb(cb, *view)


async def on_text(message: Message, what: str):
    """Ünvan / port / açar mətni (input_router perf_inp['key'] == 'rmt_<what>' olanda çağırır)."""
    ctx = get_context()
    raw = (message.text or "").strip()
    with contextlib.suppress(Exception):
        await message.delete()
    panel_args = dict(chat_id=perf_inp["chat_id"], message_id=perf_inp["panel_id"], parse_mode="HTML")
    try:
        remote.set_conn(what, "" if raw == "-" else raw)
    except ValueError as e:                               # səhv dəyər: ekran açıq qalır, yenidən yaza bilsin
        text, kb = V.remote_input_view(what, f"⚠️ {escape(str(e))}")
    else:
        perf_inp["stage"] = None
        text, kb = V.remote_conn_view("✅ Yadda saxlanıldı. İndi 🧪 Test edin.")
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
