-- 003_partial_fill_accounting.sql
--
-- A copy that filled only PART of its proportional target was being stored as
-- status 'filled' with quantity = whatever happened to fill. The trade history
-- therefore showed a green FILLED for a follower that ended up holding a
-- fraction of the master's position (observed live 2026-08-12 on
-- C-BTC-65600-120826: master exited 3000 lots, the follower leg recorded
-- "filled 1" against a proportional target of ~34).
--
-- Two changes so the history can be reconciled against the fills:
--   1. 'partial' becomes a legal copy status.
--   2. requested_quantity keeps the proportional TARGET, so "1 of 34" is
--      answerable from the row itself instead of being re-derived from balances
--      that have since moved.
--
-- Both are additive: existing rows keep their status and get a NULL target.

alter table public.trade_copies
  drop constraint if exists trade_copies_status_check;

alter table public.trade_copies
  add constraint trade_copies_status_check
  check (status in ('pending', 'filled', 'partial', 'failed', 'skipped', 'retrying'));

alter table public.trade_copies
  add column if not exists requested_quantity numeric;

comment on column public.trade_copies.requested_quantity is
  'Proportional lots this copy was SUPPOSED to fill (follower target at dispatch). '
  'quantity holds what actually filled; requested_quantity < quantity is impossible, '
  'and requested_quantity > quantity means the follower is under-sized vs the master.';
