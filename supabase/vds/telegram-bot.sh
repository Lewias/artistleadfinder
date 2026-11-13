#!/usr/bin/env bash
# Telegram bot of the assistant next to the account server, as its own small docker compose
# project (/opt/alf-telegram-bot). The Supabase stack is not changed or restarted: the bot
# only joins its network to reach the database.
#
# Usage, as root, from the copy of the repository's `supabase` folder on the server:
#   bash /root/supabase/vds/telegram-bot.sh
# Safe to run again: each migration is applied once, the bot's token stays where it is.
# The first run also removes the cloud parser this bot used to belong to: its containers,
# its data volume and its image; the bot's token moves over from /opt/alf-cloud-parser.
# The bot needs TELEGRAM_BOT_TOKEN in /opt/alf-telegram-bot/.env; without it the container
# waits idle.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SUPA=/opt/alf-server
TARGET=/opt/alf-telegram-bot
OLD=/opt/alf-cloud-parser
MIGRATIONS="0006_telegram_bot.sql 0008_telegram_assistant.sql"

step() { printf '\n==> %s\n' "$1"; }
[ "$(id -u)" = 0 ] || { echo "Запустите от root" >&2; exit 1; }
[ -f "$SUPA/.env" ] || { echo "Нет $SUPA/.env: сначала setup.sh" >&2; exit 1; }
[ -f "$SRC/worker/Dockerfile" ] || { echo "Нет $SRC/worker/Dockerfile" >&2; exit 1; }

if [ -d "$OLD" ]; then
  step "Облачный парсер: останавливаю и удаляю"
  # Before the migration: the old processes read the tables it drops.
  (cd "$OLD" && docker compose down --volumes --remove-orphans)
  mkdir -p "$TARGET"
  [ -f "$TARGET/.env" ] || mv "$OLD/.env" "$TARGET/.env"
  rm -rf "$OLD"
  docker image rm alf-cloud-parser:latest >/dev/null 2>&1 || true
fi

step "Настройки"
mkdir -p "$TARGET"
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
# The key that encrypted Instagram sessions for the parser is of no use to the bot.
sed -i '/^ALF_SECRET_KEY=/d' .env
password="$(grep '^POSTGRES_PASSWORD=' "$SUPA/.env" | cut -d= -f2-)"
case "$password" in *'|'*) echo "Пароль базы с '|' не поддерживается скриптом" >&2; exit 1 ;; esac
set_env PGPASSWORD "$password"
set_env PGHOST db
set_env PGPORT "$(grep '^POSTGRES_PORT=' "$SUPA/.env" | cut -d= -f2-)"
set_env PGDATABASE "$(grep '^POSTGRES_DB=' "$SUPA/.env" | cut -d= -f2-)"
set_env PGUSER postgres
chmod 600 .env

rm -rf build
mkdir build
cp "$SRC/worker/Dockerfile" "$SRC/worker/requirements.txt" build/
cp -r "$SRC/worker/telegram_bot" build/

cat > docker-compose.yml <<'YAML'
name: alf-telegram-bot
services:
  telegram-bot:
    container_name: alf-telegram-bot
    build: ./build
    image: alf-telegram-bot:latest
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
YAML

psql_db() { docker exec -i supabase-db psql -h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 -q "$@"; }
backed_up=0
for MIGRATION in $MIGRATIONS; do
  step "Миграция $MIGRATION"
  if [ "$(psql_db -tAc "select 1 from public._alf_migrations where name = '$MIGRATION'" </dev/null)" = 1 ]; then
    echo "  уже применена"
    continue
  fi
  # A copy of the database first, so the change can be undone.
  if [ "$backed_up" = 0 ] && command -v alf-backup >/dev/null 2>&1; then alf-backup; backed_up=1; fi
  { echo "begin;"; cat "$SRC/migrations/$MIGRATION";
    echo "insert into public._alf_migrations(name) values ('$MIGRATION');"; echo "commit;"; } | psql_db
  psql_db -c "notify pgrst, 'reload schema';" </dev/null
  echo "  применена"
done

step "Сборка и запуск"
docker compose build -q
docker compose up -d --remove-orphans
sleep 5
docker compose ps
docker logs --tail 10 alf-telegram-bot
