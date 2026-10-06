-- Anyone may sign up; an account works only after it activates an access key.
-- A key now belongs to the account, not to a computer: once activated, the account signs
-- in on any device without a key.

-- Active = not blocked and holds a key that is not revoked. Only an active account reads
-- and writes its own CRM (the access rules of 0001 call this function).
create or replace function public.is_active() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.profiles p
     where p.id = auth.uid() and not p.blocked
       and exists (
         select 1 from public.license_keys k
          where k.user_id = p.id and k.activated_at is not null and k.revoked_at is null
       )
  )
$$;

-- p_device is kept for older apps and no longer used.
create or replace function public.activate_key(p_key text, p_device text default null)
returns json
language plpgsql security definer set search_path = public as $$
declare
  found public.license_keys;
begin
  if auth.uid() is null
     or not exists (select 1 from public.profiles where id = auth.uid() and not blocked) then
    raise exception 'Аккаунт заблокирован или не найден.' using errcode = '42501';
  end if;
  select * into found from public.license_keys where key_hash = public.key_hash(p_key) for update;
  if found.id is null or found.revoked_at is not null then
    raise exception 'Ключ не найден или отозван.' using errcode = 'P0002';
  end if;
  if found.user_id is not null and found.user_id <> auth.uid() then
    raise exception 'Ключ уже используется другим аккаунтом.' using errcode = '42501';
  end if;
  update public.license_keys
     set user_id = auth.uid(), activated_at = coalesce(activated_at, now())
   where id = found.id;
  return public.session_status(p_device);
end $$;

create or replace function public.session_status(p_device text default null) returns json
language sql stable security definer set search_path = public as $$
  select json_build_object(
    'user_id', p.id,
    'email', p.email,
    'display_name', p.display_name,
    'role', p.role,
    'blocked', p.blocked,
    'licensed', exists (
      select 1 from public.license_keys k
       where k.user_id = p.id and k.activated_at is not null and k.revoked_at is null
    )
  )
  from public.profiles p where p.id = auth.uid()
$$;

-- Frees a key from its account: the account loses access, the key can be activated again.
create or replace function public.admin_unbind_key(p_id uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  update public.license_keys set user_id = null, device_id = null, activated_at = null
   where id = p_id;
end $$;

-- Keys activated under 0001 were bound to a device: they now belong to the account.
update public.license_keys set device_id = null where device_id is not null;

revoke execute on function public.activate_key(text, text), public.session_status(text)
  from anon, public;
grant execute on function public.activate_key(text, text), public.session_status(text)
  to authenticated;
