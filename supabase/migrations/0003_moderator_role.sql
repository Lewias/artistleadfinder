-- Three roles:
--   admin      everything: users, keys, roles and every CRM. Set only on the server
--              (make-admin.sh or SQL), never from the app.
--   moderator  the app as usual plus reading and changing every CRM.
--   user       the app as usual, only their own CRM.

alter table public.profiles drop constraint if exists profiles_role_check;
alter table public.profiles
  add constraint profiles_role_check check (role in ('user', 'moderator', 'admin'));

-- Sees and changes every CRM: the admin, or a moderator whose account has access.
create function public.sees_all_crm() returns boolean
language sql stable security definer set search_path = public as $$
  select public.is_admin() or exists (
    select 1 from public.profiles
     where id = auth.uid() and role = 'moderator' and public.is_active()
  )
$$;

-- Owner names next to every contact: a moderator reads the profiles too.
drop policy profiles_read on public.profiles;
create policy profiles_read on public.profiles for select to authenticated
  using (id = auth.uid() or public.sees_all_crm());

drop policy crm_read on public.crm_contacts;
drop policy crm_insert on public.crm_contacts;
drop policy crm_update on public.crm_contacts;
create policy crm_read on public.crm_contacts for select to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());
create policy crm_insert on public.crm_contacts for insert to authenticated
  with check ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());
create policy crm_update on public.crm_contacts for update to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm())
  with check ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());

drop policy statuses_read on public.crm_status_sets;
drop policy statuses_insert on public.crm_status_sets;
drop policy statuses_update on public.crm_status_sets;
create policy statuses_read on public.crm_status_sets for select to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());
create policy statuses_insert on public.crm_status_sets for insert to authenticated
  with check ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());
create policy statuses_update on public.crm_status_sets for update to authenticated
  using ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm())
  with check ((owner_id = auth.uid() and public.is_active()) or public.sees_all_crm());

-- From the app the admin makes users and moderators only; the admin role stays on the server.
create or replace function public.admin_set_role(p_user uuid, p_role text) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform public.require_admin();
  if p_user = auth.uid() then
    raise exception 'Свою роль менять нельзя.' using errcode = '42501';
  end if;
  if p_role not in ('user', 'moderator') then
    raise exception 'Можно назначить только пользователя или модератора.' using errcode = '22023';
  end if;
  if exists (select 1 from public.profiles where id = p_user and role = 'admin') then
    raise exception 'Роль админа меняется только на сервере.' using errcode = '42501';
  end if;
  update public.profiles set role = p_role where id = p_user;
end $$;

revoke execute on function public.sees_all_crm() from anon, public;
grant execute on function public.sees_all_crm() to authenticated;
