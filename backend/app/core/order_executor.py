import logging
import math
import os
import time
import asyncio
from typing import Dict, Any, Optional
from app.database import db
from app.services.delta_client import DeltaClient
from app.core.risk_engine import RiskEngine
from app.core.slippage_tracker import slippage_tracker
from app.websocket.socket_manager import socket_manager

logger = logging.getLogger(__name__)

# Fill policy (per desk): rest a GTC LIMIT at the MASTER's price to match the
# price; if it hasn't filled within FILL_WAIT_SEC, MARKET the unfilled remainder
# so the copy still goes through. Never use time_in_force='fok' — Delta rejects
# it ("allowed values are ioc, gtc").
FILL_WAIT_SEC = float(os.getenv("FILL_WAIT_SEC", "5.0"))  # ceiling for the price-matching GTC limit
# How often to ask whether the resting limit filled. This used to be a flat 1s,
# which meant a limit that filled in 60ms was still reported ~1.15s later and a
# fill on the third look cost ~3.2s — the bulk of the observed copy latency
# (execution_time_ms clustered at 3143/3184/3230/3329ms through Aug 2026, against
# 1561-3032ms end-to-end before this two-phase policy existed). The 5s ceiling is
# unchanged; only the granularity of noticing improves.
FILL_POLL_SEC = float(os.getenv("FILL_POLL_SEC", "0.25"))
# Delta needs a moment after acceptance before GET /orders reflects a fill.
CONFIRM_SETTLE_SEC = float(os.getenv("CONFIRM_SETTLE_SEC", "0.15"))
MAX_FILL_RETRIES = 2  # (retained for reference / other callers)
FILL_RETRY_DELAY = 5.0

# 003_partial_fill_accounting.sql adds trade_copies.requested_quantity and makes
# 'partial' a legal status. Until it is applied, Postgres rejects both writes —
# and the copy row is inserted BEFORE the order is placed, so an unmigrated
# database would stop the desk copying altogether rather than just lose a column.
# These two helpers degrade to the pre-migration shape and say so once, so the
# order of deploy vs migration can't take trading offline.
_HAS_PARTIAL_SCHEMA = True


def _degrade_schema(exc: Exception) -> None:
    global _HAS_PARTIAL_SCHEMA
    if _HAS_PARTIAL_SCHEMA:
        logger.error(
            "trade_copies is missing requested_quantity and/or the 'partial' status "
            "(%s). Apply backend/database/migrations/003_partial_fill_accounting.sql — "
            "until then partial fills are recorded as 'filled' with the shortfall in "
            "failure_reason.", exc,
        )
    _HAS_PARTIAL_SCHEMA = False


