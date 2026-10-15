"""
💾 Baza yedəkləmə mühərriki — common/backup.py

Bot (bot/plugins/backup_plugin.py) və userbot (userbot/plugins/backup.py) plugin-ləri bunu işlədir.
Hər biri öz bazasını, öz ayarlarını (interval, hədəf kanal, lokal nüsxə sayı) saxlayır.

  snapshot(kind, conn, lock)   — canlı bazanın ardıcıl surəti:
                                   🤖 bot     → PostgreSQL, pg_dump -Fc → data/backups/bot_<tarix>.dump
                                                (conn = common.database.Database, lock = None)
                                   📱 userbot → SQLite backup API → data/backups/userbot_<tarix>.db.gz
  inspect(path)                — gələn faylı yoxlayır: .gz açır, PostgreSQL dump-dırmı / SQLite-dırmı,
                                 cədvəllər və sətir sayları, hansı bazadır (bot / userbot)
  restore(kind, conn, lock, …) — əvvəl avtomatik "pre_restore" yedəyi, sonra bərpa:
                                   bot → pg_restore --clean --single-transaction (ya hamısı, ya heç nə)
                                   userbot → faylı CANLI SQLite bağlantısının içinə köçürür
  prune / local_list           — lokal nüsxələrin rotasiyası

Sessiya faylları (*.session, userbot.string) və config.env HEÇ VAXT yedəyə düşmür — yalnız SQLite bazası.
Bütün ağır işlər sinxrondur — çağıran asyncio.to_thread ilə işlətməlidir.
"""
import asyncio
import contextlib
import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from common.formatting import fmt_duration_words, format_bytes

logger = logging.getLogger(__name__)


BACKUP_DIR = Path(os.getenv("BACKUP_DIR", os.path.join("data", "backups")))
PRE_TAG = "pre_restore"
PRE_KEEP = 3                                   # bərpadan əvvəlki avtomatik yedəklərdən neçəsi qalsın

# avto-yedək intervalları (dəqiqə); 0 = söndürülüb
INTERVALS = [0, 60, 180, 360, 720, 1440, 4320, 10080]
KEEP_STEPS = [1, 3, 5, 7, 10, 14, 30]

KINDS = {
    "bot": {"title": "🤖 Bot bazası", "tables": {"users", "settings", "audio_cache"}},
    "userbot": {"title": "📱 Userbot bazası", "tables": {"kv", "records", "log"}},
}

MAX_UNPACKED = int(os.getenv("BACKUP_MAX_UNPACKED", str(4 << 30)))   # .gz açılanda maksimum ölçü (4 GB)
SQLITE_MAGIC = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"
PGDUMP_MAGIC = b"PGDMP"

PG_KIND = "bot"                                # PostgreSQL-də olan baza
EXT = {"bot": ".dump", "userbot": ".db.gz"}    # yedək faylının uzantısı (növə görə)


# ───────────────────────── PostgreSQL köməkçiləri ─────────────────────────
def _pg_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} tapılmadı — PostgreSQL client alətləri lazımdır (Arch: pacman -S postgresql)")
    return path


def _pg_env(dsn: str) -> dict:
    """DSN → PG* mühit dəyişənləri (parol komanda sətrinə / ps çıxışına düşməsin)."""
    from psycopg.conninfo import conninfo_to_dict
    p = conninfo_to_dict(dsn)
    env = dict(os.environ)
    for key, var in (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"),
                     ("password", "PGPASSWORD"), ("dbname", "PGDATABASE")):
        if p.get(key):
            env[var] = str(p[key])
    return env


def _run(cmd: list, env: dict, timeout: int = 1800):
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(f"{os.path.basename(cmd[0])} xətası: {' | '.join(err[-3:])[:400]}")
    return r


