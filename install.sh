#!/usr/bin/env bash
# 🚀 DLLMaster — yeni kompüterə tam avtomatik quraşdırma (Linux: Arch/Manjaro, Debian/Ubuntu)
#
#   bash install.sh              — hər şeyi quraşdırır (soruşa-soruşa)
#   bash install.sh --yes        — verilmiş suallar xaric soruşmur (config.env-i əvvəlcədən doldurmusunuzsa)
#   bash install.sh --dry-run    — heç nə dəyişmədən nə edəcəyini göstərir
#   bash install.sh --service    — sonda systemd istifadəçi servisi yaradır (kompüter açılanda bot başlasın)
#
# Addımlar: sistem paketləri → PostgreSQL (quraşdırma, istifadəçi, baza, pg_trgm) → config.env → Docker PO token
#           (könüllü) → .venv + Python paketləri → yoxlama.  Təkrar işlədilə bilər (olanı atlayır).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

YES=0; DRY=0; SERVICE=0
for a in "$@"; do case "$a" in --yes|-y) YES=1;; --dry-run) DRY=1;; --service) SERVICE=1;; esac; done

c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok() { c 32 "✅ $*"; }; warn() { c 33 "⚠️  $*"; }; info() { c 36 "▶ $*"; }; err() { c 31 "❌ $*"; }
run() { if [ "$DRY" = 1 ]; then echo "   [dry-run] $*"; else eval "$@"; fi; }
ask() { # ask "sual" DEFAULT → cavab
  local q="$1" def="${2:-}" ans
  if [ "$YES" = 1 ] || [ "$DRY" = 1 ]; then echo "$def"; return; fi
  read -r -p "$q ${def:+[$def] }" ans; echo "${ans:-$def}"
}
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = 0 ] && warn "root ilə işlədirsiniz — adi istifadəçi ilə işlətmək tövsiyə olunur."
SUDO=""; [ "$(id -u)" != 0 ] && SUDO="sudo"

# ── 1) paket meneceri ──
if have pacman; then PM=pacman; elif have apt-get; then PM=apt; else
  err "Dəstəklənən paket meneceri yoxdur (pacman / apt). Əl ilə: deps/ qovluğuna baxın."; exit 1; fi
info "Paket meneceri: $PM"

pkgs_from() { # deps/<pm>.txt → məcburi paketlər (? ilə başlayanlar könüllüdür)
  grep -vE '^\s*(#|$|\?)' "deps/$1.txt" | tr '\n' ' '
}
if [ "$PM" = pacman ]; then
  SYS_PKGS="$(pkgs_from pacman) python python-pip postgresql docker"
  run "$SUDO pacman -S --needed --noconfirm $SYS_PKGS"
else
  SYS_PKGS="$(pkgs_from apt) python3 python3-venv python3-pip postgresql postgresql-contrib docker.io curl"
  run "$SUDO apt-get update -y && $SUDO apt-get install -y $SYS_PKGS"
fi
ok "Sistem paketləri hazırdır"

# ── 2) PostgreSQL ──
info "PostgreSQL hazırlanır..."
if [ "$PM" = pacman ] && [ ! -d /var/lib/postgres/data/base ]; then
  run "$SUDO -u postgres initdb -D /var/lib/postgres/data --locale=C.UTF-8 --encoding=UTF8"
