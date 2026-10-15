"""
🎶 Playlistlər — bot/plugins/playlist_plugin.py

İstifadəçi (şəxsi çat):
  /playlist (/pl, /playlists)  — 🎶 Playlistlərim: yarat, aç, adını dəyiş, sil
  ➕ düyməsi mahnının altında  — mahnını playlistə əlavə et (1 playlist varsa birbaşa, çoxdursa seçim)
  /add (mahnıya reply)         — bot göndərdiyi mahnını sonradan da əlavə etmək (düymələr gizlənəndən sonra)
  💾 Playlist kimi saxla        — YouTube / Spotify playlist linkinin kartında (links_plugin)
  Playlistin içində: 🎵 tək mahnı · ⬇️ hamısını yüklə · 🔀 qarışıq · ✏️ ad · ✂️ mahnı sil · 🗑 sil

Creator: /menu → 💎 Premium → 🎛 İmkanlar
  • 🎶 / 💾 / ▶️ imkanları və 🔢 limitlər — 👑 Creator · 💎 Premium · 🆓 Free (features_plugin)
  • 🎶 Playlist statistikası — bu plugin: say, 🏆 ən böyük playlistlər
"""
import asyncio
import contextlib
import json
import logging
import random
import re
import time
from html import escape

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup

from common import tiers
from common.database import get_db
from common.i18n import _ as tr
from bot.core.panel import btn, pager, show as panel_show

logger = logging.getLogger(__name__)

PRIORITY = 74                     # menu (50) və digərlərindən sonra — menu_waiting_text zəncirinə qoşulur
PAGE = 8                          # playlistdə bir səhifədə neçə mahnı
WAIT_TTL = 300                    # ad gözləmə müddəti (san.)
NAME_MAX = 48
PICK_TTL = 600




