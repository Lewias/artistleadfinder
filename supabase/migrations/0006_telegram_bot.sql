-- Telegram bot: a user links a Telegram chat to the account with a one-time code from the
-- app, then starts and stops cloud parser jobs from the chat and hears how they ended.
-- Admins' chats also hear about failed jobs and a stalled parser.
--
-- The app reads the link state, asks for a code and unlinks (only its own); the bot service
-- (supabase/worker, direct database connection) does the rest and starts jobs through
-- cloud_start with the user's own rights.

-- One chat per account and one account per chat.
create table public.tg_links (
  owner_id uuid primary key references public.profiles (id) on delete cascade,
  chat_id bigint not null unique,
  tg_username text not null default '',
  notify boolean not null default true,
  linked_at timestamptz not null default now()
);

-- Codes the app shows; the bot takes one with `/start <code>`. Never readable by users.
create table public.tg_link_codes (
  code text primary key,
  owner_id uuid not null unique references public.profiles (id) on delete cascade,
  expires_at timestamptz not null
);

-- The bot's own @username, written by the bot at start, for the app's link.
create table public.tg_bot (
  id boolean primary key default true check (id),
  username text not null,
  updated_at timestamptz not null default now()
);

-- The last stage of a job the bot has told its owner about.
alter table public.cloud_jobs add column if not exists notified text;

-- A fresh code for this account (the previous one stops working).
create function public.tg_link_start() returns json
language plpgsql security definer set search_path = public as $$
declare
  fresh text := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 10));
  expires timestamptz := now() + interval '15 minutes';
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  insert into public.tg_link_codes (code, owner_id, expires_at)
  values (fresh, auth.uid(), expires)
  on conflict (owner_id) do update set code = excluded.code, expires_at = excluded.expires_at;
  return json_build_object(
    'code', fresh,
    'expires_at', expires,
    'bot', (select username from public.tg_bot where id)
  );
end $$;

create function public.tg_status() returns json
language sql stable security definer set search_path = public as $$
  select json_build_object(
    'linked', l.owner_id is not null,
    'username', l.tg_username,
    'notify', coalesce(l.notify, true),
    'linked_at', l.linked_at,
    'bot', (select username from public.tg_bot where id)
  )
    from (select 1) one
    left join public.tg_links l on l.owner_id = auth.uid()
   where public.is_active()
$$;

create function public.tg_unlink() returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  delete from public.tg_links where owner_id = auth.uid();
  delete from public.tg_link_codes where owner_id = auth.uid();
end $$;

alter table public.tg_links enable row level security;
alter table public.tg_link_codes enable row level security;
alter table public.tg_bot enable row level security;
revoke all on public.tg_links, public.tg_link_codes, public.tg_bot from anon, authenticated;

revoke execute on function public.tg_link_start(), public.tg_status(), public.tg_unlink()
  from anon, public;
grant execute on function public.tg_link_start(), public.tg_status(), public.tg_unlink()
  to authenticated;
