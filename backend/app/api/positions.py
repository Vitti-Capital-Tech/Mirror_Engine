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


def _num(v):
    """Coerce to float or None (mirrors the frontend's tolerant num())."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _prod(o: dict) -> dict:
    """Minimal product spec the live tables read — contract_value + underlying."""
    p = o.get("product") or {}
    ua = p.get("underlying_asset") or {}
    return {
        "contract_value": _num(p.get("contract_value")),
        "underlying_asset": {"symbol": ua.get("symbol")},
    }


async def _spot_map(client: DeltaClient) -> dict:
    """Underlying index/spot per asset (BTC, ETH) for computing option notional.

    Fetched from Delta's public perpetual tickers. Best-effort — on any failure
    the map is empty and the frontend simply renders "—" for the Notional column.
    """
    out: dict = {}
    for underlying, ticker in (("BTC", "BTCUSD"), ("ETH", "ETHUSD")):
        try:
            t = await client.get_ticker(ticker)
            spot = _num(t.get("spot_price")) or _num(t.get("mark_price")) or _num(t.get("close"))
            if spot is not None:
                out[underlying] = spot
        except Exception:
            continue
    return out


def _spot_for(row: dict, spots: dict):
    """Spot index for a row, keyed by its underlying asset (default BTC)."""
    prod = row.get("product") or {}
    ua = (prod.get("underlying_asset") or {}).get("symbol") or "BTC"
    return spots.get(ua) or spots.get("BTC")


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

# ---------------------------------------------------------------------------
# Live Delta view (per account) — the exact raw fields the Delta-style tables
# read, so every account (master + followers) renders identical columns.
# ---------------------------------------------------------------------------

def _enrich_order(o: dict, spots: dict) -> dict:
    """Open / stop order → the fields the Open Orders + Stop Orders tables read."""
    return {
        "id": o.get("id"),
        "client_order_id": o.get("client_order_id"),
        "product_symbol": o.get("product_symbol"),
        "product_id": o.get("product_id"),
        "side": o.get("side"),
        "size": _num(o.get("size")),
        "unfilled_size": _num(o.get("unfilled_size")),
        "order_type": o.get("order_type"),
        "reduce_only": bool(o.get("reduce_only")),
        "limit_price": _num(o.get("limit_price")),
        "average_fill_price": _num(o.get("average_fill_price")),
        "stop_price": _num(o.get("stop_price")),
        "stop_order_type": o.get("stop_order_type"),
        "stop_trigger_method": o.get("stop_trigger_method"),
        "bracket_order": o.get("bracket_order"),
        "bracket_take_profit_price": _num(o.get("bracket_take_profit_price")),
        "bracket_stop_loss_price": _num(o.get("bracket_stop_loss_price")),
        "state": o.get("state"),
        "created_at": o.get("created_at"),
        "product": _prod(o),
        "spot_price": _spot_for(o, spots),
    }


def _enrich_history(o: dict, spots: dict) -> dict:
    """Past order → the fields the Order History table reads."""
    meta = o.get("meta_data") or {}
    return {
        "id": o.get("id"),
        "client_order_id": o.get("client_order_id"),
        "product_symbol": o.get("product_symbol"),
        "product_id": o.get("product_id"),
        "side": o.get("side"),
        "size": _num(o.get("size")),
        "unfilled_size": _num(o.get("unfilled_size")),
        "order_type": o.get("order_type"),
        "reduce_only": bool(o.get("reduce_only")),
        "limit_price": _num(o.get("limit_price")),
        "average_fill_price": _num(o.get("average_fill_price")),
        "stop_price": _num(o.get("stop_price")),
        "stop_order_type": o.get("stop_order_type"),
        "bracket_order": o.get("bracket_order"),
        "bracket_take_profit_price": _num(o.get("bracket_take_profit_price")),
        "bracket_stop_loss_price": _num(o.get("bracket_stop_loss_price")),
        "state": o.get("state"),
        "cancellation_reason": o.get("cancellation_reason") or o.get("reason"),
        "realized_pnl": _num(o.get("realized_pnl") or o.get("realised_pnl")),
        "meta_data": {
            "pnl": _num(meta.get("pnl")),
            "order_size": _num(meta.get("order_size")),
            "order_type": meta.get("order_type"),
        },
        "created_at": o.get("created_at"),
        "updated_at": o.get("updated_at"),
        "product": _prod(o),
        "spot_price": _spot_for(o, spots),
    }


def _enrich_fill(f: dict) -> dict:
    """Trade fill → the fields the Fills table reads."""
    meta = f.get("meta_data") or {}
    return {
        "id": f.get("id") or f.get("fill_id"),
        "order_id": f.get("order_id"),
        "product_symbol": f.get("product_symbol"),
        "side": f.get("side"),
        "size": _num(f.get("size")),
        "price": _num(f.get("price")),
        "notional": _num(f.get("notional")),
        "fill_type": f.get("fill_type"),
        "role": f.get("role"),  # maker / taker
        "commission": _num(f.get("commission")),
        "meta_data": {
            "order_size": _num(meta.get("order_size")),
            "order_type": meta.get("order_type"),
        },
        "created_at": f.get("created_at"),
        "product": _prod(f),
    }


def _enrich_position(p: dict, spots: dict) -> dict:
    """Open position → the fields the Positions + Risk & Margin tables read."""
    sz = _num(p.get("size")) or 0
    return {
        "product_symbol": p.get("product_symbol") or p.get("symbol"),
        "product_id": p.get("product_id"),
        "size": sz,  # signed (negative = short)
        "side": "long" if sz > 0 else "short",
        "entry_price": _num(p.get("entry_price")),
        "mark_price": _num(p.get("mark_price")),
        "margin": _num(p.get("margin")),
        "liquidation_price": _num(p.get("liquidation_price")),
        "bankruptcy_price": _num(p.get("bankruptcy_price")),
        "unrealized_pnl": _num(p.get("unrealized_pnl") or p.get("unrealised_pnl")),
        "realized_cashflow": _num(p.get("realized_cashflow") or p.get("cashflow")),
        "adl_level": p.get("adl_level"),
        "product": _prod(p),
        "spot_price": _spot_for(p, spots),
    }


async def _fetch_account_live_view(acc: dict) -> dict:
    """Full live Delta view for one account: orders, order history, fills and
    per-position risk — enriched with the exact raw fields the Delta-style
    tables render. Each section is best-effort so one failure doesn't blank the
    rest. One Delta client + one spot lookup shared across all sections."""
    client = DeltaClient(
        api_key=acc["api_key"],
        api_secret=acc["api_secret"],
        environment=acc.get("environment", "demo"),
    )
    orders: list = []
    history: list = []
    fills: list = []
    risk: list = []
    try:
        spots = await _spot_map(client)

        # Resting orders — plain limits rest in "open", stop/SL/TP in "pending".
        try:
            seen_ids = set()
            raw_orders = []
            for st in ("open", "pending"):
                try:
                    for o in await client.get_open_orders(state=st):
                        oid = o.get("id")
                        if oid in seen_ids:
                            continue
                        seen_ids.add(oid)
                        raw_orders.append(o)
                except Exception as e:
                    logger.warning(f"Failed to fetch {st} orders for {acc.get('name')}: {e}")
            orders = [_enrich_order(o, spots) for o in raw_orders]
            orders.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        except Exception as e:
            logger.warning(f"Open orders failed for {acc.get('name')}: {e}")

        try:
            history = [_enrich_history(o, spots) for o in await client.get_order_history(page_size=50)]
            history.sort(key=lambda x: (x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        except Exception as e:
            logger.warning(f"Order history failed for {acc.get('name')}: {e}")

        try:
            fills = [_enrich_fill(f) for f in await client.get_fills(page_size=50)]
            fills.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        except Exception as e:
            logger.warning(f"Fills failed for {acc.get('name')}: {e}")

        try:
            risk = [_enrich_position(p, spots) for p in await client.get_positions() if (_num(p.get("size")) or 0) != 0]
            risk.sort(key=lambda x: x.get("product_symbol") or "")
        except Exception as e:
            logger.warning(f"Risk failed for {acc.get('name')}: {e}")
    finally:
        await client.close()
    return {"orders": orders, "history": history, "fills": fills, "risk": risk}


@router.get("/{account_id}/live-view")
async def get_account_live_view(account_id: str, user: CurrentUser = Depends(get_current_user)):
    """Full live Delta view (orders / stop orders / fills / history / risk) for
    one owned account. Admins may view any account. Powers the Delta-style tabs
    for master AND follower accounts alike."""
    try:
        acc = owned_account_or_404(account_id, user)
        return await _fetch_account_live_view(acc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching live view for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
