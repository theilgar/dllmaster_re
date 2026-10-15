"""
⚡ Inline axtarış ayarları — bot/core/inline_settings.py

/menu → 🎵 Musiqi → ⚡ Inline axtarış ilə idarə olunur, settings cədvəlində saxlanır
(açar: inline:<ad>). Dəyər təyin edilməyibsə config.env-dəki köhnə dəyişən, o da yoxdursa default.
Hər inline sorğuda oxunur (get_setting yaddaşdan gəlir — ucuzdur), restart lazım deyil.
"""
import os

from common.database import get_db

# ad: (env dəyişəni, default, addımlar, vahid, izah)
SPEC = {
    "debounce": ("INLINE_DEBOUNCE", 0.35, [0, 0.2, 0.35, 0.5, 0.8, 1.2], "san",
                 "hərf yazılarkən gözləmə — az: sürətli, çox: YouTube-a az sorğu"),
    "ytwait":   ("INLINE_YT_WAIT", 2.5, [1, 1.5, 2, 2.5, 3, 4, 6], "san",
                 "depoda nəticə varsa YouTube-u ən çox bu qədər gözlə"),
    "timeout":  ("INLINE_SEARCH_TIMEOUT", 12, [5, 8, 10, 12, 15, 20, 30], "san",
                 "bir axtarışın maksimum müddəti"),
    "limit":    ("INLINE_LIMIT", 10, [5, 8, 10, 15, 20, 30, 50], "",
                 "YouTube nəticələrinin sayı (az — daha sürətli)"),
    "ttl":      ("INLINE_CACHE_MIN", 30, [5, 10, 30, 60, 180, 720], "dəq",
                 "eyni sorğu bu müddətdə keşdən dərhal gəlir"),
    "depo":     ("INLINE_DEPO_HITS", 5, [0, 3, 5, 10, 15], "",
                 "depodan neçə mahnı əvvəldə göstərilsin (0 — söndür)"),
    # ── /music (çatda axtarış) ──
    "rank":     ("INLINE_RANK", 1, [0, 1], "",
                 "sıralama: 🤖 Avto — soruşulan mahnı önə (uyğunluğa görə); 📦 Depo əvvəl — depo həmişə yuxarıda"),
    "chat_fast":  ("MUSIC_FAST", 1, [0, 1], "",
                   "növbəsiz sürətli axtarış (yükləmələri gözləmir, cookies/JS-siz)"),
    "chat_first": ("MUSIC_FIRST", 10, [5, 10, 15, 25, 40], "",
                   "ilk axtarışda gətirilən nəticə — az: sürətli (▶️ ilə daha çox gəlir)"),
    "chat_depo":  ("MUSIC_DEPO_HITS", 5, [0, 3, 5, 10], "",
                   "depodan neçə mahnı əvvəldə (0 — söndür)"),
    "chat_rank":  ("MUSIC_RANK", 1, [0, 1], "",
                   "sıralama: 🤖 Avto — soruşulan mahnı önə (uyğunluğa görə); 📦 Depo əvvəl — depo həmişə yuxarıda"),
    # ── 🔀 Mix (inline mix:<id> və /mix) ──
    "mix_fast":  ("MIX_FAST", 1, [0, 1], "",
                  "növbəsiz sürətli Mix (yükləmələri gözləmir, cookies/JS-siz)"),
    "mix_size":  ("MIX_SIZE", 25, [10, 15, 25, 40, 50], "",
                  "bir dəfədə gətirilən oxşar mahnı — az: sürətli"),
    "mix_page":  ("MIX_PAGE", 5, [5, 8, 10, 20], "",
                  "inline Mix-də bir səhifədə neçə mahnı (aşağı sürüşdürdükcə növbəti)"),
    "mix_long":  ("MIX_MAX_MIN", 0, [0, 8, 10, 15, 20, 30], "dəq",
                  "bundan uzun videoları at (1 saatlıq toplular) — 0: hamısı qalsın"),
    "mix_ttl":   ("MIX_CACHE_MIN", 10, [5, 10, 30, 60, 180], "dəq",
                  "eyni mahnının Mix-i bu müddətdə keşdən dərhal gəlir"),
}
INLINE_ORDER = ["debounce", "ytwait", "limit", "depo", "rank"]
CHAT_ORDER = ["chat_fast", "chat_first", "chat_depo", "chat_rank"]
SHARED_ORDER = ["timeout", "ttl"]
MIX_ORDER = ["mix_fast", "mix_size", "mix_page", "mix_long", "mix_ttl"]
ORDER = INLINE_ORDER + CHAT_ORDER + MIX_ORDER + SHARED_ORDER
TOGGLES = {"chat_fast", "mix_fast", "rank", "chat_rank"}
RANK_TOGGLES = {"rank", "chat_rank"}
TITLES = {"debounce": "⏳ Gözləmə", "ytwait": "📦 Depo varsa YT gözləmə", "timeout": "⏱ Axtarış limiti",
          "limit": "🔢 Nəticə sayı", "ttl": "🗂 Keş müddəti", "depo": "📦 Depo nəticəsi",
          "rank": "🔀 Sıralama", "chat_rank": "🔀 Sıralama", "chat_fast": "⚡ Sürətli rejim", "chat_first": "🔢 İlk nəticələr", "chat_depo": "📦 Depo nəticəsi",
          "mix_fast": "⚡ Sürətli Mix", "mix_size": "🔢 Mix ölçüsü", "mix_page": "📄 Inline səhifə",
          "mix_long": "⏳ Uzun videolar", "mix_ttl": "🗂 Mix keşi"}


