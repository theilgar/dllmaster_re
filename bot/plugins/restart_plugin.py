"""
♻️ Botu restart — bot/plugins/restart_plugin.py

Düymə: callback_data="botrestart:ask" (təsdiq soruşur) → "botrestart:ok" (restart).
Proses öz yerində yenidən başlayır (os.execv: eyni PID, eyni argv/cwd) — systemd/screen/tmux altında da işləyir.
Restartdan sonra həmin mesaj "✅ Bot yenidən başladı" ilə yenilənir.
Əgər aktiv yükləmə və ya canlı yayımda musiqi oxudulursa, bütün proseslər tamamlandıqdan sonra təhlükəsiz restart (graceful restart) edilir.
"""
import asyncio
import contextlib
import json
import logging
import os
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from bot.core import panel as ui
from common.database import is_creator
from common.utilities import spawn, ui_styled

logger = logging.getLogger(__name__)
PRIORITY = 5                     # tez yüklənsin
STATE = os.path.join("data", "restart.json")
_STARTED = time.time()

_RESTART_PENDING = False
_FORCE_RESTART = False
_CANCEL_RESTART = False
_GRACEFUL_TASK = None


def is_restart_pending() -> bool:
    """Yükləmələr və ya canlı yayım bitənə qədər restart gözləmədədirmi?"""
    return bool(_RESTART_PENDING)


def get_active_operations(context) -> dict:
    """
    Restartdan əvvəl davam edən aktiv istifadəçi proseslərini toplayır:
    - yükləmələr (music_plugin, inline, batch, cpu_pool)
    - canlı yayım mahnıları (voice_assistant aktiv çatları)
    """
    downloads = []
    total_dl = 0

    # 1. music_plugin
    dl_jobs = getattr(context, "download_jobs", {})
    if dl_jobs:
        count = len(dl_jobs)
        total_dl += count
        downloads.append(f"Mahnı yükləməsi ({count})")

    # 2. inline yükləmələr
    try:
        from bot.music import inline
        in_jobs = getattr(inline, "download_jobs", {})
        if in_jobs:
            count = len(in_jobs)
            total_dl += count
            downloads.append(f"Inline yükləmə ({count})")
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/restart_plugin.py:64]: %r", _e)

    # 3. batch (albom / playlist / çoxlu yükləmə)
    try:
        from bot.music import batch
        b_jobs = getattr(batch, "batch_jobs", {})
        if b_jobs:
            count = len(b_jobs)
            total_dl += count
            downloads.append(f"Toplu yükləmə ({count})")
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/restart_plugin.py:75]: %r", _e)

    # 4. cpu_pool istifadəçi prosesləri
    try:
        from bot.core import cpu_pool
        user_running = cpu_pool.LANES["user"].running
        if user_running and not total_dl:
            total_dl += user_running
            downloads.append(f"Yükləmə prosesi ({user_running})")
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/restart_plugin.py:85]: %r", _e)

    # 5. Kütləvi yayım (Broadcast)
    try:
        from bot.menus.state import bc
        if bc.get("stage") == "sending":
            total_dl += 1
            downloads.append("Kütləvi yayım (Broadcast)")
    except Exception as _e:
        logger.debug("udulan xəta [bot/plugins/restart_plugin.py:94]: %r", _e)

    # 6. Depo doldurucu (Depo filler)
    filler = getattr(context, "depo_filler", None)
    if filler and getattr(filler, "running", False):
        q_len = len(getattr(filler, "queue", []))
        if q_len > 0:
            total_dl += 1
            downloads.append(f"Depo doldurucu ({q_len} növbədə)")

    # 7. Baza ehtiyat nüsxəsi (Backup)
    if getattr(context, "is_backup_running", lambda: False)():
        total_dl += 1
        downloads.append("Baza ehtiyat nüsxəsi (Backup)")

    # 8. Canlı yayım asistanı
    live_streams = []
    va = getattr(context, "voice_assistant", None)
    if va and getattr(va, "chats", None):
        for cid, st in va.chats.items():
            if st and getattr(st, "current", None) and not getattr(st, "stopped", False):
                t_title = getattr(st.current, "title", "Mahnı")
                q_count = len(getattr(st, "queue", []))
                q_note = f" (+{q_count} növbədə)" if q_count else ""
                s_name = getattr(st, "title", None) or str(cid)
                live_streams.append(f"{s_name}: {t_title}{q_note}")

    # ⏳ Canlı yayım: cari mahnı bitənə qədər gözlənilir (st.current). Növbədəkilər başlamır (bot/voice/engine.py
    # _hold_for_restart) — restartdan sonra avtomatik davam edir, ona görə gözləmə uzanmır.
    is_busy = bool(total_dl or live_streams)
    return {
        "downloads": downloads,
        "download_count": total_dl,
        "streams": live_streams,
        "stream_count": len(live_streams),
        "is_busy": is_busy,
    }


