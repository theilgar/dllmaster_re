"""
🎛 Köməkçi botların tənzimlənə bilən ayarları — hər biri 🤖 Avto və ya ✋ Manual.

🤖 Avto iki cür işləyir:
  • uyğunlaşan (safe ≠ 0) — flood alanda təhlükəsiz tərəfə sürüşür (sürət azalır, aralıq artır), sakit dövrdə
    addım-addım başlanğıc dəyərə qayıdır. Öyrənilən dəyər bazada saxlanır — restartdan sonra eyni flood təkrarlanmasın;
  • hesablanan (safe == 0) — başqa ayardan çıxarılır (məs. sink aralığı artanda gözləmə həddi də artır).
✋ Manual — istifadəçinin yazdığı dəyər olduğu kimi.

🚀 Köməkçi turbo (profil): 🤖 Avto-nun BAŞLANĞIC dəyərləri `turbo` sütununa qalxır (Telegram həddinə yaxın),
flood alanda avto yenə yavaşlayır. ✋ Manual ayarlara və config.env-ə toxunmur. Profil dəyişəndə öyrənilən dəyərlər sıfırlanır.

Rejim yazılmayıbsa: əl ilə fərqli dəyər saxlanıbsa — manual, yoxsa — avto (bot/depo/engine.py `_mode` ilə eyni qayda).
Menyu: /depo → 🤖 Köməkçi botlar → 🎛 Tənzimləmələr (bot/depo/panels.py).
"""
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

RECOVER_AFTER = 300.0     # sakit dövr (san.) — avto dəyər hər belə fasilədə bir addım başlanğıca qayıdır
FLOOD_DEBOUNCE = 30.0     # 24 köməkçi eyni anda flood alsa da avto yalnız BİR addım atır
S_PROFILE = "depo_helpers:profile"
PROFILES = ("balanced", "turbo")


@dataclass(frozen=True)
class Tunable:
    key: str
    icon: str
    label: str                    # picker başlığı
    short: str                    # hub düyməsi
    unit: str
    default: float                # standart = avtonun başlanğıc dəyəri
    lo: float                     # manual hədlər
    hi: float
    steps: tuple                  # manual düymə addımları
    digits: int = 0
    turbo: Optional[float] = None  # 🚀 tam performansda avtonun başlanğıc dəyəri (None — dəyişmir)
    safe: int = 0                 # -1 flood-da azalır (sürət) · +1 artır (aralıq) · 0 hesablanır
    backoff: float = 1.0          # flood-da vurma əmsalı
    recover: float = 0.0          # sakit dövrdə hər addımda başlanğıca doğru (mütləq dəyər)
    limit: float = 0.0            # avtonun getdiyi ən son həd
    turbo_limit: Optional[float] = None   # 🚀 turbo-da bu həd (məs. sürət 12-dən aşağı düşməsin)
    vkey: str = ""                # manual dəyərin baza açarı (boşdursa depo_helpers:v:<key>)
    env: str = ""                 # config.env — avtonun başlanğıc dəyəri
    info: str = ""
    tip: str = ""                 # nə vaxt artırıb-azaltmaq (bələdçi + picker)
    auto_info: str = ""
    derive: Optional[Callable] = None    # hesablanan: (pool, base) → dəyər

    def clip(self, v) -> float:
        v = min(self.hi, max(self.lo, float(v)))
        return self.rnd(v)

    def rnd(self, v) -> float:
        return int(round(v)) if self.digits == 0 else round(float(v), self.digits)

    def fmt(self, v) -> str:
        return f"{v:g} {self.unit}"


