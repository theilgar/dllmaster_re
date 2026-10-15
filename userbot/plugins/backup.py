"""💾 .backup / .restore — userbot bazasının (data/userbot.db) yedəklənməsi və bərpası.

  .backup                   — ayarlar kartı: 📤 İndi yedəklə · ⏱ avto interval · 📁 lokal nüsxə · 🎯 hədəf
  .backup now               — dərhal yedəklə və hədəfə göndər
  .backup to <hədəf>        — yedəklər hara getsin: @kanal · -100... · t.me linki · here (bu çat) · me (Saved)
  .backup every <müddət>    — avto-yedək: 30m · 6h · 1d · 3d · off
  .backup keep <say>        — data/backups-da neçə lokal nüsxə qalsın
  .backups                  — lokal yedəklərin siyahısı
  .restore                  — yedək sənədinə (.db / .db.gz) reply → yoxlama + müqayisə → ✅ təsdiq
  .restore <n>              — .backups siyahısındakı n-ci lokal yedəkdən bərpa

Userbot faylı Telegram-a 2 GB-a qədər göndərə / yükləyə bilir (bot limiti yoxdur).
Bərpadan əvvəl cari baza avtomatik yedəklənir (🛟), yedəkləmə ayarları isə bərpadan sonra da qorunur.
Bərpadan sonra digər userbot pluginləri avtomatik yenidən yüklənir (filtrlər, mute və s. yeni bazadan).
Botun /backup paneli də (📱 Userbot bazası da: ✅) bu bazanı yedəkləyib bərpa edə bilir — ikisindən birinin
avtomatını işlətmək kifayətdir, yoxsa userbot bazası iki dəfə yedəklənər.
"""
import asyncio
import contextlib
import time
from html import escape

from telethon import events

from common import backup as bk
from userbot.core.userbot_api import InlineKeyboardMarkup, Out, ff, logger, parse_duration, safe_edit
from common.utilities import format_ts as fmt_ts
from common.kv import next_of

KIND = "userbot"
NS = "backup"
CHECK_EVERY = 60
OWNER = __name__.rsplit(".", 1)[-1]          # plugin faylının adı (reload / uninstall üçün)


