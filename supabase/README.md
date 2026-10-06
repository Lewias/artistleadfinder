# Сервер аккаунтов и общей CRM (Supabase)

Приложение хранит на сервере только аккаунты, ключи доступа и CRM (Instagram и iMessage).
Лиды, парсер, рассылки и сессии Instagram остаются на компьютере пользователя.

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

## Облачный supabase.com

1. Создайте проект. **Authentication → Providers → Email**: вход по паролю включён,
   **Allow new users to sign up** включено, **Confirm email** выключено.
2. **SQL Editor**: выполните файлы из `migrations/` по порядку.
3. **Edge Functions**: функция `admin-users` с кодом из `functions/admin-users/index.ts`
   (или `supabase functions deploy admin-users`).
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
