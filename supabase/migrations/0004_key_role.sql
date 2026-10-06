-- A key carries the role it grants: user or moderator. The account takes that role when it
-- activates the key, or at once when the key is issued for an account. An admin keeps the
-- admin role whatever key they hold.

alter table public.license_keys
  add column role text not null default 'user' check (role in ('user', 'moderator'));

drop function public.admin_issue_key(uuid, text);
create function public.admin_issue_key(
  p_user uuid default null, p_note text default '', p_role text default 'user'
) returns text
language plpgsql security definer set search_path = public, extensions as $$
declare
  raw text := upper(encode(gen_random_bytes(10), 'hex'));
  pretty text;
begin
  perform public.require_admin();
  if p_role not in ('user', 'moderator') then
    raise exception 'Ключ даёт роль пользователя или модератора.' using errcode = '22023';
  end if;
  pretty := 'ALF-' || substr(raw, 1, 5) || '-' || substr(raw, 6, 5) || '-'
            || substr(raw, 11, 5) || '-' || substr(raw, 16, 5);
  insert into public.license_keys (key_hash, key_hint, user_id, note, role)
  values (public.key_hash(pretty), right(raw, 4), p_user, left(coalesce(p_note, ''), 200), p_role);
  if p_user is not null then
    update public.profiles set role = p_role where id = p_user and role <> 'admin';
  end if;
  return pretty;
end $$;

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
  update public.profiles set role = found.role where id = auth.uid() and role <> 'admin';
  return public.session_status(p_device);
end $$;

revoke execute on function public.admin_issue_key(uuid, text, text) from anon, public;
grant execute on function public.admin_issue_key(uuid, text, text) to authenticated;