def register(ub):
    udb = ub.db
    gen = ub.state.get("backup_gen", 0) + 1                  # reload olunanda köhnə avto-dövr dayansın
    ub.state["backup_gen"] = gen
    lock = ub.state.setdefault("backup_lock", asyncio.Lock())

    # ── ayarlar (userbot bazası, ns="backup") ──
    def get(key, default=None):
        v = udb.kv_get(NS, key)
        return default if v is None else v

    def put(key, value):
        if value is None:
            udb.kv_del(NS, key)
        else:
            udb.kv_set(NS, key, value)

    def interval() -> int:
        return int(get("iv", 0) or 0)

    def keep() -> int:
        return int(get("keep", 7) or 7)

    def target_title() -> str:
        t = get("target")
        if not t:
            from common import destinations as dest
            d = dest.get("backup")
            if d:
                return f"📍 {d['title']}"
            return "💾 Saved Messages"
        return "💾 Saved Messages" if t == "me" else str(get("target_title") or t)

    def next_due():
        return bk.next_due(get("last", 0), interval())

    # ── yedəkləmə ──
    async def run_backup(manual: bool) -> str:
        client = ub.client
        if lock.locked():
            return "⏳ yedəkləmə artıq gedir"
        async with lock:
            try:
                snap = await asyncio.to_thread(bk.snapshot, KIND, udb._conn, udb._lock)
            except Exception as e:
                logger.error(f"💾 Userbot yedəyi alınmadı: {e}", exc_info=True)
                put("last_status", f"❌ yedək alınmadı: {str(e)[:100]}")
                return get("last_status")
            put("last", int(time.time()))
            note = "" if manual else f"⏱ avto · hər {bk.iv_label(interval())}"
            if client is None or not client.is_connected():
                status = f"⚠️ userbot qoşulmayıb — yalnız lokal: {snap.name}"
            else:
                t = get("target", "me")
                try:
                    ent = await client.get_entity("me" if t == "me" else int(t))
                    await client.send_file(ent, str(snap.path), caption=bk.caption(snap, note), parse_mode="html",
                                           force_document=True, silent=not manual)
                    status = f"✅ {fmt_ts(time.time())} · {bk.human(snap.size)} → {target_title()}"
                except Exception as e:
                    logger.warning(f"💾 Userbot yedəyi göndərilmədi: {e}")
                    status = f"❌ göndərilmədi ({str(e)[:90]}) — lokalda: {snap.name}"
            await asyncio.to_thread(bk.prune, KIND, keep())
            put("last_status", status)
            logger.info(f"💾 Userbot bazası yedəyi: {status}")
            return status

    def scheduler():                                             # reload / silinmə → köhnə dövr dayanır
        return bk.auto_loop(run_backup, next_due, delay=45, label="Userbot avto-yedək xətası",
                            alive=lambda: ub.state.get("backup_gen") == gen and OWNER in ub.plugin_files,
                            check_every=CHECK_EVERY, log=logger)

    task = bk.restart_task(ub.state.get("backup_task"), scheduler)
    if task is not None:
        ub.state["backup_task"] = task

    # ── görünüş ──
    def body(note: str = "") -> str:
        iv = interval()
        due = next_due()
        rows = [
            ("Baza", f"{bk.human(bk.file_size(udb.path))} · userbot.db"),
            ("Hədəf", target_title()),
            ("Avto", f"hər {bk.iv_label(iv)}" if iv else "söndürülüb"),
        ]
        if iv:
            rows.append(("Növbəti", fmt_ts(due)))
        rows += [
            ("Lokal", f"{len(bk.local_list(KIND, include_pre=False))}/{keep()} nüsxə"),
            "# Son yedək",
            str(get("last_status") or "hələ alınmayıb"),
        ]
        if note:
            rows += ["", note]
        return ff(ub.title("backup"), rows, footer=".backup to @kanal · .restore (reply) · .backups")

    _WAIT_INPUT = {}

    def card_rows(o: Out) -> list:
        iv = interval()
        return [
            [o.btn(o.tr("📤 İndi yedəklə"), "now")],
            [o.btn(o.tr("⏱ Avto-yedək: {status}", status=('hər ' + bk.iv_label(iv)) if iv else o.tr("Söndürülüb")), "pick_iv")],
            [o.btn(o.tr("📁 Saxlama limiti: {count} nüsxə", count=keep()), "pick_k")],
            [o.btn(o.tr("🎯 Bu çata"), "here"), o.btn(o.tr("💾 Saved-ə"), "me")],
        ]

    def picker_card(o: Out, target: str, note: str = "") -> tuple[str, list]:
        if target == "iv":
            iv = interval()
            cur = ('hər ' + bk.iv_label(iv)) if iv else o.tr("Söndürülüb")
            title = o.tr("⏱ Avto-yedək intervalı")
        else:
            cur = f"{keep()} {o.tr('nüsxə')}"
            title = o.tr("📁 Saxlama limiti")

        body = [
            ("# " + title, ""),
            (o.tr("Cari dəyər"), cur),
            ("", ""),
            (o.tr("Tənzimləmə"), o.tr("Düymələrdən istifadə edin və ya çata rəqəm yazın")),
            ("• +", o.tr("maksimum / aktiv")),
            ("• -", o.tr("söndür / 0")),
        ]
        if note:
            body += ["", note]

        rows = [
            [
                o.btn("-5", f"vstep_{target}_-5"),
                o.btn("-1", f"vstep_{target}_-1"),
                o.btn("+1", f"vstep_{target}_1"),
                o.btn("+5", f"vstep_{target}_5"),
            ],
            [o.btn(o.tr("✏️ Öz rəqəmini yaz"), f"vinp_{target}")],
            [o.btn(o.tr("⬅️ Geri"), "back")],
        ]
        return ff(ub.title("backup"), body, footer="ləğv və ya geri üçün 'Geri' düyməsinə bas"), rows

    def set_iv(direction: int = 1) -> int:
        cur = interval()
        if direction == 1:
            if cur in bk.INTERVALS:
                new = next_of(bk.INTERVALS, cur)
            else:
                new = bk.INTERVALS[1] if bk.INTERVALS else 0
        elif direction == -1:
            if cur in bk.INTERVALS:
                new = bk.INTERVALS[(bk.INTERVALS.index(cur) - 1) % len(bk.INTERVALS)]
            else:
                new = 0
        else:
            new = 0 if cur else 1440
        put("iv", new)
        if new and not get("last"):
            put("last", int(time.time()))
        return new

    def wire(out: Out):
        async def redraw(o, note=""):
            _WAIT_INPUT.pop(o.chat_id, None)
            await o.update(body(note), rows=card_rows(o))

        @out.on("now")
        async def _(o, cb):
            _WAIT_INPUT.pop(o.chat_id, None)
            await o.update(body("⏳ yedək hazırlanır..."), rows=[])
            status = await run_backup(manual=True)
            await redraw(o, status)

        @out.on("pick_iv")
        async def _(o, cb):
            _WAIT_INPUT[o.chat_id] = {"target": "iv", "out": o}
            t, r = picker_card(o, "iv")
            await o.update(t, rows=r)

        @out.on("pick_k")
        async def _(o, cb):
            _WAIT_INPUT[o.chat_id] = {"target": "k", "out": o}
            t, r = picker_card(o, "k")
            await o.update(t, rows=r)

        @out.on("back")
        async def _(o, cb):
            await redraw(o)

        @out.on("vinp_iv")
        async def _(o, cb):
            _WAIT_INPUT[o.chat_id] = {"target": "iv", "out": o}
            await cb.answer(o.tr("✍️ Çata istədiyiniz intervalı, '+' və ya '-' yazın"), alert=False)

        @out.on("vinp_k")
        async def _(o, cb):
            _WAIT_INPUT[o.chat_id] = {"target": "k", "out": o}
            await cb.answer(o.tr("✍️ Çata istədiyiniz nüsxə sayını, '+' və ya '-' yazın"), alert=False)

        for tgt in ("iv", "k"):
            for d in (-5, -1, 1, 5):
                act_name = f"vstep_{tgt}_{d}"
                def _make_step(target=tgt, delta=d):
                    async def _step_handler(o, cb):
                        _WAIT_INPUT[o.chat_id] = {"target": target, "out": o}
                        note = ""
                        if target == "k":
                            cur = keep()
                            new_v = max(1, min(100, cur + delta))
                            put("keep", new_v)
                            await asyncio.to_thread(bk.prune, KIND, new_v)
                            note = f"✓ {new_v} nüsxə"
                        else:
                            cur = interval()
                            idx = bk.INTERVALS.index(cur) if cur in bk.INTERVALS else 0
                            new_idx = max(0, min(len(bk.INTERVALS) - 1, idx + delta))
                            new_v = bk.INTERVALS[new_idx]
                            put("iv", new_v)
                            if new_v and not get("last"):
                                put("last", int(time.time()))
                            note = f"✓ {('hər ' + bk.iv_label(new_v)) if new_v else 'söndürüldü'}"
                        t, r = picker_card(o, target, note)
                        await o.update(t, rows=r)
                    return _step_handler
                out.on(act_name)(_make_step(tgt, d))

        @out.on("here")
        async def _(o, cb):
            _WAIT_INPUT.pop(o.chat_id, None)
            note = await set_target(o.client, str(o.chat_id))
            await redraw(o, note)

        @out.on("me")
        async def _(o, cb):
            _WAIT_INPUT.pop(o.chat_id, None)
            put("target", "me")
            put("target_title", None)
            await redraw(o, "🎯 hədəf: Saved Messages")

        @out.on("noop")
        async def _(o, cb):
            pass

        async def refresh(o):
            await redraw(o)
        out.refresh = refresh

    @ub.raw(events.NewMessage(outgoing=True))
    async def on_user_val_input(event):
        info = _WAIT_INPUT.get(event.chat_id)
        if not info:
            return
        raw = (event.raw_text or "").strip().lower()
        if raw.startswith(".") or raw.startswith("/"):
            return
        _WAIT_INPUT.pop(event.chat_id, None)
        o = info.get("out")
        sctx = info.get("sctx")
        target = info.get("target")           # əvvəl oxunmurdu → hər daxiletmədə NameError
        with contextlib.suppress(Exception):
            await event.delete()
        note = ""
        if target == "iv":
            if raw in ("-", "off", "0", "söndür", "yox"):
                put("iv", 0)
                note = "🔴 avto-yedək söndürüldü"
            elif raw in ("+", "max", "sonsuz"):
                put("iv", 1440)
                note = "✅ avto-yedək: hər 24 saat"
            else:
                sec = parse_duration(raw)
                if sec:
                    put("iv", sec // 60)
                    note = f"✅ avto: hər {bk.iv_label(sec // 60)}"
                elif raw.isdigit():
                    put("iv", int(raw))
                    note = f"✅ avto: hər {bk.iv_label(int(raw))}"
                else:
                    note = "⚠️ düzgün rəqəm deyil"
        else:
            if raw in ("-", "off", "0", "min"):
                put("keep", 1)
                note = "📁 Saxlama limiti: 1 nüsxə"
            elif raw in ("+", "max"):
                put("keep", 20)
                note = "✅ Saxlama limiti: 20 nüsxə"
            elif raw.isdigit():
                val = max(1, min(100, int(raw)))
                put("keep", val)
                await asyncio.to_thread(bk.prune, KIND, val)
                note = f"✅ Saxlama limiti: {val} nüsxə"
            else:
                note = "⚠️ düzgün rəqəm deyil"

        if sctx:
            await sctx.show(*section_picker_view(sctx, target, note))
        elif o:
            t, r = picker_card(o, target, note)
            await o.update(t, rows=r)

    async def set_target(client, raw: str) -> str:
        raw = (raw or "").strip()
        from common import destinations as dest
        if raw.lower() in ("me", "saved", "self"):
            put("target", "me")
            put("target_title", None)
            dest.put("backup", "me", title="Saved Messages")
            return "🎯 hədəf: Saved Messages"
        try:
            res = await dest.resolve(client, raw)
            peer_id = res["chat_id"]
            title = res["title"]
            thread_id = res.get("thread_id")
            put("target", str(peer_id))
            put("target_title", title)
            dest.put("backup", peer_id, thread_id=thread_id, title=title, chat_type=res.get("type"))
            return f"🎯 hədəf: {title}"
        except Exception as e:
            return f"❌ tapılmadı: {str(e)[:120]}"

    # ── .backup ──
    @ub.command("backup", pattern=r"^\.backup(?:\s+(\S+)(?:\s+(.+))?)?$",
                help=("bazanı yedəklə / ayarlar",
                      "Userbot bazasını (filtrlər, mute, ayarlar...) yedəkləyir və seçdiyin kanala göndərir.\n\n"
                      "<b>İstifadə:</b>\n• <code>.backup</code> — ayarlar kartı (düymələrlə)\n"
                      "• <code>.backup now</code> — dərhal yedəklə\n"
                      "• <code>.backup to @kanal</code> · <code>here</code> · <code>me</code> — hədəf\n"
                      "• <code>.backup every 1d</code> · <code>6h</code> · <code>off</code> — avto-yedək\n"
                      "• <code>.backup keep 10</code> — lokal nüsxə sayı\n\n"
                      "Bərpa: yedəyə reply edib <code>.restore</code>. Lokal siyahı: <code>.backups</code>"))
    async def on_backup(event):
        sub = (event.pattern_match.group(1) or "").lower()
        arg = (event.pattern_match.group(2) or "").strip()
        note = ""
        if sub == "now":
            await safe_edit(event, "💾 <i>Yedəklənir...</i>")
            await run_backup(manual=True)                        # nəticə kartda "Son yedək" sətrindədir
        elif sub == "to":
            note = await set_target(event.client, arg or str(event.chat_id)) if arg.lower() != "here" \
                else await set_target(event.client, str(event.chat_id))
        elif sub == "every":
            if arg.lower() in ("off", "0", "söndür", "yox"):
                put("iv", 0)
                note = "⏱ avto-yedək söndürüldü"
            else:
                sec = parse_duration(arg)
                if not sec or sec < 600:
                    note = "❌ müddət: 30m · 6h · 1d · 3d · off (ən az 10 dəq.)"
                else:
                    put("iv", sec // 60)
                    if not get("last"):
                        put("last", int(time.time()))
                    note = f"⏱ avto: hər {bk.iv_label(sec // 60)}"
        elif sub == "keep":
            if arg.isdigit() and 1 <= int(arg) <= 100:
                put("keep", int(arg))
                await asyncio.to_thread(bk.prune, KIND, keep())
                note = f"📁 {arg} lokal nüsxə saxlanılacaq"
            else:
                note = "❌ say 1–100 arasında olmalıdır"
        elif sub:
            note = "❓ naməlum: .backup [now | to | every | keep]"

        out = Out(ub, event)
        wire(out)
        await out.open(body(note), card_rows(out))

    # ── .backups ──
    def list_body(note=""):
        files = bk.local_list(KIND)[:10]
        rows = [(f"{i + 1}{'🛟' if bk.PRE_TAG in p.name else ''}", f"{p.name[len(KIND) + 1:].replace('.db.gz', '')} · "
                 f"{bk.human(p.stat().st_size)}") for i, p in enumerate(files)] or ["lokal yedək yoxdur"]
        if note:
            rows += ["", note]
        return ff(ub.title("backups"), rows, footer="bərpa: .restore <n> · 🛟 = bərpadan əvvəlki avtomatik yedək")

    @ub.command("backups", help=("lokal yedəklər",
                                 "data/backups-dakı userbot yedəklərinin siyahısı. Bərpa: <code>.restore n</code>"))
    async def on_backups(event):
        out = Out(ub, event)

        async def refresh(o):
            await o.update(list_body())
        out.refresh = refresh
        await out.open(list_body())

    # ── .restore ──
    async def after_restore(keep_settings: dict) -> list:
        """Yedəkləmə ayarlarını qaytar + digər pluginləri yenidən yüklə (yaddaşdakı vəziyyət yeni bazadan)."""
        with udb._lock:
            udb._conn.execute("DELETE FROM kv WHERE ns=?", (NS,))
            udb._conn.commit()
        for k, v in keep_settings.items():
            udb.kv_set(NS, k, v)
        reloaded, failed = [], []
        load = ub.state.get("load_plugin_file")
        for name, path in list(ub.plugin_files.items()):
            if name == OWNER or load is None:
                continue
            try:
                r = load(path)
                if asyncio.iscoroutine(r):
                    await r
                reloaded.append(name)
            except Exception as e:
                failed.append(f"{name}: {str(e)[:60]}")
                logger.warning(f"💾 bərpadan sonra {name} yenidən yüklənmədi: {e}")
        return [f"🔄 {len(reloaded)} plugin yenidən yükləndi"] + [f"⚠️ {f}" for f in failed]

    @ub.command("restore", pattern=r"^\.restore(?:\s+(\d+))?$",
                help=("bazanı yedəkdən bərpa et",
                      "Yedək sənədinə (<code>.db</code> / <code>.db.gz</code>) <b>reply</b> edib "
                      "<code>.restore</code> yaz — yoxlanılır, cari baza ilə müqayisə göstərilir, ✅ ilə təsdiqləyirsən.\n"
                      "<code>.restore 2</code> — <code>.backups</code> siyahısındakı 2-ci lokal yedəkdən.\n\n"
                      "Bərpadan əvvəl cari baza avtomatik yedəklənir (🛟). Bot bazası üçün botda /backup."))
    async def on_restore(event):
        num = event.pattern_match.group(1)
        temp = None
        if num:
            files = bk.local_list(KIND)[:10]
            i = int(num) - 1
            if not 0 <= i < len(files):
                await safe_edit(event, "❌ belə nömrə yoxdur — <code>.backups</code> ilə siyahıya bax")
                return
            src, source = files[i], f"lokal: {files[i].name}"
        else:
            reply = await event.get_reply_message() if event.is_reply else None
            if not reply or not reply.document:
                await safe_edit(event, "ℹ️ Yedək sənədinə (.db / .db.gz) <b>reply</b> edib <code>.restore</code> yaz "
                                       "və ya <code>.restore n</code> (<code>.backups</code>).")
                return
            await safe_edit(event, "⏳ <i>Yüklənir və yoxlanılır...</i>")
            bk.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            temp = bk.BACKUP_DIR / f".import_ub_{int(time.time())}_{reply.id}"
            try:
                await event.client.download_media(reply, file=str(temp))
            except Exception as e:
                bk.cleanup(temp)
                await safe_edit(event, f"❌ yüklənmədi: <code>{escape(str(e))[:200]}</code>")
                return
            src = temp
            fname = getattr(reply.file, "name", None) or "sənəd"
            source = f"fayl: {fname}"

        info = await asyncio.to_thread(bk.inspect, src)
        unpacked = info["db_path"] if info["db_path"] and info["db_path"] != str(src) else None
        if not info["ok"] or info["kind"] != KIND:
            bk.cleanup(unpacked, temp)
            msg = ("❌ Bu <b>bot</b> bazasıdır — onu botda /backup → 📥 Import ilə bərpa et."
                   if info["ok"] and info["kind"] == "bot" else f"❌ {escape(info['error'] or 'uyğun deyil')}")
            await safe_edit(event, msg)
            return

        with udb._lock:
            cur = {n: udb._conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                   for (n,) in udb._conn.execute(
                       "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        rows = [("Mənbə", source), ("Ölçü", bk.human(info["raw_size"])), ("Yoxlama", "✓ sağlam · userbot"),
                "# indi → yedəkdə"]
        rows += [(n, f"{a} → {b}") for n, a, b in bk.compare_line(cur, info["tables"])]
        rows += ["", "Cari baza ƏVƏZ olunacaq.", "Əvvəl avtomatik 🛟 yedək alınır."]

        out = Out(ub, event)
        done = {"v": False}

        @out.on("ok")
        async def _(o, cb):
            if done["v"]:
                return
            done["v"] = True
            await o.update(ff(ub.title("restore"), ["♻️ bərpa olunur..."]), rows=[])
            keep_settings = udb.kv_all(NS)
            try:
                async with lock:
                    pre = await asyncio.to_thread(bk.restore, KIND, udb._conn, udb._lock, info["db_path"])
                notes = await after_restore(keep_settings)
                res = [("Nəticə", "✓ bərpa olundu"), ("🛟 Əvvəlki", pre.name), *notes,
                       "", "geri qaytarmaq: .backups → .restore <n>"]
                logger.warning(f"💾 Userbot bazası bərpa olundu ({source}); əvvəlki: {pre.name}")
            except Exception as e:
                logger.error(f"💾 Userbot bərpası alınmadı: {e}", exc_info=True)
                res = [("Nəticə", "✗ alınmadı"), ("Xəta", str(e)[:150]), "cari baza dəyişməyib"]
            finally:
                bk.cleanup(unpacked, temp)
            await o.update(ff(ub.title("restore"), res), rows=[])

        @out.on("no")
        async def _(o, cb):
            done["v"] = True
            bk.cleanup(unpacked, temp)
            await o.close()

        await out.open(ff(ub.title("restore"), rows), [[out.btn("✅ Bərpa et", "ok"), out.btn("✖️ Ləğv et", "no")]])

    # ── .menu paneli ──
    ub.add_help("p_backup", "💾 Yedək", "userbot bazasının yedəyi",
                "Son yedək, avto interval, hədəf; 📤 indi yedəklə, ⏱ avto-yedək. Ətraflı: <code>.backup</code>", "panel")

    def section_picker_view(sctx, target: str, note: str = ""):
        if target == "iv":
            val = interval()
            cur_str = ("hər " + bk.iv_label(val)) if val else sctx.tr("Söndürülüb")
            title = sctx.tr("⏱ Avto-yedək intervalı")
        else:
            val = keep()
            cur_str = f"{val} {sctx.tr('nüsxə')}"
            title = sctx.tr("📁 Saxlama limiti")

        L = [
            f"<b>{title}</b>",
            "",
            f"🔢 <b>{sctx.tr('Cari dəyər')}:</b> <b>{cur_str}</b>",
            "",
            f"<i>{sctx.tr('Dəyəri dəyişmək üçün aşağıdakı düymələrdən istifadə edin:')}</i>",
            f"• <b>+</b> — {sctx.tr('maksimum / aktiv')}",
            f"• <b>-</b> — {sctx.tr('söndür / 0')}",
        ]
        if note:
            L += ["", note]

        rows = [
            [
                sctx.btn("-5", "vstep", target, "-5"),
                sctx.btn("-1", "vstep", target, "-1"),
                sctx.btn("+1", "vstep", target, "1"),
                sctx.btn("+5", "vstep", target, "5"),
            ],
            [sctx.btn(sctx.tr("✏️ Öz rəqəmini yaz"), "vinp", target)],
            [sctx.btn(sctx.tr("⬅️ Geri"), "back")],
        ]
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    def section_view(sctx, note=""):
        iv = interval()
        L = ["💾 <b>Userbot bazası — yedək</b>", "",
             f"🗄 Baza: {bk.human(bk.file_size(udb.path))}",
             f"🎯 Hədəf: {escape(target_title())}",
             f"⏱ Avto: {('hər ' + bk.iv_label(iv) + ' · növbəti ' + fmt_ts(next_due())) if iv else '🔴 söndürülüb'}",
             f"📁 Lokal: {len(bk.local_list(KIND, include_pre=False))}/{keep()}",
             f"🕒 Son: {escape(str(get('last_status') or 'hələ alınmayıb'))}",
             "", "<i>Hədəf / bərpa: .backup to @kanal · .restore (reply)</i>"]
        if note:
            L += ["", note]
        rows = [
            [sctx.btn(sctx.tr("📤 İndi yedəklə"), "now")],
            [sctx.btn(sctx.tr("⏱ Avto-yedək: {status}", status=('hər ' + bk.iv_label(iv)) if iv else sctx.tr("Söndürülüb")), "pick_iv")],
            [sctx.btn(sctx.tr("📁 Saxlama limiti: {count} nüsxə", count=keep()), "pick_k")],
            [sctx.btn(sctx.tr("🔄 Yenilə"), "r")],
            sctx.nav(),
        ]
        return "\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows)

    @ub.section("backup", "💾 Yedək", order=45)
    async def render(sctx):
        return section_view(sctx)

    @ub.on_section("backup")
    async def handle(sctx):
        note = ""
        if sctx.action == "now":
            await sctx.answer("📤 Yedəklənir...")
            await sctx.show(*section_view(sctx, "⏳ <i>yedək hazırlanır...</i>"))
            note = escape(await run_backup(manual=True))
        elif sctx.action == "pick_iv":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.answer()
            await sctx.show(*section_picker_view(sctx, "iv"))
            return
        elif sctx.action == "pick_k":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.answer()
            await sctx.show(*section_picker_view(sctx, "k"))
            return
        elif sctx.action == "vinp" and len(sctx.args) >= 1:
            target = sctx.args[0]
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT[chat_id] = {"target": target, "sctx": sctx}
            await sctx.answer()
            cur_title = sctx.tr("⏱ Avto-yedək intervalı") if target == "iv" else sctx.tr("📁 Saxlama limiti")
            cur_val = (('hər ' + bk.iv_label(interval())) if interval() else sctx.tr("Söndürülüb")) if target == "iv" else f"{keep()} {sctx.tr('nüsxə')}"
            L = [
                f"<b>{cur_title}</b>",
                "",
                f"🔢 <b>{sctx.tr('Cari dəyər')}:</b> <b>{cur_val}</b>",
                "",
                f"✍️ <b>{sctx.tr('Dəyəri daxil edin')}:</b>",
                f"• <b>{sctx.tr('Rəqəm')}</b> (məs: <code>{60 if target == 'iv' else 5}</code>)",
                f"• <b>+</b> — {sctx.tr('maksimum')}",
                f"• <b>-</b> — {sctx.tr('minimum / söndür')}",
            ]
            rows = [[sctx.btn(sctx.tr("✖️ Ləğv et"), f"pick_{target}")]]
            await sctx.show("\n".join(L), InlineKeyboardMarkup(inline_keyboard=rows))
            return
        elif sctx.action == "vstep" and len(sctx.args) >= 2:
            target = sctx.args[0]
            delta = int(sctx.args[1])
            if target == "k":
                cur = keep()
                new_v = max(1, min(100, cur + delta))
                put("keep", new_v)
                await asyncio.to_thread(bk.prune, KIND, new_v)
                note = f"✓ {new_v} nüsxə"
                await sctx.answer(note)
            else:
                cur = interval()
                idx = bk.INTERVALS.index(cur) if cur in bk.INTERVALS else 0
                new_idx = max(0, min(len(bk.INTERVALS) - 1, idx + delta))
                new_v = bk.INTERVALS[new_idx]
                put("iv", new_v)
                if new_v and not get("last"):
                    put("last", int(time.time()))
                note = f"✓ {('hər ' + bk.iv_label(new_v)) if new_v else 'söndürüldü'}"
                await sctx.answer(note)
            await sctx.show(*section_picker_view(sctx, target, note))
            return
        elif sctx.action == "back":
            chat_id = sctx.menu.get("chat_id")
            if chat_id:
                _WAIT_INPUT.pop(chat_id, None)
            await sctx.answer()
            await sctx.show(*section_view(sctx))
            return
        elif sctx.action == "iv":
            d = (sctx.args or ["+"])[0]
            new = set_iv({"-": -1, "+": 1}.get(d, 1))
            await sctx.answer(f"⏱ {('hər ' + bk.iv_label(new)) if new else 'avto söndürüldü'}")
        else:
            await sctx.answer()
        await sctx.show(*section_view(sctx, note))
