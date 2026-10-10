-- Cloud parser: the app's own Lead Scout runs on the server in a Chromium of its own, with
-- the Instagram session the user sent from the app, and saves the leads it finds to the
-- owner's Instagram CRM.
--
-- The app sends a session, creates and cancels a job and reads its progress (only its own);
-- the cloud parser service (supabase/worker, direct database connection) does the rest.

-- ---------- CRM: where a contact came from ----------

alter table public.crm_contacts
  add column if not exists instagram_id text,
  -- 'Cloud Parser' for contacts the cloud parser created; '' for the rest.
  add column if not exists source text not null default '',
  -- What the cloud parser found: category, confidence, reasons, profile summary, origin,
  -- checked_at. Written by the parser only; the app never sends it.
  add column if not exists cloud jsonb;

-- One contact per Instagram account in a CRM.
create unique index if not exists crm_contacts_owner_instagram_id
  on public.crm_contacts (owner_id, crm, instagram_id)
  where instagram_id is not null and not purged;

-- ---------- Instagram sessions ----------

-- The app's browser profile as the parser needs it: cookies and proxy (with its password).
-- Users write it through cloud_session_put and never read it back.
create table public.cloud_sessions (
  owner_id uuid not null references public.profiles (id) on delete cascade,
  profile_id text not null check (profile_id ~ '^[0-9a-f]{32}$'),
  name text not null default '',
  record jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (owner_id, profile_id)
);

-- ---------- jobs ----------

create table public.cloud_jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  -- The app's id of the click: a repeated request returns the same job.
  request_id text not null,
  profile_id text not null,
  params jsonb not null,
  stage text not null default 'queued' check (
    stage in ('queued', 'collecting', 'completed', 'failed', 'cancelled')
  ),
  cancel_requested boolean not null default false,
  -- What the parser is doing now: found, target, step, waiting, notices.
  progress jsonb not null default '{}',
  counters jsonb not null default '{}',
  error text,
  -- Parser bookkeeping: the lease of the process running the job.
  attempts int not null default 0,
  locked_by text,
  locked_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  unique (owner_id, request_id)
);
create index cloud_jobs_owner_created on public.cloud_jobs (owner_id, created_at desc);
create index cloud_jobs_open on public.cloud_jobs (created_at)
  where stage in ('queued', 'collecting');

-- Leads of a job as saved to the CRM.
create table public.cloud_candidates (
  id bigint generated always as identity primary key,
  job_id uuid not null references public.cloud_jobs (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  username text not null,
  instagram_id text,
  -- How the parser found it: posts, tagged, followers, following, profiles; and where.
  via text[] not null default '{}',
  origins jsonb not null default '[]',
  profile jsonb,
  category text check (category in ('ARTIST', 'PRODUCER', 'MEDIA', 'OTHER', 'UNKNOWN')),
  confidence int,
  reason text,
  -- added, updated, filtered, error.
  outcome text,
  note text,
  contact_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, username)
);
create index cloud_candidates_job on public.cloud_candidates (job_id, id);

create function public.cloud_touch() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at := clock_timestamp();
  return new;
end $$;
create trigger cloud_jobs_touch before update on public.cloud_jobs
  for each row execute function public.cloud_touch();
create trigger cloud_candidates_touch before update on public.cloud_candidates
  for each row execute function public.cloud_touch();

-- ---------- what the app may do ----------

create function public.cloud_limits() returns json
language sql immutable as $$
  select json_build_object('sources', 500, 'target', 500, 'active_jobs', 2, 'settings_bytes', 20000)
$$;

-- Sends (or refreshes) the session of one of the user's browser profiles.
create function public.cloud_session_put(p_profile text, p_name text, p_record jsonb)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  if coalesce(p_profile, '') !~ '^[0-9a-f]{32}$' then
    raise exception 'Некорректный аккаунт Instagram.' using errcode = '22023';
  end if;
  if jsonb_typeof(p_record -> 'cookies') <> 'array' or length(p_record::text) > 200000 then
    raise exception 'Некорректная сессия Instagram.' using errcode = '22023';
  end if;
  insert into public.cloud_sessions (owner_id, profile_id, name, record, updated_at)
  values (auth.uid(), p_profile, left(coalesce(p_name, ''), 80),
          jsonb_build_object('cookies', p_record -> 'cookies', 'proxy', p_record -> 'proxy'),
          now())
  on conflict (owner_id, profile_id) do update
    set name = excluded.name, record = excluded.record, updated_at = now();
