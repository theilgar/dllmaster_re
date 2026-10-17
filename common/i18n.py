"""
🌐 Dil dəstəyi — common/i18n.py

  _("Azərbaycan mətni", ad=dəyər)   → cari istifadəçinin dilində (tərcümə yoxdursa — Azərbaycanca)
  current() / use(lang)             → cari dil (hər update üçün bot/main.py middleware təyin edir)
  get_lang(uid) / set_lang(uid, l)  → istifadəçinin seçimi (settings: lang:<uid>)
  template(slot, lang)              → /start bələdçilərinin tərcümələri (start_plugin)

Açar — Azərbaycan mətninin ÖZÜDÜR (gettext kimi): yeni yerə tərcümə əlavə etmək üçün mətni _() ilə
bükmək və lang/*.json-a sətir yazmaq kifayətdir. Admin paneli (/menu) Azərbaycanca qalır — onu yalnız sahibi görür.

📁 DİL FAYLLARI — lang/<kod>.json (yeni fayl = yeni dil, restart lazım deyil: reload()):
  {"_meta": {"code": "de", "name": "🇩🇪 Deutsch", "telegram_codes": ["de", "at"]},
   "templates": {"welcome": "...", "group": "...", ...},       # /start bələdçiləri (yer tutucular: {name} {bot} ...)
   "strings":   {"<Azərbaycanca mətn>": "<tərcümə>", ...}}
  Şablon fayl (bütün açarlar boş): export_template() · /menu → 🎨 Mesajlar → 🌐 Dillər → 📤 Şablon
  Dillər yalnız bu fayllardır: lang/-da neçə fayl varsa dil seçimində o qədər dil (+ əsas az) görünür; fayl silinsə dil də itir.
"""
from __future__ import annotations

import contextvars
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "lang"
DEFAULT = "az"
_BASE_DETECT = {"az": "az"}          # qalan Telegram dil kodları lang/*.json-un _meta.telegram_codes-undan gəlir
CODE_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?$")
TEMPLATE_VARS = {"name", "bot", "status", "creator", "group", "thanks", "rights", "perks", "plans"}

# reload() doldurur
LANGS = {"az": "🇦🇿 Azərbaycanca"}
STRINGS: dict = {}            # dil → {az mətn: tərcümə}
TPL: dict = {}                # dil → {slot: şablon}
_DETECT = dict(_BASE_DETECT)
REPORT: dict = {}             # dil → {"file", "strings", "templates", "skipped": [...], "error"}

_cur: contextvars.ContextVar = contextvars.ContextVar("lang", default=DEFAULT)
_cache: dict = {}


def _s(key, default=None):
    try:
        from common.database import get_db
        v = get_db().get_setting(key)
    except Exception:
        v = None
    return default if v is None else v


def default_lang() -> str:
    v = _s("i18n:default", DEFAULT)
    return v if v in LANGS else DEFAULT


def auto_detect() -> bool:
    return _s("i18n:auto", "1") == "1"


def detect(code: str | None) -> str:
    if not code or not auto_detect():
        return default_lang()
    c = code.lower().replace("_", "-")
    lang = _DETECT.get(c) or _DETECT.get(c.split("-")[0])
    if lang in LANGS:
        return lang
    return default_lang()


def get_lang(uid, code: str | None = None) -> str:
    if uid in _cache:
        return _cache[uid]
    v = _s(f"lang:{uid}") if uid else None
    if v in LANGS:
        _cache[uid] = v
        return v
    return detect(code)


def chosen(uid) -> bool:
    return _s(f"lang:{uid}") in LANGS


def set_lang(uid, lang: str):
    if lang not in LANGS:
        return
    from common.database import get_db
    get_db().set_setting(f"lang:{uid}", lang)
    _cache[uid] = lang


def current() -> str:
    return _cur.get()


def use(lang: str):
    """Bu update üçün dili təyin et (middleware). Token qaytarır — reset üçün."""
    return _cur.set(lang if lang in LANGS else DEFAULT)


def reset(token):
    _cur.reset(token)


def _(text: str, lang: str | None = None, **kw) -> str:
    lang = lang or _cur.get()
    if lang != "az":
        text = STRINGS.get(lang, {}).get(text) or text
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def template(slot: str, lang: str | None = None) -> str | None:
    lang = lang or _cur.get()
    return TPL.get(lang, {}).get(slot)


def stats() -> dict:
    """Dil seçimi statistikası (admin paneli üçün)."""
    out = {k: 0 for k in LANGS}
    try:
        from common.database import get_db
        db = get_db()
        for k, v in list(getattr(db, "_settings", {}).items()):
            if k.startswith("lang:") and v in out:
                out[v] += 1
    except Exception:
        pass
    return out


# ═════════════════════════ dil faylları: yükləmə / yoxlama / ixrac ═════════════════════════
def _ph(text: str) -> set:
    return set(re.findall(r"\{(\w+)\}", text or ""))


def source_keys() -> list:
    """Tərcümə olunmalı bütün Azərbaycanca mətnlər (lang/*.json faylları üzrə açarların birləşməsi)."""
    keys: dict = {}
    for d in STRINGS.values():
        keys.update(dict.fromkeys(d))
    return list(keys)


