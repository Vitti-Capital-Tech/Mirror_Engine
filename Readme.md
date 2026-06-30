# Mirror Engine

An institutional-grade, low-latency **copy-trading platform for Delta Exchange India**.

Mirror Engine watches a single **Master** account and replicates its activity in real time across multiple **Follower** accounts — not just position entries, but **closes, pending limit orders, and stop / take-profit (bracket) orders too**, each scaled to the follower's capital. It handles order sizing, balance-ratio allocation, slippage tracking, and full order-lifecycle mirroring (place / edit / cancel).

For deeper technical detail, see the High-Level Design at [docs/hld.md](docs/hld.md) and Low-Level Design at [docs/lld.md](docs/lld.md).

---

## How It Works

1. **Master executes** a trade (or places/edits/cancels an order) on the Master account.
2. **The engine intercepts** the event over Delta's WebSocket feed in real time.
3. **It scales the size** for each follower using a balance ratio (or a fixed multiplier), then **mirrors the action** to every active follower account concurrently.

Only **one master** is allowed at any time; you can promote any follower to master (the old master is demoted automatically).

---

## What gets mirrored

| Master action | Follower behaviour |
|---------------|--------------------|
| **Opens a position** (market fill) | Opens the same position, size scaled by ratio (floored, min 1 lot) |
| **Closes a position** (full or partial) | Proportional **reduce-only** close — never flips, ceils so no residual is left |
| **Places a limit order** | Mirrors a ratio-sized limit order |
| **Places a stop / SL / TP (bracket) order** | Mirrors via Delta's bracket endpoint with the correct trigger reference (Mark/Index) |
| **Edits an order or SL/TP price/trigger** | Edits the follower's existing order in place |
| **Cancels an order** | Cancels the follower's mirrored order (with self-heal lookup if the id map is stale) |

**SL/TP jitter:** each follower's stop/target trigger price is offset by a random **±(10–50)** so multiple followers don't all trigger at the exact same price/instant.

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

## Dashboard

* **Live Positions** (landing page) — master on top, active followers below, read **directly from Delta** so it mirrors the exchange exactly. Open orders show side, type, quantity, prices and **Trigger Index** (Mark/Index). PnL is computed mark-to-market to match Delta's UI.
* **Accounts** — add / edit / pause / resume / **promote-to-master** / delete, with test-connection. Shows balance, allocated balance, today's PnL and status.
* **Trades Log** — full audit trail; expand any row for the per-follower execution breakdown (price, slippage, latency, status).
* **Alerts** — slippage, position-mismatch and connection events, with a bell notification dropdown (unread badge + mark-as-read).

---

## Codebase Map

### Backend (`backend/app`)
* [core/trade_listener.py](backend/app/core/trade_listener.py) — master WebSocket handler; routes fills vs order place/edit/cancel events.
* [core/copy_engine.py](backend/app/core/copy_engine.py) — consumes Redis events; mirrors positions, closes, limit & bracket orders; cancel/edit sync; SL/TP jitter.
* [core/risk_engine.py](backend/app/core/risk_engine.py) — balance-ratio / multiplier sizing (floor on open, ceil on close), margin checks.
* [core/order_executor.py](backend/app/core/order_executor.py) — async follower market-order execution; reduce-only closes.
* [core/position_monitor.py](backend/app/core/position_monitor.py) — position cache & mismatch alerts.
* [services/delta_client.py](backend/app/services/delta_client.py) — Delta REST/WS client (HMAC-signed); orders, brackets, edits, cancels.
* [api/positions.py](backend/app/api/positions.py) — live positions & master open orders (direct from Delta).
* [api/accounts.py](backend/app/api/accounts.py) — account CRUD, pause/resume, promote-to-master.

### Frontend (`frontend/src`)
* [app/positions/page.tsx](frontend/src/app/positions/page.tsx) — Live Positions (landing page).
* [components/accounts/AccountsTable.tsx](frontend/src/components/accounts/AccountsTable.tsx) — account management table + actions.
* [components/accounts/EditAccountModal.tsx](frontend/src/components/accounts/EditAccountModal.tsx) — edit account settings / allocated balance.
* [components/layout/TopBar.tsx](frontend/src/components/layout/TopBar.tsx) — header, live status, notification dropdown.

---

## Running it

### Docker Compose (recommended)
```bash
# from the repo root — builds backend, frontend and redis
sudo docker compose up -d --build
```
Set frontend build args / env for your host in `docker-compose.yml`:
`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` (e.g. `http://<host>:8000`).

### Manual

**Backend**
```bash
pip install -r backend/requirements.txt
cd backend
# configure .env from .env.example (Supabase, Redis, DELTA_ENV=live|demo)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend**
```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

---

## Setup notes

* **API keys** — each account's Delta API key needs **Trading + Read** permissions. If you use IP whitelisting, whitelist the server's public IP.
* **Environment match** — the master and all active followers must be on the **same** Delta environment (`live` or `demo`). Copies cannot cross environments.
* **Allocated balance column** — run the `ALTER TABLE` above once to enable the allocated-balance feature.
