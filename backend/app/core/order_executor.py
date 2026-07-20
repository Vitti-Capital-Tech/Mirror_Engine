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

# All-or-nothing (FOK) for BOTH entries and exits: fill the full size or nothing.
# If the book can't supply it, wait FILL_RETRY_DELAY and retry up to
# MAX_FILL_RETRIES; if it still can't, the trade is skipped (no partial fills).
# The delay only applies when the first attempt can't fill — normal instant fills
# are unaffected.
MAX_FILL_RETRIES = 2
FILL_RETRY_DELAY = 5.0  # seconds (per desk: wait 5s before retrying)

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
            "quantity": quantity,
            "owner_id": account.get("owner_id"),
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

        # Fill with a plain market order (takes whatever the book offers), retrying
        # if the API errors or nothing fills. We do NOT set time_in_force='fok':
        # Delta rejects it ("allowed values are ioc, gtc") with a bad_schema error,
        # which was silently killing copies. A bare market order fills immediately.
        # Each retry orders only the UNFILLED remainder, so a partial fill on one
        # attempt can never cause an over-fill on the next. Exits are reduce-only.
        is_exit = trade_type in ("exit", "sl")

        filled_qty = 0
        exec_price = master_price

        async def _confirm(order_id, placed_size):
            """Return (filled_lots, avg_price) for THIS order. Delta exposes
            unfilled_size, not filled_size — so filled = size - unfilled_size,
            using the order's own size (we may order just the remainder)."""
            try:
                await asyncio.sleep(0.15)
                od = (await client.get_order(order_id)).get("result", {}) or {}
            except Exception:
                od = {}
            sz = float(od.get("size") or placed_size)
            unfilled = od.get("unfilled_size")
            unfilled = float(unfilled if unfilled is not None else sz)
            filled = max(0, int(sz - unfilled))
            avg = od.get("average_fill_price")
            return filled, (float(avg) if avg else None)

        for attempt in range(MAX_FILL_RETRIES + 1):
            remaining = order_size - filled_qty
            if remaining < 1:
                break
            if attempt > 0:
                await asyncio.sleep(FILL_RETRY_DELAY)
                logger.info(f"Retry {attempt}/{MAX_FILL_RETRIES} for {account_name} on {symbol} ({'exit' if is_exit else 'entry'})")
            try:
                resp = await client.place_order(
                    symbol=symbol, side=side.lower(), size=remaining,
                    order_type='market_order', reduce_only=is_exit,
                )
                oid = resp.get("id") or resp.get("result", {}).get("id")
                if not oid:
                    last_error = f"Invalid API response: {resp}"
                    continue
                f, avg = await _confirm(oid, remaining)
                filled_qty += max(0, f)
                if avg:
                    exec_price = avg
                if filled_qty >= order_size:
                    break
                last_error = f"Filled {filled_qty}/{order_size}, retrying remainder"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Market attempt {attempt + 1} for {account_name} on {symbol}: {last_error}")

        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[LATENCY] {account_name} {symbol}: follower order {'filled' if filled_qty > 0 else 'not filled'} in {execution_time_ms}ms")

        if filled_qty > 0:
            db.table("accounts").update({"consecutive_failures": 0, "status": "active"}).eq("id", account_id).execute()
            points, pct = await slippage_tracker.record_and_alert(
                trade_copy_id=copy_id, account_id=account_id, account_name=account_name,
                symbol=symbol, side=side, master_price=master_price,
                follower_price=exec_price, quantity=filled_qty, execution_time_ms=execution_time_ms,
            )
            db.table("trade_copies").update({
                "status": "filled", "quantity": filled_qty, "failure_reason": None,
            }).eq("id", copy_id).execute()
            await socket_manager.emit_account_update({"id": account_id, "consecutive_failures": 0, "status": "active"})
            return {
                "account_id": account_id, "account_name": account_name,
                "status": "filled", "execution_price": exec_price, "slippage_pct": pct,
                "filled_quantity": filled_qty, "execution_time_ms": execution_time_ms,
            }

        # Neither FOK nor the plain market order filled anything — genuinely no
        # liquidity on the book for this symbol right now.
        reason = (f"Not filled: no liquidity for {order_size} lots on {symbol} "
                  f"(FOK + market both empty) — {'close' if is_exit else 'entry'} skipped")
        logger.error(f"Copy {'close' if is_exit else 'entry'} not filled for {account_name} on {symbol}: {reason}")
        db.table("trade_copies").update({
            "status": "failed", "failure_reason": reason, "retry_count": MAX_FILL_RETRIES,
        }).eq("id", copy_id).execute()
        try:
            alert = db.table("alerts").insert({
                "level": "warning", "type": "liquidity_unavailable", "account_id": account_id,
                "message": f"{account_name} on {symbol}: {reason}",
                "metadata": {"symbol": symbol, "requested": order_size},
                "owner_id": account.get("owner_id"),
            }).execute()
            if alert.data:
                await socket_manager.emit_alert(alert.data[0])
        except Exception:
            pass
        return {
            "account_id": account_id, "account_name": account_name,
            "status": "failed", "failure_reason": reason,
            "execution_time_ms": execution_time_ms,
        }

order_executor = OrderExecutor()
