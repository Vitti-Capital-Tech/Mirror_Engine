-- 008_paper_accounts.sql
--
-- A paper FOLLOWER: a name and a balance, no credentials, no exchange.
--
-- It mirrors a real master so the copy path can be exercised against live
-- master flow with nothing at risk. Everything downstream of an account -- the
-- engine, the reconciler, the position monitor, the daily report, the alerting
-- -- reaches it through one object, so the whole mechanism is a PaperClient
-- with DeltaClient's method surface, chosen in app/services/client_factory.py.
-- This migration is the state that client needs.
--
-- `environment` IS THE FLAG. A separate boolean would have been a second source
-- of truth for one fact, which is how the two disagree later -- and the person
-- adding the account already picks demo or live from exactly this field. See
-- app/services/paper_client.is_paper, which is the one place it is read, and
-- which demands the exact word rather than a truthy value: a PAPER account
-- handed a real client just fails loudly on its missing keys, but a REAL
-- account handed a paper client has its orders quietly not placed while the
-- engine believes they were.
--
-- Safe to run more than once.

-- 1. Let the column hold it. The CHECK constraint listed only demo and live, so
--    an insert with 'paper' was rejected by Postgres before any of the
--    application's own guards were reached.
alter table public.accounts
  drop constraint if exists accounts_environment_check;

alter table public.accounts
  add constraint accounts_environment_check
  check (environment in ('demo', 'live', 'paper'));

-- 2. A paper account has no credentials to store. api_key and api_secret were
--    NOT NULL, so the row could not be written at all; and storing empty
--    strings instead would have left every reader unable to tell "no keys by
--    design" from "keys lost in a bad write".
alter table public.accounts alter column api_key    drop not null;
alter table public.accounts alter column api_secret drop not null;

-- A real account must still have them. The NOT NULL said this for every row;
-- this says it for the rows it is actually true of, which is better than what
-- it replaces -- the application validator can be bypassed by a direct write,
-- this cannot.
alter table public.accounts
  drop constraint if exists accounts_credentials_unless_paper;

alter table public.accounts
  add constraint accounts_credentials_unless_paper
  check (
    environment = 'paper'
    or (api_key is not null and api_secret is not null)
  );

-- A paper MASTER would drive real followers off trades that never happened,
-- which is the one thing in this system that could lose somebody else's money.
-- Refused in the API, in PaperClient's constructor, in
-- CopyEngine._get_master_client -- and here, where no code path can get round it.
alter table public.accounts
  drop constraint if exists accounts_paper_is_never_master;

alter table public.accounts
  add constraint accounts_paper_is_never_master
  check (environment <> 'paper' or is_master = false);

comment on column public.accounts.environment is
  'demo | live | paper. paper is a follower that trades nothing: its orders go '
  'to paper_orders and its book to paper_positions, and it carries no API '
  'credentials. Read ONLY through app/services/paper_client.is_paper.';


-- 3. The paper book.
--
-- IN THE DATABASE, NOT IN MEMORY. The backend runs under uvicorn --reload and
-- reloads on every deploy; a paper book held in the process would vanish mid
-- trading day and take the thing being observed with it.
--
-- order_id is text because Delta's ids are integers and these are deliberately
-- NOT in Delta's range (PaperClient starts them above 9e9, clear of Delta's
-- ~1.7e9 sequence) -- the ledger and the daily report pair orders by id, and an
-- id that collided would pair a paper order with a real one.
create table if not exists public.paper_orders (
    id            uuid primary key default uuid_generate_v4(),
    account_id    uuid not null references public.accounts(id) on delete cascade,
    order_id      text not null,
    symbol        text not null,
    side          text not null check (side in ('buy', 'sell')),
    size          numeric not null default 0,
    unfilled_size numeric not null default 0,
    order_type    text,
    limit_price   text,
    stop_price    text,
    stop_order_type     text,
    stop_trigger_method text,
    reduce_only   boolean not null default false,
    client_order_id text,
    -- The same vocabulary Delta uses, so the report and the ledger need no
    -- translation layer: open | pending | closed | cancelled, with `reason`
    -- carrying fill / cancel / noop / no_price.
    state         text not null default 'open',
    reason        text,
    average_fill_price text,
    cancellation_reason text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (account_id, order_id)
);

