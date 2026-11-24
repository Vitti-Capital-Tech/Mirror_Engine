# Mirror Engine

A low-latency, **multi-tenant copy-trading engine for Delta Exchange India** (BTC options and futures).

A trader works on a **master** account; every **follower** account linked to that master gets the same trades automatically — entries, exits, resting limit orders, edits, cancels, stop-losses and take-profits — each scaled to the follower's own size. A second system checks the result every 10 seconds and repairs anything the live copy got wrong.

* **High-level design:** [docs/hld.md](docs/hld.md) — components, data flow, the rules that keep followers in line.
* **Low-level design:** [docs/lld.md](docs/lld.md) — schema, events, Redis keys, every sizing rule and timer.
* **For traders:** the **Trader's Guide** page in the dashboard (`/guide`) explains all of this without the engineering.
* **Engine internals with live results:** `/architecture` on the dashboard.

---

## How it works

1. **The master trades** on Delta Exchange.
2. **A listener per master** receives every order update over Delta's WebSocket and turns it into an event on a Redis queue, tagged with *which master* produced it.
3. **The copy engine** sizes the trade for each of that master's followers and places it on their accounts — usually within 1–2 seconds.
4. **A reconciler** compares every follower with its master every 10 seconds and corrects missing, oversized, undersized or orphaned positions. Every correction is reported.
5. **Problems go to Telegram**, routed to each owner's own chat. Routine copies are silent: a message means something needs a look.

### Groups and tenants

* A **group** is one master and its followers. A follower names its master (`accounts.master_id`).
* One owner can run up to **5 masters**, each with up to **2 followers**. Groups are fully isolated: one master's fills, orders or cancels never act on another master's followers, and a follower is only ever sized against its own master.
* Any number of owners (tenants) can use the platform; an **admin** sees and manages every tenant.

---

## What gets copied

