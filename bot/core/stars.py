"""
⭐ Telegram Stars ilə premium satışı — bot/core/stars.py

Planlar sabitdir, qiymətləri creator /menu → 💎 Premium → ⭐ Ulduz satışı bölməsindən təyin edir.
Qiyməti təyin olunmayan plan satışda deyil.
"""
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from common.database import get_db

logger = logging.getLogger(__name__)

# (açar, gün sayı | None = ömürlük, ad)
PLANS = [
    ("7", 7, "7 gün"),
    ("30", 30, "30 gün"),
    ("90", 90, "90 gün"),
    ("365", 365, "1 il"),
    ("life", None, "♾ Ömürlük"),
]
PLAN_MAP = {key: (days, label) for key, days, label in PLANS}
MIN_STARS, MAX_STARS = 1, 10000

# ⭐ Bəxşiş (əməyə dəstək) — hazır məbləğlər; istifadəçi öz məbləğini də yaza bilər
TIP_AMOUNTS = [10, 25, 50, 100, 250, 500]
TIP_PLAN = "tip"


def get_price(plan: str):
    """Planın qiyməti (⭐) və ya None (satışda deyil)."""
    try:
        value = get_db().get_setting(f"stars_price:{plan}")
        return int(value) if value and value.isdigit() and int(value) > 0 else None
    except Exception as e:
        logger.warning(f"Qiymət oxunmadı ({plan}): {e}")
        return None


def set_price(plan: str, stars):
    if plan not in PLAN_MAP:
        raise ValueError(plan)
    get_db().set_setting(f"stars_price:{plan}", str(int(stars)) if stars else None)


# ── 🏷 Endirim kampaniyası: vaxt aralığı + faiz (hamı planlara) ──
SALE_PCTS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 70]


def _sale_int(key: str):
    try:
        v = get_db().get_setting(f"stars_sale:{key}")
        return int(v) if v not in (None, "") else None
    except Exception:
        return None


def sale_settings() -> dict:
    """{on, pct, start, end} — start/end epoch (None — məhdudiyyət yoxdur)."""
    return {"on": _sale_int("on") == 1, "pct": _sale_int("pct") or 20,
            "start": _sale_int("start"), "end": _sale_int("end")}


def set_sale(key: str, value) -> None:
    get_db().set_setting(f"stars_sale:{key}", None if value is None else str(int(value)))


def sale_active(now: float = None) -> bool:
    import time
    s = sale_settings()
    now = now if now is not None else time.time()
    if not s["on"] or not (1 <= s["pct"] <= 95):
        return False
    return (s["start"] is None or now >= s["start"]) and (s["end"] is None or now < s["end"])


def parse_sale_time(text: str, now: float = None, tz=None):
    """İstifadəçi yazısı → epoch. '-' → None (məhdudiyyət yox). Format: 25.12.2026 18:00 · 25.12 18:00 · 18:00 · +3g · +12s.
    Səhv formatda ValueError."""
    import re
    import time
    from datetime import datetime, timedelta
    if tz is None:
        from common.formatting import get_tz
        tz = get_tz()
    now = now if now is not None else time.time()
    t = (text or "").strip().lower()
    if t in ("-", "yox", "off", "0"):
        return None
    m = re.fullmatch(r"\+(\d+)\s*(g|gün|d|s|h|saat|dəq|dq|m)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        sec = 86400 if u in ("g", "gün", "d") else 3600 if u in ("s", "h", "saat") else 60
        return int(now + n * sec)
    dt_now = datetime.fromtimestamp(now, tz)
    m = re.fullmatch(r"(?:(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+)?(\d{1,2}):(\d{2})", t)
    if not m:
        raise ValueError(text)
    d, mo, y, h, mi = m.groups()
    year = int(y) + (2000 if y and len(y) == 2 else 0) if y else dt_now.year
    try:
        if d is None:
            dt = dt_now.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
            if dt <= dt_now:
                dt += timedelta(days=1)
        else:
            dt = datetime(year, int(mo), int(d), int(h), int(mi), tzinfo=tz)
    except ValueError:
        raise ValueError(text)
    return int(dt.timestamp())


def discounted(base: int, pct: int) -> int:
    return max(1, round(base * (100 - pct) / 100))


def effective_price(plan: str):
    """Ödənişdə istifadə olunan qiymət: aktiv endirim varsa endirimli, yoxsa əsas."""
    base = get_price(plan)
    if base and sale_active():
        return discounted(base, sale_settings()["pct"])
    return base


def plan_offers() -> list:
    """[(açar, gün, ad, qiymət, əsas_qiymət)] — yalnız satışdakılar; qiymət endirimli ola bilər."""
    out = []
    active = sale_active()
    pct = sale_settings()["pct"] if active else 0
    for key, days, label in PLANS:
        base = get_price(key)
        if base:
            out.append((key, days, label, discounted(base, pct) if active else base, base))
    return out


def plans_for_sale() -> list:
    """[(açar, gün, ad, qiymət)] — yalnız qiyməti olanlar (endirim aktivdirsə endirimli qiymət)."""
    return [(k, d, l, p) for k, d, l, p, _ in plan_offers()]


def per_month(days, price) -> str:
    if not days or days < 30:
        return ""
    return f" (~{price * 30 / days:.0f}⭐/ay)"


def buy_keyboard(current_until_is_lifetime: bool = False):
    """/premium altındakı alış düymələri; satışda plan yoxdursa None."""
    if current_until_is_lifetime:
        return None
    offers = plan_offers()
    if not offers:
        return None
    rows = [[InlineKeyboardButton(
        text=(f"🔥 {label} — {price} ulduz (əvvəl {base})" if price < base else f"⭐ {label} — {price} ulduz"),
        callback_data=f"buy:{key}")] for key, days, label, price, base in offers]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def make_payload(plan: str, user_id: int, price: int) -> str:
    return f"prem:{plan}:{user_id}:{price}"


def make_tip_payload(user_id: int, amount: int) -> str:
    return f"tip:{user_id}:{amount}"


def parse_tip_payload(payload: str):
    """(user_id, amount) və ya None"""
    try:
        kind, uid, amount = (payload or "").split(":")
        return (int(uid), int(amount)) if kind == "tip" else None
    except ValueError:
        return None


def parse_payload(payload: str):
    """(plan, user_id, price) və ya None"""
    try:
        kind, plan, uid, price = (payload or "").split(":")
        if kind != "prem" or plan not in PLAN_MAP:
            return None
        return plan, int(uid), int(price)
    except ValueError:
        return None
