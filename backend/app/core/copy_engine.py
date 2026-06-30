import logging
import asyncio
from typing import List, Dict, Any
from app.database import db
from app.websocket.socket_manager import socket_manager
from app.core.risk_engine import RiskEngine
from app.core.order_executor import order_executor

logger = logging.getLogger(__name__)

class CopyEngine:
    def __init__(self, db_client, redis_client, socket_mgr, connection_mgr) -> None:
        self.db = db_client
        self.redis = redis_client
        self.socket_manager = socket_mgr
        self.connection_manager = connection_mgr
        self.risk_engine = RiskEngine()

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
                "raw_payload": raw_payload
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

        # 2. Get active follower accounts
        try:
            followers_res = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active").execute()
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
            master_acc = self.db.table("accounts").select("available_margin, balance").eq("is_master", True).execute()
            if master_acc.data:
                master_balance = float(master_acc.data[0].get("available_margin") or master_acc.data[0].get("balance") or 0.0)
        except Exception as e:
            logger.error(f"Failed to fetch master balance for ratio calculation: {e}")

        # Closes (exit/sl) round up so reduce-only orders never leave a residual;
        # opens floor so we never over-expose.
        is_exit = trade_type in ("exit", "sl")

        tasks = []
        for follower in followers:
            # Inject master balance context
            follower["master_balance"] = master_balance
            follower_qty = self.risk_engine.calculate_follower_quantity(quantity, entry_price, follower, round_up=is_exit)
            
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
                        "quantity": follower_qty,
                        "failure_reason": f"Connection error: {e}"
                    }).execute()
                    continue

            if client:
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
            await self._mirror_cancel(master_order_id)

    async def _mirror_place(self, event: dict, master_order_id: str) -> None:
        symbol = event.get("symbol")
        side = event.get("side")
        master_qty = float(event.get("size") or 0)
        order_type = event.get("order_type") or "limit_order"
        limit_price = float(event["limit_price"]) if event.get("limit_price") else None
        stop_price = float(event["stop_price"]) if event.get("stop_price") else None
        reduce_only = bool(event.get("reduce_only"))
        if not symbol or not side or master_qty <= 0:
            return

        # Active followers
        try:
            followers = self.db.table("accounts").select("*").eq("is_master", False).eq("status", "active").execute().data or []
        except Exception as e:
            logger.error(f"Failed to query followers for order mirror: {e}")
            return
        if not followers:
            return

        # Master balance for the ratio
        master_balance = 0.0
        try:
            m = self.db.table("accounts").select("available_margin, balance").eq("is_master", True).execute()
            if m.data:
                master_balance = float(m.data[0].get("available_margin") or m.data[0].get("balance") or 0.0)
        except Exception:
            pass

        ref_price = limit_price or stop_price or 0.0
        for follower in followers:
            follower["master_balance"] = master_balance
            # Floor so the mirrored order quantity matches the follower's position
            # (which was also floored on open). reduce_only caps it anyway.
            qty = self.risk_engine.calculate_follower_quantity(master_qty, ref_price, follower, round_up=False)
            client = await self._get_follower_client(follower)
            if not client:
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
                    # Map master order -> this follower's order, so we can cancel it later.
                    await self.redis.hset(f"ordermap:{master_order_id}", follower["id"], str(follower_order_id))
                    await self.redis.expire(f"ordermap:{master_order_id}", 7 * 24 * 3600)
                    logger.info(f"Mirrored order {master_order_id} -> {follower['name']} order {follower_order_id} (qty {qty})")
            except Exception as e:
                body = getattr(getattr(e, "response", None), "text", "")
                logger.error(f"Failed to mirror order to {follower['name']}: {e} {body}")

    async def _mirror_cancel(self, master_order_id: str) -> None:
        key = f"ordermap:{master_order_id}"
        try:
            mapping = await self.redis.hgetall(key)
        except Exception as e:
            logger.error(f"Failed to read order map {key}: {e}")
            return
        if not mapping:
            return
        for follower_id, follower_order_id in mapping.items():
            acc_res = self.db.table("accounts").select("*").eq("id", follower_id).execute()
            if not acc_res.data:
                continue
            client = await self._get_follower_client(acc_res.data[0])
            if not client:
                continue
            try:
                await client.cancel_order(str(follower_order_id))
                logger.info(f"Cancelled mirrored order {follower_order_id} for follower {follower_id}")
            except Exception as e:
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
