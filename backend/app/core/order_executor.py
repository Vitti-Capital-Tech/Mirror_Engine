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
                # 1. Place limit order at master's entry price first
                logger.info(f"Placing limit order for {account_name} on {symbol} at price {master_price} for size {order_size}")
                limit_response = await client.place_order(
                    symbol=symbol,
                    side=side.lower(),
                    size=order_size,
                    order_type='limit_order',
                    limit_price=master_price
                )
                
                order_id = limit_response.get("id") or limit_response.get("result", {}).get("id")
                
                if order_id:
                    # Wait 100ms for limit order to fill
                    await asyncio.sleep(0.10)
                    
                    # Fetch current order fill state
                    order_status = await client.get_order(order_id)
                    result_data = order_status.get("result", order_status)
                    state = result_data.get("state")
                    filled_size = int(result_data.get("filled_size", 0))
                    
                    if state == "filled":
                        logger.info(f"Limit order {order_id} fully filled at {master_price}")
                        order_success = True
                        order_response = order_status
                        break
                    else:
                        # Cancel partial limit order
                        logger.warning(f"Limit order {order_id} is {state} (filled {filled_size}/{order_size}). Cancelling and sweeping remainder via market order.")
                        try:
                            await client.cancel_order(order_id)
                        except Exception as ce:
                            logger.warning(f"Limit cancel error (might have filled during cancel call): {ce}")
                            
                        # Confirm final filled size
                        final_status = await client.get_order(order_id)
                        final_result = final_status.get("result", final_status)
                        final_filled = int(final_result.get("filled_size", 0))
                        
                        remaining_size = order_size - final_filled
                        if remaining_size <= 0:
                            logger.info(f"Limit order fully filled during cancellation.")
                            order_success = True
                            order_response = final_status
                            break
                        else:
                            logger.info(f"Limit partially filled ({final_filled}/{order_size}). Punching market order for remainder {remaining_size}.")
                            market_response = await client.place_order(
                                symbol=symbol,
                                side=side.lower(),
                                size=remaining_size,
                                order_type='market_order'
                            )
                            order_success = True
                            order_response = market_response
                            break
                else:
                    last_error = f"Invalid API response on limit placement: {limit_response}"
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
