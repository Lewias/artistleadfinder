#!/usr/bin/env bash
# The admin: an existing account (registered in the app) gets the admin role, or a new
# account is created. Either way it gets an access key for the app.
# Usage, as root on the server, after setup.sh:  bash make-admin.sh
# A new password is read without echo and is not saved anywhere on the server.

set -euo pipefail
cd /opt/alf-server

env_value() { grep "^$1=" .env | cut -d= -f2-; }
DOMAIN="$(env_value PROXY_DOMAIN)"
SERVICE="$(env_value SERVICE_ROLE_KEY)"
psql_db() { docker exec -i supabase-db psql -h localhost -U postgres -d postgres -v ON_ERROR_STOP=1 -qtA "$@"; }

read -rp "Email админа: " EMAIL
EMAIL="$(tr '[:upper:]' '[:lower:]' <<<"$EMAIL" | xargs)"
USER_ID="$(psql_db -v email="$EMAIL" <<'SQL'
select id from public.profiles where lower(email) = :'email';
SQL
)"

if [ -n "$USER_ID" ]; then
  echo "Аккаунт уже зарегистрирован: он станет админом, пароль остаётся прежним."
  read -rp "Имя (Enter — оставить как есть): " NAME
else
  read -rp "Имя (как его видят в CRM): " NAME
  read -rsp "Пароль (от 8 символов): " PASSWORD; echo
  [ "${#PASSWORD}" -ge 8 ] || { echo "Пароль короче 8 символов." >&2; exit 1; }
  payload="$(EMAIL="$EMAIL" NAME="$NAME" PASSWORD="$PASSWORD" python3 -c '
import json, os
print(json.dumps({"email": os.environ["EMAIL"], "password": os.environ["PASSWORD"],
                  "email_confirm": True, "user_metadata": {"display_name": os.environ["NAME"]}}))')"
  answer="$(curl -sS -X POST "https://$DOMAIN/auth/v1/admin/users" \
    -H "apikey: $SERVICE" -H "Authorization: Bearer $SERVICE" -H "Content-Type: application/json" \
    --data-binary @- <<<"$payload")"
  USER_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))' <<<"$answer")"
  if [ -z "$USER_ID" ]; then
    echo "Не удалось создать пользователя: $answer" >&2
    exit 1
  fi
fi

# admin_issue_key checks that the caller is an admin: the session acts as the new admin.
KEY="$(psql_db -v uid="$USER_ID" -v name="$NAME" <<'SQL'
begin;
update public.profiles
   set role = 'admin', display_name = coalesce(nullif(:'name', ''), display_name)
 where id = :'uid';
select set_config('request.jwt.claims', json_build_object('sub', :'uid', 'role', 'authenticated')::text, true) \g /dev/null
select public.admin_issue_key(:'uid', 'ключ админа');
commit;
SQL
)"

cat <<EOF

Админ: $EMAIL
Ключ доступа (показывается один раз): $KEY
Войдите в приложение с этим email и паролем и введите ключ.
EOF
