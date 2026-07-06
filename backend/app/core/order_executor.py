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

# Liquidity-shortfall handling (all-or-nothing FOK entries): if the full size
# can't fill instantly, retry a few times, quickly, to catch the next liquidity
# window. Kept short so follower entries land fast (worst case ≈ RETRIES*DELAY).
MAX_FILL_RETRIES = 4
FILL_RETRY_DELAY = 1.0  # seconds  (was 5s → caused 10-15s copy latency)

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

        # Exit/SL trades close an existing position — reduce-only so the follower
        # can only reduce/close and never flip. Closes are best-effort (fill what
        # liquidity allows); entries are all-or-nothing (see below).
        is_exit = trade_type in ("exit", "sl")

        filled_qty = 0
        exec_price = master_price

        if is_exit:
            # Best-effort reduce-only close — take whatever liquidity is available.
            try:
                resp = await client.place_order(
                    symbol=symbol, side=side.lower(), size=order_size,
                    order_type='market_order', reduce_only=True,
                )
                oid = resp.get("id") or resp.get("result", {}).get("id")
                if oid:
                    await asyncio.sleep(0.1)
                    rd = (await client.get_order(oid)).get("result", {})
                    filled_qty = int(float(rd.get("filled_size") or 0))
                    if rd.get("average_fill_price"):
                        exec_price = float(rd["average_fill_price"])
            except Exception as e:
                last_error = str(e)
        else:
            # ENTRY: all-or-nothing. Use Fill-Or-Kill so the order either fills
            # the FULL size or nothing at all (never a partial). If the book can't
            # supply the full size, retry every FILL_RETRY_DELAY sec up to
            # MAX_FILL_RETRIES times, then give up without placing anything.
            for attempt in range(MAX_FILL_RETRIES + 1):  # 1 initial + N retries
                if attempt > 0:
                    await asyncio.sleep(FILL_RETRY_DELAY)
                    logger.info(f"Retry {attempt}/{MAX_FILL_RETRIES} for {account_name} on {symbol}: need full {order_size} lots (insufficient book liquidity)")
                try:
                    resp = await client.place_order(
                        symbol=symbol, side=side.lower(), size=order_size,
                        order_type='market_order', time_in_force='fok',
                    )
                    oid = resp.get("id") or resp.get("result", {}).get("id")
                    if not oid:
                        last_error = f"Invalid API response: {resp}"
                        continue
                    await asyncio.sleep(0.1)
                    rd = (await client.get_order(oid)).get("result", {})
                    f = int(float(rd.get("filled_size") or 0))
                    if f >= order_size:  # FOK => either full or zero
                        filled_qty = f
                        if rd.get("average_fill_price"):
                            exec_price = float(rd["average_fill_price"])
                        break
                    last_error = f"Insufficient liquidity for full {order_size} lots (FOK killed)"
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"FOK attempt {attempt + 1} for {account_name} on {symbol}: {last_error}")

        execution_time_ms = int((time.time() - start_time) * 1000)

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

        # Entry could not be fully filled (no order placed) OR close filled nothing.
        reason = (f"Not filled: order book lacked the full {order_size} lots after "
                  f"{MAX_FILL_RETRIES} retries — order not placed") if not is_exit else (last_error or "No liquidity to close")
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
