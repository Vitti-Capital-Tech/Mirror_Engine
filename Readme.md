# Mirror Engine

An institutional-grade, low-latency, **multi-tenant copy-trading platform for Delta Exchange India**.

Mirror Engine watches a single **Master** account and replicates its activity in real time across multiple **Follower** accounts — not just position entries, but **closes, pending limit orders, and stop / take-profit (bracket) orders too**, each scaled to the follower's capital. It handles order sizing, balance-ratio allocation, slippage tracking, and full order-lifecycle mirroring (place / edit / cancel).

It is **multi-tenant**: any user can sign up and manage their **own** master + followers in complete isolation, and an **admin console** gives platform operators a cross-tenant view of every user, account, position, trade and alert.

For deeper technical detail, see the High-Level Design at [docs/hld.md](docs/hld.md) and Low-Level Design at [docs/lld.md](docs/lld.md).

---

## How It Works

1. **Master executes** a trade (or places/edits/cancels an order) on the Master account.
2. **The engine intercepts** the event over Delta's WebSocket feed in real time — **one live listener per user's master** (so every tenant trades simultaneously).
3. **It scales the size** for each follower using a balance ratio (or a fixed multiplier), then **mirrors the action** to every active follower account concurrently — scoped to that master's owner only.

Each user may have **one master** at a time; any follower can be promoted to master (the old master is demoted automatically).

---

## What gets mirrored

| Master action | Follower behaviour |
|---------------|--------------------|
| **Opens a position** (market fill) | Opens the same position, size scaled by ratio (floored). **All-or-nothing (FOK)** — full size or skip |
| **Closes a position** (full or partial) | **Rebalances** the follower to `floor(master_remaining × ratio)` and closes only the excess (reduce-only). A small master trim never wipes a small follower |
| **Places a limit order** | Mirrors a ratio-sized limit order |
| **Places a stop / SL / TP (bracket) order** | Mirrors via Delta's bracket endpoint with the correct trigger reference (Mark/Index) |
| **Edits an order or SL/TP price/trigger** | Edits the follower's existing order in place |
| **Cancels an order** | Cancels the follower's mirrored order (with self-heal lookup if the id map is stale) |

**SL/TP jitter:** each follower's stop/target trigger price is offset by **±(10–50)**, computed **deterministically** from the follower + price — so two legs of a pair that share the same master SL/TP price land on the *same* follower price, while different followers still get different offsets (they don't all trigger at once).

