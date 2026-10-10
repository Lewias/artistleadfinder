#!/usr/bin/env bash
# Cloud parser and cloud outreach of Artist Lead Finder next to the account server, as its own small docker
# compose project (/opt/alf-cloud-parser). The Supabase stack is not changed or restarted:
# the parser only joins its network to reach the database, with hard memory and CPU caps
# so the database and anything else on the server keep their share.
#
# Usage, as root, from the folder that holds this script, after copying the build context
# (Dockerfile, requirements.txt, start.sh, cloud_worker/, artist_lead_finder/, scripts/) to
# /opt/alf-cloud-parser/build:
#   bash cloud-parser.sh
# Safe to run again: the secret key is made once, each migration is applied once.
# The Telegram bot (alf-telegram-bot) needs TELEGRAM_BOT_TOKEN in /opt/alf-cloud-parser/.env;
# without it the bot container waits idle.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SUPA=/opt/alf-server
TARGET=/opt/alf-cloud-parser
MIGRATIONS="0005_cloud_parser.sql 0006_telegram_bot.sql 0007_cloud_outreach.sql"

step() { printf '\n==> %s\n' "$1"; }
[ "$(id -u)" = 0 ] || { echo "Запустите от root" >&2; exit 1; }
[ -f "$SUPA/.env" ] || { echo "Нет $SUPA/.env: сначала setup.sh" >&2; exit 1; }
[ -f "$TARGET/build/Dockerfile" ] || { echo "Нет $TARGET/build/Dockerfile" >&2; exit 1; }

step "Настройки"
cd "$TARGET"
umask 077
touch .env
set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}
grep -q '^ALF_SECRET_KEY=.' .env || set_env ALF_SECRET_KEY "$(openssl rand -hex 32)"
password="$(grep '^POSTGRES_PASSWORD=' "$SUPA/.env" | cut -d= -f2-)"
case "$password" in *'|'*) echo "Пароль базы с '|' не поддерживается скриптом" >&2; exit 1 ;; esac
set_env PGPASSWORD "$password"
set_env PGHOST db
set_env PGPORT "$(grep '^POSTGRES_PORT=' "$SUPA/.env" | cut -d= -f2-)"
set_env PGDATABASE "$(grep '^POSTGRES_DB=' "$SUPA/.env" | cut -d= -f2-)"
set_env PGUSER postgres
chmod 600 .env

cat > docker-compose.yml <<'YAML'
name: alf-cloud-parser
services:
  cloud-parser:
    container_name: alf-cloud-parser
    build: ./build
    image: alf-cloud-parser:latest
    restart: unless-stopped
    env_file: .env
    volumes:
      - parser-data:/data
    networks:
      - supabase
    # Chromium and the core stay inside these limits whatever Instagram serves.
    mem_limit: 1400m
    memswap_limit: 1400m
    cpus: 1.0
    shm_size: 256m
    pids_limit: 512
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
  cloud-outreach:
    container_name: alf-cloud-outreach
    image: alf-cloud-parser:latest
    depends_on:
      - cloud-parser
    command: ["/app/start.sh", "cloud_worker.outreach"]
    restart: unless-stopped
    env_file: .env
    # Its own users' folders (/data/outreach) on the same volume; never the parser's.
    volumes:
      - parser-data:/data
    networks:
      - supabase
    # One Chromium that mostly waits between messages.
    mem_limit: 1100m
    memswap_limit: 1100m
    cpus: 0.75
    shm_size: 256m
    pids_limit: 512
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
  telegram-bot:
    container_name: alf-telegram-bot
    image: alf-cloud-parser:latest
    depends_on:
      - cloud-parser
    command: ["python3", "-m", "cloud_worker.bot"]
    restart: unless-stopped
    env_file: .env
    networks:
      - supabase
    # Telegram is asked for updates (long polling): no port is opened.
    mem_limit: 160m
    memswap_limit: 160m
    cpus: 0.25
    pids_limit: 64
    logging:
      driver: json-file
      options:
        max-size: 5m
        max-file: "3"
networks:
  supabase:
    external: true
    name: supabase_default
volumes:
  parser-data:
YAML

psql_db() { docker exec -i supabase-db psql -h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 -q "$@"; }
backed_up=0
for MIGRATION in $MIGRATIONS; do
  step "Миграция $MIGRATION"
  if [ "$(psql_db -tAc "select 1 from public._alf_migrations where name = '$MIGRATION'")" = 1 ]; then
    echo "  уже применена"
    continue
  fi
  # A copy of the database first, so the change can be undone.
  if [ "$backed_up" = 0 ] && command -v alf-backup >/dev/null 2>&1; then alf-backup; backed_up=1; fi
  { echo "begin;"; cat "$SRC/migrations/$MIGRATION";
    echo "insert into public._alf_migrations(name) values ('$MIGRATION');"; echo "commit;"; } | psql_db
  psql_db -c "notify pgrst, 'reload schema';"
  echo "  применена"
done

step "Сборка и запуск"
docker compose build -q
docker compose up -d
sleep 5
docker compose ps
docker logs --tail 20 alf-cloud-parser
docker logs --tail 5 alf-cloud-outreach
docker logs --tail 5 alf-telegram-bot
