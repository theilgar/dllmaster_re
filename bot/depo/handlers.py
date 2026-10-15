"""
🎮 bot/depo/handlers.py - Depo doldurucu paneli, əmrləri və callback handler-ləri.
"""
from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import json
import logging
import re
import time
from datetime import datetime
from html import escape

from aiogram import F, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup

from bot.core import depo_helpers, panel as ui
from bot.core.panel import btn
from bot.depo.cleaner import clean_song, parse_tg_target, tg_item, tl_message_item
from bot.depo.crawler import COUNTRIES, HAS_YTM
from bot.depo.engine import (
    DEFAULT_SOURCES,
    MAX_DELAY,
    MAX_WORKERS,
    NOVID_AUTO_MAX,
    Q_PAGE,
    SYNC_AUTO_MAX,
    SYNC_EVERY,
    Filler,
    delete_depo_tracks,
)
from bot.depo.monitor import (
    ProcMeter,
    TOP_MAX_RUNTIME,
    TOP_SLOW_FOR,
    TOP_SLOW_INTERVAL,
    _bar,
    bot_tz,
    get_top_interval,
    render_top,
    set_top_interval,
)
from bot.depo.panels import (
    helpers_guide_view,
    df_top_interval_picker_view,
    dd_result_view,
    sync_result_view,
    hp_add_view,
    workers_label,
    clean_prompt_view,
    kb_sig,
    make_views,
)
from bot.depo.scanner import HAS_TELETHON, TgUser
from common.utilities import spawn
from userbot.core import tg_session

try:
    from telethon import events
