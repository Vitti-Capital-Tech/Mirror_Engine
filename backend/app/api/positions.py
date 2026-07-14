import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict
from fastapi import APIRouter, HTTPException, Depends
from app.database import db
from app.models.position import PositionResponse
from app.services.delta_client import DeltaClient
from app.core.auth import get_current_user, CurrentUser, scope_owned, owned_account_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])

# India market operates in IST; "today" is an IST calendar day.
IST = timezone(timedelta(hours=5, minutes=30))
# Ledger entry types that make up realized trading PnL for the day on Delta India.
# 'cashflow' = realized PnL booked when a trade closes, 'settlement' = options
# expiry settlement, 'commission' = trading fees (negative), 'funding' = perp funding.
# Deposits/withdrawals use their own types and are intentionally excluded.
_REALIZED_PNL_TYPES = {"cashflow", "settlement", "commission", "funding", "pnl"}


async def _realized_pnl_today(client: DeltaClient) -> float:
    """Sum realized PnL (and fees) booked since IST midnight from the wallet ledger."""
    now_ist = datetime.now(IST)
    start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_ist.astimezone(timezone.utc)
    start_us = int(start_utc.timestamp() * 1_000_000)
    total = 0.0
    try:
        txns = await client.get_wallet_transactions(start_time_us=start_us)
        for tx in txns:
            ttype = (tx.get("transaction_type") or "").lower()
            if ttype not in _REALIZED_PNL_TYPES:
                continue
            # Only count the USD/USDT settlement asset (Delta India labels it "USD").
            asset = tx.get("asset_symbol")
            if asset and asset not in ("USD", "USDT"):
                continue
            # Safety filter on created_at in case start_time isn't honored.
            ca = tx.get("created_at")
            if ca:
                try:
                    when = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when < start_utc:
                        continue
                except Exception:
                    pass
            total += float(tx.get("amount") or 0)
    except Exception as e:
        logger.warning(f"Could not fetch realized PnL: {e}")
    return total


def _format_live_position(account_id: str, account_name: str, pos: dict) -> dict | None:
    """Convert a raw Delta position payload into the API response shape.

    Returns None if the position has zero size (closed) so it is omitted.
    """
    symbol = pos.get("product_symbol") or pos.get("symbol")
    if not symbol:
        return None

    raw_size = float(pos.get("size") or pos.get("quantity") or 0.0)
    if raw_size == 0:
        return None  # closed position — don't show

    # Side: explicit field wins, else sign of size
    explicit_side = pos.get("side")
    if explicit_side and str(explicit_side).lower() in ("long", "short", "buy", "sell"):
        s = str(explicit_side).lower()
        side = "long" if s in ("long", "buy") else "short"
    else:
        side = "long" if raw_size >= 0 else "short"

    qty = abs(raw_size)
    entry_price = float(pos.get("entry_price") or 0.0)
    current_price = float(pos.get("mark_price") or pos.get("current_price") or entry_price)

    # Mark-to-market PnL computed live from the mark price. This matches the
    # value Delta's own web UI shows (its UPNL column). NOTE: Delta's API
    # `unrealized_pnl` field is NOT the MTM PnL for options — it disagrees with
    # the UI — so we deliberately do not use it.
    # contract_value lives under the nested product spec (e.g. 0.001 for BTC options).
    product = pos.get("product") or {}
    multiplier = float(
        product.get("contract_value")
        or pos.get("contract_value")
        or (0.001 if ("-C-" in symbol or "-P-" in symbol or symbol.startswith("C-") or symbol.startswith("P-")) else 1.0)
    )
    if side == "long":
        unrealized_pnl = (current_price - entry_price) * qty * multiplier
    else:
        unrealized_pnl = (entry_price - current_price) * qty * multiplier

    sl_price = float(pos.get("stop_loss_price")) if pos.get("stop_loss_price") else None
    tp_price = float(pos.get("take_profit_price")) if pos.get("take_profit_price") else None

    return {
        "id": f"{account_id}-{symbol}",
        "account_id": account_id,
        "account_name": account_name,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "entry_price": entry_price,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "sync_status": "synced",
        "last_synced_at": None,
        "created_at": pos.get("created_at"),
    }


