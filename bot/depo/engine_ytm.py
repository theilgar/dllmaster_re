"""
🔎 YT Music skaneri — PARALEL ifaçı oxuyucuları (Filler mixin-i).

Əvvəl skaner ifaçıları tək-tək açırdı (hər biri ~50–90 san.): növbəyə düşən mahnı sürəti (~105–135/dəq.) 128 işçinin
yükləmə tavanına çatmırdı — növbə həmişə doldurma həddinin (işçi × 4) altında qalırdı, işçilər mahnı gözləyirdi.
İndi eyni anda N ifaçı oxunur (standart 3). Hər "yuva" öz dövrəsində işləyir: növbə doldurma həddindən azdırsa növbəti
ifaçını açır. Yuvalar eyni ifaçını götürmür (crawler `_inflight`).

  🤖 Avto   — 3-dən başlayır; YT Music tərəfi bağlantı / limit xətası verəndə 1 azalır (1 dəq.-də bir dəfədən çox yox),
              10 dəq. sakit keçəndə yenə 1 artır (başlanğıca qədər). Bu səbəbdən YT Music-in IP limiti riski azdır.
  ✋ Manual — yazdığın say (1–8) olduğu kimi.
Menyu: /depo → 🔎 Skaner (bot/depo/ytm_menu.py).
"""
import asyncio
import logging
import time

from bot.depo.crawler import YTM_LOW_WATER

logger = logging.getLogger(__name__)

YTM_PAR_DEFAULT = 3
YTM_PAR_MAX = 8
YTM_PAR_RECOVER = 600.0        # san. — avto sakit dövrdə bu fasilədə bir yuva geri qaytarır
YTM_ERR_DEBOUNCE = 60.0        # san. — 3 yuva eyni anda xəta alsa da avto yalnız BİR addım azaldır


class YtmFeederMixin:
    def _ytm_par_init(self):
        self._ytm_auto = YTM_PAR_DEFAULT      # avtonun cari yuva sayı
        self._ytm_err_at = 0.0                # son xəta (monotonic)
        self._ytm_recover_at = 0.0
        self._ytm_err_seen = 0.0              # crawler.stats["err_at"]-in gördüyümüz son qiyməti

    @property
    def ytm_par_mode(self) -> str:
        return self._mode("ytm_par_mode", "ytm_par", YTM_PAR_DEFAULT)

    @property
    def ytm_parallel(self) -> int:
        """Eyni anda neçə ifaçı oxunsun."""
        if self.ytm_par_mode == "auto":
            return max(1, min(YTM_PAR_MAX, self._ytm_auto))
        try:
            return max(1, min(YTM_PAR_MAX, int(self._get("ytm_par") or YTM_PAR_DEFAULT)))
        except (TypeError, ValueError):
            return YTM_PAR_DEFAULT

    def set_ytm_par(self, value: int) -> int:
        value = max(1, min(YTM_PAR_MAX, int(value)))
        self._set("ytm_par", str(value))
        self._set("ytm_par_mode", "manual")
        return value

    def note_ytm_error(self):
        """YT Music tərəfi bağlantı / limit xətası verdi — avto yuva sayını azaldır (manual dəyişmir)."""
        now = time.monotonic()
        if now - self._ytm_err_at < YTM_ERR_DEBOUNCE:
            return
        self._ytm_err_at = now
        if self.ytm_par_mode == "auto" and self._ytm_auto > 1:
            self._ytm_auto -= 1
            logger.info(f"🔎 YT Music xətası — avto paralel {self._ytm_auto + 1} → {self._ytm_auto}")

    def _ytm_recover_tick(self):
        now = time.monotonic()
        if (self.ytm_par_mode == "auto" and self._ytm_auto < YTM_PAR_DEFAULT
                and now - self._ytm_err_at > YTM_PAR_RECOVER and now - self._ytm_recover_at > YTM_PAR_RECOVER):
            self._ytm_recover_at = now
            self._ytm_auto += 1
            logger.info(f"🔎 YT Music sakitdir — avto paralel {self._ytm_auto - 1} → {self._ytm_auto}")

    async def ytm_feeder(self):
        """Yuvaları idarə edir: ytm_parallel qədər _ytm_slot işləyir (➕/➖ dərhal tətbiq olunur)."""
        slots = {}
        try:
            while not self.stop_event.is_set():
                self._ytm_recover_tick()
                for i in range(self.ytm_parallel):
                    t = slots.get(i)
                    if t is None or t.done():
                        slots[i] = asyncio.create_task(self._ytm_slot(i))
                for i in [k for k, t in slots.items() if t.done() and k >= self.ytm_parallel]:
                    slots.pop(i)
                await asyncio.sleep(3)
        finally:
            for t in slots.values():
                t.cancel()
            await asyncio.gather(*slots.values(), return_exceptions=True)

    async def _ytm_slot(self, i: int):
        """Bir yuva: növbə az olanda növbəti ifaçını açır. Yuva sayı azalanda (i ≥ ytm_parallel) özü bitir."""
        while not self.stop_event.is_set():
            if i >= self.ytm_parallel:
                return
            if not self.ytm_on or not self.ytm:
                await asyncio.sleep(10)
                continue
            low = max(YTM_LOW_WATER, self.workers * 4)
            country_need = (bool(self.countries()) and len(self.country_queue) < low
                            and self.ytm.has_prio_pending())
            if len(self.ytm_queue) >= low and not country_need:   # çox işçi növbəni tez boşaldır
                await asyncio.sleep(5)
                continue
            try:
                ok = await self.ytm.step(self.ytm_add_items, self._user_artists, self.stop_event)
                err_at = self.ytm.stats.get("err_at", 0.0)
                if err_at != self._ytm_err_seen:
                    self._ytm_err_seen = err_at
                    self.note_ytm_error()
                if not ok:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"🎵 YT Music skaneri: {e}")
                self.ytm.stats["note"] = f"xəta: {str(e)[:60]} — 1 dəq. sonra"
                self.ytm.yt = None                     # sessiyanı yenilə
                self.note_ytm_error()
                await asyncio.sleep(60)
