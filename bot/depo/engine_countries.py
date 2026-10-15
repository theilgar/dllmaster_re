"""
🌍 bot/depo/engine_countries.py - Filler: ölkə chartları və ölkə axtarış sorğuları (engine.py-dən ayrılıb).
"""
from bot.depo.engine_common import *  # noqa: F401,F403


class CountriesMixin:
    def country_strict(self) -> bool:
        v = self._get("country_strict")
        if v is None:
            return bool(self.countries())
        return v == "1" and bool(self.countries())

    # ── 🌍 ölkələr ──
    def countries(self) -> list:
        try:
            v = json.loads(self._get("countries") or "[]")
            return [c for c in v if c in COUNTRIES]
        except ValueError:
            return []

    def save_countries(self, lst: list):
        self._set("countries", json.dumps([c for c in lst if c in COUNTRIES]))
        if self.ytm:
            self.ytm.countries = self.countries()

    # ── 🔍 Ölkə axtarış sorğuları ──
    def get_country_queries(self, cc: str) -> list:
        raw = self._get(f"cq:{cc}")
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
            except Exception as _e:
                logger.debug("udulan xəta [bot/plugins/depo_filler_plugin.py:2434]: %r", _e)
        defs = COUNTRY_SEARCH_QUERIES.get(cc)
        if not defs:
            if cc not in YTM_OFFICIAL_CHART_COUNTRIES:
                cname = COUNTRIES.get(cc, cc)
                clean_name = re.sub(r"[^\w\s]", "", cname).strip()
                defs = [f"Trend {clean_name}", f"Top {clean_name}", f"{clean_name} music"]
            else:
                defs = []
        res = []
        for q in defs:
            is_bass = "bass" in q.lower()
            res.append({"query": q, "enabled": not is_bass})
        return res

    def save_country_queries(self, cc: str, queries: list):
        self._set(f"cq:{cc}", json.dumps(queries))

    def get_active_country_queries(self, cc: str) -> list:
        return [q["query"] for q in self.get_country_queries(cc) if q.get("enabled", True) and q.get("query")]

    def toggle_country_query(self, cc: str, index: int) -> bool:
        queries = self.get_country_queries(cc)
        if 0 <= index < len(queries):
            cur = queries[index].get("enabled", True)
            queries[index]["enabled"] = not cur
            self.save_country_queries(cc, queries)
            return queries[index]["enabled"]
        return False

    def add_country_query(self, cc: str, query: str):
        query = query.strip()
        if not query:
            return
        queries = self.get_country_queries(cc)
        for q in queries:
            if q.get("query", "").lower() == query.lower():
                q["enabled"] = True
                self.save_country_queries(cc, queries)
                return
        queries.append({"query": query, "enabled": True})
        self.save_country_queries(cc, queries)

    def reset_country_queries(self, cc: str):
        self._set(f"cq:{cc}", None)

    def _country_charts_sync(self, countries: list) -> tuple:
        """→ (mahnılar, [(ifaçı_id, ad, prio)]) — ytmusicapi rəsmi chartları və axtarış playlistləri."""
        if not HAS_YTM:
            return [], []
        yt = self.ytm.client() if self.ytm else YTMusic()
        items, artists, vids = [], [], set()
        for i, cc in enumerate(countries):
            if SHUTDOWN.is_set() or not getattr(self, "running", True) or (getattr(self, "stop_event", None) and self.stop_event.is_set()):
                return items, artists
            prio = max(1, COUNTRY_PRIO_TOP - i)
            n0 = len(items)

            # 1) Rəsmi YTM chart (yalnız həqiqi ölkə chartı olduqda — Global fallback-lər rədd edilir)
            if cc in YTM_OFFICIAL_CHART_COUNTRIES:
                try:
                    charts = yt.get_charts(country=cc) or {}
                    if _is_valid_chart_for(charts, cc):
                        for t in _chart_tracks(yt, charts):
                            it = ytm_item(t)
                            if it and it["vid"] not in vids:
                                vids.add(it["vid"])
                                it["country"] = cc
                                items.append(it)
                        ch_artists = _chart_artists(charts)
                        artists += [(aid, name, prio) for aid, name in ch_artists]
                        logger.info(f"🌍 {cc} rəsmi chart: {len(items) - n0} mahnı, {len(ch_artists)} ifaçı")
                    else:
                        logger.info(f"🌍 {cc} rəsmi chart Global qaytardığı üçün rədd edildi")
                except Exception as e:
                    logger.info(f"🌍 {cc} rəsmi chart xətası: {e}")

            # 2) Ölkə axtarış playlistləri (Azərbaycan və rəsmi chartı olmayan/az olan ölkələr üçün təmiz yerli musiqi)
            queries = self.get_active_country_queries(cc)

            if queries:
                n_pl_tracks, n_pl_artists = 0, 0
                for q in queries:
                    if SHUTDOWN.is_set() or not getattr(self, "running", True) or (getattr(self, "stop_event", None) and self.stop_event.is_set()):
                        return items, artists
                    try:
                        pls = yt.search(q, filter="playlists", limit=3) or []
                    except Exception as e:
                        logger.debug(f"🌍 {cc} playlist axtarışı xətası ({q}): {e}")
                        continue
                    for pl in pls[:2]:
                        pid = pl.get("browseId") or pl.get("playlistId")
                        if not pid:
                            continue
                        if pid.startswith("VL"):
                            pid = pid[2:]
                        try:
                            pldata = yt.get_playlist(pid, limit=80) or {}
                            for t in pldata.get("tracks", []):
                                vid = t.get("videoId")
                                if not vid or vid in vids:
                                    continue
                                it = ytm_item(t)
                                if it:
                                    vids.add(vid)
                                    it["country"] = cc
                                    items.append(it)
                                    n_pl_tracks += 1
                                for a in t.get("artists", []):
                                    aid = a.get("id")
                                    aname = a.get("name")
                                    if aid and aname:
                                        artists.append((aid, aname, prio))
                                        n_pl_artists += 1
                        except Exception as ex:
                            logger.debug(f"🌍 {cc} playlist {pid} oxunmadı: {ex}")
                        if SHUTDOWN.is_set() or (getattr(self, "stop_event", None) and self.stop_event.wait(YTM_PAUSE)):
                            return items, artists
                logger.info(f"🌍 {cc} axtarış playlistləri: +{n_pl_tracks} mahnı, +{n_pl_artists} ifaçı")
            if SHUTDOWN.is_set() or (getattr(self, "stop_event", None) and self.stop_event.wait(YTM_PAUSE)):
                return items, artists
        return items, artists

    async def apply_countries(self) -> dict:
        """Seçilmiş ölkələrin chart mahnılarını növbənin ƏVVƏLİNƏ, chart ifaçılarını skanerə prioritetlə."""
        cs = self.countries()
        if not cs:
            return {"songs": 0, "in_depo": 0, "artists": 0}
        if self.ytm:
            self.ytm.countries = cs
        items, artists = await asyncio.to_thread(self._country_charts_sync, cs)

        def check():
            return [bool(audio_cache.get_cached(it["vid"])) for it in items]

        flags = await asyncio.to_thread(check) if items else []
        fresh = [it for it, c in zip(items, flags) if not c]
        uniq, keys = [], {self._key(x) for x in self.country_queue}
        for it in fresh:                              # 🌍 ölkə növbəsinin ƏVVƏLİNƏ (təkrarsız)
            k = self._key(it)
            if k and k not in keys and (it.get("raw_duration") or 0) <= MAX_DURATION:
                keys.add(k)
                uniq.append(it)
        self.country_queue.extendleft(reversed(uniq))
        added = len(uniq)
        n_art = 0
        if self.ytm:
            prio_map = {}
            for aid, name, prio in artists:
                prio_map.setdefault(prio, []).append((aid, name))
            for prio, pairs in prio_map.items():
                n_art += await asyncio.to_thread(self.ytm.add_artists, pairs, prio)
        self._set("countries_applied", str(int(time.time())))
        logger.info(f"🌍 Ölkələr {','.join(cs)}: +{added} mahnı növbənin əvvəlinə "
                    f"({len(items) - len(fresh)} artıq depoda), {len(artists)} chart ifaçısı prioritetlə")
        return {"songs": added, "in_depo": len(items) - len(fresh), "artists": len(artists)}
