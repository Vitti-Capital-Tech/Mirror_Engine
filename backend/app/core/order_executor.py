import logging
import time
import asyncio
from typing import Dict, Any, Optional
from app.database import db
from app.services.delta_client import DeltaClient
from app.core.risk_engine import RiskEngine
from app.core.slippage_tracker import slippage_tracker
from app.websocket.socket_manager import socket_manager

logger = logging.getLogger(__name__)

# Liquidity-shortfall handling: fill what's available, then retry the unfilled
# remainder every FILL_RETRY_DELAY seconds up to MAX_FILL_RETRIES times.
MAX_FILL_RETRIES = 3
FILL_RETRY_DELAY = 5  # seconds

class OrderExecutor:
    def __init__(self) -> None:
        self.risk_engine = RiskEngine()

    async def execute(
        self,
        client: DeltaClient,
        account: dict,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: float,
        master_price: float,
        trade_type: str = "entry"
    ) -> dict:
        """
        Execute copy order on a follower account with retry mechanism and circuit breaker logic.
        """
        account_id = account["id"]
        account_name = account["name"]
        
        # 1. Create a pending trade copy record in Supabase
        copy_data = {
            "trade_id": trade_id,
            "account_id": account_id,
            "status": "pending",
            "quantity": quantity
        }
        insert_res = db.table("trade_copies").insert(copy_data).execute()
        if not insert_res.data:
            logger.error(f"Failed to create trade_copies record for account {account_name}")
            return {"account_id": account_id, "account_name": account_name, "status": "failed", "failure_reason": "DB error"}
            
        copy_record = insert_res.data[0]
        copy_id = copy_record["id"]
        
        # 2. Risk check
        allowed, reason = self.risk_engine.check(account, quantity, master_price)
        if not allowed:
            logger.warning(f"Risk check failed for follower {account_name}: {reason}")
            db.table("trade_copies").update({
                "status": "skipped",
                "failure_reason": reason
            }).eq("id", copy_id).execute()
            
            return {
                "account_id": account_id,
                "account_name": account_name,
                "status": "skipped",
                "failure_reason": reason
            }
            
        # 3. Execution with retries
        start_time = time.time()
        last_error = ""

        # Floor quantity to whole contracts (round down, never up)
        import math
        order_size = int(math.floor(quantity))
        if order_size <= 0:
            db.table("trade_copies").update({
                "status": "skipped",
                "failure_reason": f"Quantity rounded to 0: {quantity}"
            }).eq("id", copy_id).execute()
            return {
                "account_id": account_id,
                "account_name": account_name,
                "status": "skipped",
                "failure_reason": f"Quantity rounded to 0: {quantity}"
            }

        # Exit/SL trades close an existing position — reduce-only so the follower
        # can only reduce/close and never flip.
        is_exit = trade_type in ("exit", "sl")

        # Fill as much as the order book allows, then retry the unfilled
        # remainder every FILL_RETRY_DELAY seconds up to MAX_FILL_RETRIES times.
        # (Handles the case where combined demand across accounts exceeds
        # available liquidity — each account takes its share and keeps retrying.)
        total_filled = 0
        remaining = order_size
        weighted_px = []  # list of (filled_qty, price)

        for attempt in range(MAX_FILL_RETRIES + 1):  # 1 initial + N retries
            if remaining <= 0:
                break
            if attempt > 0:
                await asyncio.sleep(FILL_RETRY_DELAY)
                logger.info(f"Retry {attempt}/{MAX_FILL_RETRIES} for {account_name} on {symbol}: {remaining}/{order_size} lots still unfilled")
            try:
                resp = await client.place_order(
                    symbol=symbol, side=side.lower(), size=remaining,
                    order_type='market_order', reduce_only=is_exit,
                )
                oid = resp.get("id") or resp.get("result", {}).get("id")
                if not oid:
                    last_error = f"Invalid API response: {resp}"
                    continue
                # Market/IOC orders fill immediately — read how much actually filled.
                await asyncio.sleep(0.1)
                status = await client.get_order(oid)
                rd = status.get("result", status)
                filled = int(float(rd.get("filled_size") or 0))
                px = rd.get("average_fill_price") or rd.get("avg_fill_price")
                if filled > 0:
                    total_filled += filled
                    remaining = order_size - total_filled
                    if px:
                        weighted_px.append((filled, float(px)))
                else:
                    last_error = "No liquidity filled on this attempt"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Fill attempt {attempt + 1} for {account_name} on {symbol}: {last_error}")

        execution_time_ms = int((time.time() - start_time) * 1000)

        # Weighted-average execution price across the fills
        if weighted_px:
            exec_price = sum(q * p for q, p in weighted_px) / sum(q for q, p in weighted_px)
        else:
            exec_price = master_price

        fully_filled = total_filled >= order_size
        partial = 0 < total_filled < order_size

        if total_filled > 0:
            db.table("accounts").update({"consecutive_failures": 0, "status": "active"}).eq("id", account_id).execute()

            points, pct = await slippage_tracker.record_and_alert(
                trade_copy_id=copy_id, account_id=account_id, account_name=account_name,
                symbol=symbol, side=side, master_price=master_price,
                follower_price=exec_price, quantity=total_filled, execution_time_ms=execution_time_ms,
            )

            status_str = "filled" if fully_filled else "partial"
            reason = None if fully_filled else f"Partial fill: {total_filled}/{order_size} lots after {MAX_FILL_RETRIES} retries (insufficient liquidity)"
            db.table("trade_copies").update({
                "status": status_str, "quantity": total_filled, "failure_reason": reason,
            }).eq("id", copy_id).execute()

            await socket_manager.emit_account_update({"id": account_id, "consecutive_failures": 0, "status": "active"})

            if partial:
                logger.error(f"PARTIAL fill for {account_name} on {symbol}: {total_filled}/{order_size} lots after {MAX_FILL_RETRIES} retries")
                try:
                    alert = db.table("alerts").insert({
                        "level": "warning", "type": "partial_fill", "account_id": account_id,
                        "message": f"Partial fill for {account_name} on {symbol}: filled {total_filled} of {order_size} lots (insufficient liquidity after {MAX_FILL_RETRIES} retries)",
                        "metadata": {"symbol": symbol, "filled": total_filled, "requested": order_size},
                    }).execute()
                    if alert.data:
                        await socket_manager.emit_alert(alert.data[0])
                except Exception:
                    pass

            return {
                "account_id": account_id, "account_name": account_name,
                "status": status_str, "execution_price": exec_price, "slippage_pct": pct,
                "filled_quantity": total_filled, "requested_quantity": order_size,
                "execution_time_ms": execution_time_ms, "failure_reason": reason,
            }

        # Nothing filled at all after all retries
        logger.error(f"Copy trade UNFILLED for {account_name} on {symbol}: 0/{order_size} lots after {MAX_FILL_RETRIES} retries. Error: {last_error}")
        new_failures = account.get("consecutive_failures", 0) + 1
        db.table("accounts").update({"consecutive_failures": new_failures}).eq("id", account_id).execute()
        db.table("trade_copies").update({
            "status": "failed", "failure_reason": last_error or "No liquidity", "retry_count": MAX_FILL_RETRIES,
        }).eq("id", copy_id).execute()
        try:
            alert = db.table("alerts").insert({
                "level": "error", "type": "fill_failed", "account_id": account_id,
                "message": f"Order unfilled for {account_name} on {symbol}: 0/{order_size} lots after {MAX_FILL_RETRIES} retries ({last_error})",
                "metadata": {"symbol": symbol, "requested": order_size, "last_error": last_error},
            }).execute()
            if alert.data:
                await socket_manager.emit_alert(alert.data[0])
        except Exception:
            pass
        await socket_manager.emit_account_update({"id": account_id, "consecutive_failures": new_failures})
        return {
            "account_id": account_id, "account_name": account_name,
            "status": "failed", "failure_reason": last_error or "No liquidity",
            "execution_time_ms": execution_time_ms,
        }

order_executor = OrderExecutor()
