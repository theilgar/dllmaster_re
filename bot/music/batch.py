"""
bot.music.batch
Toplu yükləmə axını (Batch Downloader):
- Paralel / ardıcıl yükləmə növbəsi
- Canlı irəliləyiş göstəricisi (ticker) və sürət hesablaması
- Yarımçıq qalmış yükləmələrin bərpası (bjob_save, notify_orphans)
- Yekun hesabat və uğursuz olanların təkrar cəhdi (retry)
"""

import asyncio
import contextlib
import itertools
from html import escape
import json
import logging
import os
import sys
import threading
import time

from aiogram import F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.core import audio_cache, youtube_handler as yt_handler
from bot.core.youtube_handler import DownloadCancelled
from common import activity
from common.database import get_db, is_maintenance_mode
from common.formatting import fmt_bytes, fmt_duration, fmt_duration_words, fmt_rate
from common.i18n import _ as tr
from common.utilities import spawn, ui_styled

logger = logging.getLogger(__name__)

BATCH_SRC = {"pl": "pl", "sp": "sp", "art": "art", "bat": "bat"}

STAGE_LABEL = {
    "WAIT": "🕓 Növbədə",
    "CHK": "🔎 Depo yoxlanır",
    "DL": "⬇️ YouTube → server",
    "UP": "⬆️ Server → depo",
    "SEND": "📤 Depo → çat",
    "FLOOD": "⏳ Telegram flood",
}

BJOB_TTL = 3 * 86400

_sys = sys.modules[__name__]
BOOT = getattr(_sys, "_dllm_boot", None)
if not BOOT:
    BOOT = f"{os.getpid()}:{int(time.time())}"
    _sys._dllm_boot = BOOT

batch_jobs = {}
retry_store = {}
retry_ids = itertools.count(1)


def speed_line(cnt: dict, stages: dict = None) -> str:
    """📶 orta sürət (bitmiş fayllar üzrə, hər fayl ayrıca ölçülür): YouTube → server · server → depo."""
    dl_b = cnt.get("dl_b", 0)
    dl_s = cnt.get("dl_s", 0)
    up_b = cnt.get("up_b", 0)
    up_s = cnt.get("up_s", 0)
    for d in (stages or {}).values():
        if d.get("dl_bytes") and d.get("dl_secs"):
            dl_b, dl_s = dl_b + d["dl_bytes"], dl_s + d["dl_secs"]
        if d.get("up_bytes") and d.get("up_secs"):
            up_b, up_s = up_b + d["up_bytes"], up_s + d["up_secs"]
    if not dl_b and not up_b:
        return ""
    return f"📶 ⬇️ {fmt_rate(dl_b, dl_s)} · ⬆️ {fmt_rate(up_b, up_s)}"


def batch_rows(job: dict) -> list[list[InlineKeyboardButton]]:
    """Alt görünüşdə (kart) əlavə olunan sətirlər: 📊 Proses + ⏹ Dayandır."""
    return [
        [
            InlineKeyboardButton(text=tr("📊 Proses"), callback_data="bview:prog"),
            InlineKeyboardButton(text=tr("⏹ Dayandır"), callback_data="dlall_stop"),
        ]
    ]