-- The report reads a day at a time, per account; the engine reads what is
-- resting right now.
create index if not exists idx_paper_orders_acct_created
  on public.paper_orders (account_id, created_at desc);
create index if not exists idx_paper_orders_acct_state
  on public.paper_orders (account_id, state);

comment on table public.paper_orders is
  'Every order a PAPER account has placed. Written only by '
  'app/services/paper_client.py; read back as this account''s order history by '
  'the daily report, so a paper follower is graded exactly like a real one.';


-- 4. The paper position book.
--
-- Derived state, kept rather than recomputed. Summing every filled order would
-- be self-consistent but would never forget an expired contract: Delta's BTC
-- options expire daily and an expired leg is never closed by an order, it just
-- ceases to exist. A position row can be deleted; a sum over history cannot.
create table if not exists public.paper_positions (
    id          uuid primary key default uuid_generate_v4(),
    account_id  uuid not null references public.accounts(id) on delete cascade,
    symbol      text not null,
    -- Signed, like Delta's: positive is long, negative is short. The engine
    -- reads the sign to decide which side closes a leg, so storing an absolute
    -- size with a separate side column would be one more thing to disagree.
    size        numeric not null default 0,
    entry_price numeric not null default 0,
    updated_at  timestamptz not null default now(),
    unique (account_id, symbol)
);

create index if not exists idx_paper_positions_acct
  on public.paper_positions (account_id);

comment on table public.paper_positions is
  'A PAPER account''s open positions, moved by each simulated fill. Weighted '
  'average entry while adding, untouched while reducing, reset on a flip -- '
  'the same arithmetic a venue uses, because the sizing code reads it back.';


-- 5. Same per-tenant isolation as every other table (see 002). The backend uses
--    the SERVICE ROLE and bypasses all of this; it matters for anything that
--    reaches the database with a user's own JWT, such as the frontend's
--    Supabase client. Neither table carries owner_id, so ownership is followed
--    through the account the row belongs to.
--
--    WRITTEN OUT IN FULL, not generated in a `do $$` loop like 002 does. 002
--    loops because it covers five tables; this covers two, so the loop bought
--    nothing -- and it hid the statements from Supabase's SQL checker, which
--    reads queries statically and cannot see inside `execute format(...)`. It
--    therefore warned that these tables were being created WITHOUT RLS, which
--    was untrue but unverifiable by reading. Plain statements say what they do.
--
--    public.is_admin() comes from 002_row_level_security.sql; run that first if
--    this errors on it.

alter table public.paper_orders    enable row level security;
alter table public.paper_positions enable row level security;

drop policy if exists paper_orders_owner_select on public.paper_orders;
create policy paper_orders_owner_select on public.paper_orders
  for select using (
    exists (select 1 from public.accounts a
            where a.id = paper_orders.account_id
              and (a.owner_id = auth.uid() or public.is_admin())));

drop policy if exists paper_orders_owner_write on public.paper_orders;
create policy paper_orders_owner_write on public.paper_orders
  for all using (
    exists (select 1 from public.accounts a
            where a.id = paper_orders.account_id
              and (a.owner_id = auth.uid() or public.is_admin())));

drop policy if exists paper_positions_owner_select on public.paper_positions;
create policy paper_positions_owner_select on public.paper_positions
  for select using (
    exists (select 1 from public.accounts a
            where a.id = paper_positions.account_id
              and (a.owner_id = auth.uid() or public.is_admin())));

drop policy if exists paper_positions_owner_write on public.paper_positions;
create policy paper_positions_owner_write on public.paper_positions
  for all using (
    exists (select 1 from public.accounts a
            where a.id = paper_positions.account_id
              and (a.owner_id = auth.uid() or public.is_admin())));
