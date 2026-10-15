"""
📦 Kod yeniləmələri — common/code_update.py

Plugin meneceri (bot/plugins/plugin_manager_plugin.py) bunu işlədir: bir və ya bir neçə .py faylı,
yaxud .zip arxivi gəlir → hər fayl üçün yeri təyin olunur → yoxlanılır → yedəklənib yazılır → lazım
olsa geri qaytarılır. Faylları yükləmək (reload) plugin menecerinin işidir — burada yalnız fayllar.

Yerin təyini (ardıcıllıqla):
  1) zip-də yol varsa — o yol:  bot/core/x.py · userbot/plugins/y.py · common/z.py ...
     köhnə quruluşun yolları da başa düşülür:  core/x.py → yeni yeri, plugins/x.py → bot/plugins/,
     userbot_plugins/x.py → userbot/plugins/;  "DLMaster/bot/..." kimi üst qovluq atılır
  2) yalnız ad varsa — layihədə həmin adlı fayl harada varsa ora
     (bir neçə yerdə varsa → faylın başlığındakı yol ipucu, yoxsa 📍 seçim düymələri)
     Telegram-ın ad əlavələri atılır: "database (1).py" → database.py
  3) yeni fayldırsa — avtomatik (where_new):
       a) başlıqdakı yol ipucu:  "— common/x.py", "Fayl: bot/core/x.py", "bot/plugins/x.py" ...
       b) setup(context) → bot/plugins · register(ub) → userbot/plugins
       c) kim onu import edir — layihədəki və eyni paketdəki fayllar (from common import x, bot.core.x ...)
       d) öz importları — telethon / userbot.* → userbot/core · aiogram / bot.* → bot/core · qalanı → common
     Heç biri alınmasa — 📍 seçim düymələri (plugin menecerində)

Növlər:
  bot_plugin  🔌 — restartsız yenidən yüklənir
  ub_plugin   📱 — restartsız yenidən yüklənir (userbot işləyirsə)
  core        ⚙️ — common/, bot/core/, userbot/core/ → restart tələb olunur
  entry       🚀 — init.py, app.py, bot/main.py, userbot/main.py, migrate.py → restart tələb olunur

Hər tətbiqdən əvvəl köhnə fayllar data/update_backups/<tarix>/ altına yol ilə birlikdə saxlanılır
(manifest.json — yeni əlavə olunan faylları da qeyd edir), ⏪ geri qaytarma bundan istifadə edir.
"""
import ast
import logging
import hashlib
import io
import json
import os
import shutil
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from common.utilities import BASE_DIR

logger = logging.getLogger(__name__)

ROOT = Path(BASE_DIR)
BACKUPS = ROOT / "data" / "update_backups"
BACKUPS_KEEP = 10

ZONES = [                                   # (qovluq, növ)
    ("bot/plugins/disabled", "bot_plugin"),
    ("bot/plugins", "bot_plugin"),
    ("userbot/plugins", "ub_plugin"),
    ("bot/core", "core"),
    ("userbot/core", "core"),
    ("common", "core"),
]
ENTRY_FILES = {"init.py", "app.py", "bot/main.py", "userbot/main.py", "userbot/login.py"}
RESTART_PLUGINS = {"bot/plugins/plugin_manager_plugin.py"}     # özünü canlı yeniləməsin
KIND_ICON = {"bot_plugin": "🔌", "ub_plugin": "📱", "core": "⚙️", "entry": "🚀", "lang": "🌐"}

MAX_FILES = 300
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024

# köhnə core/<ad>.py → yeni yer
_OLD_CORE = {**{n: "common" for n in ("bootstrap", "logger", "utilities", "database", "updates")},
             **{n: "bot/core" for n in ("audio_cache", "depo_helpers", "stars", "welcome", "cpu_pool", "ytdl_worker",
                                         "youtube_handler", "webprofile", "spotify_handler", "song_names", "links")},
             **{n: "userbot/core" for n in ("userbot_api", "userbot_db", "tg_session", "tg_login")}}
_OLD_RENAMES = {"plugins/telethon_plugin.py": "userbot/core/accounts.py",
                "plugins/userbot_tools_plugin.py": "userbot/core/host.py",
                "login.py": "userbot/login.py"}


