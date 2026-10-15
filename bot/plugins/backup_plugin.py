"""
💾 Bot bazasının yedəklənməsi və bərpası — bot/plugins/backup_plugin.py (yalnız creator)

/menu → 🖥 Sistem → 💾 Yedəkləmə  və ya  /backup
  📤 İndi yedəklə       — 🤖 PostgreSQL bazası (pg_dump → .dump) + 📱 data/userbot.db (.db.gz) → seçilmiş kanala
  📱 Userbot bazası     — ✅ olanda userbot bazası da hər yedəkdə göndərilir (bot rejimində də işləyir)
  ⏱ Avto-yedək         — söndür · 1 / 3 / 6 / 12 saat · 1 / 3 / 7 gün (➖ ➕); restartdan sonra qaldığı yerdən
  📁 Lokal nüsxə        — data/backups/-da neçə yedək saxlansın (köhnələri silinir)
  📢 Kanal              — @username, -100... ID, t.me linki və ya kanaldan yönləndirilmiş mesaj.
                          Seçilməyibsə yedəklər sənin şəxsi çatına gəlir. Bot kanalda admin olmalıdır.
  📥 Import (bərpa)     — .dump (bot) / .db / .db.gz (userbot) faylını bota göndər (kanaldakı yedəyi yönləndirmək də olar):
                          yoxlanılır → müqayisə göstərilir → təsdiqdən sonra bərpa olunur.
                          Bərpadan ƏVVƏL cari baza avtomatik yedəklənir (♻️ geri qaytarmaq olar).
  🗂 Lokal yedəklər     — data/backups-dakı nüsxələrdən birini bir toxunuşla bərpa et

Qeydlər:
  • Bot API faylı 50 MB-a qədər göndərir, 20 MB-a qədər yükləyir. Böyük yedəklər lokalda qalır;
    böyük faylı import etmək üçün onu data/backups/-a qoy və 🗂 Lokal yedəklər-dən seç.
  • Yedəkləmə ayarları bərpadan sonra da qorunur (köhnə yedəkdəki ayarlar onları əzmir).
  • Bərpadan sonra botu restart et — plugin-lərin yaddaşdakı vəziyyəti yeni bazadan oxunsun.
  • Import hər iki bazanı tanıyır: userbot yedəyi göndərsən userbot bazası bərpa olunur və
    userbot pluginləri avtomatik yenidən yüklənir (restart lazım deyil).
  • Userbot-un öz .backup-ı da var — ikisini də avto qoysan userbot bazası iki yerə gedər.
"""
import asyncio
import contextlib
import logging
import os
import time

from html import escape
from pathlib import Path

from aiogram import F, types
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup

from common import backup as bk
from common import destinations as dest
from common.database import get_db
from common.utilities import format_ts as fmt_ts
from common.database import Settings
from bot.core.input_wait import InputWait, chain_menu_text
from bot.core import panel as ui
from bot.core.panel import btn

logger = logging.getLogger(__name__)

PRIORITY = 72                     # menu_plugin (50) və logs_plugin (70)-dən sonra: menu_waiting_text zəncirinə qoşulur
KIND = "bot"
UB_KIND = "userbot"
NS = "backup:"                    # settings açarları
SEND_LIMIT = 50 * 1024 * 1024     # Bot API sendDocument
DOWNLOAD_LIMIT = 20 * 1024 * 1024 # Bot API getFile
WAIT_TTL = 600
CHECK_EVERY = 60