def pg_table_counts(db) -> dict:
    """Canlı PostgreSQL bazasında public sxemindəki cədvəllər və sətir sayları."""
    names = [r[0] for r in db.sql_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")]
    with db.sql() as cur:
        return {n: cur.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def pg_info(db) -> tuple:
    """(bazanın adı, ölçüsü baytla)"""
    row = db.sql_one("SELECT current_database(), pg_database_size(current_database())")
    return row[0], int(row[1] or 0)


def _dump_counts(dump_path) -> dict:
    """pg_dump faylındakı hər cədvəlin sətir sayı — bazaya toxunmadan, COPY bloklarını sayaraq.
    (COPY mətn formatında sətrin içindəki yeni sətir \\n kimi yazılır — hər sətir = bir qeyd)"""
    # stderr temp fayla: PIPE olsaydı, pg_restore >64 KB xəbərdarlıq yazanda hər iki tərəf əbədi bloklanırdı
    err_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([_pg_bin("pg_restore"), "--data-only", "-f", "-", str(dump_path)],
                            stdout=subprocess.PIPE, stderr=err_f, text=True, errors="replace")
    counts, table = {}, None
    for line in proc.stdout:
        if table is None:
            if line.startswith("COPY "):
                name = line[5:].split(" ", 1)[0]
                table = name.split(".")[-1].strip('"')
                counts[table] = 0
        elif line.startswith("\\."):
            table = None
        else:
            counts[table] += 1
    rc = proc.wait()
    err_f.seek(0)
    err = err_f.read()
    err_f.close()
    if rc != 0:
        raise RuntimeError(f"pg_restore faylı oxuya bilmədi: {err.strip()[-300:]}")
    return counts


def _pg_snapshot(kind: str, db, tag: str = "") -> "Snapshot":
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = BACKUP_DIR / f"{kind}_{ts}{('_' + tag) if tag else ''}{EXT[kind]}"
    tmp = BACKUP_DIR / f".{kind}_{ts}_{os.getpid()}.dump.tmp"
    try:
        tables = pg_table_counts(db)
        _, raw_size = pg_info(db)
        # pg_dump öz ardıcıl snapshot-unu götürür — bot işləməyə davam edir, heç nə kilidlənmir
        _run([_pg_bin("pg_dump"), "--format=custom", "--compress=6", "--no-owner", "--no-acl",
              "--file", str(tmp)], _pg_env(db.dsn))
        tmp.replace(out_path)
    finally:
        tmp.unlink(missing_ok=True)
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return Snapshot(out_path, kind, out_path.stat().st_size, raw_size, _sha256(out_path), tables)


# ───────────────────────── formatlama ─────────────────────────
def iv_label(minutes: int) -> str:
    m = int(minutes or 0)
    return fmt_duration_words(m * 60) if m > 0 else "söndürülüb"


def next_due(last_ts: float | int | None, interval_min: int, now: float | None = None) -> float | None:
    """Növbəti avtomatik yedəkləmə vaxtı (UNIX timestamp).
    interval_min <= 0 olduqda None qaytarır (söndürülüb).
    last_ts boşdursa (əvvəl heç vaxt yedək alınmayıbsa), indi (now) icra olunmalıdır.
    """
    iv = int(interval_min or 0)
    if iv <= 0:
        return None
    last = int(last_ts or 0)
    cur = time.time() if now is None else float(now)
    return (last + iv * 60) if last else cur


async def auto_loop(run_backup, due, *, delay: float, label: str, alive=None, check_every: float = 60, log=None):
    """⏱ Avto-yedək dövrü (bot və userbot plugin-ləri): delay saniyə gözlə, sonra hər check_every saniyədə
    due() keçibsə run_backup(manual=False). alive() False olanda dövr bitir (məs. plugin reload / silinib)."""
    log = log or logger
    await asyncio.sleep(delay)
    while alive is None or alive():
        try:
            nxt = due()
            if nxt is not None and time.time() >= nxt:
                await run_backup(manual=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"💾 {label}: {e}", exc_info=True)
        await asyncio.sleep(check_every)


def restart_task(old, make_coro):
    """Köhnə avto-yedək tapşırığını dayandır və yenisini başlat. İşləyən event loop yoxdursa — None
    (korutin yaradılmır; çağıran sonra başlada bilər)."""
    if old and not old.done():
        old.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(make_coro())


def human(n) -> str:
    return format_bytes(n, digits=1, space=True)



def step(values: list, cur: int, direction: int) -> int:
    """Siyahıda növbəti / əvvəlki dəyər (cari dəyər siyahıda yoxdursa ən yaxınından)."""
    if cur in values:
        i = values.index(cur) + direction
    else:
        i = sum(1 for v in values if v < cur) - (1 if direction < 0 else 0)
    return values[max(0, min(len(values) - 1, i))]


def file_size(path: str) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(str(path) + suffix)
        except OSError:
            pass
    return total


# ───────────────────────── yedək ─────────────────────────
@dataclass
class Snapshot:
    path: Path
    kind: str
    size: int                    # .gz ölçüsü
    raw_size: int                # açılmış baza
    sha256: str
    tables: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)

    @property
    def name(self) -> str:
        return self.path.name


def table_counts(db_path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(kind: str, conn, lock, tag: str = "") -> Snapshot:
    if kind == PG_KIND:
        return _pg_snapshot(kind, conn, tag)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    raw = BACKUP_DIR / f".{kind}_{ts}_{os.getpid()}.db.tmp"
    out_path = BACKUP_DIR / f"{kind}_{ts}{('_' + tag) if tag else ''}.db.gz"
    try:
        path = None
        with contextlib.suppress(Exception):
            with lock:                                   # ani: yalnız fayl yolunu soruşuruq
                row = conn.execute("PRAGMA database_list").fetchone()
                path = row[2] if row and row[2] else None
        dst = sqlite3.connect(str(raw))
        try:
            if path:
                # ⚡ AYRI bağlantı ilə, qlobal kilid TUTULMADAN: WAL rejimində oxuma yazanları gözlətmir,
                # hissə-hissə (1024 səhifə) köçürülür — böyük bazada da bot donmur
                src = sqlite3.connect(path, timeout=30)
                try:
                    src.backup(dst, pages=1024, sleep=0.002)
                finally:
                    src.close()
            else:
                with lock:
                    conn.backup(dst)
        finally:
            dst.close()
        tables = table_counts(raw)
        raw_size = raw.stat().st_size
        with open(raw, "rb") as src, gzip.open(out_path, "wb", compresslevel=6) as gz:
            shutil.copyfileobj(src, gz, 1 << 20)
    finally:
        _drop_tmp(raw)
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return Snapshot(out_path, kind, out_path.stat().st_size, raw_size, _sha256(out_path), tables)


def local_list(kind: str, include_pre: bool = True) -> list:
    """Ən yenisi əvvəl."""
    if not BACKUP_DIR.is_dir():
        return []
    files = [p for p in BACKUP_DIR.glob(f"{kind}_*{EXT.get(kind, '.db.gz')}")
             if include_pre or PRE_TAG not in p.name]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _drop_tmp(raw: Path):
    """Müvəqqəti SQLite surətini yanındakı -wal / -shm ilə birlikdə sil: snapshot WAL rejimindədir və
    table_counts() onu mode=ro ilə açır — belə bağlantı -shm/-wal yaradır, amma bağlananda silə bilmir."""
    for f in (raw, raw.with_name(raw.name + "-wal"), raw.with_name(raw.name + "-shm")):
        f.unlink(missing_ok=True)


STALE_TMP = 3600                     # bu qədər köhnə müvəqqəti fayl yarımçıq / unudulmuş sayılır


def _prune_stale_tmp(kind: str) -> int:
    """Əvvəlki versiyanın qoyduğu (və ya yarıda kəsilən) .<kind>_*.tmp* fayllarını sil."""
    if not BACKUP_DIR.is_dir():
        return 0
    removed, cutoff = 0, time.time() - STALE_TMP
    for f in BACKUP_DIR.glob(f".{kind}_*.tmp*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def prune(kind: str, keep: int) -> int:
    removed = _prune_stale_tmp(kind)
    regular = [p for p in local_list(kind) if PRE_TAG not in p.name]
    pre = [p for p in local_list(kind) if PRE_TAG in p.name]
    for p in regular[max(1, keep):] + pre[PRE_KEEP:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def caption(snap: Snapshot, note: str = "", limit: int = 6) -> str:
    """Kanal postu üçün HTML başlıq (Telegram limiti 1024 simvol)."""
    from html import escape
    top = sorted(snap.tables.items(), key=lambda kv: -kv[1])[:limit]
    rows = " · ".join(f"{escape(k)}: {v}" for k, v in top)
    text = (f"<b>{KINDS[snap.kind]['title']}</b> — 💾 yedək · "
            f"{time.strftime('%d.%m.%Y %H:%M', time.localtime(snap.created))}\n"
            f"📦 {human(snap.size)} <i>(açılmış {human(snap.raw_size)})</i>\n"
            f"📊 {rows}\n"
            f"🔐 <code>{snap.sha256[:16]}</code>\n"
            + (f"{escape(note)}\n" if note else "")
            + f"#backup #{snap.kind}")
    return text[:1024]


# ───────────────────────── import / bərpa ─────────────────────────
def inspect(path) -> dict:
    """Gələn yedək faylını yoxlayır. Qaytarır:
    {ok, kind, tables, error, db_path (müvəqqəti açılmış fayl — cleanup() ilə silinir), raw_size}"""
    path = Path(path)
    res = {"ok": False, "kind": None, "tables": {}, "error": "", "db_path": None, "raw_size": 0}
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        res["error"] = f"fayl oxunmadı: {e}"
        return res

    db_path = path
    if head[:2] == GZIP_MAGIC:
        db_path = path.with_name(path.name + ".unpacked.db")
        try:
            limit = MAX_UNPACKED
            with gzip.open(path, "rb") as gz, open(db_path, "wb") as out:
                while chunk := gz.read(1 << 20):          # gzip bomb diski doldurmasın
                    limit -= len(chunk)
                    if limit < 0:
                        raise OSError(f"açılmış ölçü {format_bytes(MAX_UNPACKED)}-dan böyükdür")
                    out.write(chunk)
        except (OSError, EOFError) as e:
            db_path.unlink(missing_ok=True)
            res["error"] = f".gz açılmadı: {e}"
            return res
        with open(db_path, "rb") as f:
            head = f.read(16)
    res["db_path"] = str(db_path)

    if head[:5] == PGDUMP_MAGIC:                # 🤖 PostgreSQL yedəyi (pg_dump -Fc)
        res["raw_size"] = os.path.getsize(db_path)
        try:
            res["tables"] = _dump_counts(db_path)
        except Exception as e:
            res["error"] = f"dump oxunmadı: {e}"
            return res
        if not KINDS[PG_KIND]["tables"] <= set(res["tables"]):
            res["error"] = "PostgreSQL dump-dır, amma bot cədvəlləri tapılmadı"
            return res
        res["kind"], res["ok"] = PG_KIND, True
        return res

    if head != SQLITE_MAGIC:
        res["error"] = "tanınmadı — bot üçün .dump, userbot üçün .db / .db.gz yedəyi göndər"
        return res
    res["raw_size"] = os.path.getsize(db_path)
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            check = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if check != "ok":
            res["error"] = f"baza zədəlidir: {check}"
            return res
        res["tables"] = table_counts(db_path)
    except sqlite3.Error as e:
        res["error"] = f"SQLite xətası: {e}"
        return res

    names = set(res["tables"])
    for kind, meta in KINDS.items():
        if meta["tables"] <= names:
            res["kind"] = kind
            break
    if res["kind"] is None:
        res["error"] = "naməlum baza — nə bot, nə userbot cədvəlləri tapıldı"
        return res
    if res["kind"] == PG_KIND:
        res["error"] = ("bu, PostgreSQL-ə keçiddən əvvəlki köhnə SQLite bot yedəyidir — birbaşa bərpa olunmur. "
                        "Lazımdırsa məlumatları əl ilə PostgreSQL-ə köçür")
        return res
    res["ok"] = True
    return res


def cleanup(*paths):
    for p in paths:
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def restore(kind: str, conn, lock, db_path: str) -> Snapshot:
    """Əvvəl cari bazanın "pre_restore" yedəyi, sonra db_path bazaya bərpa olunur.
    Qaytarır: pre_restore yedəyi (geri qaytarmaq üçün)."""
    pre = snapshot(kind, conn, lock, tag=PRE_TAG)
    if kind == PG_KIND:
        env = _pg_env(conn.dsn)
        env["PGOPTIONS"] = "-c lock_timeout=60s"     # aktiv sorğu cədvəli tutubsa sonsuz gözləməsin
        # --single-transaction: xəta olsa heç nə dəyişmir; --clean: dump-dakı cədvəllər yenidən yaradılır
        _run([_pg_bin("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-acl",
              "--single-transaction", "--exit-on-error", "-d", env.get("PGDATABASE") or conn.dsn,
              str(db_path)], env)
        if hasattr(conn, "reload_caches"):            # ayar / premium keşi köhnə dəyərləri saxlamasın
            conn.reload_caches()
        return pre
    with lock:
        conn.commit()
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            src.backup(conn)
        finally:
            src.close()
        conn.commit()
    return pre


def compare_line(before: dict, after: dict, limit: int = 6) -> list:
    """[(cədvəl, indiki, yedəkdəki)] — ən böyük fərqlər əvvəl."""
    keys = set(before) | set(after)
    rows = [(k, before.get(k, 0), after.get(k, 0)) for k in keys]
    rows.sort(key=lambda r: -max(r[1], r[2]))
    return rows[:limit]
