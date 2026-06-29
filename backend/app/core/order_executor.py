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

MAX_RETRIES = 3
BACKOFFS = [0.05, 0.10, 0.20]  # seconds
CIRCUIT_BREAKER_LIMIT = 5

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
        order_success = False
        last_error = ""
        order_response = None
        
        # Round quantity to integer for contract sizes
        order_size = int(round(quantity))
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

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Place a single market order for the full size. A market order
                # fills immediately and atomically, so the follower position
                # exactly mirrors the master. (The previous limit-then-sweep
                # approach could double-fill: if the limit filled at the same
                # instant we tried to cancel it, the cancel 404'd, we misread
                # the fill as 0, and fired a second market order — producing 2x
                # the intended size.)
                logger.info(f"Placing market order for {account_name} on {symbol} side={side.lower()} size={order_size}")
                market_response = await client.place_order(
                    symbol=symbol,
                    side=side.lower(),
                    size=order_size,
                    order_type='market_order'
                )

                order_id = market_response.get("id") or market_response.get("result", {}).get("id")
                if order_id:
                    order_success = True
                    order_response = market_response
                    break
                else:
                    last_error = f"Invalid API response on market placement: {market_response}"
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt + 1} failed for {account_name} on {symbol}: {last_error}")
                
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BACKOFFS[attempt])
                
        execution_time_ms = int((time.time() - start_time) * 1000)
        
        if order_success:
            result_data = order_response.get("result", order_response)
            exec_price = result_data.get("avg_fill_price") or result_data.get("limit_price")
            if exec_price:
                exec_price = float(exec_price)
            else:
                exec_price = master_price  # fallback to prevent division by zero in slippage
                
            # Update consecutive_failures to 0 in DB
            db.table("accounts").update({
                "consecutive_failures": 0,
                "status": "active"
            }).eq("id", account_id).execute()
            
            # Record slippage and update DB status to filled
            points, pct = await slippage_tracker.record_and_alert(
                trade_copy_id=copy_id,
                account_id=account_id,
                account_name=account_name,
                symbol=symbol,
                side=side,
                master_price=master_price,
                follower_price=exec_price,
                quantity=quantity,
                execution_time_ms=execution_time_ms
            )
            
            # Emit account update with reset failures
            await socket_manager.emit_account_update({
                "id": account_id,
                "consecutive_failures": 0,
                "status": "active"
            })
            
            return {
                "account_id": account_id,
                "account_name": account_name,
                "status": "filled",
                "execution_price": exec_price,
                "slippage_pct": pct,
                "execution_time_ms": execution_time_ms
            }
        else:
            logger.error(f"Copy trade failed for follower {account_name} on {symbol} after {MAX_RETRIES + 1} attempts. Error: {last_error}")
            
            # Increment consecutive failures
            new_failures = account.get("consecutive_failures", 0) + 1
            new_status = "active"
            
            if new_failures >= CIRCUIT_BREAKER_LIMIT:
                new_status = "circuit_break"
                msg = f"Circuit breaker triggered for {account_name}: {new_failures} consecutive failures. Account PAUSED."
                logger.critical(msg)
                
                # Record circuit break alert
                alert_data = {
                    "level": "critical",
                    "type": "circuit_breaker",
                    "account_id": account_id,
                    "message": msg,
                    "metadata": {
                        "consecutive_failures": new_failures,
                        "last_error": last_error,
                        "symbol": symbol
                    }
                }
                alert_result = db.table("alerts").insert(alert_data).execute()
                if alert_result.data:
                    await socket_manager.emit_alert(alert_result.data[0])
            
            # Update account in DB
            db.table("accounts").update({
                "consecutive_failures": new_failures,
                "status": new_status
            }).eq("id", account_id).execute()
            
            # Update copy status in DB
            db.table("trade_copies").update({
                "status": "failed",
                "failure_reason": last_error,
                "retry_count": MAX_RETRIES
            }).eq("id", copy_id).execute()
            
            # Emit account update
            await socket_manager.emit_account_update({
                "id": account_id,
                "consecutive_failures": new_failures,
                "status": new_status
            })
            
            return {
                "account_id": account_id,
                "account_name": account_name,
                "status": "failed",
                "failure_reason": last_error,
                "execution_time_ms": execution_time_ms
            }

order_executor = OrderExecutor()
