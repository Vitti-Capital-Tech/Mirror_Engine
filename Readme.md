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

**Duplicate protection.** The master re-sends the same resting order on every WS update and every reconcile pass, so the engine is idempotent per (master order, follower). Two distinctions matter, and getting either wrong duplicates a trade:

* A mirror that **filled** has done its job and is never re-placed. Only one **cancelled without filling** leaves work outstanding. Treating "filled" as "gone" made the follower exit twice for one master exit, booking the same P&L twice.
* An event older than `STALE_EVENT_RECHECK_SEC` (10s) is re-checked against the exchange before mirroring. A delayed event describes a world that may have moved on — mirroring a *resting* order for one that has since filled is never right.

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

### Two states the reconciler must respect

Position sizes alone don't carry enough information to decide what to do. Two situations look identical to a size comparison but demand opposite actions, so each is **recorded when the evidence appears** rather than re-inferred every pass:

**Hands-off — the master's SL/TP triggered.** The master goes flat while the follower still holds, which by position alone is indistinguishable from an orphan to close. Closing it is wrong: the follower has its own *jittered* stop a few points away, and that is what should close the leg. The mark is raised only on **positive proof the stop fired** (`reason=stop_trigger`, or a stop fill) — an unknown reason must not raise it, because the mark suspends reconciliation on that leg and would block the follower being closed when the master exits gradually instead. Released when the follower goes flat or the master re-enters. Kept in Redis so a reload can't lose it.

**Below peak — the master is unwinding.** While the master holds less than the largest size it has held on a symbol, it is net-reduced there and the follower is **never topped up** into it. This replaced a 15-minute "recently reduced" timer that was both unsafe (it expired 45s before a top-up bought back into an unwind still in progress) and over-eager (it blocked legitimate top-ups for 15 minutes after any wobble, even with the master back at full size). A high-water mark has no clock to run out and no false positive at full size. Also in Redis — held in memory, a deploy made a master mid-unwind look like a fresh position at its peak.

Note the asymmetry throughout: **reducing** the follower on stale information is self-correcting, **adding** to it is not. Trims are allowed while the master unwinds; top-ups are not.

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

## Fill comparison — do the accounts actually match?

The ledger and the Trades Log both describe **what the engine tried to do**. Neither can answer *do the two accounts match?*, because a copy that never reached the exchange, a fill the engine never observed, and an order placed on a follower by hand all look identical in records the engine wrote itself.

So the **Comparison** tab takes the exchange as truth: `/v2/fills` for the master and for every follower over an IST day, grouped **per order** (a limit that fills in five clips is one order, not five), then matched up. The engine's own `trades` / `trade_copies` rows are layered on as *annotation* — they explain a mismatch (skipped for margin, sized to 0, failed with a reason) but never define one.

**How a master order is matched to a follower's**, in descending confidence — each labelled on the row so a reader knows what to trust:

| Link | Meaning |
|------|---------|
| `linked` | A recorded leg ties the master order id to a follower order id. Covers the large majority. |
| `inferred` | No leg, but the follower filled the same symbol and side within `INFER_WINDOW_SEC` (180s). **This is the interesting case** — the copy reached the exchange and the engine failed to write it down. Calling it missing would be a false alarm; calling it `linked` would hide a bookkeeping bug. |
| — | No follower fill for that master order at all. |

**Sizes are compared against the follower's proportional target, never the master's raw lots.** A follower sized at 1/40th of the master is *correct* when it fills 1 lot against 40. The target is the recorded leg's `requested_quantity` when there is one (literally what the engine asked the exchange for) and is derived from the account's allocation settings when there isn't; which was used is shown, because a derived target on an `auto_ratio` account is only as good as the balances at read time. A 1-lot disagreement is absorbed — opens floor and closes ceil, so that is rounding, not a failure.

**Verdicts are deliberately narrow.** `missing` means the exchange says there is no follower fill *and* the engine recorded no reason. It is **not** used for an account that couldn't be read (`unreadable`), an order still resting (`resting`), a copy the engine deliberately skipped (`skipped`), or a follower with no derivable target (`unsized`). Only `missing` / `short` / `over` count toward the error figure — a risk check doing its job is not a copy failure, and an unread account must never be reported as one that traded nothing.

**Delay** is the follower's first fill minus the master's, reported as **median, mean and p95**. The mean alone is a poor summary: one mirrored limit that rested four minutes drags it into uselessness while the typical copy landed in under a second. Negative delays are kept, not clamped — both accounts rest a limit at the same price, so the follower's can trade first, and the sign is what tells you the mirror wasn't chasing.

