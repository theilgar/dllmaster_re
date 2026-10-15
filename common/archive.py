"""
📦 Vahid Kod Arxivi Sistemi — common/archive.py

Bütün layihənin (common, bot, userbot) seçilmiş hissələrinin .zip arxivini yaradır
və 📍 Hədəflər → 📦 GitHub arxivi (kanal / qrup / community mövzusu, /setarchive)
yerinə göndərilməsi üçün məlumatları təmin edir.

Nə vaxt — iki müstəqil tetikləyici:
  archive:on        — 🌿 GitHub push-dan sonra (update_notifier)
  archive:onchange  — 🔄 kod dəyişəndə, GitHub olmasa da: plugin menecerindən 📥 yeniləmə, git pull,
                      diskdə əl ilə redaktə — hər archive:interval dəqiqədən bir yoxlanılır (fingerprint)
Son göndərilən arxivin tərkibi archive:sent-də saxlanılır (fayl → sha1) → təkrar göndərilmir,
caption-da isə nə dəyişdiyi (🆕 / ✏️ / 🗑) yazılır.

Nələr daxil olsun — iki səviyyədə seçilir (settings):
  archive:items    — bölmələr (qovluqlar): common, bot/core, bot/plugins, userbot/core, userbot/plugins,
                     giriş faylları, requirements.txt, deps/, bot/assets, lang
  archive:exclude  — bölmənin içindən ayrı-ayrı fayllar (✅/❌)
Heç vaxt daxil olmur: config.env, *.session, userbot.string, data/, .venv, .git, __pycache__ — sirlər və bazalar.
"""
import hashlib
import io
import json
import time
import zipfile

from common import code_update as cu
from common.database import Settings, get_db
from common.kv import next_of

ROOT = cu.ROOT
SEND_LIMIT = 50 * 1024 * 1024

# açar: (ad, qovluq / fayl nümunəsi, default)
ITEMS = {
    "common":  ("🧩 common", "zone:common", True),
    "bcore":   ("⚙️ bot/core", "zone:bcore", True),
    "bplug":   ("🔌 bot/plugins", "zone:bplug", True),
    "ucore":   ("⚙️ userbot/core", "zone:ucore", True),
    "uplug":   ("📱 userbot/plugins", "zone:uplug", True),
    "entry":   ("🚀 giriş faylları", "zone:entry", True),
    "req":     ("📄 requirements.txt", "glob:requirements*.txt", True),
    "deps":    ("🐧 deps/ (sistem paketləri)", "glob:deps/*.txt", True),
    "assets":  ("🖼 bot/assets", "glob:bot/assets/*", False),
    "lang":    ("🌐 lang/ (dil faylları)", "glob:lang/*.json", True),
}
ORDER = list(ITEMS)
NEVER = ("config.env", ".env", ".session", "userbot.string", "/data/", "/.venv/", "/.git/", "__pycache__",
         "/download/", ".db", ".db-wal", ".db-shm")


_s = Settings().get


def enabled() -> bool:
    return _s("archive:on", "1") == "1"


def set_enabled(on: bool):
    get_db().set_setting("archive:on", "1" if on else "0")


def on_change() -> bool:
    return _s("archive:onchange", "0") == "1"


def set_on_change(on: bool):
    get_db().set_setting("archive:onchange", "1" if on else "0")


INTERVALS = (5, 10, 30, 60, 180)


def interval() -> int:
    try:
        v = int(_s("archive:interval", "10"))
    except ValueError:
        v = 10
    return v if v in INTERVALS else 10


def cycle_interval() -> int:
    nxt = next_of(INTERVALS, interval())
    get_db().set_setting("archive:interval", str(nxt))
    return nxt


# ───────────── 🔄 dəyişiklik izləmə ─────────────
def manifest() -> tuple:
    """({fayl: sha1}, ən son dəyişiklik vaxtı) — seçilmiş fayllar üzrə."""
    out, newest = {}, 0.0
    for rel in selected_files():
        p = ROOT / rel
        try:
            out[rel] = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            pass
    return out, newest


def sent_manifest():
    try:
        v = json.loads(_s("archive:sent") or "null")
    except ValueError:
        v = None
    return v if isinstance(v, dict) else None


def mark_sent(man: dict):
    get_db().set_setting("archive:sent", json.dumps(man, separators=(",", ":")))


def diff(old: dict, new: dict) -> dict:
    return {"new": sorted(set(new) - set(old)), "gone": sorted(set(old) - set(new)),
            "changed": sorted(r for r in new if r in old and new[r] != old[r])}


def items() -> list:
    try:
        v = json.loads(_s("archive:items") or "null")
    except ValueError:
        v = None
    if not isinstance(v, list):
        return [k for k in ORDER if ITEMS[k][2]]
    return [k for k in v if k in ITEMS]


def set_items(keys: list):
    get_db().set_setting("archive:items", json.dumps([k for k in keys if k in ITEMS]))


def excluded() -> set:
    try:
        return set(json.loads(_s("archive:exclude") or "[]"))
    except ValueError:
        return set()


def toggle_file(rel: str) -> bool:
    """True — fayl indi daxildir."""
    ex = excluded()
    if rel in ex:
        ex.discard(rel)
    else:
        ex.add(rel)
    get_db().set_setting("archive:exclude", json.dumps(sorted(ex)))
    return rel not in ex


def reset():
    for k in ("archive:items", "archive:exclude", "archive:interval"):
        get_db().set_setting(k, None)


def _safe(rel: str) -> bool:
    low = "/" + rel.lower()
    return not any(n in low or low.endswith(n) for n in NEVER)


def files_of(key: str) -> list:
    """Bölmənin faylları (layihə yolları, sıralı) — istisnalar NƏZƏRƏ ALINMADAN."""
    src = ITEMS[key][1]
    if src.startswith("zone:"):
        out = cu.zone_files(src[5:])
    else:
        pattern = src[5:]
        out = [str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.glob(pattern) if p.is_file()]
    return sorted(r for r in out if _safe(r))


def selected_files() -> list:
    ex = excluded()
    seen, out = set(), []
    for k in items():
        for r in files_of(k):
            if r not in ex and r not in seen:
                seen.add(r)
                out.append(r)
    return out


def build(info: dict = None) -> tuple:
    """(zip baytları, fayl siyahısı). info — commit / branch qeydi üçün."""
    files = selected_files()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(ROOT / rel, rel)
        info = info or {}
        z.writestr("ARCHIVE_INFO.txt",
                   f"DLLMaster kod arxivi · {time.strftime('%d.%m.%Y %H:%M')}\n"
                   + (f"commit {info.get('hash')} · branch {info.get('branch')}\n" if info.get("hash") else "")
                   + (f"{info.get('url')}\n" if info.get("url") else "")
                   + f"{len(files)} fayl · bölmələr: {', '.join(items())}\n\n"
                   "Yenidən quraşdırmaq: bu .zip-i bota göndər (📥 yeniləmə) — yollar layihədəki kimidir.\n")
    return buf.getvalue(), files


def summary() -> str:
    keys = items()
    ex = excluded()
    total = sum(len(files_of(k)) for k in keys)
    skipped = sum(1 for k in keys for r in files_of(k) if r in ex)
    return f"{len(keys)} bölmə · {total - skipped} fayl" + (f" ({skipped} fayl çıxarılıb)" if skipped else "")