@dataclass
class Item:
    name: str                       # göndərilən ad / zip-dəki yol
    data: bytes
    rel: str = ""                   # layihədə yol (bot/core/x.py); boşdursa təyin olunmayıb
    kind: str = ""
    state: str = ""                 # new | changed | same
    error: str = ""
    notes: list = field(default_factory=list)
    choices: list = field(default_factory=list)   # 📍 yer təyin olunmayanda: mümkün layihə yolları
    where: str = ""                 # yeni faylın yeri necə tapıldı (planda göstərilir)

    def reset(self):
        self.rel = self.kind = self.state = self.error = self.where = ""
        self.notes, self.choices = [], []

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def hot(self) -> bool:
        if self.kind == "lang":
            return True                                 # 🌐 i18n.reload() — restart yoxdur
        return (self.kind in ("bot_plugin", "ub_plugin") and self.rel not in RESTART_PLUGINS
                and PurePosixPath(self.rel).stem != "__init__")

    @property
    def disabled(self) -> bool:
        return self.rel.startswith("bot/plugins/disabled/")


# ───────────────────────── yolun təyini ─────────────────────────
def kind_of(rel: str) -> str:
    if rel in ENTRY_FILES:
        return "entry"
    if rel.startswith("lang/") and rel.endswith(".json") and rel.count("/") == 1:
        return "lang"                                   # 🌐 dil faylı — restart lazım deyil
    for zone, kind in ZONES:
        parent = str(PurePosixPath(rel).parent)
        if parent == zone:
            return kind
    return ""


def _all_known() -> dict:
    """fayl adı → [layihədəki yollar]"""
    out = {}
    for zone, _ in ZONES:
        d = ROOT / zone
        if d.is_dir():
            for p in d.glob("*.py"):
                out.setdefault(p.name, []).append(f"{zone}/{p.name}")
    for rel in ENTRY_FILES:
        if (ROOT / rel).exists():
            out.setdefault(PurePosixPath(rel).name, []).append(rel)
    return out


def _map_path(path: str):
    """zip-dəki yol → layihə yolu və ya None."""
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None
    parts = list(p.parts)
    if parts and parts[-1].endswith(".json"):           # 🌐 .../lang/de.json → lang/de.json
        return f"lang/{parts[-1]}" if len(parts) >= 2 and parts[-2] == "lang" else None
    for _ in range(2):                                  # "DLMaster/bot/core/x.py" → "bot/core/x.py"
        rel = "/".join(parts)
        if rel in _OLD_RENAMES:
            return _OLD_RENAMES[rel]
        if len(parts) == 2 and parts[0] == "core" and parts[1][:-3] in _OLD_CORE:
            return f"{_OLD_CORE[parts[1][:-3]]}/{parts[1]}"
        if len(parts) == 2 and parts[0] == "plugins":
            return f"bot/plugins/{parts[1]}"
        if len(parts) == 2 and parts[0] == "userbot_plugins":
            return f"userbot/plugins/{parts[1]}"
        if kind_of(rel):
            return rel
        if len(parts) > 1:
            parts = parts[1:]
    return None


_DUP_SUFFIX = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|-copy|_copy| copy)$", re.I)


def clean_name(name: str) -> str:
    """Telegram / brauzer əlavələri: "database (1).py", "x copy.py" → "database.py", "x.py"."""
    p = PurePosixPath(name.replace("\\", "/"))
    stem = _DUP_SUFFIX.sub("", p.stem).strip() or p.stem
    return str(p.with_name(stem + p.suffix)) if "/" in name.replace("\\", "/") else stem + p.suffix


CORE_ZONES = ("common", "bot/core", "userbot/core")
_HINT_RE = re.compile(r"\b((?:bot|userbot)/(?:core|plugins)|common)/([A-Za-z_][\w]*)\.py\b")


def _hint_zone(src: str, stem: str):
    """Faylın başlığında (ilk ~1500 simvol) öz yolu yazılıbsa: "— common/database.py" → common."""
    for m in _HINT_RE.finditer(src[:1500]):
        if m.group(2) == stem:
            return m.group(1)
    return None


def _import_refs(src: str, stem: str) -> set:
    """Bu mənbə `stem` modulunu hansı zonadan import edir."""
    out = set()
    for zone in CORE_ZONES:
        pkg = zone.replace("/", ".")
        if re.search(rf"(?:from|import)\s+{re.escape(pkg)}\.{stem}\b", src) or \
                re.search(rf"from\s+{re.escape(pkg)}\s+import\s+[^\n]*\b{stem}\b", src):
            out.add(zone)
    return out


