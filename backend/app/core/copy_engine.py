import logging
import asyncio
import hashlib
import os
import time
from datetime import datetime
from typing import List, Dict, Any
from app.database import db
from app.websocket.socket_manager import socket_manager
from app.core.risk_engine import RiskEngine
from app.core.order_executor import order_executor
from app.core import order_ledger as ledger
from app.services.delta_client import DeltaClient
from app.services import telegram_client as tg

logger = logging.getLogger(__name__)


def _short_reason(exc, body: str = "") -> str:
    """Turn a Delta error into a short human reason for a notification."""
    import json as _json
    if body:
        try:
            j = _json.loads(body)
            err = (j or {}).get("error")
            if isinstance(err, dict):
                code = err.get("code") or err.get("message")
                if code:
                    return str(code).replace("_", " ")
            if isinstance(err, str):
                return err.replace("_", " ")
        except Exception:
            pass
    s = str(exc)
    return (s[:120] + "…") if len(s) > 120 else s

# If a mirrored LIMIT order hasn't filled after this window (checked twice:
# wait, then retry-wait), escalate it to a full-or-nothing market order.
ESCALATE_WAIT_SEC = 5

# A copied ENTRY is only "fresh" if the master's entry happened within this
# window. After downtime the master may still hold hours-old positions (the
# reconciler would otherwise re-enter the follower at today's drifted price),
# or a stale fill event may sit queued in Redis across the outage and replay
# hours later. Either way an entry this old is NOT copied — a late entry at a
# moved price is worse than sitting the trade out. Exits/closes are NEVER gated
# on age (a late close still matters). (Prathav, 2026-07-29)
FRESH_ENTRY_SEC = float(os.getenv("FRESH_ENTRY_SEC", "180"))  # 3 minutes

# A follower may briefly hold MORE than its target while a mirrored close is
# still working (order in flight, escalation pending). Only trim over-exposure
# that has survived this long since the master's last fill on the symbol, so we
# never race a close that's about to land.
TRIM_SETTLE_SEC = float(os.getenv("TRIM_SETTLE_SEC", "45"))

# When the master is FLAT, the follower still holds, and we CAN'T tell how the
# master exited (`_classify_master_exit` -> "unknown", e.g. the master's close
# has already rolled out of the recent order-history window — common for a grid
# trader), we normally leave the follower alone and retry. That deferral is
# unbounded, which is how an orphan can sit forever. Escape hatch: once the
# master's last fill on that symbol is older than this, a follower's own jittered
# stop was never going to fire alongside it either — so treat it as an orphan and
# close. Generous vs FRESH_ENTRY_SEC because a real SL/TP mirror fires in seconds.
STALE_ORPHAN_SEC = float(os.getenv("STALE_ORPHAN_SEC", "360"))  # 6 minutes

# Recovering a leg the master entered a long time ago used to be refused outright:
# the reconciler logged "SKIP stale entry ... leaving follower flat" every 15s,
# forever. Safe against price drift, but it meant ANY entry a follower missed was
# never recovered — the follower silently diverged further from the master all day
# (2026-07-30 audit: 5 missing legs, one 3.8h old and still being skipped).
#
# Instead of refusing on AGE, judge on PRICE: recover the leg if the current mark
# is still within this much of the master's own entry price. A leg the master got
# at a similar price is worth having; one that has run away is not, and that case
# alerts instead of silently doing nothing. Percent, e.g. 15 = ±15%.
SYNC_PRICE_TOLERANCE_PCT = float(os.getenv("SYNC_PRICE_TOLERANCE_PCT", "15"))

# Deadband before the reconciler will resize a follower that is on the correct
# side. The target is derived from a LIVE balance ratio (auto_ratio divides the
# follower's balance by the master's), so it drifts continuously as both accounts
# mark to market: a 3050-lot master leg sat exactly on the 30/31 boundary and the
# target flipped between the two. Acting on that 1-lot difference produced a
# trim-then-top-up churn loop, paying spread and fees on every oscillation
# (observed live 2026-07-30, trimmed P-BTC-60000 and P-BTC-60500 by 1 each).
#
# Same threshold position_monitor already uses to decide a mismatch is worth
# alerting on, so the alert and the correction now agree on what "out of sync"
# means. A real miss (a whole missing leg, or half a position) clears this
# comfortably; ratio noise never does.
RECON_SIZE_TOLERANCE_PCT = float(os.getenv("RECON_SIZE_TOLERANCE_PCT", "5"))


def _size_deadband(target: float) -> float:
    """Minimum |held - target| worth acting on: at least 1 lot, and at least
    RECON_SIZE_TOLERANCE_PCT of the target."""
    return max(1.0, abs(float(target)) * RECON_SIZE_TOLERANCE_PCT / 100.0)