def button(text: str = "♻️ Botu restart et") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data="botrestart:ask")


def is_authorized(context, user_id: int) -> bool:
    """Əmrin yalnız bot sahibi tərəfindən icra edilməsini təmin edir."""
    if not user_id:
        return False
    if is_creator(user_id):
        return True
    cid = getattr(context, "creator_id", None)
    return bool(cid and user_id == cid)


async def _cleanup(context):
    """execv prosesi əvəz edir — finally/atexit işləmir, ona görə əl ilə bağlayırıq."""
    for name in ("voice_assistant",):
        obj = getattr(context, name, None)
        if obj is not None:
            save_fn = getattr(obj, "save_active_sessions", None)
            if callable(save_fn):
                with contextlib.suppress(Exception):
                    try:
                        save_fn(now=True)               # sinxron — execv-dən əvvəl diskə yazılsın
                    except TypeError:
                        save_fn()
            fn = getattr(obj, "stop", None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    try:
                        r = fn(for_restart=True)
                    except TypeError:
                        r = fn()
                    if asyncio.iscoroutine(r):
                        await asyncio.wait_for(r, 5)
                break
    client = getattr(context, "telethon_client", None)
    if client is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(client.disconnect(), 5)
    with contextlib.suppress(Exception):
        from bot.core import cpu_pool
        await asyncio.wait_for(cpu_pool.stop_pools(), 10)
    with contextlib.suppress(Exception):
        await context.bot.session.close()


async def restart(context):
    """Vahid restart: asistan / userbot / işçi proseslər bağlanır, loglar yazılır, proses əvəzlənir.
    BOT_RESTART=exit — prosesdən çıxır (systemd / docker özü qaldırır)."""
    await _cleanup(context)
    logging.shutdown()
    if os.getenv("BOT_RESTART", "exec").lower() == "exit":
        os._exit(int(os.getenv("BOT_RESTART_EXIT_CODE", "1")))
    from common.utilities import restart_argv
    argv = restart_argv()                      # "python init.py bot" / "python -m bot.main" — hər ikisi
    os.execv(argv[0], argv)


async def perform_restart(context, chat_id: int, message_id: int, update_id: int = None, user_id: int = None, source: str = "cmd"):
    """Bütün lazımi məlumatları qeyd edib restart prosesini icra edir."""
    global _RESTART_PENDING
    _RESTART_PENDING = False
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"chat": chat_id, "msg": message_id, "at": time.time(), "source": source}, f)

    if update_id is not None:
        logging.getLogger("aiogram.dispatcher").setLevel(logging.CRITICAL)
        with contextlib.suppress(Exception):
            await context.bot.get_updates(offset=update_id + 1, timeout=0, limit=1)

    logger.warning(f"♻️ Restart — {user_id} tərəfindən (çat: {chat_id}, mənbə: {source})")
    await restart(context)