def _project_sources():
    for zone, _ in ZONES:
        d = ROOT / zone
        if d.is_dir():
            for p in d.glob("*.py"):
                yield p
    for rel in ENTRY_FILES:
        if (ROOT / rel).exists():
            yield ROOT / rel


def where_new(stem: str, src: str, tree, batch_srcs: dict) -> tuple:
    """Yeni nüvə faylının yeri → (zona, səbəb) və ya (None, None)."""
    zone = _hint_zone(src, stem)
    if zone:
        return zone, "faylın başlığındakı yol"
    votes = {}
    for other in batch_srcs.values():                   # eyni paketdəki fayllar
        for z in _import_refs(other, stem):
            votes[z] = votes.get(z, 0) + 1
    for p in _project_sources():                        # layihədəki fayllar
        try:
            for z in _import_refs(p.read_text(encoding="utf-8", errors="ignore"), stem):
                votes[z] = votes.get(z, 0) + 1
        except OSError:
            pass
    if votes:
        best = max(votes.values())
        top = [z for z, n in votes.items() if n == best]
        if len(top) == 1:
            return top[0], f"{best} fayl onu {top[0].replace('/', '.')}-dan import edir"
    mods = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
            for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])}
    if mods & {"telethon", "userbot"}:
        return "userbot/core", "telethon / userbot importları"
    if mods & {"aiogram", "bot"}:
        return "bot/core", "aiogram / bot importları"
    return "common", "yalnız ümumi importlar"


def _defs(tree) -> set:
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def resolve(item: Item, known: dict):
    if item.name.lower().endswith(".json") and "/" not in item.name.replace("\\", "/"):
        item.rel, item.kind = f"lang/{item.name}", "lang"         # tək göndərilmiş de.json
        return
    if "/" in item.name.replace("\\", "/"):
        rel = _map_path(item.name)
        if not rel:
            item.error = "yol tanınmadı (bot/, userbot/, common/ və ya kök faylları olmalıdır)"
            return
        item.rel = rel
    else:
        mapped = _OLD_RENAMES.get(f"plugins/{item.name}") if item.name in ("telethon_plugin.py",
                                                                           "userbot_tools_plugin.py") else None
        places = known.get(item.name, [])
        active = [p for p in places if not p.startswith("bot/plugins/disabled/")]
        if mapped:
            item.rel = mapped
        elif len(active) == 1 or (len(places) == 1):
            item.rel = (active or places)[0]
        elif len(active) > 1:
            try:                                        # başlıqdakı yol ipucu ilə seçilir
                zone = _hint_zone(item.data[:3000].decode("utf-8", "ignore"), PurePosixPath(item.name).stem)
            except Exception:
                zone = None
            hit = f"{zone}/{item.name}" if zone else None
            if hit in active:
                item.rel = hit
                item.where = "faylın başlığındakı yol"
            else:
                item.choices = active
                item.error = "bu ad bir neçə yerdə var — 📍 yerini seç"
                return
        else:
            item.rel = ""                               # yeni fayl — məzmuna görə
    item.kind = kind_of(item.rel) if item.rel else ""