except ImportError:
    events = None

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    bot = context.bot
    filler = Filler(context)
    context.depo_filler = filler
    tg = TgUser(context)
    filler.tg = tg
    helpers = depo_helpers.install(context)
    filler.helpers = helpers
    try:
        spawn(helpers.reload())
    except RuntimeError:
        pass
    context.depo_userbot = tg
    tg.start()

    is_creator = context.is_creator


    # ➕ Mənbə əlavə et funksiyası ləğv edilib
    scan_state = {"running": False, "stop": False, "task": None}
    context.depo_scan_state = scan_state

    # 🤖 ➕ Köməkçi bot: creator-dan token gözlənilir (5 dəq.)
    hp_wait = {"until": 0, "chat_id": None, "msg_id": None}
    hp_rate_wait = {"until": 0, "chat_id": None, "msg_id": None}
    df_val_wait = {"until": 0, "target": None, "chat_id": None, "msg_id": None, "back": "df:panel"}
    clean_wait = {"until": 0, "chat_id": None, "msg_id": None}
    clean_pending = {}  # uid -> {"query": str, "tracks": list, "ts": float}
    cq_add_wait = {"until": 0, "cc": None, "chat_id": None, "msg_id": None}

    def depo_waiting(uid) -> bool:
        return False

    def hp_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < hp_wait["until"]

    def hp_rate_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < hp_rate_wait["until"]

    def df_val_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < df_val_wait["until"]

    def clean_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < clean_wait["until"]

    def cq_add_waiting(uid) -> bool:
        return is_creator(uid) and time.time() < cq_add_wait["until"]

    # music_plugin / links_plugin bu yoxlamaya baxır — daxiletmə gözlənilərkən axtarış başlamasın
    context.depo_waiting = lambda uid: (
        hp_waiting(uid) or hp_rate_waiting(uid) or df_val_waiting(uid)
        or clean_waiting(uid) or cq_add_waiting(uid)
    )

    stop_kb = InlineKeyboardMarkup(inline_keyboard=[[btn("⏹ Skanı dayandır", "scan:stop")]])

    # ── 🔎 Skan (həm /depo add, həm /scan_chat) ──
    async def run_scan(chat_id: int, ent, limit, header: str, incremental: bool = False):
        _, title, _ = tg.describe(ent)
        scan_state.update(running=True, stop=False)
        limit_label = f"son {limit} audio" if limit else "bütün tarixçə"
        status = await bot.send_message(
            chat_id, f"🔎 <b>{escape(header)}</b>\n<i>{escape(title)} skan edilir ({limit_label})...</i>",
            parse_mode="HTML", reply_markup=stop_kb)
        batch, last_edit, added = [], [0.0], [0]

        def collect(it):
            batch.append(it)
            return True

        async def flush():
            if batch:
                added[0] += filler.add_front(batch)
                batch.clear()

        async def progress(st):
            await flush()                                 # doldurucu gözləmədən işə başlasın
            if time.time() - last_edit[0] < 4:
                return
            last_edit[0] = time.time()
            try:
                await status.edit_text(
                    f"🔎 <b>{escape(header)}</b>\n<i>{escape(title)}</i>\n\n"
                    f"📨 Audio: <b>{st['messages']}</b> · 🎶 adı oxundu: {st['items']} · ➕ növbəyə: {added[0]}",
                    parse_mode="HTML", reply_markup=stop_kb)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3365]: %r", _e)

        try:
            if incremental:      # daimi mənbə — mövqe yadda qalır, növbəti qurulma təkrar oxumur
                st = await filler.tg_scan_source(ent, limit, collect, lambda: scan_state["stop"], progress)
            else:
                st = await tg.scan(ent, limit, collect, lambda: scan_state["stop"], progress)
            await flush()
            head = "⏹ <b>Skan dayandırıldı</b>" if scan_state["stop"] else "✅ <b>Skan tamamlandı</b>"
            tail = ("" if filler.running else
                    "\n\n⚠️ Doldurucu söndürülüb — yükləmə üçün: /depo on")
            await status.edit_text(
                f"{head} — {escape(title)}\n\n"
                f"📨 Audio mesaj: <b>{st['messages']}</b>\n"
                f"🎶 Adı təmizlənib oxundu: <b>{st['items']}</b>\n"
                f"➕ Növbəyə əlavə olundu: <b>{added[0]}</b> <i>(qalanı artıq növbədə idi)</i>\n"
                f"❔ Adı oxunmayan: {st['unreadable']}\n\n"
                f"<i>Hər mahnı Spotify / iTunes-da axtarılıb song.link ilə YouTube-dan yüklənəcək. "
                f"Vəziyyət:</i> /depo{tail}",
                parse_mode="HTML")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Çat skan xətası ({title}): {e}", exc_info=True)
            try:
                await status.edit_text(f"❌ <b>Skan xətası:</b> <code>{escape(str(e))[:300]}</code>",
                                       parse_mode="HTML")
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3393]: %r", _e)
        finally:
            scan_state.update(running=False, stop=False, task=None)

    def launch_scan(chat_id, ent, limit, header, incremental=False) -> bool:
        if scan_state["running"]:
            return False
        scan_state["running"] = True
        scan_state["task"] = asyncio.create_task(run_scan(chat_id, ent, limit, header, incremental))
        return True

    context.depo_scan_state = scan_state


    async def waiting_token(message: types.Message) -> bool:
        return bool(message.from_user) and hp_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_token)
    async def helper_token_input(message: types.Message):
        try:
            await message.delete()                     # token çatda qalmasın
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3413]: %r", _e)
        found = re.findall(r"\d{6,12}:[A-Za-z0-9_-]{30,}", message.text or "")
        if not found:
            note = "❌ Token tapılmadı. Format: <code>123456789:AAH...</code>\n✍️ Yenidən göndər:"
        else:
            res = [helpers.add_token(t) for t in found]          # bir mesajda bir neçə token da olar
            ok = res.count("ok")
            labels = {"format": "format səhvdir", "main": "əsas botun tokenidir", "dup": "artıq siyahıdadır"}
            bad = [labels[r] for r in res if r != "ok"]
            if ok:
                hp_wait["until"] = 0
                await helpers.reload()
            note = (f"✅ {ok} köməkçi əlavə olundu" if ok else "❌ Əlavə olunmadı") \
                + (f" · ⚠️ {', '.join(bad)}" if bad else "") + ("" if ok else "\n✍️ Yenidən göndər:")
        text, kb = (helpers_view(note) if not hp_waiting(message.from_user.id) else hp_add_view(note))
        if hp_wait["msg_id"]:
            try:
                await bot.edit_message_text(chat_id=hp_wait["chat_id"], message_id=hp_wait["msg_id"],
                                            text=text, parse_mode="HTML", reply_markup=kb)
                return
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3434]: %r", _e)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def waiting_hp_rate(message: types.Message) -> bool:
        return bool(message.from_user) and hp_rate_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_hp_rate)
    async def helper_rate_input(message: types.Message):
        raw = message.text.strip().lower()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3446]: %r", _e)
        note = ""
        coords = (hp_rate_wait["chat_id"], hp_rate_wait["msg_id"])
        if raw in ("-", "min", "off"):
            v = helpers.set_rate(1)
            note = f"✅ Sürət limiti: {v} mahnı/dəq. (minimum)"
        elif raw in ("+", "max", "sonsuz"):
            v = helpers.set_rate(60)
            note = f"✅ Sürət limiti: {v} mahnı/dəq. (maksimum)"
        elif raw.isdigit():
            val = int(raw)
            v = helpers.set_rate(val)
            note = f"✅ Sürət limiti: {v} mahnı/dəq. hər bot"
        else:
            text = (
                "🤖 <b>Köməkçi botlar › Sürət limiti</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                "• <b>Rəqəm</b> (məs: <code>15</code>)\n"
                "• <b>+</b> (maksimum 60)\n"
                "• <b>-</b> (minimum 1)\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "df:pick:hpr")]])
            if coords[1]:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
            return

        hp_rate_wait["until"] = 0
        text, kb = df_rate_picker_view(note)
        if coords[1]:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def waiting_df_val(message: types.Message) -> bool:
        return bool(message.from_user) and df_val_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_df_val)
    async def df_val_input(message: types.Message):
        raw = message.text.strip().lower()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3491]: %r", _e)
        target = df_val_wait.get("target") or "d"
        coords = (df_val_wait["chat_id"], df_val_wait["msg_id"])
        back_route = df_val_wait.get("back") or "df:panel"
        bk = "dt" if back_route == "dt:start" else "p"

        if target == "d":
            if raw in ("-", "min", "off"):
                v = filler.set_delay(0)
                note = "✅ Gözləmə intervalı: 0 san. (minimum)"
            elif raw in ("+", "max", "sonsuz"):
                v = filler.set_delay(MAX_DELAY)
                note = f"✅ Gözləmə intervalı: {MAX_DELAY} san. (maksimum)"
            elif raw.isdigit():
                val = int(raw)
                v = filler.set_delay(val)
                note = f"✅ Gözləmə intervalı: {v} san."
            else:
                text = (
                    "📦 <b>Depo doldurucu › Gözləmə intervalı</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                    "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                    "• <b>Rəqəm</b> (məs: <code>10</code>)\n"
                    f"• <b>+</b> (maksimum {MAX_DELAY})\n"
                    "• <b>-</b> (minimum 0)\n"
                )
                cancel_target = f"df:pick:d:{bk}" if bk != "p" else "df:pick:d"
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                if coords[1]:
                    with contextlib.suppress(Exception):
                        await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                return

            df_val_wait["until"] = 0
            text, kb = df_delay_picker_view(note, back=back_route)
            if coords[1]:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                    return
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

        elif target == "w":
            if raw in ("-", "min"):
                v = filler.set_workers(1)
                note = "✅ Worker sayı: 1 işçi (minimum)"
            elif raw in ("+", "max"):
                v = filler.set_workers(MAX_WORKERS)
                note = f"✅ Worker sayı: {MAX_WORKERS} işçi (maksimum)"
            elif raw.isdigit():
                val = int(raw)
                v = filler.set_workers(val)
                note = f"✅ Worker sayı: {workers_label(v)}"
            else:
                text = (
                    "📦 <b>Depo doldurucu › Worker sayı</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                    "✍️ <i>Yalnız aşağıdakılardan birini yazın:</i>\n"
                    "• <b>Rəqəm</b> (məs: <code>4</code>)\n"
                    f"• <b>+</b> (maksimum {MAX_WORKERS})\n"
                    "• <b>-</b> (minimum 1)\n"
                )
                cancel_target = f"df:pick:w:{bk}" if bk != "p" else "df:pick:w"
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                if coords[1]:
                    with contextlib.suppress(Exception):
                        await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                return

            df_val_wait["until"] = 0
            text, kb = df_worker_picker_view(note, back=back_route)
            if coords[1]:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                    return
            await message.answer(text, parse_mode="HTML", reply_markup=kb)

        elif target == "i":
            if raw in ("-", "min"):
                v = set_top_interval(0.5)
                note = "✅ Yenilənmə intervalı: 0.5 san. (minimum)"
            elif raw in ("+", "max"):
                v = set_top_interval(60.0)
                note = "✅ Yenilənmə intervalı: 60 san. (maksimum)"
            else:
                try:
                    val = float(raw.replace(",", "."))
                    v = set_top_interval(val)
                    note = f"✅ Yenilənmə intervalı: {v:g} san."
                except Exception:
                    text = (
                        "📊 <b>Canlı monitor › Yenilənmə intervalı</b>\n━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ <b>\"{escape(message.text[:30])}\" düzgün deyil!</b>\n\n"
                        "✍️ <i>Yalnız saniyə yazın (məs: <code>1</code>, <code>2</code>, <code>5</code>):</i>\n"
                    )
                    cancel_target = f"df:pick:i:{bk}" if bk != "p" else "df:pick:i"
                    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                    if coords[1]:
                        with contextlib.suppress(Exception):
                            await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                    return

            ts = top_sessions.get(coords)
            if ts:
                ts["interval"] = v
                ts["dirty"] = True
            df_val_wait["until"] = 0
            text, kb = df_top_interval_picker_view(ts, note, back=back_route)
            if coords[1]:
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb)
                    return
            await message.answer(text, parse_mode="HTML", reply_markup=kb)


    async def waiting_clean(message: types.Message) -> bool:
        return bool(message.from_user) and clean_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_clean)
    async def clean_query_input(message: types.Message):
        raw = message.text.strip()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3688]: %r", _e)
        coords = (clean_wait.get("chat_id"), clean_wait.get("msg_id"))
        clean_wait["until"] = 0
        text, kb = await prepare_clean_view(message.from_user.id, raw, coords[0], coords[1])
        if coords[1]:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb
                )
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    async def waiting_cq_add(message: types.Message) -> bool:
        return bool(message.from_user) and cq_add_waiting(message.from_user.id)

    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), waiting_cq_add)
    async def cq_add_input(message: types.Message):
        raw = message.text.strip()
        try:
            await message.delete()
        except Exception as _e:
            logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:3709]: %r", _e)
        cc = cq_add_wait.get("cc") or "AZ"
        coords = (cq_add_wait.get("chat_id"), cq_add_wait.get("msg_id"))
        cq_add_wait["until"] = 0
        if len(raw) < 2:
            note = "⚠️ Sorğu çox qısadır"
        else:
            filler.add_country_query(cc, raw)
            note = f"✅ \"{raw}\" əlavə olundu"
        text, kb = country_queries_view(cc, note)
        if coords[1]:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=coords[0], message_id=coords[1], text=text, parse_mode="HTML", reply_markup=kb
                )
                return
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


    hp_page = {"n": 0}                 # 🤖 siyahının cari səhifəsi (düymələr basılanda yerində qalsın)
    HP_PER_PAGE = 8
    _v = make_views(filler, helpers, tg, context, scan_state, clean_pending, clean_wait, hp_page, HP_PER_PAGE)
    ytm_line = _v.ytm_line
    sources_view = _v.sources_view
    df_delay_picker_view = _v.df_delay_picker_view
    df_worker_picker_view = _v.df_worker_picker_view
    df_rate_picker_view = _v.df_rate_picker_view
    fail_view = _v.fail_view
    queue_view = _v.queue_view
    country_queries_list_view = _v.country_queries_list_view
    country_queries_view = _v.country_queries_view
    countries_view = _v.countries_view
    prepare_clean_view = _v.prepare_clean_view
    helpers_view = _v.helpers_view
    dd_intro = _v.dd_intro
    sync_intro = _v.sync_intro
    panel_async = _v.panel_async


    # ── 🧹 Sinxronizasiya ──
    sync_state = {"running": False, "stop": False, "pending": None, "task": None}


    # ── 🧬 Dublikatlar və depo indeksi ──
    dd_state = {"running": False, "stop": False, "pending": None, "task": None}


    async def run_dedupe(chat_id: int, msg_id: int):
        stop_kb = InlineKeyboardMarkup(inline_keyboard=[[btn("⏹ Dayandır", "df:ddstop")]])
        last = [0.0]

        async def progress(st):
            if time.time() - last[0] < 3:
                return
            last[0] = time.time()
            with contextlib.suppress(Exception):
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, parse_mode="HTML",
                                            reply_markup=stop_kb,
                                            text=f"🧬 <b>Depo oxunur...</b> <i>({st.get('method', 'userbot')})</i>\n\n📨 {st['scanned']} post")
        dedupe_dict = {"chat_id": chat_id, "msg_id": msg_id, "ts": time.time()}
        filler._set("active_dedupe", json.dumps(dedupe_dict))
        try:
            st = await filler.dedupe_scan(progress, lambda: dd_state["stop"])
            filler._set("active_dedupe", "")
            dd_state["pending"] = st
            text, kb = dd_result_view(st)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, parse_mode="HTML",
                    text=("⚠️ <b>Dublikat yoxlaması dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                          "Yoxlamanı yenidən başlada bilərsiniz:"),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [btn("🔄 Yenidən başla", "df:ddgo")],
                        ui.nav("df:panel", "Doldurucu", close_data="df:close")
                    ]))
            raise
        except (ConnectionError, Exception) as e:
            if dd_state.get("stop") or "disconnected" in str(e).lower():
                logger.info(f"Depo dublikat yoxlaması restart səbəbilə dayandı: {e}")
                text = ("⚠️ <b>Dublikat yoxlaması dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                        "Yoxlamanı yenidən başlada bilərsiniz:")
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [btn("🔄 Yenidən başla", "df:ddgo")],
                    ui.nav("df:panel", "Doldurucu", close_data="df:close")
                ])
            else:
                logger.error(f"Depo dublikat yoxlaması: {e}", exc_info=True)
                text, kb = (f"❌ <b>Yoxlama alınmadı:</b> <code>{escape(str(e))[:300]}</code>",
                            InlineKeyboardMarkup(inline_keyboard=[
                                [btn("🔄 Yenidən başla", "df:ddgo")],
                                ui.nav("df:panel", "Doldurucu", close_data="df:close")
                            ]))
        finally:
            dd_state.update(running=False, task=None)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML",
                                        reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


    async def run_sync(chat_id: int, msg_id: int, mode: str = "quick"):
        stop_kb = InlineKeyboardMarkup(inline_keyboard=[[btn("⏹ Dayandır", "df:syncstop")]])
        last = [0.0]

        async def progress(st):
            if time.time() - last[0] < 3:
                return
            last[0] = time.time()
            pct = st["checked"] / st["total"] * 100 if st["total"] else 100
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, parse_mode="HTML", reply_markup=stop_kb,
                    text=(f"🧹 <b>Yoxlanılır...</b> ({st.get('method', 'userbot')})\n\n"
                          f"<code>[{_bar(pct / 100, 20)}]</code> {pct:.0f}%\n"
                          f"📨 {st['checked']} / {st['total']} · 🗑 yoxdur: {len(st['missing'])}"))
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:4317]: %r", _e)

        sync_state.update(running=True, stop=False, pending=None)
        filler._set("active_sync", json.dumps({"chat_id": chat_id, "msg_id": msg_id, "mode": mode, "ts": time.time()}))
        try:
            st = await filler.sync_scan(progress, lambda: sync_state["stop"], mode=mode)
            filler._set("active_sync", "")
            sync_state["pending"] = st
            text, kb = sync_result_view(st)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, parse_mode="HTML",
                    text=("⚠️ <b>Sinxronizasiya dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                          "Yarımçıq qalan sinxronizasiyanı davam etdirə və ya sıfırdan başlada bilərsiniz:"),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [btn("▶️ Davam et", "df:syncgo:quick"), btn("🔄 Yenidən başla", "df:syncgo:full")],
                        ui.nav("df:panel", "Doldurucu", close_data="df:close")
                    ]))
            raise
        except (ConnectionError, Exception) as e:
            if sync_state.get("stop") or "disconnected" in str(e).lower():
                logger.info(f"Depo sinxronizasiyası restart səbəbilə dayandı: {e}")
                text = ("⚠️ <b>Sinxronizasiya dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                        "Yarımçıq qalan sinxronizasiyanı davam etdirə və ya sıfırdan başlada bilərsiniz:")
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [btn("▶️ Davam et", "df:syncgo:quick"), btn("🔄 Yenidən başla", "df:syncgo:full")],
                    ui.nav("df:panel", "Doldurucu", close_data="df:close")
                ])
            else:
                logger.error(f"Depo sinxronizasiya xətası: {e}", exc_info=True)
                text, kb = (f"❌ <b>Sinxronizasiya alınmadı:</b> <code>{escape(str(e))[:300]}</code>",
                            InlineKeyboardMarkup(inline_keyboard=[
                                [btn("🔄 Yenidən başla", f"df:syncgo:{mode}")],
                                ui.nav("df:panel", "Doldurucu", close_data="df:close")
                            ]))
        finally:
            sync_state.update(running=False, task=None)
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML",
                                        reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

    def launch_sync(chat_id, msg_id, mode: str = "quick") -> bool:
        if sync_state["running"]:
            return False
        sync_state["running"] = True
        sync_state["task"] = asyncio.create_task(run_sync(chat_id, msg_id, mode=mode))
        return True

    async def auto_sync_loop():
        await asyncio.sleep(1800)                     # bot açılandan yarım saat sonra
        while True:
            try:
                last = json.loads(filler._get("last_sync") or "null") or {}
            except ValueError:
                last = {}
            wait = SYNC_EVERY - (time.time() - last.get("ts", 0))
            if wait > 0:
                await asyncio.sleep(min(wait, 3600))
                continue
            if sync_state["running"] or not await tg.ok():
                await asyncio.sleep(3600)
                continue
            try:
                sync_state["running"] = True
                st = await filler.sync_scan(allow_bot=False, mode="quick")
                miss = len(st["missing"])
                ok = bool(miss) and st["checked"] and miss / st["checked"] <= SYNC_AUTO_MAX
                if ok:
                    await asyncio.to_thread(filler.sync_apply, st["depo_id"], st["missing"], True)
                filler._set("last_sync", json.dumps({"ts": time.time(), "total": st["total"], "missing": miss,
                                                     "applied": bool(ok)}))
                logger.info(f"🧹 Avtomatik sinxronizasiya: {st['total']} post, {miss} yoxdur"
                            + (" — bazadan silindi" if ok else (" — çox çoxdur, /depo sync ilə yoxla" if miss else "")))
                # 🧬 kanalda olub bazada olmayan / dublikat postlar → bazaya bağla (heç nə silinmir)
                if not dd_state["running"]:
                    try:
                        dd_state["running"] = True
                        dst = await filler.dedupe_scan()
                        if not dst["stopped"] and (dst["orphans"] or dst["dups"]):
                            await asyncio.to_thread(filler.dedupe_link, dst)
                        # 🔎 ID-siz postlar — gündə ən çox NOVID_AUTO_MAX, əvvəl tapılmayanlar təkrarlanmır
                        try:
                            failed = set(json.loads(filler._get("novid_failed") or "[]"))
                        except ValueError:
                            failed = set()
                        todo = [p for p in dst.get("novid_posts") or [] if p["mid"] not in failed][:NOVID_AUTO_MAX]
                        if todo and not dst["stopped"]:
                            m = await filler.match_novid(todo)
                            n = await asyncio.to_thread(filler.novid_link, dst["depo_id"], m["matched"])
                            failed |= {p["mid"] for p in m["unmatched"]}
                            filler._set("novid_failed", json.dumps(sorted(failed)[-5000:]))
                            logger.info(f"🔎 ID-siz depo postları: {n} bağlandı, {len(m['unmatched'])} tapılmadı")
                    except Exception as e:
                        logger.warning(f"🧬 Avtomatik indeks alınmadı: {e}")
                    finally:
                        dd_state["running"] = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"🧹 Avtomatik sinxronizasiya alınmadı: {e}")
                filler._set("last_sync", json.dumps({"ts": time.time(), "total": 0, "missing": 0, "applied": False}))
            finally:
                sync_state["running"] = False

    try:
        sync_state["auto"] = asyncio.get_running_loop().create_task(auto_sync_loop())
    except RuntimeError:
        pass
    context.depo_sync_state = sync_state
    context.depo_dedupe_state = dd_state


    @dp.message(Command("depo"))
    async def depo_cmd(message: types.Message, command: CommandObject):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        args = (command.args or "").strip()
        low = args.lower()
        if low in ("on", "aç", "start"):
            filler.start()
            await message.answer("🟢 <b>Depo doldurucu işə salındı.</b>\n<i>Söndürmək üçün:</i> /depo off",
                                 parse_mode="HTML")
            return
        if low in ("off", "bağla", "stop"):
            await filler.stop()
            await message.answer("🔴 <b>Depo doldurucu söndürüldü.</b>", parse_mode="HTML")
            return
        if low.startswith(("delay", "fasilə", "fasile")):
            num = re.search(r"\d+", low)
            if not num:
                await message.answer(f"⏱ Fasilə: <b>{filler.delay} san.</b>\n<i>Dəyiş:</i> <code>/depo delay 5</code>",
                                     parse_mode="HTML")
                return
            v = filler.set_delay(int(num.group()))
            await message.answer(f"⏱ Mahnılar arası fasilə: <b>{v} san.</b>", parse_mode="HTML")
            return
        if low.startswith(("workers", "paralel", "parallel")):
            num = re.search(r"\d+", low)
            if not num:
                await message.answer(f"🧵 Rejim: <b>{workers_label(filler.workers)}</b>\n"
                                     f"<i>Dəyiş:</i> <code>/depo workers 16</code> (1 = tək-tək, max {MAX_WORKERS})",
                                     parse_mode="HTML")
                return
            v = filler.set_workers(int(num.group()))
            warn = ("\n⚠️ <i>32-dən çox işçi: eyni anda çoxlu yt-dlp + ffmpeg — CPU / RAM yükü ciddi artır, "
                    "YouTube 429 / bot-yoxlaması demək olar ki, qaçılmazdır (bir IP). /depotop ilə izlə.</i>" if v > 32 else
                    "\n⚠️ <i>Çox işçi: Telegram kanala göndərmə limiti (flood) və YouTube 429 / bot-yoxlaması "
                    "ehtimalı artır. Fasiləni 0 etmə.</i>" if v > 8 else "")
            await message.answer(f"🧵 Rejim: <b>{workers_label(v)}</b>{warn}", parse_mode="HTML")
            return
        if low.startswith("ytm"):
            if not HAS_YTM:
                await message.answer("❌ <code>pip install ytmusicapi</code> lazımdır.", parse_mode="HTML")
                return
            arg = low[3:].strip()
            if arg in ("on", "off"):
                filler.set_ytm(arg == "on")
                if arg == "off":
                    filler.ytm_queue.clear()
                    filler.ytm_seen.clear()
            await message.answer(ytm_line(), parse_mode="HTML")
            return
        if low.startswith(("sync", "sinxron")):
            text, kb = sync_intro()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if low.startswith(("fail", "xəta")):
            text, kb = fail_view()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if low.startswith(("helper", "köməkçi", "komekci")):
            rest = args.split(None, 1)[1].strip() if len(args.split(None, 1)) > 1 else ""
            sub, _, val = rest.partition(" ")
            note = ""
            if sub.lower() == "add" and val.strip():
                try:
                    await message.delete()                     # token çatda qalmasın
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:4484]: %r", _e)
                r = helpers.add_token(val.strip())
                note = {"ok": "✅ Köməkçi əlavə olundu — yoxlanılır...",
                        "format": "❌ Token formatı səhvdir (123456789:AA...)",
                        "main": "❌ Bu əsas botun tokenidir", "dup": "⚠️ Bu bot artıq siyahıdadır"}[r]
                if r == "ok":
                    await helpers.reload()
                    note = "✅ Köməkçi əlavə olundu"
            elif sub.lower() in ("rm", "del", "sil") and val.strip().isdigit():
                r = helpers.remove(int(val) - 1)
                note = {"ok": "🗑 Silindi", "range": "❌ Belə nömrə yoxdur",
                        "env": "⚠️ Bu bot config.env-dəki DEPO_HELPER_TOKENS-dandır — oradan sil"}[r]
                if r == "ok":
                    await helpers.reload()
            text, kb = helpers_view(note)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if low.startswith("add"):
            await message.answer("❌ Mənbə əlavə etmək funksiyası ləğv edilib.", parse_mode="HTML")
            return
        if low.startswith(("clean", "sil", "təmizlə", "temizle")):
            parts_args = args.split(None, 1)
            q = parts_args[1].strip() if len(parts_args) > 1 else ""
            if not q:
                clean_wait["until"] = time.time() + 300
                clean_wait["chat_id"] = message.chat.id
                clean_wait["msg_id"] = message.message_id
                text, kb = clean_prompt_view()
                await message.answer(text, parse_mode="HTML", reply_markup=kb)
                return
            text, kb = await prepare_clean_view(message.from_user.id, q, message.chat.id, message.message_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        if low.startswith(("queries", "sorğular", "sorgular", "cq")):
            text, kb = country_queries_list_view()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        text, kb = await panel_async()
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.message(Command("depo_clean"), F.from_user.id.func(is_creator))
    async def depo_clean_cmd(message: types.Message, command: CommandObject):
        q = (command.args or "").strip()
        if not q:
            clean_wait["until"] = time.time() + 300
            clean_wait["chat_id"] = message.chat.id
            clean_wait["msg_id"] = message.message_id
            text, kb = clean_prompt_view()
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return
        text, kb = await prepare_clean_view(message.from_user.id, q, message.chat.id, message.message_id)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    @dp.callback_query(F.data.startswith("df:"))
    async def depo_callback(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        action = parts[1]
        if action != "hpadd":
            hp_wait["until"] = 0
        if action != "cleanprompt":
            clean_wait["until"] = 0
        if action != "cqadd":
            cq_add_wait["until"] = 0

        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                if "not modified" not in str(e):
                    logger.warning(f"Depo paneli yenilənmədi: {e}")

        if action in ("dm", "wm") and len(parts) > 2 and parts[2] in ("auto", "manual"):
            which = "delay" if action == "dm" else "workers"
            await asyncio.to_thread(filler.set_mode, which, parts[2])
            if parts[2] == "auto":
                val = f"{filler.delay} san." if which == "delay" else workers_label(filler.workers)
                await cb.answer(f"🤖 Avto: {val}")
            else:
                await cb.answer("✋ Manual — dəyəri özün seç")
            await show(*await panel_async())
            return
        if action == "close":
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:4563]: %r", _e)
            return
        if action == "on":
            filler.start()
            await cb.answer("🟢 İşə salındı")
        elif action == "off":
            await cb.answer("🔴 Söndürülür — depoya gedən mahnılar bitirilir...")
            await filler.stop()
        elif action == "noop":
            await cb.answer("➖ / ➕ ilə dəyiş")
            return
        elif (action in ("pick", "d") and len(parts) > 2 and parts[2] == "d") or (action == "d" and len(parts) == 2):
            bk = parts[3] if len(parts) > 3 else "p"
            back_url = "dt:start" if bk == "dt" else "df:panel"
            df_val_wait["until"] = 0
            await cb.answer()
            await show(*df_delay_picker_view(back=back_url))
            return
        elif (action in ("pick", "w") and len(parts) > 2 and parts[2] == "w") or (action == "w" and len(parts) == 2):
            bk = parts[3] if len(parts) > 3 else "p"
            back_url = "dt:start" if bk == "dt" else "df:panel"
            df_val_wait["until"] = 0
            await cb.answer()
            await show(*df_worker_picker_view(back=back_url))
            return
        elif (action in ("pick", "i") and len(parts) > 2 and parts[2] == "i") or (action == "i" and len(parts) == 2):
            bk = parts[3] if len(parts) > 3 else "dt"
            back_url = "dt:start" if bk == "dt" else "df:panel"
            df_val_wait["until"] = 0
            await cb.answer()
            ts = top_sessions.get((cb.message.chat.id, cb.message.message_id))
            await show(*df_top_interval_picker_view(ts, back=back_url))
            return
        elif action == "d" and len(parts) > 2 and parts[2] in ("+", "-"):
            v = filler.step_delay(1 if parts[2] == "+" else -1)
            await cb.answer(f"⏱ Fasilə: {v} san.")
        elif action == "w" and len(parts) > 2 and parts[2] in ("+", "-"):
            cur = filler.workers
            v = filler.step_workers(1 if parts[2] == "+" else -1)
            if v == cur:
                await cb.answer("Tək-tək rejimdir (minimum)" if v == 1 else f"Maksimum {MAX_WORKERS} paralel",
                                show_alert=False)
            else:
                await cb.answer(f"🧵 {workers_label(v)}")
        elif action == "ytm":
            if not HAS_YTM:
                await cb.answer("ytmusicapi quraşdırılmayıb: pip install ytmusicapi", show_alert=True)
                return
            filler.set_ytm(not filler.ytm_on)
            if not filler.ytm_on:
                filler.ytm_queue.clear()
                filler.ytm_seen.clear()
            await cb.answer("🎵 YT Music kataloqu " + ("açıldı" if filler.ytm_on else "söndürüldü"))
        elif action == "rebuild":
            filler.reset_state()
            await filler.save_state(force=True)
            await cb.answer("Növbə sıfırlandı — Telegram tarixçəsi də tam yenidən oxunacaq", show_alert=True)
        elif action == "sources":
            await cb.answer()
            await show(*sources_view())
            return
        elif action == "addsrc":
            await cb.answer("❌ Mənbə əlavə etmək funksiyası ləğv edilib", show_alert=True)
            return
        elif action == "rm" and len(parts) > 2 and parts[2].isdigit():
            srcs = filler.sources()
            i = int(parts[2])
            if 0 <= i < len(srcs):
                srcs.pop(i)
                filler.save_sources(srcs)
            await cb.answer("Silindi")
            await show(*sources_view())
            return
        elif action == "sync":
            await cb.answer()
            await show(*sync_intro())
            return
        elif action == "syncgo":
            mode = parts[2] if len(parts) > 2 and parts[2] in ("quick", "full") else "quick"
            if not launch_sync(cb.message.chat.id, cb.message.message_id, mode=mode):
                await cb.answer("Sinxronizasiya artıq gedir", show_alert=True)
                return
            await cb.answer(f"🔎 {'Tez' if mode == 'quick' else 'Tam'} yoxlama başladı")
            await show(f"🧹 <b>Yoxlanılır...</b> ({'⚡ Tez' if mode == 'quick' else '🔍 Tam'})", InlineKeyboardMarkup(
                inline_keyboard=[[btn("⏹ Dayandır", "df:syncstop")]]))
            return
        elif action == "syncstop":
            sync_state["stop"] = True
            await cb.answer("⏹ Dayandırılır — yoxlanılan hissənin nəticəsi göstəriləcək")
            return
        elif action == "syncapply" and len(parts) > 2:
            st = sync_state.get("pending")
            if not st or not st["missing"]:
                await cb.answer("Nəticə köhnəlib — yenidən yoxla", show_alert=True)
                await show(*sync_intro())
                return
            block = parts[2] == "b"
            r = await asyncio.to_thread(filler.sync_apply, st["depo_id"], st["missing"], block)
            sync_state["pending"] = None
            filler._set("last_sync", json.dumps({"ts": time.time(), "total": st["total"],
                                                 "missing": len(st["missing"]), "applied": True}))
            n = 0 if block else filler.unblock_last()   # ♻️ blok qoyulmayıb — sadəcə növbənin önünə
            await cb.answer("✅ Silindi")
            text = (f"✅ <b>Baza sinxronlaşdırıldı</b>\n\n🗑 Post: {r['posts']} · mahnı (video): {r['songs']} · "
                    f"sətir: {r['rows']}\n"
                    + ("🚫 Doldurucu bunları yenidən yükləməyəcək." if block else
                       f"♻️ {n} mahnı növbənin önünə qoyuldu — yenidən yüklənəcək."))
            kb_rows = ([[btn("♻️ Fikrimi dəyişdim — yenidən yüklə", "df:unblock")]] if block and r["songs"] else [])
            kb_rows.append(ui.nav("df:panel", "Doldurucu", close_data="df:close"))
            await show(text, InlineKeyboardMarkup(inline_keyboard=kb_rows))
            return
        elif action == "unblock":
            n = filler.unblock_last()
            await cb.answer(f"♻️ {n} mahnı növbəyə qoyuldu")
        elif action == "unblockall":
            n = filler.clear_removed()
            await cb.answer(f"♻️ {n} mahnının bloku götürüldü — mənbələrdə rast gəlinsə yüklənəcək", show_alert=True)
            await show(*sync_intro())
            return
        elif action == "fail":
            await cb.answer()
            await show(*queue_view("f", 0))
            return
        elif action == "dd":
            await cb.answer()
            if dd_state["running"]:
                await show("🧬 <b>Yoxlama gedir...</b>", InlineKeyboardMarkup(
                    inline_keyboard=[[btn("⏹ Dayandır", "df:ddstop")]]))
            elif dd_state["pending"]:
                await show(*dd_result_view(dd_state["pending"]))
            else:
                await show(*dd_intro())
            return
        elif action == "ddgo":
            if dd_state["running"] or sync_state["running"]:
                await cb.answer("Artıq bir yoxlama gedir", show_alert=True)
                return
            if not await tg.ok():
                await cb.answer("Userbot aktiv deyil", show_alert=True)
                return
            await cb.answer("🔎 Başladı")
            dd_state.update(running=True, stop=False, pending=None)
            await show("🧬 <b>Depo oxunur...</b>", InlineKeyboardMarkup(
                inline_keyboard=[[btn("⏹ Dayandır", "df:ddstop")]]))
            dd_state["task"] = asyncio.create_task(run_dedupe(cb.message.chat.id, cb.message.message_id))
            return
        elif action == "ddstop":
            dd_state["stop"] = True
            await cb.answer("⏹ Dayandırılır...")
            return
        elif action == "ddlink":
            st = dd_state.get("pending")
            if not st:
                await cb.answer("Əvvəl yoxla", show_alert=True)
                return
            await cb.answer("🔗 Bağlanır...")
            r = await asyncio.to_thread(filler.dedupe_link, st)
            dd_state["pending"] = None
            await show(*dd_intro(f"✅ <b>{r['linked']}</b> video bazaya bağlandı, <b>{r['repointed']}</b> sətir "
                                 f"kanonik posta yönəldi. Bu mahnılar artıq yenidən yüklənməyəcək."))
            return
        elif action == "ddnovid":
            st = dd_state.get("pending")
            posts = (st or {}).get("novid_posts") or []
            if not posts:
                await cb.answer("Əvvəl yoxla", show_alert=True)
                return
            if dd_state.get("running"):
                await cb.answer("⏳ Artıq gedir", show_alert=True)
                return
            await cb.answer("🔎 Axtarılır...")
            dd_state.update(running=True, stop=False)
            chat_id, msg_id = cb.message.chat.id, cb.message.message_id
            stop_kb = InlineKeyboardMarkup(inline_keyboard=[[btn("⏹ Dayandır", "df:ddstop")]])
            last = [0.0]

            async def progress(m):
                if time.time() - last[0] < 3:
                    return
                last[0] = time.time()
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id, parse_mode="HTML", reply_markup=stop_kb,
                        text=(f"🔎 <b>ID-siz mahnılar YouTube-da axtarılır...</b>\n\n"
                              f"<code>[{_bar(m['done'] / max(1, m['total']), 20)}]</code> {m['done']}/{m['total']}\n"
                              f"✅ tapıldı: {len(m['matched'])} · ❔ tapılmadı: {len(m['unmatched'])}"))

            async def job():
                try:
                    m = await filler.match_novid(posts, progress, lambda: dd_state["stop"])
                    linked = await asyncio.to_thread(filler.novid_link, st["depo_id"], m["matched"])
                    sample = "\n".join(f"• {escape((p['performer'] + ' - ' + p['title'])[:60])}"
                                       for p in m["unmatched"][:6])
                    note = (f"🔎 <b>Nəticə{' (dayandırıldı)' if m['stopped'] else ''}:</b>\n"
                            f"✅ <b>{linked}</b> mahnı YouTube-da tapıldı və bazaya bağlandı — artıq axtarışda "
                            f"görünür və yenidən yüklənmir\n"
                            + (f"🔁 {m['already']} post artıq bazadakı mahnının dublikatıdır\n" if m["already"] else "")
                            + (f"❔ {len(m['unmatched'])} üçün əmin uyğunluq tapılmadı (toxunulmadı):\n{sample}"
                               if m["unmatched"] else ""))
                    st["novid_posts"] = m["unmatched"]
                    text, kb = dd_result_view(st, note)
                except Exception as e:
                    logger.error(f"ID-siz uyğunlaşdırma: {e}", exc_info=True)
                    text, kb = (f"❌ <code>{escape(str(e))[:300]}</code>",
                                InlineKeyboardMarkup(inline_keyboard=[ui.nav("df:panel", "Doldurucu", close_data="df:close")]))
                finally:
                    dd_state.update(running=False, task=None)
                with contextlib.suppress(Exception):
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML",
                                                reply_markup=kb, disable_web_page_preview=True)

            dd_state["task"] = asyncio.create_task(job())
            return
        elif action in ("ctry", "ctryt", "ctryclr", "ctrygo", "ctryup", "ctrystrict"):
            cs = filler.countries()
            note = ""
            if action == "ctryt" and len(parts) > 2 and parts[2] in COUNTRIES:
                cc = parts[2]
                cs = [c for c in cs if c != cc] if cc in cs else cs + [cc]
                filler.save_countries(cs)
                if cc not in cs:                          # seçimdən çıxdı → onun mahnıları ölkə növbəsindən
                    filler.country_queue = deque(x for x in filler.country_queue if x.get("country") != cc)
                else:
                    if filler._get("country_strict") is None:
                        filler._set("country_strict", "1")
                    spawn(filler.apply_countries())
                if filler.ytm:
                    filler.ytm.reset_prio()           # köhnə seçimin prioriteti qalmasın
                await cb.answer(("✅ " if cc in cs else "➖ ") + COUNTRIES[cc])
            elif action == "ctryup" and len(parts) > 2 and parts[2] in cs:
                i = cs.index(parts[2])
                if i > 0:
                    cs[i - 1], cs[i] = cs[i], cs[i - 1]
                    filler.save_countries(cs)
                await cb.answer("⬆️ Prioritet artırıldı")
            elif action == "ctrystrict":
                on = filler._get("country_strict") != "1"
                filler._set("country_strict", "1" if on else "0")
                await cb.answer("🎯 Yalnız seçilmiş ölkələr" if on else "🌍 Ölkələr əvvəl, sonra qalan dünya")
            elif action == "ctryclr":
                filler.save_countries([])
                filler.country_queue.clear()
                if filler.ytm:
                    filler.ytm.reset_prio()
                cs = []
                await cb.answer("🗑 Ölkə seçimi silindi")
            elif action == "ctrygo":
                if not cs:
                    await cb.answer("Əvvəl ölkə seç", show_alert=True)
                    return
                await cb.answer("⚡ Mahnılar və ifaçılar oxunur...")
                await show("🌍 <b>Ölkə mahnıları oxunur...</b>\n<i>" + ", ".join(COUNTRIES[c] for c in cs) + "</i>",
                           None)
                if filler.ytm:
                    filler.ytm.reset_prio()
                try:
                    r = await filler.apply_countries()
                    note = (f"✅ <b>+{r['songs']}</b> mahnı növbənin əvvəlinə əlavə olundu"
                            + (f" · {r['in_depo']} artıq depoda" if r["in_depo"] else "")
                            + f"\n🎤 {r['artists']} ifaçı skanerdə prioritetlə")
                    if not filler.running:
                        note += "\n⚠️ Doldurucu söndürülüb — ▶️ ilə işə sal"
                except Exception as e:
                    note = f"❌ <code>{escape(str(e))[:200]}</code>"
            else:
                await cb.answer()
            await show(*countries_view(note))
            return
        elif action == "dddel":
            st = dd_state.get("pending")
            if not st or not st["dups"]:
                await cb.answer("Əvvəl yoxla", show_alert=True)
                return
            await cb.answer()
            await show(f"🗑 <b>{len(st['dups'])} dublikat post depodan silinsin?</b>\n\n"
                       "Əvvəl bütün video ID-lər saxlanılan (kanonik) posta bağlanır — heç bir mahnı itmir, "
                       "yalnız artıq nüsxələr silinir. Geri qaytarmaq olmur.",
                       InlineKeyboardMarkup(inline_keyboard=[
                           ui.confirm("df:dddelok", "df:dd", "sil")]))
            return
        elif action == "dddelok":
            st = dd_state.get("pending")
            if not st or not st["dups"]:
                await cb.answer("Əvvəl yoxla", show_alert=True)
                return
            await cb.answer("🗑 Silinir...")
            r = await asyncio.to_thread(filler.dedupe_link, st)

            async def prog(done, total):
                with contextlib.suppress(Exception):
                    await cb.message.edit_text(f"🗑 <b>Silinir...</b> {done}/{total}", parse_mode="HTML")
            n = await filler.dedupe_delete(st, prog)
            dd_state["pending"] = None
            await show(*dd_intro(f"✅ {r['linked']} video bağlandı, {r['repointed']} sətir yönəldi, "
                                 f"<b>{n}</b> dublikat post depodan silindi."))
            return
        elif action == "q":                                   # df:q:<r|f>:<səhifə>
            tab = parts[2] if len(parts) > 2 and parts[2] in ("r", "f") else "r"
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await cb.answer()
            await show(*queue_view(tab, page))
            return
        elif action == "qa" and len(parts) > 4:              # df:qa:<op>:<hash>:<səhifə>
            op, h = parts[2], parts[3]
            page = int(parts[4]) if parts[4].isdigit() else 0
            tab = "r" if op in ("rn", "rd") else "f"
            if op == "rn":
                t = filler.retry_now(h)
                filler.kick()
                msg = f"⚡ İndi sınanır: {t}" if t else "Artıq siyahıda yoxdur"
            elif op == "rd":
                t = filler.retry_drop(h)
                msg = f"🗑 Silindi: {t}" if t else "Artıq siyahıda yoxdur"
            elif op == "fr":
                t = await asyncio.to_thread(filler.failed_requeue_one, h)
                filler.kick()
                msg = f"🔁 Növbənin önünə: {t}" if t else "Artıq siyahıda yoxdur"
            elif op == "fd":
                t = await asyncio.to_thread(filler.failed_delete_one, h)
                msg = f"🗑 Silindi: {t}" if t else "Artıq siyahıda yoxdur"
            else:
                msg = ""
            await cb.answer(msg[:190])
            await filler.save_state(force=True)
            total = len(filler.retry) if tab == "r" else filler.failed_list(0)[0]
            page = min(page, max(0, (total - 1) // Q_PAGE))
            await show(*queue_view(tab, page))
            return
        elif action == "qb" and len(parts) > 2:              # toplu əməliyyatlar
            op, tab = parts[2], "r"
            if op == "rnow":
                n = len(filler.retry)
                for r in filler.retry:
                    r["next"] = 0
                filler.kick()
                note = f"⚡ {n} mahnı indi sınanır"
            elif op == "r2f":
                n = await asyncio.to_thread(filler.retry_to_failed)
                note, tab = f"❌ {n} mahnı alınmayanlar siyahısına köçürüldü", "f"
            elif op == "rdall":
                note = f"🗑 {filler.retry_drop_all()} mahnı təkrar növbəsindən silindi"
            elif op == "frall":
                n = await asyncio.to_thread(filler.requeue_failed)
                filler.kick()
                note, tab = f"🔁 {n} mahnı növbənin önünə qoyuldu", "f"
            elif op == "fdall":
                await asyncio.to_thread(filler.clear_failed)
                note, tab = "🗑 Alınmayanlar siyahısı təmizləndi", "f"
            else:
                note = ""
            await cb.answer(note[:190])
            await filler.save_state(force=True)
            await show(*queue_view(tab, 0, note))
            return
        elif action == "force":
            done = await filler.force()
            await filler.save_state(force=True)
            await cb.answer("⚡ Məcburi davam")
            text, kb = await panel_async()
            await show(text + "\n\n⚡ <b>Məcburi davam:</b>\n" + "\n".join(f"• {d}" for d in done), kb)
            return
        elif action == "failre":
            n = await asyncio.to_thread(filler.requeue_failed)
            await cb.answer(f"🔁 {n} mahnı növbənin önünə qoyuldu")
            filler.kick()
            await show(*queue_view("f", 0, f"🔁 {n} mahnı yenidən sınanacaq"))
            return
        elif action == "failclr":
            await asyncio.to_thread(filler.clear_failed)
            await cb.answer("🗑 Təmizləndi")
            await show(*queue_view("f", 0))
            return
        elif action == "retrynow":
            for r in filler.retry:
                r["next"] = 0
            filler.kick()
            await cb.answer(f"⚡ {len(filler.retry)} mahnı indi sınanacaq")
            await show(*queue_view("r", 0))
            return
        elif action == "hp":
            await cb.answer()
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            await show(*helpers_view(page=page))
            return
        elif action == "hpguide":
            await cb.answer()
            await show(*helpers_guide_view())
            return
        elif action == "hps" and len(parts) > 2:
            helpers.set_scope(parts[2])
            await cb.answer(depo_helpers.SCOPES.get(parts[2], ""))
            await show(*helpers_view())
            return
        elif action in ("pick", "hpr") and (len(parts) <= 2 or parts[2] == "hpr"):
            arg = parts[2] if len(parts) > 2 else ""
            if arg in ("+", "-"):
                v = helpers.step_rate(1 if arg == "+" else -1)
                await cb.answer(f"⏱ {v} mahnı/dəq. hər bot" + (" · ⚠️ 20-dən çox flood riski" if v > 20 else ""))
                await show(*helpers_view())
                return
            hp_rate_wait["until"] = 0
            await cb.answer()
            await show(*df_rate_picker_view())
            return
        elif action == "vstep" and len(parts) > 2:
            target = parts[2]
            delta = int(parts[3]) if len(parts) > 3 else 1
            bk = parts[4] if len(parts) > 4 else "p"
            back_url = "dt:start" if bk == "dt" else "df:panel"
            if target == "hpr":
                v = helpers.step_rate_delta(delta)
                await cb.answer(f"⏱ {v} mahnı/dəq. hər bot")
                await show(*df_rate_picker_view(f"✓ {v} mahnı/dəq. hər bot"))
            elif target == "d":
                v = filler.step_delay_delta(delta)
                await cb.answer(f"⏱ {v} san.")
                await show(*df_delay_picker_view(f"✓ Gözləmə intervalı: {v} san.", back=back_url))
            elif target == "w":
                v = filler.step_workers_delta(delta)
                await cb.answer(f"🧵 {workers_label(v)}")
                await show(*df_worker_picker_view(f"✓ Worker sayı: {workers_label(v)}", back=back_url))
            elif target == "i":
                cur = float(top_sessions.get((cb.message.chat.id, cb.message.message_id), {}).get("interval", get_top_interval()))
                new_val = max(0.5, min(60.0, round(cur + delta, 1)))
                set_top_interval(new_val)
                ts = top_sessions.get((cb.message.chat.id, cb.message.message_id))
                if ts:
                    ts["interval"] = new_val
                    ts["dirty"] = True
                await cb.answer(f"🔄 {new_val:g} san.")
                await show(*df_top_interval_picker_view(ts, f"✓ Yenilənmə intervalı: {new_val:g} san.", back=back_url))
            return
        elif action == "vinp" and len(parts) > 2:
            target = parts[2]
            bk = parts[3] if len(parts) > 3 else "p"
            back_url = "dt:start" if bk == "dt" else "df:panel"
            await cb.answer()
            if target == "hpr":
                hp_rate_wait.update(until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id)
                cur = helpers.rate
                text = (
                    "🤖 <b>Köməkçi botlar › Sürət limiti</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"Hazırkı sürət limiti: <b>{cur} mahnı/dəq. hər bot</b>\n\n"
                    "✍️ <b>Sürət limitini daxil edin:</b>\n"
                    "• <b>Rəqəm</b> yazın (məs: <code>15</code>) — 1 ilə 60 arası\n"
                    "• <b>+</b> yazın — maksimum (<code>60</code>)\n"
                    "• <b>-</b> yazın — minimum (<code>1</code>)\n"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "df:pick:hpr")]])
                await show(text, kb)
            elif target == "d":
                df_val_wait.update(target="d", until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id, back=back_url)
                cur = filler.delay
                text = (
                    "📦 <b>Depo doldurucu › Gözləmə intervalı</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"Hazırkı gözləmə intervalı: <b>{cur} san.</b>\n\n"
                    "✍️ <b>Gözləmə intervalını daxil edin:</b>\n"
                    f"• <b>Rəqəm</b> yazın (məs: <code>10</code>) — 0 ilə {MAX_DELAY} arası\n"
                    f"• <b>+</b> yazın — maksimum (<code>{MAX_DELAY}</code> san.)\n"
                    "• <b>-</b> yazın — minimum (<code>0</code> san.)\n"
                )
                cancel_target = f"df:pick:d:{bk}" if bk != "p" else "df:pick:d"
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                await show(text, kb)
            elif target == "w":
                df_val_wait.update(target="w", until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id, back=back_url)
                cur = filler.workers
                text = (
                    "📦 <b>Depo doldurucu › Worker sayı</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"Hazırkı worker sayı: <b>{cur} işçi</b> <i>({workers_label(cur)})</i>\n\n"
                    "✍️ <b>Worker sayını daxil edin:</b>\n"
                    f"• <b>Rəqəm</b> yazın (məs: <code>4</code>) — 1 ilə {MAX_WORKERS} arası\n"
                    f"• <b>+</b> yazın — maksimum (<code>{MAX_WORKERS}</code>)\n"
                    "• <b>-</b> yazın — minimum (<code>1</code>)\n"
                )
                cancel_target = f"df:pick:w:{bk}" if bk != "p" else "df:pick:w"
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                await show(text, kb)
            elif target == "i":
                df_val_wait.update(target="i", until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id, back=back_url)
                cur = float(top_sessions.get((cb.message.chat.id, cb.message.message_id), {}).get("interval", get_top_interval()))
                text = (
                    "📊 <b>Canlı monitor › Yenilənmə intervalı</b>\n━━━━━━━━━━━━━━━━━━\n"
                    f"Hazırkı yenilənmə intervalı: <b>{cur:g} san.</b>\n\n"
                    "✍️ <b>Yenilənmə intervalını daxil edin (saniyələrlə):</b>\n"
                    "• <b>Rəqəm</b> yazın (məs: <code>1</code>, <code>2</code>, <code>5</code>) — 0.5 ilə 60 arası\n"
                    "• <b>+</b> yazın — maksimum (<code>60</code> san.)\n"
                    "• <b>-</b> yazın — minimum (<code>0.5</code> san.)\n"
                )
                cancel_target = f"df:pick:i:{bk}" if bk != "p" else "df:pick:i"
                kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", cancel_target)]])
                await show(text, kb)
            return
        elif action == "hpadd":
            await cb.answer()
            hp_wait.update(until=time.time() + 300, chat_id=cb.message.chat.id, msg_id=cb.message.message_id)
            await show(*hp_add_view())
            return
        elif action == "hprm" and len(parts) > 2 and parts[2].isdigit():
            i = int(parts[2])
            name = helpers.helpers[i].name if i < len(helpers.helpers) else ""
            r = helpers.remove(i)
            if r == "ok":
                await helpers.reload()
            await cb.answer({"ok": f"🗑 {name} silindi", "range": "Siyahı dəyişib — yenilə",
                             "env": "config.env-dən silinməlidir"}[r])
            await show(*helpers_view())
            return
        elif action == "hpconv":
            v = helpers.toggle_conv()
            await cb.answer(depo_helpers.CONV_MODES[v])
            await show(*helpers_view())
            return
        elif action == "hpchk":
            await cb.answer("🔄 Yoxlanılır...")
            if len(helpers.tokens()) != len(helpers.helpers):
                await helpers.reload()
            else:
                await helpers.recheck()
            await show(*helpers_view("✅ Yoxlandı"))
            return
        elif action == "cleanprompt":
            clean_wait["until"] = time.time() + 300
            clean_wait["chat_id"] = cb.message.chat.id
            clean_wait["msg_id"] = cb.message.message_id
            await cb.answer()
            await show(*clean_prompt_view())
            return
        elif action == "cleango":
            pending = clean_pending.get(cb.from_user.id)
            if not pending or not pending.get("tracks"):
                await cb.answer("Sorğu müddəti bitib və ya mahnı tapılmadı", show_alert=True)
                await show(*clean_prompt_view())
                return
            await cb.answer("🗑 Silinir...")
            tracks = pending["tracks"]
            query = pending["query"]
            clean_pending.pop(cb.from_user.id, None)
            await show(f"⏳ <b>{len(tracks)} mahnı depodan və bazadan silinir...</b>\n<i>Zəhmət olmasa gözləyin...</i>", None)
            res = await delete_depo_tracks(bot, tracks, filler)
            text = (
                f"✅ <b>Depo təmizləndi!</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"🔍 Sorğu: <code>{escape(query)}</code>\n\n"
                f"🗑 <b>Depodan silindi:</b> {res['tg_deleted']} mesaj\n"
                f"💾 <b>Bazadan silindi:</b> {res['db_deleted']} sətir\n"
                f"🚫 <b>Qara siyahıya salındı:</b> {res['unique_vids']} mahnı (artıq yüklənməyəcək)\n"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [btn("🗑 Başqa sorğu ilə sil", "df:cleanprompt")],
                ui.nav("df:panel", "Doldurucu", close_data="df:close")
            ])
            await show(text, kb)
            return
        elif action == "cqlist":
            cq_add_wait["until"] = 0
            await cb.answer()
            await show(*country_queries_list_view())
            return
        elif action == "cq" and len(parts) > 2:
            cq_add_wait["until"] = 0
            cc = parts[2]
            await cb.answer()
            await show(*country_queries_view(cc))
            return
        elif action == "cqt" and len(parts) > 3 and parts[3].isdigit():
            cc = parts[2]
            idx = int(parts[3])
            new_st = filler.toggle_country_query(cc, idx)
            await cb.answer("✅ Aktivləşdirildi" if new_st else "❌ Söndürüldü")
            await show(*country_queries_view(cc))
            return
        elif action == "cqadd" and len(parts) > 2:
            cc = parts[2]
            cq_add_wait["until"] = time.time() + 300
            cq_add_wait["cc"] = cc
            cq_add_wait["chat_id"] = cb.message.chat.id
            cq_add_wait["msg_id"] = cb.message.message_id
            cname = COUNTRIES.get(cc, cc)
            text = (
                f"✍️ <b>{cname} ({cc}) üçün yeni axtarış sorğusu:</b>\n━━━━━━━━━━━━━━━━━━\n\n"
                f"Axtarış üçün istifadə olunacaq açar sözü yazın (məs: <code>{cname} Pop Hit</code>):\n\n"
                f"<i>Bu sorğu üzrə YouTube / YT Music-də playlistlər axtarılacaq və mahnılar depoya yüklənəcək.</i>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", f"df:cq:{cc}")]])
            await cb.answer()
            await show(text, kb)
            return
        elif action == "cqrst" and len(parts) > 2:
            cc = parts[2]
            filler.reset_country_queries(cc)
            await cb.answer("🔄 Default sorğular bərpa olundu")
            await show(*country_queries_view(cc, "Default sorğular bərpa olundu"))
            return
        elif action == "defsrc":
            filler.save_sources(list(DEFAULT_SOURCES))
            await cb.answer("Default mənbələr")
            await show(*sources_view())
            return
        else:
            await cb.answer()
        await show(*await panel_async())

    @dp.channel_post(F.audio)
    async def tg_source_post(post: types.Message):
        """Mənbə kanalına (bot orada admin olanda) yeni mahnı düşdü → dərhal növbəyə."""
        name = (post.chat.username or "").lower()
        srcs = filler.sources()
        if not any((s["kind"] == "tg_channel" and s["id"].lower() == name) or
                   (s["kind"] == "tg_chat" and s["id"] == str(post.chat.id)) for s in srcs):
            return
        a = post.audio
        res = clean_song(a.performer, a.title, a.file_name, post.caption, post.chat.title)
        if not res:
            return
        item = tg_item(res[0], res[1], a.duration or 0)
        if filler.add_front([item]):
            logger.info(f"📦 Kanal postu növbəyə: {item['title']}")

    # ── 📊 Canlı monitor (depo top) ──
    top_sessions = {}          # (chat_id, msg_id) -> {"paused", "view", "sort", "stop", "until", ...}
    context.depo_top_sessions = top_sessions

    def top_kb(sess, running=True):
        if not running:
            return InlineKeyboardMarkup(inline_keyboard=[
                [btn("▶️ Yenidən başlat", "dt:start")],
                ui.nav("df:panel", "Doldurucu", close_data="dt:close"),
            ])
        log = sess["view"] == "log"
        row1 = [btn("▶️ Davam" if sess["paused"] else "⏸ Fasilə", "dt:pause"),
                btn("🧵 İşçilər" if log else "📜 Jurnal", "dt:view")]
        if not log:
            row1.append(btn("↕️ #-yə görə" if sess["sort"] == "time" else "↕️ Vəziyyətə görə", "dt:sort"))
        pages = sess.get("pages", 1)
        pg_row = ([[btn(ui.PREV, "dt:pg:-"), btn(f"{sess.get('page', 0) + 1}/{pages}", "dt:noop"),
                    btn(ui.NEXT, "dt:pg:+")]] if not log and pages > 1 else [])
        cur_int = float(sess.get("interval", get_top_interval()))
        alt_btn = btn("⚡ 1 san.", "dt:set:i:1") if cur_int != 1 else btn("⏱ 3 san.", "dt:set:i:3")
        return InlineKeyboardMarkup(inline_keyboard=[
            row1,
            *pg_row,
            [btn(f"🧵 Worker: {filler.workers}", "dt:pick:w"), btn(f"⏱ Fasilə: {filler.delay} san.", "dt:pick:d")],
            [btn(f"🔄 İnterval: {cur_int:g} san.", "dt:pick:i"), alt_btn],
            [btn("⏸ Doldurucunu söndür", "dt:off") if filler.running else btn("▶️ Doldurucunu işə sal", "dt:on"),
             btn("⏹ Monitoru dayandır", "dt:stop")],
            ui.nav("df:panel", "Doldurucu", close_data="dt:close"),
        ])


    async def top_run(key, sess):
        chat_id, msg_id = key
        meter = ProcMeter()
        meter.sample()
        tz = bot_tz()
        last_text = last_kb = None
        next_tick = time.monotonic()
        try:
            while not sess["stop"]:
                if not sess["paused"] and time.time() >= sess["until"]:
                    break
                kb = top_kb(sess)
                sig = kb_sig(kb)
                text = None
                if not sess["paused"] or sess.pop("dirty", False):
                    proc = meter.sample() if not sess["paused"] else (sess.get("proc") or meter.sample())
                    sess["proc"] = proc
                    text = render_top(filler, sess, proc, datetime.now(tz).strftime("%H:%M:%S"))
                try:
                    if text and text != last_text:
                        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                    parse_mode="HTML", reply_markup=kb)
                        last_text, last_kb = text, sig
                    elif sig != last_kb:
                        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=kb)
                        last_kb = sig
                except Exception as e:
                    err = str(e).lower()
                    wait = getattr(e, "retry_after", None)
                    if wait:
                        # bu çatda başqa trafik də var (toplu yükləmə, mahnılar) — gözlə və 10 dəq. yavaş işlə
                        sess["slow_until"] = time.time() + TOP_SLOW_FOR
                        logger.info(f"depo top: flood limit, {wait} san. gözlənilir — sonra hər "
                                    f"{TOP_SLOW_INTERVAL:g} san.")
                        await asyncio.sleep(wait + 1)
                        next_tick = time.monotonic()
                    elif "not modified" in err:
                        last_text, last_kb = text or last_text, sig
                    elif "not found" in err or "can't be edited" in err:
                        break                                   # mesaj silinib
                    else:
                        logger.warning(f"depo top yenilənmədi: {e}")
                # saniyələrlə tənzimlənən interval addımı
                step = float(sess.get("interval", get_top_interval()))
                if time.time() < sess.get("slow_until", 0):
                    step = max(step, TOP_SLOW_INTERVAL)
                else:                                   # 📡 istifadəçi yükləmələri gedir → mahnılar axır, yavaş
                    try:
                        from common import activity as act
                        if act.active or (act.events and time.time() - act.events[-1][0] < 5):
                            step = max(step, TOP_SLOW_INTERVAL)
                    except Exception as _e:
                        logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:5228]: %r", _e)
                next_tick += step
                delay = next_tick - time.monotonic()
                if delay < 0:
                    next_tick = time.monotonic()
                    delay = 0
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            sess["closed"] = True
            raise
        finally:
            top_sessions.pop(key, None)
            if not sess.get("closed"):
                proc = sess.get("proc") or meter.sample()
                text = render_top(filler, {**sess, "paused": True}, proc,
                                  datetime.now(tz).strftime("%H:%M:%S")).replace("⏸ fasilə", "⏹ dayandı")
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text,
                                                parse_mode="HTML", reply_markup=top_kb(sess, running=False))
                except Exception as _e:
                    logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:5248]: %r", _e)

    def top_start(key, interval: float | None = None):
        for k, old in list(top_sessions.items()):     # bir çatda bir monitor
            if k[0] == key[0]:
                old["stop"] = True
                if k == key:
                    old["closed"] = True
        cur_int = float(interval) if interval is not None else get_top_interval()
        sess = {"paused": False, "view": "workers", "sort": "time", "stop": False,
                "until": time.time() + TOP_MAX_RUNTIME,
                "interval": cur_int}
        top_sessions[key] = sess
        sess["task"] = asyncio.create_task(top_run(key, sess))
        return sess

    @dp.message(Command("depotop"))
    async def depotop_cmd(message: types.Message, command: CommandObject):
        if not message.from_user or not is_creator(message.from_user.id):
            return
        args = (command.args or "").strip()
        custom_int = None
        if args:
            try:
                val = float(args.replace(",", "."))
                custom_int = max(0.5, min(60.0, round(val, 1)))
                set_top_interval(custom_int)
            except ValueError:
                pass
        cur_int = custom_int or get_top_interval()
        msg = await message.answer(f"📊 <i>depo top başlayır... ({cur_int:g} san. interval)</i>", parse_mode="HTML")
        top_start((msg.chat.id, msg.message_id), interval=cur_int)

    @dp.callback_query(F.data.startswith("dt:"))
    async def depotop_callback(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        action = parts[1]
        key = (cb.message.chat.id, cb.message.message_id)
        sess = top_sessions.get(key)

        if action == "start":
            await cb.answer("📊 Canlı monitor")
            top_start(key)
            return
        if action == "close":
            if sess:
                sess["closed"] = sess["stop"] = True
            await cb.answer()
            try:
                await cb.message.delete()
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:5290]: %r", _e)
            return
        if action == "noop":
            await cb.answer("➖ / ➕ ilə dəyiş")
            return
        async def show(text, kb):
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:5299]: %r", _e)

        def stop_for_view(ts):
            if ts:
                ts["closed"] = ts["stop"] = True
                t = ts.get("task")
                if t and not t.done():
                    t.cancel()

        # bu düymələr monitor dayansa da işləyir
        if (action in ("pick", "w") and len(parts) > 2 and parts[2] == "w") or (action == "w" and (len(parts) <= 2 or parts[2] not in ("+", "-"))):
            stop_for_view(sess)
            await cb.answer()
            await show(*df_worker_picker_view(back="dt:start"))
            return
        elif (action in ("pick", "d") and len(parts) > 2 and parts[2] == "d") or (action == "d" and (len(parts) <= 2 or parts[2] not in ("+", "-"))):
            stop_for_view(sess)
            await cb.answer()
            await show(*df_delay_picker_view(back="dt:start"))
            return
        elif (action in ("pick", "i") and len(parts) > 2 and parts[2] == "i") or (action == "i" and (len(parts) <= 2 or parts[2] not in ("+", "-"))):
            stop_for_view(sess)
            await cb.answer()
            await show(*df_top_interval_picker_view(sess, back="dt:start"))
            return
        elif action == "set" and len(parts) > 2 and parts[2] == "i" and len(parts) > 3:
            val = float(parts[3])
            val = max(0.5, min(60.0, round(val, 1)))
            set_top_interval(val)
            if sess:
                sess["interval"] = val
                sess["dirty"] = True
            await cb.answer(f"🔄 İnterval: {val:g} san.")
            if sess and not sess.get("stop"):
                return
            await show(*df_top_interval_picker_view(sess, f"✅ İnterval təyin edildi: {val:g} san.", back="dt:start"))
            return
        elif action == "vstep" and len(parts) > 2 and parts[2] == "i" and len(parts) > 3:
            delta = float(parts[3])
            cur = float(sess.get("interval", get_top_interval())) if sess else get_top_interval()
            new_val = max(0.5, min(60.0, round(cur + delta, 1)))
            set_top_interval(new_val)
            if sess:
                sess["interval"] = new_val
                sess["dirty"] = True
            await cb.answer(f"🔄 İnterval: {new_val:g} san.")
            await show(*df_top_interval_picker_view(sess, f"✓ Yenilənmə intervalı: {new_val:g} san.", back="dt:start"))
            return
        elif action == "w" and len(parts) > 2:
            cur = filler.workers
            v = filler.step_workers(1 if parts[2] == "+" else -1)
            if v == cur:
                await cb.answer("Minimum 1 işçi" if v == 1 else f"Maksimum {MAX_WORKERS} işçi")
            else:
                await cb.answer(f"🧵 {workers_label(v)}" + (" · ⚠️ CPU/RAM və YouTube 429 riski" if v > 32 else
                                                             " · ⚠️ flood riski" if v > 8 else ""))
        elif action == "d" and len(parts) > 2:
            v = filler.step_delay(1 if parts[2] == "+" else -1)
            await cb.answer(f"⏱ Fasilə: {v} san.")
        elif action == "on":
            filler.start()
            await cb.answer("🟢 Doldurucu işə salındı")
        elif action == "off":
            await cb.answer("🔴 Söndürülür — depoya gedən mahnılar bitirilir...")
            await filler.stop()
        elif not sess:
            await cb.answer("Monitor dayanıb — ▶️ ilə yenidən başlat")
            return
        elif action == "pause":
            sess["paused"] = not sess["paused"]
            if not sess["paused"]:
                sess["until"] = max(sess["until"], time.time() + 300)
            await cb.answer("⏸ Fasilə" if sess["paused"] else "▶️ Davam")
        elif action == "view":
            sess["view"] = "workers" if sess["view"] == "log" else "log"
            sess["dirty"] = True
            await cb.answer("📜 Son hadisələr" if sess["view"] == "log" else "🧵 İşçilər")
        elif action == "pg" and len(parts) > 2:
            n = sess.get("pages", 1)
            sess["page"] = (sess.get("page", 0) + (1 if parts[2] == "+" else -1)) % max(n, 1)
            sess["dirty"] = True
            await cb.answer(f"📄 {sess['page'] + 1}/{n}")
            return
        elif action == "sort":
            sess["sort"] = "idx" if sess["sort"] == "time" else "time"
            sess["dirty"] = True
            await cb.answer("Vəziyyətə görə" if sess["sort"] == "time" else "İşçi nömrəsinə görə")
        elif action == "stop":
            sess["stop"] = True
            await cb.answer("⏹ Monitor dayandırıldı")
            return
        else:
            await cb.answer()
            return
        if sess:
            sess["dirty"] = True

    class StopTopOnLeave:
        """Monitor mesajında 📦 panel / menyu düyməsi basılanda canlı yenilənmə dayansın (paneli üstələməsin)."""
        async def __call__(self, handler, event, data):
            d = getattr(event, "data", None) or ""
            if d.startswith(("df:", "menu:", "fx:")) and event.message:
                ts = top_sessions.get((event.message.chat.id, event.message.message_id))
                if ts:
                    ts["closed"] = ts["stop"] = True
                    t = ts.get("task")
                    if t and not t.done():
                        t.cancel()
            return await handler(event, data)

    dp.callback_query.outer_middleware(StopTopOnLeave())

    # ── 📱 Userbot komandaları ──
    @dp.message(Command("userbot"), F.from_user.id.func(is_creator))
    async def userbot_status(message: types.Message):
        try:
            from userbot.core.userbot_api import ff         # userbot/core-dakı terminal tərzli kart
        except Exception:
            ff = None
        title = f"userbot@{(await bot.get_me()).username}"
        mode = getattr(context, "telethon_session_mode", "?")
        if not await tg.ok():
            if ff:
                await message.answer(ff(title, [("Userbot", "○ aktiv deyil"),
                                                ("Həll", "serverdə login.py ilə sessiya yarat")]), parse_mode="HTML")
            else:
                await message.answer("🔴 <b>Userbot aktiv deyil!</b>\n"
                                     "<i>TELETHON_API_ID/HASH və serverdə login.py ilə yaradılmış sessiyanı yoxla.</i>",
                                     parse_mode="HTML")
            return
        me = tg.me = await tg.client.get_me()
        n = len(filler.tg_source_ids())
        if ff:
            name = " ".join(x for x in (me.first_name, me.last_name) if x) or "—"
            body = [("Userbot", "● aktiv"), ("Hesab", name), ("Username", f"@{me.username or '—'}"), ("ID", me.id),
                    ("Sessiya", "yaddaşda" if mode == "memory" else "fayl"),
                    "# Depo",
                    ("Doldurucu", "● işləyir" if filler.running else "○ söndürülüb"),
                    ("TG mənbələri", n),
                    ("Skan", "● gedir" if scan_state["running"] else "○ yoxdur"),
                    "# İpucu",
                    ("Skan", "/scan_chat <link> [say]"),
                    ("Panel", "istənilən çatda .menu"), ("Yoxla", ".alive")]
            await message.answer(ff(title, body, logo=True), parse_mode="HTML")
            return
        await message.answer(
            f"🟢 <b>Userbot aktivdir!</b>\n"
            f"👤 <b>Hesab:</b> {escape(me.first_name or '')} (@{escape(me.username or 'yoxdur')})\n"
            f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
            f"💾 <b>Sessiya:</b> {'yaddaşda' if mode == 'memory' else 'fayl'}\n"
            f"✈️ <b>Telegram mənbələri:</b> {n}\n\n"
            f"<i>Birdəfəlik skan:</i> <code>/scan_chat &lt;link/ID&gt; [say]</code>\n"
            f"⚡ <i>İstənilən çatda</i> <code>.alive</code> <i>yazaraq yoxla.</i>",
            parse_mode="HTML",
        )

    @dp.message(Command("scan_chat"), F.from_user.id.func(is_creator))
    async def scan_chat_cmd(message: types.Message, command: CommandObject):
        if not await tg.ok():
            await message.answer("🔴 Userbot aktiv deyil. /userbot", parse_mode="HTML")
            return
        target, limit = parse_tg_target(command.args or "")
        if not target:
            await message.answer(
                "ℹ️ <b>İstifadə:</b> <code>/scan_chat &lt;link_və_ya_id&gt; [audio_sayı]</code>\n\n"
                "• <code>/scan_chat https://t.me/+AbCdEfGhIjK</code> <i>(bütün tarixçə)</i>\n"
                "• <code>/scan_chat https://t.me/kanal_adi 300</code> <i>(son 300 audio)</i>\n"
                "• <code>/scan_chat -1001234567890 500</code>",
                parse_mode="HTML")
            return
        if scan_state["running"]:
            await message.answer("⚠️ Artıq skan gedir. Dayandırmaq: /stop_scan", parse_mode="HTML")
            return
        try:
            ent = await tg.entity(target)
        except Exception as e:
            await message.answer(f"❌ Çat açılmadı: <code>{escape(str(e))[:300]}</code>", parse_mode="HTML")
            return
        launch_scan(message.chat.id, ent, limit, "Birdəfəlik skan")

    @dp.callback_query(F.data == "scan:stop")
    async def cb_stop_scan(cb: types.CallbackQuery):
        if scan_state["running"]:
            scan_state["stop"] = True
            await cb.answer("⏹ Skan dayandırılır...")
        else:
            await cb.answer("Hazırda aktiv skan yoxdur.", show_alert=True)

    @dp.message(Command("stop_scan"), F.from_user.id.func(is_creator))
    async def cmd_stop_scan(message: types.Message):
        if scan_state["running"]:
            scan_state["stop"] = True
            await message.answer("⏹ <b>Skan dayandırılır...</b>", parse_mode="HTML")
        else:
            await message.answer("ℹ️ Hazırda aktiv skan yoxdur.", parse_mode="HTML")

    if HAS_TELETHON:
        # .alive / .info / .fastfetch / .clear — userbot/plugins/-dadır

        async def on_new_audio(event):
            """Yalnız mənbə kimi əlavə olunmuş çatlardakı yeni audiolar → növbənin əvvəlinə."""
            if not getattr(event.message, "media", None) or event.chat_id not in filler.tg_source_ids():
                return
            title = ""
            try:
                chat = await event.get_chat()
                title = getattr(chat, "title", "") or ""
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:5477]: %r", _e)
            item = tl_message_item(event.message, title)
            if item and filler.add_front([item]):
                logger.info(f"📥 Userbot: yeni audio növbəyə: {item['title']}")

        # ortaq client-ə qoşulur; userbot/core/accounts reload olsa yeni client-ə avtomatik köçür
        if events and hasattr(events, "NewMessage"):
            tg_session.add_handler(context, "depo:new_audio", on_new_audio, events.NewMessage())

    # 💾 Bot dayananda (Ctrl+C / systemctl stop — aiogram siqnalı tutur) növbə diskə yazılır
    if not getattr(context, "_depo_shutdown_hooked", False):
        context._depo_shutdown_hooked = True

        async def _depo_on_shutdown():
            f = getattr(context, "depo_filler", None)     # plugin yenidən yüklənibsə — ən təzəsi
            if f:
                if f.running:
                    await f.stop(persist=False, grace=25)    # on=1 qalır → restartdan sonra qaldığı yerdən
                else:
                    await f.save_state(force=True)
                logger.info(f"💾 Depo növbəsi yazıldı: {len(f.queue)} əsas + {len(f.ytm_queue)} YT Music"
                            f" + {len(f.inflight)} yarımçıq")

        dp.shutdown.register(_depo_on_shutdown)

    # Restartdan sonra: əvvəl açıq idisə davam et
    if filler.enabled:
        try:
            filler.start()
        except RuntimeError:              # event loop hələ işləmirsə
            pass

    # 🔄 Restartdan sonra yarımçıq qalmış sinxron və ya dublikat mesajlarını yenilə
    async def _recover_interrupted_tasks():
        await asyncio.sleep(2.5)
        try:
            raw_s = filler._get("active_sync")
            if raw_s:
                filler._set("active_sync", "")
                data = json.loads(raw_s)
                cid, mid = data.get("chat_id"), data.get("msg_id")
                if cid and mid:
                    text = ("⚠️ <b>Sinxronizasiya dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                            "Yarımçıq qalan sinxronizasiyanı davam etdirə və ya sıfırdan başlada bilərsiniz:")
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [btn("▶️ Davam et", "df:syncgo:quick"), btn("🔄 Yenidən başla", "df:syncgo:full")],
                        ui.nav("df:panel", "Doldurucu", close_data="df:close")
                    ])
                    with contextlib.suppress(Exception):
                        await bot.edit_message_text(chat_id=cid, message_id=mid, text=text, parse_mode="HTML", reply_markup=kb)
        except Exception as _e:
            logger.debug("recover active_sync: %r", _e)

        try:
            raw_d = filler._get("active_dedupe")
            if raw_d:
                filler._set("active_dedupe", "")
                data = json.loads(raw_d)
                cid, mid = data.get("chat_id"), data.get("msg_id")
                if cid and mid:
                    text = ("⚠️ <b>Dublikat yoxlaması dayandırıldı</b> <i>(bot yenidən başladıldı)</i>\n\n"
                            "Yoxlamanı yenidən başlada bilərsiniz:")
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [btn("🔄 Yenidən başla", "df:ddgo")],
                        ui.nav("df:panel", "Doldurucu", close_data="df:close")
                    ])
                    with contextlib.suppress(Exception):
                        await bot.edit_message_text(chat_id=cid, message_id=mid, text=text, parse_mode="HTML", reply_markup=kb)
        except Exception as _e:
            logger.debug("recover active_dedupe: %r", _e)

    try:
        spawn(_recover_interrupted_tasks())
    except Exception as _e:
        logger.debug("udulan xəta [bot/depo/handlers.py:2570]: %r", _e)

    logger.info("✅ Depo doldurucu + userbot plugin-i yükləndi (/depo, /depotop, /scan_chat, /userbot)")