TUNABLES = {t.key: t for t in (
    Tunable("rate", "⏱", "Sürət limiti", "Sürət", "mahnı/dəq.", 18, 1, 60, (-5, -1, 1, 5),
            turbo=20, safe=-1, backoff=0.75, recover=1, limit=6, turbo_limit=12, vkey="depo_helpers:rate",
            info="Hər köməkçinin dəqiqədə depoya yükləyə biləcəyi mahnı sayı. Telegram həddi ≈ 20.",
            tip="Tez-tez flood görürsənsə 12–15-ə endir. Flood yoxdursa və yükləmə yavaşdırsa 20-yə qədər qaldır.",
            auto_info="Qısa flood alanda 25% azalır (ən azı 6, turbo-da 12), 5 dəq. sakit keçəndə +1 qalxır. "
                      "Uzun flood (saatlıq kvota) sürəti azaltmır."),
    Tunable("group_interval", "👥", "Qrup aralığı", "Qrup", "san.", 3.2, 1.0, 30.0, (-1, -0.2, 0.2, 1),
            digits=1, turbo=3.0, safe=1, backoff=1.3, recover=0.2, limit=8.0,
            info="Bir botun eyni qrupa göndərdiyi mahnılar arasında fasilə. 3.2 san. ≈ 18 mahnı/dəq.",
            tip="Qrupda mahnı göndərərkən flood olursa 4–5 san.-yə qaldır. Azaltsan mahnılar qrupa daha tez çatır.",
            auto_info="Qrupda flood alanda 30% artır (ən çoxu 8 san.), 5 dəq. sakit keçəndə 0.2 azalır."),
    Tunable("sink_interval", "🔁", "Çevirmə aralığı", "Çevirmə", "san.", 1.0, 0.2, 10.0, (-1, -0.1, 0.1, 1),
            digits=1, turbo=0.7, safe=1, backoff=1.5, recover=0.2, limit=5.0, env="DEPO_SINK_INTERVAL",
            info="Bir sink çatına forward-lar arası fasilə (file_id çevirmə). Böyük dəyər çevirməni yavaşladır.",
            tip="Çevirmə flood alırsa artır. 0.7 san.-dən aşağı salma, şəxsi çatda saniyədə 1-dən çox forward risklidir.",
            auto_info="Sink çatı flood alanda 50% artır (ən çoxu 5 san.), 5 dəq. sakit keçəndə 0.2 azalır."),
    Tunable("sink_max_wait", "⏳", "Sink gözləmə həddi", "Gözləmə", "san.", 20.0, 5.0, 120.0, (-10, -5, 5, 10),
            turbo=10.0, env="DEPO_SINK_MAX_WAIT",
            derive=lambda p, base: max(base, 15 * p.tune("sink_interval")),
            info="Çevirmə sink növbəsində bu qədərdən çox gözləməz. Eager rejimdə mahnı gözləmədən lazy yolla qeyd olunur.",
            tip="Kiçik dəyər: yükləmə heç gözləmir. Böyük dəyər: eager rejimdə çevirmə üçün daha çox səbr edir.",
            auto_info="Çevirmə aralığına görə hesablanır: ən azı 20 san., aralığın 15 qatı."),
    Tunable("warm_interval", "🔥", "Fon çevirici", "Fon", "san.", 3.0, 0.0, 60.0, (-5, -1, 1, 5),
            digits=1, turbo=1.5, env="DEPO_WARM_INTERVAL",
            derive=lambda p, base: 0.0 if base <= 0 else max(base, 3 * p.tune("sink_interval")),
            info="Lazy rejimdə boş vaxtda hər N saniyədən bir mahnı əvvəlcədən çevrilir. 0 — söndürülüb.",
            tip="Kiçik dəyər: mahnılar fonda tez hazır olur, amma sink çatı daha çox yüklənir. 0 yazsan fon çevirici dayanır.",
            auto_info="Çevirmə aralığına görə hesablanır: ən azı 3 san., aralığın 3 qatı."),
    Tunable("poll_every", "👋", "Yönləndirmə sorğusu", "Sorğu", "san.", 45, 10, 600, (-30, -10, 10, 30),
            turbo=90, safe=1, backoff=2.0, recover=5, limit=300,
            info="Köməkçilərə yazanlara cavab üçün Telegram-dan yeni mesajlar bu qədər saniyədən bir yoxlanılır.",
            tip="Böyük dəyər: köməkçiyə yazanlar cavabı gec alır, amma Telegram sorğuları azalır. Çox köməkçidə kiçik salma.",
            auto_info="Sorğu flood alanda 2 dəfə artır (ən çoxu 300 san.), 5 dəq. sakit keçəndə 5 azalır."),
    Tunable("chat_rate", "🚦", "Kanal sürəti", "Kanal", "mahnı/dəq.", 0, 0, 300, (-20, -10, 10, 20),
            info="Bir depo kanalına bütün köməkçilərin BİRLİKDƏ göndərə biləcəyi mahnı sayı. 0 — söndürülüb (standart): "
                 "Telegram kvotanı hər bota hər kanal üçün ayrıca verir, ümumi sürəti məhdudlaşdırmaq tutumu azaldır.",
            tip="Yalnız kanalda qeyri-adi flood görürsənsə sına (məs. 90). Normalda 0 saxla.",
            auto_info="Standart 0 (söndürülüb). Avto dəyişmir; yalnız ✋ Manual ilə işə salınır."),
    Tunable("long_flood", "🌊", "Kanal flood həddi", "Kanal", "san.", 60, 10, 600, (-30, -10, 10, 30),
            info="Köməkçinin flood gözləməsi bundan uzundursa, bu botun həmin kanal üçün saatlıq kvotası dolub deməkdir: "
                 "bot yalnız bu kanal üçün kənarlaşdırılır (başqa kanalda işləyir), sürət azalmır. Kanalda bütün "
                 "köməkçilər doldusa kanal dayanır və yükləmə digərinə keçir.",
            tip="Kiçik dəyər: kvota daha tez aşkarlanır. Böyük dəyər: uzun flood adi sürət limiti sayılıb sürəti azaldır.",
            auto_info="Standart 60 san. Qısa floodlar sürəti azaldır, bu həddən uzunlar botu həmin kanaldan kənarlaşdırır."),
)}