def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def vid_of(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def item_key(item: dict) -> str:
    vid = vid_of(item.get("url"))
    if vid:
        return vid
    return "q:" + (item.get("query") or item.get("title") or "").strip().lower()[:200]


def clean_item(item: dict) -> dict:
    """run_batch-in istifadə etdiyi sahələr — JSON-a sığan, artıq heç nə."""
    out = {k: item.get(k) for k in ("title", "url", "query", "raw_duration", "meta", "thumb", "sp_id")
           if item.get(k) not in (None, "")}
    out["title"] = (out.get("title") or "?")[:200]
    return out


# ═════════════════════════ 🗄 baza ═════════════════════════
def db_lists(uid) -> list:
    return get_db().sql_all(
        """SELECT p.id, p.name, p.source, COUNT(i.id) AS n FROM playlists p
           LEFT JOIN playlist_items i ON i.playlist_id = p.id
           WHERE p.user_id = %s GROUP BY p.id ORDER BY p.updated DESC""", (uid,))


def db_count_lists(uid) -> int:
    return get_db().sql_one("SELECT COUNT(*) FROM playlists WHERE user_id = %s", (uid,))[0]


def db_get(pid):
    return get_db().sql_one(
        """SELECT p.id, p.user_id, p.name, p.source, COUNT(i.id) AS n FROM playlists p
           LEFT JOIN playlist_items i ON i.playlist_id = p.id WHERE p.id = %s GROUP BY p.id""", (pid,))


def db_create(uid, name, source=None) -> int:
    now = int(time.time())
    return get_db().sql_one(
        "INSERT INTO playlists (user_id, name, source, created, updated) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (uid, name, source, now, now))[0]


def db_rename(pid, name):
    with get_db().sql() as cur:
        cur.execute("UPDATE playlists SET name = %s, updated = %s WHERE id = %s", (name, int(time.time()), pid))


def db_delete(pid):
    with get_db().sql() as cur:
        cur.execute("DELETE FROM playlists WHERE id = %s", (pid,))       # mahnılar CASCADE ilə silinir


def db_page(pid, page) -> list:
    return get_db().sql_all(
        "SELECT id, title FROM playlist_items WHERE playlist_id = %s ORDER BY pos, id LIMIT %s OFFSET %s",
        (pid, PAGE, page * PAGE))


def db_item(pid, iid):
    row = get_db().sql_one("SELECT item FROM playlist_items WHERE playlist_id = %s AND id = %s", (pid, iid))
    return json.loads(row[0]) if row else None


def db_all_items(pid) -> list:
    return [json.loads(r[0]) for r in get_db().sql_all(
        "SELECT item FROM playlist_items WHERE playlist_id = %s ORDER BY pos, id", (pid,))]


def db_remove(pid, iid) -> bool:
    with get_db().sql() as cur:
        n = cur.execute("DELETE FROM playlist_items WHERE playlist_id = %s AND id = %s", (pid, iid)).rowcount
        cur.execute("UPDATE playlists SET updated = %s WHERE id = %s", (int(time.time()), pid))
    return n > 0


def db_add(pid, items: list, limit: int) -> dict:
    """Dublikatları atır, limiti (0 = limitsiz) aşmır → {added, dup, cut}"""
    now = int(time.time())
    with get_db().sql() as cur:
        have = {r[0] for r in cur.execute("SELECT key FROM playlist_items WHERE playlist_id = %s", (pid,))}
        pos = cur.execute("SELECT COALESCE(MAX(pos), 0) FROM playlist_items WHERE playlist_id = %s",
                          (pid,)).fetchone()[0]
        new, dup = [], 0
        for it in items:
            k = item_key(it)
            if k in have:
                dup += 1
                continue
            have.add(k)
            new.append((k, clean_item(it)))
        room = max(0, limit - (len(have) - len(new))) if limit else len(new)
        cut = max(0, len(new) - room)
        new = new[:room]
        if new:
            cur.executemany(
                """INSERT INTO playlist_items (playlist_id, pos, key, title, item, added)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                [(pid, pos + i, k, it["title"], json.dumps(it, ensure_ascii=False), now)
                 for i, (k, it) in enumerate(new, 1)])
            cur.execute("UPDATE playlists SET updated = %s WHERE id = %s", (now, pid))
    return {"added": len(new), "dup": dup, "cut": cut}


def db_stats() -> dict:
    with get_db().sql() as cur:
        a = cur.execute("""SELECT COUNT(*), COUNT(DISTINCT user_id), COUNT(*) FILTER (WHERE source = 'link')
                           FROM playlists""").fetchone()
        n = cur.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0]
    return {"lists": a[0], "owners": a[1], "imported": a[2], "items": n}


def db_top(limit=10) -> list:
    return get_db().sql_all(
        """SELECT p.id, p.name, p.user_id, u.username, u.first_name, COUNT(i.id) AS n FROM playlists p
           LEFT JOIN playlist_items i ON i.playlist_id = p.id LEFT JOIN users u ON u.user_id = p.user_id
           GROUP BY p.id, u.username, u.first_name ORDER BY n DESC, p.updated DESC LIMIT %s""", (limit,))


# ═════════════════════════ plugin ═════════════════════════
def setup(context):
    dp, bot = context.dp, context.bot
    waiting = {}       # uid → {"mode": "new" | "ren", "pid", "item", "since"}
    picks = {}         # uid → (item, ts) — ➕ basılıb, playlist seçilir



    def is_waiting(uid) -> bool:
        w = waiting.get(uid)
        if w and time.time() - w["since"] > WAIT_TTL:
            waiting.pop(uid, None)
            return False
        return bool(w)

    from bot.core.input_wait import chain_menu_text
    chain_menu_text(context, is_waiting)

    def premium_kb():
        return kb([btn(tr("💎 Premium al"), "sg:premium")])

    async def gate(uid, feature) -> bool:
        return await asyncio.to_thread(tiers.allowed, feature, uid)

    def limits(uid):
        return tiers.limit_for("playlists", uid), tiers.limit_for("playlist_songs", uid)

    def own(pl, uid) -> bool:
        return bool(pl) and pl["user_id"] == uid

    # ───────────── 🎶 Playlistlərim ─────────────
    def home_view(uid, note: str = ""):
        rows = db_lists(uid)
        lim, songs = limits(uid)
        head = tr("🎶 <b>Playlistlərim</b>") + f" · {len(rows)}/{tiers.fmt_limit(lim)}"
        if not rows:
            body = tr("<i>Hələ playlistin yoxdur.</i>\n\nMahnının altındakı <b>➕</b> düyməsi ilə və ya "
                      "<b>➕ Yeni playlist</b> ilə başla.")
            if tiers.visible("playlist_import", uid):
                body += " " + tr("YouTube / Spotify playlist linki göndərib <b>💾 Playlist kimi saxla</b> da "
                                 "edə bilərsən.")
        else:
            body = tr("Hər playlistdə {n} mahnıya qədər.", n=tiers.fmt_limit(songs))
        buttons = [[btn(("💾 " if r["source"] == "link" else "🎶 ") + f"{r['name'][:40]} · {r['n']}",
                        f"pl:o:{r['id']}:0")] for r in rows[:40]]
        buttons.append([btn(tr("➕ Yeni playlist"), "pl:new"), btn(tr("✖️ Bağla"), "pl:x")])
        return f"{head}\n\n{body}" + (f"\n\n{note}" if note else ""), InlineKeyboardMarkup(inline_keyboard=buttons)

    def open_view(pl, page: int, edit_mode: bool = False, note: str = ""):
        pid, n = pl["id"], pl["n"]
        pages = max(1, -(-n // PAGE))
        page = max(0, min(page, pages - 1))
        items = db_page(pid, page)
        _, songs = limits(pl["user_id"])
        lines = [f"{'💾' if pl['source'] == 'link' else '🎶'} <b>{escape(pl['name'])}</b>",
                 tr("🎵 {n} mahnı", n=n) + (f" / {tiers.fmt_limit(songs)}" if songs else "")
                 + (f" · {tr('səhifə')} {page + 1}/{pages}" if pages > 1 else "")]
        if not n:
            lines.append("\n" + tr("<i>Boşdur — mahnının altındakı ➕ ilə və ya mahnıya reply edib /add yazaraq "
                                   "əlavə et.</i>"))
        if edit_mode:
            lines.append("\n" + tr("✂️ <b>Silmək istədiyin mahnıya toxun</b>"))
        if note:
            lines.append(f"\n{note}")
        rows = []
        for i, it in enumerate(items, page * PAGE + 1):
            text = f"{'❌' if edit_mode else '🎵'} {i}. {it['title'] or '?'}"[:60]
            rows.append([btn(text, f"pl:{'rm' if edit_mode else 's'}:{pid}:{it['id']}:{page}")])
        nav = pager(f"pl:{'ed' if edit_mode else 'o'}:{pid}:", page, pages)
        if nav:
            rows.append(nav)
        if edit_mode:
            rows.append([btn(tr("✅ Hazır"), f"pl:o:{pid}:{page}")])
        else:
            owner = pl["user_id"]
            if n and tiers.visible("playlist_dlall", owner):
                badge = " 💎" if tiers.locked("playlist_dlall", owner) else ""
                rows.append([btn(tr("⬇️ Hamısını yüklə") + badge, f"pl:all:{pid}"),
                             btn(tr("🔀 Qarışıq") + badge, f"pl:shuf:{pid}")])
            rows.append([btn(tr("✏️ Ad"), f"pl:ren:{pid}"),
                         *([btn(tr("✂️ Mahnı sil"), f"pl:ed:{pid}:{page}")] if n else []),
                         btn(tr("🗑 Sil"), f"pl:del:{pid}")])
            rows.append([btn(tr("⬅️ Playlistlər"), "pl:home"), btn(tr("✖️ Bağla"), "pl:x")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def picker_view(uid, item):
        rows = db_lists(uid)
        lim, _ = limits(uid)
        buttons = [[btn(f"🎶 {r['name'][:40]} · {r['n']}", f"pl:p:{r['id']}")] for r in rows[:30]]
        if not lim or len(rows) < lim:
            buttons.append([btn(tr("➕ Yeni playlist"), "pl:pn")])
        buttons.append([btn(tr("✖️ Ləğv et"), "pl:pc")])
        return (tr("➕ <b>{t}</b>\n\nHansı playlistə əlavə edək?", t=escape(item["title"][:80])),
                InlineKeyboardMarkup(inline_keyboard=buttons))

    def add_note(res: dict, name: str) -> str:
        if res["added"]:
            out = tr("✅ <b>{name}</b> playlistinə əlavə olundu", name=escape(name))
            if res["added"] > 1:
                out = tr("✅ <b>{name}</b> playlistinə {n} mahnı əlavə olundu", name=escape(name), n=res["added"])
        elif res["dup"]:
            out = tr("ℹ️ Bu mahnı artıq <b>{name}</b> playlistindədir", name=escape(name))
        else:
            out = tr("⚠️ <b>{name}</b> doludur", name=escape(name))
        if res["added"] and res["dup"]:
            out += "\n" + tr("<i>{n} mahnı artıq orada idi</i>", n=res["dup"])
        if res["cut"]:
            out += "\n" + tr("⚠️ <i>Limit dolduğu üçün {n} mahnı əlavə olunmadı</i>", n=res["cut"])
        return out

    async def add_to(uid, pid, items) -> str:
        pl = await asyncio.to_thread(db_get, pid)
        if not own(pl, uid):
            return tr("❌ Playlist tapılmadı")
        res = await asyncio.to_thread(db_add, pid, items, tiers.limit_for("playlist_songs", uid))
        return add_note(res, pl["name"])

    async def create_checked(uid, name, source=None):
        """Limit yoxlaması ilə yeni playlist → (pid, None) və ya (None, xəta mətni)."""
        lim = tiers.limit_for("playlists", uid)
        if lim and await asyncio.to_thread(db_count_lists, uid) >= lim:
            msg = tr("⚠️ Playlist limiti doludur ({n}).", n=lim)
            if tiers.role_of(uid) == "free" and _bigger_for_premium("playlists"):
                msg += "\n" + tr("💎 Premium ilə daha çox — /premium")
            return None, msg
        return await asyncio.to_thread(db_create, uid, name[:NAME_MAX], source), None

    def _bigger_for_premium(name) -> bool:
        p, f = tiers.get_limit(name, "premium"), tiers.get_limit(name, "free")
        return p == 0 or (f != 0 and p > f)

    async def show(target, text, markup, edit: bool):
        if edit:
            await panel_show(target, text, markup)
            return
        await target.answer(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

    # ───────────── komandalar ─────────────
    @dp.message(Command("playlist", "playlists", "pl"), F.chat.type == "private")
    async def pl_cmd(message: types.Message):
        uid = message.from_user.id
        waiting.pop(uid, None)
        if not await gate(uid, "playlists"):
            await message.answer(tiers.gate_text("playlists", uid), parse_mode="HTML", reply_markup=premium_kb())
            return
        await show(message, *await asyncio.to_thread(home_view, uid), edit=False)

    @dp.message(Command("add"), F.reply_to_message.audio)
    async def add_cmd(message: types.Message):
        uid = message.from_user.id
        item = await asyncio.to_thread(item_from_audio, message.reply_to_message.audio)
        if not item:
            await message.reply(tr("❌ Bu mahnını tanımadım — yalnız botun göndərdiyi mahnılar əlavə oluna bilər."))
            return
        await start_add(uid, item, message, reply=True)

    def item_from_audio(audio, vid=None) -> dict | None:
        """Telegram audio-su (və ya video_id) → playlist elementi, audio keşindən."""
        row = None
        try:
            if vid:
                row = get_db().cache_get(vid)
            elif audio and audio.file_unique_id:
                row = get_db().sql_one(
                    """SELECT video_id, title, performer, duration FROM audio_cache
                       WHERE unique_id = %s ORDER BY hits DESC LIMIT 1""", (audio.file_unique_id,))
                row = dict(row) if row else None
        except Exception as e:
            logger.debug(f"keş axtarışı: {e}")
        vid = vid or (row or {}).get("video_id")
        if not vid or vid.startswith("depo:"):
            return None
        title = (row or {}).get("title") or (audio.title if audio else None) or vid
        artist = (row or {}).get("performer") or (audio.performer if audio else None)
        duration = (row or {}).get("duration") or (audio.duration if audio else 0) or 0
        return clean_item({
            "title": f"{artist} - {title}" if artist else title,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "raw_duration": int(duration),
            "meta": {"artist": artist, "track": title} if artist else None,
        })

    async def start_add(uid, item, message: types.Message | None, reply: bool = False, cb=None):
        """➕: playlist yoxdursa yaradılır, biri varsa birbaşa, çoxdursa seçim."""
        async def say(text, markup=None, alert=False):
            if cb is not None and markup is None:
                await cb.answer(re.sub(r"<[^>]+>", "", text)[:190], show_alert=alert)
                return
            if cb is not None:
                await cb.answer()
            target_chat = message.chat.id if message and message.chat.type == "private" else uid
            try:
                if reply and message:
                    await message.reply(text, parse_mode="HTML", reply_markup=markup)
                else:
                    await bot.send_message(target_chat, text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                if cb is not None:
                    await cb.answer(tr("Əvvəlcə botu şəxsi çatda başlat (/start)"), show_alert=True)

        if not await gate(uid, "playlists"):
            await say(tiers.gate_text("playlists", uid), premium_kb() if cb is None else None, alert=True)
            return
        rows = await asyncio.to_thread(db_lists, uid)
        if not rows:
            pid, err = await create_checked(uid, tr("❤️ Sevimlilər"))
            if err:
                await say(err, alert=True)
                return
            await say(await add_to(uid, pid, [item]))
            return
        if len(rows) == 1:
            await say(await add_to(uid, rows[0]["id"], [item]))
            return
        picks[uid] = (item, time.time())
        text, markup = await asyncio.to_thread(picker_view, uid, item)
        await say(text, markup)

    # ───────────── ✍️ ad yazılır ─────────────
    @dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), F.from_user.func(lambda u: is_waiting(u.id)))
    async def name_input(message: types.Message):
        uid = message.from_user.id
        w = waiting.pop(uid, None)
        name = re.sub(r"\s+", " ", message.text).strip()[:NAME_MAX]
        if not w or not name:
            return
        if w["mode"] == "ren":
            pl = await asyncio.to_thread(db_get, w["pid"])
            if own(pl, uid):
                await asyncio.to_thread(db_rename, w["pid"], name)
                pl = await asyncio.to_thread(db_get, w["pid"])
                await show(message, *await asyncio.to_thread(open_view, pl, 0, False, tr("✏️ Ad dəyişdi")), edit=False)
            return
        pid, err = await create_checked(uid, name)
        if err:
            await message.answer(err, parse_mode="HTML")
            return
        if w.get("item"):
            note = await add_to(uid, pid, [w["item"]])
            await message.answer(note, parse_mode="HTML")
            return
        pl = await asyncio.to_thread(db_get, pid)
        await show(message, *await asyncio.to_thread(open_view, pl, 0, False, tr("✅ Playlist yaradıldı")), edit=False)

    # ───────────── 🎶 callback-lər ─────────────
    @dp.callback_query(F.data.startswith("pl:"))
    async def pl_cb(cb: types.CallbackQuery):
        uid = cb.from_user.id
        parts = cb.data.split(":")
        act = parts[1]
        msg = cb.message

        if act == "a":                                   # ➕ mahnının altından
            item = await asyncio.to_thread(item_from_audio, getattr(msg, "audio", None), parts[2])
            if not item:
                await cb.answer(tr("Bu mahnı əlavə oluna bilmir"), show_alert=True)
                return
            await start_add(uid, item, msg, cb=cb)
            return

        if act == "x":
            waiting.pop(uid, None)
            await cb.answer()
            with contextlib.suppress(Exception):
                await msg.delete()
            return

        if act in ("p", "pn", "pc"):                     # playlist seçimi (➕-dan sonra)
            got = picks.get(uid)
            if not got or time.time() - got[1] > PICK_TTL:
                picks.pop(uid, None)
                await cb.answer(tr("Köhnəlib — ➕-a yenidən toxun"), show_alert=True)
                return
            if act == "pc":
                picks.pop(uid, None)
                await cb.answer(tr("Ləğv edildi"))
                with contextlib.suppress(Exception):
                    await msg.delete()
                return
            if act == "pn":
                waiting[uid] = {"mode": "new", "item": got[0], "since": time.time()}
                picks.pop(uid, None)
                await cb.answer()
                await show(msg, tr("✍️ Yeni playlistin adını yaz:"), kb([btn(tr("✖️ Ləğv et"), "pl:cancel")]), edit=True)
                return
            picks.pop(uid, None)
            await cb.answer()
            await show(msg, await add_to(uid, int(parts[2]), [got[0]]), None, edit=True)
            return

        if act == "cancel":
            waiting.pop(uid, None)
            await cb.answer(tr("Ləğv edildi"))
            await show(msg, *await asyncio.to_thread(home_view, uid), edit=True)
            return

        if not await gate(uid, "playlists"):
            await cb.answer(tr("🎶 Playlistlər bağlıdır"), show_alert=True)
            return

        if act == "open":                                # /start menyusundan — yeni mesaj kimi
            await cb.answer()
            await show(msg, *await asyncio.to_thread(home_view, uid), edit=False)
            return

        if act == "home":
            waiting.pop(uid, None)
            await cb.answer()
            await show(msg, *await asyncio.to_thread(home_view, uid), edit=True)
            return

        if act == "new":
            lim = tiers.limit_for("playlists", uid)
            if lim and await asyncio.to_thread(db_count_lists, uid) >= lim:
                await cb.answer(tr("⚠️ Playlist limiti doludur ({n})", n=lim), show_alert=True)
                return
            waiting[uid] = {"mode": "new", "since": time.time()}
            await cb.answer()
            await show(msg, tr("✍️ Yeni playlistin adını yaz:"), kb([btn(tr("✖️ Ləğv et"), "pl:cancel")]), edit=True)
            return

        # qalanları: pl:<act>:<pid>[:...]
        try:
            pid = int(parts[2])
        except (IndexError, ValueError):
            await cb.answer()
            return
        pl = await asyncio.to_thread(db_get, pid)
        if not own(pl, uid):
            await cb.answer(tr("❌ Playlist tapılmadı"), show_alert=True)
            return

        if act in ("o", "ed"):
            await cb.answer()
            page = int(parts[3]) if len(parts) > 3 else 0
            await show(msg, *await asyncio.to_thread(open_view, pl, page, act == "ed"), edit=True)
            return

        if act == "rm":
            iid, page = int(parts[3]), int(parts[4])
            ok = await asyncio.to_thread(db_remove, pid, iid)
            await cb.answer(tr("🗑 Silindi") if ok else tr("Artıq yoxdur"))
            pl = await asyncio.to_thread(db_get, pid)
            await show(msg, *await asyncio.to_thread(open_view, pl, page, pl["n"] > 0), edit=True)
            return

        if act == "ren":
            waiting[uid] = {"mode": "ren", "pid": pid, "since": time.time()}
            await cb.answer()
            await show(msg, tr("✍️ <b>{name}</b> üçün yeni ad yaz:", name=escape(pl["name"])),
                       kb([btn(tr("✖️ Ləğv et"), f"pl:o:{pid}:0")]), edit=True)
            return

        if act == "del":
            await cb.answer()
            await show(msg, tr("🗑 <b>{name}</b> ({n} mahnı) silinsin?", name=escape(pl["name"]), n=pl["n"]),
                       kb([btn(tr("✅ Bəli, sil"), f"pl:delok:{pid}"), btn(tr("❌ Xeyr"), f"pl:o:{pid}:0")]),
                       edit=True)
            return

        if act == "delok":
            await asyncio.to_thread(db_delete, pid)
            await cb.answer(tr("🗑 Silindi"))
            await show(msg, *await asyncio.to_thread(home_view, uid), edit=True)
            return

        if act in ("s", "all", "shuf"):
            run_batch = getattr(context, "music_run_batch", None)
            if not run_batch:
                await cb.answer(tr("Yükləmə modulu yüklənməyib"), show_alert=True)
                return
            if act == "s":
                item = await asyncio.to_thread(db_item, pid, int(parts[3]))
                if not item:
                    await cb.answer(tr("Artıq yoxdur"), show_alert=True)
                    return
                await cb.answer()
                status = await msg.answer("⏳", parse_mode="HTML")
                try:
                    await run_batch(status, [item], item["title"], uid, "playlist", single=True)
                except Exception as e:
                    logger.warning(f"Playlist tək mahnı yükləmə xətası: {e}")
                finally:
                    with contextlib.suppress(Exception):
                        await status.delete()
                return
            if not await gate(uid, "playlist_dlall"):
                await cb.answer(tr("💎 Playlisti birdən yükləmək Premium üçündür"))
                await msg.answer(tiers.gate_text("playlist_dlall", uid), parse_mode="HTML", reply_markup=premium_kb())
                return
            busy = getattr(context, "music_batch_busy", None)
            if busy and busy(uid):
                await cb.answer(tr("Artıq bir toplu yükləmə gedir — bitməsini gözlə və ya ⏹ Dayandır"),
                                show_alert=True)
                return
            items = await asyncio.to_thread(db_all_items, pid)
            if act == "shuf":
                random.shuffle(items)
            await cb.answer(tr("⬇️ {n} mahnı yüklənir...", n=len(items)))
            status = await msg.answer("⏳", parse_mode="HTML")
            await run_batch(status, items, ("🔀 " if act == "shuf" else "🎶 ") + pl["name"], uid, "playlist")
            return

        await cb.answer()

    # ───────────── 💾 linkdən saxlama (links_plugin çağırır) ─────────────
    async def import_items(uid, name: str, items: list) -> tuple:
        """→ (uğurlu?, mətn). Gate və limitlər burada yoxlanır."""
        if not await gate(uid, "playlists"):
            return False, tiers.gate_text("playlists", uid)
        if not await gate(uid, "playlist_import"):
            return False, tiers.gate_text("playlist_import", uid)
        pid, err = await create_checked(uid, name or tr("💾 Saxlanmış playlist"), source="link")
        if err:
            return False, err
        return True, await add_to(uid, pid, items) + "\n" + tr("🎶 /playlist — playlistlərin")

    context.playlist_import = import_items

    # ═════════════════════════ 👑 creator: statistika ═════════════════════════
    # İmkanlar və limitlər: /menu → 💎 Premium → 🎛 İmkanlar (features_plugin)
    def admin_view():
        st = db_stats()
        lines = [
            "💎 Premium › 🎛 İmkanlar › 🎶 <b>Playlist statistikası</b>",
            "─────────────",
            f"🎶 Playlist: <b>{st['lists']}</b> · 👤 sahib: <b>{st['owners']}</b> · 💾 linkdən: <b>{st['imported']}</b>",
            f"🎵 Mahnı: <b>{st['items']}</b>",
            "",
            "🏆 <b>Ən böyük playlistlər</b>",
        ]
        rows = db_top(10)
        if not rows:
            lines.append("<i>Hələ playlist yoxdur.</i>")
        for i, r in enumerate(rows, 1):
            who = f"@{r['username']}" if r["username"] else escape(r["first_name"] or str(r["user_id"]))
            lines.append(f"{i}. <b>{escape(r['name'][:40])}</b> · {r['n']} 🎵 — {who} <code>{r['user_id']}</code>")
        return "\n".join(lines), kb([btn("🔄 Yenilə", "pla:panel"), btn("⬅️ İmkanlar", "tf:panel")])

    @dp.callback_query(F.data.startswith("pla:"))
    async def admin_cb(cb: types.CallbackQuery):
        await cb.answer()
        await show(cb.message, *await asyncio.to_thread(admin_view), edit=True)

    logger.info("🎶 Playlist plugin yükləndi")
