-- Accounts, roles, access keys and the shared CRM of Artist Lead Finder.
--
-- Users see and change only their own CRM; admins see and change every CRM. Access keys
-- bind an account to one device. Everything else of the app stays on the user's computer.

create extension if not exists pgcrypto;

-- ---------- profiles and roles ----------

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null default '',
  display_name text not null default '',
  role text not null default 'user' check (role in ('user', 'admin')),
  blocked boolean not null default false,
  created_at timestamptz not null default now()
);

-- Every new auth user gets a profile; the role is never taken from the sign-up data.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    coalesce(new.email, ''),
    left(coalesce(new.raw_user_meta_data ->> 'display_name', ''), 80)
  );
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles where id = auth.uid() and role = 'admin' and not blocked
  )
$$;

create function public.is_active() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and not blocked)
$$;

-- ---------- access keys ----------

create table public.license_keys (
  id uuid primary key default gen_random_uuid(),
  -- sha256 of the key; the key itself is shown once when issued and never stored.
  key_hash text not null unique,
  -- Last characters, to tell keys apart in the admin list.
  key_hint text not null,
  user_id uuid references public.profiles (id) on delete set null,
  device_id text,
  note text not null default '',
  created_at timestamptz not null default now(),
  activated_at timestamptz,
  revoked_at timestamptz
);

create function public.key_hash(p_key text) returns text
language sql immutable set search_path = public, extensions as $$
  select encode(digest(upper(regexp_replace(coalesce(p_key, ''), '[^A-Za-z0-9]', '', 'g')), 'sha256'), 'hex')
$$;

-- The signed-in user binds a key to this device. A key issued for someone else, a key
-- in use on another device or a revoked key is refused.
create function public.activate_key(p_key text, p_device text) returns json
language plpgsql security definer set search_path = public as $$
declare
  found public.license_keys;
begin
  if auth.uid() is null or not public.is_active() then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  if coalesce(length(p_device), 0) not between 8 and 80 then
    raise exception 'Некорректное устройство.' using errcode = '22023';
  end if;
  select * into found from public.license_keys where key_hash = public.key_hash(p_key) for update;
  if found.id is null or found.revoked_at is not null then
    raise exception 'Ключ не найден или отозван.' using errcode = 'P0002';
  end if;
  if found.user_id is not null and found.user_id <> auth.uid() then
    raise exception 'Ключ выдан другому аккаунту.' using errcode = '42501';
  end if;
  if found.device_id is not null and found.device_id <> p_device then
    raise exception 'Ключ уже используется на другом устройстве. Попросите админа отвязать его.'
      using errcode = '42501';
  end if;
  update public.license_keys
     set user_id = auth.uid(), device_id = p_device, activated_at = coalesce(activated_at, now())
   where id = found.id;
  return public.session_status(p_device);
end $$;

-- What the app checks at start and every few minutes.
create function public.session_status(p_device text) returns json
language sql stable security definer set search_path = public as $$
  select json_build_object(
    'user_id', p.id,
    'email', p.email,
    'display_name', p.display_name,
    'role', p.role,
    'blocked', p.blocked,
    'licensed', exists (
      select 1 from public.license_keys k
       where k.user_id = p.id and k.device_id = p_device and k.revoked_at is null
    )
  )
  from public.profiles p where p.id = auth.uid()
$$;

-- ---------- admin actions ----------

create function public.require_admin() returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'Доступно только админу.' using errcode = '42501';
  end if;
end $$;

-- A new key, returned once in plain text. Without a user the first account to activate
-- it takes it.
create function public.admin_issue_key(p_user uuid default null, p_note text default '')
returns text
language plpgsql security definer set search_path = public, extensions as $$
declare
  raw text := upper(encode(gen_random_bytes(10), 'hex'));
  pretty text;
begin
  perform public.require_admin();
  pretty := 'ALF-' || substr(raw, 1, 5) || '-' || substr(raw, 6, 5) || '-'
            || substr(raw, 11, 5) || '-' || substr(raw, 16, 5);
  insert into public.license_keys (key_hash, key_hint, user_id, note)
  values (public.key_hash(pretty), right(raw, 4), p_user, left(coalesce(p_note, ''), 200));
  return pretty;
end $$;