async def _graceful_restart_loop(context, chat_id: int, message_id: int, update_id: int = None, user_id: int = None, source: str = "cmd"):
    global _RESTART_PENDING, _FORCE_RESTART, _CANCEL_RESTART, _GRACEFUL_TASK
    start_time = time.time()
    last_text = ""
    last_edit_time = 0.0

    # Canlı yayımlarda radio təkrar doldurmanı dayandırırıq ki, sonsuz dövrə düşməsin
    va = getattr(context, "voice_assistant", None)
    if va and getattr(va, "chats", None):
        for st in va.chats.values():
            if hasattr(st, "radio"):
                st.radio = None

    try:
        while True:
            if _CANCEL_RESTART:
                _RESTART_PENDING = False
                _CANCEL_RESTART = False
                if va:
                    with contextlib.suppress(Exception):
                        await va.release_holds()                # ⏳ saxlanmış yayımlar qaldığı yerdən davam etsin
                with contextlib.suppress(Exception):
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="✖️ <b>Gözlənilən restart ləğv edildi.</b>\nProseslər normal qaydada davam edir.",
                        parse_mode="HTML"
                    )
                return

            if _FORCE_RESTART:
                _RESTART_PENDING = False
                _FORCE_RESTART = False
                with contextlib.suppress(Exception):
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="⚡ <b>Dərhal restart edilir...</b>",
                        parse_mode="HTML"
                    )
                await perform_restart(context, chat_id=chat_id, message_id=message_id,
                                      update_id=update_id, user_id=user_id, source=source)
                return

            active = get_active_operations(context)
            if not active["is_busy"]:
                _RESTART_PENDING = False
                logger.info("♻️ Bütün aktiv proseslər tamamlandı — təhlükəsiz restart icra olunur...")
                with contextlib.suppress(Exception):
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="✅ <b>Bütün proseslər tamamlandı!</b>\n♻️ Bot yenidən başladılır...",
                        parse_mode="HTML"
                    )
                await perform_restart(context, chat_id=chat_id, message_id=message_id,
                                      update_id=update_id, user_id=user_id, source=source)
                return

            elapsed = time.time() - start_time
            if elapsed > 1800:
                _RESTART_PENDING = False
                logger.warning("♻️ Graceful restart maksimum vaxtı aşdı (30 dəq.) — restart icra olunur.")
                with contextlib.suppress(Exception):
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="⚠️ <b>Maksimum gözləmə vaxtı bitdi.</b>\n♻️ Bot indi restart edilir...",
                        parse_mode="HTML"
                    )
                await perform_restart(context, chat_id=chat_id, message_id=message_id,
                                      update_id=update_id, user_id=user_id, source=source)
                return

            now = time.time()
            if now - last_edit_time >= 6.0:
                lines = [
                    "⏳ <b>Təhlükəsiz Restart Gözlənilir...</b>\n",
                    "Tamamlanması gözlənilən aktiv proseslər:",
                ]
                if active["downloads"]:
                    lines.append(f"• 🎵 Aktiv yükləmələr: <b>{active['download_count']}</b> ədəd")
                if active["streams"]:
                    stream_details = "\n".join(f"  └ <i>{escape(s[:60])}</i>" for s in active["streams"][:3])
                    lines.append(f"• 🎙 Canlı yayım: <b>{active['stream_count']}</b> qrupda musiqi oxunur\n{stream_details}")
                lines.append(f"\n⏱ Gözləmə müddəti: {int(elapsed)} san.")
                lines.append("<i>Bütün proseslər bitən kimi bot avtomatik restart olunacaq.</i>")
                new_text = "\n".join(lines)
                if new_text != last_text:
                    last_text = new_text
                    last_edit_time = now
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⚡ Dərhal Restart Et", callback_data="botrestart:force")],
                        [InlineKeyboardButton(text="✖️ Ləğv Et", callback_data="botrestart:cancel_graceful")],
                    ])
                    with contextlib.suppress(Exception):
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=new_text,
                            parse_mode="HTML",
                            reply_markup=kb,
                        )

            await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Graceful restart xətası: {e}", exc_info=True)
        _RESTART_PENDING = False
        with contextlib.suppress(Exception):
            await perform_restart(context, chat_id=chat_id, message_id=message_id,
                                  update_id=update_id, user_id=user_id, source=source)