async def _fetch_account_positions(acc: dict) -> List[dict]:
    """Fetch live positions for a single account directly from Delta Exchange."""
    client = DeltaClient(
        api_key=acc["api_key"],
        api_secret=acc["api_secret"],
        environment=acc.get("environment", "demo"),
    )
    try:
        live = await client.get_positions()
        out = []
        for pos in live:
            formatted = _format_live_position(acc["id"], acc["name"], pos)
            if formatted:
                out.append(formatted)
        return out
    except Exception as e:
        logger.warning(f"Failed to fetch live positions for {acc['name']}: {e}")
        return []
    finally:
        await client.close()


@router.get("", response_model=List[PositionResponse])
async def list_positions(user: CurrentUser = Depends(get_current_user)):
    """List open positions for the caller's accounts, fetched LIVE from Delta.

    Reads directly from the exchange (not the DB) so the view always matches
    exactly what Delta shows, with no flicker from background writers.
    """
    try:
        acc_res = scope_owned(db.table("accounts").select("*"), user).execute()
        accounts = acc_res.data or []
        if not accounts:
            return []

        results = await asyncio.gather(
            *[_fetch_account_positions(acc) for acc in accounts],
            return_exceptions=True,
        )

        positions = []
        for r in results:
            if isinstance(r, list):
                positions.extend(r)

        positions.sort(key=lambda x: (x["account_name"], x["symbol"]))
        return positions
    except Exception as e:
        logger.error(f"Error querying positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sync-status")
async def get_sync_status(user: CurrentUser = Depends(get_current_user)) -> Dict[str, int]:
    """Retrieve summary of master vs followers position synchronization states."""
    try:
        res = scope_owned(db.table("positions").select("sync_status"), user).execute()
        positions = res.data or []
        
        # Calculate status counts
        total = len(positions)
        synced = sum(1 for p in positions if p.get("sync_status") == "synced")
        out_of_sync = sum(1 for p in positions if p.get("sync_status") == "out_of_sync")
        unknown = sum(1 for p in positions if p.get("sync_status") == "unknown" or p.get("sync_status") is None)
        
        return {
            "total": total,
            "synced": synced,
            "out_of_sync": out_of_sync,
            "unknown": unknown
        }
    except Exception as e:
        logger.error(f"Error checking position sync stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{account_id}", response_model=List[PositionResponse])
