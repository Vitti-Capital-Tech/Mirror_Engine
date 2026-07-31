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
| **Closes a position** (full or partial) | **Rebalances** the follower to `ceil(master_remaining × ratio)` and closes only the excess (reduce-only). A small master trim never wipes a small follower |
| **Places a limit order** | Mirrors a ratio-sized limit order |
| **Places a stop / SL / TP (bracket) order** | Mirrors via Delta's bracket endpoint with the correct trigger reference (Mark/Index) |
| **Edits an order or SL/TP price/trigger** | Edits the follower's existing order in place |
| **Cancels an order** | Cancels the follower's mirrored order (with self-heal lookup if the id map is stale) |

**SL/TP jitter:** each follower's stop/target trigger price is offset by **±(0–20)**, computed **deterministically** from the follower + price — so two legs of a pair that share the same master SL/TP price land on the *same* follower price, while different followers still get different offsets (they don't all trigger at once).

**Unfilled-limit escalation — the 5s clock starts at the *master's* fill.** The master trades with limit orders, so a mirrored follower limit that never fills would leave the follower behind. The rule is: mirror the master's limit at the same price and **let it rest for as long as the master's rests**; the moment the **master's own order fills**, give the follower's mirror `ESCALATE_WAIT_SEC` (5s) to fill, then force the remainder to market.

Timing this from *placement* instead is wrong in both directions: it forces the follower in or out while the master is still patiently waiting, and — because a mirrored reduce-only order looked like it had "nothing to close" — it cancelled the order, which the 30s reconcile then re-placed, producing an endless place/cancel loop. While the master's order is still resting the escalation does **nothing at all** (no market, no cancel). SL/TP brackets are excluded entirely; they're meant to rest until triggered.

**Protective-cancel safety — trigger vs. manual cancel.** Follower stops are jittered to a slightly different price, so the master's stop firing tells you *nothing* about whether the follower's has fired. The two cases are therefore handled oppositely, using Delta's own `reason` field:

* `reason=stop_cancel` → the master **deliberately cancelled** the stop → the follower's copy is orphaned, so cancel it too.
* anything else (`stop_trigger`, a fill, or an unreadable reason) → **leave the follower's stop in place** and let its own trigger do the work.

Deliberately biased toward keeping protection: stripping a live stop leaves a naked position, whereas an orphaned stop is harmless and gets cleaned up by the protection sweep. (Inferring this from the master's *position* instead misreads a TP firing on a **partial** close — the master is still holding, so it looks like a manual cancel and the follower's protection is stripped while its own stop hasn't triggered yet.)

---

## Reconciliation — the safety net

Live copying can fail for reasons the copy path cannot control: an order rejected for margin, a request that arrives too late, a WebSocket gap, a limit that simply never fills. Rather than trying to enumerate those, the engine periodically compares **state** and repairs the difference. It doesn't care *why* the follower is wrong.

**Position reconcile (every 15s)** — for every symbol, one of:

| Situation | Action |
|---|---|
| Master holds, follower flat | **Open** it (subject to the price guard below) |
| Follower holds, master flat | **Close** it (orphan) |
| Opposite sides | **Close** it (desync) |
| Right side, **too big** | **Trim** the excess (a close the follower never completed) |
| Right side, **too small** | **Top up** the shortfall (a partly-missed entry) |

**Order reconcile (every 30s)** — re-mirrors the master's resting plain limit orders, and syncs protection **both ways**: cancels a follower SL/TP the master no longer has, *and places one the follower is missing*. The latter matters because brackets are excluded from the ordinary re-mirror, so before this existed a stop that failed to mirror once was never restored — leaving a position unprotected indefinitely.

Every corrective action is guarded so noise can't trigger a trade:

* **Two-pass confirmation** — a difference must persist across consecutive passes before anything is placed.
* **Size deadband** (`RECON_SIZE_TOLERANCE_PCT`, default 5%) — the target is derived from a *live* balance ratio, so it drifts continuously. Acting on a 1-lot difference on a 30-lot leg produces a trim/top-up churn loop that pays spread both ways.
* **Settle window** (`TRIM_SETTLE_SEC`, 45s) — never resize while the master is still actively trading that symbol.
* **One definition of "target"** — the live close, mirrored close, escalation, trim, top-up and post-cancel settle all compute `ceil(master_size × ratio)`. Mixing `ceil` and `floor` between paths makes two of them each close a lot off the same 1-lot difference.

**Price guard on recovery** (`SYNC_PRICE_TOLERANCE_PCT`, default 15%) — a leg the master entered long ago is only recovered if the current mark is still within this much of the master's entry; otherwise it alerts instead of silently doing nothing. Judging on *age* alone means a missed entry is never recovered and the follower diverges permanently.

> **Caveat for options:** entry-vs-mark drift measures the master's *P&L*, not whether copying still makes sense. Near expiry, premium decay pushes drift above 90% regardless, so the guard blocks nearly every recovery. Raise `SYNC_PRICE_TOLERANCE_PCT` (or set it very high) if matching the master's positions matters more than matching its entry prices.

## Order-ID ledger

Position-based reconciliation can tell you *that* a follower is out of sync, never *why* — and crucially it cannot distinguish **"the order was never placed"** from **"the order was placed and never filled"**, which need completely different fixes.

The ledger records, keyed on the **master order id**, the master's order plus one leg per follower: mirrored (with the follower's own order id), deliberately skipped (with the reason), failed, or cancelled. Kept in Redis for 7 days.

