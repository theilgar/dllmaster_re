#!/usr/bin/env bash
# 📦 DLLMaster — köçürmə paketi (EVDƏ işlədilir): kod + config.env + sessiyalar + Chrome profilləri + PostgreSQL dump.
#
#   bash deploy/pack_home.sh                 — ~/dlm_migrate_<tarix>.tar.gz yaradır (bot DAYANMIŞ olmalıdır)
#   bash deploy/pack_home.sh --dry-run       — heç nə yazmır, paketə nə düşəcəyini göstərir
#   bash deploy/pack_home.sh --with-backups  — data/backups (yedəklər) də daxil edilir (+~1 GB)
#   bash deploy/pack_home.sh --force         — bot işləyirsə də davam (tövsiyə olunmur: baza / sessiya yarımçıq ola bilər)
#   OUT=/yol/paket.tar.gz bash deploy/pack_home.sh
#
# Paketə düşmür (yeni serverdə lazım deyil, ya da özü yaranır): .venv, bot_logs, data/antidelete_media (~6 GB),
# data/records, data/voice_cache, dlm_backup*, __pycache__, Chrome keşləri.  Depo mahnıları Telegram kanalındadır — köçürülmür.
# Sonra:  scp paket.tar.gz vds:~/   →   VDS-də:  bash restore_vds.sh paket.tar.gz   (təlimat: deploy/KOCURME_VDS.md)
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1

DRY=0; FORCE=0; BACKUPS=0
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --force) FORCE=1;; --with-backups) BACKUPS=1;; esac; done
c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok() { c 32 "✅ $*"; }; warn() { c 33 "⚠️  $*"; }; info() { c 36 "▶ $*"; }; err() { c 31 "❌ $*"; }

OUT="${OUT:-$HOME/dlm_migrate_$(date +%Y%m%d_%H%M).tar.gz}"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
OLD_BASE="$(pwd)"

# ── 1) bot dayanmış olmalıdır (sessiya fayllarını və bazanı tutarlı götürmək üçün) ──
if [ "$DRY" = 0 ] && pgrep -f "python.* (init|app)\.py" >/dev/null 2>&1 && [ "$FORCE" = 0 ]; then
  err "Bot hələ işləyir. Əvvəl dayandırın (Ctrl+C / systemctl --user stop dllmaster), sonra təkrar edin. (--force ilə keçmək olar)"
  exit 1
fi

# ── 2) paketə düşən fayllar ──
FILES="$STAGE/files.list"
{
  git ls-files -co --exclude-standard 2>/dev/null | grep -vE '^(dlm_backup|\.venv/|bot_logs/|data/antidelete_media/|data/records/|data/voice_cache/)' || true
  for f in config.env userbot.session; do [ -e "$f" ] && echo "$f"; done
  for f in data/*.session data/*.session-journal data/userbot.string data/userbot.db data/userbot.db-wal data/userbot.db-shm data/depo_state.json; do [ -e "$f" ] && echo "$f"; done
  for d in data/yt_profiles data/yt_accounts data/audit_reports data/va_records; do [ -d "$d" ] && find "$d" -type f; done
  [ "$BACKUPS" = 1 ] && [ -d data/backups ] && find data/backups -type f
} | sort -u > "$FILES"
# Chrome keşləri (cookie-lər qalır): profil başına yüzlərlə MB, yeni serverdə özü yaranır
grep -vE '/(Cache|Code Cache|GPUCache|DawnGraphiteCache|DawnWebGPUCache|Service Worker|IndexedDB|blob_storage|Shared Dictionary|GrShaderCache|ShaderCache|GraphiteDawnCache|component_crx_cache|extensions_crx_cache|optimization_guide_model_store|Safe Browsing|Crashpad)/' "$FILES" > "$FILES.f" && mv "$FILES.f" "$FILES"

info "Paketə düşən fayllar: $(wc -l < "$FILES")  (config.env: $([ -e config.env ] && echo var || echo YOXDUR))"
for k in "config.env" "userbot.session" "data/yt_profiles" "data/userbot.db" "data/depo_state.json"; do
  n=$(grep -cE "^${k}(/|$)" "$FILES" || true); printf '   %-24s %s fayl\n' "$k" "$n"
done
echo "   + db.sql.gz (PostgreSQL dump) + MANIFEST.txt"
echo "   Paket: $OUT"
if [ -n "$(git status --porcelain 2>/dev/null | head -1)" ]; then
  warn "Commit edilməmiş dəyişikliklər var — paket onları da daxil edir (git ls-files -co), yəni cari iş qovluğu olduğu kimi köçür."
fi
if [ "$DRY" = 1 ]; then ok "dry-run: heç nə yazılmadı"; exit 0; fi

# ── 3) SQLite (userbot.db) WAL-ı əsas fayla yaz ──
if [ -e data/userbot.db ]; then
  python3 - <<'PY' 2>/dev/null || warn "userbot.db checkpoint alınmadı (sqlite3 yoxdur?) — WAL faylı paketə düşür, bu da kifayətdir"
import sqlite3
c = sqlite3.connect("data/userbot.db"); c.execute("PRAGMA wal_checkpoint(TRUNCATE)"); c.close()
PY
fi

# ── 4) PostgreSQL dump ──
URL="$(grep -E '^DATABASE_URL=' config.env | head -1 | sed -E 's/^DATABASE_URL=//; s/^"//; s/"$//')"
[ -z "$URL" ] && { err "config.env-də DATABASE_URL yoxdur"; exit 1; }
info "PostgreSQL dump (≈1 GB baza, sıxılmış ~300 MB)..."
if ! pg_dump --no-owner --no-acl "$URL" 2>"$STAGE/pg_dump.err" | gzip -1 > "$STAGE/db.sql.gz"; then
  err "pg_dump alınmadı: $(tail -2 "$STAGE/pg_dump.err")"; exit 1
fi
[ -s "$STAGE/db.sql.gz" ] || { err "dump boşdur"; exit 1; }
ok "dump: $(du -h "$STAGE/db.sql.gz" | cut -f1)"

# ── 5) MANIFEST + paket ──
{
  echo "OLD_BASE=$OLD_BASE"
  echo "DATE=$(date -Is)"
  echo "COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo yoxdur)"
  echo "PG_DUMP=$(pg_dump --version)"
  echo "PYTHON=$(.venv/bin/python --version 2>&1 || python3 --version)"
  echo "FILES=$(wc -l < "$FILES")"
} > "$STAGE/MANIFEST.txt"
info "Paket yığılır..."
tar -czf "$OUT" -T "$FILES" -C "$STAGE" db.sql.gz MANIFEST.txt 2>"$STAGE/tar.err" || { err "tar: $(tail -2 "$STAGE/tar.err")"; exit 1; }
chmod 600 "$OUT"
ok "Hazırdır: $OUT ($(du -h "$OUT" | cut -f1))"
echo "   ⚠️ Paketdə config.env (tokenlər), Telegram sessiyaları və Google cookie-ləri var — yalnız şifrəli kanalla (scp) göndərin, sonra silin."
echo "   Növbəti addım:  scp '$OUT' ISTIFADECI@VDS:~/   →   VDS-də: bash restore_vds.sh ~/$(basename "$OUT")"