async def list_account_positions(account_id: str, user: CurrentUser = Depends(get_current_user)):
    """List open positions specifically for one account (must be owned)."""
    try:
        owned_account_or_404(account_id, user)
        res = db.table("positions").select("*, accounts(name)").eq("account_id", account_id).execute()
        positions = res.data or []
        
        formatted = []
        for pos in positions:
            acc_info = pos.get("accounts") or {}
            acc_name = acc_info.get("name") or "Unknown"
            
            formatted.append({
                "id": pos.get("id"),
                "account_id": pos.get("account_id"),
                "account_name": acc_name,
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "quantity": float(pos.get("quantity", 0)),
                "entry_price": float(pos.get("entry_price", 0)),
                "current_price": float(pos.get("current_price") or pos.get("entry_price") or 0),
                "unrealized_pnl": float(pos.get("unrealized_pnl") or 0),
                "sl_price": pos.get("sl_price"),
                "tp_price": pos.get("tp_price"),
                "sync_status": pos.get("sync_status", "unknown"),
                "last_synced_at": pos.get("last_synced_at"),
                # created_at = first DB write time (use created_at if available, else updated_at as proxy)
                "created_at": pos.get("created_at") or pos.get("updated_at")
            })
            
        return formatted
    except Exception as e:
        logger.error(f"Error querying positions for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def _sync_accounts(accounts: list) -> list:
    """Sync live balance + positions for the given accounts into the DB."""
    sync_results = []
    if True:
        for acc in accounts:
            client = DeltaClient(
                api_key=acc["api_key"],
                api_secret=acc["api_secret"],
                environment=acc.get("environment", "demo")
            )
            try:
                # 1. Fetch and update live wallet balance for the account
                try:
                    wallet = await client.get_wallet()
                    # Parse balances: Delta API returns a list of balances per asset (e.g. USDT, BTC)
                    # We will sum them or fetch the main margin asset (USDT).
                    balances_list = wallet.get("result", wallet) if isinstance(wallet, dict) else wallet
                    if isinstance(balances_list, list):
                        # Find USDT or first available balance
                        usdt_bal = next((b for b in balances_list if b.get("asset") == "USDT"), None)
                        if not usdt_bal and len(balances_list) > 0:
                            usdt_bal = balances_list[0]
                        
                        if usdt_bal:
                            balance_val = float(usdt_bal.get("balance") or 0.0)
                            avail_margin = float(usdt_bal.get("available_margin") or balance_val)
                            # Update account table in Supabase
                            db.table("accounts").update({
                                "balance": balance_val,
                                "available_margin": avail_margin
                            }).eq("id", acc["id"]).execute()
                            logger.info(f"Updated live balance for {acc['name']}: bal={balance_val}, margin={avail_margin}")
                except Exception as bal_err:
                    logger.warning(f"Failed to fetch wallet balance for {acc['name']}: {bal_err}")

                live_positions = await client.get_positions()

                # Accumulate live unrealized PnL as "today's PnL" (intraday MTM).
                today_pnl = 0.0
                for pos in live_positions:
                    symbol = pos.get("product_symbol") or pos.get("symbol")
                    if not symbol:
                        continue

                    fmt = _format_live_position(acc["id"], acc["name"], pos)
                    if fmt:
                        today_pnl += float(fmt.get("unrealized_pnl") or 0.0)

                    from app.core.position_monitor import position_monitor
                    await position_monitor.on_position_update(
                        account_id=acc["id"],
                        account_name=acc["name"],
                        position_data=pos
                    )

                # Add realized PnL booked today (closed trades) so the figure
                # survives closing positions — "today's PnL" = realized + unrealized.
                realized_today = await _realized_pnl_today(client)
                today_pnl += realized_today

                db.table("accounts").update({"today_pnl": round(today_pnl, 2)}).eq("id", acc["id"]).execute()

                # Deletion is intentionally omitted here.
                # WebSocket handles position closes (size=0 messages) in real time.
                # Inferring closure from REST absence causes flickering when the API
                # partially fails (e.g. options endpoint 400s).
                
                sync_results.append({"account_name": acc["name"], "status": "success", "positions_count": len(live_positions)})
            except Exception as e:
                logger.error(f"Failed to sync live positions for account {acc['name']}: {e}")
                sync_results.append({"account_name": acc["name"], "status": "failed", "error": str(e)})
            finally:
                await client.close()
    return sync_results


async def sync_live_positions():
    """Internal: sync ALL active accounts (used by the background poller/startup)."""
    acc_res = db.table("accounts").select("*").eq("status", "active").execute()
    return {"success": True, "results": await _sync_accounts(acc_res.data or [])}


@router.post("/sync-live")
async def sync_live_endpoint(user: CurrentUser = Depends(get_current_user)):
    """Sync the caller's active accounts (admins: all)."""
    try:
        acc_res = scope_owned(db.table("accounts").select("*"), user).eq("status", "active").execute()
        return {"success": True, "results": await _sync_accounts(acc_res.data or [])}
    except Exception as e:
        logger.error(f"Error in sync-live: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/master/open-orders")
async def get_master_open_orders(user: CurrentUser = Depends(get_current_user)):
    """Fetch live open orders directly from Delta for the caller's Master account."""
    try:
        master_res = scope_owned(db.table("accounts").select("*"), user).eq("is_master", True).execute()
        if not master_res.data:
            return []

        master = master_res.data[0]
        client = DeltaClient(
            api_key=master["api_key"],
            api_secret=master["api_secret"],
            environment=master.get("environment", "demo")
        )
        try:
            # Stop / SL / TP orders rest in the "pending" state; plain limit orders
            # in "open". Fetch both so triggered/stop orders are shown too.
            open_orders = []
            seen_ids = set()
            for st in ("open", "pending"):
                try:
                    for o in await client.get_open_orders(state=st):
                        oid = o.get("id")
                        if oid in seen_ids:
                            continue
                        seen_ids.add(oid)
                        open_orders.append(o)
                except Exception as e:
                    logger.warning(f"Failed to fetch {st} orders: {e}")
            formatted = []
            for order in open_orders:
                base_type = order.get("order_type")  # 'market_order' / 'limit_order'
                stop_type = order.get("stop_order_type")  # e.g. 'stop_loss_order'
                # A stop/take-profit order is a triggered order — label it like Delta does.
                if stop_type or order.get("stop_price"):
                    if base_type == "limit_order":
                        display_type = "stop_limit"
                    else:
                        display_type = "stop_market"
                else:
                    display_type = base_type

                formatted.append({
                    "id": order.get("id"),
                    "symbol": order.get("product_symbol"),
                    "side": order.get("side"),
                    "quantity": float(order.get("size", 0)),
                    "limit_price": float(order.get("limit_price")) if order.get("limit_price") else None,
                    "stop_price": float(order.get("stop_price")) if order.get("stop_price") else None,
                    "order_type": display_type,
                    "trigger_method": order.get("stop_trigger_method"),  # mark_price / spot_price / etc.
                    "created_at": order.get("created_at")
                })
            # Sort strictly: newest orders at the top, or sorted by ID for complete stability
            formatted.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            return formatted
        except Exception as e:
            logger.error(f"Failed to fetch master open orders: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch open orders from Delta: {str(e)}")
        finally:
            await client.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching master open orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/master/order-history")
async def get_master_order_history(user: CurrentUser = Depends(get_current_user)):
    """Fetch recent closed/cancelled orders (order history) from Delta for the
    caller's Master account."""
    try:
        master_res = scope_owned(db.table("accounts").select("*"), user).eq("is_master", True).execute()
        if not master_res.data:
            return []
        master = master_res.data[0]
        client = DeltaClient(
            api_key=master["api_key"],
            api_secret=master["api_secret"],
            environment=master.get("environment", "demo"),
        )
        try:
            history = await client.get_order_history(page_size=50)
            formatted = []
            for o in history:
                base_type = o.get("order_type")
                stop_type = o.get("stop_order_type")
                if stop_type or o.get("stop_price"):
                    display_type = "stop_limit" if base_type == "limit_order" else "stop_market"
                else:
                    display_type = base_type
                size = float(o.get("size") or 0)
                unfilled = float(o.get("unfilled_size") or 0)
                formatted.append({
                    "id": o.get("id"),
                    "symbol": o.get("product_symbol"),
                    "side": o.get("side"),
                    "quantity": size,
                    "filled": max(0.0, size - unfilled),
                    "limit_price": float(o.get("limit_price")) if o.get("limit_price") else None,
                    "stop_price": float(o.get("stop_price")) if o.get("stop_price") else None,
                    "avg_fill_price": float(o.get("average_fill_price")) if o.get("average_fill_price") else None,
                    "order_type": display_type,
                    "reduce_only": bool(o.get("reduce_only")),
                    "state": o.get("state"),
                    "reason": o.get("cancellation_reason") or o.get("reason"),
                    "created_at": o.get("created_at"),
                })
            formatted.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            return formatted
        finally:
            await client.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching master order history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
