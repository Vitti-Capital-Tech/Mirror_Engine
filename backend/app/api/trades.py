import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from app.database import db
from app.models.trade import TradeResponse, TradeStatsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trades", tags=["trades"])

@router.get("", response_model=List[TradeResponse])
async def list_trades(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    symbol: Optional[str] = None
):
    """List trades with pagination, optional filters, and joined trade copy details."""
    try:
        # Construct query
        query = db.table("trades").select("*, copies:trade_copies(account_id, accounts(name), status, execution_price, slippage_pct, execution_time_ms, failure_reason)")
        
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
async def get_trade_stats():
    """Aggregate statistics for all copy trades."""
    try:
        # Fetch copies for stats
        copies_res = db.table("trade_copies").select("status, slippage_pct, execution_time_ms").execute()
        copies = copies_res.data or []
        
        total_copies = len(copies)
        successful_copies = sum(1 for c in copies if c["status"] == "filled")
        failed_copies = sum(1 for c in copies if c["status"] == "failed")
        
        success_rate_pct = (successful_copies / total_copies * 100) if total_copies > 0 else 100.0
        
        # Slippage calculations
        slippages = [
            float(c["slippage_pct"]) 
            for c in copies 
            if c["status"] == "filled" and c.get("slippage_pct") is not None
        ]
        avg_slippage_pct = (sum(slippages) / len(slippages)) if slippages else 0.0
        max_slippage_pct = max(slippages) if slippages else 0.0
        
        # Execution latency
        latencies = [
            int(c["execution_time_ms"]) 
            for c in copies 
            if c["status"] == "filled" and c.get("execution_time_ms") is not None
        ]
        avg_execution_time_ms = (sum(latencies) / len(latencies)) if latencies else 0.0
        
        # Total parent trades
        trades_res = db.table("trades").select("id").execute()
        total_trades = len(trades_res.data or [])
        
        return {
            "total_trades": total_trades,
            "successful_copies": successful_copies,
            "failed_copies": failed_copies,
            "success_rate_pct": round(success_rate_pct, 2),
            "avg_slippage_pct": round(avg_slippage_pct, 6),
            "max_slippage_pct": round(max_slippage_pct, 6),
            "avg_execution_time_ms": round(avg_execution_time_ms, 2)
        }
    except Exception as e:
        logger.error(f"Error calculating stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{id}", response_model=TradeResponse)
async def get_trade(id: str):
    """Fetch details of a single trade by ID."""
    try:
        res = db.table("trades").select("*, copies:trade_copies(account_id, accounts(name), status, execution_price, slippage_pct, execution_time_ms, failure_reason)").eq("id", id).execute()
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
