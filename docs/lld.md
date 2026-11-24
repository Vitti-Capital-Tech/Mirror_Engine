# Mirror Engine — Low-Level Design

Schema, events, state, and the exact rules each path follows. Reflects the code at commit `5982fe8` (24 Nov 2026). For the overall picture see the [HLD](hld.md).

---

## 1. Database (Supabase Postgres)

Base tables in `backend/database/supabase_schema.sql`; changes in `backend/database/migrations/001`–`007`. Every domain row carries `owner_id` (migration 001); Row-Level Security (002) restricts reads to the owner or an admin — the backend uses the service key and bypasses it.

### `accounts`
| Column | Notes |
|---|---|
| `id`, `name`, `owner_id` | |
| `api_key`, `api_secret` | Fernet-encrypted at rest (`enc:v1:` prefix), decrypted inside `DeltaClient` |
| `is_master` | |
| `master_id` | follower → its master (006). `ON DELETE SET NULL`: a follower of a deleted master follows nobody |
| `environment` | `demo` / `live` |
| `status` | `active` / `paused` / `error` / `circuit_break` |
| `allocation_mode`, `allocation_value` | `auto_ratio` / `multiplier` / `fixed` / `capital_pct` |
| `allocated_balance` | replaces the real balance in the auto ratio (001) |
| `max_position_size` | hard cap on any follower order |
| `tp_sl_offset` | on a master: how far its followers' SL/TP sit beyond its own (005); default 50 when unset |
| `balance`, `available_margin`, `used_margin`, `today_pnl`, `realized_pnl_today` (007) | refreshed by the live position poller |
| `consecutive_failures` | executor's circuit-breaker counter |

### `trades` — one row per master order that reached the fill path
`master_trade_id` (unique; the master order id, or `ord:<id>` for an order-stage row, 004), `symbol`, `side`, **`quantity` = how much of that order has been copied so far** (the per-order claim, §4.1), `entry_price`, `trade_type`, `status` (`processing` / `copied` / `partial` / `failed`), `raw_payload`.

### `trade_copies` — one row per (master order, follower) leg
`trade_id`, `account_id`, `follower_order_id`, `quantity` (filled), `requested_quantity` (the proportional target, 003), `master_order_id` (004), `execution_price`, `slippage_*`, `execution_time_ms`, `status` (`pending` / `filled` / `partial` / `failed` / `skipped` / `retrying`), `failure_reason`.