async def schedule_graceful_restart(context, chat_id: int, message_id: int, update_id: int = None, user_id: int = None, source: str = "cmd"):
    global _RESTART_PENDING, _FORCE_RESTART, _CANCEL_RESTART, _GRACEFUL_TASK
    if _RESTART_PENDING:
        return

    _RESTART_PENDING = True
    _FORCE_RESTART = False
    _CANCEL_RESTART = False

    active = get_active_operations(context)
    if not active["is_busy"]:
        _RESTART_PENDING = False
        await perform_restart(context, chat_id, message_id, update_id, user_id, source)
        return

    lines = [
        "⏳ <b>Təhlükəsiz Restart Planlaşdırıldı</b>\n",
        "Tamamlanması gözlənilən aktiv proseslər:",
    ]
    if active["downloads"]:
        lines.append(f"• 🎵 Aktiv yükləmələr: <b>{active['download_count']}</b> ədəd")
    if active["streams"]:
        stream_details = "\n".join(f"  └ <i>{escape(s[:60])}</i>" for s in active["streams"][:3])
        lines.append(f"• 🎙 Canlı yayım: <b>{active['stream_count']}</b> qrupda musiqi oxunur\n{stream_details}")
    lines.extend([
        "",
        "<i>Bütün yükləmələr və yayımdakı mahnılar tamamlanan kimi bot avtomatik restart olunacaq.</i>\n",
        "<i>💡 Gözləmədən dərhal restart etmək üçün aşağıdakı düyməyə bas:</i>",
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Dərhal Restart Et", callback_data="botrestart:force")],
        [InlineKeyboardButton(text="✖️ Restartı Ləğv Et", callback_data="botrestart:cancel_graceful")],
    ])

    with contextlib.suppress(Exception):
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb,
        )

    _GRACEFUL_TASK = spawn(_graceful_restart_loop(context, chat_id, message_id, update_id, user_id, source))


async def _announce(context):
    """Restartdan sonra köhnə mesajı yenilə."""
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        os.remove(STATE)
    except (OSError, ValueError):
        return
    await asyncio.sleep(2)
    took = time.time() - st.get("at", time.time())
    took_str = f"{took:.1f} san." if took < 60 else f"{int(took // 60)} dəq. {int(took % 60)} san."
    source = st.get("source", "menu")

    if source == "menu":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Bot idarəsi", callback_data="menu:dev")
        ]])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🏠 Menyu", callback_data="menu:main"),
            InlineKeyboardButton(text="✖️ Bağla", callback_data="menu:cancel"),
        ]])

    with contextlib.suppress(Exception):
        await context.bot.edit_message_text(
            chat_id=st["chat"], message_id=st["msg"], parse_mode="HTML",
            text=f"✅ <b>Bot yenidən başladı</b>\n⏱ Müddət: {took_str}",
            reply_markup=kb,
        )