def _parse_epoch(v):
    """Best-effort convert a Delta timestamp to epoch SECONDS (UTC). Accepts an
    ISO-8601 string like '2026-07-28T12:00:01.174513Z' or an epoch in s/ms/µs.
    Returns None if it can't be parsed."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    elif isinstance(v, str) and v.strip().isdigit():
        n = float(v.strip())
    else:
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    # normalise ms / µs epochs down to seconds
    if n > 1e14:
        n /= 1e6
    elif n > 1e11:
        n /= 1e3
    return n


class CopyEngine:
    def __init__(self, db_client, redis_client, socket_mgr, connection_mgr) -> None:
        self.db = db_client
        self.redis = redis_client
        self.socket_manager = socket_mgr
        self.connection_manager = connection_mgr
        self.risk_engine = RiskEngine()
        # Short-lived cache of master position sizes {symbol: (size, ts)} so a
        # burst of protective cancels doesn't fire a get_positions REST call each.
        self._master_pos_cache: dict = {}
        self._master_signed_cache: dict = {}  # {symbol: (signed_size, ts)}
        self._MASTER_POS_TTL = 3.0  # seconds
        # Protective orders seen as "orphan" (master no longer has them) in the
        # PREVIOUS sync sweep. We only cancel a follower's SL/TP after it's been an
        # orphan for two consecutive sweeps, so a master bracket edit
        # (cancel-and-replace, momentarily no stop) can't cause a wrong cancel.
        self._prot_orphans_prev: set = set()
        # Debounce for the 10s position reconciler's OPEN action, per
        # (follower_id, symbol): don't re-fire a recovery open within this window
        # (avoids spamming a margin-rejected open every cycle, and gives a fill
        # time to reflect before we'd consider re-opening).
        self._recon_open_ts: dict = {}
        self._RECON_OPEN_DEBOUNCE = 30.0  # seconds
        # A mismatch must persist across TWO consecutive 10s reconcile passes
        # before we act — so a transient race (a just-filled mirror whose position
        # hasn't shown in get_positions yet) can never trigger a duplicate.
        self._recon_open_prev: set = set()
        self._recon_close_prev: set = set()
        # Same two-pass confirmation for a TRIM (follower on the correct side but
        # holding more than its share of the master's remaining position — i.e. a
        # partial exit that never got copied). Keyed on the excess too, so a size
        # that's still moving restarts the confirmation instead of acting mid-flight.
        self._recon_trim_prev: set = set()
        # Same, for a TOP-UP (follower on the correct side but holding LESS than its
        # share — a partially-missed entry). Adds exposure, so it gets the same
        # two-pass confirmation, the open debounce, and a price-drift guard.
        self._recon_topup_prev: set = set()
        # {symbol: abs master size seen last pass} and {symbol: ts until which the
        # master counts as REDUCING on that symbol. While a master is unwinding, a
        # follower must never be adding — see the top-up branch.
        self._master_size_prev: dict = {}
        self._master_reducing_until: dict = {}
        self._REDUCING_COOLDOWN = float(os.getenv("REDUCING_COOLDOWN_SEC", "900"))
        # (follower_id, symbol, stop_order_type) seen as MISSING protection in the
        # previous sweep. Protection is only ADDED after two consecutive sightings,
        # so a partial order read can never cause a duplicate stop.
        self._prot_missing_prev: set = set()
        # {account_id: (set_of_resting_order_ids, ts)} — see _order_is_live. Short
        # TTL: this only shortcuts the "yes, still live" answer, never "gone".
        self._open_orders_cache: dict = {}
        self._OPEN_ORDERS_TTL = 3.0  # seconds
        # {(master_id, api_key): DeltaClient} — reused so the hot master helpers
        # don't pay a TLS handshake per call. See _get_master_client.
        self._master_clients: dict = {}
        # How the master most recently CLOSED each symbol ("sl_tp" | "manual"),
        # cached so the 10s reconcile loop doesn't re-pull order history every
        # pass while it waits for a follower's own stop to (or fail to) hit.
        self._master_exit_cache: dict = {}  # {symbol: (reason, ts)}
        self._MASTER_EXIT_TTL = 20.0  # seconds

    async def process_fill(self, event_dict: dict) -> None:
        """
        Process a master fill event:
        1. Save master trade to trades table
        2. Fetch active followers
        3. Execute orders on followers in parallel
        4. Update master trade status
        5. Emit Socket.IO update
        """
        master_trade_id = event_dict.get("master_trade_id")
        symbol = event_dict.get("symbol")
        side = event_dict.get("side")
        quantity = float(event_dict.get("quantity", 0))
        entry_price = float(event_dict.get("entry_price", 0))
        trade_type = event_dict.get("trade_type", "entry")
        raw_payload = event_dict.get("raw_payload")
        owner_id = event_dict.get("owner_id")
        ts_detected = event_dict.get("ts")
        _t0 = time.time()
        if ts_detected:
            logger.info(f"[LATENCY] {symbol}: {(_t0 - ts_detected):.2f}s from master-fill detection to dispatch start")

        logger.info(f"Processing master fill: {side.upper()} {quantity} {symbol} @ {entry_price} (ID: {master_trade_id})")

        # Stale-ENTRY guard: after downtime a fill event can sit queued in Redis
        # across the outage and replay hours later. Re-entering at a price that
        # has since drifted is worse than skipping, so drop entries older than
        # FRESH_ENTRY_SEC. Exits are always processed — a late close still matters.
        if trade_type == "entry" and ts_detected:
            age = _t0 - float(ts_detected)
            if age > FRESH_ENTRY_SEC:
                logger.warning(
                    f"Skipping STALE entry {side.upper()} {quantity} {symbol} — detected "
                    f"{age:.0f}s ago (> {FRESH_ENTRY_SEC:.0f}s). Not copying a late entry."
                )
                return

        # 1. Save master trade to Supabase
        try:
            # Check for existing
            existing = self.db.table("trades").select("id").eq("master_trade_id", master_trade_id).execute()
            if existing.data:
                logger.warning(f"Master trade {master_trade_id} already exists in DB, skipping.")
                return

            trade_data = {
                "master_trade_id": master_trade_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "trade_type": trade_type,
                "status": "processing",
                "raw_payload": raw_payload,
                "owner_id": owner_id,
            }
            insert_res = self.db.table("trades").insert(trade_data).execute()
            if not insert_res.data:
                logger.error(f"Failed to insert master trade {master_trade_id} into DB.")
                return
            
            trade_record = insert_res.data[0]
            trade_uuid = trade_record["id"]
        except Exception as e:
            logger.error(f"Failed to save master trade to DB: {e}")
            return

        # 2. Get active follower accounts (scoped to the master's owner)
        try:
            fq = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active")
            if owner_id:
                fq = fq.eq("owner_id", owner_id)
            followers_res = fq.execute()
            followers = followers_res.data or []
        except Exception as e:
            logger.error(f"Failed to query follower accounts: {e}")
            self.db.table("trades").update({"status": "failed"}).eq("id", trade_uuid).execute()
            return

        if not followers:
            logger.info("No active follower accounts found.")
            self.db.table("trades").update({"status": "copied"}).eq("id", trade_uuid).execute()
            
            # Emit completed trade with no copies
            await self.socket_manager.emit_trade_copy({
                "id": trade_uuid,
                "master_trade_id": master_trade_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "trade_type": trade_type,
                "status": "copied",
                "created_at": trade_record.get("created_at"),
                "copies": []
            })
            return

        # 3. Create execution tasks for each follower
        master_balance = 0.0
        try:
            mq = self.db.table("accounts").select("*").eq("is_master", True)
            if owner_id:
                mq = mq.eq("owner_id", owner_id)
            master_acc = mq.execute()
            if master_acc.data:
                master_balance = float(master_acc.data[0].get("allocated_balance") or master_acc.data[0].get("balance") or master_acc.data[0].get("available_margin") or 0.0)
        except Exception as e:
            logger.error(f"Failed to fetch master balance for ratio calculation: {e}")

        # Opens: floor (never over-expose).
        # Closes: rebalance each follower to floor(master_remaining × ratio) —
        # i.e. close only the difference between what the follower holds and what
        # it *should* hold given the master's REMAINING position. This prevents a
        # small master trim of a large position from wiping a small follower
        # (the old ceil(master_close × ratio) rounded every tiny trim up to a
        # full follower lot).
        is_exit = trade_type in ("exit", "sl")
        master_row = master_acc.data[0] if master_acc.data else None

        master_remaining = None
        master_signed_now = None
        if not is_exit:
            # Decide open vs close from the master's post-fill position. If this
            # fill (not flagged reduce_only) leaves the master NOT on the side this
            # order would open (buy=>long / sell=>short), it actually REDUCED/closed
            # the master's position -> reclassify as EXIT. This makes a MANUAL
            # master close propagate to followers immediately, instead of the
            # follower only exiting ~20s later via its own jittered stop.
            master_signed_now = await self._master_position_signed(master_row, symbol, fresh=True)
            sl = (side or "").lower()
            opens = master_signed_now is not None and (
                (sl == "buy" and master_signed_now > 0) or (sl == "sell" and master_signed_now < 0)
            )
            if master_signed_now is not None and not opens:
                is_exit = True
                trade_type = "exit"  # so the follower order is reduce-only
                logger.info(
                    f"Reclassified {symbol} {side} as EXIT (master now {master_signed_now:+.0f}) — "
                    f"unflagged close, mirroring follower close immediately"
                )
        if is_exit:
            master_remaining = await self._master_position_size(master_row, symbol)

        # Order-ID ledger: record the MASTER order before dispatching anything, so
        # a crash mid-dispatch still leaves an entry with no follower legs (i.e.
        # visibly unmirrored) instead of no trace at all.
        await ledger.record_master_order(
            self.redis, master_trade_id, symbol=symbol, side=side, size=quantity,
            price=entry_price, kind="exit" if is_exit else "entry",
            owner_id=owner_id, source="fill", ts=ts_detected,
        )

        tasks = []
        for follower in followers:
            # Inject master balance context
            follower["master_balance"] = master_balance

            client = self.connection_manager.get_client(follower["id"])
            if not client:
                try:
                    client = await self.connection_manager.connect_account(follower)
                except Exception as e:
                    logger.error(f"Failed to connect client for follower {follower['name']}: {e}")
                    self.db.table("trade_copies").insert({
                        "trade_id": trade_uuid,
                        "account_id": follower["id"],
                        "status": "failed",
                        "quantity": 0,
                        "failure_reason": f"Connection error: {e}",
                        "owner_id": follower.get("owner_id"),
                    }).execute()
                    await ledger.record_follower_leg(
                        self.redis, master_trade_id, follower["id"],
                        status="failed", reason=f"connection error: {e}",
                    )
                    continue
            if not client:
                continue

            if is_exit:
                if master_remaining is None:
                    # Couldn't read the master's remaining size — fall back to a
                    # proportional close rather than skipping the exit entirely.
                    follower_qty = self.risk_engine.calculate_follower_quantity(quantity, entry_price, follower, round_up=True)
                else:
                    # round_up=True (min_one=False): one definition of the follower's
                    # target across every path — live close, mirrored close,
                    # escalation, reconciler trim/top-up and the post-cancel settle.
                    # Mixing ceil and floor made two of them close a lot each off the
                    # same 1-lot difference.
                    target = self.risk_engine.calculate_follower_quantity(master_remaining, entry_price, follower, round_up=True, min_one=False)
                    current = await self._position_size(client, symbol)
                    follower_qty = int(current) - int(target)
                    if follower_qty < _size_deadband(target):
                        logger.info(
                            f"No close needed for {follower['name']} on {symbol}: holds {current:.0f}, "
                            f"target {int(target)} (master left {master_remaining:.0f})"
                        )
                        await ledger.record_follower_leg(
                            self.redis, master_trade_id, follower["id"], status="skipped",
                            reason=f"already at target {int(target)} (holds {current:.0f})",
                        )
                        continue
            else:
                # Only open if the master genuinely holds a same-side position.
                # If the master is flat/opposite, this "entry" was really a close
                # that wasn't flagged reduce_only — don't open on the follower.
                same_side = master_signed_now is not None and (
                    (side.lower() == "buy" and master_signed_now > 0)
                    or (side.lower() == "sell" and master_signed_now < 0)
                )
                if master_signed_now is not None and not same_side:
                    logger.info(
                        f"Skipping follower OPEN for {follower['name']} {symbol} {side}: "
                        f"master holds {master_signed_now:+.0f} — not a genuine open (likely an unflagged close)."
                    )
                    await ledger.record_follower_leg(
                        self.redis, master_trade_id, follower["id"], status="skipped",
                        reason=f"not a genuine open (master holds {master_signed_now:+.0f})",
                    )
                    continue
                follower_qty = self.risk_engine.calculate_follower_quantity(quantity, entry_price, follower, round_up=True)

            tasks.append(order_executor.execute(
                client=client,
                account=follower,
                trade_id=trade_uuid,
                symbol=symbol,
                side=side,
                quantity=follower_qty,
                master_price=entry_price,
                trade_type=trade_type
            ))

        # 4. Gather results in parallel
        results = []
        if tasks:
            results = await asyncio.gather(*tasks)
        if ts_detected:
            logger.info(f"[LATENCY] {symbol}: {(time.time() - ts_detected):.2f}s end-to-end (detection → followers executed)")

        # 4b. Telegram notifications — one clean message per follower outcome.
        #     Each outcome is also written to the order-ID ledger against this
        #     master order, so "did follower X get master order Y?" is answerable
        #     later without re-deriving it from positions. (The executor may place
        #     a limit AND a market remainder, so we record the status/qty rather
        #     than a single follower order id.)
        for r in results:
            acct = r.get("account_name") or "Follower"
            st = r.get("status")
            if r.get("account_id"):
                await ledger.record_follower_leg(
                    self.redis, master_trade_id, r["account_id"],
                    status="filled" if st == "filled" else ("skipped" if st in ("skipped", "skipped_circuit_breaker") else "failed"),
                    qty=r.get("filled_quantity"),
                    reason=r.get("failure_reason"),
                )
            if st == "filled":
                lots = r.get("filled_quantity")
                px = r.get("execution_price")
                if is_exit:
                    asyncio.create_task(tg.notify_close(acct, symbol, lots, px))
                else:
                    asyncio.create_task(tg.notify_open(acct, symbol, side, lots, px))
            elif st == "failed":
                reason = r.get("failure_reason") or "order not filled"
                key = f"fail:{r.get('account_id')}:{symbol}:{side}:{'exit' if is_exit else 'entry'}"
                asyncio.create_task(tg.notify_fail(acct, symbol, side, None, reason, key=key))

        # 5. Determine final master trade status
        filled_count = sum(1 for r in results if r.get("status") == "filled")
        failed_count = sum(1 for r in results if r.get("status") == "failed")
        skipped_count = sum(1 for r in results if r.get("status") in ("skipped", "skipped_circuit_breaker"))

        final_status = "copied"
        if failed_count > 0:
            if filled_count > 0:
                final_status = "partial"
            else:
                final_status = "failed"
        elif filled_count == 0 and skipped_count > 0:
            final_status = "failed"

        try:
            self.db.table("trades").update({"status": final_status}).eq("id", trade_uuid).execute()
        except Exception as e:
            logger.error(f"Failed to update master trade status: {e}")

        # 6. Emit Socket.IO event with full results
        trade_event_payload = {
            "id": trade_uuid,
            "master_trade_id": master_trade_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "trade_type": trade_type,
            "status": final_status,
            "created_at": trade_record.get("created_at"),
            "copies": results
        }
        await self.socket_manager.emit_trade_copy(trade_event_payload)
        logger.info(f"Completed trade copy chain. Status: {final_status}. Fills: {filled_count}/{len(followers)}")

    @staticmethod
    def _jitter_trigger(price, seed: str = ""):
        """Offset an SL/TP trigger by a DETERMINISTIC +/- (0..20).

        The offset is derived from the follower (``seed``) and the trigger price,
        NOT random. This means:
          * two legs of a pair that share the same master trigger price get the
            SAME follower price (so a pair stays aligned), and
          * different followers still get different offsets, so they don't all
            trigger at the exact same price/instant.
        """
        if price is None:
            return None
        base = round(float(price), 1)
        h = int(hashlib.sha256(f"{seed}:{base}".encode()).hexdigest(), 16)
        magnitude = h % 21                 # 0..20 inclusive
        sign = 1 if (h >> 7) & 1 else -1
        return round(base + sign * magnitude, 1)

    @staticmethod
    async def _position_size(client, symbol: str) -> float:
        """Live absolute position size for a symbol on the given client (0 if none)."""
        try:
            for p in await client.get_positions():
                s = p.get("product_symbol") or p.get("symbol")
                if s == symbol:
                    return abs(float(p.get("size") or 0))
        except Exception as e:
            logger.warning(f"Position size fetch failed for {symbol}: {e}")
        return 0.0

    @staticmethod
    async def _position_size_signed(client, symbol: str) -> float:
        """Live SIGNED position size (negative = short, positive = long; 0 if none)."""
        try:
            for p in await client.get_positions():
                s = p.get("product_symbol") or p.get("symbol")
                if s == symbol:
                    return float(p.get("size") or 0)
        except Exception as e:
            logger.warning(f"Signed position fetch failed for {symbol}: {e}")
        return 0.0

    async def _place_order_with_retry(self, client, attempts: int = 2, delay: float = 5.0, **kwargs):
        """Place an order, retrying TRANSIENT failures (network / 5xx / 429 /
        timeout) after `delay` seconds — the teammate's "wait 5s then retry"
        rule. A 4xx validation error (e.g. reduce-only side mismatch, bad price)
        is deterministic, so we don't waste retries on it; it's raised at once
        for the caller to log. The 30s reconcile pass is the longer-term retry."""
        last = None
        for i in range(max(1, attempts)):
            try:
                return await client.place_order(**kwargs)
            except Exception as e:
                last = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise  # permanent client error — retrying won't help
                if i < attempts - 1:
                    logger.warning(
                        f"place_order transient failure ({status or e}); retrying in {delay:.0f}s "
                        f"[{kwargs.get('symbol')} {kwargs.get('side')} {kwargs.get('size')}]"
                    )
                    await asyncio.sleep(delay)
        raise last

    def _get_master_client(self, master_row: dict):
        """Reusable DeltaClient for the master, kept alive across calls.

        Four hot helpers (_master_position_size, _master_position_signed,
        _master_recent_fill_ts, _classify_master_exit) each used to CONSTRUCT and
        close a DeltaClient per call. Every construction is a fresh httpx client,
        so every call paid a full TLS handshake to Delta — several times per
        15-second reconcile pass, per symbol. That handshake cost sat directly on
        the event loop and was a large part of why signed orders were reaching the
        exchange 13-21s late.

        Keyed on api_key too, so rotating credentials transparently builds a new
        client rather than reusing a stale one."""
        if not master_row:
            return None
        key = (master_row.get("id"), master_row.get("api_key"))
        client = self._master_clients.get(key)
        if client is None:
            client = DeltaClient(
                master_row["api_key"], master_row["api_secret"],
                master_row.get("environment", "demo"),
            )
            # Only ever one live client per master; drop any older credential.
            for stale in [k for k in self._master_clients if k[0] == key[0]]:
                old = self._master_clients.pop(stale, None)
                if old is not None:
                    asyncio.create_task(old.close())
            self._master_clients[key] = client
        return client

    async def _live_order_ids(self, client, account_id, fresh: bool = False):
        """Set of the follower's resting order ids, cached briefly per account.

        Returns None if the orders couldn't be read at all (callers must then
        assume the order IS live — see _order_is_live)."""
        if not fresh and account_id:
            hit = self._open_orders_cache.get(account_id)
            if hit and (time.time() - hit[1]) < self._OPEN_ORDERS_TTL:
                return hit[0]
        ids, ok = set(), False
        for st in ("open", "pending"):
            try:
                for o in await client.get_open_orders(state=st):
                    ids.add(str(o.get("id")))
                ok = True
            except Exception:
                pass
        if not ok:
            return None
        if account_id:
            self._open_orders_cache[account_id] = (ids, time.time())
        return ids

    async def _order_is_live(self, client, order_id: str, account_id=None) -> bool:
        """True if the given order id is still resting (open/pending).

        This is the single hottest call in the engine: the master re-sends the
        same resting order on every WS update and every reconcile pass, and each
        repeat asked the exchange twice (open + pending) PER FOLLOWER. That REST
        volume was congesting the event loop badly enough to delay order placement
        past Delta's ~5s signature window, so orders were being rejected outright
        with expired_signature (13-21s late, observed 2026-07-30).

        Cached, but ASYMMETRICALLY, because the two wrong answers are not equally
        costly. A stale "live" just skips a re-place for a few seconds and the
        reconciler covers it. A stale "not live" makes us place a DUPLICATE order.
        So a cache hit can only ever answer YES; anything not in the cached set is
        re-checked fresh before we act on it.

        On any read failure, return True — never risk double-placing because a
        status check hiccuped."""
        cached = await self._live_order_ids(client, account_id)
        if cached is not None and str(order_id) in cached:
            return True  # fast path: still resting, no REST call needed
        fresh = await self._live_order_ids(client, account_id, fresh=True)
        if fresh is None:
            return True  # couldn't read — assume live rather than duplicate
        return str(order_id) in fresh

    async def _close_follower_position(self, client, symbol: str, name: str = "") -> None:
        """Market-close (reduce-only) any remaining follower position on `symbol`.
        No-op if the follower is already flat (e.g. its own bracket closed it)."""
        try:
            positions = await client.get_positions()
            pos = next((p for p in positions
                        if (p.get("product_symbol") == symbol or p.get("symbol") == symbol)
                        and float(p.get("size") or 0) != 0), None)
            if not pos:
                return
            sz = float(pos.get("size") or 0)
            qty = int(abs(sz))
            side = "buy" if sz < 0 else "sell"  # close a short with buy, a long with sell
            resp = await client.place_order(
                symbol=symbol, side=side, size=qty, order_type="market_order", reduce_only=True,
            )
            oid = resp.get("id") or resp.get("result", {}).get("id")
            logger.info(f"Closed {name} position on {symbol}: {side} {qty} (reduce-only, matching master exit) order {oid}")
        except Exception as e:
            logger.error(f"Failed to close {name} position on {symbol}: {e}")

    async def _classify_master_exit(self, master_row: dict, symbol: str) -> str:
        """How did the master most recently CLOSE its position on `symbol`?

        Returns one of:
          "sl_tp"   — the last fill on the symbol came from a stop (SL/TP trigger
                      or close-on-trigger bracket). The follower's own jittered
                      stop sits at ~the same price and will close it, so reconcile
                      should LEAVE the follower alone (force-closing just churns).
          "manual"  — the last fill was a plain market/limit close (a manual /
                      discretionary close the live copy missed). The follower's
                      stop is far from price and will NEVER hit, so the follower
                      is an orphan reconcile must close.
          "unknown" — couldn't determine (REST error, or no filled order for the
                      symbol in the recent history window). Caller LEAVES the
                      position and retries next pass — we never force-close on a
                      guess.

        Reads the master's own order history, so it works even when the live WS
        dropped the master's close (the exact case reconcile exists for). Cached
        ~_MASTER_EXIT_TTL s per symbol so the 10s loop doesn't re-pull history
        every pass while waiting."""
        if not master_row:
            return "unknown"
        cached = self._master_exit_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self._MASTER_EXIT_TTL:
            return cached[0]
        try:
            history = await self._get_master_client(master_row).get_order_history(page_size=100)
        except Exception as e:
            logger.warning(f"classify_master_exit: order-history fetch failed for {symbol}: {e}")
            return "unknown"  # transient — do NOT cache; retry next pass

        # Master is flat, so the most recent FILLED order on the symbol is the one
        # that closed it (whether or not reduce_only was set — a manual close via
        # the exchange UI doesn't always set the flag). A stop_order_type on that
        # order => the master exited via SL/TP; anything else => a manual close.
        def _created(o):
            return str(o.get("created_at") or o.get("updated_at") or "")
        result = "unknown"
        for o in sorted(history, key=_created, reverse=True):
            if (o.get("product_symbol") or o.get("symbol")) != symbol:
                continue
            if float(o.get("filled_size") or 0) <= 0:
                continue  # never executed (e.g. a cancelled resting order)
            result = "sl_tp" if o.get("stop_order_type") else "manual"
            break
        if result != "unknown":
            self._master_exit_cache[symbol] = (result, time.time())
            logger.info(f"classify_master_exit: {symbol} -> {result}")
        return result

    @staticmethod
    def _price_drift_ok(mark, entry) -> tuple:
        """Is the current price still close enough to the master's entry to make
        recovering/topping-up a stale leg worthwhile?

        Returns (ok, drift_pct). Unknown prices return ok=True: the alternative is
        the old behaviour of never recovering anything, and a missing leg is a
        certain divergence while price drift is only a possible cost."""
        try:
            e = float(entry or 0)
            m = float(mark or 0)
        except (TypeError, ValueError):
            return True, 0.0
        if e <= 0 or m <= 0:
            return True, 0.0
        drift = abs(m - e) / e * 100.0
        return drift <= SYNC_PRICE_TOLERANCE_PCT, drift

    async def _missed_exit_ids(self, follower: dict, symbol: str) -> str:
        """Master EXIT order ids on `symbol` that never reached this follower, per
        the order-ID ledger — comma-joined for logging.

        This is the audit trail the position-only reconciler never had: when we
        heal a mismatch we can now name the master order that caused it, instead
        of just reporting a net size difference. Diagnostic only — the heal itself
        never depends on it, so an empty result never blocks the fix."""
        try:
            rows = await ledger.missing_for_follower(
                self.redis, follower.get("owner_id"), symbol, follower.get("id"),
                kind="exit",
            )
            return ", ".join(str(r.get("master_order_id")) for r in rows[:5])
        except Exception as e:
            logger.debug(f"ledger lookup failed for {symbol}: {e}")
            return ""

    async def _cancel_follower_stops(self, client, symbol: str, name: str = "") -> None:
        """Cancel any resting SL/TP (stop) orders the follower still has on
        `symbol`. Called after force-closing an orphaned master-flat leg so no
        protective order is left resting on a now-flat position."""
        try:
            for st in ("open", "pending"):
                try:
                    for o in await client.get_open_orders(state=st):
                        if (o.get("product_symbol") or o.get("symbol")) != symbol:
                            continue
                        if not o.get("stop_order_type"):
                            continue  # only protective (SL/TP) orders
                        try:
                            await client.cancel_order(str(o.get("id")), product_id=o.get("product_id"))
                            logger.info(f"reconcile: cancelled stale {o.get('stop_order_type')} on {symbol} for {name} (orphan close)")
                        except Exception as e:
                            logger.warning(f"reconcile: failed to cancel stale stop on {symbol} for {name}: {e}")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"reconcile: cancel-stops sweep failed on {symbol} for {name}: {e}")

    async def _follower_close_qty(self, client, follower: dict, symbol: str, master_row: dict, ref_price: float = 0.0):
        """How many lots the follower should CLOSE to rebalance to the master's
        REMAINING position: follower_current - floor(master_remaining × ratio).
        A small master trim therefore closes ~nothing on a small follower.
        Returns (close_qty, follower_current); close_qty is None if the master
        size can't be read (caller falls back)."""
        master_remaining = await self._master_position_size(master_row, symbol)
        current = await self._position_size(client, symbol)
        if master_remaining is None:
            return None, current
        # round_up=True to match the trim, the top-up and the post-cancel settle.
        # This used to floor while they ceil, so on a ratio of ~0.0098 a 3050-lot
        # master leg gave target 29 here and 30 there — the trim closed down to 30
        # and this path then closed one MORE, twice-reducing the follower off a
        # single 1-lot difference (observed live 2026-07-30 on P-BTC-60000,
        # P-BTC-60500 and C-BTC-67500). min_one=False so a fully-exited master can
        # still take the follower to 0.
        target = self.risk_engine.calculate_follower_quantity(
            master_remaining, ref_price, follower, round_up=True, min_one=False
        )
        # Master still holds but the ratio gave 0 => sizing unavailable, not "close
        # everything". Return None so the caller treats it as undeterminable.
        if master_remaining and int(target) < 1:
            logger.warning(
                "Close sizing unavailable for %s on %s (master holds %s) — not forcing a close",
                follower.get("name"), symbol, master_remaining,
            )
            return None, current
        close_qty = max(0, int(current) - int(target))
        # Same deadband as every other resize path, so ratio noise can't trigger a
        # forced close the reconciler would just undo.
        if close_qty < _size_deadband(target):
            close_qty = 0
        return close_qty, current

    async def _master_position_size(self, master_row: dict, symbol: str, fresh: bool = False):
        """Live absolute master position size for a symbol (cached ~3s so a burst
        of cancels doesn't hammer REST). Pass fresh=True to bypass the cache when
        the answer must be current (e.g. deciding if an SL/TP cancel is a hit).
        Returns None if it can't be determined."""
        if not master_row:
            return None
        if not fresh:
            cached = self._master_pos_cache.get(symbol)
            if cached and (time.time() - cached[1]) < self._MASTER_POS_TTL:
                return cached[0]
        try:
            size = await self._position_size(self._get_master_client(master_row), symbol)
            self._master_pos_cache[symbol] = (size, time.time())
            return size
        except Exception as e:
            logger.error(f"Master position size fetch failed for {symbol}: {e}")
            return None

    async def _master_position_signed(self, master_row: dict, symbol: str, fresh: bool = False):
        """Live SIGNED master position for a symbol (negative=short, positive=long,
        0=flat). Cached ~3s. Used to tell whether a master order OPENS or CLOSES:
        an order opposite the master's held side is a close/reduce, even when the
        master didn't set the reduce_only flag. Returns None if undeterminable."""
        if not master_row:
            return None
        if not fresh:
            cached = self._master_signed_cache.get(symbol)
            if cached and (time.time() - cached[1]) < self._MASTER_POS_TTL:
                return cached[0]
        try:
            signed = await self._position_size_signed(self._get_master_client(master_row), symbol)
            self._master_signed_cache[symbol] = (signed, time.time())
            return signed
        except Exception as e:
            logger.error(f"Master signed position fetch failed for {symbol}: {e}")
            return None

    async def _master_recent_fill_ts(self, master_row: dict) -> dict:
        """Map {symbol: epoch seconds of the master's MOST RECENT fill}. Used to
        gate reconcile-recovery opens on freshness: after downtime the master
        still holds hours-old positions, and re-entering the follower now (at a
        drifted price) is worse than staying out. Fetches the 100 most recent
        fills once; a symbol absent from that window has had no recent activity
        and is therefore treated as stale. (Prathav, 2026-07-29)"""
        out: dict = {}
        if not master_row:
            return out
        try:
            fills = await self._get_master_client(master_row).get_fills(page_size=100)
            for f in fills:
                sym = f.get("product_symbol") or f.get("symbol")
                ts = _parse_epoch(f.get("created_at"))
                if sym and ts is not None and ts > out.get(sym, 0):
                    out[sym] = ts
        except Exception as e:
            logger.warning(f"reconcile freshness: could not fetch master fills: {e}")
        return out

    async def _get_follower_client(self, follower: dict):
        client = self.connection_manager.get_client(follower["id"])
        if not client:
            try:
                client = await self.connection_manager.connect_account(follower)
            except Exception as e:
                logger.error(f"Failed to connect client for follower {follower['name']}: {e}")
                return None
        return client

    async def process_order_event(self, event: dict) -> None:
        """Mirror a master's resting order onto followers (place), cancel the
        mirrored follower orders (cancel), or — when a master SL/TP fills —
        close followers to match the master's exit (exit)."""
        action = event.get("action")
        master_order_id = str(event.get("master_order_id"))
        if action == "place":
            await self._mirror_place(event, master_order_id)
        elif action == "cancel":
            await self._mirror_cancel(master_order_id, event)
        elif action == "exit":
            await self._sync_followers_to_master_exit(event)
        elif action == "master_stop_filled":
            await self._mark_hands_off(event, master_order_id, "master SL/TP triggered")
        elif action == "master_filled":
            await self._escalate_after_master_fill(event, master_order_id)
        elif action == "sync_protection":
            await self._sync_protection(event)
        elif action == "reconcile_positions":
            await self._reconcile_positions(event)

    async def _mark_hands_off(self, event: dict, master_order_id: str, reason: str) -> None:
        """Flag every active follower's leg on this symbol as DO-NOTHING.

        Raised when the master's SL/TP triggers. From that instant the master is
        flat while the follower still holds — which is indistinguishable, from
        positions alone, from an orphan the reconciler should close. It must NOT
        be closed: the follower has its own jittered stop at a slightly different
        price, and that is what should close the position.

        The mark is cleared once the episode is genuinely over — the follower goes
        flat (its stop fired) or the master re-enters the symbol."""
        symbol = event.get("symbol")
        if not symbol:
            return
        try:
            fq = self.db.table("accounts").select("id").eq("is_master", False).eq("status", "active")
            if event.get("owner_id"):
                fq = fq.eq("owner_id", event["owner_id"])
            followers = fq.execute().data or []
        except Exception as e:
            logger.warning(f"hands-off: could not load followers for {symbol}: {e}")
            return
        for f in followers:
            await ledger.mark_hands_off(
                self.redis, event.get("owner_id"), f["id"], symbol, reason,
                master_order_id=master_order_id,
            )
        logger.info(
            f"hands-off: {symbol} marked for {len(followers)} follower(s) — {reason}; "
            f"their own stops will close these legs, reconciler will not touch them"
        )
        # Deliberately NOT sent to Telegram. This is routine, expected behaviour on
        # every master SL/TP trigger, so alerting on it would be constant noise and
        # would bury the notifications that need acting on. The log line above is
        # the record. (Prathav, 2026-08-03)

    async def _escalate_after_master_fill(self, event: dict, master_order_id: str) -> None:
        """The master's resting limit just FILLED. Give each follower's mirrored
        limit ESCALATE_WAIT_SEC to fill at the same price, then force the remainder
        to market.

        This is the correct trigger for the desk rule "limit, 5s, then market".
        The clock starts when the MASTER is filled, not when we place: a master can
        rest a limit for hours, and marketing the follower during that wait would
        put it in or out ahead of the master. Placement-time escalation now defers
        to this (see _escalate_unfilled_limit).

        Without it, a master limit that fills while the follower's mirror sits
        behind it in the queue leaves the follower silently un-copied — how
        C-BTC-67200 ended up holding a position the master had exited."""
        symbol = event.get("symbol")
        try:
            mapping = await self.redis.hgetall(f"ordermap:{master_order_id}")
        except Exception as e:
            logger.warning(f"master_filled: could not read order map for {master_order_id}: {e}")
            return
        if not mapping:
            return  # nothing mirrored for this order (or already cleaned up)

        master_row = None
        try:
            mq = self.db.table("accounts").select("*").eq("is_master", True)
            if event.get("owner_id"):
                mq = mq.eq("owner_id", event["owner_id"])
            m = mq.execute()
            master_row = m.data[0] if m.data else None
        except Exception:
            pass

        for follower_id, follower_order_id in mapping.items():
            try:
                acc = self.db.table("accounts").select("*").eq("id", follower_id).execute()
                if not acc.data:
                    continue
                follower = acc.data[0]
                client = await self._get_follower_client(follower)
                if not client:
                    continue
                # Size the escalation from the FOLLOWER's own order, never the
                # master's — the master's 3000 lots are the follower's 30, and
                # marketing the master's size would blow the follower up.
                fo = await self._safe_get_order(client, follower_order_id)
                if not fo or self._order_done(fo):
                    continue  # already filled or gone — nothing to force
                fqty = int(float(fo.get("size") or 0))
                if fqty < 1:
                    continue
                logger.info(
                    f"Master fill on {symbol}: giving {follower.get('name')}'s mirror "
                    f"{follower_order_id} ({fqty} lots) {ESCALATE_WAIT_SEC}s to fill "
                    f"before forcing market"
                )
                # master_order_id deliberately omitted — we already KNOW it filled,
                # so the "is the master still resting?" guard must not re-check it.
                asyncio.create_task(self._escalate_unfilled_limit(
                    follower, client, follower_order_id, fo.get("product_id") or event.get("product_id"),
                    symbol, fo.get("side") or event.get("side"), fqty,
                    bool(fo.get("reduce_only")), master_row,
                    event.get("limit_price"),
                ))
            except Exception as e:
                logger.warning(f"master_filled: escalation setup failed for {follower_id}: {e}")

    async def _reconcile_positions(self, event: dict) -> None:
        """Every 10s: make each follower's OPEN POSITIONS match the master's,
        recovering anything the live copy missed (e.g. a WS-dropped entry).

          • Master holds a leg the follower is FLAT on -> OPEN it (market, current
            price — the master's entry moment has passed). Skipped if the follower
            already has a resting order on that symbol (live copy is on it) or if
            we opened it within the debounce window.
          • Follower holds a leg the master is FLAT on, or on the OPPOSITE side
            (a desync) -> CLOSE it (reduce-only market).
          • Follower holds the RIGHT side but MORE than its share of what the
            master still holds -> TRIM the excess (reduce-only market). This is
            the incomplete-exit case: side-only comparison saw "follower is short,
            master is short, fine" and left the follower carrying lots the master
            had already sold. Guarded so rounding, positions still being built and
            transient reads can't cause a wrong trim (see the block itself), and
            the order-ID ledger names the master exit involved. NOTE this is the
            BACKSTOP — a master that cancels its exit is settled immediately by
            _settle_exit_after_cancel; this catches the case where it never does.

        NOTE on the master-FLAT case when the follower still has its own resting
        SL/TP: whether we close depends on HOW the master got flat (see
        _classify_master_exit). If the master exited via its own SL/TP hit, we
        LEAVE the follower — its jittered stop sits at ~the same price and closes
        it on its own within seconds; force-closing would just churn. If the
        master closed MANUALLY (a market/limit close the live copy missed), the
        follower's stop is far from price and will NEVER hit, so the follower is
        an orphan we close (and we cancel its now-stale stop). A wrong-SIDE desync
        (master NOT flat) always closes, unconditionally."""
        owner_id = event.get("owner_id")
        master_map = {}
        for p in (event.get("positions") or []):
            sym = p.get("symbol")
            if sym:
                master_map[sym] = (float(p.get("size") or 0), p.get("mark"), p.get("entry"))

        # Master balance for proportional sizing of any recovery open, and the
        # master row itself so we can classify how the master closed a symbol.
        master_balance = 0.0
        master_row = None
        try:
            mq = self.db.table("accounts").select("*").eq("is_master", True)
            if owner_id:
                mq = mq.eq("owner_id", owner_id)
            m = mq.execute()
            if m.data:
                master_row = m.data[0]
                master_balance = float(master_row.get("allocated_balance") or master_row.get("balance") or master_row.get("available_margin") or 0.0)
        except Exception:
            pass

        try:
            fq = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active")
            if owner_id:
                fq = fq.eq("owner_id", owner_id)
            followers = fq.execute().data or []
        except Exception as e:
            logger.error(f"reconcile_positions: failed to load followers: {e}")
            return

        now = time.time()

        # Which way is the master going on each symbol? A snapshot alone is not
        # enough to justify ADDING exposure: on 2026-08-02 the master was two
        # minutes into a multi-hour unwind of P-BTC-63000-030826 but still showed
        # +560, so the top-up bought a lot back 79s after the master had sold —
        # and the whole position was closed as an orphan two hours later. Any
        # observed decrease marks the symbol as REDUCING for a cooldown, during
        # which top-ups are suppressed. Trims are unaffected: reducing on a
        # snapshot is safe, adding is not.
        for _sym, _p in master_map.items():
            _cur = abs(float(_p[0] or 0))
            _prev = self._master_size_prev.get(_sym)
            if _prev is not None and _cur < _prev:
                self._master_reducing_until[_sym] = now + self._REDUCING_COOLDOWN
            self._master_size_prev[_sym] = _cur
        # A symbol the master has fully exited is also "reducing" until it re-enters.
        for _sym in list(self._master_size_prev):
            if _sym not in master_map:
                if self._master_size_prev.get(_sym):
                    self._master_reducing_until[_sym] = now + self._REDUCING_COOLDOWN
                self._master_size_prev[_sym] = 0.0

        current_open: set = set()   # (follower_id, sym) missing THIS pass
        current_close: set = set()  # (follower_id, sym) orphan/opposite THIS pass
        current_trim: set = set()   # (follower_id, sym, excess) over-exposed THIS pass
        current_topup: set = set()  # (follower_id, sym, short_by) under-exposed THIS pass
        # Lazily fetched once per pass (shared across followers): {sym: last master
        # fill epoch}. Gates recovery-opens so we never re-enter a stale leg.
        master_fresh_ts = None
        for fol in followers:
            fol["master_balance"] = master_balance
            fid = fol.get("id")
            client = await self._get_follower_client(fol)
            if not client:
                # keep prior candidates alive so a fetch blip doesn't reset the streak
                continue
            try:
                fpos = {}
                for p in await client.get_positions():
                    s = p.get("product_symbol") or p.get("symbol")
                    sz = float(p.get("size") or 0)
                    if s and sz != 0:
                        fpos[s] = sz
                resting = set()          # symbols with ANY resting order (incl. SL/TP)
                resting_plain: dict = {}  # symbol -> [non-protective resting orders]
                for st in ("open", "pending"):
                    try:
                        for o in await client.get_open_orders(state=st):
                            psym = o.get("product_symbol")
                            if not psym:
                                continue
                            resting.add(psym)
                            if not o.get("stop_order_type"):
                                resting_plain.setdefault(psym, []).append(o)
                    except Exception:
                        pass

                # Release any hands-off mark whose episode is over: the follower has
                # gone flat (its own stop fired, as intended) or the master has
                # re-entered the symbol. Without this the leg would be excluded from
                # reconciliation for the full TTL.
                for sym, _p in master_map.items():
                    if _p[0]:
                        await ledger.clear_hands_off(self.redis, fol.get("owner_id"), fid, sym)
                for sym in await ledger.list_hands_off(self.redis, fol.get("owner_id"), fid):
                    if sym not in fpos:
                        logger.info(
                            f"hands-off cleared for {fol.get('name')} {sym} — follower is "
                            f"flat (its own stop did the job)"
                        )
                        await ledger.clear_hands_off(self.redis, fol.get("owner_id"), fid, sym)

                # 1) CLOSE / TRIM — follower holds a leg the master is flat on, is
                #    on the OPPOSITE side, or is on the right side at the wrong SIZE.
                for sym, fsz in list(fpos.items()):
                    msz = master_map.get(sym, (0, None, None))[0]
                    same_side = (fsz > 0 and msz > 0) or (fsz < 0 and msz < 0)
                    if same_side:
                        # Right side — but is it the right SIZE? An exit the follower
                        # didn't complete leaves it over-exposed on the CORRECT side,
                        # which a side-only comparison cannot see. On
                        # C-BTC-67200-300726 (2026-07-29) the master's exit limit
                        # half-filled while the follower's mirrored copy filled
                        # nothing, so the follower held 30 against a target of 15 for
                        # hours and every check said "both short, fine".
                        #
                        # The target uses round_up=True — the same rounding the OPEN
                        # path used to size the position — so ordinary ceil/floor
                        # rounding can never masquerade as over-exposure and trigger
                        # a spurious trim. Only a genuinely missed reduction shows up.
                        # A plain resting order on this symbol USED to skip the trim
                        # outright ("a close is in flight, don't race it"). That was
                        # the flaw that would have missed 67200 entirely: the
                        # follower's mirrored exit sat there unfilled for 2.5h, so the
                        # guard suppressed the fix for the whole incident. It can't
                        # tell "in flight" from "stuck".
                        #
                        # Split the two cases by what the order would DO (derived from
                        # the side, not the reduce_only flag, which the exchange
                        # doesn't always set):
                        #   • anything that would ADD to the position -> genuinely
                        #     unsettled (the follower may be building) -> skip.
                        #   • only reducing orders -> a stuck close. Cancel them, then
                        #     trim. Cancelling FIRST matters: leaving a reduce-only
                        #     order resting while we market the excess would double-
                        #     close the follower if it later filled.
                        stuck_closes = []
                        adding = False
                        for o in resting_plain.get(sym, []):
                            oside = (o.get("side") or "").lower()
                            reduces = (oside == "buy" and fsz < 0) or (oside == "sell" and fsz > 0)
                            if reduces:
                                stuck_closes.append(o)
                            else:
                                adding = True
                        if adding:
                            continue  # position still being built — size not settled
                        held = int(abs(fsz))
                        _m = master_map.get(sym, (0, None, None))
                        mark, mentry = _m[1], _m[2]
                        target = self.risk_engine.calculate_follower_quantity(
                            abs(msz), float(mark) if mark else 0.0, fol, round_up=True
                        )
                        # A zero target while the master still HOLDS means the ratio
                        # could not be computed (balance read as 0) — NOT that the
                        # follower should be flat. Trimming on it would close the
                        # entire position. Never act on an unavailable ratio.
                        if int(target) < 1:
                            logger.warning(
                                f"reconcile: sizing unavailable for {fol.get('name')} on {sym} "
                                f"(master {msz:+.0f}, holds {held}) — leaving it alone this pass"
                            )
                            continue
                        excess = held - int(target)
                        # Deadband: the target moves with a live balance ratio, so a
                        # 1-lot difference on a 30-lot leg is noise, not a miss.
                        # Acting on it churns (trim 1, top-up 1, repeat) — see
                        # RECON_SIZE_TOLERANCE_PCT.
                        if abs(excess) < _size_deadband(target):
                            continue
                        # Don't act while the master is still trading this symbol —
                        # its size is moving and any diff we see is transient.
                        if master_fresh_ts is None:
                            master_fresh_ts = await self._master_recent_fill_ts(master_row)
                        last_fill = master_fresh_ts.get(sym)
                        if last_fill is not None and (now - last_fill) < TRIM_SETTLE_SEC:
                            continue

                        # ---- UNDER-exposed: the follower holds LESS than its share.
                        # Nothing used to handle this at all — the open path only fires
                        # when the follower is completely FLAT, so a partially-filled or
                        # partially-missed entry stayed short of target indefinitely
                        # (2026-07-30 audit: 4 symbols, all under). Top up the shortfall,
                        # subject to the same price guard as a stale recovery since this
                        # is ADDING exposure at today's price.
                        if excess <= -1:
                            short_by = -excess
                            # NEVER add while the master is unwinding this symbol.
                            reducing_until = self._master_reducing_until.get(sym, 0)
                            if now < reducing_until:
                                logger.info(
                                    f"reconcile: {fol.get('name')} under-exposed on {sym} "
                                    f"({held} vs {int(target)}) but master is REDUCING — "
                                    f"not topping up for another {reducing_until - now:.0f}s"
                                )
                                continue
                            ok, drift = self._price_drift_ok(mark, mentry)
                            if not ok:
                                logger.info(
                                    f"reconcile: {fol.get('name')} under-exposed on {sym} "
                                    f"({held} vs target {int(target)}) but price drifted "
                                    f"{drift:.1f}% from master entry {mentry} — NOT topping up"
                                )
                                asyncio.create_task(tg.notify_fail(
                                    fol.get("name"), sym, "topup", short_by,
                                    f"price drifted {drift:.0f}% from master entry",
                                    key=f"drift:{fid}:{sym}", window=3600,
                                ))
                                self._recon_open_ts[(fid, sym)] = now
                                continue
                            ukey = (fid, sym, short_by)
                            current_topup.add(ukey)
                            if ukey not in self._recon_topup_prev:
                                logger.info(
                                    f"reconcile: {fol.get('name')} under-exposed on {sym} — holds "
                                    f"{held}, target {int(target)} (short {short_by}) — confirming "
                                    f"next pass before topping up"
                                )
                                continue
                            if now - self._recon_open_ts.get((fid, sym), 0) < self._RECON_OPEN_DEBOUNCE:
                                continue
                            self._recon_open_ts[(fid, sym)] = now
                            side = "buy" if fsz > 0 else "sell"
                            try:
                                await client.place_order(
                                    symbol=sym, side=side, size=int(short_by),
                                    order_type="market_order", reduce_only=False,
                                )
                                logger.info(
                                    f"reconcile: TOPPED UP {fol.get('name')} {sym} by {short_by} "
                                    f"({held} -> {int(target)}, master {msz:+.0f}, drift {drift:.1f}%)"
                                )
                                asyncio.create_task(tg.notify_open(
                                    fol.get("name"), sym, side, int(short_by), mark or None))
                            except Exception as e:
                                body = getattr(getattr(e, "response", None), "text", "")
                                logger.warning(f"reconcile top-up failed for {fol.get('name')} {sym}: {e} {body}")
                                asyncio.create_task(tg.notify_fail(
                                    fol.get("name"), sym, side, int(short_by), _short_reason(e, body),
                                    key=f"topup:{fid}:{sym}", window=1800,
                                ))
                            continue

                        # ---- OVER-exposed: an exit the follower didn't complete.
                        tkey = (fid, sym, excess)
                        current_trim.add(tkey)
                        if tkey not in self._recon_trim_prev:
                            logger.info(
                                f"reconcile: {fol.get('name')} over-exposed on {sym} — holds "
                                f"{held}, target {int(target)} for master {msz:+.0f} "
                                f"(excess {excess}) — confirming next pass before trimming"
                            )
                            continue
                        missed = await self._missed_exit_ids(fol, sym)
                        side = "sell" if fsz > 0 else "buy"
                        try:
                            # Clear any stuck reduce-only limit first, so it can't
                            # fill on top of the market close below.
                            for o in stuck_closes:
                                if await self._safe_cancel(client, o.get("id"), o.get("product_id")):
                                    logger.info(
                                        f"reconcile: cancelled stuck close {o.get('id')} on {sym} "
                                        f"for {fol.get('name')} before trimming (never filled)"
                                    )
                            await client.place_order(
                                symbol=sym, side=side, size=int(excess),
                                order_type="market_order", reduce_only=True,
                            )
                            logger.info(
                                f"reconcile: TRIMMED {fol.get('name')} {sym} by {excess} "
                                f"({held} -> {int(target)}, master {msz:+.0f}) — missed master "
                                f"exit order(s): {missed or 'none recorded in ledger'}"
                            )
                            asyncio.create_task(tg.notify_close(fol.get("name"), sym, int(excess)))
                        except Exception as e:
                            body = getattr(getattr(e, "response", None), "text", "")
                            logger.warning(f"reconcile trim failed for {fol.get('name')} {sym}: {e} {body}")
                        continue
                    # Master FLAT but the follower still holds AND has its own
                    # resting SL/TP: only close it if the master got flat by a
                    # MANUAL close (its stop won't hit -> orphan). If the master
                    # exited via SL/TP, leave it — the follower's own jittered stop
                    # is at ~the same price and closes it on its own (no churn). If
                    # we can't tell, leave it and retry next pass (never guess).
                    # HANDS-OFF: the master's SL/TP triggered on this symbol, so it
                    # is flat while the follower still holds. That is NOT an orphan —
                    # the follower's own jittered stop is what should close it. Never
                    # force it, regardless of how long it takes or how the master's
                    # exit classifies.
                    if msz == 0:
                        hoff = await ledger.is_hands_off(self.redis, fol.get("owner_id"), fid, sym)
                        if hoff:
                            logger.info(
                                f"reconcile: leaving {fol.get('name')} {sym} {fsz:+.0f} alone — "
                                f"hands-off ({hoff.get('reason')}); its own stop closes this leg"
                            )
                            continue
                    if msz == 0 and sym in resting:
                        reason = await self._classify_master_exit(master_row, sym)
                        if reason != "manual":
                            # That deferral is UNBOUNDED, which is how an orphan can
                            # sit forever: for a busy (grid) master the close rolls
                            # out of the recent order-history window and classify
                            # returns "unknown" on every pass. Escape hatch — if the
                            # master hasn't filled anything on this symbol in a long
                            # while, the follower's own jittered stop was never going
                            # to fire alongside the master's either, so it IS an
                            # orphan and we stop waiting.
                            if master_fresh_ts is None:
                                master_fresh_ts = await self._master_recent_fill_ts(master_row)
                            last_fill = master_fresh_ts.get(sym)
                            if last_fill is not None and (now - last_fill) <= STALE_ORPHAN_SEC:
                                continue  # recent master exit — let the follower's
                                          # own stop have its chance first
                            age_txt = f"{now - last_fill:.0f}s ago" if last_fill else "not in recent window"
                            logger.info(
                                f"reconcile: {fol.get('name')} still holds {sym} {fsz:+.0f} behind a "
                                f"resting stop; master exit reads '{reason}' but is stale (last master "
                                f"fill {age_txt}) — treating as an orphan and closing"
                            )
                    key = (fid, sym)
                    current_close.add(key)
                    if key not in self._recon_close_prev:
                        continue  # first sighting — confirm next pass before acting
                    side = "sell" if fsz > 0 else "buy"
                    try:
                        await client.place_order(
                            symbol=sym, side=side, size=int(abs(fsz)),
                            order_type="market_order", reduce_only=True,
                        )
                        why = "manual master close orphan" if msz == 0 else "wrong-side desync"
                        missed = await self._missed_exit_ids(fol, sym)
                        logger.info(
                            f"reconcile: closed {fol.get('name')} {sym} {fsz:+.0f} (master {msz:+.0f}) "
                            f"— {why}; missed master exit order(s): {missed or 'none recorded in ledger'}"
                        )
                        asyncio.create_task(tg.notify_close(fol.get("name"), sym, int(abs(fsz))))
                        # Master-flat close leaves the follower's own SL/TP resting
                        # on a now-flat leg — nothing else cancels it once the
                        # master's gone, so clear it here.
                        if msz == 0:
                            await self._cancel_follower_stops(client, sym, fol.get("name"))
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.warning(f"reconcile close failed for {fol.get('name')} {sym}: {e} {body}")

                # 2) OPEN — master holds a leg the follower is flat on (recover miss).
                for sym, (msz, mark, mentry) in master_map.items():
                    if msz == 0 or fpos.get(sym, 0) != 0:
                        continue
                    if sym in resting:
                        continue  # live copy already has a resting order here
                    key = (fid, sym)
                    current_open.add(key)
                    if key not in self._recon_open_prev:
                        continue  # first sighting — a just-filled mirror may not be
                                  # reflected yet; confirm on the next pass (avoids a
                                  # duplicate entry racing the live copy)
                    if now - self._recon_open_ts.get(key, 0) < self._RECON_OPEN_DEBOUNCE:
                        continue
                    price = float(mark) if mark else 0.0
                    target = self.risk_engine.calculate_follower_quantity(abs(msz), price, fol, round_up=True)
                    if target < 1:
                        continue
                    # A leg the master entered RECENTLY is recovered outright. An
                    # older one used to be refused unconditionally, which meant a
                    # missed entry was never recovered — the reconciler logged "SKIP
                    # stale entry ... leaving follower flat" every 15s indefinitely
                    # while the follower drifted further from the master (2026-07-30
                    # audit: 5 missing legs, the oldest 3.8h and still being skipped).
                    #
                    # Age is the wrong test. What actually matters is whether the
                    # price has moved away from what the master paid: judge on PRICE
                    # drift instead, and when it IS too far, say so loudly rather
                    # than silently doing nothing forever. (desk call, 2026-07-30)
                    if master_fresh_ts is None:
                        master_fresh_ts = await self._master_recent_fill_ts(master_row)
                    last_fill = master_fresh_ts.get(sym)
                    is_fresh = last_fill is not None and (now - last_fill) <= FRESH_ENTRY_SEC
                    if not is_fresh:
                        ok, drift = self._price_drift_ok(mark, mentry)
                        age_txt = f"{now - last_fill:.0f}s ago" if last_fill else "no recent fill"
                        if not ok:
                            logger.info(
                                f"reconcile: NOT recovering {fol.get('name')} {sym} — price drifted "
                                f"{drift:.1f}% from master entry {mentry} (mark {mark}, master last "
                                f"fill {age_txt}, tolerance {SYNC_PRICE_TOLERANCE_PCT:.0f}%)"
                            )
                            # ONE message per episode, not one per 15s pass. Cleared
                            # below when the leg is finally recovered, so a fresh
                            # occurrence later still alerts.
                            asyncio.create_task(tg.notify_fail(
                                fol.get("name"), sym, "recover", int(target),
                                f"price drifted {drift:.0f}% from master entry — follower left flat",
                                key=f"drift:{fid}:{sym}", window=tg.STATE_ALERT_WINDOW,
                            ))
                            self._recon_open_ts[key] = now
                            continue
                        logger.info(
                            f"reconcile: recovering stale leg {fol.get('name')} {sym} "
                            f"(master last fill {age_txt}) — price within {drift:.1f}% of master "
                            f"entry {mentry}, inside the {SYNC_PRICE_TOLERANCE_PCT:.0f}% tolerance"
                        )
                    self._recon_open_ts[key] = now
                    side = "buy" if msz > 0 else "sell"
                    try:
                        await client.place_order(
                            symbol=sym, side=side, size=int(target),
                            order_type="market_order", reduce_only=False,
                        )
                        logger.info(f"reconcile: opened {fol.get('name')} {sym} {side} {int(target)} (master {msz:+.0f}) — recovered missing leg")
                        asyncio.create_task(tg.notify_open(fol.get("name"), sym, side, int(target), price or None))
                        # Episode over — let a future drift/failure on this leg
                        # alert again instead of being silently suppressed.
                        asyncio.create_task(tg.clear_alert(f"drift:{fid}:{sym}"))
                        asyncio.create_task(tg.clear_alert(f"recon:{fid}:{sym}"))
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.warning(f"reconcile open failed for {fol.get('name')} {sym}: {e} {body}")
                        asyncio.create_task(tg.notify_fail(
                            fol.get("name"), sym, side, int(target), _short_reason(e, body),
                            key=f"recon:{fol.get('id')}:{sym}", window=1800,
                        ))
            except Exception as e:
                logger.warning(f"reconcile_positions error for {fol.get('name')}: {e}")

        # Remember this pass's candidates so the next pass can confirm them.
        self._recon_open_prev = current_open
        self._recon_close_prev = current_close
        self._recon_trim_prev = current_trim
        self._recon_topup_prev = current_topup

    async def _sync_protection(self, event: dict) -> None:
        """Cancel any follower SL/TP whose master counterpart no longer exists.

        Removing a position's TP/SL on the master doesn't always emit a WS cancel
        event, so relying on _mirror_cancel alone can leave a follower's stop
        resting after the master dropped it. This reconciliation (driven by the
        listener's periodic sweep) matches by (symbol, stop_order_type), ignoring
        the jittered price: if the master holds the position but has NO protective
        order of that type, the follower's matching one is an orphan → cancel it.
        We only touch symbols the master still holds (a flat master is handled by
        the exit-close path), so we never strip protection the master still wants."""
        owner_id = event.get("owner_id")
        # Payload is now a list of dicts (symbol + trigger detail) so we can PLACE
        # missing protection, not just cancel orphans. Older queued events used
        # [symbol, type] pairs — accept both so a deploy mid-flight can't break.
        master_prot: set = set()
        prot_detail: dict = {}
        for item in (event.get("master_protection") or []):
            if isinstance(item, dict):
                s, t = item.get("symbol"), item.get("stop_order_type")
                if s and t:
                    master_prot.add((s, t))
                    prot_detail[(s, t)] = item
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                master_prot.add((item[0], item[1]))
        master_symbols = set(event.get("master_symbols") or [])
        if not master_symbols:
            return
        try:
            fq = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active")
            if owner_id:
                fq = fq.eq("owner_id", owner_id)
            followers = fq.execute().data or []
        except Exception as e:
            logger.error(f"sync_protection: failed to load followers: {e}")
            return
        current_orphans: set = set()
        current_missing: set = set()
        for fol in followers:
            client = await self._get_follower_client(fol)
            if not client:
                continue
            try:
                orders = []
                seen = set()
                read_ok = False
                for st in ("pending", "open"):
                    try:
                        for o in await client.get_open_orders(state=st):
                            if o.get("id") in seen:
                                continue
                            seen.add(o.get("id"))
                            orders.append(o)
                        read_ok = True
                    except Exception:
                        pass
                for o in orders:
                    stype = o.get("stop_order_type")
                    sym = o.get("product_symbol")
                    if not stype or not sym:
                        continue  # only protective (SL/TP) orders
                    if sym not in master_symbols:
                        continue  # master no longer holds this symbol — exit path handles it
                    if (sym, stype) in master_prot:
                        continue  # master still has this protection — keep it
                    # Orphan candidate: master holds the position but has no such
                    # SL/TP. Only cancel if it was ALSO an orphan last sweep, so a
                    # transient mid-edit snapshot can't trigger a wrong cancel.
                    okey = (fol.get("id"), sym, stype)
                    current_orphans.add(okey)
                    if okey not in self._prot_orphans_prev:
                        logger.info(f"sync_protection: {stype} on {sym} for {fol.get('name')} looks orphaned — confirming next sweep before cancelling")
                        continue
                    try:
                        await client.cancel_order(str(o.get("id")), product_id=o.get("product_id"))
                        logger.info(f"sync_protection: cancelled orphan {stype} on {sym} for {fol.get('name')} (master removed it)")
                    except Exception as e:
                        logger.warning(f"sync_protection: failed to cancel {stype} on {sym} for {fol.get('name')}: {e}")

                # ---- ADD protection the follower is MISSING. ----
                # Until now this sweep could only ever CANCEL: nothing anywhere in
                # the engine could place a stop a follower never got. Brackets are
                # excluded from the order re-mirror (reconciling them could
                # double-place), so a single failed bracket mirror left that position
                # unprotected indefinitely — 2026-07-30 audit found a follower short
                # 31 lots of C-BTC-67500-310726 with no stop at all, and another
                # holding C-BTC-65400-300726 with no TP.
                if not read_ok or not prot_detail:
                    continue  # couldn't read the follower's orders — never guess
                try:
                    fheld = {}
                    for p in await client.get_positions():
                        s = p.get("product_symbol") or p.get("symbol")
                        sz = float(p.get("size") or 0)
                        if s and sz:
                            fheld[s] = sz
                except Exception as e:
                    logger.warning(f"sync_protection: positions read failed for {fol.get('name')}: {e}")
                    continue
                have = {(o.get("product_symbol"), o.get("stop_order_type"))
                        for o in orders if o.get("stop_order_type")}
                for (sym, stype), det in prot_detail.items():
                    if sym not in fheld:
                        continue  # follower doesn't hold it — nothing to protect
                    if (sym, stype) in have:
                        continue  # already protected
                    mkey = (fol.get("id"), sym, stype)
                    current_missing.add(mkey)
                    if mkey not in self._prot_missing_prev:
                        logger.info(
                            f"sync_protection: {fol.get('name')} holds {sym} with no {stype} "
                            f"— confirming next sweep before placing"
                        )
                        continue
                    stop_price = det.get("stop_price")
                    product_id = det.get("product_id")
                    if stop_price is None or not product_id:
                        continue
                    try:
                        jittered = self._jitter_trigger(float(stop_price), seed=str(fol.get("id")))
                        otype = det.get("order_type") or "market_order"
                        leg = {"order_type": otype, "stop_price": str(jittered)}
                        if otype == "limit_order" and det.get("limit_price") is not None:
                            leg["limit_price"] = str(det["limit_price"])
                        sl = leg if stype == "stop_loss_order" else None
                        tp = leg if stype == "take_profit_order" else None
                        resp = await client.place_bracket(
                            product_id=product_id, stop_loss=sl, take_profit=tp,
                            trigger_method=det.get("trigger") or "mark_price",
                        )
                        result = resp.get("result", resp) if isinstance(resp, dict) else {}
                        foid = (result.get(stype) or {}).get("id") if isinstance(result, dict) else None
                        moid = det.get("master_order_id")
                        if foid and moid:
                            # Map it like a live mirror would, so a later master
                            # cancel/edit of this stop can find the follower's copy.
                            await self.redis.hset(f"ordermap:{moid}", fol["id"], str(foid))
                            await self.redis.expire(f"ordermap:{moid}", 7 * 24 * 3600)
                            await ledger.record_follower_leg(
                                self.redis, moid, fol["id"], status="placed",
                                follower_order_id=foid, reason="protection restored by sweep",
                            )
                        logger.info(
                            f"sync_protection: PLACED missing {stype} on {sym} for "
                            f"{fol.get('name')} @ {jittered} (master {stop_price}, order {foid})"
                        )
                        asyncio.create_task(tg.send_alert({
                            "level": "warning", "type": "protection_restored",
                            "message": (f"{fol.get('name')} was holding {sym} with no {stype} — "
                                        f"restored at {jittered}"),
                        }))
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.error(
                            f"sync_protection: FAILED to place {stype} on {sym} for "
                            f"{fol.get('name')}: {e} {body}"
                        )
                        asyncio.create_task(tg.notify_fail(
                            fol.get("name"), sym, stype, None, _short_reason(e, body),
                            key=f"prot:{fol.get('id')}:{sym}:{stype}", window=1800,
                        ))
            except Exception as e:
                logger.warning(f"sync_protection: error for {fol.get('name')}: {e}")
        # Remember this sweep's candidates so the next sweep can confirm them.
        self._prot_orphans_prev = current_orphans
        self._prot_missing_prev = current_missing

    async def _sync_followers_to_master_exit(self, event: dict) -> None:
        """Retired by strategy decision: we no longer force-close followers when a
        master SL/TP fills. Each follower has its own mirrored (jittered) SL/TP
        that closes its position at ~the same level; forcing a market close caused
        wasteful sell-then-buyback round-trips with bad fills in fast moves. Kept
        as a no-op so any stray 'exit' event can't reintroduce a forced close."""
        logger.debug("exit event ignored (%s) — followers close via their own jittered SL/TP", (event or {}).get("symbol"))
        return

    async def _mirror_place(self, event: dict, master_order_id: str) -> None:
        symbol = event.get("symbol")
        side = event.get("side")
        master_qty = float(event.get("size") or 0)
        order_type = event.get("order_type") or "limit_order"
        limit_price = float(event["limit_price"]) if event.get("limit_price") else None
        stop_price = float(event["stop_price"]) if event.get("stop_price") else None
        reduce_only = bool(event.get("reduce_only"))
        owner_id = event.get("owner_id")
        if not symbol or not side or master_qty <= 0:
            return
        ts = event.get("ts")
        if ts:
            logger.info(f"[LATENCY] {symbol} order-mirror: {(time.time() - ts):.2f}s from master order detection to mirror start")

        # Active followers (scoped to the master's owner)
        try:
            fq = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active")
            if owner_id:
                fq = fq.eq("owner_id", owner_id)
            followers = fq.execute().data or []
        except Exception as e:
            logger.error(f"Failed to query followers for order mirror: {e}")
            return
        if not followers:
            return

        # Master balance for the ratio
        master_balance = 0.0
        master_row = None
        try:
            mq = self.db.table("accounts").select("*").eq("is_master", True)
            if owner_id:
                mq = mq.eq("owner_id", owner_id)
            m = mq.execute()
            if m.data:
                master_row = m.data[0]
                master_balance = float(master_row.get("allocated_balance") or master_row.get("balance") or master_row.get("available_margin") or 0.0)
        except Exception:
            pass

        is_bracket = bool(event.get("is_bracket"))
        is_update = bool(event.get("is_update"))
        product_id = event.get("product_id")
        stop_order_type = event.get("stop_order_type")
        trigger_method = event.get("stop_trigger_method") or "mark_price"

        # Infer a CLOSE even when the master didn't set reduce_only: if this order
        # is on the OPPOSITE side of the master's current position, it's reducing
        # the master (a close/trim), not opening. Treat it as reduce-only so we
        # NEVER open a fresh follower position for a master close — followers then
        # only reduce their matching position (and do nothing if they hold none).
        if not reduce_only and not is_bracket and stop_price is None and master_row:
            msigned = await self._master_position_signed(master_row, symbol)
            if msigned is not None and (
                (side == "sell" and msigned > 0) or (side == "buy" and msigned < 0)
            ):
                logger.info(
                    f"Inferred reduce-only for {symbol} {side}: master holds {msigned:+.0f} "
                    f"(order reduces it, reduce_only flag was not set)"
                )
                reduce_only = True

        ref_price = limit_price or stop_price or 0.0

        # Order-ID ledger: record the master's resting order. Called again on every
        # WS update / reconcile pass for the same order — record_master_order is
        # idempotent, so that just refreshes the entry. Brackets are the master's
        # own protection, not a directional order, so they're logged as such and
        # never counted as a missed entry/exit.
        _kind = "bracket" if is_bracket else ("exit" if reduce_only else "entry")
        await ledger.record_master_order(
            self.redis, master_order_id, symbol=symbol, side=side, size=master_qty,
            price=ref_price or None, kind=_kind, owner_id=owner_id, source="order",
            ts=event.get("ts"),
        )

        # Is this the master's PROTECTION (SL/TP) rather than a directional order?
        # Decided once: it changes how the mirror is sized (cover the position, not
        # "close the difference") — see the branch in the follower loop below.
        is_protective_order = bool(stop_order_type or stop_price is not None)

        for follower in followers:
            follower["master_balance"] = master_balance
            # Floor so the mirrored order quantity matches the follower's position
            # (which was also floored on open). reduce_only caps it anyway.
            qty = self.risk_engine.calculate_follower_quantity(master_qty, ref_price, follower, round_up=True)
            if qty < 1:
                # Sizing unavailable (e.g. the master's balance read as 0, so the
                # ratio can't be computed). Skip rather than guess — the reconciler
                # picks this leg up once balances read again.
                logger.warning(
                    f"Skipping mirror to {follower.get('name')} on {symbol}: "
                    f"sizing unavailable for master qty {master_qty}"
                )
                await ledger.record_follower_leg(
                    self.redis, master_order_id, follower["id"], status="skipped",
                    reason="sizing unavailable (balance ratio could not be computed)",
                )
                continue
            place_stop_price = stop_price  # per-follower (jittered for protection)
            client = await self._get_follower_client(follower)
            if not client:
                continue

            # Idempotency for resting orders: if this master order is already
            # mirrored to this follower, only act again when the master EDITED it
            # (is_update). We now mirror on order STATE (open/pending) rather than
            # the create action, so the same resting order can surface on repeated
            # WS updates and via the reconcile pass — this guard stops duplicates
            # across every path (bracket / plain limit / reduce-only close).
            # BUT verify the mapped follower order still exists: we only listen to
            # the master's WS, so a mirrored order that already filled/cancelled
            # leaves a stale map that would otherwise block re-placing forever.
            if not is_update:
                mapped = await self.redis.hget(f"ordermap:{master_order_id}", follower["id"])
                if mapped:
                    if await self._order_is_live(client, mapped, follower["id"]):
                        continue
                    # stale mapping — mirrored order is gone; clear and re-place
                    await self.redis.hdel(f"ordermap:{master_order_id}", follower["id"])

            # Bracket SL/TP attached to a position -> use the bracket endpoint.
            if is_bracket and product_id and stop_price is not None:
                existing_foid = await self.redis.hget(f"ordermap:{master_order_id}", follower["id"])
                # Self-heal: if this is an edit but we have no mapped follower order
                # (e.g. the bracket was created before id-tracking), find the
                # follower's matching bracket order on the exchange.
                if is_update and not existing_foid:
                    try:
                        orders = []
                        for st in ("pending", "open"):
                            try:
                                orders += await client.get_open_orders(state=st)
                            except Exception:
                                pass
                        match = next(
                            (o for o in orders
                             if str(o.get("product_id")) == str(product_id)
                             and o.get("stop_order_type") == stop_order_type),
                            None,
                        )
                        if match and match.get("id"):
                            existing_foid = str(match["id"])
                            await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], existing_foid)
                    except Exception as e:
                        logger.warning(f"Bracket self-heal lookup failed for {follower['name']}: {e}")
                jittered_stop = self._jitter_trigger(stop_price, seed=str(follower.get("id")))
                edited = False
                if is_update and existing_foid:
                    # Master EDITED the SL/TP price -> edit the follower's existing
                    # bracket order rather than creating a new one (which 400s).
                    try:
                        resp = await client.edit_order(existing_foid, product_id=product_id, stop_price=jittered_stop, stop_trigger_method=trigger_method)
                        new_id = (resp.get("result") or {}).get("id") if isinstance(resp, dict) else None
                        if new_id and str(new_id) != str(existing_foid):
                            await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(new_id))
                        logger.info(f"Updated bracket {master_order_id} ({stop_order_type}) -> {follower['name']} order {new_id or existing_foid} @ {jittered_stop} (master {stop_price})")
                        edited = True
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.warning(f"Bracket edit failed for {follower['name']} ({e} {body}); re-placing SL/TP so the update still reflects.")
                if not edited:
                    # Either a fresh bracket, or the edit failed because the
                    # follower's order was gone (deleted / replaced) — (re)place it
                    # so a master update always reflects on the FIRST try.
                    try:
                        leg = {"order_type": order_type, "stop_price": str(jittered_stop)}
                        if order_type == "limit_order" and limit_price is not None:
                            leg["limit_price"] = str(limit_price)
                        sl = leg if stop_order_type == "stop_loss_order" else None
                        tp = leg if stop_order_type == "take_profit_order" else None
                        resp = await client.place_bracket(
                            product_id=product_id, stop_loss=sl, take_profit=tp, trigger_method=trigger_method
                        )
                        result = resp.get("result", resp) if isinstance(resp, dict) else {}
                        leg_key = "stop_loss_order" if sl else "take_profit_order"
                        foid = (result.get(leg_key) or {}).get("id") if isinstance(result, dict) else None
                        if foid:
                            await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(foid))
                            await self.redis.expire(f"ordermap:{master_order_id}", 7 * 24 * 3600)
                            await ledger.record_follower_leg(
                                self.redis, master_order_id, follower["id"],
                                status="placed", follower_order_id=foid,
                            )
                        logger.info(f"Mirrored bracket {master_order_id} ({stop_order_type}, trigger={trigger_method}) -> {follower['name']}")
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.error(f"Failed to (re)place bracket to {follower['name']}: {e} {body}")
                continue

            # CLOSE via limit (reduce-only): REST a matching reduce-only limit on
            # the follower (same limit price) so it exits at the same level as the
            # master — rather than waiting to close reactively when the master's
            # order fills. Size = the follower's share of the master's close order
            # (min_one=False so a tiny master trim doesn't wipe a small follower),
            # CAPPED at what the follower actually holds so it can never over-close
            # or hit a "no position" reject. reduce_only also caps it exchange-side.
            # A PROTECTIVE order (SL/TP) is reduce_only too, but it is NOT a
            # close-now instruction — it's cover that should REST until triggered.
            # Running it through the close-rebalance logic below asks "how much does
            # this follower need to close right now?", and for a correctly-sized
            # follower the answer is always "nothing" — so the protection was
            # silently dropped. That is why C-BTC-65400-300726 logged
            #   "Reduce-only close ... nothing to rest (holds 1, target 1)"
            # 175 times while the follower sat there with no TP at all, and why
            # C-BTC-67500-310726 was short 31 lots with no stop (2026-07-30 audit).
            # Protection only ever got placed by accident, on the passes where the
            # follower happened to be OVER target so the rebalance returned qty >= 1.
            #
            # Size it proportionally like any other mirror, capped at what the
            # follower actually holds so the cover can't exceed the position.
            if is_protective_order and not is_update:
                try:
                    held = int(abs(float(await self._position_size_signed(client, symbol))))
                except Exception:
                    held = 0
                if held < 1:
                    logger.info(
                        f"Protection {stop_order_type or 'stop'} for {follower['name']} on "
                        f"{symbol}: follower holds nothing to protect, skipping"
                    )
                    await ledger.record_follower_leg(
                        self.redis, master_order_id, follower["id"], status="skipped",
                        reason="no position to protect",
                    )
                    continue
                qty = min(int(qty), held)
                # Jitter like the bracket path, so followers don't all trigger at the
                # same instant on the same price. Per-follower local — mutating the
                # shared stop_price would compound the jitter for the next follower.
                if stop_price is not None:
                    place_stop_price = self._jitter_trigger(stop_price, seed=str(follower.get("id")))

            elif reduce_only and not is_update:
                # A reduce-only order must be on the OPPOSITE side of the
                # follower's position: a buy reduces a short, a sell reduces a
                # long. If the follower is flat or on the SAME side (a position
                # desync vs the master), the order can never reduce anything and
                # Delta rejects it with a 400 — so skip instead of churning.
                try:
                    signed = float(await self._position_size_signed(client, symbol))
                except Exception:
                    signed = 0.0
                reduces = (side == "buy" and signed < 0) or (side == "sell" and signed > 0)
                if not reduces:
                    logger.info(
                        f"Reduce-only {side} for {follower['name']} on {symbol}: follower holds "
                        f"{signed:+.0f} — not reducible by a {side}, skipping (position desync?)"
                    )
                    await ledger.record_follower_leg(
                        self.redis, master_order_id, follower["id"], status="skipped",
                        reason=f"holds {signed:+.0f}, not reducible by {side}",
                    )
                    continue
                # Size the close by REBALANCING to the master's position AFTER this
                # close fills — NOT by ceil(close_chunk × ratio). Ceiling each chunk
                # over-closes a small follower on repeated trims: a 50-lot master
                # trim is ~0.5 follower lots but ceil'd to 1 EVERY time, so the
                # follower sheds far more than its share (600→400 master = 33%, but
                # 6→2 follower = 67%). Instead: follower's proportional TARGET for
                # the master's REMAINING (current − this close), and close only the
                # difference. A tiny trim that leaves the follower already at target
                # closes nothing (correct), never over-shoots.
                follower_held = int(abs(signed))
                master_now = await self._master_position_size(master_row, symbol, fresh=True) or 0.0
                intended_remaining = max(0.0, float(master_now) - float(master_qty))
                # round_up=True (min_one=False) so every path in the engine agrees on
                # the follower's target size — flooring here closed one lot more than
                # the reconciler thinks correct, and the two then fought each other.
                target = self.risk_engine.calculate_follower_quantity(
                    intended_remaining, ref_price, follower, round_up=True, min_one=False
                )
                qty = min(follower_held - int(target), follower_held)
                if qty < _size_deadband(target):
                    logger.info(
                        f"Reduce-only close for {follower['name']} on {symbol}: nothing to rest "
                        f"(holds {follower_held}, target {int(target)} for master remaining {intended_remaining:.0f})"
                    )
                    await ledger.record_follower_leg(
                        self.redis, master_order_id, follower["id"], status="skipped",
                        reason=f"already at target {int(target)} (holds {follower_held})",
                    )
                    continue

            # Plain limit order: if the master EDITED it, edit the follower's
            # existing order instead of placing a duplicate.
            existing_foid = await self.redis.hget(f"ordermap:{master_order_id}", follower["id"])
            if is_update and existing_foid:
                try:
                    resp = await client.edit_order(existing_foid, product_id=product_id, limit_price=limit_price)
                    # Delta edits can cancel-and-replace (new order id) — refresh the map.
                    new_id = (resp.get("result") or {}).get("id") if isinstance(resp, dict) else None
                    if new_id and str(new_id) != str(existing_foid):
                        await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(new_id))
                    logger.info(f"Updated order {master_order_id} -> {follower['name']} order {new_id or existing_foid} @ {limit_price}")
                    continue
                except Exception as e:
                    body = getattr(getattr(e, "response", None), "text", "")
                    logger.warning(f"Order edit failed for {follower['name']} ({e} {body}); re-placing so the update reflects on the first try.")
                    # fall through to place a fresh order (the mapped one was gone)

            try:
                resp = await self._place_order_with_retry(
                    client,
                    symbol=symbol,
                    side=side,
                    size=int(qty),
                    order_type=order_type,
                    limit_price=limit_price,
                    reduce_only=reduce_only,
                    stop_price=place_stop_price,
                    stop_order_type=event.get("stop_order_type"),
                    stop_trigger_method=event.get("stop_trigger_method"),
                )
                result = resp.get("result", resp)
                follower_order_id = result.get("id")
                if follower_order_id:
                    # Map master order -> this follower's order, so we can cancel/edit it later.
                    # This follower's resting set just changed — drop the cache so
                    # the next liveness check reads fresh.
                    self._open_orders_cache.pop(follower["id"], None)
                    await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(follower_order_id))
                    await self.redis.expire(f"ordermap:{master_order_id}", 7 * 24 * 3600)
                    await ledger.record_follower_leg(
                        self.redis, master_order_id, follower["id"], status="placed",
                        follower_order_id=follower_order_id, qty=qty,
                    )
                    logger.info(f"Mirrored order {master_order_id} -> {follower['name']} order {follower_order_id} (qty {qty})")

                    # Plain LIMIT order (not a stop/bracket): if it hasn't filled
                    # after the wait+retry window, escalate to market so the follower
                    # actually gets in — or OUT. Reduce-only closes used to be
                    # excluded here on the theory that they should sit and fill at the
                    # master's price. 67200 showed the flaw: the master's exit limit
                    # only half-filled and was then cancelled, so the follower's
                    # mirrored exit rested 2.5h, never filled, and died with the
                    # master's cancel — leaving the follower holding a position the
                    # master had left. Exits now escalate too ("limit wait 5s, then
                    # market", to maintain relative position). The escalation body
                    # already sizes a reduce-only force correctly: it re-derives how
                    # much the follower still owes and cancels the stale limit if the
                    # answer is nothing.
                    if order_type == "limit_order" and stop_price is None and not is_bracket:
                        asyncio.create_task(self._escalate_unfilled_limit(
                            follower, client, follower_order_id, product_id, symbol,
                            side, int(qty), reduce_only, master_row, limit_price,
                            master_order_id=master_order_id,
                        ))
            except Exception as e:
                resp_obj = getattr(e, "response", None)
                body = ""
                if resp_obj is not None:
                    try:
                        body = resp_obj.text
                    except Exception:
                        body = ""
                logger.error(
                    f"Failed to mirror order to {follower['name']} "
                    f"[{symbol} {side} qty={qty} type={order_type} reduce_only={reduce_only} "
                    f"limit={limit_price} stop={stop_price}]: {e} | body={body}"
                )
                await ledger.record_follower_leg(
                    self.redis, master_order_id, follower["id"], status="failed",
                    qty=qty, reason=_short_reason(e, body),
                )
                key = f"fail:{follower['id']}:{symbol}:{side}:place"
                asyncio.create_task(tg.notify_fail(
                    follower.get("name"), symbol, side, int(qty), _short_reason(e, body), key=key,
                ))

    @staticmethod
    def _order_done(od: dict) -> bool:
        """True if the order is fully filled/closed (nothing left to force)."""
        state = (od.get("state") or "").lower()
        unfilled = od.get("unfilled_size")
        unfilled = float(unfilled if unfilled is not None else (od.get("size") or 1))
        return state in ("closed", "filled") or unfilled <= 0

    @staticmethod
    def _filled_size(od: dict) -> int:
        fs = od.get("filled_size")
        if fs is not None:
            return int(float(fs))
        return int(float(od.get("size") or 0) - float(od.get("unfilled_size") or 0))

    async def _safe_get_order(self, client, order_id) -> dict:
        try:
            return (await client.get_order(str(order_id))).get("result", {}) or {}
        except Exception:
            return {}

    async def _safe_cancel(self, client, order_id, product_id) -> bool:
        """Cancel; return True only if it actually cancelled (order was open)."""
        try:
            await client.cancel_order(str(order_id), product_id=product_id)
            return True
        except Exception as e:
            logger.warning(f"Escalation cancel failed for {order_id}: {e}")
            return False

    async def _escalate_unfilled_limit(self, follower, client, order_id, product_id,
                                       symbol, side, qty, reduce_only, master_row,
                                       limit_price=None, master_order_id=None) -> None:
        """The follower's mirrored order rests as a GTC limit at the master's
        price. If it hasn't filled within ESCALATE_WAIT_SEC, MARKET it so the
        follower still gets in/out (team rule: "GTC daalo, 5s me fill na ho to
        market"). Only forces when still warranted, and never double-fills."""
        try:
            # Give the GTC limit the full window to fill at the master's price.
            await asyncio.sleep(ESCALATE_WAIT_SEC)
            if self._order_done(await self._safe_get_order(client, order_id)):
                return

            # THE MASTER'S OWN ORDER MUST HAVE FILLED FIRST. The 5s window is
            # measured from the master getting filled, not from us placing — the
            # master may rest a limit for hours, and forcing the follower in or out
            # while the master is still waiting is not copying, it is front-running
            # our own master.
            #
            # Skipping this check caused a place/cancel loop on live accounts
            # (2026-07-31, P-BTC-62800): the 30s order reconcile re-mirrored the
            # master's resting reduce-only limit, this escalation decided 5s later
            # that no close was needed and CANCELLED it, the reconciler re-placed
            # it 30s later, forever. Leaving the order resting breaks that loop —
            # the reconciler's idempotency check then sees it still live and does
            # nothing. When the master's order does fill, the fill event triggers
            # _escalate_after_master_fill instead.
            if master_order_id and master_row:
                mo = await self._safe_get_order(
                    self._get_master_client(master_row), master_order_id
                )
                if mo and not self._order_done(mo):
                    logger.debug(
                        "Escalation: master order %s on %s still resting — leaving "
                        "%s's mirror to wait with it", master_order_id, symbol,
                        follower.get("name"),
                    )
                    return

            # ---- Point 3 exceptions: leave the GTC limit RESTING (no market, no
            # cancel) and let it wait, when: ----
            # (a) cheap tail ENTRIES (limit < 2): a market order gives an awful fill
            #     on a sub-$2 premium, and not getting in is a free option — so wait.
            #     EXITS are deliberately exempt: staying in a position the master has
            #     already left is open-ended risk, while the slippage is bounded, so
            #     relative position wins over fill price. This exemption is the whole
            #     reason 67200 (a 1.2 option) sat unexited for 2.5h — the guard fired
            #     and the follower was left holding. (desk call, 2026-07-30)
            if not reduce_only and limit_price is not None and float(limit_price) < 2:
                logger.info(f"Escalation: {follower['name']} {symbol} ENTRY limit {limit_price} < 2 — leaving it resting (no market).")
                return

            # Is forcing still warranted?
            if reduce_only:
                # Only force-close if the follower is STILL over its rebalance
                # target (guards against the master cancelling/re-quoting the close).
                cq, cur = await self._follower_close_qty(client, follower, symbol, master_row)
                if cq is not None and cq < 1:
                    logger.info(f"Escalation: no close needed for {follower['name']} {symbol}; cancelling stale limit.")
                    await self._safe_cancel(client, order_id, product_id)
                    return
            else:
                # (b) master's own order isn't filled yet (master has no position):
                #     don't force the follower in AHEAD of the master — leave the
                #     limit resting and wait (the 15s reconcile matches it later).
                msz = await self._master_position_size(master_row, symbol)
                if msz is not None and msz == 0:
                    logger.info(f"Escalation: master not in {symbol} yet — leaving {follower['name']} limit resting to wait.")
                    return

            # Cancel, then CONFIRM it didn't fill during the race before marketing.
            cancelled = await self._safe_cancel(client, order_id, product_id)
            od = await self._safe_get_order(client, order_id)
            if not cancelled and self._order_done(od):
                logger.info(f"Escalation aborted for {follower['name']} {symbol}: limit filled during cancel (no double-order).")
                return

            # Market only the UNFILLED remainder, never more than intended.
            market_qty = int(qty) - self._filled_size(od)
            if reduce_only:
                cq, _cur = await self._follower_close_qty(client, follower, symbol, master_row)
                if cq is not None:
                    market_qty = min(market_qty, cq)
            if market_qty < 1:
                return
            # The GTC limit didn't fill at the master's price within the window —
            # MARKET the remainder so the follower gets filled (team rule: GTC,
            # then market after 5s). NOT fok (Delta rejects it); a bare market
            # order fills against the book immediately.
            try:
                resp = await client.place_order(
                    symbol=symbol, side=(side or "").lower(), size=int(market_qty),
                    order_type="market_order", reduce_only=reduce_only,
                )
                oid = resp.get("id") or resp.get("result", {}).get("id")
                logger.info(f"Escalated unfilled limit -> MARKET for {follower['name']} {symbol} qty {market_qty} (order {oid})")
                acct = follower.get("name") or "Follower"
                if reduce_only:
                    asyncio.create_task(tg.notify_close(acct, symbol, int(market_qty)))
                else:
                    asyncio.create_task(tg.notify_open(acct, symbol, side, int(market_qty)))
            except Exception as e:
                resp_obj = getattr(e, "response", None)
                body = ""
                if resp_obj is not None:
                    try:
                        body = resp_obj.text
                    except Exception:
                        body = ""
                logger.error(
                    f"Escalation market order failed for {follower['name']} "
                    f"[{symbol} {(side or '').lower()} qty={int(market_qty)} reduce_only={reduce_only} type=market]: "
                    f"{e} | body={body}"
                )
                key = f"fail:{follower.get('id')}:{symbol}:{side}:escalate"
                asyncio.create_task(tg.notify_fail(
                    follower.get("name"), symbol, side, int(market_qty), _short_reason(e, body), key=key,
                ))
        except Exception as e:
            logger.error(f"Escalation error for {symbol}: {e}")

    async def _find_follower_order(self, client, event: dict):
        """Locate the follower's order matching a master order, for self-healing
        cancels when the id map is missing/stale. Matches on product + leg, or
        for plain orders on side + price."""
        product_id = event.get("product_id")
        stop_order_type = event.get("stop_order_type")
        side = (event.get("side") or "").lower()
        limit_price = event.get("limit_price")
        orders = []
        for st in ("pending", "open"):
            try:
                orders += await client.get_open_orders(state=st)
            except Exception:
                pass
        for o in orders:
            if str(o.get("product_id")) != str(product_id):
                continue
            if stop_order_type:
                if o.get("stop_order_type") == stop_order_type:
                    return str(o.get("id"))
            else:
                if (o.get("side") or "").lower() == side and not o.get("stop_order_type"):
                    if limit_price is None or str(o.get("limit_price")) == str(limit_price):
                        return str(o.get("id"))
        return None

    async def _settle_exit_after_cancel(self, follower: dict, client, symbol: str,
                                        master_row: dict, ref_price: float = 0.0) -> None:
        """The master cancelled a plain resting order on `symbol`. If that leaves the
        follower holding MORE than its share of what the master still holds, close the
        difference at market now.

        This is the 67200 fix. The master rested a full-size exit limit at 1.2; it
        half-filled, so the master ended at -1464. The follower's mirrored exit limit
        never filled at all, and when the master cancelled the remainder we faithfully
        cancelled the follower's copy too — taking the follower out of the queue while
        it was still holding everything. Mirroring the cancel is literally correct and
        semantically backwards: the master cancels because it GOT what it wanted, so
        copying that to a follower which achieved nothing locks in the divergence.

        A cancel is the ideal moment to settle: unlike a timer, it's the master itself
        telling us the episode is over, so there's nothing left to wait for. Market
        (not limit) is right here — the master just abandoned that price.

        Only ever REDUCES, and only when the follower is genuinely above target, so a
        cancel on an unrelated/unfilled entry order is a no-op. The 15s reconciler
        remains the backstop for a master that never cancels at all."""
        name = follower.get("name") or "Follower"
        try:
            # Hands-off: the master's SL/TP triggered on this symbol. Its leftover
            # resting orders then get cancelled, which used to land here and force a
            # close — observed live 2026-08-03 closing 29 lots of P-BTC-62000-030826
            # right after the master's TP fired, instead of letting the follower's
            # own stop do it.
            hoff = await ledger.is_hands_off(self.redis, follower.get("owner_id"),
                                             follower.get("id"), symbol)
            if hoff:
                logger.info(
                    f"Exit settle skipped for {name} on {symbol} — hands-off "
                    f"({hoff.get('reason')}); its own stop closes this leg"
                )
                return
            signed = float(await self._position_size_signed(client, symbol))
            if signed == 0:
                return  # follower already flat — nothing owed
            master_now = await self._master_position_size(master_row, symbol, fresh=True)
            if master_now is None:
                return  # can't read the master — leave it to the reconciler
            # Target with round_up=True deliberately: the SAME rounding the open used
            # to size this position, and the same the reconciler's trim uses. Using
            # the floor-based _follower_close_qty here would close one lot more than
            # the reconciler considers correct, and the two would disagree forever.
            target = self.risk_engine.calculate_follower_quantity(
                abs(float(master_now)), ref_price or 0.0, follower, round_up=True
            )
            if master_now and int(target) < 1:
                logger.warning(
                    f"Exit settle for {name} on {symbol}: sizing unavailable "
                    f"(master {master_now}) — not closing on an unknown ratio"
                )
                return
            held = int(abs(signed))
            close_qty = held - int(target)
            # Same deadband as the reconciler, so the two can't disagree about what
            # counts as out of sync and undo each other.
            if close_qty < _size_deadband(target):
                return  # already at (or near) target — nothing owed
            side = "buy" if signed < 0 else "sell"
            await client.place_order(
                symbol=symbol, side=side, size=int(close_qty),
                order_type="market_order", reduce_only=True,
            )
            logger.info(
                f"Exit settle after master cancel: closed {int(close_qty)} on {symbol} for "
                f"{name} ({held} -> {int(target)}, master now {master_now:+.0f}) — the master "
                f"abandoned its exit price but the follower had not exited"
            )
            asyncio.create_task(tg.notify_close(name, symbol, int(close_qty)))
        except Exception as e:
            body = getattr(getattr(e, "response", None), "text", "")
            logger.warning(f"Exit settle after cancel failed for {name} {symbol}: {e} {body}")
            asyncio.create_task(tg.notify_fail(
                name, symbol, "close", None, _short_reason(e, body),
                key=f"settle:{follower.get('id')}:{symbol}", window=900,
            ))

    async def _mirror_cancel(self, master_order_id: str, event: dict | None = None) -> None:
        key = f"ordermap:{master_order_id}"
        try:
            mapping = await self.redis.hgetall(key)
        except Exception as e:
            logger.error(f"Failed to read order map {key}: {e}")
            mapping = {}

        product_id = (event or {}).get("product_id")
        symbol = (event or {}).get("symbol")

        # A protective order = stop / SL / TP / bracket leg. Decide ONCE, from the
        # MASTER's position, whether this cancel is genuine or an SL/TP-hit:
        #   • master still HOLDS the position  -> the user cancelled/edited the stop
        #       intentionally  -> propagate the cancel to followers.
        #   • master is FLAT                    -> the leg vanished because the SL/TP
        #       HIT (OCO) -> keep each follower's own (jittered) bracket so it closes
        #       its own position; don't strip its protection.
        is_protective = bool(
            (event or {}).get("stop_order_type")
            or (event or {}).get("stop_price")
            or (event or {}).get("is_bracket")
        )
        # Master row is needed both for the protective check below and for the
        # post-cancel exit settle further down, so resolve it once.
        master_row = None
        if symbol and event:
            try:
                mq = self.db.table("accounts").select("*").eq("is_master", True)
                if event.get("owner_id"):
                    mq = mq.eq("owner_id", event["owner_id"])
                mrow = mq.execute()
                master_row = mrow.data[0] if mrow.data else None
            except Exception:
                master_row = None
        if is_protective and symbol and event:
            # Did the master CANCEL this stop, or did it FIRE?
            #
            # Delta tells us directly: reason="stop_cancel" for a deliberate cancel,
            # "stop_trigger"/fill when it fires. That distinction is critical here
            # because follower stops are JITTERED to a slightly different price —
            # the master's stop firing says nothing about whether the follower's
            # has fired, so:
            #   • deliberate cancel -> the follower's copy is now orphaned, cancel it
            #   • anything else     -> LEAVE the follower's stop alone and let its
            #     own trigger do the work (stripping it would leave the position
            #     unprotected, the same failure as C-BTC-67500 having no SL).
            #
            # Previously this was inferred from the master's POSITION alone, which
            # misreads a TP firing on a PARTIAL close: the master is still holding,
            # so it looked like a manual cancel and the follower's protection was
            # stripped while its own (jittered) stop had not yet triggered.
            _reason = (event.get("reason") or "")
            if _reason != "stop_cancel":
                logger.info(
                    f"Protective order on {symbol} vanished (reason "
                    f"{_reason or 'unknown'}) — not a deliberate cancel, "
                    f"leaving follower stops in place to trigger on their own."
                )
                # Hands-off requires POSITIVE evidence that the stop FIRED, not
                # merely "this wasn't a deliberate cancel". An unknown reason must
                # NOT freeze the leg: hands-off blocks reconciliation for up to a
                # day, and that would stop the follower being closed when the master
                # exits gradually via partial exits and ends up flat — a case that
                # must still be followed. Keeping the follower's stop is safe on an
                # unknown reason; disabling the safety net on one is not.
                if _reason == "stop_trigger":
                    await self._mark_hands_off(
                        event, master_order_id, "master SL/TP triggered",
                    )
                return
            master_sz = await self._master_position_size(master_row, symbol, fresh=True)
            if master_sz is not None and master_sz == 0:
                # Master EXITED this symbol (its SL/TP hit / it closed). By strategy
                # decision we do NOT force-close followers and we do NOT strip their
                # protection: each follower has its own mirrored (jittered) SL/TP
                # that closes its position at ~the same level. Forcing a market
                # close caused wasteful sell-then-buyback round-trips with bad fills
                # in fast moves. Leave the follower's brackets to do the work.
                logger.info(f"Master exited {symbol} — leaving followers to their own jittered SL/TP (no forced close).")
                return
            # else: master still holds it (or size unknown) -> genuine cancel, propagate.

        # Determine the set of followers to act on: mapped ones, plus (for
        # self-heal) all active followers if we have no mapping.
        targets = dict(mapping) if mapping else {}
        if not targets and event:
            try:
                fols = self.db.table("accounts").select("id").eq("is_master", False).eq("status", "active").execute().data or []
                targets = {f["id"]: None for f in fols}
            except Exception:
                targets = {}

        for follower_id, follower_order_id in targets.items():
            acc_res = self.db.table("accounts").select("*").eq("id", follower_id).execute()
            if not acc_res.data:
                continue
            client = await self._get_follower_client(acc_res.data[0])
            if not client:
                continue

            try:
                if follower_order_id:
                    await client.cancel_order(str(follower_order_id), product_id=product_id)
                    logger.info(f"Cancelled mirrored order {follower_order_id} for follower {follower_id}")
                else:
                    raise RuntimeError("no mapped id")
            except Exception as e:
                # Self-heal: find the matching order on the exchange and cancel it.
                if event:
                    try:
                        foid = await self._find_follower_order(client, event)
                        if foid:
                            await client.cancel_order(foid, product_id=product_id)
                            logger.info(f"Self-healed cancel: cancelled {foid} for follower {follower_id}")
                        else:
                            logger.warning(f"Cancel: no matching follower order found for {follower_id}")
                    except Exception as e2:
                        logger.warning(f"Failed self-heal cancel for {follower_id}: {e2}")
                else:
                    logger.warning(f"Failed to cancel mirrored order {follower_order_id}: {e}")

            # The follower's mirrored order is now gone. If this was an EXIT the
            # follower never actually completed, finish it — see the method docstring.
            # Runs AFTER the cancel so there's no resting reduce-only left to fill
            # on top of the market close (which would over-close the follower).
            if not is_protective and symbol and master_row:
                await self._settle_exit_after_cancel(
                    acc_res.data[0], client, symbol, master_row,
                    ref_price=float((event or {}).get("limit_price") or 0.0),
                )
        # The ordermap entry goes away below, but the ledger keeps the audit trail:
        # stamp the master order cancelled so a mirrored-then-cancelled order is
        # never read back as "the follower still has this".
        await ledger.mark_state(self.redis, master_order_id, "cancelled")
        for follower_id in targets:
            await ledger.record_follower_leg(
                self.redis, master_order_id, follower_id,
                status="cancelled", reason="master cancelled the order",
            )
        try:
            await self.redis.delete(key)
        except Exception:
            pass

    async def handle_sl_tp(self, account_id: str, symbol: str, sl_price: float = None, tp_price: float = None) -> None:
        logger.info(f"Copying SL/TP order for follower {account_id} on {symbol}: SL={sl_price}, TP={tp_price}")
        client = self.connection_manager.get_client(account_id)
        if not client:
            logger.error(f"DeltaClient not found for account {account_id}")
            return
            
        try:
            # For simplicity, edit bracket order on positions
            # We can find open orders/brackets if supported or just log.
            pass
        except Exception as e:
            logger.error(f"Failed to copy SL/TP to follower {account_id}: {e}")
