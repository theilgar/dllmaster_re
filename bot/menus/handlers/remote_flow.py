"""
bot.menus.handlers.remote_flow
🌐 Uzaq serverlər paneli: düymələrin icrası, mətn daxiletməsi (ünvan / port / açar / ad), test və quraşdırma.
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

_busy = {"test": set(), "setup": set()}               # uzun əməliyyat bir serverdə bir dəfəyə bir


async def _mcb_remote(cb: CallbackQuery, parts: list, action: str = "rmt"):
    """menu:rmt[:on | dl[:rejim] | up[:rejim] | auto | srvs | add | acc[:hesab] | fix:retry|home|auto
               | s:<id>[:on | ff | slots[:d+|d-|u+|u-|rec] | edit:host|port|key|name | test | setup | del | delok]]"""
    if not is_creator(cb.from_user.id):
        await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
        return
    sub = parts[2] if len(parts) > 2 else ""
    arg = parts[3] if len(parts) > 3 else ""

    if sub in ("conn", "slots", "edit", "test", "setup"):  # köhnə tək-server düymələri → ilk server səhifəsi
        first = remote.servers()[:1]
        if not first:
            sub, parts = "srvs", ["menu", "rmt", "srvs"]
        else:
            sub, arg, parts = "s", first[0].id, ["menu", "rmt", "s", first[0].id, *([] if sub == "conn" else parts[2:])]
    if sub == "s":
        await _server_page(cb, arg, parts[4] if len(parts) > 4 else "", parts[5] if len(parts) > 5 else "")
        return
    if sub == "acc" and arg == "ff":                       # köhnə ümumi 🦊 açarı
        arg = ""
    perf_inp["stage"] = None                              # başqa səhifəyə keçəndə köhnə mətn gözləməsi qalmasın

    view = None
    if sub == "on":
        if not remote.enabled() and not remote.configured():
            await cb.answer("Əvvəl 🖥 Serverlər bölməsində server ünvanını daxil edin", show_alert=True)
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

    elif sub == "srvs":
        await cb.answer()
        view = V.remote_servers_view()

    elif sub == "add":
        try:
            srv = remote.add_server()
        except ValueError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await cb.answer("➕ Server əlavə olundu. Ünvanını yazın")
        view = V.remote_server_view(srv.id, "Əvvəl ✏️ Ünvanı yazın.")

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
            spawn(remote.check_all())                     # arxa planda: növbəti 🔄 Yenilə yeni vəziyyəti göstərər
        view = V.remote_view()

    await safe_edit_cb(cb, *view)


async def _server_page(cb: CallbackQuery, sid: str, sub: str, arg: str):
    srv = remote.get_server(sid)
    if srv is None:
        await cb.answer("Server tapılmadı", show_alert=True)
        await safe_edit_cb(cb, *V.remote_servers_view("⚠️ Server tapılmadı"))
        return
    if sub == "edit" and arg in V._PROMPTS:               # mətn gözləyən ekran: input_router → on_text()
        perf_inp.update(stage="await_val", key=f"rmt_{arg}", rmt_sid=sid, chat_id=cb.message.chat.id,
                        panel_id=cb.message.message_id)
        await cb.answer()
        await safe_edit_cb(cb, *V.remote_input_view(sid, arg))
        return
    perf_inp["stage"] = None
    view = None
    if sub == "on":
        on = remote.set_server_on(sid, not srv.on)
        await cb.answer("Server açıldı" if on else "Server söndürüldü, iş başqa serverlərə gedir")
        view = V.remote_server_view(sid)
    elif sub == "ff":
        on = remote.set_server_firefox(sid, not srv.firefox)
        await cb.answer()
        view = V.remote_server_view(sid, "🦊 Serverin Firefox-u açıldı: cookies serverdə oxunur." if on
                                    else "🏠 Serverin Firefox-u söndürüldü: evdəki hesablar istifadə olunur.")
    elif sub == "slots":
        note = ""
        if arg in ("d+", "d-", "u+", "u-"):
            lane = "depo" if arg[0] == "d" else "user"
            cur = srv.depo_slots if lane == "depo" else srv.user_slots
            n = remote.set_slots(sid, lane, cur + (1 if arg[1] == "+" else -1))
            await cb.answer(f"{'📦 Depo' if lane == 'depo' else '👤 İstifadəçi'}: {n}")
        elif arg == "rec" and srv.slot_advice()["rec_depo"]:
            await cb.answer(f"📦 Depo: {remote.set_slots(sid, 'depo', srv.slot_advice()['rec_depo'])}")
        elif arg in ("di", "ui"):
            await cb.answer("Depo: depo doldurucunun eyni anda yükləmə və upload sayı. "
                            if arg == "di" else
                            "İstifadəçi: istifadəçilərin mahnı yükləmələri üçün ayrılmış slotlar, depo bunları tutmur.",
                            show_alert=True)
            return
        else:
            await cb.answer()
        view = V.remote_slots_view(sid, note)
    elif sub in ("test", "setup"):
        if not srv.configured():
            await cb.answer("Əvvəl server ünvanını daxil edin", show_alert=True)
            return
        if sid in _busy[sub]:
            await cb.answer("Əməliyyat artıq gedir...", show_alert=True)
            return
        _busy[sub].add(sid)
        try:
            if sub == "test":
                await cb.answer("🧪 Yoxlanılır...")
                await safe_edit_cb(cb, *V.remote_server_view(sid, "⏳ <b>Yoxlanılır...</b>"))
                await srv.check(remote.mode("upload") != "home")
                note = ""
            else:
                await cb.answer("🛠 Qurulur, 1–3 dəqiqə çəkir...")
                await safe_edit_cb(cb, *V.remote_server_view(sid, "⏳ <b>Server qurulur</b>: yt-dlp və aiogram yüklənir, gözləyin..."))
                try:
                    note = escape("\n".join(await srv.setup(remote.mode("upload") != "home")))
                except remote.RemoteUnavailable as e:
                    note = f"❌ {escape(str(e))}"
        finally:
            _busy[sub].discard(sid)
        view = V.remote_server_view(sid, note)
    elif sub == "del":
        await cb.answer()
        view = V.remote_delete_view(sid)
    elif sub == "delok":
        await remote.remove_server(sid)
        await cb.answer("🗑 Server silindi")
        view = V.remote_servers_view("🗑 Server silindi")
    else:
        await cb.answer()
        view = V.remote_server_view(sid)
    await safe_edit_cb(cb, *view)


async def on_text(message: Message, what: str):
    """Ünvan / port / açar / ad mətni (input_router perf_inp['key'] == 'rmt_<what>' olanda çağırır)."""
    ctx = get_context()
    raw = (message.text or "").strip()
    sid = perf_inp.get("rmt_sid")
    with contextlib.suppress(Exception):
        await message.delete()
    panel_args = dict(chat_id=perf_inp["chat_id"], message_id=perf_inp["panel_id"], parse_mode="HTML")
    try:
        remote.set_conn(sid, what, "" if raw == "-" and what != "name" else raw)
    except ValueError as e:                               # səhv dəyər: ekran açıq qalır, yenidən yaza bilsin
        text, kb = V.remote_input_view(sid, what, f"⚠️ {escape(str(e))}")
    else:
        perf_inp["stage"] = None
        text, kb = V.remote_server_view(sid, "✅ Yadda saxlanıldı. İndi 🧪 Test edin." if what != "name" else "✅ Yadda saxlanıldı.")
    with contextlib.suppress(Exception):
        await ctx.bot.edit_message_text(text=text, reply_markup=kb, **panel_args)
