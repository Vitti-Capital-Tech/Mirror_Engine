-- =====================================================
-- Multi-tenant + Auth migration (Phase 1)
-- Additive & non-breaking: nullable owner_id, new tables.
-- RLS is NOT enabled here — do that only AFTER backfilling owner_id
-- (see 002_enable_rls.sql) so the live single-tenant app keeps working.
-- Run in: Supabase Dashboard → SQL Editor
-- =====================================================

-- 1. Profiles (1:1 with auth.users) — holds role
create table if not exists profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    email text,
    role text not null default 'user' check (role in ('user', 'admin')),
    created_at timestamptz default now()
);

-- Auto-create a profile row when a new auth user signs up
create or replace function handle_new_user()
returns trigger as $$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function handle_new_user();

-- 2. Email-OTP 2FA storage
create table if not exists auth_otps (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid references auth.users(id) on delete cascade,
    code_hash text not null,
    purpose text not null default 'login_2fa',
    expires_at timestamptz not null,
    attempts int not null default 0,
    consumed_at timestamptz,
    created_at timestamptz default now()
);
create index if not exists idx_auth_otps_user on auth_otps(user_id);

-- 3. owner_id on all tenant-scoped tables (nullable for now; backfill later)
alter table accounts     add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table trades       add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table trade_copies add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table positions    add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table alerts       add column if not exists owner_id uuid references auth.users(id) on delete cascade;

create index if not exists idx_accounts_owner     on accounts(owner_id);
create index if not exists idx_trades_owner       on trades(owner_id);
create index if not exists idx_trade_copies_owner on trade_copies(owner_id);
create index if not exists idx_positions_owner    on positions(owner_id);
create index if not exists idx_alerts_owner       on alerts(owner_id);

-- Also make room for the allocated_balance column used by the ratio feature
alter table accounts add column if not exists allocated_balance numeric;