def _num(v, default):
    try:
        f = float(v)
        return int(f) if f.is_integer() and isinstance(default, int) else f
    except (TypeError, ValueError):
        return default


def get(name: str):
    env, default, *_ = SPEC[name]
    if is_auto(name):
        return auto_value(name)
    try:
        v = get_db().get_setting(f"inline:{name}")
    except Exception:
        v = None
    if v is None:
        v = os.getenv(env)
    return _num(v, default)


def put(name: str, value):
    """Dəyəri addımların sərhədinə sıxır: məs. timeout=0 inline axtarışı tamam sındırırdı (min 5 san)."""
    if value is not None:
        steps = SPEC[name][2]
        value = min(max(value, steps[0]), steps[-1] * 3)
    if value is not None and is_auto(name):
        set_auto(name, False)                     # əl ilə dəyişdi → Manual
    get_db().set_setting(f"inline:{name}", None if value is None else str(value))


def toggle(name: str) -> int:
    new = 0 if get(name) else 1
    put(name, new)
    return new


def step(name: str, direction: int):
    steps = SPEC[name][2]
    cur = get(name)
    if cur in steps:
        i = steps.index(cur) + direction
    else:
        i = sum(1 for s in steps if s < cur) - (1 if direction < 0 else 0)
    new = steps[max(0, min(len(steps) - 1, i))]
    put(name, new)
    return new


def reset():
    for name in SPEC:
        put(name, None)
        set_auto(name, False)


def fmt(name: str) -> str:
    return _fmt(name) + (" 🤖" if is_auto(name) else "")


def _fmt(name: str) -> str:
    v = get(name)
    unit = SPEC[name][3]
    if name in RANK_TOGGLES:
        return "🤖 Avto (uyğunluğa görə)" if v else "📦 Depo əvvəl"
    if name in TOGGLES:
        return "✅ açıq" if v else "❌ bağlı"
    if name in ("depo", "chat_depo") and not v:
        return "söndürülüb"
    if name == "mix_long" and not v:
        return "hamısı qalır"
    if isinstance(v, float):
        v = f"{v:g}"
    return f"{v} {unit}".strip()


def is_default(name: str) -> bool:
    try:
        return get_db().get_setting(f"inline:{name}") is None
    except Exception:
        return True


# ───────────────────────── 🤖 Avto rejim (hər ayar üçün) ─────────────────────────
# Avto: dəyəri bot özü seçir — son sorğuların orta cavab müddətinə, xəta / vaxt aşımı və keş uğuruna görə.
# Əl ilə dəyişdirsən həmin ayar avtomatik Manual-a keçir. Sorğu azdırsa (<5) — defolt.
_GROUP = {"chat_fast": "chat", "chat_first": "chat", "chat_depo": "chat", "chat_rank": "chat",
          "mix_fast": "mix", "mix_size": "mix", "mix_page": "mix", "mix_long": "mix", "mix_ttl": "mix"}
_stats_provider = None
MIN_SAMPLES = 5


def set_stats_provider(fn):
    """fn() → {"inline": {...}, "chat": {...}, "mix": {...}} (queries, cache_hits, errors, timeouts, answered, total)."""
    global _stats_provider
    _stats_provider = fn


def is_auto(name: str) -> bool:
    try:
        return get_db().get_setting(f"inline:auto:{name}") == "1"
    except Exception:
        return False


def set_auto(name: str, on: bool):
    get_db().set_setting(f"inline:auto:{name}", "1" if on else None)


def set_all_auto(on: bool):
    for name in SPEC:
        set_auto(name, on)


def _metrics(name: str) -> dict:
    group = _GROUP.get(name, "inline")
    try:
        st = (_stats_provider() or {}).get(group) or {}
    except Exception:
        st = {}
    q = st.get("queries", 0)
    ans = st.get("answered", 0)
    return {"q": q, "avg": (st.get("total", 0.0) / ans) if ans else 0.0,
            "hit": (st.get("cache_hits", 0) / q) if q else 0.0,
            "err": ((st.get("errors", 0) + st.get("timeouts", 0)) / q) if q else 0.0}


def _snap(name: str, v):
    """Ən yaxın addıma yuvarlaqlaşdır."""
    steps = SPEC[name][2]
    return min(steps, key=lambda x: abs(x - v))


def auto_value(name: str):
    """Hazırkı statistikaya görə tövsiyə olunan dəyər (SPEC addımlarından biri)."""
    default = SPEC[name][1]
    m = _metrics(name)
    if m["q"] < MIN_SAMPLES:
        return default
    slow, fast, flaky = m["avg"] > 3, m["avg"] < 1, m["err"] > 0.15
    if name == "debounce":
        v = 0.5 if (slow or flaky) else (0.2 if fast else default)
    elif name == "ytwait":
        v = 4 if flaky else (1.5 if fast else default)
    elif name == "timeout":
        v = 20 if flaky else (10 if m["avg"] < 2 else default)
    elif name == "limit":
        v = 8 if slow else (15 if fast else default)
    elif name == "ttl":
        v = 60 if m["hit"] > 0.4 else (10 if m["hit"] < 0.1 and m["q"] >= 20 else default)
    elif name == "chat_first":
        v = 5 if m["avg"] > 4 else (8 if slow else default)
    elif name == "mix_size":
        v = 15 if m["avg"] > 4 else default
    elif name == "mix_ttl":
        v = 30 if m["hit"] > 0.4 else default
    elif name == "mix_long":
        v = 20                                  # 20 dəq.-dən uzun toplular (1 saatlıq mix) atılsın
    else:                                       # depo sayı, sürətli rejim, sıralama, səhifə — sabit ağıllı defolt
        v = default
    return _snap(name, v)