ADAPTIVE = tuple(k for k, t in TUNABLES.items() if t.safe)


class TuningMixin:
    """HelperPool-a qoşulur: self._get / self._set (baza ayarları) tələb olunur."""

    def _tune_init(self):
        self._auto = {}           # key → avtonun cari dəyəri
        self._auto_at = {}        # key → son dəyişiklik / flood anı (monotonic)
        self._flood_at = {}       # key → son flood anı

    @staticmethod
    def _k_mode(key: str) -> str:
        return f"depo_helpers:mode:{key}"

    @staticmethod
    def _k_auto(key: str) -> str:
        return f"depo_helpers:auto:{key}"

    @staticmethod
    def _k_val(key: str) -> str:
        return TUNABLES[key].vkey or f"depo_helpers:v:{key}"

    def _num(self, k: str, default=None):
        try:
            return float(self._get(k))
        except (TypeError, ValueError):
            return default

    @property
    def profile(self) -> str:
        v = self._get(S_PROFILE)
        return v if v in PROFILES else "balanced"

    @property
    def turbo(self) -> bool:
        return self.profile == "turbo"

    def set_profile(self, profile: str):
        """🚀 Köməkçi turbo / balanslı — avtonun başlanğıc dəyərləri dəyişir, öyrənilənlər sıfırlanır."""
        if profile not in PROFILES or profile == self.profile:
            return
        self._set(S_PROFILE, profile)
        for key in TUNABLES:
            self.reset_tune(key)
        logger.info(f"🎛 Köməkçi profili: {'🚀 tam performans' if profile == 'turbo' else 'balanslı'}")

    def toggle_turbo(self) -> bool:
        self.set_profile("balanced" if self.turbo else "turbo")
        return self.turbo

    def manual_keys(self) -> list:
        return [k for k in TUNABLES if self.tune_mode(k) == "manual"]

    def _limit(self, t: Tunable) -> float:
        return t.turbo_limit if self.turbo and t.turbo_limit is not None else t.limit

    def tune_base(self, key: str):
        """Avtonun başlanğıc (ən yaxşı) dəyəri: config.env → 🚀 tam performans → standart."""
        t = TUNABLES[key]
        try:
            if t.env and os.getenv(t.env):
                return t.clip(float(os.getenv(t.env)))
        except ValueError:
            pass
        return t.clip(t.turbo) if self.turbo and t.turbo is not None else t.default

    def tune_mode(self, key: str) -> str:
        m = self._get(self._k_mode(key))
        if m in ("auto", "manual"):
            return m
        v = self._num(self._k_val(key))
        return "manual" if v is not None and v != float(TUNABLES[key].default) else "auto"

    def tune(self, key: str):
        """Hazırda qüvvədə olan dəyər (rejimə görə)."""
        t = TUNABLES[key]
        if self.tune_mode(key) == "manual":
            v = self._num(self._k_val(key))
            return t.clip(t.default if v is None else v)
        return self.tune_auto(key)

    def tune_auto(self, key: str):
        """🤖 Avtonun dəyəri — manual rejimdə də göstərilə bilər (keçid anında başlanğıc nöqtə)."""
        t = TUNABLES[key]
        base = self.tune_base(key)
        if t.derive:
            return t.clip(t.derive(self, base))
        if not t.safe:
            return base                                  # uyğunlaşmayan ayar: avto = başlanğıc dəyər
        now = time.monotonic()
        cur = self._auto.get(key)
        if cur is None:
            lo, hi = sorted((base, self._limit(t)))
            cur = t.rnd(min(hi, max(lo, self._num(self._k_auto(key), base))))
            self._auto[key], self._auto_at[key] = cur, now
        elif cur != base:
            n = int((now - self._auto_at[key]) // RECOVER_AFTER)
            if n > 0:                                    # sakit dövr: başlanğıca doğru addım-addım
                cur = t.rnd(min(base, cur + n * t.recover) if t.safe < 0 else max(base, cur - n * t.recover))
                self._auto[key], self._auto_at[key] = cur, now
                self._set(self._k_auto(key), str(cur))
        return cur

    def note_flood(self, key: str):
        """Flood alındı → 🤖 avto rejimdəki ayar təhlükəsiz tərəfə bir addım sürüşür."""
        t = TUNABLES.get(key)
        if t is None or not t.safe or self.tune_mode(key) != "auto":
            return
        cur = self.tune_auto(key)
        now = time.monotonic()
        last = self._flood_at.get(key)
        self._flood_at[key] = self._auto_at[key] = now           # sakitlik sayğacı yenidən başlayır
        if last is not None and now - last < FLOOD_DEBOUNCE:
            return
        step = 10 ** -t.digits
        scaled = round(cur * t.backoff, t.digits)
        limit = self._limit(t)
        if t.safe < 0:
            nxt = max(limit, min(scaled, cur - step))
        else:
            nxt = min(limit, max(scaled, cur + step))
        nxt = t.rnd(nxt)
        if nxt == cur:
            return
        self._auto[key] = nxt
        self._set(self._k_auto(key), str(nxt))
        logger.info(f"🎛 {t.label}: {t.fmt(cur)} → {t.fmt(nxt)} (🤖 avto, flood)")

    def reset_tune(self, key: str):
        """🤖 Avtonun öyrəndiyini unudur — başlanğıc dəyərə qayıdır."""
        if key not in TUNABLES or TUNABLES[key].derive or not TUNABLES[key].safe:
            return
        base = self.tune_base(key)
        self._auto[key], self._auto_at[key] = base, time.monotonic()
        self._flood_at.pop(key, None)
        self._set(self._k_auto(key), str(base))

    def set_tune(self, key: str, val):
        """✋ Manual dəyər (rejim manual olur) → yazılan, hədlərə salınmış dəyər."""
        t = TUNABLES[key]
        v = t.clip(val)
        self._set(self._k_val(key), str(v))
        self._set(self._k_mode(key), "manual")
        return v

    def step_tune(self, key: str, delta: float):
        return self.set_tune(key, self.tune(key) + delta)

    def set_tune_mode(self, key: str, mode: str):
        if key not in TUNABLES or mode not in ("auto", "manual"):
            return
        if mode == "manual" and self._num(self._k_val(key)) is None:
            self._set(self._k_val(key), str(self.tune(key)))     # dəyər tullanmasın: indikindən başla
        self._set(self._k_mode(key), mode)

    def all_auto(self):
        for key in TUNABLES:
            self.set_tune_mode(key, "auto")

    def flood_ago(self, key: str):
        """Son flood neçə saniyə əvvəl olub (bu işə salınmadan bəri), yoxdursa None."""
        at = self._flood_at.get(key)
        return None if at is None else max(0.0, time.monotonic() - at)
