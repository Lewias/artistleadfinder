# Сервер аккаунтов и общей CRM (Supabase)

Приложение хранит на сервере аккаунты, ключи доступа и CRM (Instagram и iMessage);
через него же Telegram-бот передаёт вопросы ассистенту. Парсер, рассылки, сессии Instagram и сам
ассистент остаются на компьютере пользователя.

Сервер — это Supabase на своём VDS (папка `vds/`). Подойдёт и облачный supabase.com,
настройка для него в конце.

## Свой VDS

Нужно: Ubuntu 22.04 или новее, 2 ядра, 4 ГБ памяти, от 20 ГБ диска и домен
(например `api.example.ru`), у которого A-запись указывает на IP сервера.

Запускаются только нужные части Supabase: база, вход, REST, функции, шлюз, панель Studio
и Caddy (HTTPS через Let's Encrypt). Realtime, хранилище файлов, пулер и сбор логов
не запускаются. База наружу не открыта, сетевой экран пропускает только SSH, 80 и 443.

### Установка

1. Скопируйте папку `supabase` на сервер (с компьютера, в PowerShell):

   ```
   scp -r supabase root@IP_СЕРВЕРА:/root/
   ```

2. На сервере:

   ```
   ssh root@IP_СЕРВЕРА
   bash /root/supabase/vds/setup.sh api.example.ru
   ```

   Скрипт ставит Docker, генерирует секреты, поднимает сервер, применяет миграции
   и включает ежедневную копию базы. В конце он печатает **URL** и **anon key** для приложения
   и пароль панели Studio (`https://api.example.ru`).

3. Первый админ:

   ```
   bash /root/supabase/vds/make-admin.sh
   ```

   Скрипт спросит email, имя и пароль и выведет ключ `ALF-…`. Им админ активирует свой компьютер.
   Пользователи регистрируются сами в приложении. Ключи им админ выпускает в разделе «Пользователи».

4. URL и anon key впишите в `backend/artist_lead_finder/account/project.py` и пересоберите
   приложение. Anon key не секрет: доступ режут правила в самой базе.
   `SERVICE_ROLE_KEY` и пароль базы остаются в `/opt/alf-server/.env` на сервере, никому их не передавайте.

### Обновление после изменений в репозитории

Новые файлы в `migrations/` и правки функции применяются той же командой:

```
scp -r supabase root@IP_СЕРВЕРА:/root/
ssh root@IP_СЕРВЕРА bash /root/supabase/vds/setup.sh api.example.ru
```

Секреты и данные при этом не меняются. Каждая миграция применяется один раз.

### Обслуживание

- Состояние: `cd /opt/alf-server && docker compose ps`
- Журнал сервиса: `docker logs supabase-auth` (или `supabase-rest`, `supabase-edge-functions`, `supabase-caddy`)
- Перезапуск: `cd /opt/alf-server && docker compose restart`
- Копии базы: `/opt/alf-server/backups/`, каждую ночь в 03:30, хранятся последние 14.
  Сделать копию сейчас: `alf-backup`. Копии лучше иногда забирать к себе:
  `scp root@IP_СЕРВЕРА:/opt/alf-server/backups/*.dump .`

### MCP для Claude Code

MCP даёт полный доступ к базе, поэтому наружу он закрыт. Панель Studio слушает только `127.0.0.1:3000`
на самом сервере, и MCP работает через SSH-туннель, пока он открыт:

```
ssh -N -L 3000:127.0.0.1:3000 root@IP_СЕРВЕРА
claude mcp add --scope local --transport http supabase http://localhost:3000/api/mcp
```

### Восстановление из копии

```
cd /opt/alf-server
docker exec -i supabase-db pg_restore -h localhost -U supabase_admin -d postgres --clean --if-exists \
  < backups/alf-ГГГГ-ММ-ДД.dump
docker compose restart auth rest
```

## Telegram-бот ассистента

Через бота можно задать вопрос ассистенту приложения с телефона и получить ответ в чат.
Ассистент работает в приложении на компьютере пользователя (там его ключ Anthropic, CRM и
переписка), поэтому сервер только передаёт текст:

- Пользователь нажимает «Подключить Telegram» в «Настройках» → «Ассистент», приложение
  получает одноразовый код (`tg_link_start`, 15 минут) и открывает `t.me/<бот>?start=<код>`.
- Любое сообщение привязанного чата бот кладёт в `tg_requests`. Запущенное приложение раз в
  несколько секунд забирает вопросы своего аккаунта (`tg_requests_take`), спрашивает
  ассистента — в том же чате, что виден в разделе «Ассистент», — и пишет ответ
  (`tg_request_answer`); бот отправляет его в чат.
- Приложение закрыто — бот сразу говорит об этом, вопрос ждёт до суток. Приложение взяло
  вопрос и не ответило за 15 минут — бот просит повторить.
- Из Telegram ничего не отправляется артистам и не запускается: черновики и предложенные
  действия ассистента ждут кнопки в приложении, ответ в чате только показывает их.
- `/new` — новый чат с ассистентом, `/unlink` — отвязать чат, `/help` — справка.
- Мост в приложении работает у админов и модераторов (у них есть ассистент).

Части:
- `migrations/0006_telegram_bot.sql` — привязка чата (`tg_links`, `tg_link_codes`, `tg_bot`,
  функции `tg_link_start`, `tg_status`, `tg_unlink`).
- `migrations/0008_telegram_assistant.sql` — очередь вопросов (`tg_requests`,
  `tg_requests_take`, `tg_request_answer`) и удаление облачного парсера: таблицы
  `cloud_sessions`, `cloud_jobs`, `cloud_candidates` и их функции. Контакты, которые парсер
  сохранил в CRM, остаются.
- `worker/telegram_bot/` — сам бот: один небольшой контейнер `alf-telegram-bot` (160 МБ,
  без портов: бот сам спрашивает Telegram о новых сообщениях).
- `vds/telegram-bot.sh` — установка и обновление.
- В приложении: `backend/artist_lead_finder/assistant/telegram.py`.

Токен от @BotFather кладётся на сервер, не в репозиторий:

```
ssh -t root@IP_СЕРВЕРА 'umask 077; read -rsp "Токен бота: " t; echo; mkdir -p /opt/alf-telegram-bot && cd /opt/alf-telegram-bot && touch .env && (grep -v "^TELEGRAM_BOT_TOKEN=" .env; echo "TELEGRAM_BOT_TOKEN=$t") > .env.new && mv .env.new .env; [ -f docker-compose.yml ] && docker compose up -d'
```

### Установка и обновление

```
ssh root@IP_СЕРВЕРА rm -rf /root/supabase
scp -r supabase root@IP_СЕРВЕРА:/root/
ssh root@IP_СЕРВЕРА bash /root/supabase/vds/telegram-bot.sh
```

Скрипт делает копию базы, один раз применяет каждую миграцию, собирает образ и
перезапускает только `alf-telegram-bot`. Если на сервере ещё стоит облачный парсер
(`/opt/alf-cloud-parser`), скрипт останавливает его контейнеры, удаляет их том с данными и
образ и переносит токен бота в `/opt/alf-telegram-bot/.env`.

- Журнал: `docker logs -f alf-telegram-bot`
- Остановить: `cd /opt/alf-telegram-bot && docker compose stop`

Тесты бота (локальный Postgres из пакета `pgserver`, без Docker; окружение в папке без
кириллицы в пути):

```
python -m venv C:/Users/Public/alf-cloud-test/venv
C:/Users/Public/alf-cloud-test/venv/Scripts/pip install -r supabase/worker/requirements-dev.txt
cd supabase/worker && C:/Users/Public/alf-cloud-test/venv/Scripts/python -m pytest
```

## Облачный supabase.com

1. Создайте проект. **Authentication → Providers → Email**: вход по паролю включён,
   **Allow new users to sign up** включено, **Confirm email** выключено.
2. **SQL Editor**: выполните файлы из `migrations/` по порядку.
3. **Edge Functions**: функция `admin-users` с кодом из `functions/admin-users/index.ts`
   (или `supabase functions deploy admin-users`). Telegram-бот (`worker/`) запускается
   отдельно, на любой машине с доступом к базе (переменные PGHOST, PGUSER, PGPASSWORD и т. д.).
4. Первый админ: **Authentication → Users → Add user** с отметкой «Auto Confirm», затем в SQL Editor:

   ```sql
   begin;
   update public.profiles set role = 'admin', display_name = 'Ваше имя'
   where email = 'admin@example.com';
   select set_config('request.jwt.claims',
     json_build_object('sub', id, 'role', 'authenticated')::text, true)
   from public.profiles where email = 'admin@example.com';
   select public.admin_issue_key(id, 'первый ключ админа') from public.profiles
   where email = 'admin@example.com';
   commit;
   ```

5. **Project Settings → API**: Project URL и anon public key — в `project.py`.

## Как это работает

- **Регистрация и вход.** Человек сам регистрируется в приложении (имя, email, пароль) и сразу входит. Пока у аккаунта нет ключа, приложение просит ключ доступа `ALF-…`, его выдаёт админ. Ключ вводится один раз и принадлежит аккаунту: дальше человек просто входит на любом компьютере. «Отвязать» в разделе «Пользователи» снимает доступ с аккаунта, и ключ можно выдать другому; «Отозвать» гасит ключ навсегда.
- **Роли.**
  - **Админ** — всё: пользователи, ключи, роли и CRM всех пользователей. Назначается только на сервере (`make-admin.sh`), из приложения админа сделать нельзя.
  - **Модератор** — обычный софт плюс чтение и правка CRM всех пользователей, у каждого контакта подписан владелец. Пользователей и ключей не видит. Роль задаёт ключ: админ при выпуске выбирает «Пользователь» или «Модератор», и аккаунт получает эту роль, когда вводит ключ (или сразу, если ключ выпущен для него). Её можно сменить и потом, в разделе «Пользователи».
  - **Пользователь** — обычный софт, только своя CRM.

  Эти правила проверяет сам сервер (Row Level Security), а не только приложение.
- **Синхронизация CRM.** Приложение работает с локальной копией CRM и синхронизирует её с сервером каждые 15 секунд, а также сразу после правки. Действует последняя правка.
- **Без связи с сервером** приложение блокируется. Как только связь вернётся, работа продолжится сама.
- **Ключи** сервер хранит только в виде отпечатка sha256. Сам ключ показывается один раз при выпуске.
