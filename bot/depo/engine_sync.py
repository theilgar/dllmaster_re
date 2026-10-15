"""
🧹 bot/depo/engine_sync.py - Filler: depo sinxronu, dublikat və novid təmizliyi (engine.py-dən ayrılıb).
"""
from bot.depo.engine_common import *  # noqa: F401,F403


class SyncMixin:
    # ── 🧹 depo ↔ baza sinxronizasiyası ──
    # ── 🧹 depo ↔ baza sinxronizasiyası ──
    def _depo_msg_ids(self, depo_id, checked_before: int = None, limit: int = None) -> list:
        db = get_db()
        return db.cache_depo_mids(depo_id, checked_before=checked_before, limit=limit)

    async def _active_tg_workers(self, depo_id) -> list:
        """Aktiv olan bütün userbot client-lərini və onların depo kanalı entity-lərini qaytarır:
        [(label, client, entity), ...]"""
        workers = []

        # 1. Əsas userbot
        if self.tg and await self.tg.ok():
            try:
                ent = await self._depo_entity(depo_id, client=self.tg.client)
                workers.append(("1️⃣ Əsas userbot", self.tg.client, ent))
            except Exception as e:
                logger.warning(f"1-ci userbot depo entity xətası: {e}")

        # 2. Canlı yayım asistanı (və ya digər slotlar)
        va = getattr(self.ctx, "voice_assistant", None)
        if va:
            for sl in getattr(va, "slots", []):
                c = getattr(sl, "client", None)
                if c and getattr(c, "is_connected", lambda: False)():
                    if any(c is w[1] for w in workers):
                        continue
                    try:
                        ent2 = await self._depo_entity(depo_id, client=c)
                        workers.append((f"{len(workers)+1}️⃣ {sl.name()}", c, ent2))
                    except Exception as e:
                        logger.debug(f"{sl.name()} depo entity alına bilmədi: {e}")
        return workers

    async def _depo_entity(self, depo_id, client=None):
        c = client or (self.tg.client if self.tg else None)
        if not c:
            raise RuntimeError("userbot client yoxdur")
        ref = audio_cache.depo_setting()
        target = ({"type": "id", "id": int(ref)} if ref.lstrip("-").isdigit()
                  else {"type": "user", "name": ref.lstrip("@").split("/")[-1]})
        try:
            ent = await c.get_entity(target["name"] if target["type"] == "user" else target["id"])
        except Exception:
            try:
                ent = await c.get_entity(depo_id)
            except Exception:
                await c.get_dialogs(limit=50)
                ent = await c.get_entity(depo_id)
        if tl_utils.get_peer_id(ent) != depo_id:            # səhv çat — hər şey "yoxdur" görünərdi
            raise RuntimeError(f"userbot başqa çat açdı ({tl_utils.get_peer_id(ent)} ≠ {depo_id})")
        return ent

    async def sync_scan(self, progress=None, should_stop=lambda: False, allow_bot=True,
                        mode: str = "quick", cutoff_days: int = 7) -> dict:
        """Bazadakı depo postlarının həqiqətən kanalda olub-olmadığını yoxlayır (heç nə silmir)."""
        bot = self.ctx.bot
        depo_id = await audio_cache.depo_chat_id(bot)
        if not depo_id:
            raise RuntimeError("depo kanalı əlçatan deyil")

        db = get_db()
        now = int(time.time())
        cutoff = now - (cutoff_days * 86400) if cutoff_days else None

        workers = await self._active_tg_workers(depo_id)
        if workers:
            n_ub = len(workers)
            # 1) Tez rejim: yalnız yeni və ya son cutoff_days gündə yoxlanmamış postlar
            if mode == "quick":
                ids = await asyncio.to_thread(self._depo_msg_ids, depo_id, checked_before=cutoff)
                total_to_check = len(ids)

                # Əgər yoxlanılmalı postlar azdırsa (<= 5000 * n_ub): sürətli paralel batch get_messages
                if total_to_check <= 5000 * n_ub:
                    method_name = f"{n_ub} userbot (paralel tez)" if n_ub > 1 else "userbot (tez)"
                    st = {"depo_id": depo_id, "total": total_to_check, "checked": 0, "missing": [],
                          "unknown": 0, "method": method_name, "stopped": False, "started": time.time()}
                    if not ids:
                        return st

                    queue = asyncio.Queue()
                    for i in range(0, len(ids), 100):
                        queue.put_nowait(ids[i:i + 100])

                    confirmed = []
                    lock = asyncio.Lock()

                    async def batch_worker(label, cl, ent):
                        while not queue.empty() and not should_stop():
                            try:
                                batch = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            try:
                                msgs = await cl.get_messages(ent, ids=batch)
                                c_loc, m_loc = [], []
                                for mid, m in zip(batch, msgs):
                                    if m is None or getattr(m, "media", None) is None:
                                        m_loc.append(mid)
                                    else:
                                        c_loc.append(mid)
                                async with lock:
                                    confirmed.extend(c_loc)
                                    st["missing"].extend(m_loc)
                                    st["checked"] += len(batch)
                                if progress:
                                    await progress(st)
                            except (ConnectionError, asyncio.CancelledError):
                                if should_stop() or not getattr(cl, "is_connected", lambda: True)():
                                    st["stopped"] = True
                                    break
                                queue.put_nowait(batch)
                            except Exception as e:
                                logger.warning(f"Batch xətası ({label}): {e}")
                                wait = getattr(e, "seconds", None)
                                if wait:
                                    await asyncio.sleep(min(wait, 10))
                                    queue.put_nowait(batch)
                                else:
                                    async with lock:
                                        st["unknown"] += len(batch)
                            await asyncio.sleep(0.05)

                    await asyncio.gather(*(batch_worker(lbl, cl, ent) for lbl, cl, ent in workers))
                    if should_stop():
                        st["stopped"] = True
                    if confirmed:
                        await asyncio.to_thread(db.cache_mark_checked_mids, depo_id, confirmed, now)
                    return st

            # 2) Tam rejim və ya çoxlu yoxlanmamış post olduqda: Telethon sürətli musiqi axını
            all_ids = set(await asyncio.to_thread(self._depo_msg_ids, depo_id))
            method_name = f"{n_ub} userbot (paralel axın)" if n_ub > 1 else "userbot (axın)"
            st = {"depo_id": depo_id, "total": len(all_ids), "checked": 0, "missing": [],
                  "unknown": 0, "method": method_name, "stopped": False, "started": time.time()}

            channel_mids = set()
            mids_lock = asyncio.Lock()

            if n_ub >= 2 and all_ids:
                min_all, max_all = min(all_ids), max(all_ids)
                step = (max_all - min_all) / n_ub
                slices = []
                for i in range(n_ub):
                    low = int(min_all + i * step) if i > 0 else 0
                    high = int(min_all + (i + 1) * step) if i < n_ub - 1 else 0
                    slices.append((low, high + 1 if high else 0))

                async def stream_part(cl, ent, min_id, max_id):
                    kwargs = {"filter": InputMessagesFilterMusic}
                    if min_id:
                        kwargs["min_id"] = min_id
                    if max_id:
                        kwargs["max_id"] = max_id
                    buf = []
                    try:
                        async for m in cl.iter_messages(ent, **kwargs):
                            if should_stop():
                                st["stopped"] = True
                                break
                            buf.append(m.id)
                            if len(buf) >= 300:
                                async with mids_lock:
                                    channel_mids.update(buf)
                                    st["checked"] = min(len(channel_mids), st["total"])
                                buf.clear()
                                if progress:
                                    await progress(st)
                    except (ConnectionError, asyncio.CancelledError):
                        st["stopped"] = True
                    if buf:
                        async with mids_lock:
                            channel_mids.update(buf)
                            st["checked"] = min(len(channel_mids), st["total"])
                        if progress:
                            with contextlib.suppress(Exception):
                                await progress(st)

                await asyncio.gather(*(
                    stream_part(w[1], w[2], s[0], s[1])
                    for w, s in zip(workers, slices)
                ))
            else:
                cl, ent = workers[0][1], workers[0][2]
                try:
                    async for m in cl.iter_messages(ent, filter=InputMessagesFilterMusic):
                        if should_stop():
                            st["stopped"] = True
                            break
                        channel_mids.add(m.id)
                        st["checked"] = min(len(channel_mids), st["total"])
                        if progress and len(channel_mids) % 1000 == 0:
                            await progress(st)
                except (ConnectionError, asyncio.CancelledError):
                    st["stopped"] = True

            if progress:
                await progress(st)

            if st["stopped"]:
                found = channel_mids & all_ids
                if found:
                    await asyncio.to_thread(db.cache_mark_checked_mids, depo_id, found, now)
                return st

            missing_candidates = sorted(all_ids - channel_mids)
            real_missing = []

            # Namizəd silinmişləri dəqiq yoxla (false-positive olmasın) — paralel get_messages
            if missing_candidates:
                limit_cand = 5000 * n_ub
                if len(missing_candidates) <= limit_cand:
                    queue_cand = asyncio.Queue()
                    for i in range(0, len(missing_candidates), 100):
                        queue_cand.put_nowait(missing_candidates[i:i + 100])

                    cand_lock = asyncio.Lock()

                    async def cand_worker(lbl, cl, ent):
                        while not queue_cand.empty() and not should_stop():
                            try:
                                chunk = queue_cand.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            try:
                                cmsgs = await cl.get_messages(ent, ids=chunk)
                                loc_miss, loc_found = [], []
                                for mid, cm in zip(chunk, cmsgs):
                                    if cm is None or getattr(cm, "media", None) is None:
                                        loc_miss.append(mid)
                                    else:
                                        loc_found.append(mid)
                                async with cand_lock:
                                    real_missing.extend(loc_miss)
                                    channel_mids.update(loc_found)
                            except Exception:
                                async with cand_lock:
                                    real_missing.extend(chunk)
                            await asyncio.sleep(0.05)

                    await asyncio.gather(*(cand_worker(w[0], w[1], w[2]) for w in workers))
                else:
                    real_missing = missing_candidates

            if st.get("stopped"):
                st["missing"] = []
                return st

            st["missing"] = sorted(real_missing)
            st["checked"] = st["total"]

            # Kanalda tapılan bütün postların checked vaxtını yenilə
            await asyncio.to_thread(db.cache_mark_all_checked, depo_id, exclude_mids=real_missing, ts=now)
            return st

        if not allow_bot:
            raise RuntimeError("userbot aktiv deyil")

        # Bot API ehtiyat yolu
        if mode == "quick":
            ids = await asyncio.to_thread(self._depo_msg_ids, depo_id, checked_before=cutoff, limit=500)
        else:
            ids = await asyncio.to_thread(self._depo_msg_ids, depo_id)

        st = {"depo_id": depo_id, "total": len(ids), "checked": 0, "missing": [], "unknown": 0,
              "method": "bot", "stopped": False, "started": time.time()}

        confirmed_bot = []
        for mid in ids:
            if should_stop():
                st["stopped"] = True
                break
            while True:
                try:
                    await bot.edit_message_reply_markup(chat_id=depo_id, message_id=mid, reply_markup=None)
                    confirmed_bot.append(mid)
                    break
                except Exception as e:
                    wait = getattr(e, "retry_after", None)
                    if wait:
                        await asyncio.sleep(wait + 1)
                        continue
                    err = str(e).lower()
                    if "not modified" in err:
                        confirmed_bot.append(mid)
                    elif "not found" in err or "message_id_invalid" in err:
                        st["missing"].append(mid)
                    else:
                        st["unknown"] += 1
                    break
            st["checked"] += 1
            if progress and st["checked"] % 20 == 0:
                await progress(st)
            await asyncio.sleep(0.3)

        if confirmed_bot:
            await asyncio.to_thread(db.cache_mark_checked_mids, depo_id, confirmed_bot, now)
        return st

    # ── 🧬 Depo indeksi: kanal ↔ baza tam uyğunlaşdırma və dublikatlar ──
    async def dedupe_scan(self, progress=None, should_stop=lambda: False) -> dict:
        """Depo kanalının BÜTÜN audio postlarını userbot ilə oxuyur (heç nə dəyişmir)."""
        bot = self.ctx.bot
        depo_id = await audio_cache.depo_chat_id(bot)
        if not depo_id:
            raise RuntimeError("depo kanalı əlçatan deyil")
        workers = await self._active_tg_workers(depo_id)
        if not workers:
            raise RuntimeError("userbot aktiv deyil — kanal tarixçəsini yalnız userbot oxuya bilər")

        n_ub = len(workers)
        method_name = f"{n_ub} userbot (paralel axın)" if n_ub > 1 else "userbot"
        posts, st = [], {"depo_id": depo_id, "scanned": 0, "stopped": False, "method": method_name, "started": time.time()}
        posts_lock = asyncio.Lock()

        def make_post(m):
            doc = getattr(m, "document", None)
            if doc is None:
                return None
            a = next((x for x in doc.attributes if isinstance(x, DocumentAttributeAudio)), None)
            return {"mid": m.id, "vid": _post_vid(m), "performer": (a.performer if a else "") or "",
                    "title": (a.title if a else "") or "", "duration": int(a.duration or 0) if a else 0,
                    "size": getattr(doc, "size", 0) or 0}

        if n_ub >= 2:
            w_lead = workers[0]
            latest = await w_lead[1].get_messages(w_lead[2], limit=1)
            max_mid = latest[0].id if latest else 0

            async def scan_part(cl, ent, min_id, max_id):
                kwargs = {"filter": InputMessagesFilterMusic}
                if min_id:
                    kwargs["min_id"] = min_id
                if max_id:
                    kwargs["max_id"] = max_id
                buf = []
                try:
                    async for m in cl.iter_messages(ent, **kwargs):
                        if should_stop():
                            st["stopped"] = True
                            break
                        p = make_post(m)
                        if p:
                            buf.append(p)
                        if len(buf) >= 200:
                            async with posts_lock:
                                posts.extend(buf)
                                st["scanned"] = len(posts)
                            buf.clear()
                            if progress:
                                await progress(st)
                except (ConnectionError, asyncio.CancelledError):
                    st["stopped"] = True
                if buf:
                    async with posts_lock:
                        posts.extend(buf)
                        st["scanned"] = len(posts)
                    if progress:
                        with contextlib.suppress(Exception):
                            await progress(st)

            if max_mid > 0:
                step = max_mid / n_ub
                slices = []
                for i in range(n_ub):
                    low = int(i * step) if i > 0 else 0
                    high = int((i + 1) * step) if i < n_ub - 1 else 0
                    slices.append((low, high + 1 if high else 0))

                await asyncio.gather(*(
                    scan_part(w[1], w[2], s[0], s[1])
                    for w, s in zip(workers, slices)
                ))
            else:
                cl, ent = workers[0][1], workers[0][2]
                try:
                    async for m in cl.iter_messages(ent, filter=InputMessagesFilterMusic):
                        if should_stop():
                            st["stopped"] = True
                            break
                        p = make_post(m)
                        if p:
                            posts.append(p)
                            st["scanned"] = len(posts)
                        if progress and len(posts) % 300 == 0:
                            await progress(st)
                except (ConnectionError, asyncio.CancelledError):
                    st["stopped"] = True
        else:
            cl, ent = workers[0][1], workers[0][2]
            async for m in cl.iter_messages(ent, filter=InputMessagesFilterMusic):
                if should_stop():
                    st["stopped"] = True
                    break
                p = make_post(m)
                if p:
                    posts.append(p)
                    st["scanned"] = len(posts)
                    if progress and len(posts) % 300 == 0:
                        await progress(st)

        st["posts"] = posts
        st.update(await asyncio.to_thread(self._dedupe_analyze, depo_id, posts))
        return st

    @staticmethod
    def _dedupe_analyze(depo_id, posts) -> dict:
        db = get_db()
        with db.sql() as cur:
            rows = [dict(r) for r in cur.execute(
                """SELECT video_id, file_id, unique_id, title, performer, duration, size, chat_id, message_id, hits
                   FROM audio_cache WHERE chat_id = %s""", (depo_id,)).fetchall()]
            known = {r[0] for r in cur.execute("SELECT video_id FROM audio_cache").fetchall()}
        by_mid = {}                                   # post → ona baxan baza sətirləri
        for r in rows:
            by_mid.setdefault(r["message_id"], []).append(r)
        post_ids = {p["mid"] for p in posts}
        hits = {mid: sum(r.get("hits") or 0 for r in rs) for mid, rs in by_mid.items()}

        def rank(p):                                  # kanonik post: bazada istifadə olunan, çox istənilən, ən köhnə
            return (0 if p["mid"] in by_mid else 1, -hits.get(p["mid"], 0), p["mid"])

        # 1) eyni video ID — bir neçə post
        groups = {}
        novid = []
        for p in posts:
            (groups.setdefault(p["vid"], []) if p["vid"] else novid).append(p)
        canon, dups = [], {}                          # dups: dublikat post → kanonik post
        same_vid = 0
        for vid, ps in groups.items():
            ps.sort(key=rank)
            canon.append(ps[0])
            for d in ps[1:]:
                dups[d["mid"]] = ps[0]["mid"]
                same_vid += 1
        # 2) eyni mahnı, fərqli video ID (ad + artist + müddət ±8 san.)
        same_song = 0
        buckets = {}
        for p in canon + novid:
            if (p["performer"] or "").strip() in ("", "YouTube"):
                continue
            tt = names.track_tokens(p["title"])
            if tt:
                buckets.setdefault(tt, []).append(p)
        song_groups = []
        for tt, ps in buckets.items():
            ps.sort(key=rank)
            used = set()
            for i, head in enumerate(ps):
                if head["mid"] in used:
                    continue
                at = names.artist_tokens(head["performer"])
                grp = [head]
                for q in ps[i + 1:]:
                    if q["mid"] in used or not (names.artist_tokens(q["performer"]) & at):
                        continue
                    if head["duration"] and q["duration"] and abs(head["duration"] - q["duration"]) > 8:
                        continue
                    grp.append(q)
                    used.add(q["mid"])
                used.add(head["mid"])
                if len(grp) > 1:
                    song_groups.append(grp)
                    for q in grp[1:]:
                        dups[q["mid"]] = head["mid"]
                        same_song += 1
        # zəncir: dublikatın kanoniki özü də dublikatdırsa — sona qədər
        for mid in list(dups):
            seen = {mid}
            while dups[mid] in dups and dups[mid] not in seen:
                seen.add(dups[mid])
                dups[mid] = dups[dups[mid]]
        # 3) bazada olmayan postlar (video ID var, amma bot bu mahnını tanımır → yenidən yükləyirdi)
        orphans = [p for p in posts if p["vid"] and p["vid"] not in known]
        # 4) bazası dublikat posta baxan sətirlər — kanonik posta yönəldiləcək
        repoint = sum(len(by_mid.get(m, [])) for m in dups)
        samples = [[f"{q['performer']} - {q['title']}"[:60] + (f" ({q['vid']})" if q["vid"] else "") for q in g[:3]]
                   for g in song_groups[:6]]
        # 5) ID-siz postlar ki, bazada heç bir sətir onlara baxmır (əl ilə atılmış / başqa mənbədən) —
        #    video ID bilinmədiyi üçün axtarışda görünmür; 🔎 ilə YouTube-da tapılıb bağlana bilər
        novid_posts = [p for p in novid if p["mid"] not in by_mid and p["mid"] not in dups and p["title"]]
        return {"novid_posts": novid_posts,
                "total": len(posts), "in_db": len(post_ids & set(by_mid)), "novid": len(novid),
                "orphans": orphans, "dups": dups, "same_vid": same_vid, "same_song": same_song,
                "repoint": repoint, "samples": samples, "missing_db": len(set(by_mid) - post_ids)}

    def dedupe_link(self, st: dict) -> dict:
        """Dəyişmədən (heç nə silmədən): bazada olmayan postları bazaya yazır, dublikat postlara baxan
        sətirləri kanonik posta yönəldir. Bundan sonra bu mahnılar heç vaxt yenidən yüklənmir."""
        depo_id = st["depo_id"]
        posts = {p["mid"]: p for p in st["posts"]}
        db = get_db()
        pool = getattr(self.ctx, "depo_helpers", None)
        now = int(time.time())
        tmpl = {r["message_id"]: dict(r) for r in map(dict, db.sql_all(
            """SELECT file_id, unique_id, title, performer, duration, size, chat_id, message_id
               FROM audio_cache WHERE chat_id = %s""", (depo_id,)))}

        def template(mid):
            """Kanonik postun keş sətri; bazada yoxdursa — tənbəl çevrilən yer tutucu file_id."""
            if mid in tmpl:
                return tmpl[mid]
            p = posts[mid]
            fid = f"depo:{depo_id}:{mid}"            # 🐢 köməkçi botların lazy çeviricisi ilə real file_id-yə çevrilir
            t = {"file_id": fid, "unique_id": None, "title": p["title"], "performer": p["performer"],
                 "duration": p["duration"], "size": p["size"], "chat_id": depo_id, "message_id": mid}
            with db.sql() as cur:
                cur.execute("""INSERT INTO depo_foreign
                    (file_id, unique_id, chat_id, message_id, main_file_id, created) VALUES (%s,%s,%s,%s,NULL,%s)
                    ON CONFLICT DO NOTHING""", (fid, None, depo_id, mid, now))
            if pool is not None:
                pool.foreign[fid] = (depo_id, mid, None)
            tmpl[mid] = t
            return t

        linked = repointed = 0
        dups = st["dups"]
        for p in st["orphans"]:
            mid = dups.get(p["mid"], p["mid"])
            t = template(mid)
            db.cache_put(p["vid"], t["file_id"], t["unique_id"], t["title"], t["performer"], t["duration"],
                         t["size"], depo_id, mid)
            linked += 1
        for dmid, cmid in dups.items():
            p = posts.get(dmid)
            t = template(cmid)
            repointed += db.cache_repoint(depo_id, dmid, t)
            if p and p["vid"] and not db.cache_get(p["vid"]):
                db.cache_put(p["vid"], t["file_id"], t["unique_id"], t["title"], t["performer"], t["duration"],
                             t["size"], depo_id, cmid)
                linked += 1
        audio_cache.mark_dirty()
        self._set("last_dedupe", json.dumps({"ts": time.time(), "linked": linked, "repointed": repointed,
                                             "dups": len(dups)}))
        logger.info(f"🧬 Depo indeksi: {linked} video bazaya bağlandı, {repointed} sətir kanonik posta yönəldi")
        return {"linked": linked, "repointed": repointed}

    async def match_novid(self, posts: list, progress=None, should_stop=lambda: False,
                          concurrency: int = 3) -> dict:
        """ID-siz depo postlarını YouTube-da tapır: ad + artist uyğunluğu və müddət (±10 san.).
        Yalnız əmin olduqda bağlanır; şübhəli olanlar toxunulmaz qalır (səhv mahnı bağlanmasın)."""
        ym = getattr(self.ctx, "youtube_manager", None)
        if ym is None:
            raise RuntimeError("YouTube servisi yoxdur")
        db = get_db()
        st = {"total": len(posts), "done": 0, "matched": [], "unmatched": [], "already": 0,
              "stopped": False, "started": time.time()}
        sem = asyncio.Semaphore(concurrency)

        async def one(p):
            async with sem:
                if should_stop():
                    return
                q = f"{p['performer']} {p['title']}".strip()
                try:
                    res = await ym.youtube_search(q, limit=5, source="yt", fast=True)
                except TypeError:
                    res = await ym.youtube_search(q, limit=5)
                except Exception as e:
                    logger.debug(f"🔎 ID-siz axtarış alınmadı ({q}): {e}")
                    res = []
                best = None
                for r in res or []:
                    mm = _VID_RE.search(r.get("url") or "")
                    vid = mm.group(1) if mm else None
                    if not vid:
                        continue
                    if not names.meta_matches(p["performer"], p["title"], r.get("title") or ""):
                        continue
                    d = int(r.get("raw_duration") or 0)
                    if p["duration"] and d and abs(d - p["duration"]) > 10:
                        continue
                    best = vid
                    break
                if best and db.cache_get(best):
                    st["already"] += 1           # bu video artıq başqa posta bağlıdır — bu post dublikatdır
                elif best:
                    st["matched"].append({**p, "vid": best})
                else:
                    st["unmatched"].append(p)
                st["done"] += 1
                if progress and st["done"] % 10 == 0:
                    await progress(st)
                await asyncio.sleep(0.3)         # YouTube-a yumşaq

        await asyncio.gather(*(one(p) for p in posts))
        st["stopped"] = should_stop()
        return st

    def novid_link(self, depo_id: int, matched: list) -> int:
        """match_novid-in tapdıqlarını bazaya yazır (dedupe_link ilə eyni mexanizm, heç nə silinmir)."""
        if not matched:
            return 0
        r = self.dedupe_link({"depo_id": depo_id, "posts": matched, "orphans": matched, "dups": {}})
        return r["linked"]

    async def dedupe_delete(self, st: dict, progress=None) -> int:
        """Dublikat postları depodan silir (əvvəl dedupe_link çağırılmalıdır — baza artıq kanoniklərə baxır)."""
        mids = sorted(st["dups"])
        if not mids:
            return 0
        depo_id, deleted = st["depo_id"], 0
        ent = await self._depo_entity(depo_id) if self.tg and await self.tg.ok() else None
        for i in range(0, len(mids), 100):
            chunk = mids[i:i + 100]
            try:
                if ent is None:
                    raise RuntimeError("userbot yoxdur")
                await self.tg.client.delete_messages(ent, chunk)
                deleted += len(chunk)
            except Exception:
                for mid in chunk:                       # ehtiyat: bot özü (kanalda "Delete messages" icazəsi)
                    try:
                        await self.ctx.bot.delete_message(depo_id, mid)
                        deleted += 1
                    except Exception as e:
                        wait = getattr(e, "retry_after", None)
                        if wait:
                            await asyncio.sleep(wait + 1)
                    await asyncio.sleep(0.05)
            if progress:
                await progress(deleted, len(mids))
        gone = set(mids)
        pool = getattr(self.ctx, "depo_helpers", None)
        if pool is not None:
            for fid, (cid, mid, _) in list(pool.foreign.items()):
                if cid == depo_id and mid in gone:
                    pool.foreign.pop(fid, None)
        def drop():
            db = get_db()
            with db.sql() as cur:
                cur.execute("DELETE FROM depo_foreign WHERE chat_id = %s AND message_id = ANY(%s)",
                            (depo_id, list(mids)))
        await asyncio.to_thread(drop)
        audio_cache.mark_dirty()
        logger.info(f"🧬 Depodan {deleted} dublikat post silindi")
        return deleted

    def sync_apply(self, depo_id, missing, block: bool = True) -> dict:
        """Kanalda olmayan postların bütün sətirlərini bazadan silir (sinxron — to_thread ilə çağır)."""
        missing = list(missing)
        db = get_db()
        removed, rows_n = [], 0
        with db.sql() as cur:
            for i in range(0, len(missing), 500):
                chunk = missing[i:i + 500]
                rows = cur.execute("""SELECT video_id, title, performer FROM audio_cache
                                      WHERE chat_id = %s AND message_id = ANY(%s)""",
                                   (depo_id, chunk)).fetchall()
                removed += [(r[0], f"{r[2]} - {r[1]}" if r[2] else (r[1] or r[0])) for r in rows]
                rows_n += cur.execute("DELETE FROM audio_cache WHERE chat_id = %s AND message_id = ANY(%s)",
                                      (depo_id, chunk)).rowcount
                cur.execute("DELETE FROM depo_foreign WHERE chat_id = %s AND message_id = ANY(%s)",
                            (depo_id, chunk))
            if block and removed:
                now = int(time.time())
                cur.executemany("""INSERT INTO depo_removed (video_id, title, ts) VALUES (%s,%s,%s)
                                   ON CONFLICT (video_id) DO UPDATE SET title=EXCLUDED.title, ts=EXCLUDED.ts""",
                                [(v, t, now) for v, t in removed])
        audio_cache.mark_dirty()
        if block:
            self.removed.update(v for v, _ in removed)
        pool = getattr(self.ctx, "depo_helpers", None)
        if pool is not None:
            gone = set(missing)
            for fid, (cid, mid, _) in list(pool.foreign.items()):
                if cid == depo_id and mid in gone:
                    pool.foreign.pop(fid, None)
        self._set("last_removed", json.dumps(removed[:5000]))
        return {"posts": len(missing), "rows": rows_n, "songs": len(removed), "blocked": block}

    def unblock_last(self) -> int:
        """Son sinxronizasiyada silinənləri blokdan çıxarıb növbənin önünə qoyur."""
        try:
            removed = json.loads(self._get("last_removed") or "[]")
        except ValueError:
            removed = []
        if not removed:
            return 0
        vids = [v for v, _ in removed]
        db = get_db()
        with db.sql() as cur:
            cur.execute("DELETE FROM depo_removed WHERE video_id = ANY(%s)", (vids,))
        self.removed.difference_update(vids)
        self._set("last_removed", "[]")
        items = [{"title": t, "url": f"https://www.youtube.com/watch?v={v}", "raw_duration": 0, "query": t,
                  "meta": None, "thumb": None, "sp_id": None} for v, t in removed]
        self.seen.difference_update(it["url"] for it in items)
        return self.add_front(items)

    def clear_removed(self) -> int:
        n = len(self.removed)
        db = get_db()
        with db.sql() as cur:
            cur.execute("DELETE FROM depo_removed")
        self.removed.clear()
        return n