end $$;

-- A new job, or the one this request already created. Returns its id at once.
create function public.cloud_start(p_request_id text, p_params jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  limits json := public.cloud_limits();
  existing uuid;
  active int;
  profile text := p_params ->> 'profile_id';
  sources jsonb := coalesce(p_params -> 'sources', '[]');
  categories jsonb := coalesce(p_params -> 'categories', '[]');
  settings jsonb := coalesce(p_params -> 'settings', '{}');
  target int;
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

  if not exists (
    select 1 from public.cloud_sessions where owner_id = auth.uid() and profile_id = profile
  ) then
    raise exception 'Сессия этого аккаунта Instagram не отправлена на сервер.' using errcode = '22023';
  end if;
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
  if jsonb_typeof(settings) <> 'object'
     or length(settings::text) > (limits ->> 'settings_bytes')::int then
    raise exception 'Некорректные настройки парсера.' using errcode = '22023';
  end if;

  -- One request at a time per user, so two clicks cannot pass the count together.
  perform pg_advisory_xact_lock(hashtext('cloud_start:' || auth.uid()::text));
  select count(*) into active from public.cloud_jobs
   where owner_id = auth.uid() and stage in ('queued', 'collecting');
  if active >= (limits ->> 'active_jobs')::int then
    raise exception 'Уже идёт % облачных задачи. Дождитесь конца или отмените одну.', active
      using errcode = '54000';
  end if;

  insert into public.cloud_jobs (owner_id, request_id, profile_id, params)
  values (
    auth.uid(), p_request_id, profile,
    jsonb_build_object(
      'sources', sources, 'categories', categories, 'target', target, 'settings', settings
    )
  )
  on conflict (owner_id, request_id) do nothing
  returning id into existing;
  if existing is null then
    select id into existing from public.cloud_jobs
     where owner_id = auth.uid() and request_id = p_request_id;
  end if;
  return existing;
end $$;

-- Asks the parser to stop; a job still waiting in the queue stops at once.
create function public.cloud_cancel(p_job uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  update public.cloud_jobs
     set cancel_requested = true,
         stage = case when stage = 'queued' then 'cancelled' else stage end,
         finished_at = case when stage = 'queued' then now() else finished_at end
   where id = p_job and owner_id = auth.uid() and stage in ('queued', 'collecting');
  if not found and not exists (
    select 1 from public.cloud_jobs where id = p_job and owner_id = auth.uid()
  ) then
    raise exception 'Задача не найдена.' using errcode = 'P0002';
  end if;
end $$;

-- Which sessions the server has (never the cookies themselves).
create function public.cloud_sessions_list() returns table (
  profile_id text, name text, has_proxy boolean, updated_at timestamptz
)
language sql stable security definer set search_path = public as $$
  select s.profile_id, s.name, (s.record -> 'proxy') is not null and s.record -> 'proxy' <> 'null',
         s.updated_at
    from public.cloud_sessions s
   where s.owner_id = auth.uid() and public.is_active()
   order by s.name
$$;

-- ---------- access rules ----------

alter table public.cloud_sessions enable row level security;
alter table public.cloud_jobs enable row level security;
alter table public.cloud_candidates enable row level security;

create policy cloud_jobs_read on public.cloud_jobs for select to authenticated
  using (owner_id = auth.uid() and public.is_active());
create policy cloud_candidates_read on public.cloud_candidates for select to authenticated
  using (owner_id = auth.uid() and public.is_active());

revoke all on public.cloud_sessions, public.cloud_jobs, public.cloud_candidates
  from anon, authenticated;
grant select (id, request_id, profile_id, params, stage, cancel_requested, progress, counters,
  error, created_at, updated_at, started_at, finished_at) on public.cloud_jobs to authenticated;
grant select (id, job_id, username, instagram_id, via, origins, profile, category, confidence,
  reason, outcome, note, contact_id, created_at, updated_at)
  on public.cloud_candidates to authenticated;

revoke execute on function public.cloud_start(text, jsonb), public.cloud_cancel(uuid),
  public.cloud_limits(), public.cloud_session_put(text, text, jsonb),
  public.cloud_sessions_list() from anon, public;
grant execute on function public.cloud_start(text, jsonb), public.cloud_cancel(uuid),
  public.cloud_limits(), public.cloud_session_put(text, text, jsonb),
  public.cloud_sessions_list() to authenticated;
