# Mirror Engine — Multi-Tenant Design

Turning Mirror Engine from a single-tenant internal tool into a multi-tenant platform where **each user manages their own isolated master + follower accounts**, and an **admin** can see everything.

Status: **design / proposal** (not yet implemented). Auth decision: **Supabase Auth**.

---

## 1. Goals

- Any user can sign up, log in, and manage **their own** master + follower accounts.
- A user's data (accounts, trades, positions, alerts) is **fully isolated** from other users.
- "One master" becomes **one master per user** (not a global constraint).
- Each user's copy engine runs **independently** — a user's master fills copy only into *that user's* followers.
- An **admin** role can view (and if needed manage) all users and their data.
- API keys are handled securely (encrypted at rest; frontend never holds the DB service key).

### Non-goals (for v1)
- Billing / subscriptions.
- Cross-user strategy sharing.
- Follower "catch-up" reconciliation (tracked separately).

---

## 2. Roles

| Role | Capability |
|------|------------|
| **user** | Full CRUD on their own accounts; sees only their own trades/positions/alerts/dashboard. |
| **admin** | Read across all users; manage users; view global system health. (Write on others' data optional/guarded.) |

Role stored on the user profile (`profiles.role`, default `user`).

---

## 3. Authentication — Supabase Auth

- Use Supabase Auth (email/password to start; social/OTP later).
- The frontend authenticates with Supabase, receives a **JWT**.
- Every backend API call sends `Authorization: Bearer <jwt>`.
- The backend **verifies the JWT** (Supabase JWKS / shared secret) and extracts `user_id` + `role`.
- The frontend **no longer uses the service key** — all privileged DB access goes through the backend.

**New table `profiles`** (1:1 with `auth.users`):
```sql
create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  role text not null default 'user' check (role in ('user','admin')),
  created_at timestamptz default now()
);
```
A trigger on `auth.users` insert creates the matching `profiles` row.

---

## 4. Data model changes

Add an **`owner_id uuid references auth.users(id)`** to every tenant-scoped table:

- `accounts.owner_id`
- `trades.owner_id`
- `trade_copies.owner_id`
- `positions.owner_id`
- `alerts.owner_id`

Indexes on `owner_id` for each. Backfill existing rows to a designated owner during migration (see §9).

### "One master per user"
Replace the global master-uniqueness check with a per-owner one:
```sql
create unique index one_master_per_owner
  on accounts(owner_id) where is_master = true;
```

### Row Level Security (RLS)
RLS is currently **disabled**. Enable it and add policies so users see only their rows, admins see all:

```sql
alter table accounts enable row level security;

create policy owner_rw on accounts
  for all using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

create policy admin_all on accounts
  for all using (
    exists (select 1 from profiles p where p.id = auth.uid() and p.role = 'admin')
  );
```
Repeat for `trades`, `trade_copies`, `positions`, `alerts`.

> Note: the **backend** uses a service role for the trading engine (WS listeners, copy execution) and must always filter by `owner_id` in code. RLS protects the **user-facing** path (frontend → backend with user JWT, or frontend → Supabase directly if ever used).

---

## 5. API layer changes

- Add JWT auth middleware/dependency in FastAPI that resolves `current_user` (id, role) from the bearer token.
- Every endpoint scopes queries to `current_user.id` (admins may pass/act across owners).
- Account create/update sets `owner_id = current_user.id`.
- Reject cross-owner access (404/403) even if an id is guessed.

Example dependency:
```python
async def get_current_user(authorization: str = Header(...)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    claims = verify_supabase_jwt(token)   # validates signature + exp
    return User(id=claims["sub"], role=lookup_role(claims["sub"]))
```

---

## 6. Backend engine — per-user copy groups (the hard part)

Today there is **one global master listener** and a copy engine that reads all active followers. Multi-tenant needs each user's group to run independently.

### Listener manager
- Replace the singleton `trade_listener` with a **`ListenerManager`** that runs **one master WebSocket listener per user** who has an active master.
- On startup: load all users with an active master, start a listener for each.
- On account changes (add/promote/pause/delete master): start/stop that user's listener.

### Owner-tagged events
- When a master fill / order event is pushed to Redis, tag it with `owner_id`.
- The copy engine, when processing an event, queries followers **`where owner_id = <event owner> and is_master = false and status = active`** — never crossing tenants.
- `ordermap` keys namespaced per owner if needed: `ordermap:{owner_id}:{master_order_id}`.

### Concurrency & limits
- N users → N master WS connections + follower REST clients. Connection manager already pools clients; ensure it keys by account id (unique globally) — fine.
- Consider per-process listener caps and horizontal scaling (multiple worker processes each owning a shard of users) if user count grows.

---

## 7. Admin

- Admin dashboard: list users, their account counts, status, recent activity, system health.
- Admin API endpoints (guarded by `role == admin`) that can query across `owner_id`.
- Optional: impersonate/read a specific user's dashboard for support.

---

## 8. Security

- **Encrypt API keys at rest** — store `api_key`/`api_secret` encrypted (e.g. AES-GCM with a server-side key from env/secrets manager); decrypt only in the backend when creating a DeltaClient. Never return secrets to the frontend (already masked in responses).
- **Remove the service key from the frontend** — the browser currently reads Supabase directly with the service key; switch to backend-mediated access with the user JWT.
- **Whitelisting note** — all outbound Delta calls originate from the server IP, so users still whitelist the same server IP (document this in onboarding).
- Rate limiting on auth + account-mutation endpoints.
- Audit log of sensitive actions (account create/delete, promote, key changes).

---

## 9. Migration (single-tenant → multi-tenant)

1. Create `profiles`, add `owner_id` columns (nullable first).
2. Create the first admin user; **backfill all existing `accounts`/`trades`/… `owner_id`** to that admin (or a designated owner) so current data keeps working.
3. Make `owner_id` NOT NULL; add indexes + the per-owner master unique index.
4. Enable RLS + policies.
5. Deploy the JWT-aware backend + ListenerManager.
6. Ship login/signup UI; cut the frontend over from service key to JWT.

Each step is reversible/independent; RLS is enabled only after backfill to avoid locking out existing data.

---

## 10. Frontend changes

- **Login / Signup** pages (Supabase Auth client).
- **Auth context/provider**; attach JWT to all API calls; redirect unauthenticated users.
- All existing pages (Positions, Accounts, Trades, Alerts) automatically scope to the logged-in user via the backend.
- **Admin area** (visible only to admins): user list + drill-down.
- Sign-out, session refresh handling.

---

## 11. Phasing / roadmap

1. **Phase 1 — Auth + isolation**: `profiles`, Supabase Auth, `owner_id` + RLS, JWT backend dependency, login/signup, scoped APIs. (Foundation; data isolation works, still one shared engine.)
2. **Phase 2 — Per-user engine**: ListenerManager (one master WS per user), owner-tagged copy events, per-owner follower routing.
3. **Phase 3 — Admin**: cross-user dashboard + admin APIs.
4. **Phase 4 — Hardening**: API-key encryption, remove frontend service key, rate limits, audit log.

---

## 12. Open questions / risks

- **Scale of concurrent master WS connections** — how many users/masters do we expect? Beyond ~dozens per process, we need a sharded multi-worker design.
- **API-key custody** — are we comfortable holding users' Delta keys (encrypted) server-side? (Required for copy trading.)
- **Admin write scope** — should admins be able to *modify* users' accounts, or read-only?
- **Billing/limits** — any cap on followers per user?
- **Shared server IP** — every user whitelists the same server IP; fine for a hosted product but worth documenting.
