-- The cloud parser is gone; the Telegram bot now carries questions to the app's assistant.
--
-- The assistant runs inside the app on the user's computer (its Anthropic key, CRM and
-- message history are there), so the server only passes text: the bot stores what the
-- linked chat wrote, the running app takes it, asks the assistant and writes the answer
-- back, and the bot sends the answer to the chat.

-- ---------- the cloud parser and the cloud outreach ----------

-- Instagram sessions (cookies, proxy passwords), jobs and their results go with it. The
-- CRM keeps the contacts the parser saved and what it found on them (crm_contacts.source,
-- .cloud, .instagram_id).
drop function if exists public.cloud_unwritten(int);
drop function if exists public.cloud_start(text, jsonb);
drop function if exists public.cloud_cancel(uuid);
drop function if exists public.cloud_limits();
drop function if exists public.cloud_session_put(text, text, jsonb);
drop function if exists public.cloud_sessions_list();
drop table if exists public.cloud_candidates;
drop table if exists public.cloud_jobs;
drop table if exists public.cloud_sessions;
drop function if exists public.cloud_touch();

-- ---------- questions from Telegram ----------

-- When the app of this account last asked for questions: the bot tells a user whose app
-- is closed that the question will wait.
alter table public.tg_links add column if not exists app_seen_at timestamptz;

create table public.tg_requests (
  id bigint generated always as identity primary key,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  chat_id bigint not null,
  text text not null check (length(text) between 1 and 4000),
  -- new: waits for the app; taken: the assistant works on it; answered: the bot has to
  -- send the answer; sent: done; expired: nobody took it or answered in time.
  status text not null default 'new' check (
    status in ('new', 'taken', 'answered', 'sent', 'expired')
  ),
  answer text,
  created_at timestamptz not null default now(),
  taken_at timestamptz,
  answered_at timestamptz,
  sent_at timestamptz
);
create index tg_requests_open on public.tg_requests (owner_id, id)
  where status in ('new', 'taken');
create index tg_requests_answered on public.tg_requests (id) where status = 'answered';

-- The app asks for the questions of its account; each one is handed out once, so two
-- computers of one account do not answer the same question twice.
create function public.tg_requests_take()
returns table (request_id bigint, question text)
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  update public.tg_links set app_seen_at = now() where owner_id = auth.uid();
  return query
    update public.tg_requests r
       set status = 'taken', taken_at = now()
     where r.id in (
       select q.id from public.tg_requests q
        where q.owner_id = auth.uid() and q.status = 'new'
          and q.created_at > now() - interval '24 hours'
        order by q.id
        limit 5
        for update skip locked)
    returning r.id, r.text;
end $$;

-- The assistant's answer to a question this account took.
create function public.tg_request_answer(p_id bigint, p_answer text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  update public.tg_requests
     set status = 'answered',
         answer = left(coalesce(nullif(btrim(p_answer), ''), 'Ассистент ничего не ответил.'), 20000),
         answered_at = now()
   where id = p_id and owner_id = auth.uid() and status = 'taken';
end $$;

alter table public.tg_requests enable row level security;
revoke all on public.tg_requests from anon, authenticated;

revoke execute on function public.tg_requests_take(), public.tg_request_answer(bigint, text)
  from anon, public;
grant execute on function public.tg_requests_take(), public.tg_request_answer(bigint, text)
  to authenticated;
