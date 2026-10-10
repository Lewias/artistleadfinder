-- Cloud outreach: the app's «Рассылка» runs on the server, in the cloud parser's image but
-- its own process and Chromium, with the session (and the proxy it must have) the user
-- sent from the app. A job is a list of usernames and the message variants; who answered
-- is in Instagram, who was written to goes to the job's results and to the CRM.

-- ---------- jobs of two kinds ----------

alter table public.cloud_jobs
  add column if not exists kind text not null default 'scout'
    check (kind in ('scout', 'outreach'));
create index if not exists cloud_jobs_kind_open on public.cloud_jobs (kind, created_at)
  where stage in ('queued', 'collecting');

grant select (kind) on public.cloud_jobs to authenticated;

create or replace function public.cloud_limits() returns json
language sql immutable as $$
  select json_build_object(
    'sources', 500, 'target', 500, 'active_jobs', 2, 'settings_bytes', 20000,
    'recipients', 500, 'messages', 50, 'message_chars', 1000, 'active_outreach', 1
  )
$$;

-- ---------- starting a job ----------

create or replace function public.cloud_start(p_request_id text, p_params jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  limits json := public.cloud_limits();
  existing uuid;
  active int;
  v_kind text := coalesce(p_params ->> 'kind', 'scout');
  profile text := p_params ->> 'profile_id';
  sources jsonb := coalesce(p_params -> 'sources', '[]');
  categories jsonb := coalesce(p_params -> 'categories', '[]');
  settings jsonb := coalesce(p_params -> 'settings', '{}');
  usernames jsonb := coalesce(p_params -> 'usernames', '[]');
  messages jsonb := coalesce(p_params -> 'messages', '[]');
  session jsonb;
  target int;
  v_params jsonb;
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  if coalesce(length(p_request_id), 0) not between 8 and 64 then
    raise exception 'Некорректный запрос.' using errcode = '22023';
  end if;
  select id into existing from public.cloud_jobs
   where owner_id = auth.uid() and request_id = p_request_id;
  if existing is not null then
    return existing;
  end if;
  if v_kind not in ('scout', 'outreach') then
    raise exception 'Некорректный запрос.' using errcode = '22023';
  end if;

  select record into session from public.cloud_sessions
   where owner_id = auth.uid() and profile_id = profile;
  if session is null then
    raise exception 'Сессия этого аккаунта Instagram не отправлена на сервер.' using errcode = '22023';
  end if;
  if jsonb_typeof(settings) <> 'object'
     or length(settings::text) > (limits ->> 'settings_bytes')::int then
    raise exception 'Некорректные настройки.' using errcode = '22023';
  end if;

  if v_kind = 'outreach' then
    -- Messages go out only through the account's own proxy, never the server's address.
    if jsonb_typeof(session -> 'proxy') is distinct from 'object' then
      raise exception 'Для облачной рассылки у аккаунта должен быть прокси. Добавьте его в «Аккаунтах» и отправьте задачу снова.'
        using errcode = '22023';
    end if;
    if jsonb_typeof(usernames) <> 'array' or jsonb_array_length(usernames) = 0 then
      raise exception 'Некому писать: список пуст.' using errcode = '22023';
    end if;
    if jsonb_array_length(usernames) > (limits ->> 'recipients')::int or exists (
      select 1 from jsonb_array_elements(usernames) item
       where jsonb_typeof(item) <> 'string' or (item #>> '{}') !~ '^[a-z0-9_.]{1,30}$'
    ) then
      raise exception 'Некорректный список получателей (не больше % за раз).',
        limits ->> 'recipients' using errcode = '22023';
    end if;
    if jsonb_typeof(messages) <> 'array' or jsonb_array_length(messages) = 0 then
      raise exception 'Добавьте хотя бы одно сообщение для рассылки.' using errcode = '22023';
    end if;
    if jsonb_array_length(messages) > (limits ->> 'messages')::int or exists (
      select 1 from jsonb_array_elements(messages) item
       where jsonb_typeof(item) <> 'string'
          or length(btrim(item #>> '{}')) not between 1 and (limits ->> 'message_chars')::int
    ) then
      raise exception 'Некорректные сообщения (не больше %, до % символов).',
        limits ->> 'messages', limits ->> 'message_chars' using errcode = '22023';
    end if;
    v_params := jsonb_build_object('usernames', usernames, 'messages', messages, 'settings', settings);
  else
    if jsonb_typeof(sources) <> 'array' or jsonb_array_length(sources) = 0 then
      raise exception 'Добавьте хотя бы один источник.' using errcode = '22023';
    end if;
    if jsonb_array_length(sources) > (limits ->> 'sources')::int or exists (
      select 1 from jsonb_array_elements(sources) item
       where jsonb_typeof(item) <> 'string' or length(item #>> '{}') not between 1 and 300
    ) then
      raise exception 'Некорректные источники (не больше % за раз).', limits ->> 'sources'
        using errcode = '22023';
    end if;
    if jsonb_typeof(categories) <> 'array' or jsonb_array_length(categories) = 0 or exists (
      select 1 from jsonb_array_elements_text(categories) category
       where category not in ('ARTIST', 'PRODUCER', 'MEDIA', 'OTHER', 'UNKNOWN')
    ) then
      raise exception 'Выберите категории для CRM.' using errcode = '22023';
    end if;
    target := coalesce((p_params ->> 'target')::int, 0);
    if target not between 1 and (limits ->> 'target')::int then
      raise exception 'Сколько лидов найти: от 1 до %.', limits ->> 'target' using errcode = '22023';
    end if;
    v_params := jsonb_build_object(
      'sources', sources, 'categories', categories, 'target', target, 'settings', settings
    );
  end if;

  -- One request at a time per user, so two clicks cannot pass the count together.
  perform pg_advisory_xact_lock(hashtext('cloud_start:' || auth.uid()::text));
  select count(*) into active from public.cloud_jobs j
   where j.owner_id = auth.uid() and j.kind = v_kind
     and j.stage in ('queued', 'collecting');
  if v_kind = 'outreach' and active >= (limits ->> 'active_outreach')::int then
    raise exception 'Облачная рассылка уже идёт. Дождитесь конца или остановите её.'
      using errcode = '54000';
  end if;
  if v_kind = 'scout' and active >= (limits ->> 'active_jobs')::int then
    raise exception 'Уже идёт % облачных задачи. Дождитесь конца или отмените одну.', active
      using errcode = '54000';
  end if;

  insert into public.cloud_jobs (owner_id, request_id, profile_id, params, kind)
  values (auth.uid(), p_request_id, profile, v_params, v_kind)
  on conflict (owner_id, request_id) do nothing
  returning id into existing;
  if existing is null then
    select id into existing from public.cloud_jobs
     where owner_id = auth.uid() and request_id = p_request_id;
  end if;
  return existing;
end $$;

-- ---------- who the cloud has not written to yet ----------

-- New contacts the cloud parser added to the CRM that no cloud outreach has written to (or
-- has queued), newest first: the recipients of «Написать найденным».
create or replace function public.cloud_unwritten(p_limit int default 500)
returns table (username text, found_at timestamptz)
language sql stable security definer set search_path = public as $$
  select c.username, max(c.created_at) as found_at
    from public.cloud_candidates c
    join public.cloud_jobs j on j.id = c.job_id
   where c.owner_id = auth.uid() and public.is_active()
     and j.kind = 'scout' and c.outcome = 'added'
     and c.username ~ '^[a-z0-9_.]{1,30}$'
     and not exists (
       select 1 from public.cloud_candidates o
         join public.cloud_jobs oj on oj.id = o.job_id
        where o.owner_id = c.owner_id and oj.kind = 'outreach' and o.username = c.username)
     and not exists (
       select 1 from public.cloud_jobs a
        where a.owner_id = c.owner_id and a.kind = 'outreach'
          and a.stage in ('queued', 'collecting') and a.params -> 'usernames' ? c.username)
   group by c.username
   order by max(c.created_at) desc
   limit greatest(1, least(coalesce(p_limit, 500), 500))
$$;

revoke execute on function public.cloud_unwritten(int) from anon, public;
grant execute on function public.cloud_unwritten(int) to authenticated;