def batch_summary(
    header: str,
    ok: int,
    failed: int,
    total: int,
    stopped: bool,
    cnt: dict,
    sent: dict,
    took: float,
    errors: list,
    uid: int,
    source: str,
    failed_items: list,
    left_items: list,
) -> tuple[str, InlineKeyboardMarkup | None]:
    if stopped:
        head = tr("⏹ <b>Dayandırıldı</b>")
    elif failed:
        head = tr("⚠️ <b>Bitdi, amma bəziləri alınmadı</b>")
    else:
        head = tr("✅ <b>Hamısı göndərildi</b>")

    lines = [
        f"{head} — {escape(header)}",
        "",
        tr("🎵 <b>{n}</b> mahnı göndərildi", n=ok) + f" / {total}",
        tr("⏱ Musiqi: <b>{d}</b>", d=fmt_duration_words(sent.get("dur", 0))),
        tr("💾 Həcm: <b>{s}</b>", s=fmt_bytes(sent.get("bytes", 0))),
        tr("📦 Depodan (keşdən): {n} · {s}", n=cnt.get("cached", 0), s=fmt_bytes(sent.get("c_bytes", 0))),
        tr("⬇️ YouTube-dan → depoya əlavə olundu: {n} · {s}", n=cnt.get("dl", 0), s=fmt_bytes(sent.get("d_bytes", 0))),
        tr("⌛ Vaxt: {t}", t=fmt_duration(took)),
    ]
    sp = speed_line(cnt)
    if sp:
        lines.append(sp.replace("📶", "📶 " + tr("Orta sürət:")))
    if cnt.get("flood_n"):
        lines.append(tr("🐢 Flood gözləmələri: {n} dəfə · {s} san.", n=cnt["flood_n"], s=int(cnt.get("flood_s", 0))))
    if errors:
        lines += ["", tr("❌ <b>Alınmadı: {n}</b>", n=len(errors))]
        lines += [f"• {escape(t[:50])}" for t in errors[:10]]
        if len(errors) > 10:
            lines.append("<i>" + tr("… və daha {n}", n=len(errors) - 10) + "</i>")
    if stopped and left_items:
        lines.append("\n" + tr("⏸ Yüklənməmiş qaldı: {n}", n=len(left_items)))

    rows = []
    if failed_items:
        t = next(retry_ids)
        retry_store[t] = {
            "items": failed_items,
            "header": header,
            "uid": uid,
            "source": source,
            "ts": time.time(),
        }
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr("🔁 Alınmayanları yenidən yüklə ({n})", n=len(failed_items)),
                    callback_data=f"bretry:{t}",
                )
            ]
        )
    if stopped and left_items:
        t = next(retry_ids)
        retry_store[t] = {
            "items": left_items,
            "header": header,
            "uid": uid,
            "source": source,
            "ts": time.time(),
        }
        rows.append(
            [
                InlineKeyboardButton(
                    text=tr("▶️ Qalanları yüklə ({n})", n=len(left_items)),
                    callback_data=f"bretry:{t}",
                )
            ]
        )
    if rows:
        rows.append([InlineKeyboardButton(text=tr("✖️ Bağla"), callback_data="bretry:x")])

    for k in [k for k, v in retry_store.items() if time.time() - v["ts"] > 86400]:
        retry_store.pop(k, None)

    return "\n".join(lines), (InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def bjob_save(jid: str, data: dict):
    with contextlib.suppress(Exception):
        get_db().set_setting(f"bjob:{jid}", json.dumps(data, ensure_ascii=False))


def bjob_done(jid: str, done: set):
    with contextlib.suppress(Exception):
        get_db().set_setting(f"bjob:{jid}:done", json.dumps(sorted(done)))


def bjob_drop(jid: str):
    with contextlib.suppress(Exception):
        get_db().set_setting(f"bjob:{jid}", None)
        get_db().set_setting(f"bjob:{jid}:done", None)


def bjob_load(jid: str):
    try:
        data = json.loads(get_db().get_setting(f"bjob:{jid}") or "null")
        done = set(json.loads(get_db().get_setting(f"bjob:{jid}:done") or "[]"))
        return data, done
    except Exception:
        return None, set()


def bjob_orphans() -> list:
    """Başqa prosesdən (restartdan əvvəl) qalan yarımçıq yükləmələr."""
    try:
        rows = get_db().sql_all("SELECT key FROM settings WHERE key LIKE 'bjob:%%' AND key NOT LIKE '%%:done'")
    except Exception:
        return []
    out = []
    for (key,) in rows:
        jid = key.split(":", 1)[1]
        data, done = bjob_load(jid)
        if not data:
            continue
        if time.time() - data.get("ts", 0) > BJOB_TTL:
            bjob_drop(jid)
            continue
        if data.get("boot") != BOOT and not data.get("notified"):
            out.append((jid, data, done))
    return out


def register_batch_system(
    dp,
    context,
    prepare_track_fn,
    deliver_track_fn,
    maybe_send_tip_prompt_fn,
    video_id_from_url_fn,
    log_download_fn,
):
    """Toplu yükləmə sistemini qeydiyyatdan keçirir və context-ə bağlayır."""

    def music_batch_rows(chat_id, msg_id) -> list:
        job = batch_jobs.get((chat_id, msg_id))
        return batch_rows(job) if job else []

    def music_batch_alt(chat_id, msg_id):
        job = batch_jobs.get((chat_id, msg_id))
        if job:
            job["mode"] = "alt"

    context.music_batch_rows = music_batch_rows
    context.music_batch_alt = music_batch_alt
    context.music_batch_busy = lambda uid: uid != context.creator_id and any(
        j["owner"] == uid for j in batch_jobs.values()
    )

    async def notify_orphans():
        await asyncio.sleep(2)
        for jid, data, done in await asyncio.to_thread(bjob_orphans):
            chat_id = data.get("chat_id")
            msg_id = data.get("msg_id")
            if not msg_id and "_" in jid:
                with contextlib.suppress(ValueError, IndexError):
                    msg_id = int(jid.split("_")[1])
            left_cnt = len(data["items"]) - len(done)
            if left_cnt <= 0:
                await asyncio.to_thread(bjob_drop, jid)
                continue
            text = (
                tr("♻️ <b>Yarımçıq qalmış yükləmə</b> — bot yenidən başladı")
                + f"\n\n📂 <b>{escape(data.get('header') or 'Toplu yükləmə')}</b>"
                + f"\n📊 {len(done)}/{len(data['items'])} göndərilmişdi · "
                + tr("<b>{n}</b> mahnı qalıb", n=left_cnt)
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=tr("▶️ Davam etdir ({n})", n=left_cnt),
                            callback_data=f"bresume:{jid}",
                        ),
                        InlineKeyboardButton(text=tr("✖️ Ləğv et"), callback_data=f"bresume:x:{jid}"),
                    ]
                ]
            )
            try:
                thread = data.get("thread")
                await context.bot.send_message(
                    chat_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    message_thread_id=thread if thread else None,
                    disable_web_page_preview=True,
                )
                data["notified"] = True
                await asyncio.to_thread(bjob_save, jid, data)
            except Exception as e:
                logger.debug(f"notify_orphans {jid}: {e}")

    spawn(notify_orphans())

    @dp.callback_query(F.data.startswith("bresume:"))
    async def bresume_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        if parts[1] == "x":
            data, _ = await asyncio.to_thread(bjob_load, parts[2])
            if data and cb.from_user.id not in (data.get("uid"), context.creator_id):
                await cb.answer("⛔", show_alert=True)
                return
            await asyncio.to_thread(bjob_drop, parts[2])
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return
        jid = parts[1]
        data, done = await asyncio.to_thread(bjob_load, jid)
        if not data:
            await cb.answer(tr("Köhnəlib və ya artıq davam etdirilib"), show_alert=True)
            return
        if cb.from_user.id not in (data.get("uid"), context.creator_id):
            await cb.answer(tr("⛔ Yalnız yükləməni başladan"), show_alert=True)
            return
        busy = getattr(context, "music_batch_busy", None)
        if busy and busy(cb.from_user.id):
            await cb.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"), show_alert=True)
            return
        left = [it for j, it in enumerate(data["items"]) if j not in done]
        await asyncio.to_thread(bjob_drop, jid)
        await cb.answer(tr("▶️ {n} mahnı yüklənir...", n=len(left)))
        with contextlib.suppress(Exception):
            await cb.message.edit_reply_markup(reply_markup=None)
        status = await cb.message.answer("⏳", parse_mode="HTML")
        await run_batch(
            status,
            left,
            data.get("header") or "♻️",
            data["uid"],
            data.get("source") or "bat",
            single=bool(data.get("single")) and len(left) == 1,
        )

    @dp.callback_query(F.data.startswith("bview:"))
    async def batch_view_cb(cb: types.CallbackQuery):
        job = batch_jobs.get((cb.message.chat.id, cb.message.message_id))
        if not job:
            await cb.answer(tr("Yükləmə artıq bitib"))
            return
        if cb.from_user.id not in (job["owner"], context.creator_id):
            await cb.answer("⛔", show_alert=True)
            return
        mode = cb.data.split(":", 1)[1]
        await cb.answer()
        if mode == "alt" and job.get("alt"):
            job["mode"] = "alt"
            try:
                text, kb = job["alt"]()
                kb = InlineKeyboardMarkup(inline_keyboard=list(kb.inline_keyboard) + batch_rows(job))
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
            except Exception as e:
                logger.debug(f"bview alt: {e}")
        else:
            job["mode"] = "prog"
            job["last_text"] = None
            job["kick"].set()

    @dp.callback_query(F.data.startswith("bretry:"))
    async def batch_retry_cb(cb: types.CallbackQuery):
        key = cb.data.split(":", 1)[1]
        if key == "x":
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.edit_reply_markup(reply_markup=None)
            return
        job = retry_store.get(int(key)) if key.isdigit() else None
        if not job:
            await cb.answer(tr("Köhnəlib — yenidən başlat"), show_alert=True)
            return
        if cb.from_user.id not in (job["uid"], context.creator_id):
            await cb.answer(tr("⛔ Yalnız yükləməni başladan"), show_alert=True)
            return
        busy = getattr(context, "music_batch_busy", None)
        if busy and busy(cb.from_user.id):
            await cb.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"), show_alert=True)
            return
        retry_store.pop(int(key), None)
        await cb.answer(tr("🔁 {n} mahnı yenidən yüklənir...", n=len(job["items"])))
        with contextlib.suppress(Exception):
            await cb.message.edit_reply_markup(reply_markup=None)
        status = await cb.message.answer("⏳", parse_mode="HTML")
        await run_batch(status, [dict(it) for it in job["items"]], f"🔁 {job['header']}", job["uid"], job["source"])

    @dp.callback_query(F.data == "dlall_stop")
    async def handle_download_all_stop(callback: types.CallbackQuery):
        if not callback.message:
            await callback.answer()
            return
        key = (callback.message.chat.id, callback.message.message_id)
        job = batch_jobs.get(key)
        if not job:
            await callback.answer(tr("Artıq bitib və ya dayandırılıb"))
            return
        if callback.from_user.id not in (job["owner"], context.creator_id):
            await callback.answer(tr("⛔ Yalnız yükləməni başladan dayandıra bilər"), show_alert=True)
            return
        job["event"].set()
        await callback.answer(tr("⏹ Dayandırılır..."))

    async def _run_batch(
        status_msg: types.Message,
        items: list,
        header: str,
        user_id: int,
        source: str,
        skipped: int = 0,
        original_message_id: int = None,
        single: bool = False,
        alt_view=None,
        alt_label: str = None,
    ):
        chat_id = status_msg.chat.id
        event = threading.Event()
        job_key = (chat_id, status_msg.message_id)
        job = batch_jobs[job_key] = {
            "event": event,
            "owner": user_id,
            "mode": "prog",
            "alt": alt_view,
            "kick": asyncio.Event(),
            "last_text": None,
        }
        delay = 1.0 if status_msg.chat.type == "private" else 3.2
        if status_msg.chat.type != "private":
            with contextlib.suppress(Exception):
                delay = 3.2 / (1 + context.depo_helpers.group_count(chat_id))

        ok = failed = 0
        errors = []
        note = "\n<i>" + tr("⏭ {n} uzun video (15 dəq.+) ötürüldü", n=skipped) + "</i>" if skipped else ""
        if is_maintenance_mode() and user_id != getattr(context, "creator_id", 0):
            note += "\n" + tr("⚠️ <i>Botda texniki sazlama işləri aparılır.</i>")

        jid = f"{chat_id}_{status_msg.message_id}"
        thread = status_msg.message_thread_id if getattr(status_msg, "is_topic_message", False) else None
        await asyncio.to_thread(
            bjob_save,
            jid,
            {
                "boot": BOOT,
                "ts": time.time(),
                "chat_id": chat_id,
                "thread": thread,
                "uid": user_id,
                "header": header,
                "source": source,
                "single": single,
                "msg_id": status_msg.message_id,
                "items": [
                    {
                        k: v
                        for k, v in it.items()
                        if k in ("title", "url", "query", "raw_duration", "meta", "thumb", "original_title")
                    }
                    for it in items
                ],
            },
        )
        saved = {"t": time.time(), "n": 0}
        finished = False

        par = 1 if single else yt_handler.batch_parallel()
        lane = None
        try:
            from bot.core import cpu_pool

            lane = cpu_pool.LANES["user"]
            if lane.max_size > 1:
                par = max(1, min(par, lane.max_size - 1))
        except Exception as _e:
            logger.debug("udulan xəta [bot/music/batch.py]: %r", _e)

        sem = asyncio.Semaphore(par)
        window = max(1, par * 2)
        prep = {}
        mine = {"running": 0}

        stages = {}
        cnt = {
            "cached": 0,
            "dl": 0,
            "flood_n": 0,
            "flood_s": 0,
            "dl_b": 0,
            "dl_s": 0.0,
            "up_b": 0,
            "up_s": 0.0,
        }
        t_start = time.time()

        async def prepare(j, item):
            activity.set_stage(stages[j])
            async with sem:
                if event.is_set():
                    raise DownloadCancelled("İstifadəçi dayandırdı")
                activity.stage("CHK")
                for _ in range(20):
                    if lane is None or not mine["running"] or not getattr(lane, "waiting", 0):
                        break
                    await asyncio.sleep(0.5)
                mine["running"] += 1
                try:
                    res = await prepare_track_fn(item, event, user_id=user_id, chat_id=chat_id)
                    activity.stage("READY", cached=bool(res.get("cached")))
                    return res
                finally:
                    mine["running"] -= 1

        def schedule(upto: int):
            for j in range(upto):
                if j < len(items) and j not in prep and j not in done_idx:
                    stages[j] = {"phase": "WAIT", "since": time.time()}
                    prep[j] = asyncio.create_task(prepare(j, items[j]))

        done_idx = set()
        pos = {"i": 0}

        def render() -> str:
            now = time.time()
            i = pos["i"]
            live = []
            for j in sorted(stages):
                d = stages[j]
                ph = d.get("phase")
                if ph not in ("CHK", "DL", "UP", "SEND", "FLOOD"):
                    continue
                if ph == "FLOOD":
                    lbl = tr("⏳ Telegram flood — {n} san. gözləyir", n=max(0, int(d.get("until", now) - now)))
                else:
                    lbl = tr(STAGE_LABEL[ph]) + f" · {fmt_duration(now - d['since'])}"
                live.append(f"{lbl}\n   <i>{escape(items[j]['title'][:55])}</i>")
            if single:
                return (
                    tr("⏳ <b>Yüklənir:</b>")
                    + f"\n{escape(items[0]['title'][:80])}"
                    + (f"\n\n{live[0].split(chr(10))[0]}" if live else "")
                )
            done = ok + failed
            busy = mine["running"]
            lines = [
                tr("⬇️ <b>Yüklənir</b>") + f" — {escape(header)}",
                "",
                f"📊 {done}/{len(items)} · ✅ {ok} · ❌ {failed}",
                "📦 "
                + tr("depodan")
                + f" {cnt['cached']} · ⬇️ YouTube "
                + f"{cnt['dl']}"
                + (f" · ⚙️ {busy}/{par}" if par > 1 else ""),
            ]
            el = now - t_start
            eta = f" · ~{fmt_duration(el / done * (len(items) - done))} " + tr("qalıb") if done else ""
            lines.append(f"⏱ {fmt_duration(el)}" + eta)
            sp = speed_line(cnt, stages)
            if sp:
                lines.append(sp)
            fl_n = cnt["flood_n"] + sum(stages[j].get("flood_n", 0) for j in stages if j >= i)
            fl_s = cnt["flood_s"] + sum(stages[j].get("flood_s", 0) for j in stages if j >= i)
            if fl_n:
                lines.append(tr("🐢 Flood gözləmələri: {n} dəfə · {s} san.", n=fl_n, s=int(fl_s)))
            if live:
                lines.append("")
                lines += live[:4]
            return "\n".join(lines) + note

        def prog_kb():
            rows = [[InlineKeyboardButton(text=tr("⏹ Dayandır"), callback_data="dlall_stop")]]
            if alt_view:
                rows[0].append(InlineKeyboardButton(text=alt_label or tr("↩️ Geri"), callback_data="bview:alt"))
            return InlineKeyboardMarkup(inline_keyboard=rows)

        tick_every = 2.5 if status_msg.chat.type == "private" else 5.0

        async def ticker():
            while True:
                if job["mode"] == "prog":
                    text = render()
                    if text != job["last_text"]:
                        try:
                            await status_msg.edit_text(text, parse_mode="HTML", reply_markup=prog_kb())
                            job["last_text"] = text
                        except Exception as e:
                            wait = getattr(e, "retry_after", None)
                            if wait:
                                await asyncio.sleep(wait + 1)
                            elif "not modified" in str(e):
                                job["last_text"] = text
                job["kick"].clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(job["kick"].wait(), tick_every)

        tick_task = asyncio.create_task(ticker())
        sent = {"dur": 0, "bytes": 0, "c_bytes": 0, "d_bytes": 0}
        failed_items = []
        pinned = False
        if not single and len(items) > 1:
            with contextlib.suppress(Exception):
                await status_msg.pin(disable_notification=True)
                pinned = True

        def _size_of(item, res) -> int:
            if res.get("path"):
                with contextlib.suppress(OSError):
                    return os.path.getsize(res["path"])
            with contextlib.suppress(Exception):
                c = audio_cache.get_cached(res.get("vid") or video_id_from_url_fn(item.get("url"))) or {}
                return int(c.get("size") or 0)
            return 0

        status_msg_deleted = False
        try:
            for i, item in enumerate(items):
                if event.is_set():
                    break
                schedule(i + window)
                pos["i"] = i
                try:
                    res = await prep.pop(i)
                    done_idx.add(i)
                    cnt["cached" if res.get("cached") else "dl"] += 1
                    size = await asyncio.to_thread(_size_of, item, res)
                    st_tok = activity.set_stage(stages[i])
                    try:
                        artist, track = await deliver_track_fn(status_msg, item, res, user_id, event)
                    finally:
                        activity._STAGE.reset(st_tok)
                    ok += 1
                    sent["dur"] += int(item.get("raw_duration") or 0)
                    sent["bytes"] += size
                    sent["c_bytes" if res.get("cached") else "d_bytes"] += size
                    try:
                        await log_download_fn(user_id, f"{artist} - {track}", item["url"], source, chat_id=chat_id)
                    except Exception as e:
                        logger.warning(f"log_download error: {e}")
                except DownloadCancelled:
                    break
                except Exception as e:
                    done_idx.add(i)
                    failed += 1
                    errors.append(item["title"])
                    failed_items.append(item)
                    logger.warning(f"Yükləmə xətası ({item.get('url') or item.get('query')}): {e}")
                finally:
                    d = stages.pop(i, {})
                    cnt["flood_n"] += d.get("flood_n", 0)
                    cnt["flood_s"] += d.get("flood_s", 0)
                    for k_b, k_s, src_b, src_s in (
                        ("dl_b", "dl_s", "dl_bytes", "dl_secs"),
                        ("up_b", "up_s", "up_bytes", "up_secs"),
                    ):
                        if d.get(src_b) and d.get(src_s):
                            cnt[k_b] += d[src_b]
                            cnt[k_s] += d[src_s]
                    if len(done_idx) - saved["n"] >= 5 or time.time() - saved["t"] > 10:
                        saved.update(t=time.time(), n=len(done_idx))
                        await asyncio.to_thread(bjob_done, jid, set(done_idx))
                if not single:
                    await asyncio.sleep(delay)
            finished = True
        finally:
            if single or finished or event.is_set():
                await asyncio.to_thread(bjob_drop, jid)
            else:
                with contextlib.suppress(BaseException):
                    bjob_done(jid, set(done_idx))
            tick_task.cancel()
            with contextlib.suppress(BaseException):
                await tick_task
            batch_jobs.pop(job_key, None)
            for t in prep.values():
                t.cancel()
            for r in await asyncio.gather(*prep.values(), return_exceptions=True):
                if isinstance(r, dict) and r.get("path"):
                    with contextlib.suppress(OSError):
                        os.remove(r["path"])
            prep.clear()
            if single and not finished and not status_msg_deleted:
                with contextlib.suppress(Exception):
                    await status_msg.delete()
                status_msg_deleted = True

        stopped = event.is_set()
        if single:
            if failed and not ok:
                with contextlib.suppress(Exception):
                    await status_msg.edit_text(
                        tr("❌ <b>Yüklənmədi:</b>") + f" {escape(errors[0][:80])}",
                        parse_mode="HTML",
                    )
                await asyncio.sleep(3)
            elif stopped and not ok:
                with contextlib.suppress(Exception):
                    await status_msg.edit_text(tr("⏹ <b>Yükləmə dayandırıldı</b>"), parse_mode="HTML")
                await asyncio.sleep(2)
            if not status_msg_deleted:
                with contextlib.suppress(Exception):
                    await status_msg.delete()
                status_msg_deleted = True
            if ok:
                spawn(maybe_send_tip_prompt_fn(status_msg.bot, chat_id, user_id))
            return ok

        if pinned:
            with contextlib.suppress(Exception):
                await status_msg.bot.unpin_chat_message(chat_id, message_id=status_msg.message_id)
        left_items = [it for j, it in enumerate(items) if j not in done_idx]
        text, kb = batch_summary(
            header,
            ok,
            failed,
            len(items),
            stopped,
            cnt,
            sent,
            time.time() - t_start,
            errors,
            user_id,
            source,
            failed_items,
            left_items,
        )
        for attempt in range(3):
            try:
                with ui_styled("report"):
                    await status_msg.bot.send_message(
                        chat_id,
                        text + note,
                        parse_mode="HTML",
                        reply_markup=kb,
                        message_thread_id=getattr(status_msg, "message_thread_id", None)
                        if getattr(status_msg, "is_topic_message", False)
                        else None,
                        disable_web_page_preview=True,
                    )
                break
            except Exception as e:
                wait = getattr(e, "retry_after", None)
                if not wait or attempt == 2:
                    logger.warning(f"Toplu yükləmə hesabatı göndərilmədi: {e}")
                    break
                await asyncio.sleep(wait + 1)
        with contextlib.suppress(Exception):
            await status_msg.delete()
        if original_message_id:
            with contextlib.suppress(Exception):
                await status_msg.bot.delete_message(chat_id, original_message_id)
        if ok:
            spawn(maybe_send_tip_prompt_fn(status_msg.bot, chat_id, user_id))
        return ok

    async def run_batch(
        status_msg: types.Message,
        items: list,
        header: str,
        user_id: int,
        source: str,
        skipped: int = 0,
        original_message_id: int = None,
        single: bool = False,
        alt_view=None,
        alt_label: str = None,
    ):
        with activity.using(BATCH_SRC.get(source, "bat"), user_id):
            return await _run_batch(
                status_msg,
                items,
                header,
                user_id,
                source,
                skipped=skipped,
                original_message_id=original_message_id,
                single=single,
                alt_view=alt_view,
                alt_label=alt_label,
            )

    context.music_run_batch = run_batch
    return run_batch