fi
run "$SUDO systemctl enable --now postgresql"
DB_NAME="${DLM_DB_NAME:-dlmaster}"; DB_USER="${DLM_DB_USER:-dlm}"
DB_PASS="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
# config.env artıq DATABASE_URL ehtiva edirsə (köhnə kompüterdən köçürülüb) — baza eyni istifadəçi / parol / adla yaradılır
if [ -f config.env ]; then
  URL="$(grep -E '^DATABASE_URL=' config.env | head -1 | sed -E 's/^DATABASE_URL=//; s/^"//; s/"$//')"
  if [[ "$URL" =~ ^postgres(ql)?://([^:@/]+):([^@]+)@[^/]+/([^?]+) ]]; then
    DB_USER="${BASH_REMATCH[2]}"; DB_PASS="${BASH_REMATCH[3]}"; DB_NAME="${BASH_REMATCH[4]}"
    info "config.env-dəki DATABASE_URL istifadə olunur (istifadəçi: $DB_USER, baza: $DB_NAME)"
  fi
fi
pg() { $SUDO -u postgres psql -v ON_ERROR_STOP=1 -tAc "$1"; }
if [ "$DRY" = 1 ]; then
  echo "   [dry-run] CREATE ROLE $DB_USER / CREATE DATABASE $DB_NAME / CREATE EXTENSION pg_trgm"
else
  if [ "$(pg "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" 2>/dev/null)" = 1 ]; then
    ok "PostgreSQL istifadəçisi '$DB_USER' artıq var (parol dəyişmir)"; DB_PASS=""
  else
    pg "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS'" >/dev/null && ok "İstifadəçi '$DB_USER' yaradıldı"
  fi
  if [ "$(pg "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null)" = 1 ]; then
    ok "Baza '$DB_NAME' artıq var"
  else
    $SUDO -u postgres createdb -O "$DB_USER" "$DB_NAME" && ok "Baza '$DB_NAME' yaradıldı"
  fi
  $SUDO -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" >/dev/null 2>&1 \
    && ok "pg_trgm aktivdir (sürətli depo axtarışı)" || warn "pg_trgm yoxdur (postgresql-contrib?) — depo axtarışı sadə rejimdə işləyəcək"
fi

# ── 3) config.env ──
info "config.env..."
if [ -f config.env ]; then
  ok "config.env artıq var — toxunulmur"
  if [ -n "$DB_PASS" ] && ! grep -q '^DATABASE_URL=' config.env; then
    run "printf 'DATABASE_URL=\"postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME\"\n' >> config.env"
  fi
else
  TOKEN="$(ask 'Telegram bot tokeni (@BotFather):' '')"
  CREATOR="$(ask 'Sizin Telegram ID-niz (rəqəm, @userinfobot):' '')"
  CUSER="$(ask 'Telegram istifadəçi adınız (@-siz):' '')"
  API_ID="$(ask 'Telethon API ID (my.telegram.org, userbot üçün; boş buraxmaq olar):' '')"
  API_HASH="$(ask 'Telethon API HASH:' '')"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] config.env config.env.example əsasında yaradılır"; else
    cp config.env.example config.env
    esc() { printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'; }
    [ -n "$TOKEN" ]    && sed -i "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=\"$(esc "$TOKEN")\"|" config.env
    [ -n "$CREATOR" ]  && sed -i "s|^CREATOR_ID=.*|CREATOR_ID=$(esc "$CREATOR")|" config.env
    [ -n "$CUSER" ]    && sed -i "s|^CREATOR_USERNAME=.*|CREATOR_USERNAME=\"$(esc "$CUSER")\"|" config.env
    [ -n "$API_ID" ]   && sed -i "s|^TELETHON_API_ID=.*|TELETHON_API_ID=$(esc "$API_ID")|" config.env
    [ -n "$API_HASH" ] && sed -i "s|^TELETHON_API_HASH=.*|TELETHON_API_HASH=\"$(esc "$API_HASH")\"|" config.env
    [ -n "$DB_PASS" ]  && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=\"postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME\"|" config.env
    chmod 600 config.env
    ok "config.env yaradıldı (icazə 600)"
    [ -z "$TOKEN" ] && warn "TELEGRAM_TOKEN boşdur — botu işə salmazdan əvvəl config.env-də doldurun"
    [ -z "$DB_PASS" ] && warn "Baza istifadəçisi əvvəldən var idi: DATABASE_URL-dəki parolu özünüz yazın"
  fi
fi

# ── 4) Docker PO token provayderi (YouTube 403-ə qarşı, könüllü) ──
info "PO token provayderi (Docker)..."
if have docker; then
  run "$SUDO systemctl enable --now docker"
  if [ "$(ask 'YouTube 403-ə qarşı PO token konteynerini qaldırım? (y/n)' 'y')" = y ]; then
    run "$SUDO docker rm -f bgutil-pot >/dev/null 2>&1; $SUDO docker run -d --name bgutil-pot --restart unless-stopped -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider"
    ok "bgutil-pot konteyneri (port 4416) işləyir"
  fi
else
  warn "Docker yoxdur — PO token atlanıldı (bot işləyir, amma YouTube-da 403 daha çox olar)"
fi

# ── 5) Python mühiti ──
info ".venv və Python paketləri..."
run "python3 -m venv .venv"
run ".venv/bin/pip install --upgrade pip wheel"
run ".venv/bin/pip install -r requirements.txt"
ok "Python paketləri hazırdır"

# ── 6) yoxlama ──
if [ "$DRY" = 0 ]; then
  info "Yoxlama..."
  .venv/bin/python - <<'PY' || warn "Yoxlamada problem var — yuxarıdakı mesajlara baxın"
import os, sys
from dotenv import load_dotenv
load_dotenv("config.env")
import psycopg
url = os.getenv("DATABASE_URL", "")
try:
    with psycopg.connect(url, connect_timeout=5) as c:
        c.execute("SELECT 1")
    print("✅ PostgreSQL bağlantısı işləyir")
except Exception as e:
    print("❌ PostgreSQL:", str(e)[:150]); sys.exit(1)
print("✅ TELEGRAM_TOKEN", "var" if os.getenv("TELEGRAM_TOKEN", "").count(":") else "YOXDUR (config.env-də doldurun)")
PY
fi

# ── 7) systemd servisi (könüllü) ──
if [ "$SERVICE" = 1 ]; then
  UNIT="$HOME/.config/systemd/user/dllmaster.service"
  info "systemd servisi: $UNIT"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] $UNIT yazılır"; else
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=DLLMaster bot
After=network-online.target postgresql.service

[Service]
WorkingDirectory=$(pwd)
Environment=DLM_AUTO_INSTALL=1 PYTHONUNBUFFERED=1
ExecStart=$(pwd)/.venv/bin/python init.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload && systemctl --user enable dllmaster.service
    loginctl enable-linger "$USER" 2>/dev/null || true
    ok "Servis qurulub. Başlat: systemctl --user start dllmaster  · Log: journalctl --user -u dllmaster -f"
  fi
fi

echo
ok "Quraşdırma bitdi."
echo "   Botu işə salmaq:  .venv/bin/python init.py"
echo "   Userbot/asistan sessiyası və cookie hesabı: bot işləyəndən sonra /menu-dan qoşun."