class OrderExecutor:
    def __init__(self) -> None:
        self.risk_engine = RiskEngine()

    @staticmethod
    def _insert_copy_row(copy_data: dict):
        """Insert the pending copy row, retrying without the post-migration
        columns if the database doesn't have them yet."""
        try:
            return db.table("trade_copies").insert(copy_data).execute()
        except Exception as e:
            if "requested_quantity" not in copy_data:
                raise
            _degrade_schema(e)
            legacy = {k: v for k, v in copy_data.items() if k != "requested_quantity"}
            return db.table("trade_copies").insert(legacy).execute()

    @staticmethod
    def _write_copy_status(copy_id, status: str, quantity, reason) -> None:
        """Record the copy's terminal status, downgrading 'partial' to 'filled'
        on a pre-migration database (the shortfall still reaches failure_reason,
        so the information isn't lost — only the badge is)."""
        payload = {"status": status, "quantity": quantity, "failure_reason": reason}
        try:
            db.table("trade_copies").update(payload).eq("id", copy_id).execute()
        except Exception as e:
            if status != "partial":
                raise
            _degrade_schema(e)
            payload["status"] = "filled"
            db.table("trade_copies").update(payload).eq("id", copy_id).execute()

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
        if _HAS_PARTIAL_SCHEMA:
            # The proportional TARGET, kept for the life of the row. `quantity` is
            # overwritten below with what actually filled, so without this the
            # history can't answer "the follower got 1 lot — of how many?".
            copy_data["requested_quantity"] = quantity
        try:
            insert_res = self._insert_copy_row(copy_data)
        except Exception as e:
            logger.error(f"Could not create trade_copies record for {account_name}: {e}")
            return {"account_id": account_id, "account_name": account_name,
                    "status": "failed", "failure_reason": f"DB error: {e}"}
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

        # Rest a GTC LIMIT at the MASTER's price (match the price); if it hasn't
        # filled within FILL_WAIT_SEC, cancel the remainder and MARKET it so the
        # copy still goes through. Never fok (Delta rejects it). Exits reduce-only.
        is_exit = trade_type in ("exit", "sl")

        filled_qty = 0
        exec_price = master_price
        limit_px = float(master_price) if master_price else None

        # True when the exchange could not be read AND the limit could not be
        # cancelled — i.e. lots may still fill behind our back. Phase 2 is skipped
        # in that state: an under-filled copy is recovered by the 15s position
        # reconciler, an over-filled one leaves the follower long/short more than
        # the master and has to be unwound at a worse price.
        unknown_state = False

        async def _confirm(order_id, placed_size, settle: float = CONFIRM_SETTLE_SEC):
            """Return (filled_lots, avg_price) for THIS order, or (None, None) if
            the exchange couldn't be read. Delta exposes unfilled_size, not
            filled_size — so filled = size - unfilled_size, using the order's own
            size (we may order just the remainder).

            None means UNKNOWN, never "zero filled". Collapsing the two is how a
            single REST hiccup turned into a double fill: get_order raised, the
            caller read 0 filled, and Phase 2 marketed the FULL size on top of a
            limit that had already filled it."""
            if settle:
                await asyncio.sleep(settle)
            try:
                od = (await client.get_order(order_id)).get("result", {}) or {}
            except Exception as e:
                logger.warning(f"Could not read order {order_id} for {account_name} on {symbol}: {e}")
                return None, None
            unfilled = od.get("unfilled_size")
            if not od or unfilled is None:
                logger.warning(f"Order {order_id} for {account_name} on {symbol} returned no fill state: {od}")
                return None, None
            sz = float(od.get("size") or placed_size)
            filled = max(0, int(sz - float(unfilled)))
            avg = od.get("average_fill_price")
            return filled, (float(avg) if avg else None)

        # Phase 1 — GTC limit at the master's price, wait up to FILL_WAIT_SEC.
        if limit_px and limit_px > 0:
            oid = None
            product_id = None
            try:
                resp = await client.place_order(
                    symbol=symbol, side=side.lower(), size=order_size,
                    order_type='limit_order', limit_price=limit_px, reduce_only=is_exit,
                )
                result = resp.get("result", resp) if isinstance(resp, dict) else {}
                oid = result.get("id") or resp.get("id")
                product_id = result.get("product_id")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Limit place failed for {account_name} on {symbol} @ {limit_px}: {last_error}")
            if oid:
                deadline = time.time() + FILL_WAIT_SEC
                while True:
                    f, avg = await _confirm(oid, order_size)
                    if f is not None:
                        filled_qty = max(filled_qty, f)
                        if avg:
                            exec_price = avg
                    if filled_qty >= order_size or time.time() >= deadline:
                        break
                    await asyncio.sleep(FILL_POLL_SEC)

                if filled_qty < order_size:
                    # Cancel FIRST, then count. Confirming before the cancel left a
                    # window where the limit filled between the read and the cancel;
                    # Phase 2 then marketed a remainder the follower had already
                    # bought. The cancel is what makes the fill count final, so it
                    # has to happen before the number is trusted.
                    cancelled = False
                    try:
                        await client.cancel_order(str(oid), product_id=product_id)
                        cancelled = True
                    except Exception as e:
                        logger.warning(f"Could not cancel unfilled limit {oid} for {account_name}: {e}")
                    f, avg = await _confirm(oid, order_size)
                    if f is not None:
                        filled_qty = max(filled_qty, f)
                        if avg:
                            exec_price = avg
                    elif not cancelled:
                        unknown_state = True
                        last_error = (
                            f"Limit {oid} could be neither cancelled nor read — "
                            f"skipping the market remainder to avoid over-filling"
                        )
                        logger.error(f"{account_name} on {symbol}: {last_error}")

        # Phase 2 — still short → MARKET the remainder so the copy goes through.
        if filled_qty < order_size and not unknown_state:
            remaining = order_size - filled_qty
            try:
                resp = await client.place_order(
                    symbol=symbol, side=side.lower(), size=remaining,
                    order_type='market_order', reduce_only=is_exit,
                )
                oid2 = resp.get("id") or resp.get("result", {}).get("id")
                if oid2:
                    f2, avg2 = await _confirm(oid2, remaining)
                    if f2 is None:
                        # The exchange accepted the market order but we can't read
                        # it back. Count it as filled: a market order that was
                        # accepted has almost certainly executed, and assuming it
                        # did NOT is what makes the reconciler stack a second
                        # order on top of it. If it really didn't fill, the 15s
                        # position reconciler tops the follower up — the recoverable
                        # direction of the two.
                        logger.warning(
                            f"Market fallback for {account_name} on {symbol} unreadable "
                            f"({oid2}) — assuming {remaining} filled; reconciler will correct"
                        )
                        filled_qty += remaining
                    else:
                        filled_qty += max(0, f2)
                        if avg2:
                            exec_price = avg2
                        if f2 <= 0:
                            last_error = f"Market fill empty for {symbol} (no liquidity)"
                        else:
                            logger.info(f"Market fallback filled {f2}/{remaining} for {account_name} on {symbol}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Market fallback failed for {account_name} on {symbol}: {last_error}")

        execution_time_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[LATENCY] {account_name} {symbol}: follower order {'filled' if filled_qty > 0 else 'not filled'} in {execution_time_ms}ms")

        if filled_qty > 0:
            # A copy that filled only part of its proportional target is NOT a
            # success — the follower is left holding a different fraction of the
            # master than every other leg, which is the one thing this platform
            # exists to prevent. It used to be stored as 'filled' with the partial
            # quantity and nothing else, so the history showed a green FILLED for a
            # follower that was out of proportion (live 2026-08-12, C-BTC-65600:
            # master exited 3000 lots, the leg recorded "filled 1" against a
            # target of ~34, no alert, no top-up).
            shortfall = order_size - filled_qty
            is_partial = shortfall > 0
            db.table("accounts").update({"consecutive_failures": 0, "status": "active"}).eq("id", account_id).execute()
            points, pct = await slippage_tracker.record_and_alert(
                trade_copy_id=copy_id, account_id=account_id, account_name=account_name,
                symbol=symbol, side=side, master_price=master_price,
                follower_price=exec_price, quantity=filled_qty, execution_time_ms=execution_time_ms,
            )
            reason = None
            if is_partial:
                reason = (
                    f"Partial {'close' if is_exit else 'entry'}: filled {filled_qty} of "
                    f"{order_size} lots on {symbol} ({shortfall} short)"
                    + (f" — {last_error}" if last_error else "")
                )
                logger.error(f"{account_name}: {reason}")
            self._write_copy_status(
                copy_id, "partial" if is_partial else "filled", filled_qty, reason
            )
            await socket_manager.emit_account_update({"id": account_id, "consecutive_failures": 0, "status": "active"})
            if is_partial:
                # Surfaced as an alert because the reconciler's top-up is silent:
                # without this a chronically under-filling symbol looks healthy.
                try:
                    alert = db.table("alerts").insert({
                        "level": "warning", "type": "partial_fill", "account_id": account_id,
                        "trade_copy_id": copy_id,
                        "message": f"{account_name} on {symbol}: {reason}",
                        "metadata": {"symbol": symbol, "requested": order_size,
                                     "filled": filled_qty, "short": shortfall},
                        "owner_id": account.get("owner_id"),
                    }).execute()
                    if alert.data:
                        await socket_manager.emit_alert(alert.data[0])
                except Exception as e:
                    logger.warning(f"Could not raise partial_fill alert: {e}")
            return {
                "account_id": account_id, "account_name": account_name,
                "status": "partial" if is_partial else "filled",
                "execution_price": exec_price, "slippage_pct": pct,
                "filled_quantity": filled_qty, "requested_quantity": order_size,
                "failure_reason": reason, "execution_time_ms": execution_time_ms,
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