**Unfilled-limit escalation:** the master trades with limit orders, so a mirrored follower limit that doesn't fill would leave the follower missing the position. If a plain limit order hasn't executed within a wait+retry window (5s × 2), the engine **cancels it and places a full-or-nothing market order** so the follower gets in — for entries and exits. Entries only escalate if the master actually holds the position (its own limit filled). SL/TP brackets are excluded (they're meant to rest until triggered).

**Protective-cancel safety:** when a stop/SL/TP cancel arrives, the engine checks the **master's** position: if the master still holds it, the cancel is genuine (user removed/edited the stop) and is propagated; if the master is flat, the leg was cancelled by an SL/TP *hit* (OCO) and the follower's own bracket is kept so it closes independently.

---

## Allocation & sizing

Set per follower (editable any time via the ✏️ edit action):

* **Auto Balance Ratio** *(recommended)* — follower size = master size × (follower balance ÷ master balance). Quantities are **floored** on opens (never over-expose) and **ceiled** on closes (combined with reduce-only, never leaves a residual).
* **Multiplier** — follower copies a fixed scale of the master size (e.g. 2×).

### Allocated Balance (testing aid)
Each account can carry an optional **Allocated Balance** that overrides its real balance *only for the ratio math*. This lets you test with very different real balances — e.g. set the master's allocated balance to `60` and a follower's to `56` so a 1-lot master trade copies as ~1 lot, while the real balances are untouched.

> Requires a one-time DB column: `ALTER TABLE accounts ADD COLUMN IF NOT EXISTS allocated_balance numeric;`

---

## Core Architecture

```text
 Master Account (Delta Exchange, WebSocket)
            │  fills + order lifecycle events
            ▼
   [Trade Listener]  ──► routes events:
            │            • market fill      → trade_events queue
            │            • limit/stop place/edit/cancel → order_events queue
            ▼
   [Redis Queues]  (trade_events, order_events)
            │
            ├──► [Copy Engine] ─► [Risk Engine] (ratio sizing, margin checks)
            │           └─► [Order Executor] ─► follower market orders (positions/closes)
            │           └─► bracket / limit place·edit·cancel ─► follower accounts
            │
            ▼
   Live position & PnL = read directly from Delta (no DB flicker)
   Socket.IO ─► Next.js dashboard (real-time)
```

---

## Execution & reliability

* **Low-latency pipeline** — the master WebSocket reader drains the socket instantly into a queue and a worker processes **fills first** (ahead of the master's resting-order churn), and copy events are handled **concurrently, ordered per symbol**. Under a high-frequency master this keeps copy latency ~1s instead of stacking up to ~8s.
* **All-or-nothing fills** — follower entries and exits use **Fill-Or-Kill** (full size or nothing, no partials). If the book can't supply the full size, it waits **5s** and retries (×2), then skips.
* **Cached master state** — master position lookups are cached briefly so a burst of cancels doesn't hammer the REST API.

## Alerts & notifications

Alert types raised: **position mismatch** (follower size ≠ master × ratio — raised on *both* master and follower, with a 30-min cooldown to prevent re-alert spam), **high slippage** (> 0.03%), and **liquidity unavailable** (a follower order couldn't fill the full size).

* **Dashboard** — Alert Feed with level filters + a notification bell.
* **Telegram** — every alert is also pushed to a Telegram group via the Bot API (configure `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`; resolutions are skipped and duplicates deduped). Leave unset to disable.

## Accounts, Auth & Multi-tenancy

* **Landing page** (`/`) — a public marketing page; logged-in users are forwarded to their dashboard.
* **Sign up / Sign in** — email + password. Optional **email-OTP 2FA** (via Resend) behind the `TWOFA_ENABLED` flag, and **Continue with Google** (Supabase OAuth). A flip-card animates between login and signup.
* **Data isolation** — every account, trade, copy, position and alert carries an `owner_id`; all API routes are scoped so a user only ever sees their own data. Postgres **Row-Level Security** (migration `002`) enforces the same at the DB layer as defense-in-depth.
* **Quick-admin login** — typing the configured passphrase (`ADMIN_MAGIC_CODE`) as the password signs straight into the shared admin account (no OTP). The admin email/password live server-side in `.env`.
* **Encryption at rest** — Delta API keys/secrets are Fernet-encrypted in the DB and decrypted transparently on use.

## Trader dashboard

* **Live Positions** (landing) — master on top, active followers below, read **directly from Delta** so it mirrors the exchange exactly. Shows **Today P&L** (realized + unrealized) and Active P&L per account. PnL is computed mark-to-market to match Delta's UI.
* **Accounts** — add / edit / pause / resume / **promote-to-master** / delete, with test-connection.
* **Trades Log** — full audit trail; expand any row for the per-follower execution breakdown (price, slippage, latency, status).
* **Alerts** — slippage, position-mismatch and connection events, with a bell notification dropdown.

## Admin console (admins only)

A dedicated console with its own navigation (regular sections are hidden):

* **Positions** — every user's live master + follower positions, grouped and styled like the trader view, with per-account Today/Active P&L, Balance and Alloc.
* **Users** — every account holder with their master, follower count, active accounts, copies and join date; regular users only.
* **All Accounts** — every master/follower across all tenants (owner, role, status, env, balance, PnL).
* **Trade Log / Alert Feed** — cross-tenant, collapsible per-user cards.

Admins are resolved from the `profiles.role` column; the first admin is set by hand in SQL, after which admins can promote/demote others.

**Responsive:** on mobile the sidebar is replaced by a bottom tab bar; tables scroll horizontally.

---

## Codebase Map

### Backend (`backend/app`)
* [core/trade_listener.py](backend/app/core/trade_listener.py) — master WebSocket handler + **`ListenerManager`** (one listener per user's master).
* [core/copy_engine.py](backend/app/core/copy_engine.py) — consumes Redis events; mirrors positions, closes, limit & bracket orders; cancel/edit sync; SL/TP jitter; owner-scoped followers.
* [core/risk_engine.py](backend/app/core/risk_engine.py) — balance-ratio / multiplier sizing (floor on open, ceil on close), margin checks.
* [core/order_executor.py](backend/app/core/order_executor.py) — async follower execution; FOK all-or-nothing entries + retries; reduce-only closes.
* [core/auth.py](backend/app/core/auth.py) — token verification, `require_admin`, owner scoping, email-OTP 2FA helpers.
* [core/crypto.py](backend/app/core/crypto.py) — Fernet encryption for API keys at rest.
* [services/delta_client.py](backend/app/services/delta_client.py) — Delta REST/WS client (HMAC-signed); orders, brackets, edits, cancels, wallet transactions.
* [api/auth.py](backend/app/api/auth.py) — signup / login / 2FA / quick-admin passphrase.
* [api/admin.py](backend/app/api/admin.py) — cross-tenant overview, users, accounts, positions, trades, alerts.
* [api/positions.py](backend/app/api/positions.py) — live positions; **Today P&L = realized (ledger) + unrealized**.
* [api/accounts.py](backend/app/api/accounts.py) — account CRUD, pause/resume, promote-to-master (encrypts secrets).
* [database/migrations/](backend/database/migrations/) — `001_multitenant_auth.sql` (profiles, OTP, owner_id), `002_row_level_security.sql`.

### Frontend (`frontend/src`)
* [app/page.tsx](frontend/src/app/page.tsx) — public landing page (role-aware redirect when logged in).
* [app/admin/](frontend/src/app/admin) — admin console (Positions, Users, All Accounts, Trades, Alerts) + guard layout.
* [app/positions/page.tsx](frontend/src/app/positions/page.tsx) — trader Live Positions.
* [components/auth/AuthCard.tsx](frontend/src/components/auth/AuthCard.tsx) — flip-card login/signup; [AuthShell.tsx](frontend/src/components/auth/AuthShell.tsx) — split-screen shell.
* [context/AuthContext.tsx](frontend/src/context/AuthContext.tsx) — session/role state (cached); [components/layout/AppShell.tsx](frontend/src/components/layout/AppShell.tsx) — route guards + chrome.
* [components/layout/MobileNav.tsx](frontend/src/components/layout/MobileNav.tsx) — mobile bottom tab bar.

---

## Running it

### Deployment (production)
The app runs split: **frontend on Vercel**, **backend + Redis on the server (Docker)**.

* **Frontend (Vercel)** — set these env vars in the Vercel project (they're baked into the client at build time), then redeploy:
  `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
* **Backend (server)** — `sudo docker compose up -d --build` from the repo root. Secrets live in `backend/.env` (see below) — never in Vercel.

> Note: the Docker image also bundles a frontend on port `3000`; when serving the UI from Vercel that copy is optional/legacy.

### Manual (local dev)

**Backend**
```bash
pip install -r backend/requirements.txt
cd backend        # configure .env (see below)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend**
```bash
cd frontend && npm install
npm run dev   # http://localhost:3000
```

### Backend `.env` (server-side secrets)
```
SUPABASE_URL=...            SUPABASE_SERVICE_KEY=...    SUPABASE_ANON_KEY=...
REDIS_URL=redis://redis:6379
DELTA_ENV=live              # or demo
ENCRYPTION_KEY=...          # optional; derived from service key if unset
# Auth / 2FA
TWOFA_ENABLED=false         # true → email-OTP 2FA on login
RESEND_API_KEY=...          RESEND_FROM="Mirror Engine <noreply@yourdomain>"
# Quick-admin passphrase login
ADMIN_MAGIC_CODE=...        ADMIN_EMAIL=admin@yourdomain   ADMIN_PASSWORD=...
# Telegram alert notifications (optional; leave blank to disable)
TELEGRAM_BOT_TOKEN=...      TELEGRAM_CHAT_ID=...
```

### Database migrations (run once in Supabase SQL editor)
1. `backend/database/migrations/001_multitenant_auth.sql` — profiles, OTP table, `owner_id` columns, `allocated_balance`.
2. `backend/database/migrations/002_row_level_security.sql` — RLS policies (safe; the service-role backend bypasses them).

Bootstrap the first admin by hand: `update profiles set role='admin' where email='you@domain';`

---

## Setup notes

* **API keys** — each account's Delta API key needs **Trading + Read** permissions. If you use IP whitelisting, whitelist the server's public IP.
* **Environment match** — the master and all active followers must be on the **same** Delta environment (`live` or `demo`). Copies cannot cross environments.
* **Google OAuth** — in Supabase → Auth → URL Configuration, set the Site URL and add `<your-domain>/auth/callback` to the redirect URLs; the Google client's authorized redirect must include the Supabase `/auth/v1/callback`.
* **HTTPS** — a Vercel (HTTPS) frontend cannot call an `http://` backend (mixed content); serve the backend over HTTPS.