def check(item: Item, batch_srcs: dict = None):
    if item.error:
        return
    if len(item.data) > MAX_FILE:
        item.error = f"çox böyükdür ({len(item.data) // 1024} KB)"
        return
    try:
        src = item.data.decode("utf-8")
    except UnicodeDecodeError:
        item.error = "UTF-8 mətn deyil"
        return
    if item.kind == "lang":                             # 🌐 JSON + dil faylı yoxlaması (common/i18n.py)
        try:
            from common import i18n
            code, *_rest = i18n._validate(json.loads(src.lstrip("\ufeff")), item.rel)
            item.rel = f"lang/{code}.json"
            item.notes.append(f"🌐 dil: {code}")
        except Exception as e:
            item.error = f"dil faylı: {str(e)[:150]}"
            return
        target = ROOT / item.rel
        item.state = ("same" if target.exists() and target.read_bytes() == item.data
                      else "changed" if target.exists() else "new")
        return
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        item.error = f"SyntaxError: {e.msg} (sətir {e.lineno})"
        return
    defs = _defs(tree)
    if not item.rel:                                    # yeni fayl: yerini məzmun deyir
        stem = PurePosixPath(item.name).stem
        hint = _hint_zone(src, stem)
        if hint:
            item.rel, item.where = f"{hint}/{item.name}", "faylın başlığındakı yol"
        elif "register" in defs and "setup" not in defs:
            item.rel, item.where = f"userbot/plugins/{item.name}", "register(ub) — userbot plugini"
        elif "setup" in defs:
            item.rel, item.where = f"bot/plugins/{item.name}", "setup(context) — bot plugini"
        else:
            others = {k: v for k, v in (batch_srcs or {}).items() if k != item.name}
            zone, why = where_new(stem, src, tree, others)
            if not zone:
                item.choices = [f"{z}/{item.name}" for z in CORE_ZONES]
                item.error = "yeni faylın yeri bilinmir — 📍 yerini seç"
                return
            item.rel, item.where = f"{zone}/{item.name}", why
            item.choices = [f"{z}/{item.name}" for z in CORE_ZONES if z != zone]   # 📍 dəyişmək üçün
        item.kind = kind_of(item.rel)
    stem = PurePosixPath(item.rel).stem
    if item.kind == "bot_plugin" and stem != "__init__" and "setup" not in defs:
        item.error = ("setup(context) yoxdur" + (" — bu userbot pluginidir, yolu userbot/plugins/ olmalıdır"
                                                 if "register" in defs else ""))
    elif item.kind == "ub_plugin" and stem != "__init__" and "register" not in defs:
        item.error = ("register(ub) yoxdur" + (" — bu bot pluginidir, yolu bot/plugins/ olmalıdır"
                                               if "setup" in defs else ""))
    if item.error:
        return
    target = ROOT / item.rel
    if target.exists():
        item.state = "same" if hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(item.data).digest() \
            else "changed"
    else:
        item.state = "new"


# ───────────────────────── paket ─────────────────────────
def from_zip(data: bytes) -> list:
    items = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename
            is_lang = name.endswith(".json") and "lang/" in "/" + name.replace("\\", "/")
            if info.is_dir() or "__pycache__" in name or "/." in "/" + name or not (name.endswith(".py") or is_lang):
                continue
            if len(items) >= MAX_FILES:
                raise ValueError(f"arxivdə {MAX_FILES}-dən çox fayl var")
            total += info.file_size
            if total > MAX_TOTAL:
                raise ValueError("arxiv açılmış halda 20 MB-dan böyükdür")
            items.append(Item(name=name, data=z.read(info)))
    return items


def plan(items: list) -> list:
    """Hər faylın yerini tapır və yoxlayır. Eyni fayl iki dəfə gəlibsə — SONUNCU qalır."""
    known = _all_known()
    batch_srcs = {}
    for it in items:
        it.reset()
        if not getattr(it, "_cleaned", False):
            it.name = clean_name(it.name)
            it._cleaned = True
        if it.name.endswith(".py"):
            batch_srcs[it.name] = it.data.decode("utf-8", "ignore")
    for it in items:
        if getattr(it, "forced", ""):                     # 📍 əl ilə seçilmiş yer
            it.name = it.forced
        resolve(it, known)
        check(it, batch_srcs)
    last = {}
    for i, it in enumerate(items):
        if it.rel:
            last[it.rel] = i
    return [it for i, it in enumerate(items) if not it.rel or last[it.rel] == i]


def needs_restart(items) -> bool:
    return any(it.ok and it.state != "same" and not it.hot for it in items)