| Master does | Follower gets |
|---|---|
| **Market order** | Its share, placed straight away. If Delta fills the master's order in **pieces**, every piece is copied at the ratio and the total is exact — for entries and exits. |
| **Limit order** | The same order at the same price, sized to its share, resting like the master's. |
| **Edits a limit** (price or size) | Its copy is edited. If the edit fails while the copy still rests, the old copy is cancelled before a new one is placed — never two. |
| **Cancels** | Its copy is cancelled (found by an id tied to the master's order). |
| **Limit fills** | The follower's copy gets **5 seconds** to fill at the same price, then the rest goes at market. A *partial* master fill keeps the copy waiting at his price. |
| **Closes / reduces** | Closed to its share of what the master still holds — a small trim never wipes out a small follower. |
| **Stop-loss / take-profit** | Its own copy, placed **50 points beyond** the master's (higher for a trigger above the price, lower for one below), so the master always exits first. *(Temporary desk rule, 24 Nov 2026.)* |

If a follower **missed an entry**, it is recovered only while the master's entry is under **3 minutes** old; older than that it is not bought late at a new price — a *Left out of sync* alert says so instead.

---

## Sizing

Set per follower on the Accounts page:

| Mode | Follower trades |
|---|---|
| **Auto ratio** *(usual)* | master size × (follower balance ÷ master balance). An **allocated balance** on the account replaces its real balance in this ratio. |
| **Multiplier** | master size × a fixed number |
| **Fixed** | the same number of lots on every trade |
| **Capital %** | a percentage of the follower's available margin |

Orders round **up** to whole lots (at least 1 lot on an open); exit pieces round **down** so they never add up to more than the follower's share. A **max position size** caps any follower. The ratio uses **total balance**, not free margin, so it doesn't move as positions lock margin.

**Sizing is fail-closed:** if a balance or setting can't be read, the size is 0 and nothing is placed — there is no 1:1 fallback. A missed copy is recoverable; a 100× oversized order is not.

---

## Alerts

Each owner's alerts go to their own Telegram chat (`TELEGRAM_CHAT_ROUTES`), one message per problem rather than one per 10-second pass.

| Message | Meaning | Act? |
|---|---|---|
| ⚠️ Mismatch | a follower differs from its master (size, missing leg, missing SL/TP, missing order) | usually not — a fix normally follows |
| 🔧 Reconciler corrected | the safety check fixed something, and says what | no |
| ⏸ Holding on purpose | a difference left deliberately (e.g. the master is reducing that leg) | no |
| ℹ️ Left out of sync | a missed entry older than 3 minutes, not bought late | decide by hand |
| ⏳ Not corrected | still wrong after 60s — the fix did not work | **yes** |
| ⚠️ Mirror Failed | the exchange rejected a copy (often `insufficient_margin`) | **yes** |
| 🔥 Engine error | the engine crashed on its own correction for that leg | **yes — tell tech** |
| 🛑 Stopped retrying | an order was placed three times and vanished each time | **yes** |
| High slippage / No liquidity | a follower filled far from the master's price (>2% and ≥ $5), or the book was empty | check the fill |

A daily comparison report (master vs follower fills, read from the exchange) is posted at **23:45 IST**.

---

## Dashboard

* **Positions** — every group: master on top, followers below, balances and P&L (realised + unrealised, at the price you can actually trade). A yellow *no SYMBOL* chip means a follower lacks a leg its master holds.
* **Accounts** — add accounts, set sizing mode / allocated balance / max position, pause, resume, promote to master, test the connection.
* **Trades Log** — every master trade and what each follower did with it.
* **Comparison** — master and follower fills for an IST day, matched order by order from the exchange's own fill tape; CSV / HTML / Telegram exports.
* **Alerts** — the in-app alert feed.
* **Trader's Guide** — the plain-language manual for traders.

There is one menu for everyone. An admin sees every tenant's accounts on the same pages (grouped by owner, with an account switcher in the header) and can manage all of them.

Times on the dashboard are shown in the **viewer's** time zone; Delta shows IST and the server logs UTC.

---

## Codebase map

### Backend (`backend/app`)
| File | Role |
|---|---|
| [core/trade_listener.py](backend/app/core/trade_listener.py) | Master WebSocket handler; turns order updates into events. `ListenerManager` runs one per master. |
| [core/copy_engine.py](backend/app/core/copy_engine.py) | Consumes events; copies fills, resting orders, edits, cancels and stops; the 10s position reconciler and protection sync. |
| [core/leg_locks.py](backend/app/core/leg_locks.py) | One lock per (master, symbol) so two actions never size off the same stale position. |
| [core/risk_engine.py](backend/app/core/risk_engine.py) | Follower sizing (the four modes, fail-closed). |
| [core/order_executor.py](backend/app/core/order_executor.py) | Places a follower's market copy: limit at the master's price, then market. |
| [core/order_ledger.py](backend/app/core/order_ledger.py) | Redis records: per-order ledger, hands-off marks, master peaks. |
| [core/position_monitor.py](backend/app/core/position_monitor.py) | Per-update position checks and the orphan alert. |
| [core/fill_compare.py](backend/app/core/fill_compare.py), [core/daily_report.py](backend/app/core/daily_report.py) | Exchange-truth comparison and the daily report. |
| [services/delta_client.py](backend/app/services/delta_client.py) | Signed Delta REST/WS client. |
| [services/telegram_client.py](backend/app/services/telegram_client.py) | Alert wording, routing per owner, de-duplication. |
| [api/](backend/app/api) | REST API: accounts, positions, trades, alerts, comparison, auth, admin. |

### Frontend (`frontend/src`)
Next.js 14 dashboard: `app/positions`, `app/accounts`, `app/trades`, `app/comparison`, `app/alerts`, `app/guide`, `app/admin`; shared layout in `components/layout`. `public/architecture.html` is served at `/architecture`.

---

## Tests

All tests live in [`backend/tests/`](backend/tests). Each file is a self-contained script — no pytest, no network, no Redis, no Supabase; the exchange and database are in-memory fakes that keep state.

```bash
python backend/tests/run_all.py              # every file, with a pass/fail summary (from anywhere)
python backend/tests/run_all.py split leg    # only files whose name matches
cd backend && python tests/test_end_to_end.py   # one file — run from backend/, like the runner does
```

Most files replay a real incident (dated in the file's docstring) and are first shown to fail on the code before the fix.

---

## Running it

### Production
* **Frontend** on **Vercel** — deploys on every push to `main`. Only `NEXT_PUBLIC_*` values belong there.
* **Backend + Redis** in **Docker** on the EC2 host (`docker-compose.yml`). The backend is bind-mounted and runs `uvicorn --reload`, so pulling new code restarts it in ~3s. All secrets live in `backend/.env` on the host.
* **Monitoring:** `ops/watchdog.sh` (cron, every 2 min, restarts a hung backend and alerts) and `.github/workflows/uptime.yml` (external health check every ~5 min).

### Local
```bash
cd backend && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
cd frontend && npm install && npm run dev      # http://localhost:3000
```

### Backend `.env`
```
SUPABASE_URL=...  SUPABASE_SERVICE_KEY=...  SUPABASE_ANON_KEY=...  SUPABASE_JWT_SECRET=...
REDIS_URL=redis://redis:6379/0
DELTA_ENV=live                       # or demo
ENCRYPTION_KEY=...                   # Fernet key for API secrets at rest
PASSWORDLESS_LOGIN=true              # email-only login
PASSWORDLESS_SECRET=...              # pin once; changing it changes every user's login
ADMIN_MAGIC_CODE=...  ADMIN_EMAIL=...  ADMIN_PASSWORD=...
TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...  TELEGRAM_CHAT_ROUTES={"<owner-id>": "<chat-id>"}
DAILY_REPORT_ENABLED=true            # 23:45 IST by default
```
Tuning knobs (all optional): `FRESH_ENTRY_SEC` (180), `RECON_SIZE_TOLERANCE_PCT` (5), `TRIM_SETTLE_SEC` (10), `STALE_ORPHAN_SEC` (60), `STUCK_AFTER_SEC` (60), `ORDER_CONFIRM_SETTLE_SEC` (3), `SLIPPAGE_ALERT_PCT` (0.02), `SLIPPAGE_ALERT_MIN_USD` (5).

### Database
Run `backend/database/supabase_schema.sql`, then the migrations in `backend/database/migrations/` in order (`001` … `007`). Bootstrap the first admin by hand: `update profiles set role='admin' where email='you@domain';`

### Setup notes
* Each account's Delta API key needs **Trading + Read**; whitelist the server's IP.
* A master and its followers must be on the **same** Delta environment.
* A `.env` change does not reach a running container — recreate it: `docker compose up -d --force-recreate --no-deps backend`.
