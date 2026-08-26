-- 004_order_stage_history.sql
--
-- The history tables were fed by ONE path. process_fill (master fill events)
-- writes trades + trade_copies; _mirror_place (master resting-order events)
-- writes only the Redis ledger. But resting orders are how this engine actually
-- mirrors now, so the Trades page showed exits and almost no entries: 30 of the
-- first 53 trades on record had no follower leg at all, and the follower's
-- positions on 2026-08-26 were opened with zero rows to show for them.
--
-- Worse, nothing observed the FOLLOWER's fills. Followers connect with an
-- on_position callback and no on_fill, so when a mirrored resting order filled,
-- no row was ever updated — the ledger said "placed" forever.
--
-- Two additions so both halves can be recorded and reconciled:
--
--   1. trade_copies.master_order_id — the master order this leg mirrors. The
--      link that lets a FOLLOWER fill (which knows only its own order id) find
--      its row, and lets the order path find a leg it already recorded without
--      depending on trades.id.
--
--   2. An index on follower_order_id, which is the lookup key on every follower
--      fill. Unindexed it was a full scan per fill.
--
-- Order-stage rows are written to `trades` with master_trade_id prefixed
-- 'ord:<order_id>' so they cannot collide with the fill-stage row for the same
-- order id ('<order_id>'). The two are genuinely different events — "the master
-- rested this order" and "the master's order filled" — and the fill path's
-- duplicate guard keys on master_trade_id, so sharing the id would make the
-- order row swallow the fill.

alter table public.trade_copies
  add column if not exists master_order_id varchar(255);

comment on column public.trade_copies.master_order_id is
  'The master ORDER id this leg mirrors. Set by the resting-order mirror path; '
  'null for legs created from a master fill, which reach their row via trade_id.';

create index if not exists idx_trade_copies_master_order_id
  on public.trade_copies (master_order_id);

create index if not exists idx_trade_copies_follower_order_id
  on public.trade_copies (follower_order_id);