def setup(context):
    dp = context.dp
    context.restart_button = button
    spawn(_announce(context))

    # ───────────────────────── /restart Komandası ─────────────────────────
    @dp.message(Command("restart", "botrestart", "reboot"))
    async def restart_command_handler(message: Message, command: CommandObject, event_update: types.Update = None):
        uid = message.from_user.id if message.from_user else None
        if not is_authorized(context, uid):
            with ui_styled("admin"):
                await message.reply(
                    "⛔ <b>Bu əmr yalnız bot sahibi üçün əlçatandır.</b>",
                    parse_mode="HTML"
                )
            return

        query = (command.args or "").strip().lower()
        up_id = getattr(event_update, "update_id", None)

        # /restart now, /restart force, /restart -f, /restart dərhal
        if query in ("now", "force", "-f", "dərhal", "derhal", "yes", "bəli", "beli", "1"):
            if time.time() - _STARTED < 15:
                await message.reply(
                    f"⚠️ <b>Bot yenicə başladılıb ({int(time.time() - _STARTED)} san. əvvəl).</b>\n"
                    "Zəhmət olmasa bir neçə saniyə gözləyin.",
                    parse_mode="HTML"
                )
                return

            sent = await message.reply(
                "♻️ <b>Bot dərhal yenidən başladılır...</b>\n"
                "<i>Gedən yükləmələr dayandırılır, sessiyalar bağlanır...</i>",
                parse_mode="HTML"
            )
            await perform_restart(context, chat_id=sent.chat.id, message_id=sent.message_id,
                                  update_id=up_id, user_id=uid, source="cmd")
            return

        active = get_active_operations(context)
        if query in ("wait", "graceful", "gözlə", "gozle"):
            sent = await message.reply("⏳ <b>Təhlükəsiz restart hazırlanır...</b>", parse_mode="HTML")
            await schedule_graceful_restart(context, chat_id=sent.chat.id, message_id=sent.message_id,
                                           update_id=up_id, user_id=uid, source="cmd")
            return

        # /restart təsdiq paneli
        if active["is_busy"]:
            lines = [
                "♻️ <b>Botu yenidən başlatmaq istəyirsiniz?</b>\n",
                "⚠️ <b>Hazırda aktiv proseslər var:</b>",
            ]
            if active["downloads"]:
                lines.append(f"• 🎵 Aktiv yükləmələr: <b>{active['download_count']}</b> ədəd")
            if active["streams"]:
                stream_details = "\n".join(f"  └ <i>{escape(s[:60])}</i>" for s in active["streams"][:3])
                lines.append(f"• 🎙 Canlı yayım: <b>{active['stream_count']}</b> qrupda musiqi oxunur\n{stream_details}")
            lines.extend([
                "",
                "<i>💡 <b>Bitdikdə Restart Et</b> — yükləmələr və yayımdakı <u>cari</u> mahnı bitəndə restart olur; "
                "yayımın növbəsi restartdan sonra avtomatik davam edir (yeni /play əlavə etmək olar).</i>",
                "<i>⚡ Dərhal: <code>/restart now</code> — yayım 20–40 san. kəsilir, sonra avtomatik bərpa olunur.</i>",
            ])
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⏳ Bitdikdə Restart Et", callback_data="botrestart:graceful"),
                    InlineKeyboardButton(text="⚡ Dərhal Restart Et", callback_data="botrestart:force"),
                ],
                [
                    InlineKeyboardButton(text="✖️ Ləğv Et", callback_data="botrestart:cancel"),
                ]
            ])
            await message.reply("\n".join(lines), parse_mode="HTML", reply_markup=kb)
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="♻️ Bəli, Restart Et", callback_data="botrestart:ok"),
                InlineKeyboardButton(text="✖️ Ləğv Et", callback_data="botrestart:cancel"),
            ]
        ])
        await message.reply(
            "♻️ <b>Botu yenidən başlatmaq istəyirsiniz?</b>\n\n"
            "• Hazırda aktiv yükləmə və ya canlı yayım yoxdur.\n"
            "• Userbot sessiyaları təhlükəsiz bağlanıb yenidən qoşulacaq.\n"
            "• Bot açıldıqdan sonra bu mesaj avtomatik yenilənəcək.\n\n"
            "<i>💡 Təsdiqsiz dərhal restart üçün: <code>/restart now</code></i>",
            parse_mode="HTML",
            reply_markup=kb,
        )

    # ───────────────────────── Restart Callback Düymələri ─────────────────────────
    @dp.callback_query(F.data.startswith("botrestart:"))
    async def restart_cb(cb: types.CallbackQuery, event_update: types.Update):
        global _FORCE_RESTART, _CANCEL_RESTART
        uid = cb.from_user.id if cb.from_user else None
        if not is_authorized(context, uid):
            await cb.answer("⛔ Yalnız bot sahibi", show_alert=True)
            return

        act = cb.data.split(":", 1)[1]

        if act == "ask":
            await cb.answer()
            active = get_active_operations(context)
            if active["is_busy"]:
                lines = [
                    "♻️ <b>Bot restart edilsin?</b>\n",
                    "⚠️ <b>Hazırda aktiv proseslər var:</b>",
                ]
                if active["downloads"]:
                    lines.append(f"• 🎵 Aktiv yükləmələr: <b>{active['download_count']}</b> ədəd")
                if active["streams"]:
                    stream_details = "\n".join(f"  └ <i>{escape(s[:60])}</i>" for s in active["streams"][:3])
                    lines.append(f"• 🎙 Canlı yayım: <b>{active['stream_count']}</b> qrupda musiqi oxunur\n{stream_details}")
                lines.extend([
                    "",
                    "<i>Proseslərin kəsilməməsi üçün gözləmə rejimində təhlükəsiz restart tövsiyə olunur.</i>"
                ])
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="⏳ Bitdikdə Restart Et", callback_data="botrestart:graceful"),
                        InlineKeyboardButton(text="⚡ Dərhal Restart Et", callback_data="botrestart:force"),
                    ],
                    [
                        InlineKeyboardButton(text="✖️ Ləğv Et", callback_data="botrestart:cancel"),
                    ]
                ])
                with contextlib.suppress(Exception):
                    await cb.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
                return
            else:
                with contextlib.suppress(Exception):
                    await cb.message.edit_text(
                        "♻️ <b>Bot restart edilsin?</b>\n\nUserbotlar və köməkçi xidmətlər yenidən qoşulacaq.",
                        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            ui.confirm("botrestart:ok", "menu:dev", "restart et")]))
                return

        if act == "cancel":
            await cb.answer("Ləğv edildi")
            with contextlib.suppress(Exception):
                await cb.message.edit_text("✖️ <b>Restart əməliyyatı ləğv edildi.</b>", parse_mode="HTML")
            return

        if act == "cancel_graceful":
            _CANCEL_RESTART = True
            await cb.answer("✖️ Restart ləğv edildi")
            return

        if act == "force":
            if _RESTART_PENDING:
                _FORCE_RESTART = True
                await cb.answer("⚡ Dərhal restart icra olunur...")
                return
            await cb.answer("♻️ Dərhal restart...")
            cur_text = getattr(cb.message, "text", "") or ""
            source = "menu" if ("dev" in str(getattr(cb.message, "reply_markup", "")) or "idarəsi" in cur_text) else "cmd"
            with contextlib.suppress(Exception):
                await cb.message.edit_text("♻️ <b>Dərhal restart edilir...</b>", parse_mode="HTML")
            await perform_restart(context, chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                                  update_id=event_update.update_id, user_id=uid, source=source)
            return

        if act not in ("ok", "graceful"):
            await cb.answer()
            return

        # Təkrar çatdırılmış köhnə callback restart dövrünə salmasın
        if time.time() - _STARTED < 15:
            await cb.answer("✅ Artıq yenidən başlayıb", show_alert=True)
            return

        cur_text = getattr(cb.message, "text", "") or ""
        source = "menu" if ("dev" in str(getattr(cb.message, "reply_markup", "")) or "idarəsi" in cur_text) else "cmd"

        active = get_active_operations(context)
        if active["is_busy"]:
            await cb.answer("⏳ Təhlükəsiz restart planlaşdırıldı")
            await schedule_graceful_restart(context, chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                                           update_id=event_update.update_id, user_id=uid, source=source)
            return

        await cb.answer("♻️ Restart...")
        with contextlib.suppress(Exception):
            await cb.message.edit_text("♻️ <b>Restart edilir...</b>", parse_mode="HTML")

        await perform_restart(context, chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                              update_id=event_update.update_id, user_id=uid, source=source)
