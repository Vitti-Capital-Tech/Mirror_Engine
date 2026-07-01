-- 002_row_level_security.sql
-- Defense-in-depth: enforce per-tenant isolation at the database layer.
--
-- The backend uses the Supabase SERVICE ROLE key, which BYPASSES RLS — so these
-- policies do NOT affect the API. They protect against any direct access with a
-- user's anon/JWT credentials (e.g. the frontend Supabase client), ensuring a
-- user can only ever read/write rows they own, while admins can see everything.
--
-- Safe to run multiple times (drops policies before recreating).

-- Helper: is the current JWT an admin?
create or replace function public.is_admin()
returns boolean as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid() and p.role = 'admin'
  );
$$ language sql stable security definer;

-- ---------------------------------------------------------------------------
-- profiles: a user can read/update their own row; admins see all.
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;

drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles
  for select using (id = auth.uid() or public.is_admin());

drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles
  for update using (id = auth.uid() or public.is_admin());

-- ---------------------------------------------------------------------------
-- Owner-scoped tables: owner_id = auth.uid(), or admin.
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
  tables text[] := array['accounts', 'trades', 'trade_copies', 'positions', 'alerts'];
begin
  foreach t in array tables loop
    execute format('alter table public.%I enable row level security;', t);

    execute format('drop policy if exists %I_owner_select on public.%I;', t, t);
    execute format(
      'create policy %I_owner_select on public.%I for select using (owner_id = auth.uid() or public.is_admin());',
      t, t);

    execute format('drop policy if exists %I_owner_insert on public.%I;', t, t);
    execute format(
      'create policy %I_owner_insert on public.%I for insert with check (owner_id = auth.uid() or public.is_admin());',
      t, t);

    execute format('drop policy if exists %I_owner_update on public.%I;', t, t);
    execute format(
      'create policy %I_owner_update on public.%I for update using (owner_id = auth.uid() or public.is_admin());',
      t, t);

    execute format('drop policy if exists %I_owner_delete on public.%I;', t, t);
    execute format(
      'create policy %I_owner_delete on public.%I for delete using (owner_id = auth.uid() or public.is_admin());',
      t, t);
  end loop;
end $$;

-- auth_otps: never exposed to end users — only the service role touches it.
alter table public.auth_otps enable row level security;
-- (no policies => no anon/JWT access at all; service role still bypasses)
