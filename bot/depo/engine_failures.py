"""
🔁 bot/depo/engine_failures.py - Filler: təkrar növbəsi və alınmayan mahnılar (engine.py-dən ayrılıb).
"""
from bot.depo.engine_common import *  # noqa: F401,F403


class FailuresMixin:
    # ── axtarış nəticələrinin keşi (bazada): eyni ad üçün API-lər təkrar yorulmasın ──
    # ── 🔁 uğursuzlar və 🧹 silinmişlər ──
    def _init_fail_tables(self):
        try:
            rows = get_db().sql_all("SELECT video_id FROM depo_removed")   # cədvəllər database.py sxemində
            self.removed = {r[0] for r in rows}
        except Exception as e:
            logger.warning(f"depo_failed / depo_removed cədvəlləri hazırlanmadı: {e}")

    @staticmethod
    def classify(err: str) -> str:
        """'perm' — təkrar mənasızdır (video silinib, özəl, region...), 'temp' — sonra yenidən sına."""
        return "perm" if _PERM_ERR.search(err or "") else "temp"

    def schedule_retry(self, item: dict, err: str, kind: str = "temp"):
        tries = int(item.get("_tries") or 0) + 1
        steps = NOTFOUND_BACKOFF if kind == "notfound" else RETRY_BACKOFF
        if kind == "perm" or tries > len(steps):
            self.mark_failed(item, err, tries)
            return False
        item["_tries"] = tries
        self.retry.append({"item": item, "tries": tries, "next": time.time() + steps[tries - 1],
                           "err": (err or "")[:200], "kind": kind})
        return True

    def mark_failed(self, item: dict, err: str, tries: int):
        try:
            db = get_db()
            with db.sql() as cur:
                cur.execute("""INSERT INTO depo_failed (key, title, item, err, tries, ts) VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (key) DO UPDATE SET title=EXCLUDED.title, item=EXCLUDED.item,
                                   err=EXCLUDED.err, tries=EXCLUDED.tries, ts=EXCLUDED.ts""",
                            (self._key(item) or item.get("title"), item.get("title"),
                             json.dumps(item, default=str), (err or "")[:300], tries, int(time.time())))
        except Exception as e:
            logger.debug(f"depo_failed yazılmadı: {e}")

    def failed_list(self, limit: int = 10) -> tuple:
        try:
            db = get_db()
            with db.sql() as cur:
                n = cur.execute("SELECT COUNT(*) FROM depo_failed").fetchone()[0]
                rows = cur.execute("SELECT title, err, tries FROM depo_failed ORDER BY ts DESC LIMIT %s",
                                   (limit,)).fetchall()
            return n, [tuple(r) for r in rows]
        except Exception:
            return 0, []

    def requeue_failed(self) -> int:
        """❌ siyahısındakıları növbənin önünə qaytarır (cəhd sayı sıfırlanır)."""
        db = get_db()
        with db.sql() as cur:
            rows = cur.execute("SELECT item FROM depo_failed").fetchall()
            cur.execute("DELETE FROM depo_failed")
        items = []
        for r in rows:
            try:
                it = json.loads(r[0])
            except ValueError:
                continue
            if isinstance(it, dict):
                it.pop("_tries", None)
                items.append(it)
        self.seen.difference_update(self._key(it) for it in items)
        return self.add_front(items)

    def clear_failed(self):
        db = get_db()
        with db.sql() as cur:
            cur.execute("DELETE FROM depo_failed")

    def retry_due(self):
        now = time.time()
        best = None
        for i, r in enumerate(self.retry):
            if r["next"] <= now and (best is None or r["next"] < self.retry[best]["next"]):
                best = i
        return self.retry.pop(best)["item"] if best is not None else None

    def next_retry_in(self):
        return min((r["next"] for r in self.retry), default=None)

    # ── 📋 Gözləyənlər: tək-tək idarə ──
    def retry_page(self, page: int, per: int = Q_PAGE) -> tuple:
        rows = sorted(self.retry, key=lambda r: r["next"])
        return len(rows), rows[page * per:(page + 1) * per]

    def _retry_find(self, h: str):
        return next((r for r in self.retry if hkey(self._key(r["item"]) or r["item"].get("title")) == h), None)

    def retry_now(self, h: str) -> str:
        r = self._retry_find(h)
        if not r:
            return ""
        r["next"] = 0
        return r["item"].get("title") or ""

    def retry_drop(self, h: str) -> str:
        """Təkrar növbəsindən birdəfəlik sil (❌ siyahısına da düşmür)."""
        r = self._retry_find(h)
        if not r:
            return ""
        self.retry.remove(r)
        return r["item"].get("title") or ""

    def retry_drop_all(self) -> int:
        n = len(self.retry)
        self.retry.clear()
        return n

    def retry_to_failed(self) -> int:
        """Təkrar növbəsindəkiləri ❌ siyahısına köçürür (gözləmədən)."""
        n = 0
        for r in list(self.retry):
            self.mark_failed(r["item"], r.get("err") or "", r.get("tries") or 0)
            n += 1
        self.retry.clear()
        return n

    def failed_page(self, page: int, per: int = Q_PAGE) -> tuple:
        try:
            db = get_db()
            with db.sql() as cur:
                n = cur.execute("SELECT COUNT(*) FROM depo_failed").fetchone()[0]
                rows = cur.execute("SELECT key, title, err, tries, ts FROM depo_failed ORDER BY ts DESC "
                                   "LIMIT %s OFFSET %s", (per, page * per)).fetchall()
            return n, [tuple(r) for r in rows]
        except Exception:
            return 0, []

    def _failed_take(self, h: str, delete: bool = True):
        db = get_db()
        with db.sql() as cur:
            rows = cur.execute("SELECT key, item, title FROM depo_failed").fetchall()
            hit = next((r for r in rows if hkey(r[0]) == h), None)
            if hit and delete:
                cur.execute("DELETE FROM depo_failed WHERE key = %s", (hit[0],))
        return hit

    def failed_requeue_one(self, h: str) -> str:
        hit = self._failed_take(h)
        if not hit:
            return ""
        try:
            it = json.loads(hit[1])
        except ValueError:
            return ""
        if not isinstance(it, dict):
            return ""
        it.pop("_tries", None)
        self.seen.discard(self._key(it))
        self.add_front([it])
        return it.get("title") or hit[2] or ""

    def failed_delete_one(self, h: str) -> str:
        hit = self._failed_take(h)
        return (hit[2] or hit[0]) if hit else ""