### `positions`, `alerts`, `profiles`, `auth_otps`
`positions` caches live positions for the dashboard (a closed symbol's row is **deleted**, not zeroed). `alerts` holds the in-app feed (`level`, `type`, `message`, `metadata`, `is_resolved`). `profiles(id, email, role)` and `auth_otps` back auth (001).

---

## 2. Events

All pushed by `TradeListener` to Redis and carrying `owner_id` **and `master_account_id`**.

| Queue | Action | Produced when | Consumer |
|---|---|---|---|
| `trade_events` | fill | a master **market** order fills (each piece) — `quantity` is **filled so far** (`size − unfilled_size`) | `process_fill` |
| `order_events` | `place` | a master limit/stop is open/pending (incl. the 30s re-push, `source="reconcile"`) | `_mirror_place` |
| | `cancel` | a master order is cancelled | `_mirror_cancel` |
| | `master_filled` | a master **limit** fills (partial or full) | `_escalate_after_master_fill`, `_confirm_order_copied` |
| | `master_stop_filled` | a master SL/TP fills | `_mark_hands_off` |
| | `reconcile_positions` | every 10s, with the master's positions | `_reconcile_positions` |
| | protection sync | every 30s, the master's resting stops | `_sync_protection` |

The listener marks a master order **filled** (Redis `masterfilled:<id>`, 24h) only when it is **completely** filled — a first partial fill does not.

---

## 3. Dispatch and locking (`main.py`, `core/leg_locks.py`)

* `redis_consumer` / `order_consumer` `BRPOP` and start **one task per event**.
* Each event with a symbol runs inside `leg_locks.hold(master_id, symbol)`. Locks are per **(master, symbol)** — two masters on one strike never block each other.
* **Live events** wait `LIVE_ACQUIRE_TIMEOUT = 30s`, then run anyway (a master trade must not be dropped). **Reconciler** corrections and **confirm-copy** take the same lock; the reconciler waits `ACQUIRE_TIMEOUT = 5s` and skips the leg if busy.
* Locks are not re-entrant; a path that holds one never asks again. Idle locks are evicted after an hour.

---

## 4. Copy paths (`core/copy_engine.py`)

### 4.1 Market fills — `process_fill`
1. **Stale entry guard:** an entry event older than `FRESH_ENTRY_SEC` (180s) is dropped.
2. **Claim** (`_claim_master_fill`): read the `trades` row for the order id. None → insert (`quantity = filled so far`). Exists with `quantity < filled so far` → conditional update `quantity: old → new`; only the winner of a race proceeds, with `copied_before = old`. Otherwise skip (a repeat or a late piece).
3. **Group:** `_split_accounts(master_id=event.master_account_id)` → this master's active followers only.
4. **Classify:** a fill that leaves the master not on the side it would open is reclassified as an exit (an unflagged close).
5. **Size per follower** (all targets use `ceil(master_size × ratio)`, `min_one=False`):
   * **Entry, first claim:** `share(filled so far)`.
   * **Entry, further piece:** `share(filled so far) − share(copied_before)`, capped at `target(master position) − held`.
   * **Exit, in one go** (`copied_before == 0` and the order is complete): `held − target(master remaining)`; skip inside the deadband.
   * **Exit, in pieces** (`copied_before > 0` or `unfilled_size > 0`): `floor_share(filled so far) − floor_share(copied_before)`, capped at `held − target`; if the master is now flat and this is the last piece, `held` (close the remainder). Master size read fresh.
6. **Execute** via `order_executor.execute` (§4.6) and record legs, ledger and status.

### 4.2 Resting orders — `_mirror_place`
* **Idempotency:** `ordermap:<master order>` → `{follower: follower order}`. An existing live mirror is left alone unless the event is an edit; one that **filled** (`mirrordone:<order>:<follower>`) is never re-placed; one the master cancelled (`ocancel:<order>` tombstone) is never resurrected.
* **Stale events** older than `STALE_EVENT_RECHECK_SEC` (3s) are re-checked against the exchange.
* **Settled?** `_master_order_settled`: (1) the `masterfilled` marker → yes; (2) present in a fresh read of the master's resting book → no; (3) ask the exchange: done → yes; **open with a partial fill → no (still rests)**; cancelled → yes if anything filled. The unfilled amount from (3) is remembered for sizing.
* **Entry sizing:** still resting → treat the master's resting entries on the symbol as one ladder: `target(master position + Σ unfilled rungs) − held`, split across rungs by largest remainder (`core/ladder.py`). Already filled → `target(master position) − (held + the follower's resting orders on that side)`, holding read fresh (`_held_plus_resting`).
* **Exit sizing:** filled → `held − target − the follower's resting exits`; resting → coverage of the master's exit ladder; then capped at the follower's uncovered position (`_cap_close_to_room`).
* **Protective orders** route to their own branch, sized to the held position, trigger from `_follower_trigger` (§4.5).
* **Edits** edit the follower's copy (price and size). If the edit fails while the copy is still resting, it is **cancelled first**; if that cancel fails, nothing is re-placed.
* **Placement** through `_place_order_with_retry` with `client_order_id = mo<master order id>_<6 hex>` (§4.7). A copy that came from the 30s re-push means the live copy never landed → a Mismatch + Reconciler-corrected alert.
* **Followers** are the event's group only (`_split_accounts(master_id=event.master_account_id)`).

### 4.3 Cancels — `_mirror_cancel`
1. Tombstone `ocancel:<order>`.
2. **Master** = `_event_master(event)` (the one the event names; a demoted master → none).
3. A cancelled **stop**: propagate only on `reason=stop_cancel`; a fired stop leaves the follower's alone.
4. **Targets** = the order's `ordermap`; if empty, this master's followers only (`_follows`: `master_id` equal).
5. Per target (skipping any follower of another master): cancel the mapped copy, or **self-heal** with `_find_follower_order` — exact `client_order_id` prefix `mo<order>_`; else (untagged legacy copies) same side **and** exact price, never a copy tagged for another order; no price → nothing.
6. **Settle** (`_settle_exit_after_cancel`, plain orders): unless hands-off, close `held − target(master now) − the follower's resting exits`, at market, reduce-only, outside the deadband. An unreadable book counts as nothing resting.