```bash
# Full copy trail for one master order
curl -H "Authorization: Bearer $TOKEN" "$API/api/trades/ledger/order/1442271322"

# Which orders on this symbol did a follower never receive?
curl -H "Authorization: Bearer $TOKEN" "$API/api/trades/ledger/symbol/P-BTC-63000-310726?missing_only=true"
```

Both are read-only and owner-scoped. A "skipped" leg is an accounted-for decision (e.g. already at target) and is *not* reported as missing; only an absent or failed leg is.

---

## Allocation & sizing

Set per follower (editable any time via the ✏️ edit action):

* **Auto Balance Ratio** *(recommended)* — follower size = master size × (follower balance ÷ master balance), **ceiled**, so a small master trade whose share is a fraction still copies as ≥1 lot. The same `ceil` defines the follower's target size on *every* path (open, close, escalation, reconcile), so no two paths can disagree about what "in sync" means. `reduce_only` caps closes so they can never over-close.
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
* **Limit, then market** — a follower copy first rests a limit at the master's price; any unfilled remainder is then sent as a market order so the copy completes. (Delta rejects `fok`, so it is not used.)
* **Cached master state** — master position lookups are cached ~3s, and a **single reusable API client per master**: the hot helpers used to construct and close a client per call, paying a full TLS handshake every time.
* **Cached order liveness** — "is this mirrored order still resting?" is the hottest call in the engine (the master re-sends the same resting order on every update, and each repeat asked the exchange twice *per follower*). Cached 3s, but **asymmetrically**: a cache hit may only ever answer *"still live"* — anything not in the cached set is re-verified fresh, because a stale *"gone"* would place a duplicate order. A failed read also answers "live", for the same reason.
* **Signature retry** — Delta rejects a request signature older than ~5s. Under load a signed order could reach the exchange 13–21s late and be refused as `expired_signature`; it is now re-signed and resent once rather than dropped as a permanent 4xx.

## Alerts & notifications

Alert types raised: **position mismatch** (follower size ≠ master × ratio — raised on *both* master and follower, once per episode, re-arming when it resolves), **high slippage** (> 0.03%), **liquidity unavailable**, and **protection restored** (a follower was holding with no SL/TP).

* **Dashboard** — Alert Feed with level filters + a notification bell.
* **Telegram** — alerts are pushed to a Telegram group via the Bot API (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`; leave unset to disable).

**Alerts are edge-triggered, not repeated.** Suppression state lives in **Redis**, so it survives a restart — otherwise every deploy re-alerts every ongoing condition from scratch. A persistent condition (a leg that can't be recovered, an order that keeps failing) sends **one** message and is then silent for `STATE_ALERT_WINDOW` (6h); when the underlying problem resolves, the flag is cleared so a genuine recurrence alerts again. Identical trade events (same account/symbol/side/size) collapse within 45s so a retry can't report the same fill twice.

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
* [core/copy_engine.py](backend/app/core/copy_engine.py) — consumes Redis events; mirrors positions, closes, limit & bracket orders; cancel/edit sync; SL/TP jitter; the 15s position reconciler (open / close / trim / top-up) and protection sync; owner-scoped followers.
* [core/order_ledger.py](backend/app/core/order_ledger.py) — Redis order-ID ledger: every master order and each follower's outcome, so "which master order never reached this follower?" is answerable.
* [test_order_ledger.py](backend/test_order_ledger.py) — self-contained regression suite (no network/Redis/Supabase) replaying real incidents: missed partial exit, stuck exit order, place/cancel loop, trigger-vs-cancel, deadband, liveness cache. Run with `python test_order_ledger.py`.
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

#### Copy/reconcile tuning (all optional — defaults shown)
```
FRESH_ENTRY_SEC=180             # a queued fill event older than this is not copied
SYNC_PRICE_TOLERANCE_PCT=15     # recover a stale leg only if price is within this % of the master's entry
RECON_SIZE_TOLERANCE_PCT=5      # deadband before resizing (below this, ratio noise)
TRIM_SETTLE_SEC=45              # don't resize within this long of the master's last fill
STALE_ORPHAN_SEC=360            # a follower leg behind a stop, with the master long gone, is an orphan
```
> On **options**, `SYNC_PRICE_TOLERANCE_PCT=15` blocks nearly every recovery — premium decay pushes drift past 90% near expiry. Raise it if matching positions matters more than matching entry prices.

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
