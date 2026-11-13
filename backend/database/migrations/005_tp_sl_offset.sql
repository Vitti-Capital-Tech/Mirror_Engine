-- 005_tp_sl_offset.sql
--
-- A configurable TP/SL offset for followers, set on the MASTER account.
--
-- Followers do not mirror the master's stop prices exactly. They never have:
-- _jitter_trigger offsets each one by a deterministic ±(0..20), derived from a
-- hash of the follower id and the trigger price. Two followers therefore sit at
-- different prices and do not all trigger in the same instant, which is the
-- whole point — a pack of identical stops firing together is a worse fill for
-- everyone and a visible footprint in the book.
--
-- The problem is that 0..20 is arbitrary and invisible. Nobody chose it, it
-- cannot be tuned per desk, and on a contract trading at 2.8 an offset of 20 is
-- absurd while on one trading at 70,000 it is nothing. The desk wants to say
-- what the spread should be.
--
-- So: one number on the master, in price units. Its followers are placed either
-- side of the master's trigger — the first at +x, the second at −x — so they
-- stay spread rather than stacked. NULL keeps the existing hash jitter, which
-- is what every account has today and what they keep until someone sets this.
--
-- Deliberately on the MASTER and not on each follower: the offset describes how
-- far apart this master's copies should sit, which is one decision about one
-- group, not a setting to repeat (and keep in sync) on every follower.

alter table public.accounts
  add column if not exists tp_sl_offset numeric;

comment on column public.accounts.tp_sl_offset is
  'Master accounts only. Price offset applied to follower SL/TP triggers: the '
  'first follower sits at +offset, the second at -offset, alternating. NULL '
  'falls back to the built-in deterministic hash jitter (+/-0..20).';

-- Must be a real distance if it is set at all. 0 would stack every follower on
-- the master''s exact price, which is the thing the offset exists to prevent.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'accounts_tp_sl_offset_check'
  ) then
    alter table public.accounts
      add constraint accounts_tp_sl_offset_check
      check (tp_sl_offset is null or tp_sl_offset > 0);
  end if;
end $$;
