import logging
import asyncio
import hashlib
import time
from typing import List, Dict, Any
from app.database import db
from app.websocket.socket_manager import socket_manager
from app.core.risk_engine import RiskEngine
from app.core.order_executor import order_executor
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
                master_balance = float(master_acc.data[0].get("allocated_balance") or master_acc.data[0].get("available_margin") or master_acc.data[0].get("balance") or 0.0)
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
                    continue
            if not client:
                continue

            if is_exit:
                if master_remaining is None:
                    # Couldn't read the master's remaining size — fall back to a
                    # proportional close rather than skipping the exit entirely.
                    follower_qty = self.risk_engine.calculate_follower_quantity(quantity, entry_price, follower, round_up=True)
                else:
                    target = self.risk_engine.calculate_follower_quantity(master_remaining, entry_price, follower, round_up=False, min_one=False)
                    current = await self._position_size(client, symbol)
                    follower_qty = int(current) - int(target)
                    if follower_qty < 1:
                        logger.info(
                            f"No close needed for {follower['name']} on {symbol}: holds {current:.0f}, "
                            f"target {int(target)} (master left {master_remaining:.0f})"
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
        for r in results:
            acct = r.get("account_name") or "Follower"
            st = r.get("status")
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

    @staticmethod
    async def _order_is_live(client, order_id: str) -> bool:
        """True if the given order id is still resting (open/pending) on the
        account. On any fetch error, return True so we never risk double-placing
        just because a status check hiccuped."""
        try:
            for st in ("open", "pending"):
                try:
                    for o in await client.get_open_orders(state=st):
                        if str(o.get("id")) == str(order_id):
                            return True
                except Exception:
                    return True
        except Exception:
            return True
        return False

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
        target = self.risk_engine.calculate_follower_quantity(
            master_remaining, ref_price, follower, round_up=False, min_one=False
        )
        return max(0, int(current) - int(target)), current

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
            mc = DeltaClient(master_row["api_key"], master_row["api_secret"], master_row.get("environment", "demo"))
            try:
                size = await self._position_size(mc, symbol)
            finally:
                await mc.close()
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
            mc = DeltaClient(master_row["api_key"], master_row["api_secret"], master_row.get("environment", "demo"))
            try:
                signed = await self._position_size_signed(mc, symbol)
            finally:
                await mc.close()
            self._master_signed_cache[symbol] = (signed, time.time())
            return signed
        except Exception as e:
            logger.error(f"Master signed position fetch failed for {symbol}: {e}")
            return None

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
        elif action == "sync_protection":
            await self._sync_protection(event)
        elif action == "reconcile_positions":
            await self._reconcile_positions(event)

    async def _reconcile_positions(self, event: dict) -> None:
        """Every 10s: make each follower's OPEN POSITIONS match the master's,
        recovering anything the live copy missed (e.g. a WS-dropped entry).

          • Master holds a leg the follower is FLAT on -> OPEN it (market, current
            price — the master's entry moment has passed). Skipped if the follower
            already has a resting order on that symbol (live copy is on it) or if
            we opened it within the debounce window.
          • Follower holds a leg the master is FLAT on, or on the OPPOSITE side
            (a desync) -> CLOSE it (reduce-only market).

        NOTE: the close side actively flattens follower legs the master no longer
        holds — this supersedes the earlier "don't force-close on SL/TP hit, let
        the follower's own jittered stop close it" behaviour for the master-flat
        case (the reconcile now closes within ~10s instead)."""
        owner_id = event.get("owner_id")
        master_map = {}
        for p in (event.get("positions") or []):
            sym = p.get("symbol")
            if sym:
                master_map[sym] = (float(p.get("size") or 0), p.get("mark"))

        # Master balance for proportional sizing of any recovery open.
        master_balance = 0.0
        try:
            mq = self.db.table("accounts").select("*").eq("is_master", True)
            if owner_id:
                mq = mq.eq("owner_id", owner_id)
            m = mq.execute()
            if m.data:
                mr = m.data[0]
                master_balance = float(mr.get("allocated_balance") or mr.get("available_margin") or mr.get("balance") or 0.0)
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
        current_open: set = set()   # (follower_id, sym) missing THIS pass
        current_close: set = set()  # (follower_id, sym) orphan/opposite THIS pass
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
                resting = set()
                for st in ("open", "pending"):
                    try:
                        for o in await client.get_open_orders(state=st):
                            if o.get("product_symbol"):
                                resting.add(o.get("product_symbol"))
                    except Exception:
                        pass

                # 1) CLOSE — follower holds a leg the master is flat on / opposite.
                for sym, fsz in list(fpos.items()):
                    msz = master_map.get(sym, (0, None))[0]
                    same_side = (fsz > 0 and msz > 0) or (fsz < 0 and msz < 0)
                    if same_side:
                        continue  # follower on the right side — keep it
                    # Master FLAT but follower still has its own resting SL/TP ->
                    # leave it, let that (jittered) stop close it (respects "no
                    # forced close on SL/TP hit"). Wrong-SIDE desync always closes.
                    if msz == 0 and sym in resting:
                        continue
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
                        logger.info(f"reconcile: closed {fol.get('name')} {sym} {fsz:+.0f} (master holds {msz:+.0f}) — mismatch")
                        asyncio.create_task(tg.notify_close(fol.get("name"), sym, int(abs(fsz))))
                    except Exception as e:
                        body = getattr(getattr(e, "response", None), "text", "")
                        logger.warning(f"reconcile close failed for {fol.get('name')} {sym}: {e} {body}")

                # 2) OPEN — master holds a leg the follower is flat on (recover miss).
                for sym, (msz, mark) in master_map.items():
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
                    self._recon_open_ts[key] = now
                    side = "buy" if msz > 0 else "sell"
                    try:
                        await client.place_order(
                            symbol=sym, side=side, size=int(target),
                            order_type="market_order", reduce_only=False,
                        )
                        logger.info(f"reconcile: opened {fol.get('name')} {sym} {side} {int(target)} (master {msz:+.0f}) — recovered missing leg")
                        asyncio.create_task(tg.notify_open(fol.get("name"), sym, side, int(target), price or None))
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
        master_prot = {(s, t) for s, t in (event.get("master_protection") or [])}
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
        for fol in followers:
            client = await self._get_follower_client(fol)
            if not client:
                continue
            try:
                orders = []
                seen = set()
                for st in ("pending", "open"):
                    try:
                        for o in await client.get_open_orders(state=st):
                            if o.get("id") in seen:
                                continue
                            seen.add(o.get("id"))
                            orders.append(o)
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
            except Exception as e:
                logger.warning(f"sync_protection: error for {fol.get('name')}: {e}")
        # Remember this sweep's orphans so the next sweep can confirm them.
        self._prot_orphans_prev = current_orphans

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
                master_balance = float(master_row.get("allocated_balance") or master_row.get("available_margin") or master_row.get("balance") or 0.0)
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
        for follower in followers:
            follower["master_balance"] = master_balance
            # Floor so the mirrored order quantity matches the follower's position
            # (which was also floored on open). reduce_only caps it anyway.
            qty = self.risk_engine.calculate_follower_quantity(master_qty, ref_price, follower, round_up=True)
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
                    if await self._order_is_live(client, mapped):
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
            if reduce_only and not is_update:
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
                target = self.risk_engine.calculate_follower_quantity(
                    intended_remaining, ref_price, follower, round_up=False, min_one=False
                )
                qty = min(follower_held - int(target), follower_held)
                if qty < 1:
                    logger.info(
                        f"Reduce-only close for {follower['name']} on {symbol}: nothing to rest "
                        f"(holds {follower_held}, target {int(target)} for master remaining {intended_remaining:.0f})"
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
                    stop_price=stop_price,
                    stop_order_type=event.get("stop_order_type"),
                    stop_trigger_method=event.get("stop_trigger_method"),
                )
                result = resp.get("result", resp)
                follower_order_id = result.get("id")
                if follower_order_id:
                    # Map master order -> this follower's order, so we can cancel/edit it later.
                    await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(follower_order_id))
                    await self.redis.expire(f"ordermap:{master_order_id}", 7 * 24 * 3600)
                    logger.info(f"Mirrored order {master_order_id} -> {follower['name']} order {follower_order_id} (qty {qty})")

                    # Plain LIMIT ENTRY (not a stop/bracket/reduce-only): if it
                    # hasn't filled after the wait+retry window, escalate to a
                    # full-or-nothing market order so the follower actually gets in.
                    # Reduce-only closes are intentionally excluded — they mirror
                    # the master's resting limit EXIT and should sit and fill at
                    # that price, not be forced to market or cancelled.
                    if order_type == "limit_order" and stop_price is None and not is_bracket and not reduce_only:
                        asyncio.create_task(self._escalate_unfilled_limit(
                            follower, client, follower_order_id, product_id, symbol,
                            side, int(qty), reduce_only, master_row, limit_price,
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
                                       limit_price=None) -> None:
        """The follower's mirrored order rests as a GTC limit at the master's
        price. If it hasn't filled within ESCALATE_WAIT_SEC, MARKET it so the
        follower still gets in/out (team rule: "GTC daalo, 5s me fill na ho to
        market"). Only forces when still warranted, and never double-fills."""
        try:
            # Give the GTC limit the full window to fill at the master's price.
            await asyncio.sleep(ESCALATE_WAIT_SEC)
            if self._order_done(await self._safe_get_order(client, order_id)):
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
                # Only force-enter if the master actually holds the position.
                msz = await self._master_position_size(master_row, symbol)
                if msz is not None and msz == 0:
                    logger.info(f"Escalation skipped for {follower['name']} {symbol}: master has no position.")
                    await self._safe_cancel(client, order_id, product_id)
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
        if is_protective and symbol and event:
            owner_id = event.get("owner_id")
            try:
                mq = self.db.table("accounts").select("*").eq("is_master", True)
                if owner_id:
                    mq = mq.eq("owner_id", owner_id)
                mrow = mq.execute()
                master_row = mrow.data[0] if mrow.data else None
            except Exception:
                master_row = None
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