def setup(context):
    dp, bot = context.dp, context.bot
    lock = asyncio.Lock()
    context.is_backup_running = lambda: lock.locked()
    state = InputWait(WAIT_TTL, pending=None)   # await: "chat" | "file"

    is_creator = context.is_creator

    # ── ayarlar (settings cədvəli, yaddaşda keşlənir) ──
    get, put = Settings(NS).get, Settings(NS).put

    def interval() -> int:
        v = get("iv")
        return int(v) if v and str(v).isdigit() else 0

    def keep() -> int:
        v = get("keep")
        return int(v) if v and str(v).isdigit() else 7

    def include_ub() -> bool:
        return (get("ub") or "1") == "1"

    def udb():
        """Userbot bazası (fayl yoxdursa None — userbot heç işlənməyib)."""
        from userbot.core import userbot_db
        if not os.path.exists(userbot_db.DB_PATH) and userbot_db._udb is None:
            return None
        return userbot_db.get_udb()

    def own_settings() -> dict:
        db = get_db()
        return {k: v for k, v in dict(db._settings).items() if isinstance(k, str) and k.startswith(NS)}

    def next_due():
        return bk.next_due(get("last"), interval())

    def label() -> str:
        iv = interval()
        st = get("last_status") or ""
        warn = " ⚠️" if st.startswith("❌") else ""
        return f"💾 Yedəkləmə · {'⏱ ' + bk.iv_label(iv) if iv else '🔴 avto yox'}{warn}"

    context.backup_label = label

    # music_plugin kanal adını / ID-ni mahnı axtarışı saymasın
    chain_menu_text(context, lambda uid: (waiting("chat") or waiting("val")) and is_creator(uid))

    def waiting(kind: str) -> bool:
        return state.waiting(kind, prefix=(kind == "val"))      # "val" — "val:iv", "val:keep" ...

    def reset_wait():
        state.clear()
        pend = state.get("pending")
        if pend:
            bk.cleanup(pend.get("tmp"), pend.get("unpacked"))
        state["pending"] = None

    # ── yedəkləmə ──
    async def target_chat():
        """(göndərmə parametrləri, ad) — kanal / qrup / community mövzusu (📍 Hədəflər, /setbackup)."""
        kw = dest.kwargs("backup")
        if kw:
            return kw, dest.describe("backup")
        return ({"chat_id": context.creator_id} if context.creator_id else None), "👤 şəxsi çat"

    # ── 📱 Böyük fayllar (Bot API: göndərmə 50 MB, yükləmə 20 MB) userbot / asistan hesabı ilə (Telethon: 2 GB) ──
    def big_clients() -> list:
        """[(ad, client)] — əvvəl əsas userbot (1️⃣), sonra canlı yayım asistanı (2️⃣)."""
        out = []
        c1 = getattr(context, "telethon_client", None)
        if c1 is not None:
            out.append(("1️⃣ userbot", c1))
        va = getattr(context, "voice_assistant", None)
        try:
            c2 = va.any_client() if va is not None else None
        except Exception:
            c2 = None
        if c2 is not None and all(c2 is not c for _, c in out):
            out.append(("2️⃣ asistan", c2))
        return out

    async def send_big(path, caption: str, kw: dict, manual: bool):
        """Faylı userbot / asistan hesabı ilə göndərir. Hədəf = yedək kanalı / qrupu (+ mövzu) və ya sizin şəxsi çat.
        Uğurda hesabın adını, alınmasa None qaytarır."""
        errors = []
        target = kw.get("chat_id")
        thread = kw.get("message_thread_id")
        for name, client in big_clients():
            try:
                if not client.is_connected():
                    continue
                ent_candidates = [target]
                if target == context.creator_id:
                    ent_candidates.append("me")                    # öz hesabınızdırsa — Saved Messages
                for ent in ent_candidates:
                    try:
                        await client.send_file(ent, str(path), caption=caption, parse_mode="html",
                                               silent=not manual, reply_to=thread or None, force_document=True)
                        return name + ("" if ent == target else " → Saved Messages")
                    except Exception as e:
                        errors.append(f"{name}: {str(e)[:60]}")
            except Exception as e:
                errors.append(f"{name}: {str(e)[:60]}")
        logger.info(f"💾 Böyük yedək userbotla göndərilmədi: {'; '.join(errors) or 'hesab yoxdur'}")
        return None

    async def download_big(doc, message, dst) -> bool:
        """Bot API-nin yükləyə bilmədiyi (>20 MB) faylı: sizin userbot hesabı bot çatından yükləyir."""
        c1 = getattr(context, "telethon_client", None)
        if c1 is None or not c1.is_connected():
            return False
        try:
            me = await bot.me()
            async for m in c1.iter_messages(me.username, limit=30):
                f = getattr(m, "file", None)
                if f is not None and getattr(m, "document", None) and f.size == doc.file_size \
                        and (f.name or "") == (doc.file_name or ""):
                    await c1.download_media(m, file=str(dst))
                    return Path(dst).exists()
        except Exception as e:
            logger.info(f"💾 Böyük fayl userbotla yüklənmədi: {e}")
        return False

    async def send_snap(snap, kw, title, manual, note) -> str:
        icon = "🤖" if snap.kind == KIND else "📱"
        if not kw:
            return f"{icon} ⚠️ yalnız lokal — CREATOR_ID və kanal yoxdur"
        if snap.size > SEND_LIMIT:
            via = await send_big(snap.path, bk.caption(snap, note), kw, manual)
            if via:
                return f"{icon} {bk.human(snap.size)} ({via})"
            return f"{icon} ⚠️ {bk.human(snap.size)} > 50 MB — userbot da göndərə bilmədi, yalnız lokal: {snap.name}"
        try:
            await bot.send_document(document=FSInputFile(str(snap.path), filename=snap.name),
                                    caption=bk.caption(snap, note), parse_mode="HTML",
                                    disable_notification=not manual, **kw)
            return f"{icon} {bk.human(snap.size)}"
        except Exception as e:
            logger.warning(f"💾 Yedək göndərilmədi ({kw}): {e}")
            return f"{icon} ❌ göndərilmədi ({str(e)[:80]}) — lokalda: {snap.name}"

    async def run_backup(manual: bool) -> str:
        if lock.locked():
            return "⏳ Yedəkləmə artıq gedir"
        async with lock:
            sources = [(KIND, get_db())]
            if include_ub():
                u = udb()
                if u is not None:
                    sources.append((UB_KIND, u))
            snaps, parts = [], []
            for kind, d in sources:
                try:
                    src = (d, None) if kind == KIND else (d._conn, d._lock)    # bot → PostgreSQL
                    snaps.append(await asyncio.to_thread(bk.snapshot, kind, *src))
                except Exception as e:
                    logger.error(f"💾 {kind} yedəyi alınmadı: {e}", exc_info=True)
                    parts.append(f"{'🤖' if kind == KIND else '📱'} ❌ yedək alınmadı: {str(e)[:80]}")
            put("last", str(int(time.time())))
            kw, title = await target_chat()
            note = "" if manual else f"⏱ avto · hər {bk.iv_label(interval())}"
            for snap in snaps:
                parts.append(await send_snap(snap, kw, title, manual, note))
            for kind, _ in sources:
                await asyncio.to_thread(bk.prune, kind, keep())
            ok = snaps and not any("❌" in x or "⚠️" in x for x in parts)
            status = (f"{'✅' if ok else '⚠️'} {fmt_ts(time.time())} → {title}: " + " · ".join(parts))[:300]
            if not snaps:
                status = "❌ " + " · ".join(parts)
            put("last_status", status)
            logger.info(f"💾 Yedək: {status}")
            return status

    def scheduler():                                             # 30 san. — başlanğıc yükü keçsin
        return bk.auto_loop(run_backup, next_due, delay=30, label="Avto-yedək xətası",
                            check_every=CHECK_EVERY, log=logger)

    context.backup_task = bk.restart_task(getattr(context, "backup_task", None), scheduler)
    if context.backup_task is None:                              # loop hələ yoxdur — polling başlayanda
        async def _start(**_):
            context.backup_task = asyncio.create_task(scheduler())
        dp.startup.register(_start)

    # ── pəncərələr ──
    def nav():
        return ui.nav("menu:arch", "Arxiv", close_data="bk:close", refresh_data="bk:panel")

    def panel(note: str = ""):
        iv, kp = interval(), keep()
        due = next_due()
        chat = dest.get("backup")
        target = escape(dest.describe("backup")) if chat else "👤 şəxsi çat <i>(hədəf seçilməyib)</i>"
        n_local = len(bk.local_list(KIND, include_pre=False))
        u = udb()
        from userbot.core import userbot_db
        try:
            pg_name, pg_size = bk.pg_info(get_db())
            bot_line = f"🤖 <b>Bot bazası:</b> PostgreSQL <code>{escape(pg_name)}</code> · {bk.human(pg_size)}"
        except Exception as e:
            bot_line = f"🤖 <b>Bot bazası:</b> ❌ <code>{escape(str(e))[:100]}</code>"
        ub_line = ("📱 <b>Userbot bazası:</b> " + ("✅ hər yedəkdə · " if include_ub() else "🔴 daxil deyil · ")
                   + (f"{bk.human(bk.file_size(userbot_db.DB_PATH))}" if u is not None else "<i>fayl yoxdur</i>"))
        lines = [
            "<i>🏠 › 🖥 Sistem</i>",
            "💾 <b>Yedəkləmə — bot + userbot</b>",
            "━━━━━━━━━━━━━━━━━━",
            bot_line,
            ub_line,
            f"📢 <b>Hədəf:</b> {target}",
            "⏱ <b>Avto:</b> " + (f"hər {bk.iv_label(iv)} · növbəti: {fmt_ts(due)}" if iv else "🔴 söndürülüb"),
            f"📁 <b>Lokal:</b> {n_local}/{kp} nüsxə (hər baza üçün) · <code>{escape(str(bk.BACKUP_DIR))}</code>",
            f"🕒 <b>Son:</b> {escape(get('last_status') or 'hələ yedək alınmayıb')}",
            "━━━━━━━━━━━━━━━━━━",
            "<i>📥 Import — yedək faylını bura göndər və ya kanaldan yönləndir. Bərpadan əvvəl cari baza "
            "avtomatik yedəklənir.</i>",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = [
            [btn("📤 İndi yedəklə", "bk:now")],
            [ui.toggle("📱 Userbot bazası da", include_ub(), "bk:ub")],
            [btn(f"⏱ Avto-yedək: {('hər ' + bk.iv_label(iv)) if iv else '🔴 Deaktiv'}", "bk:pick:iv"),
             btn(f"📁 Saxla: {kp} nüsxə", "bk:pick:keep")],
            [btn("📍 Hədəfi seç", "bk:chat")] + ([btn("👤 Şəxsi çata", "bk:dm")] if chat else []),
            [btn("📥 Import (bərpa)", "bk:imp"), btn("🗂 Lokal yedəklər", "bk:list")],
            nav(),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def backup_picker_view(target: str, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
        if target == "iv":
            val = interval()
            cur_str = ("hər " + bk.iv_label(val)) if val else "Söndürülüb"
            title = "⏱ <b>Avto-yedək intervalı</b>"
            desc = "Baza məlumatlarının avtomatik ehtiyat nüsxəsinin çıxarılma tezliyi."
        else:
            val = keep()
            cur_str = f"{val} nüsxə"
            title = "📁 <b>Lokal nüsxə limiti</b>"
            desc = "Serverdə hər baza üçün saxlanılan maksimum ehtiyat nüsxə sayı."

        lines = [
            "<i>🏠 › 🖥 Sistem › 💾 Yedəkləmə</i>",
            title,
            "━━━━━━━━━━━━━━━━━━",
            f"<i>{desc}</i>\n",
            f"🔢 <b>Cari dəyər:</b> <b>{cur_str}</b>\n",
            "<i>Dəyəri dəyişmək üçün aşağıdakı düymələrdən istifadə edin və ya çata istədiyiniz rəqəmi yazın:</i>",
            "• <b>+</b> — maksimum / aktiv",
            "• <b>-</b> — söndür / 0",
        ]
        if note:
            lines.append(f"\n{note}")
        rows = ui.stepper(f"bk:vstep:{target}:", f"bk:vinp:{target}") + \
            [ui.nav("bk:panel", "Yedəkləmə", close_data="bk:close")]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def local_files() -> list:
        files = bk.local_list(KIND) + bk.local_list(UB_KIND)
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:10]

    def list_view(note: str = ""):
        files = local_files()
        lines = ["<i>🏠 › 🖥 Sistem › 💾 Yedəkləmə</i>", "🗂 <b>Lokal yedəklər</b>", "━━━━━━━━━━━━━━━━━━"]
        rows = []
        if not files:
            lines.append("<i>Hələ lokal yedək yoxdur.</i>")
        for i, p in enumerate(files):
            pre = bk.PRE_TAG in p.name
            st = p.stat()
            icon = "📱" if p.name.startswith(UB_KIND + "_") else "🤖"
            lines.append(f"{i + 1}. {icon}{'🛟' if pre else ''} <code>{escape(p.name)}</code> · {bk.human(st.st_size)}"
                         + (" <i>(bərpadan əvvəlki)</i>" if pre else ""))
            rows.append([btn(f"♻️ {i + 1}-i bərpa et", f"bk:lr:{i}"), btn(f"📤 {i + 1}-i göndər", f"bk:ls:{i}")])
        lines.append("\n<i>🤖 bot · 📱 userbot · 🛟 — bərpadan əvvəl avtomatik alınan yedək (geri qaytarmaq üçün).</i>")
        if note:
            lines.append(f"\n{note}")
        rows.append(ui.nav("bk:panel", "Yedəkləmə", close_data="bk:close"))
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def confirm_view(info: dict, source: str):
        if info["kind"] == KIND:
            cur = bk.pg_table_counts(get_db())
        else:
            db = udb()
            with db._lock:
                cur = {n: db._conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                       for (n,) in db._conn.execute(
                           "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        lines = ["<i>🏠 › 🖥 Sistem › 💾 Yedəkləmə</i>", "📥 <b>Bərpanı təsdiqlə</b>", "━━━━━━━━━━━━━━━━━━",
                 f"📄 {escape(source)} · açılmış {bk.human(info['raw_size'])}",
                 "✅ Yoxlama: baza sağlamdır, növü: "
                 + ("🤖 bot bazası" if info["kind"] == KIND else "📱 userbot bazası"), "",
                 "<b>Cədvəl</b>: indi → yedəkdə"]
        for name, now_n, new_n in bk.compare_line(cur, info["tables"], limit=8):
            arrow = "🔺" if new_n > now_n else ("🔻" if new_n < now_n else "▫️")
            lines.append(f"{arrow} <code>{escape(name)}</code>: {now_n} → <b>{new_n}</b>")
        lines += ["━━━━━━━━━━━━━━━━━━",
                  "⚠️ Cari baza bu yedəklə <b>əvəz olunacaq</b>. Əvvəl avtomatik 🛟 yedək alınır.",
                  "<i>Yedəkləmə ayarların (kanal, interval) dəyişməyəcək.</i>"]
        if info["kind"] == UB_KIND:
            lines.append("<i>📱 Bərpadan sonra userbot pluginləri avtomatik yenidən yüklənir.</i>")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            ui.confirm("bk:ok", "bk:panel", "bərpa et")])
        return "\n".join(lines), kb

    def done_kb(need_restart: bool = True):
        rows = []
        restart = getattr(context, "restart_button", None)
        if restart and need_restart:
            rows.append([restart("♻️ Botu restart et (tövsiyə olunur)")])
        rows.append(ui.nav("bk:panel", "Yedəkləmə", close_data="bk:close"))
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def show(target, text, kb):
        """target: CallbackQuery və ya (chat_id, message_id)."""
        await ui.show(target, text, kb, bot=bot)
        if isinstance(target, types.CallbackQuery) and target.message:
            state["panel"] = (target.message.chat.id, target.message.message_id)

    async def to_panel(message, text, kb):
        await state.to_panel(bot, message, text, kb)

    # ── bərpa ──
    async def do_restore(kind: str, db_file: str) -> tuple:
        """Qaytarır: (🛟 əvvəlki vəziyyətin faylı, əlavə qeydlər)."""
        notes = []
        if kind == UB_KIND:
            u = udb()
            if u is None:
                from userbot.core.userbot_db import get_udb
                u = get_udb()
            keep_ub = u.kv_all("backup")                         # userbot-un öz .backup ayarları
            async with lock:
                pre = await asyncio.to_thread(bk.restore, UB_KIND, u._conn, u._lock, db_file)
                with u._lock:
                    u._conn.execute("DELETE FROM kv WHERE ns=?", ("backup",))
                    u._conn.commit()
                for k, v in keep_ub.items():
                    u.kv_set("backup", k, v)
            ub = getattr(context, "userbot", None)
            load = ub.state.get("load_plugin_file") if ub else None
            if load:
                ok, bad = 0, 0
                for name, path in list(ub.plugin_files.items()):
                    try:
                        r = load(path)
                        if asyncio.iscoroutine(r):
                            await r
                        ok += 1
                    except Exception as e:
                        bad += 1
                        logger.warning(f"💾 {name} yenidən yüklənmədi: {e}")
                notes.append(f"🔄 {ok} userbot plugini yenidən yükləndi" + (f" · ⚠️ {bad} xəta" if bad else ""))
            else:
                notes.append("ℹ️ Userbot bu rejimdə işləmir — növbəti başlanğıcda yeni baza oxunacaq")
            logger.warning(f"💾 Userbot bazası bərpa olundu (əvvəlki: {pre.name})")
            return pre.name, notes

        db = get_db()
        keep_settings = own_settings()
        async with lock:
            pre = await asyncio.to_thread(bk.restore, KIND, db, None, db_file)
            # yaddaşdakı keşlər yeni bazadan
            db._settings = {r["key"]: r["value"] for r in db.sql_all("SELECT key, value FROM settings")}
            db._cs_cache = None
            for k in [k for k in list(db._settings) if k.startswith(NS)]:
                if k not in keep_settings:
                    db.set_setting(k, None)
            for k, v in keep_settings.items():
                db.set_setting(k, v)
            with contextlib.suppress(Exception):
                from bot.core import audio_cache
                audio_cache.mark_dirty()
        logger.warning(f"💾 Bot bazası bərpa olundu (əvvəlki vəziyyət: {pre.name})")
        notes.append("♻️ <b>Botu restart et</b> — plugin-lərin yaddaşdakı vəziyyəti (depo növbəsi, keşlər, "
                     "köhnə yedəkdə olmayan sütunlar) yeni bazadan qurulsun.")
        return pre.name, notes

    async def prepare_restore(src: Path, source: str, temp: bool):
        info = await asyncio.to_thread(bk.inspect, src)
        unpacked = info["db_path"] if info["db_path"] and info["db_path"] != str(src) else None
        if not info["ok"] or info["kind"] not in (KIND, UB_KIND):
            bk.cleanup(unpacked, src if temp else None)
            return None, f"❌ {escape(info['error'] or 'uyğun deyil')}"
        state["pending"] = {"db": info["db_path"], "tmp": str(src) if temp else None, "unpacked": unpacked,
                            "source": source, "kind": info["kind"]}
        return confirm_view(info, source), None

    # ── komanda ──
    @dp.message(Command("backup"))
    async def backup_cmd(message: types.Message):
        if not is_creator(message.from_user.id) or message.chat.type != "private":
            return
        reset_wait()
        text, kb = panel()
        sent = await message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        state["panel"] = (sent.chat.id, sent.message_id)

    # ── düymələr ──
    @dp.callback_query(F.data.startswith("bk:"))
    async def backup_cb(cb: types.CallbackQuery):
        parts = cb.data.split(":")
        act = parts[1]
        arg = parts[2] if len(parts) > 2 else ""
        note = ""

        if act == "noop":
            await cb.answer()
            return
        if act == "close":
            reset_wait()
            await cb.answer()
            with contextlib.suppress(Exception):
                await cb.message.delete()
            return

        if act == "now":
            await cb.answer("📤 Yedəklənir...")
            await show(cb, *panel("⏳ <i>Yedək hazırlanır...</i>"))
            note = await run_backup(manual=True)
        elif act == "pick" and arg in ("iv", "keep"):
            state.start(f"val:{arg}")
            await cb.answer()
            await show(cb, *backup_picker_view(arg))
            return
        elif act == "vinp" and arg in ("iv", "keep"):
            state.start(f"val:{arg}")
            await cb.answer("✍️ Çata istədiyiniz rəqəmi, '+' və ya '-' yazın")
            return
        elif act == "vstep" and len(parts) > 3 and parts[2] in ("iv", "keep"):
            target = parts[2]
            delta = int(parts[3])
            state.start(f"val:{target}")
            if target == "keep":
                cur = keep()
                new_v = max(1, min(100, cur + delta))
                put("keep", str(new_v))
                for k in (KIND, UB_KIND):
                    await asyncio.to_thread(bk.prune, k, new_v)
                note = f"📁 Saxlama limiti: {new_v} nüsxə"
                await cb.answer(note)
            else:
                cur = interval()
                idx = bk.INTERVALS.index(cur) if cur in bk.INTERVALS else 0
                new_idx = max(0, min(len(bk.INTERVALS) - 1, idx + delta))
                new_v = bk.INTERVALS[new_idx]
                put("iv", str(new_v))
                if new_v and not get("last"):
                    put("last", str(int(time.time())))
                note = f"⏱ Avto-yedək: {('hər ' + bk.iv_label(new_v)) if new_v else 'Söndürülüb'}"
                await cb.answer(note)
            await show(cb, *backup_picker_view(target, note))
            return
        elif act == "iv":
            cur = interval()
            new = 0 if arg == "0" and cur else (bk.step(bk.INTERVALS, cur, 1 if arg != "-" else -1)
                                                if arg in ("+", "-") else 1440)
            put("iv", str(new))
            if new and not get("last"):
                put("last", str(int(time.time())))          # ilk avto-yedək interval bitəndə
            await cb.answer(f"⏱ {'hər ' + bk.iv_label(new) if new else 'Avto-yedək söndürüldü'}")
        elif act == "keep":
            put("keep", str(bk.step(bk.KEEP_STEPS, keep(), 1 if arg == "+" else -1)))
            await cb.answer(f"📁 {keep()} nüsxə")
            for k in (KIND, UB_KIND):
                await asyncio.to_thread(bk.prune, k, keep())
        elif act == "ub":
            put("ub", "0" if include_ub() else "1")
            await cb.answer("📱 Userbot bazası " + ("hər yedəkdə göndəriləcək" if include_ub() else "daxil edilmir"))
        elif act == "chat":
            reset_wait()
            state.start("chat")
            await cb.answer()
            await show(cb, ("<i>🏠 › 🖥 Sistem › 💾 Yedəkləmə</i>\n📢 <b>Yedək kanalı</b>\n\n"
                            "Kanalı göndər:\n• <code>@kanal</code>\n• <code>-1001234567890</code>\n"
                            "• <code>https://t.me/kanal</code>\n• və ya kanaldan istənilən mesajı <b>yönləndir</b>\n\n"
                            "<b>Qrup / community mövzusu:</b> həmin mövzunun içində <code>/setbackup</code> yaz\n\n"
                            "<i>Bot həmin kanalda \"mesaj göndərmək\" icazəsi olan admin olmalıdır. "
                            "Yedəkdə istifadəçi məlumatları var — kanal gizli olsun.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "bk:panel")]]))
            return
        elif act == "dm":
            dest.clear("backup")
            await cb.answer("👤 Yedəklər şəxsi çatına gələcək")
        elif act == "imp":
            reset_wait()
            state.start("file")
            await cb.answer()
            await show(cb, ("<i>🏠 › 🖥 Sistem › 💾 Yedəkləmə</i>\n📥 <b>Bazanı import et</b>\n\n"
                            "Yedək faylını (bot: <code>.dump</code>, userbot: <code>.db</code> / <code>.db.gz</code>) bura <b>sənəd kimi</b> göndər "
                            "və ya yedək kanalından yönləndir.\n"
                            "🤖 bot və 📱 userbot yedəyini özü tanıyır — hansı bazanın bərpa olunacağını "
                            "təsdiqdən əvvəl göstərəcək.\n\n"
                            f"<i>Bot API limiti: {bk.human(DOWNLOAD_LIMIT)}. Daha böyük faylı serverdə "
                            f"<code>{escape(str(bk.BACKUP_DIR))}/</code> qovluğuna qoy və 🗂 Lokal yedəklər-dən seç.</i>"),
                       InlineKeyboardMarkup(inline_keyboard=[[btn("✖️ Ləğv et", "bk:panel")]]))
            return
        elif act == "list":
            reset_wait()
            await cb.answer()
            await show(cb, *list_view())
            return
        elif act in ("lr", "ls"):
            files = local_files()
            i = int(arg) if arg.isdigit() else -1
            if not 0 <= i < len(files):
                await cb.answer("Fayl tapılmadı — siyahını yenilə", show_alert=True)
                await show(cb, *list_view())
                return
            p = files[i]
            if act == "ls":
                await cb.answer("📤 Göndərilir...")
                try:
                    if p.stat().st_size > SEND_LIMIT:
                        via = await send_big(p, f"🗂 Lokal yedək: <code>{escape(p.name)}</code>",
                                             {"chat_id": cb.message.chat.id}, True)
                        if not via:
                            raise RuntimeError("50 MB-dan böyükdür və userbot göndərə bilmədi")
                        await show(cb, *list_view(f"📤 Göndərildi ({via})"))
                        return
                    await bot.send_document(cb.message.chat.id, FSInputFile(str(p), filename=p.name),
                                            caption=f"🗂 Lokal yedək: <code>{escape(p.name)}</code>", parse_mode="HTML")
                except Exception as e:
                    await show(cb, *list_view(f"❌ Göndərilmədi: <code>{escape(str(e))[:150]}</code>"))
                return
            reset_wait()
            await cb.answer("🔎 Yoxlanılır...")
            view, err = await prepare_restore(p, f"lokal: {p.name}", temp=False)
            await show(cb, *(view or list_view(err)))
            return
        elif act == "ok":
            pend = state.get("pending")
            if not pend:
                await cb.answer("Gözləyən bərpa yoxdur", show_alert=True)
            else:
                await cb.answer("♻️ Bərpa olunur...")
                await show(cb, "♻️ <b>Bərpa olunur...</b>\n<i>Əvvəl cari baza yedəklənir.</i>", None)
                try:
                    pre, notes = await do_restore(pend["kind"], pend["db"])
                    what = "🤖 Bot bazası" if pend["kind"] == KIND else "📱 Userbot bazası"
                    text = (f"✅ <b>{what} bərpa olundu</b>\n\n"
                            f"📄 Mənbə: {escape(pend['source'])}\n"
                            f"🛟 Əvvəlki vəziyyət saxlanıldı: <code>{escape(pre)}</code>\n"
                            "<i>(🗂 Lokal yedəklər-dən bir toxunuşla geri qaytara bilərsən)</i>\n\n"
                            + "\n".join(notes))
                except Exception as e:
                    logger.error(f"💾 Bərpa alınmadı: {e}", exc_info=True)
                    text = f"❌ <b>Bərpa alınmadı:</b> <code>{escape(str(e))[:300]}</code>\n<i>Cari baza dəyişməyib.</i>"
                reset_wait()
                await show(cb, text, done_kb(pend["kind"] == KIND))
                return
        else:                                                    # "panel" və naməlum
            reset_wait()
            await cb.answer()
        await show(cb, *panel(escape(note) if note else ""))

    # ── kanal / fayl / dəyər gözləməsi ──
    async def waiting_val(message: types.Message) -> bool:
        return bool(message.from_user) and is_creator(message.from_user.id) and waiting("val") and bool(message.text)

    @dp.message(F.chat.type == "private", waiting_val)
    async def set_backup_value(message: types.Message):
        target = str(state.get("await") or "").split(":")[-1]
        if target not in ("iv", "keep"):
            state.clear()
            return
        raw = message.text.strip().lower()
        with contextlib.suppress(Exception):
            await message.delete()

        note = ""
        if target == "iv":
            if raw in ("-", "off", "0", "bagli", "bağlı", "sondur"):
                put("iv", "0")
                note = "🔴 Avto-yedək söndürüldü"
            elif raw in ("+", "max", "sonsuz", "inf", "unlimited"):
                put("iv", "1440")
                note = "✅ Avto-yedək: hər 24 saat (1440 dəq)"
            else:
                try:
                    v = int(raw)
                    put("iv", str(max(0, v)))
                    note = f"✅ Avto-yedək: {('hər ' + bk.iv_label(v)) if v else 'Söndürülüb'}"
                except ValueError:
                    note = f"⚠️ \"{message.text[:20]}\" düzgün rəqəm deyil! Rəqəm, '+' və ya '-' yazın."
        else:
            if raw in ("-", "off", "0", "min"):
                put("keep", "1")
                note = "📁 Saxlama limiti: 1 nüsxə"
            elif raw in ("+", "max"):
                put("keep", "20")
                note = "✅ Saxlama limiti: 20 nüsxə"
            else:
                try:
                    v = int(raw)
                    put("keep", str(max(1, min(100, v))))
                    note = f"✅ Saxlama limiti: {keep()} nüsxə"
                except ValueError:
                    note = f"⚠️ \"{message.text[:20]}\" düzgün rəqəm deyil! Rəqəm, '+' və ya '-' yazın."

        state.start(f"val:{target}")
        await to_panel(message, *backup_picker_view(target, note))

    async def waiting_chat(message: types.Message) -> bool:
        return bool(message.from_user) and is_creator(message.from_user.id) and waiting("chat")

    async def waiting_file(message: types.Message) -> bool:
        return bool(message.from_user) and is_creator(message.from_user.id) and waiting("file") \
            and message.document is not None

    @dp.message(F.chat.type == "private", waiting_chat)
    async def set_chat(message: types.Message):
        try:
            r = await dest.resolve_message(bot, message)      # t.me/qrup/5 → forum mövzusu
        except ValueError as e:
            await message.reply(f"❌ {escape(str(e))[:300]}\n<i>Alternativ: mövzunun içində /setbackup yaz.</i>",
                                parse_mode="HTML")
            return

        try:
            kw = {"message_thread_id": r["thread_id"]} if r["thread_id"] else {}
            await bot.send_message(r["chat_id"], "✅ <b>Baza yedəkləri bura göndəriləcək.</b>",
                                   parse_mode="HTML", disable_notification=True, **kw)
        except Exception as e:
            await message.reply(f"❌ Bu çata yaza bilmirəm: <code>{escape(str(e))[:200]}</code>\n"
                                "<i>Bot qrupda üzv / kanalda admin olmalıdır.</i>", parse_mode="HTML")
            return
        dest.put("backup", r["chat_id"], r["thread_id"], r["title"], r["type"])
        state.clear()
        with contextlib.suppress(Exception):
            await message.delete()
        await to_panel(message, *panel(f"✅ Hədəf təyin edildi: <b>{escape(r['title'])}</b>"))

    @dp.message(F.chat.type == "private", waiting_file)
    async def import_file(message: types.Message):
        doc = message.document
        big = (doc.file_size or 0) > DOWNLOAD_LIMIT
        if big and not getattr(context, "telethon_client", None):
            await message.reply(f"❌ Fayl {bk.human(doc.file_size)} — Bot API {bk.human(DOWNLOAD_LIMIT)}-dan böyük "
                                f"faylı yükləyə bilmir, userbot da qoşulu deyil.\nFaylı serverdə "
                                f"<code>{escape(str(bk.BACKUP_DIR))}/</code>-a qoy və 🗂 Lokal yedəklər-dən seç.",
                                parse_mode="HTML")
            return
        state.clear()
        bk.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = bk.BACKUP_DIR / f".import_{int(time.time())}_{doc.file_unique_id}"
        status = await message.reply("⏳ <i>Yüklənir və yoxlanılır...</i>" if not big else
                                     "⏳ <i>Böyük fayl — userbot hesabı ilə yüklənir...</i>", parse_mode="HTML")
        try:
            if big:
                if not await download_big(doc, message, tmp):
                    raise RuntimeError("userbot faylı bot çatında tapa bilmədi (userbot sizin hesabınız olmalıdır)")
            else:
                await bot.download(doc, destination=tmp)
        except Exception as e:
            bk.cleanup(tmp)
            await status.edit_text(f"❌ Fayl yüklənmədi: <code>{escape(str(e))[:200]}</code>", parse_mode="HTML")
            return
        view, err = await prepare_restore(tmp, doc.file_name or "fayl", temp=True)
        with contextlib.suppress(Exception):
            await status.delete()
        if err:
            await to_panel(message, *panel(err))
            return
        await to_panel(message, *view)

    # menyuda başqa yerə keçəndə gözləmə ləğv olunsun
    class ResetOnMenu:
        async def __call__(self, handler, event, data):
            d = getattr(event, "data", None) or ""
            if d.startswith("menu:") and state["await"]:
                reset_wait()
            return await handler(event, data)

    dp.callback_query.middleware(ResetOnMenu())

    logger.info("✅ Backup plugin yükləndi (/backup) — avto: " + (bk.iv_label(interval())))


async def teardown(context):
    t = getattr(context, "backup_task", None)
    if t and not t.done():
        t.cancel()