async def teardown(context):
    """plugin_manager söndürəndə / yenidən yükləyəndə fon işini dayandırır (vəziyyət saxlanılır)."""
    for ts in list(getattr(context, "depo_top_sessions", {}).values()):
        ts["stop"] = True
    pool = getattr(context, "depo_helpers", None)
    if pool:
        await pool.close()
    ss = getattr(context, "depo_sync_state", None)
    if ss:
        ss["stop"] = True
        for k in ("auto", "task"):
            t = ss.get(k)
            if t and not t.done():
                t.cancel()
    dd = getattr(context, "depo_dedupe_state", None)
    if dd:
        dd["stop"] = True
        t = dd.get("task")
        if t and not t.done():
            t.cancel()
    scan = getattr(context, "depo_scan_state", None)
    if scan and scan.get("task") and not scan["task"].done():
        scan["stop"] = True
        scan["task"].cancel()
    filler = getattr(context, "depo_filler", None)
    if filler:
        await filler.stop(persist=False)
    tg_session.remove_handler(context, "depo:new_audio")
    tg = getattr(context, "depo_userbot", None)
    if tg and tg.own_client:                   # client-i bu plugin yaradıbsa (userbot/core/accounts yoxdursa)
        if tg.task and not tg.task.done():
            tg.task.cancel()
        if tg.client.is_connected():
            await tg.client.disconnect()
            logger.info("🛑 Telethon userbot bağlantısı kəsildi.")
        context.telethon_client = None