create function public.admin_revoke_key(p_id uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  update public.license_keys set revoked_at = coalesce(revoked_at, now()) where id = p_id;
end $$;

-- Lets the key be activated on another device (a new computer).
create function public.admin_unbind_key(p_id uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  update public.license_keys set device_id = null where id = p_id;
end $$;

create function public.admin_set_role(p_user uuid, p_role text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  if p_user = auth.uid() then
    raise exception 'Свою роль менять нельзя.' using errcode = '42501';
  end if;
  if p_role not in ('user', 'admin') then
    raise exception 'Неизвестная роль.' using errcode = '22023';
  end if;
  update public.profiles set role = p_role where id = p_user;
end $$;

create function public.admin_set_blocked(p_user uuid, p_blocked boolean) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  if p_user = auth.uid() then
    raise exception 'Себя заблокировать нельзя.' using errcode = '42501';
  end if;
  update public.profiles set blocked = p_blocked where id = p_user;
end $$;

create function public.admin_set_name(p_user uuid, p_name text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  update public.profiles set display_name = left(coalesce(p_name, ''), 80) where id = p_user;
end $$;

-- ---------- shared CRM ----------

create table public.crm_contacts (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  crm text not null check (crm in ('instagram', 'imessage')),
  name text not null default '',
  statuses jsonb not null default '[]',
  channels jsonb not null default '[]',
  notes text not null default '',
  last_contact_at timestamptz,
  next_action text not null default '',
  next_action_at timestamptz,
  earned numeric not null default 0,
  potential numeric not null default 0,
  -- In the trash since; null for live contacts.
  deleted_at timestamptz,
  -- Deleted for good: the row stays empty so every copy of the app learns about it.
  purged boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid()
);
create index crm_contacts_owner_updated on public.crm_contacts (owner_id, updated_at);
create index crm_contacts_updated on public.crm_contacts (updated_at);

-- One row per (owner, CRM): the status list in display order.
create table public.crm_status_sets (
  owner_id uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  crm text not null check (crm in ('instagram', 'imessage')),
  items jsonb not null default '[]',
  updated_at timestamptz not null default now(),
  updated_by uuid default auth.uid(),
  primary key (owner_id, crm)
);

-- The server sets the change time and author; a contact never changes owner.
create function public.crm_touch() returns trigger
language plpgsql set search_path = public as $$
begin
  new.updated_at := clock_timestamp();
  new.updated_by := auth.uid();
  if tg_op = 'UPDATE' then
    new.owner_id := old.owner_id;
  end if;
  return new;
end $$;

create trigger crm_contacts_touch before insert or update on public.crm_contacts
  for each row execute function public.crm_touch();
create trigger crm_status_sets_touch before insert or update on public.crm_status_sets
  for each row execute function public.crm_touch();

-- ---------- access rules ----------

alter table public.profiles enable row level security;
alter table public.license_keys enable row level security;
alter table public.crm_contacts enable row level security;
alter table public.crm_status_sets enable row level security;

create policy profiles_read on public.profiles for select to authenticated
  using (id = auth.uid() or public.is_admin());

create policy keys_read on public.license_keys for select to authenticated
  using (user_id = auth.uid() or public.is_admin());

create policy crm_read on public.crm_contacts for select to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.is_admin());
create policy crm_insert on public.crm_contacts for insert to authenticated
  with check ((owner_id = auth.uid() and public.is_active()) or public.is_admin());
create policy crm_update on public.crm_contacts for update to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.is_admin())
  with check ((owner_id = auth.uid() and public.is_active()) or public.is_admin());

create policy statuses_read on public.crm_status_sets for select to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.is_admin());
create policy statuses_insert on public.crm_status_sets for insert to authenticated
  with check ((owner_id = auth.uid() and public.is_active()) or public.is_admin());
create policy statuses_update on public.crm_status_sets for update to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.is_admin())
  with check ((owner_id = auth.uid() and public.is_active()) or public.is_admin());

-- Rows are never deleted by the app: the trash and purge are updates (see `purged`).
revoke all on public.profiles, public.license_keys, public.crm_contacts, public.crm_status_sets
  from anon, authenticated;
grant select on public.profiles, public.license_keys to authenticated;
grant select, insert, update on public.crm_contacts, public.crm_status_sets to authenticated;

revoke execute on all functions in schema public from anon, public;
grant execute on function
  public.is_admin(),
  public.is_active(),
  public.activate_key(text, text),
  public.session_status(text),
  public.admin_issue_key(uuid, text),
  public.admin_revoke_key(uuid),
  public.admin_unbind_key(uuid),
  public.admin_set_role(uuid, text),
  public.admin_set_blocked(uuid, boolean),
  public.admin_set_name(uuid, text)
  to authenticated;