# ───────────────────────── tətbiq / geri qaytarma ─────────────────────────
def apply(items: list) -> str:
    """Xətasız və dəyişən faylları yazır. Qaytarır: yedək qovluğunun adı."""
    todo = [it for it in items if it.ok and it.state in ("new", "changed")]
    base = time.strftime("%Y%m%d_%H%M%S")
    tag, n = base, 1
    while (BACKUPS / tag).exists():                     # eyni saniyədə ikinci yeniləmə birincinin yedəyini silməsin
        n += 1
        tag = f"{base}_{n}"
    bdir = BACKUPS / tag
    bdir.mkdir(parents=True, exist_ok=False)
    manifest = {"created": time.time(), "files": []}
    for it in todo:
        target = ROOT / it.rel
        existed = target.exists()
        if existed:
            dst = bdir / it.rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dst)
        manifest["files"].append({"rel": it.rel, "existed": existed, "kind": it.kind})
    (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        for it in todo:
            target = ROOT / it.rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.upd")
            tmp.write_bytes(it.data)
            os.replace(tmp, target)
    except Exception:
        rollback(tag)                                   # yarımçıq yeniləmə qalmasın (disk dolu, icazə ...)
        raise
    if any(it.kind == "lang" for it in todo):           # 🌐 yeni / dəyişən dil faylı — dərhal aktiv
        try:
            from common import i18n
            i18n.reload()
        except Exception as _e:
            logger.debug("udulan xəta [common/code_update.py:421]: %r", _e)
    _prune()
    return tag


def restore_one(tag: str, rel: str):
    """Bir faylı yedəkdən qaytar (yeni idisə sil) — yüklənməyən plugin üçün."""
    bdir = BACKUPS / tag
    src = bdir / rel
    target = ROOT / rel
    if src.exists():
        shutil.copy2(src, target)
    else:
        target.unlink(missing_ok=True)


def rollback(tag: str) -> list:
    """Bütün yeniləməni geri qaytarır. Qaytarır: manifest-dəki fayllar [{rel, existed, kind}]."""
    bdir = BACKUPS / tag
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        restore_one(tag, f["rel"])
    manifest["rolled_back"] = time.time()
    (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest["files"]


def history(limit: int = 5) -> list:
    """[(tag, manifest)] — ən yenisi əvvəl."""
    if not BACKUPS.is_dir():
        return []
    out = []
    for d in sorted(BACKUPS.iterdir(), reverse=True):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                out.append((d.name, json.loads(m.read_text(encoding="utf-8"))))
            except ValueError:
                continue
        if len(out) >= limit:
            break
    return out


def _prune():
    dirs = sorted((d for d in BACKUPS.iterdir() if d.is_dir()), reverse=True) if BACKUPS.is_dir() else []
    for d in dirs[BACKUPS_KEEP:]:
        shutil.rmtree(d, ignore_errors=True)


# ───────────────────────── 📤 export ─────────────────────────
# (açar, ad, qovluq | None = giriş faylları) — zip-də yollar layihədəki kimi qalır, ona görə export edilmiş
# arxiv birbaşa yenidən import (📥 yeniləmə) oluna bilər
EXPORT_ZONES = [
    ("common", "🧩 common", "common"),
    ("bcore", "⚙️ bot/core", "bot/core"),
    ("bplug", "🔌 bot/plugins", "bot/plugins"),
    ("ucore", "⚙️ userbot/core", "userbot/core"),
    ("uplug", "📱 userbot/plugins", "userbot/plugins"),
    ("entry", "🚀 giriş faylları", None),
]


def zone_files(key: str) -> list:
    """Zonadakı .py faylları (layihə yolları, sıralı). bot/plugins → söndürülmüşlər də."""
    zone = next((z for z in EXPORT_ZONES if z[0] == key), None)
    if zone is None:
        return []
    if zone[2] is None:
        return sorted(rel for rel in ENTRY_FILES if (ROOT / rel).is_file())
    d = ROOT / zone[2]
    out = [f"{zone[2]}/{p.name}" for p in d.glob("*.py")] if d.is_dir() else []
    if key == "bplug" and (d / "disabled").is_dir():
        out += [f"{zone[2]}/disabled/{p.name}" for p in (d / "disabled").glob("*.py")]
    return sorted(out)


def export_zip(keys: list) -> tuple:
    """(zip baytları, fayl sayı, ölçü). Sessiya, baza, config.env heç vaxt düşmür — yalnız .py."""
    files = []
    for k in keys:
        files += zone_files(k)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(ROOT / rel, rel)
        z.writestr("EXPORT_INFO.txt",
                   f"DLLMaster kod exportu · {time.strftime('%d.%m.%Y %H:%M')}\n"
                   f"{len(files)} fayl: {', '.join(k for k in keys)}\n\n"
                   "Yenidən quraşdırmaq: bu .zip-i bota göndər (📥 yeniləmə) — yollar layihədəki kimidir.\n")
    data = buf.getvalue()
    return data, len(files), len(data)