### 4.4 After a master limit fills
* `_escalate_after_master_fill`: a **partial** master fill keeps the copy queued (no clock). On a full fill each mirror gets `ESCALATE_WAIT_SEC` (5s); the remainder is cancelled and sent at market, sized from the follower's own order, at most once per mirror (`escalating:<order>` claim).
* `_confirm_order_copied` (separate task, after `ORDER_CONFIRM_SETTLE_SEC` = 3s): once per (order, follower) (`confirmed:` marker), defer while the mirror still rests; otherwise, **inside the leg lock with a fresh read**, buy `min(order share − legs filled, position target − held)`. With nothing mirrored it checks this master's followers only.

### 4.5 Stops — `_follower_trigger`
`trigger = master ± offset`, where offset = master's `tp_sl_offset` or 50, and the sign moves **away from the current price**: `+` if the master's trigger is above it, `−` if below. The current price comes from the contract's public ticker field for the order's trigger method (`spot_price` / `mark_price` / `close`). Unreadable price, or a result ≤ 0 → the master's own trigger. Used by bracket mirrors (incl. edits), protective mirrors and `_sync_protection`.

When the master's stop fires: `master_stop_filled` → `handsoff:<owner>:<follower>:<symbol>` (24h). The reconciler and the post-cancel settle leave that leg alone; the orphan alert is suppressed. Cleared when the follower is flat or the master re-enters.

### 4.6 Executing a market copy — `order_executor.execute`
Rest a limit at the master's price for up to `FILL_WAIT_SEC` (5s, polling every 0.25s), then market the remainder; record the leg (`trade_copies`), slippage and latency. Consecutive failures feed the account's circuit breaker; success resets it.

### 4.7 Placing with retry — `_place_order_with_retry`
Every order carries a `client_order_id` (`mo<order>_<hex>` for mirrors, `me<28 hex>` otherwise; Delta: string ≤ 32 chars, unique across open orders). A 4xx other than 429 is raised. On a timeout / 5xx / 429, wait 5s, then look for the id in the follower's open orders and recent history — **found means the first attempt landed** and it is returned instead of placing again.

---

## 5. Reconciler — `_reconcile_positions` (every 10s per master)

For each follower of the event's master (`fpos` read strictly; a failed read skips the follower):

| Follower vs target | Action | Extra conditions |
|---|---|---|
| holds, master flat | **close** | not hands-off; manual master exit, or `STALE_ORPHAN_SEC` (60s) past the master's exit |
| opposite side | **close** | |
| right side, too big | **trim** | not while the master is working an order on the leg |
| right side, too small | **top up** | not below the master's peak (`mpeak`); `TRIM_SETTLE_SEC` (10s) since the master's last fill |
| flat, master holds | **open** | master's last **opening** fill on the leg ≤ `FRESH_ENTRY_SEC` (180s), else ℹ️ Left out of sync |

Every action: seen on **two consecutive passes**; outside the deadband `max(1 lot, RECON_SIZE_TOLERANCE_PCT = 5%)`; inside the leg lock; **last look** (`_live_signed_fresh` + `_still_worth_acting`, or the inline equivalent for open) so it sends the smaller of what it planned and what is still true. Each leg is isolated: one leg's exception costs that leg only and raises 🔥 Engine error. A discrepancy still present after `STUCK_AFTER_SEC` (60s) raises ⏳ Not corrected.

`_sync_protection` (every 30s): places a follower SL/TP the master has and the follower lacks (after two sweeps), cancels one the master dropped.

---

## 6. Redis keys (beyond the queues)

| Key | Holds | TTL |
|---|---|---|
| `ordermap:<master order>` | `{follower: follower order}` | 7d |
| `ordersize:<master order>` | master size each mirror reflects (resize detection) | 7d |
| `mirrordone:<order>:<follower>` | this mirror filled — never re-place | 7d |
| `ocancel:<order>` | the master cancelled it — never resurrect | 10 min |
| `masterfilled:<order>` | master order completely filled | 24h |
| `escalating:<…>`, `confirmed:<order>:<follower>` | once-only claims | 7d |
| `oledger:<order>`, `oledger:sym:<owner>:<symbol>` | per-order ledger: master order + each follower's outcome | 7d |
| `handsoff:<owner>:<follower>:<symbol>` (+ index) | leave this leg to the follower's own stop | 24h |
| `mpeak:<master>:<symbol>` | master high-water mark (unwind detection) | — |
| alert keys (`recon:`, `drift:`, `stale:`, `orphan:`, …) | once-per-episode alert suppression | episode / window |

