-- 007_realized_pnl_today.sql
--
-- Split today's P&L into its two halves.
--
-- today_pnl is REALISED + UNREALISED (see _sync_live_positions in
-- app/api/positions.py: it accumulates each open position's unrealized_pnl and
-- then adds the realised figure booked today). The dashboard shows today_pnl
-- and the unrealised total side by side, which quietly double-counts: the
-- second column is already inside the first, and the two cannot be added.
--
-- The sync already computes the realised half; it just threw it away after
-- folding it in. Storing it means the screen can show realised and unrealised
-- as two numbers that are genuinely separate and genuinely add up to today's
-- figure — rather than deriving one by subtracting two values that were read
-- from the exchange at different moments, which would make a column that only
-- changes when a trade closes jitter on every refresh.

alter table public.accounts
  add column if not exists realized_pnl_today numeric not null default 0;

comment on column public.accounts.realized_pnl_today is
  'P&L booked from trades CLOSED today. today_pnl = this + the unrealised '
  'total across open positions, both written by the same sync pass so the '
  'two halves always agree with the whole.';
