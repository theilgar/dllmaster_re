"""
📋 bot/depo/engine_queue.py - Filler: növbə qurulması, axtarış / resolve və keş (engine.py-dən ayrılıb).
"""
from bot.depo.engine_common import *  # noqa: F401,F403


class QueueMixin:
    def _init_resolve_table(self):
        pass                                         # cədvəl common/database.py sxemində yaradılır

    @staticmethod
    def _qkey(item) -> str:
        if item.get("sp_id"):
            return f"sp:{item['sp_id']}"
        return "q:" + " ".join(sorted(_toks(item.get("query") or item.get("title") or "")))

    def _rc_get(self, key):
        try:
            row = get_db().sql_one("SELECT vid, meta, ts FROM depo_resolve WHERE qkey = %s", (key,))
        except Exception:
            return None
        if not row:
            return None
        vid, meta, ts = row[0], row[1], row[2] or 0
        if not vid and time.time() - ts > NOTFOUND_RETRY:
            return None                              # köhnə "tapılmadı" — yenidən sına
        try:
            meta = json.loads(meta) if meta else None
        except ValueError:
            meta = None
        return {"vid": vid or None, "meta": meta}

    def _rc_put(self, key, vid, meta=None):
        try:
            with get_db().sql() as cur:
                cur.execute("""INSERT INTO depo_resolve (qkey, vid, meta, ts) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (qkey) DO UPDATE SET vid=EXCLUDED.vid, meta=EXCLUDED.meta, ts=EXCLUDED.ts""",
                            (key, vid or "", json.dumps(meta) if meta else None, int(time.time())))
        except Exception as e:
            logger.debug(f"depo_resolve yazılmadı: {e}")

    # ── növbə ──
    def _key(self, item):
        return item.get("url") or (item.get("query") or "").lower()

    def _ok(self, item) -> bool:
        key = self._key(item)
        return bool(key) and key not in self.seen and (item.get("raw_duration") or 0) <= MAX_DURATION

    def _add(self, item):
        if not self._ok(item):
            return False
        self.seen.add(self._key(item))
        self.queue.append(item)
        return True

    def add_front(self, items) -> int:
        """Siyahını növbənin əvvəlinə (sırası qorunur) — yeni əlavə olunan mənbə gözləməsin."""
        fresh = [it for it in items if self._ok(it)]
        uniq, keys = [], set()
        for it in fresh:
            k = self._key(it)
            if k not in keys:
                keys.add(k)
                uniq.append(it)
        for it in reversed(uniq):
            self.seen.add(self._key(it))
            self.queue.appendleft(it)
        return len(uniq)

    async def build_queue(self):
        ctx = self.ctx
        ym = ctx.youtube_manager
        spotify_data = getattr(ctx, "links_spotify_data", None)
        spotify = getattr(ctx, "spotify", None)
        now = int(time.time())
        db = get_db()

        # 0) 🌍 seçilmiş ölkələrin chartları — hər şeydən əvvəl
        if self.countries():
            try:
                r = await self.apply_countries()
                self.stats["note"] = f"🌍 ölkə chartları: +{r['songs']} mahnı"
            except Exception as e:
                logger.info(f"🌍 Ölkə chartları alınmadı: {e}")

        # 1) istifadəçilərin sevdiyi ifaçılar
        try:
            def _top_downloads():
                return db.sql_all(
                    """SELECT MAX(title) AS title, MAX(url) AS url, COUNT(*) AS n FROM downloads
                       WHERE ts >= %s AND title IS NOT NULL GROUP BY COALESCE(url, title) ORDER BY n DESC LIMIT 200""",
                    (now - 7 * 86400,))
            rows = await asyncio.to_thread(_top_downloads)
        except Exception:
            rows = []
        artists = {}
        for r in rows:
            artist = (r["title"] or "").split(" - ", 1)[0].split(",")[0].strip()
            if artist and artist != "YouTube":
                artists[artist] = artists.get(artist, 0) + r["n"]
        top_artists = sorted(artists, key=artists.get, reverse=True)[:12]
        if spotify and spotify_data:
            for name in top_artists:
                if self.stop_event.is_set():
                    return
                try:
                    res = await asyncio.to_thread(spotify.sp.search, q=f"artist:{name}", type="artist", limit=1)
                    found = (res.get("artists") or {}).get("items") or []
                    if not found:
                        continue
                    data = await spotify_data("artist", found[0]["id"])
                    for it in (data.get("top") or data.get("items") or [])[:10]:
                        self._add(it)
                except Exception as e:
                    logger.debug(f"Doldurucu: ifaçı {name}: {e}")

        # 2) ən çox yüklənən mahnıların Mix-i
        for r in rows[:8]:
            if self.stop_event.is_set():
                return
            m = re.search(r"v=([\w-]{11})", r["url"] or "")
            if not m:
                continue
            try:
                for it in await ym.youtube_mix(m.group(1), limit=15):
                    self._add(it)
            except Exception as e:
                logger.debug(f"Doldurucu: mix {m.group(1)}: {e}")

        # 3) mənbələr
        for src in self.sources():
            if self.stop_event.is_set():
                return
            try:
                if src["kind"].startswith("sp_") and spotify_data:
                    data = await spotify_data(src["kind"][3:], src["id"])
                    for it in data.get("items") or []:
                        self._add(it)
                elif src["kind"] == "yt_list":
                    data = await ym.playlist_entries(yt_list_url(src["id"]), limit=200)
                    for it in data["entries"]:
                        self._add(it)
                elif src["kind"] == "tg_channel":
                    if self.tg and await self.tg.ok():
                        ent = await self.tg.entity({"type": "user", "name": src["id"]})
                        await self.tg_scan_source(ent, None, self._add, self.stop_event.is_set)
                    else:
                        data = await tg_channel_items(src["id"])
                        for it in data["items"]:
                            self._add(it)
                elif src["kind"] == "tg_chat":
                    if not (self.tg and await self.tg.ok()):
                        logger.info(f"Doldurucu: {src.get('label')}: userbot aktiv deyil — ötürüldü")
                        continue
                    ent = await self.tg.entity_for_source(src)
                    await self.tg_scan_source(ent, src.get("limit"), self._add, self.stop_event.is_set)
            except Exception as e:
                logger.warning(f"Doldurucu: mənbə {src.get('label') or src['id']}: {e}")

        self.stats["last_build"] = time.time()
        self.stats["queue_built"] = len(self.queue)
        logger.info(f"📦 Doldurucu növbəsi: {len(self.queue)} mahnı")

    async def lookup(self, item):
        """Telegram adı → Spotify / iTunes-da rəsmi mahnı. Tapılsa item-i yeniləyir, platforma linkini qaytarır."""
        c = item.get("clean") or {}
        artist, track = c.get("artist", ""), c.get("track") or item.get("title", "")
        dur = int(item.get("raw_duration") or 0)
        found = None
        try:
            found = await spotify_lookup(getattr(self.ctx, "spotify", None), artist, track, dur)
        except Exception as e:
            logger.debug(f"Spotify axtarışı ({artist} - {track}): {e}")
        if not found:
            try:
                found = await itunes_lookup(artist, track, dur)
            except Exception as e:
                logger.debug(f"iTunes axtarışı ({artist} - {track}): {e}")
        if not found:
            return None
        item["meta"] = {"artist": found["artist"], "track": found["track"]}
        item["title"] = f"{found['artist']} - {found['track']}"
        item["query"] = f"{found['first']} {found['track']}".strip()
        item["thumb"] = item.get("thumb") or found["thumb"]
        if found["duration"]:
            item["raw_duration"] = found["duration"]
        if found["sp_id"]:
            item["sp_id"] = found["sp_id"]
        logger.info(f"🔎 {artist} - {track} → {item['title']} ({found['src']})")
        return found["link"]

    async def _songlink_youtube(self, link: str):
        songlink = getattr(self.ctx, "music_songlink", None)
        if not (link and songlink):
            return None
        try:
            sl = await songlink(link)
            links = (sl or {}).get("links") or {}
            url = links.get("youtube") or links.get("youtubeMusic")
            vid = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url or "")
            return f"https://www.youtube.com/watch?v={vid.group(1)}" if vid else None
        except Exception as e:
            logger.debug(f"song.link alınmadı: {e}")
            return None

    async def resolve(self, item):
        """(YouTube linki, keşdən?) — keş → [Telegram adı: Spotify/iTunes axtarışı] → song.link → YouTube axtarışı."""
        if item.get("url"):
            return item["url"], False
        key = self._qkey(item)
        hit = self._rc_get(key)
        if hit:
            if hit["meta"] and not item.get("meta"):
                item["meta"] = hit["meta"]
                item["title"] = f"{hit['meta']['artist']} - {hit['meta']['track']}"
            return (f"https://www.youtube.com/watch?v={hit['vid']}" if hit["vid"] else None), True

        link = None
        if item.get("sp_id"):
            link = f"https://open.spotify.com/track/{item['sp_id']}"
        elif item.get("lookup"):
            link = await self.lookup(item)
        url = await self._songlink_youtube(link)
        if not url:
            resolve = getattr(self.ctx, "music_resolve_youtube", None)
            url = await resolve(item.get("query") or item["title"], item.get("raw_duration") or 0) if resolve else None
        vid = re.search(r"v=([\w-]{11})", url or "")
        self._rc_put(key, vid.group(1) if vid else None, item.get("meta"))
        return url, False

    # ── 🎵 YouTube Music növbəsi ──
    async def ytm_add_items(self, items, front: bool = False) -> tuple:
        """(növbəyə əlavə, artıq depoda) — depoda olanlar növbəyə heç düşmür.
        front=True — 🌍 seçilmiş ölkənin ifaçısı: növbənin əvvəlinə."""
        if len(self.ytm_seen) > YTM_SEEN_CAP:
            self.ytm_seen.clear()
        fresh = []
        for it in items:                            # paralel skaner yuvaları eyni mahnını iki dəfə növbəyə qoymasın:
            if it["vid"] not in self.ytm_seen:      # "gördüm" işarəsi await-dən ƏVVƏL qoyulur
                self.ytm_seen.add(it["vid"])
                fresh.append(it)

        def check():
            res = []
            for it in fresh:
                vid = it.get("vid")
                if vid and audio_cache.get_cached(vid):
                    res.append(True)
                    continue
                meta = it.get("meta")
                if meta and meta.get("artist") and meta.get("track"):
                    same = audio_cache.find_same(meta["artist"], meta["track"], int(it.get("raw_duration") or 0))
                    if same and same.get("file_id"):
                        if vid:
                            audio_cache.alias(vid, same, it.get("title"))
                        res.append(True)
                        continue
                res.append(False)
            return res

        try:
            flags = await asyncio.to_thread(check) if fresh else []
        except BaseException:
            for it in fresh:                        # yoxlama alınmadı — mahnılar itməsin, növbəti dəfə yenidən baxılsın
                self.ytm_seen.discard(it["vid"])
            raise
        added = in_depo = 0
        new = []
        for it, cached in zip(fresh, flags):
            self.ytm_seen.add(it["vid"])
            if cached:
                in_depo += 1
            else:
                new.append(it)
                added += 1
        if front:                                   # 🌍 seçilmiş ölkənin ifaçısı → ölkə növbəsi
            self.country_queue.extend(new)
        else:
            self.ytm_queue.extend(new)
        return added, in_depo
