"""
🧩 Depo shard-ları — eyni mənbə üçün BİR NEÇƏ kanal (Telegram-ın flood limiti çat səviyyəsindədir).

Telegram HƏR BOTA HƏR KANAL üçün ayrıca saatlıq kvota verir (köhnə log: 48 botun flood-u 00:06–00:36 arası tədricən gəldi,
yükləmə 150–200/dəq. davam etdi, hamı dolanda dayandı; bütün banlar eyni pəncərədə bitir). Ona görə əlavə kanal tutumu
artırır və kanalda BÜTÜN köməkçilər dolanda yükləmə digərinə keçir:

  • əsas depo (audio_cache.depo_setting) həmişə birinci kanaldır, əlavə kanallar `/depo shard add <kanal>` ilə qoşulur;
  • `choose` — flood-da olmayan və köməkçisi olan kanallardan ən az yüklənəni seçir (ümumi tutuma bölünmüş yük);
  • bot flood alanda yalnız O BOT bu kanal üçün kənarlaşdırılır (Helper.cool_chat); kanalda hamı dolanda `pause` —
    kanal dayanır, yükləmə digərinə keçir (depo_helpers.HelperPool.upload);
  • köhnə mahnılar öz yerində qalır: audio_cache hər sətirdə chat_id saxlayır.

Siyasət (yeni mahnı hansı kanala getsin):
  🤖 Avto   — flood-da olmayan kanallardan, köməkçi sayına görə ən az yüklənən;
  ✋ Manual — sizin verdiyiniz paylar (çəki): yük paylara mütənasib bölünür, pay 0 — kanala yeni mahnı getmir.
Flood-da olan kanal hər iki siyasətdə atlanır.

Əlavə kanal yoxdursa heç nə dəyişmir (choose əsas kanalı qaytarır).
Faza 1: yalnız əsas ("ytm") depo üçün qoşulub — doldurucu və flood orada olur; "yt" yuvası sonra.
"""
import json
import logging
import time
from collections import deque

from common.database import Settings

logger = logging.getLogger(__name__)

SLOT = "ytm"
S_SHARDS, S_PAUSE = "shards", "shard_pause"
S_POLICY, S_WEIGHTS = "shard_policy", "shard_weights"
MAIN_KEY = "main"             # əsas deponun çəki açarı (əlavə kanallar üçün — ref kiçik hərflə)
DEFAULT_WEIGHT, MAX_WEIGHT = 100, 1000
LOAD_WINDOW = 600.0           # yük hesabı üçün son 10 dəq.
RESOLVE_RETRY = 120.0         # kanal tapılmayanda təkrar cəhd
_cfg = Settings("depo:", empty_is_default=True)

_ids = {}             # ref (kiçik hərflə) → {"id": int | None, "ts": monotonic}
_primary = {}         # slot → {"id": int, "thread": int | None} (choose çağırışlarından)
_pause = {}           # chat_id → monotonic (flood bitənə qədər)
_sent = {}            # chat_id → deque(monotonic) — son yükləmələr
_slot_at = {}         # chat_id → növbəti boş yazma anı (monotonic): kanala ümumi sürət (chat_slot)
_main_ok = {}         # chat_id → əsas bot orada admin-dirmi (yoxlanana qədər kanal istifadə olunmur)
_bad = {}             # chat_id → (monotonic, səbəb): göndərmə "Forbidden" verdi, bir müddət istifadə olunmur
BAD_FOR = 600.0
_loaded = False
capacity_fn = None    # (chat_id) → neçə köməkçi bu kanalda işləyir; HelperPool qeyd edir
exhausted_fn = None   # (chat_id) → bu kanalda BÜTÜN köməkçilər kvotasını dolduruble; HelperPool qeyd edir


def _key(ref) -> str:
    return str(ref).strip().lower()


