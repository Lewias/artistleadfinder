#!/usr/bin/env bash
# Account and CRM server of Artist Lead Finder on one Ubuntu VDS: self-hosted Supabase.
#
# Runs only what the app uses: Postgres, Auth, REST, Edge Functions, the API gateway,
# Studio (behind a password) and Caddy for HTTPS. Realtime, Storage, the pooler and
# the log stack are not started. Postgres is not published to the internet.
#
# Usage, as root, from the folder that holds this script with ../migrations and ../functions:
#   bash setup.sh api.example.ru
# Safe to run again: secrets are generated once, new migrations are applied once.

set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Укажите домен: bash setup.sh api.example.ru" >&2
  exit 1
fi
[ "$(id -u)" = 0 ] || { echo "Запустите от root (sudo bash setup.sh $DOMAIN)" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
TARGET=/opt/alf-server
SERVICES="db auth rest meta studio api-gw functions caddy"

step() { printf '\n==> %s\n' "$1"; }

step "Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq ca-certificates curl git openssl ufw cron

step "Docker"
if ! command -v docker >/dev/null 2>&1; then
  # Docker's own repository first; Ubuntu's packages when it has no build for this release yet.
  curl -fsSL https://get.docker.com | sh || apt-get install -yq docker.io docker-compose-v2
fi
docker compose version >/dev/null 2>&1 || apt-get install -yq docker-compose-v2
systemctl enable --now docker

step "Файл подкачки"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Сетевой экран: открыты только SSH, 80 и 443"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null

step "Supabase"
if [ ! -f "$TARGET/docker-compose.yml" ]; then
  rm -rf /tmp/supabase-src
  git clone --depth 1 https://github.com/supabase/supabase /tmp/supabase-src
  mkdir -p "$TARGET"
  cp -a /tmp/supabase-src/docker/. "$TARGET/"
  rm -rf /tmp/supabase-src
fi
cd "$TARGET"

set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

if [ ! -f .env ]; then
  cp .env.example .env
  sh utils/generate-keys.sh --update-env >/dev/null
  rm -f .env.old
  set_env POOLER_TENANT_ID "$(openssl rand -hex 8)"
  set_env DASHBOARD_USERNAME admin
fi
chmod 600 .env
# Studio on the server's own loopback: reachable only through an SSH tunnel (MCP, admin work).
cat > docker-compose.alf.yml <<'YAML'
services:
  studio:
    ports:
      - "127.0.0.1:3000:3000"
YAML
set_env COMPOSE_FILE "docker-compose.yml:docker-compose.caddy.yml:docker-compose.alf.yml"
set_env PROXY_DOMAIN "$DOMAIN"
set_env SUPABASE_PUBLIC_URL "https://$DOMAIN"
set_env API_EXTERNAL_URL "https://$DOMAIN/auth/v1"
set_env SITE_URL "https://$DOMAIN"
# Anyone signs up and signs in at once (no email confirmation); access comes with a key.
set_env DISABLE_SIGNUP false
set_env ENABLE_EMAIL_SIGNUP true
set_env ENABLE_EMAIL_AUTOCONFIRM true
set_env ENABLE_PHONE_SIGNUP false
set_env ENABLE_ANONYMOUS_USERS false
set_env OPENAI_API_KEY ""
# The admin-users function checks the caller's session itself.
set_env FUNCTIONS_VERIFY_JWT false

step "Функция admin-users"
mkdir -p volumes/functions/admin-users
cp "$SRC/functions/admin-users/index.ts" volumes/functions/admin-users/index.ts

step "Запуск"
docker compose pull -q $SERVICES
docker compose up -d $SERVICES

echo "Жду, пока поднимутся база и вход..."
for _ in $(seq 1 90); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' supabase-auth 2>/dev/null)" = healthy ]; then
    break
  fi
  sleep 2
done
[ "$(docker inspect -f '{{.State.Health.Status}}' supabase-auth)" = healthy ] || {
  echo "Сервис входа не поднялся. Журнал: docker logs supabase-auth" >&2
  exit 1
}

step "Миграции"
psql_db() { docker exec -i supabase-db psql -h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 -q "$@"; }
psql_db -c "create table if not exists public._alf_migrations (name text primary key, applied_at timestamptz not null default now());
alter table public._alf_migrations enable row level security;
revoke all on public._alf_migrations from anon, authenticated;"
for file in "$SRC"/migrations/*.sql; do
  name="$(basename "$file")"
  if [ "$(psql_db -tAc "select 1 from public._alf_migrations where name = '$name'")" = 1 ]; then
    echo "  $name: уже применена"
    continue
  fi
  { echo "begin;"; cat "$file"; echo "insert into public._alf_migrations(name) values ('$name');"; echo "commit;"; } | psql_db
  echo "  $name: применена"
done
# PostgREST rereads the schema after the change.
psql_db -c "notify pgrst, 'reload schema';"

step "Ежедневная копия базы"
install -m 700 "$HERE/backup.sh" /usr/local/bin/alf-backup
echo "30 3 * * * root /usr/local/bin/alf-backup >> /var/log/alf-backup.log 2>&1" > /etc/cron.d/alf-backup

step "Проверка HTTPS"
anon="$(grep '^ANON_KEY=' .env | cut -d= -f2-)"
ok=""
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null -H "apikey: $anon" "https://$DOMAIN/auth/v1/health"; then ok=1; break; fi
  sleep 4
done
if [ -z "$ok" ]; then
  echo "https://$DOMAIN пока не отвечает. Проверьте, что домен указывает на IP этого сервера,"
  echo "и посмотрите журнал: docker logs supabase-caddy"
fi

cat <<EOF

Готово.

Для приложения (backend/artist_lead_finder/account/project.py):
  SUPABASE_URL      = "https://$DOMAIN"
  SUPABASE_ANON_KEY = "$anon"

Панель Supabase Studio: https://$DOMAIN
  логин:  $(grep '^DASHBOARD_USERNAME=' .env | cut -d= -f2-)
  пароль: $(grep '^DASHBOARD_PASSWORD=' .env | cut -d= -f2-)

Первый админ: bash $HERE/make-admin.sh
Ключ SERVICE_ROLE_KEY и пароль базы лежат в $TARGET/.env. Никому их не передавайте.
EOF
