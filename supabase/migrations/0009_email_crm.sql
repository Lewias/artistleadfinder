-- The email CRM («Почта»): contacts and status lists with crm = 'email'.
-- Until this runs, the app keeps the email CRM on each computer and syncs the other two.

alter table public.crm_contacts drop constraint if exists crm_contacts_crm_check;
alter table public.crm_contacts
  add constraint crm_contacts_crm_check check (crm in ('instagram', 'imessage', 'email'));

alter table public.crm_status_sets drop constraint if exists crm_status_sets_crm_check;
alter table public.crm_status_sets
  add constraint crm_status_sets_crm_check check (crm in ('instagram', 'imessage', 'email'));