Follower fills that no master order accounts for are listed separately rather than counted as agreement — usually a mirror of an order placed just before the window opened, but a manual trade on a follower looks exactly the same here.

```bash
curl -H "Authorization: Bearer $TOKEN" "$API/api/comparison"
```

```bash
curl -H "Authorization: Bearer $TOKEN" "$API/api/comparison/report.html?date=2026-08-26" -o report.html
```

```bash
curl -H "Authorization: Bearer $TOKEN" "$API/api/comparison/report.csv?date=2026-08-26" -o report.csv
```

Admins may pass `?owner_id=` to view another tenant's comparison. Read-only, like everything else an admin can reach.

### The daily report

Once per IST day (`DAILY_REPORT_HOUR_IST`, default 23:45) the same comparison is posted to the Telegram chat: match rate, error count, delay distribution, a per-follower rollup, and the worst few rows **named** — a count with no examples just means opening the full report to find out what broke.

The send is **idempotent per day** via a Redis marker. The backend restarts on every deploy, and a report that re-sends itself after each one is noise the desk learns to ignore, which is worse than not sending it. The Comparison tab has *Send to Telegram* for an on-demand copy, which deliberately ignores the marker.

Three renderings of the same data, because it gets read at three depths:

* **Telegram** — the one-screen answer, read on a phone.
* **CSV** — one row per (master order, follower) leg, for sorting and pivoting. Deliberately not one wide row per order with a column group per follower: that cannot be filtered and breaks the moment a follower is added.
* **HTML** — the shareable doc. Self-contained (no external CSS, fonts or scripts) so it survives being attached to a message, opened offline, or printed to PDF.

---

## Allocation & sizing

Set per follower (editable any time via the ✏️ edit action):

* **Auto Balance Ratio** *(recommended)* — follower size = master size × (follower balance ÷ master balance), **ceiled**, so a small master trade whose share is a fraction still copies as ≥1 lot. The same `ceil` defines the follower's target size on *every* path (open, close, escalation, reconcile), so no two paths can disagree about what "in sync" means. `reduce_only` caps closes so they can never over-close.

  **The ratio is built on equity (total balance), never free margin.** Available margin shrinks as positions lock margin up and grows as they close, so a ratio built on it moves with the book rather than with account size — the same unchanged positions read as "expected 22" one moment and "expected 30" half an hour later, firing false mismatch alerts and giving the reconciler a moving target. Equity only changes on a real deposit or withdrawal.

### Sizing is fail-closed

If the follower's size cannot be computed, sizing returns **0 and every caller skips** — it never falls back to a guess. This matters more than it sounds: the natural "sensible default" here is to copy the master lot-for-lot, and on a follower far smaller than the master that is the single most dangerous number the system can produce (a 610-lot target on a 70 USD account was computed live before this was closed). Every route to it is shut:

| Condition | Result |
|---|---|
| Master or follower balance unreadable | refuse |
| `allocation_mode` not set | refuse |
| `allocation_value` not set | refuse |
| Unrecognised `allocation_mode` | refuse |

