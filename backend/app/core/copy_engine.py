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

logger = logging.getLogger(__name__)

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
        self._MASTER_POS_TTL = 3.0  # seconds

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

        master_remaining = None
        if is_exit:
            master_row = master_acc.data[0] if master_acc.data else None
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
                follower_qty = self.risk_engine.calculate_follower_quantity(quantity, entry_price, follower, round_up=False)

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
        """Offset an SL/TP trigger by a DETERMINISTIC +/- (10..50).

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
        magnitude = 10 + (h % 41)          # 10..50 inclusive
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

    async def _master_position_size(self, master_row: dict, symbol: str):
        """Live absolute master position size for a symbol (cached ~3s so a burst
        of cancels doesn't hammer REST). Returns None if it can't be determined."""
        if not master_row:
            return None
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
        """Mirror a master's resting order onto followers (place) or cancel the
        mirrored follower orders (cancel)."""
        action = event.get("action")
        master_order_id = str(event.get("master_order_id"))
        if action == "place":
            await self._mirror_place(event, master_order_id)
        elif action == "cancel":
            await self._mirror_cancel(master_order_id, event)

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

        ref_price = limit_price or stop_price or 0.0
        for follower in followers:
            follower["master_balance"] = master_balance
            # Floor so the mirrored order quantity matches the follower's position
            # (which was also floored on open). reduce_only caps it anyway.
            qty = self.risk_engine.calculate_follower_quantity(master_qty, ref_price, follower, round_up=False)
            client = await self._get_follower_client(follower)
            if not client:
                continue

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
                try:
                    jittered_stop = self._jitter_trigger(stop_price, seed=str(follower.get("id")))
                    if is_update and existing_foid:
                        # Master EDITED the SL/TP price -> edit the follower's existing
                        # bracket order rather than creating a new one (which 400s).
                        resp = await client.edit_order(existing_foid, product_id=product_id, stop_price=jittered_stop, stop_trigger_method=trigger_method)
                        new_id = (resp.get("result") or {}).get("id") if isinstance(resp, dict) else None
                        if new_id and str(new_id) != str(existing_foid):
                            await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(new_id))
                        logger.info(f"Updated bracket {master_order_id} ({stop_order_type}) -> {follower['name']} order {new_id or existing_foid} @ {jittered_stop} (master {stop_price})")
                    else:
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
                    logger.error(f"Failed to mirror bracket to {follower['name']}: {e} {body}")
                continue

            # CLOSE via limit (reduce-only): size it by rebalancing to the master's
            # REMAINING position, not by the master's close-chunk size. Otherwise a
            # 1-lot master trim would close a whole follower lot (min-1) and wipe a
            # small follower after a few trims.
            if reduce_only and not is_update:
                cq, cur = await self._follower_close_qty(client, follower, symbol, master_row, ref_price)
                if cq is None:
                    pass  # master size unknown → fall through with the mirrored qty
                elif cq < 1:
                    logger.info(f"No close needed for {follower['name']} on {symbol}: holds {cur:.0f} (rebalance says 0)")
                    continue
                else:
                    qty = cq

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
                except Exception as e:
                    body = getattr(getattr(e, "response", None), "text", "")
                    logger.error(f"Failed to update order for {follower['name']}: {e} {body}")
                continue

            try:
                resp = await client.place_order(
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

                    # Plain LIMIT order (not a stop/bracket): if it hasn't filled
                    # after the wait+retry window, escalate to a full-or-nothing
                    # market order so the follower actually gets in.
                    if order_type == "limit_order" and stop_price is None and not is_bracket:
                        asyncio.create_task(self._escalate_unfilled_limit(
                            follower, client, follower_order_id, product_id, symbol,
                            side, int(qty), reduce_only, master_row,
                        ))
            except Exception as e:
                body = getattr(getattr(e, "response", None), "text", "")
                logger.error(f"Failed to mirror order to {follower['name']}: {e} {body}")

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
                                       symbol, side, qty, reduce_only, master_row) -> None:
        """If a mirrored limit order hasn't executed after wait+retry, force the
        follower in/out with a full-or-nothing market order — but only when it's
        still warranted, and without ever double-filling."""
        try:
            # Wait, then retry-wait; bail the moment it fills.
            for _ in range(2):
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
            try:
                resp = await client.place_order(
                    symbol=symbol, side=(side or "").lower(), size=int(market_qty),
                    order_type="market_order", time_in_force="fok", reduce_only=reduce_only,
                )
                oid = resp.get("id") or resp.get("result", {}).get("id")
                logger.info(f"Escalated unfilled limit -> market for {follower['name']} {symbol} qty {market_qty} (order {oid})")
            except Exception as e:
                logger.error(f"Escalation market order failed for {follower['name']} {symbol}: {e}")
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
            master_sz = await self._master_position_size(master_row, symbol)
            if master_sz is not None and master_sz == 0:
                logger.info(f"Master flat on {symbol}; keeping follower SL/TP (leg cancelled by an SL/TP hit, not a manual cancel).")
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