def _validate(data, filename: str = "") -> tuple:
    """(kod, ad, telegram_kodları, strings, templates, skipped) — səhvdə ValueError."""
    if not isinstance(data, dict):
        raise ValueError("JSON obyekt olmalıdır")
    meta = data.get("_meta") or {}
    code = str(meta.get("code") or Path(filename).stem or "").lower().strip()
    if not CODE_RE.match(code):
        raise ValueError(f"dil kodu yanlışdır: '{code}' (məs. de, fr, pt-br)")
    if code == "az":
        raise ValueError("az — əsas dildir, faylla dəyişdirilmir")
    name = str(meta.get("name") or code).strip()[:40]
    tg = [str(x).lower() for x in (meta.get("telegram_codes") or [code]) if isinstance(x, str)]
    skipped = []
    strings = {}
    for k, v in (data.get("strings") or {}).items():
        if not isinstance(k, str) or not isinstance(v, str) or not v.strip():
            continue                                         # boş — tərcümə edilməyib (az göstərilir)
        if _ph(k) != _ph(v):
            skipped.append(f"yer tutucu uyğun deyil: {k[:50]!r} → {sorted(_ph(v))} (lazım: {sorted(_ph(k))})")
            continue
        strings[k] = v
    templates = {}
    for slot, v in (data.get("templates") or {}).items():
        if not isinstance(v, str) or not v.strip():
            continue
        bad = _ph(v) - TEMPLATE_VARS
        if bad:
            skipped.append(f"şablon '{slot}': naməlum yer tutucu {sorted(bad)}")
            continue
        templates[slot] = v
    return code, name, tg, strings, templates, skipped


def reload() -> dict:
    """lang/*.json faylları → STRINGS / TPL. Qaytarır: REPORT."""
    langs = {"az": "🇦🇿 Azərbaycanca"}
    strings, tpl, detect_map, report = {}, {}, dict(_BASE_DETECT), {}
    if LANG_DIR.is_dir():
        for f in sorted(LANG_DIR.glob("*.json")):
            try:
                code, name, tg, st, tp, skipped = _validate(json.loads(f.read_text(encoding="utf-8")), f.name)
            except Exception as e:
                report[f.stem] = {"file": f.name, "strings": 0, "templates": 0, "skipped": [], "error": str(e)[:200]}
                continue
            langs[code] = name
            strings[code] = st
            tpl[code] = tp
            for t in tg:
                detect_map[t] = code
            report[code] = {"file": f.name, "strings": len(strings[code]), "templates": len(tpl[code]),
                            "skipped": skipped, "error": ""}
    LANGS.clear()
    LANGS.update(langs)
    STRINGS.clear()
    STRINGS.update(strings)
    TPL.clear()
    TPL.update(tpl)
    _DETECT.clear()
    _DETECT.update(detect_map)
    REPORT.clear()
    REPORT.update(report)
    _cache.clear()
    return REPORT


def coverage(code: str) -> int:
    """Tərcümə faizi (mətnlər + şablonlar)."""
    if code == "az":
        return 100
    keys = source_keys()
    done = sum(1 for k in keys if k in STRINGS.get(code, {}))
    slots = 7
    done_t = min(slots, len(TPL.get(code, {})))
    return int(100 * (done + done_t) / max(1, len(keys) + slots))


def export_template(az_templates: dict = None) -> dict:
    """Tərcüməçi üçün boş fayl: bütün açarlar, dəyərlər boş (Azərbaycanca orijinal açarın özüdür)."""
    return {"_meta": {"code": "xx", "name": "🏳️ Language name", "telegram_codes": ["xx"], "version": 1,
                      "help": "code: ISO dil kodu (de, fr, pt-br) · telegram_codes: bu dili avtomatik seçəcək "
                              "Telegram dilləri · boş qalan sətir Azərbaycanca göstərilir · {yer_tutucular} "
                              "dəyişməməlidir"},
            "templates": {k: "" for k in (az_templates or {})} or {k: "" for k in TPL.get("en", {})},
            "templates_original_az": az_templates or {},
            "strings": {k: "" for k in source_keys()}}


def export_lang(code: str) -> dict:
    return {"_meta": {"code": code, "name": LANGS.get(code, code),
                      "telegram_codes": sorted(t for t, c in _DETECT.items() if c == code), "version": 1},
            "templates": dict(TPL.get(code, {})),
            "strings": {k: STRINGS.get(code, {}).get(k, "") for k in source_keys()}}


def install(raw: bytes, filename: str = "") -> tuple:
    """Göndərilən JSON faylı lang/<kod>.json kimi yaz, yenidən yüklə. → (kod, REPORT[kod])"""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        raise ValueError(f"JSON oxunmadı: {e}")
    code, *_rest = _validate(data, filename)
    LANG_DIR.mkdir(parents=True, exist_ok=True)
    data.setdefault("_meta", {})["code"] = code
    data.pop("templates_original_az", None)
    (LANG_DIR / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    reload()
    return code, REPORT.get(code, {})


def remove(code: str) -> bool:
    code = str(code or "").lower().strip()
    if not CODE_RE.match(code) or code == "az":      # callback-dən gəlir: "../x" kimi yollar silinməsin
        return False
    f = LANG_DIR / f"{code}.json"
    if not f.exists():
        return False
    f.unlink()
    reload()
    return True


def picker_title() -> str:
    """Dil seçimi başlığı — hər dildə (istifadəçi hələ dil seçməyib)."""
    seen, parts = set(), []
    for code in LANGS:
        t = _("🌐 Dil seçin:", code).replace("🌐", "").rstrip(":").strip()
        if t and t not in seen:
            seen.add(t)
            parts.append(t)
    return "🌐 " + " · ".join(parts)


reload()
