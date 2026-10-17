#!/usr/bin/env bash
# 🚚 DLLMaster — köçürmə paketini VDS-də açıb işə hazır vəziyyətə gətirir.
#
#   bash restore_vds.sh ~/dlm_migrate_XXXX.tar.gz [HEDEF_QOVLUQ]     (standart hədəf: ~/dllmaster)
#   --dry-run     — nə edəcəyini göstərir, heç nə dəyişmir
#   --no-service  — systemd istifadəçi servisi qurulmur
#   --force-db    — baza boş deyilsə də SİLİB yenidən yükləyir
#
# Addımlar: paketi aç → install.sh (paketlər, PostgreSQL, .venv, PO token, systemd) → baza dump-ını yüklə →
#           bazadakı köhnə yolları düzəlt (deploy/fix_paths.py --vds) → icazələr.  Botu İŞƏ SALMIR:
# əvvəl EVDƏKİ botu dayandırın (eyni Telegram sessiyası / bot tokeni iki yerdə eyni anda işləyə bilməz), sonra:
#   systemctl --user start dllmaster && journalctl --user -u dllmaster -f
# Təlimat və yoxlama siyahısı: deploy/KOCURME_VDS.md
set -uo pipefail

BUNDLE=""; TARGET=""; DRY=0; SERVICE=1; FORCEDB=0
for a in "$@"; do case "$a" in
  --dry-run) DRY=1;; --no-service) SERVICE=0;; --force-db) FORCEDB=1;;
  *) if [ -z "$BUNDLE" ]; then BUNDLE="$a"; elif [ -z "$TARGET" ]; then TARGET="$a"; fi;;
esac; done
c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok() { c 32 "✅ $*"; }; warn() { c 33 "⚠️  $*"; }; info() { c 36 "▶ $*"; }; err() { c 31 "❌ $*"; }
run() { if [ "$DRY" = 1 ]; then echo "   [dry-run] $*"; else eval "$@"; fi; }
[ -z "$BUNDLE" ] && { err "İstifadə: bash restore_vds.sh paket.tar.gz [hədəf_qovluq]"; exit 1; }
[ -f "$BUNDLE" ] || { err "Paket tapılmadı: $BUNDLE"; exit 1; }
TARGET="${TARGET:-$HOME/dllmaster}"
[ "$(id -u)" = 0 ] && warn "root ilə işlədirsiniz — adi istifadəçi tövsiyə olunur (systemd --user servisi üçün)."

# ── 1) aç ──
info "Paket açılır → $TARGET"
if [ -e "$TARGET/config.env" ] && [ "$DRY" = 0 ]; then
  warn "$TARGET/config.env artıq var — üstünə yazılacaq (köhnəsi config.env.before_restore)"; cp "$TARGET/config.env" "$TARGET/config.env.before_restore"
fi
run "mkdir -p '$TARGET' && tar -xzf '$BUNDLE' -C '$TARGET'"
if [ "$DRY" = 1 ]; then OLD_BASE="(MANIFEST.txt-dən oxunacaq)"; else
  cd "$TARGET" || exit 1
  OLD_BASE="$(grep '^OLD_BASE=' MANIFEST.txt | head -1 | cut -d= -f2-)"
  [ -n "$OLD_BASE" ] || { err "MANIFEST.txt-də OLD_BASE yoxdur — paket tam deyil"; exit 1; }
  ok "Açıldı. Köhnə qovluq: $OLD_BASE · $(grep '^COMMIT=' MANIFEST.txt)"
fi

# ── 2) Python: sistemdə köhnədirsə uv ilə 3.14 (bot 3.14-də sınaqdan keçib) ──
if [ "$DRY" = 0 ]; then
  PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
  if [ "$(printf '%s\n3.13\n' "$PYV" | sort -V | head -1)" != "3.13" ]; then
    warn "Sistem Python $PYV (< 3.13). uv ilə 3.14 quraşdırıram..."
    command -v uv >/dev/null 2>&1 || { curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
    uv python install 3.14 && export DLM_PYTHON="$(uv python find 3.14)" && ok "Python: $DLM_PYTHON"
  fi
fi

# ── 3) install.sh: paketlər, PostgreSQL (config.env-dəki DATABASE_URL ilə), .venv, PO token, systemd ──
info "install.sh işləyir (paketlər / PostgreSQL / .venv)..."
INST="--yes"; [ "$SERVICE" = 1 ] && INST="$INST --service"; [ "$DRY" = 1 ] && INST="$INST --dry-run"
if [ "$DRY" = 1 ]; then echo "   [dry-run] bash install.sh $INST"; else bash install.sh $INST || warn "install.sh xəbərdarlıqla bitdi — yuxarıya baxın"; fi

# ── 4) baza ──
info "PostgreSQL dump yüklənir..."
if [ "$DRY" = 1 ]; then echo "   [dry-run] zcat db.sql.gz | psql \$DATABASE_URL"; else
  URL="$(grep -E '^DATABASE_URL=' config.env | head -1 | sed -E 's/^DATABASE_URL=//; s/^"//; s/"$//')"
  HAVE="$(psql "$URL" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo ERR)"
  if [ "$HAVE" = ERR ]; then err "Bazaya qoşulmaq alınmadı ($URL) — install.sh çıxışına baxın"; exit 1; fi
  if [ "$HAVE" != 0 ] && [ "$FORCEDB" = 0 ]; then
    warn "Bazada artıq $HAVE cədvəl var — dump YÜKLƏNMİR (silmək üçün --force-db)"
  else
    [ "$HAVE" != 0 ] && psql "$URL" -qc "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    # PG 18 dump-ının yeni versiya sətirləri (transaction_timeout, \restrict) köhnə serverdə xəta verməsin
    zcat db.sql.gz | grep -vE '^(SET transaction_timeout|\\restrict|\\unrestrict)' | psql "$URL" -q -v ON_ERROR_STOP=0 >restore_db.log 2>&1
    N="$(psql "$URL" -tAc "SELECT count(*) FROM audio_cache" 2>/dev/null || echo ?)"
    ok "Baza yükləndi: audio_cache = $N sətir (xəbərdarlıqlar: restore_db.log)"
    rm -f db.sql.gz
  fi
fi

# ── 5) yollar və server rejimi ──
info "Bazadakı köhnə yollar düzəldilir (fix_paths.py --vds)..."
if [ "$DRY" = 1 ]; then echo "   [dry-run] .venv/bin/python deploy/fix_paths.py --old '$OLD_BASE' --new \$PWD --vds"; else
  .venv/bin/python deploy/fix_paths.py --old "$OLD_BASE" --new "$PWD" --vds || warn "fix_paths işləmədi — əl ilə: .venv/bin/python deploy/fix_paths.py --old '$OLD_BASE' --new '$PWD' --vds"
fi

# ── 6) icazələr ──
run "chmod 600 config.env userbot.session data/*.session data/userbot.string 2>/dev/null; chmod -R go-rwx data/yt_profiles 2>/dev/null; true"

echo
ok "Hazırdır. Botu İŞƏ SALMAZDAN ƏVVƏL:"
echo "   1. EVDƏKİ botu dayandırın və bir daha avtomatik işə düşməsin (iki yerdə eyni token / sessiya → xəta, sessiya itə bilər)."
echo "   2. VDS-də:  systemctl --user start dllmaster && journalctl --user -u dllmaster -f"
echo "   3. Yoxlama siyahısı: deploy/KOCURME_VDS.md → «İşə salandan sonra»"
