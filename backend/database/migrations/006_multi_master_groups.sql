-- 006_multi_master_groups.sql
--
-- Several masters under one login, each with its own followers.
--
-- Until now the shape was one master per owner, and a follower was tied to its
-- master IMPLICITLY — by sharing an owner_id. Nothing recorded which master a
-- follower actually mirrors, because with one master per owner there was only
-- one answer.
--
-- That assumption is what breaks. Two masters under the same email would both
-- resolve "my followers" to every follower that email owns, so each master's
-- fills would copy into the other's accounts. The groups have to be explicit.
--
-- So: a follower points at its master. Groups are then master_id, not owner_id,
-- and two groups under one login cannot see each other.
--
-- Limits, per the desk: at most 5 masters per owner, at most 2 followers per
-- master. Both are enforced in the API (accounts.py) rather than here — they
-- need a readable error, and a CHECK constraint cannot count sibling rows
-- without a trigger.

alter table public.accounts
  add column if not exists master_id uuid references public.accounts(id) on delete set null;

comment on column public.accounts.master_id is
  'Followers only: the master account this follower mirrors. Masters have NULL. '
  'Groups are defined by this, not by owner_id — one owner may hold up to 5 '
  'independent master/follower groups.';

create index if not exists accounts_master_id_idx on public.accounts(master_id);

-- Backfill is unambiguous precisely because the old rule held: every owner had
-- at most one master, so each existing follower has exactly one candidate.
-- Doing it here rather than leaving NULLs means copying keeps working the
-- moment the new code ships, instead of every follower looking unassigned.
update public.accounts f
   set master_id = m.id
  from public.accounts m
 where f.is_master = false
   and f.master_id is null
   and m.is_master = true
   and m.owner_id = f.owner_id;

-- A master cannot point at a master, and nothing may point at itself.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'accounts_master_id_check'
  ) then
    alter table public.accounts
      add constraint accounts_master_id_check
      check (
        (is_master = true  and master_id is null)
        or
        (is_master = false and (master_id is null or master_id <> id))
      );
  end if;
end $$;