A missed copy is recoverable — the reconciler retries once the inputs read again. A 100× oversized order is not. The same principle applies to *closes*: a target of 0 while the master still holds means "sizing unavailable", **not** "the follower should be flat", so nothing is closed on an unreadable ratio.
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
* **Comparison** — master vs follower fills for an IST day, read from the exchange and matched order by order: match rate, error count, delay median/mean/p95, per-follower rollup, and CSV / HTML / Telegram exports. See [Fill comparison](#fill-comparison--do-the-accounts-actually-match).
* **Alerts** — slippage, position-mismatch and connection events, with a bell notification dropdown.

## Admin console (admins only)

A dedicated console with its own navigation (regular sections are hidden):

* **Positions** — every user's live master + follower positions, grouped and styled like the trader view, with per-account Today/Active P&L, Balance and Alloc.
* **Users** — every account holder with their master, follower count, active accounts, copies and join date; regular users only.
* **All Accounts** — every master/follower across all tenants (owner, role, status, env, balance, PnL).
* **Trade Log / Alert Feed** — cross-tenant, collapsible per-user cards.
* **Comparison** — pick a tenant, see their master vs their followers, plus the daily-report health (scheduled? Telegram wired up? sent today?) and a *send all tenants now* button.

Admins are resolved from the `profiles.role` column; the first admin is set by hand in SQL, after which admins can promote/demote others. An admin has **full operational control** over any tenant's accounts — pause, resume, edit, delete and promote-to-master included — so the console is a management surface, not just a viewer.

**Responsive:** on mobile the sidebar is replaced by a bottom tab bar; tables scroll horizontally.

---

## Codebase Map

### Backend (`backend/app`)
* [core/trade_listener.py](backend/app/core/trade_listener.py) — master WebSocket handler + **`ListenerManager`** (one listener per user's master).
* [core/copy_engine.py](backend/app/core/copy_engine.py) — consumes Redis events; mirrors positions, closes, limit & bracket orders; cancel/edit sync; SL/TP jitter; the 15s position reconciler (open / close / trim / top-up) and protection sync; owner-scoped followers.
* [core/order_ledger.py](backend/app/core/order_ledger.py) — Redis order-ID ledger: every master order and each follower's outcome, so "which master order never reached this follower?" is answerable.
* [test_order_ledger.py](backend/test_order_ledger.py) — self-contained regression suite (no network/Redis/Supabase) replaying real incidents: missed partial exit, stuck exit order, place/cancel loop, trigger-vs-cancel, deadband, liveness cache. Run with `python test_order_ledger.py`.
* [core/fill_compare.py](backend/app/core/fill_compare.py) — the exchange-truth comparison: groups `/v2/fills` per order for master + every follower, matches them (`linked` / `inferred`), grades each leg against its proportional target, and summarises match rate, delays and errors.
* [core/daily_report.py](backend/app/core/daily_report.py) — the three renderings (Telegram / CSV / self-contained HTML) plus the once-per-IST-day scheduler, idempotent through a Redis marker.
* [test_fill_compare.py](backend/test_fill_compare.py) — self-contained checks (no network) for the ways the comparison could lie: an unrecorded copy read as missing, a correctly-sized follower read as short, an unreadable account read as one that did not trade, one follower fill claimed by two master orders. Run with `python test_fill_compare.py`.
* [core/risk_engine.py](backend/app/core/risk_engine.py) — balance-ratio / multiplier sizing (floor on open, ceil on close), margin checks.
* [core/order_executor.py](backend/app/core/order_executor.py) — async follower execution; FOK all-or-nothing entries + retries; reduce-only closes.
* [core/auth.py](backend/app/core/auth.py) — token verification, `require_admin`, owner scoping, email-OTP 2FA helpers.
* [core/crypto.py](backend/app/core/crypto.py) — Fernet encryption for API keys at rest.
* [services/delta_client.py](backend/app/services/delta_client.py) — Delta REST/WS client (HMAC-signed); orders, brackets, edits, cancels, wallet transactions.
* [api/auth.py](backend/app/api/auth.py) — signup / login / 2FA / quick-admin passphrase.
* [api/admin.py](backend/app/api/admin.py) — cross-tenant overview, users, accounts, positions, trades, alerts.
* [api/positions.py](backend/app/api/positions.py) — live positions; **Today P&L = realized (ledger) + unrealized**.
* [api/comparison.py](backend/app/api/comparison.py) — the comparison + report endpoints (JSON / CSV / HTML / text / send / status), owner-scoped.
* [api/accounts.py](backend/app/api/accounts.py) — account CRUD, pause/resume, promote-to-master (encrypts secrets).
* [database/migrations/](backend/database/migrations/) — `001_multitenant_auth.sql` (profiles, OTP, owner_id), `002_row_level_security.sql`.

### Frontend (`frontend/src`)
* [app/page.tsx](frontend/src/app/page.tsx) — public landing page (role-aware redirect when logged in).
* [app/admin/](frontend/src/app/admin) — admin console (Positions, Users, All Accounts, Trades, Alerts) + guard layout.
* [app/positions/page.tsx](frontend/src/app/positions/page.tsx) — trader Live Positions.
* [components/auth/AuthCard.tsx](frontend/src/components/auth/AuthCard.tsx) — flip-card login/signup; [AuthShell.tsx](frontend/src/components/auth/AuthShell.tsx) — split-screen shell.
* [context/AuthContext.tsx](frontend/src/context/AuthContext.tsx) — session/role state (cached); [components/layout/AppShell.tsx](frontend/src/components/layout/AppShell.tsx) — route guards + chrome.
* [components/comparison/ComparisonView.tsx](frontend/src/components/comparison/ComparisonView.tsx) — the Comparison tab: headline stats, per-follower rollup, expandable order-by-order table, unexplained-fill list, and a legend for every verdict. Shared by the trader and admin pages.
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
STALE_EVENT_RECHECK_SEC=10      # re-verify a delayed order event against the exchange before mirroring
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