---

## 7. Alerts (`services/telegram_client.py`)

| Function | Message |
|---|---|
| `notify_mismatch` | ⚠️ Mismatch (stage one, at the second sighting) |
| `notify_correction` | 🔧 Reconciler corrected |
| `notify_holding` | ⏸ Holding on purpose |
| `notify_stuck` | ⏳ Not corrected |
| `notify_fail` | ⚠️ Mirror Failed / 🔥 Engine error / 🛑 Stopped retrying / ℹ️ Left out of sync (deliberate skips) |
| `send_alert` | forwarded feed alerts: high slippage, no liquidity |

`position_mismatch` rows are in-app only (`SILENT_ALERT_TYPES`). Routing: `TELEGRAM_CHAT_ROUTES` by owner, else `TELEGRAM_CHAT_ID`. A chat upgraded to a supergroup is followed automatically (`migrate_to_chat_id`), but update the `.env` too. `notify_open` / `notify_close` exist and are deliberately never called.

---

## 8. Timers and limits

| Name | Value | Where |
|---|---|---|
| Position reconcile | 10s | `POSITION_RECONCILE_SEC` |
| Order sweep / protection sync | 30s | `RECONCILE_INTERVAL_SEC` |
| Limit copy fill window after the master's fill | 5s | `ESCALATE_WAIT_SEC` |
| Executor limit-before-market | 5s | `FILL_WAIT_SEC` |
| Confirm-copy settle | 3s | `ORDER_CONFIRM_SETTLE_SEC` |
| Stale event re-check | 3s | `STALE_EVENT_RECHECK_SEC` |
| Late-entry cut-off | 180s | `FRESH_ENTRY_SEC` |
| Settle before resizing | 10s | `TRIM_SETTLE_SEC` |
| Orphan after the master left | 60s | `STALE_ORPHAN_SEC` |
| Mismatch alert after | 8s | `MISMATCH_ALERT_AFTER_SEC` |
| Not-corrected alert after | 60s | `STUCK_AFTER_SEC` |
| Deadband | 5% / 1 lot | `RECON_SIZE_TOLERANCE_PCT` |
| Leg lock wait — live / reconciler | 30s / 5s | `LIVE_ACQUIRE_TIMEOUT` / `ACQUIRE_TIMEOUT` |
| Follower SL/TP offset | 50 | `DEFAULT_TP_SL_OFFSET`, per master `tp_sl_offset` |
| Slippage alert | > 2% and ≥ $5 | `SLIPPAGE_ALERT_PCT`, `SLIPPAGE_ALERT_MIN_USD` |
| Masters per owner / followers per master | 5 / 2 | `api/accounts.py` |

---

## 9. Tests (`backend/tests/`)

Self-contained scripts; `python backend/tests/run_all.py` runs them all. The in-memory exchange (`test_end_to_end.py`) holds positions and an order book, fills market orders, rests limits and applies reduce-only rules. Files most relevant to the rules above:

| File | Covers |
|---|---|
| `test_end_to_end.py` | a full session: open, mirror, edit, cancel, stale re-mirror, top-up, trim, hands-off, two masters on one owner |
| `test_split_fill.py` | market orders filling in pieces — entries and exits, overlapping, slow, repeated, out of order |
| `test_cancel_cross_group.py` | one master's cancel / confirm-copy never acts on another group; demoted and masterless cases |
| `test_concurrency_guards.py` | live leg wait, open last look, failed edit, lost-response retry, settle with resting exits, two master orders seconds apart, confirm-copy lock |
| `test_partial_and_selfheal.py` | a part-filled master order still rests; self-heal cancels only the exact copy; mirror ids |
| `test_stop_after_master.py` | follower SL/TP beyond the master's |
| `test_settled_race.py`, `test_confirm_copy.py`, `test_escalation_race.py` | the settled check, confirm-copy, one escalation per mirror |
| `test_reconcile_unbound.py`, `test_last_look.py`, `test_leg_locks.py` | reconciler robustness, last look, lock keying |
| `test_fill_compare.py`, `test_order_history.py`, `test_pending_legs.py` | comparison report and copy history |