# ── əlavə kanalların siyahısı ──
def _read() -> dict:
    try:
        data = json.loads(_cfg.get(S_SHARDS) or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def extras(slot: str = SLOT) -> list:
    """[{"ref": "@kanal" | "-100…", "thread": int | None}] — əsas kanaldan başqa kanallar."""
    out = []
    for e in _read().get(slot) or []:
        if isinstance(e, dict) and e.get("ref"):
            out.append({"ref": str(e["ref"]), "thread": e.get("thread") or None})
    return out


def _write(slot: str, items: list):
    data = _read()
    data[slot] = items
    _cfg.put(S_SHARDS, json.dumps(data, ensure_ascii=False))


def add(ref, thread=None, slot: str = SLOT) -> str:
    """→ "ok" | "dup" | "empty"."""
    if not str(ref or "").strip():
        return "empty"
    items = extras(slot)
    if any(_key(e["ref"]) == _key(ref) for e in items):
        return "dup"
    items.append({"ref": str(ref).strip(), "thread": int(thread) if thread else None})
    _write(slot, items)
    return "ok"


def remove(ref, slot: str = SLOT) -> bool:
    items = extras(slot)
    kept = [e for e in items if _key(e["ref"]) != _key(ref)]
    if len(kept) == len(items):
        return False
    _write(slot, kept)
    for cid in [i["id"] for k, i in _ids.items() if k == _key(ref) and i.get("id")]:
        _pause.pop(cid, None)
        _main_ok.pop(cid, None)
        _bad.pop(cid, None)
    _ids.pop(_key(ref), None)
    return True


# ── əsas bot kanalda admin-dirmi (köməkçilər yazsa da, çevirmə / ehtiyat yükləmə əsas botla olur) ──
def set_main_ok(chat_id, ok: bool):
    _main_ok[chat_id] = bool(ok)
    if ok:
        _bad.pop(chat_id, None)


def main_ok(chat_id) -> bool:
    return _main_ok.get(chat_id) is True


def mark_bad(chat_id, why: str = ""):
    """Göndərmə qadağan oldu (əsas bot üzv deyil və s.) → kanal BAD_FOR saniyə seçilmir, əsas depo toxunulmaz qalır."""
    if chat_id not in _bad or time.monotonic() - _bad[chat_id][0] > BAD_FOR:
        logger.warning(f"🧩 Shard {chat_id} {BAD_FOR / 60:.0f} dəq. dayandırıldı: {why[:120]}")
    _bad[chat_id] = (time.monotonic(), why)


def is_bad(chat_id) -> bool:
    hit = _bad.get(chat_id)
    return bool(hit) and time.monotonic() - hit[0] < BAD_FOR


def usable(chat_id) -> bool:
    """Əlavə kanal yük qəbul edə bilərmi: əsas bot admin, köməkçisi var, yaxın vaxtda xəta verməyib."""
    return main_ok(chat_id) and not is_bad(chat_id) and _cap(chat_id) > 0


# ── siyasət: 🤖 avto / ✋ manual (paylar) ──
def policy() -> str:
    return "manual" if _cfg.get(S_POLICY) == "manual" else "auto"


def set_policy(value: str):
    _cfg.put(S_POLICY, "manual" if value == "manual" else "auto")


def _weights() -> dict:
    try:
        data = json.loads(_cfg.get(S_WEIGHTS) or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def weight(key) -> int:
    k = MAIN_KEY if key in (None, MAIN_KEY) else _key(key)
    try:
        return max(0, min(MAX_WEIGHT, int(_weights().get(k, DEFAULT_WEIGHT))))
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT


def set_weight(key, value) -> int:
    k = MAIN_KEY if key in (None, MAIN_KEY) else _key(key)
    w = max(0, min(MAX_WEIGHT, int(value)))
    data = _weights()
    data[k] = w
    _cfg.put(S_WEIGHTS, json.dumps(data))
    return w


# ── kanal ID-lərinin tapılması ──
async def resolve(bot, ref):
    """Kanalın rəqəmli ID-si (əsas bot əlçatan olmalıdır); tapılmayanda None (RESOLVE_RETRY-dən tez-tez soruşmur)."""
    k = _key(ref)
    hit = _ids.get(k)
    if hit and (hit["id"] or time.monotonic() - hit["ts"] < RESOLVE_RETRY):
        return hit["id"]
    cid = None
    try:
        chat = await bot.get_chat(int(ref) if str(ref).lstrip("-").isdigit() else ref)
        cid = chat.id
    except Exception as e:
        logger.warning(f"🧩 Shard kanalı əlçatan deyil ({ref}): {e} — əsas botu kanala admin əlavə edin")
    _ids[k] = {"id": cid, "ts": time.monotonic()}
    return cid


def shard_ids(slot: str = SLOT) -> list:
    return [i["id"] for e in extras(slot) if (i := _ids.get(_key(e["ref"]))) and i.get("id")]


def is_shard(chat_id) -> bool:
    try:
        return int(chat_id) in shard_ids()
    except (TypeError, ValueError):
        return False


def ref_of(chat_id):
    """Əlavə kanalın istifadəçi adı / id-si (bilinmirsə None) — postu başqa hesabla açmaq üçün."""
    for e in extras():
        i = _ids.get(_key(e["ref"]))
        if i and i.get("id") == chat_id:
            return e["ref"]
    return None


# ── flood dayanması ──
def _load_pause():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_cfg.get(S_PAUSE) or "{}")
        now_m, now_t = time.monotonic(), time.time()
        for cid, until in data.items():
            if float(until) > now_t:
                _pause[int(cid)] = now_m + float(until) - now_t
    except (ValueError, TypeError):
        pass


def _save_pause():
    now_m, now_t = time.monotonic(), time.time()
    _cfg.put(S_PAUSE, json.dumps({str(c): now_t + (u - now_m) for c, u in _pause.items() if u > now_m}))


def pause(chat_id, wait: float) -> bool:
    """Kanalı `wait` saniyə dayandırır → ilk dəfədirsə True (eyni flood-u digər köməkçilər də bildirir)."""
    _load_pause()
    until = time.monotonic() + wait + 1
    first = until > _pause.get(chat_id, 0) + 5
    if first:
        _pause[chat_id] = until
        _save_pause()
    return first


def left(chat_id) -> float:
    _load_pause()
    return max(0.0, _pause.get(chat_id, 0.0) - time.monotonic())


def clear():
    _pause.clear()
    _cfg.put(S_PAUSE, "{}")


# ── kanal başına ÜMUMİ sürət: sürü effektinin qarşısı ──
def chat_slot(chat_id, per_min: float, not_before: float = 0.0) -> float:
    """Bu kanala növbəti yazma üçün yer ayırır → başlama anı (monotonic).
    Bütün köməkçilər eyni cədvəli bölüşür: restart və ya flood bitəndə yüzlərlə işçi eyni saniyədə yazmır —
    60/per_min saniyə aralıqla sıralanır."""
    at = max(not_before, time.monotonic(), _slot_at.get(chat_id, 0.0))
    _slot_at[chat_id] = at + 60.0 / max(1.0, float(per_min))
    return at


# ── yük ──
def note_sent(chat_id):
    dq = _sent.setdefault(chat_id, deque())
    now = time.monotonic()
    dq.append(now)
    while dq and now - dq[0] > LOAD_WINDOW:
        dq.popleft()


def load(chat_id) -> int:
    dq = _sent.get(chat_id)
    if not dq:
        return 0
    now = time.monotonic()
    while dq and now - dq[0] > LOAD_WINDOW:
        dq.popleft()
    return len(dq)


def _exhausted(chat_id) -> bool:
    try:
        return bool(exhausted_fn and exhausted_fn(chat_id))
    except Exception:
        return False


def _cap(chat_id) -> int:
    try:
        return max(0, int(capacity_fn(chat_id))) if capacity_fn else 1
    except Exception:
        return 1


def _pick(cands: list):
    """cands: [(chat_id, thread, açar)] → (chat_id, thread). Flood-da olmayanlardan siyasətə görə seçir."""
    manual = policy() == "manual"
    pool = [c for c in cands if weight(c[2]) > 0] or cands[:1] if manual else cands     # pay 0 → yeni mahnı getmir
    free = [c for c in pool if left(c[0]) <= 0 and not _exhausted(c[0])] or [min(pool, key=lambda c: left(c[0]))]
    if manual:
        score = lambda c: load(c[0]) / max(1, weight(c[2]))                                      # noqa: E731
    else:
        score = lambda c: load(c[0]) / max(1, _cap(c[0]))                                # noqa: E731
    best = min(free, key=lambda c: (score(c), cands.index(c)))
    return best[0], best[1]


async def choose(bot, slot: str, primary_id, primary_thread):
    """(chat_id, thread) — siyasətə görə: 🤖 ən az yüklənən (köməkçi sayına bölünmüş) və ya ✋ paylara mütənasib.
    Flood-da olan və yararsız kanallar atlanır. Əlavə kanal yoxdursa — əsas."""
    _primary[slot] = {"id": primary_id, "thread": primary_thread}
    ex = extras(slot)
    if not ex:
        return primary_id, primary_thread
    cands = [(primary_id, primary_thread, MAIN_KEY)]
    for e in ex:
        cid = await resolve(bot, e["ref"])
        if cid and cid != primary_id and usable(cid):
            cands.append((cid, e.get("thread"), _key(e["ref"])))
    if len(cands) == 1:
        return primary_id, primary_thread
    return _pick(cands)


def alternative(chat_id):
    """Kvotası dolan kanal əvəzinə: (chat_id, thread) və ya None — yalnız dayanmayan, kvotası dolmayan, yararlı kanallar
    (✋ manualda pay 0 olan kanallar da çıxarılır)."""
    prim = _primary.get(SLOT) or {}
    cands, known = [], set()
    if prim.get("id"):
        cands.append((prim["id"], prim.get("thread"), MAIN_KEY))
        known.add(prim["id"])
    for e in extras():
        i = _ids.get(_key(e["ref"]))
        if i and i.get("id"):
            known.add(i["id"])
            if usable(i["id"]):
                cands.append((i["id"], e.get("thread"), _key(e["ref"])))
    if chat_id not in known:
        return None
    manual = policy() == "manual"
    free = [c for c in cands if c[0] != chat_id and left(c[0]) <= 0 and not _exhausted(c[0])
            and (not manual or weight(c[2]) > 0)]
    if not free:
        return None
    best = min(free, key=lambda c: load(c[0]) / max(1, weight(c[2]) if manual else _cap(c[0])))
    return best[0], best[1]


def report(slot: str = SLOT) -> list:
    """Panel üçün: [{"ref", "id", "thread", "left", "sent", "helpers", "primary"}]"""
    prim = _primary.get(slot) or {}
    out = []
    if prim.get("id"):
        out.append({"ref": "əsas", "id": prim["id"], "thread": prim.get("thread"), "left": left(prim["id"]),
                    "sent": load(prim["id"]), "helpers": _cap(prim["id"]), "primary": True, "key": MAIN_KEY,
                    "weight": weight(MAIN_KEY), "main_ok": True, "bad": False})
    for e in extras(slot):
        cid = (_ids.get(_key(e["ref"])) or {}).get("id")
        out.append({"ref": e["ref"], "id": cid, "thread": e.get("thread"), "left": left(cid) if cid else 0.0,
                    "sent": load(cid) if cid else 0, "helpers": _cap(cid) if cid else 0, "primary": False,
                    "main_ok": main_ok(cid) if cid else False, "bad": is_bad(cid) if cid else False,
                    "key": _key(e["ref"]), "weight": weight(e["ref"])})
    return out

