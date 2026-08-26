import logging
from typing import List, Optional
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query, Depends
from app.config import settings
from app.database import db
from app.models.trade import TradeResponse, TradeStatsResponse
from app.core.auth import get_current_user, CurrentUser, scope_owned
from app.core import order_ledger as ledger

logger = logging.getLogger(__name__)

# Read-only Redis handle for the order-ID ledger endpoints below.
_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

router = APIRouter(prefix="/api/trades", tags=["trades"])

@router.get("", response_model=List[TradeResponse])
async def list_trades(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """List the caller's trades with pagination and joined copy details."""
    try:
        # Construct query
        query = scope_owned(db.table("trades").select("*, copies:trade_copies(account_id, accounts(name), status, quantity, requested_quantity, execution_price, slippage_pct, execution_time_ms, failure_reason)"), user)

        if symbol:
            query = query.eq("symbol", symbol.upper())
        if status:
            query = query.eq("status", status.lower())
            
        # Supabase pagination is 0-indexed range (inclusive)
        offset = (page - 1) * limit
        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        trades = res.data or []
        
        # Flatten / format account name from join
        for trade in trades:
            formatted_copies = []
            for copy in trade.get("copies", []):
                accounts_info = copy.get("accounts") or {}
                account_name = accounts_info.get("name") or "Unknown"
                
                formatted_copies.append({
                    "account_id": copy.get("account_id"),
                    "account_name": account_name,
                    "status": copy.get("status"),
                    "quantity": copy.get("quantity"),
                    "requested_quantity": copy.get("requested_quantity"),
                    "execution_price": copy.get("execution_price"),
                    "slippage_pct": copy.get("slippage_pct"),
                    "execution_time_ms": copy.get("execution_time_ms"),
                    "failure_reason": copy.get("failure_reason")
                })
            trade["copies"] = formatted_copies
            
        return trades
    except Exception as e:
        logger.error(f"Error querying trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats", response_model=TradeStatsResponse)
async def get_trade_stats(user: CurrentUser = Depends(get_current_user)):
    """Aggregate statistics for the caller's copy trades."""
    try:
        # Fetch copies for stats
        copies_res = scope_owned(db.table("trade_copies").select("status, slippage_pct, execution_time_ms"), user).execute()
        copies = copies_res.data or []
        
        total_copies = len(copies)
        successful_copies = sum(1 for c in copies if c["status"] == "filled")
        partial_copies = sum(1 for c in copies if c["status"] == "partial")
        failed_copies = sum(1 for c in copies if c["status"] == "failed")

        # A partial is NOT a success: the follower executed, but not in proportion
        # to the master. Counting it as one is what let short fills read as a 100%
        # success rate.
        success_rate_pct = (successful_copies / total_copies * 100) if total_copies > 0 else 100.0

        # ...but it DID execute, so its price and latency are real samples and
        # belong in the slippage/latency averages.
        executed = ("filled", "partial")
        slippages = [
            float(c["slippage_pct"])
            for c in copies
            if c["status"] in executed and c.get("slippage_pct") is not None
        ]
        avg_slippage_pct = (sum(slippages) / len(slippages)) if slippages else 0.0
        max_slippage_pct = max(slippages) if slippages else 0.0
        
        # Execution latency
        latencies = [
            int(c["execution_time_ms"])
            for c in copies
            if c["status"] in executed and c.get("execution_time_ms") is not None
        ]
        avg_execution_time_ms = (sum(latencies) / len(latencies)) if latencies else 0.0
        
        # Total parent trades
        trades_res = scope_owned(db.table("trades").select("id"), user).execute()
        total_trades = len(trades_res.data or [])
        
        return {
            "total_trades": total_trades,
            "successful_copies": successful_copies,
            "partial_copies": partial_copies,
            "failed_copies": failed_copies,
            "success_rate_pct": round(success_rate_pct, 2),
            "avg_slippage_pct": round(avg_slippage_pct, 6),
            "max_slippage_pct": round(max_slippage_pct, 6),
            "avg_execution_time_ms": round(avg_execution_time_ms, 2)
        }
    except Exception as e:
        logger.error(f"Error calculating stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --------------------------------------------------------------------------
# Order-ID ledger — "was master order X mirrored to every follower?"
#
# The trades/trade_copies tables only record copies the engine ACTUALLY
# attempted. The ledger additionally records master orders whose copy never
# happened, which is the only way to see a silently-missed exit (e.g. a partial
# exit lost during a backend outage). Read-only, owner-scoped.
# Declared above GET /{id} — both are distinct path shapes, but keeping the
# specific routes first avoids any future shadowing.
# --------------------------------------------------------------------------

def _follower_names(ids) -> dict:
    """{account_id: name} for the given ids (best effort — a missing account
    shouldn't blank out the whole ledger view)."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    try:
        res = db.table("accounts").select("id, name").in_("id", ids).execute()
        return {r["id"]: r.get("name") for r in (res.data or [])}
    except Exception as e:
        logger.warning(f"ledger: could not resolve follower names: {e}")
        return {}


def _decorate(entry: dict, active_followers: dict, names: dict) -> dict:
    """Annotate a ledger entry with follower names and who is MISSING it.
    `names` is resolved once per REQUEST by the caller — decorating a 200-entry
    listing must not mean 200 account lookups."""
    legs = entry.get("legs") or {}
    entry["legs"] = [
        {"follower_id": fid, "follower": names.get(fid, fid), **leg}
        for fid, leg in legs.items()
    ]
    # Missing = an active follower with no leg at all, or one whose leg failed.
    entry["missing_followers"] = [
        {"follower_id": fid, "follower": names.get(fid, fid),
         "reason": (legs.get(fid) or {}).get("reason") or "no copy attempted"}
        for fid in active_followers
        if (legs.get(fid) or {}).get("status") not in ledger.ACCOUNTED_STATUSES
    ]
    return entry


def _active_followers(user: CurrentUser) -> dict:
    try:
        q = db.table("accounts").select("id, name").eq("is_master", False).eq("status", "active")
        res = scope_owned(q, user).execute()
        return {r["id"]: r.get("name") for r in (res.data or [])}
    except Exception as e:
        logger.warning(f"ledger: could not load active followers: {e}")
        return {}


@router.get("/ledger/order/{master_order_id}")
async def ledger_by_order(master_order_id: str, user: CurrentUser = Depends(get_current_user)):
    """Full copy trail for one MASTER order id: what the master did, and what
    each follower did about it (or why nothing happened)."""
    entry = await ledger.get_entry(_redis, master_order_id)
    if not entry:
        raise HTTPException(status_code=404, detail="No ledger entry for that master order id.")
    # Strict tenant check: a master order id is a guessable integer, so a
    # non-admin must own the entry outright. An entry with no recorded owner is
    # admin-only rather than open to everyone.
    if not user.is_admin and entry.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail="No ledger entry for that master order id.")
    active = _active_followers(user)
    names = _follower_names(list((entry.get("legs") or {}).keys()) + list(active.keys()))
    return _decorate(entry, active, names)


@router.get("/ledger/symbol/{symbol}")
async def ledger_by_symbol(
    symbol: str,
    limit: int = Query(50, ge=1, le=200),
    missing_only: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    """Order-ID trail for a symbol, newest first. `missing_only=true` narrows it
    to master orders at least one active follower never received — the fastest
    way to answer "which exit didn't get copied?"."""
    active = _active_followers(user)
    entries = await ledger.recent(_redis, user.id, symbol.upper(), limit=limit)
    seen_ids = [fid for e in entries for fid in (e.get("legs") or {})]
    names = _follower_names(seen_ids + list(active.keys()))
    out = [_decorate(e, active, names) for e in entries]
    if missing_only:
        out = [e for e in out if e["missing_followers"]]
    return {"symbol": symbol.upper(), "count": len(out), "entries": out}


@router.get("/{id}", response_model=TradeResponse)
async def get_trade(id: str, user: CurrentUser = Depends(get_current_user)):
    """Fetch details of a single trade by ID (must be owned)."""
    try:
        res = scope_owned(db.table("trades").select("*, copies:trade_copies(account_id, accounts(name), status, quantity, requested_quantity, execution_price, slippage_pct, execution_time_ms, failure_reason)").eq("id", id), user).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Trade not found.")
            
        trade = res.data[0]
        formatted_copies = []
        for copy in trade.get("copies", []):
            accounts_info = copy.get("accounts") or {}
            account_name = accounts_info.get("name") or "Unknown"
            
            formatted_copies.append({
                "account_id": copy.get("account_id"),
                "account_name": account_name,
                "status": copy.get("status"),
                "execution_price": copy.get("execution_price"),
                "slippage_pct": copy.get("slippage_pct"),
                "execution_time_ms": copy.get("execution_time_ms"),
                "failure_reason": copy.get("failure_reason")
            })
        trade["copies"] = formatted_copies
        return trade
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying trade details: {e}")
        raise HTTPException(status_code=500, detail=str(e))
