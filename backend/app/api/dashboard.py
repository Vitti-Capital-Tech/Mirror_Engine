import logging
import time
from datetime import datetime, date
from fastapi import APIRouter, HTTPException
from app.database import db
from app.config import settings
from app.models.position import DashboardStats, SystemStatus
from app.core.connection_manager import connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

START_TIME = time.time()

@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats():
    """Retrieve aggregated dashboard stats."""
    try:
        # 1. Query all accounts
        acc_res = db.table("accounts").select("*").execute()
        accounts = acc_res.data or []
        
        total_accounts = len(accounts)
        active_accounts = sum(1 for a in accounts if a["status"] == "active")
        paused_accounts = sum(1 for a in accounts if a["status"] == "paused")
        error_accounts = sum(1 for a in accounts if a["status"] in ("error", "circuit_break"))
        
        master_acc = next((a for a in accounts if a.get("is_master")), None)
        master_account_name = master_acc["name"] if master_acc else None
        
        # Today's PnL is the sum of today_pnl of all follower accounts
        total_pnl = sum(float(a.get("today_pnl") or 0.0) for a in accounts if not a.get("is_master"))
        
        # 2. Query today's trade copies
        today_iso = date.today().isoformat()
        copies_res = db.table("trade_copies").select("status, slippage_pct").gte("created_at", today_iso).execute()
        copies = copies_res.data or []
        
        total_copies_today = len(copies)
        successful_copies = sum(1 for c in copies if c["status"] == "filled")
        failed_copies = sum(1 for c in copies if c["status"] == "failed")
        
        success_rate_pct = (successful_copies / total_copies_today * 100) if total_copies_today > 0 else 100.0
        
        slippages = [
            float(c["slippage_pct"]) 
            for c in copies 
            if c["status"] == "filled" and c.get("slippage_pct") is not None
        ]
        avg_slippage_pct = (sum(slippages) / len(slippages)) if slippages else 0.0
        max_slippage_pct = max(slippages) if slippages else 0.0
        
        # 3. Active alerts count
        alerts_res = db.table("alerts").select("id", count="exact").eq("is_resolved", False).execute()
        active_alerts_count = alerts_res.count or len(db.table("alerts").select("id").eq("is_resolved", False).execute().data or [])
        
        # 4. WebSocket connections
        ws_conn_count = len(connection_manager.connected_account_ids)
        
        # Total parent trades today
        trades_res = db.table("trades").select("id").gte("created_at", today_iso).execute()
        total_trades_today = len(trades_res.data or [])
        
        return {
            "total_accounts": total_accounts,
            "active_accounts": active_accounts,
            "paused_accounts": paused_accounts,
            "error_accounts": error_accounts,
            "master_account_name": master_account_name,
            "total_trades_today": total_trades_today,
            "successful_copies": successful_copies,
            "failed_copies": failed_copies,
            "success_rate_pct": round(success_rate_pct, 2),
            "avg_slippage_pct": round(avg_slippage_pct, 6),
            "max_slippage_pct": round(max_slippage_pct, 6),
            "total_pnl": round(total_pnl, 2),
            "active_alerts_count": active_alerts_count,
            "websocket_connections": ws_conn_count
        }
    except Exception as e:
        logger.error(f"Error compiling dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/system", response_model=SystemStatus)
async def get_system_status():
    """Retrieve detailed system components connectivity and health status."""
    redis_ok = False
    supabase_ok = False
    master_ws_connected = False
    
    # 1. Ping Supabase
    try:
        db.table("accounts").select("id").limit(1).execute()
        supabase_ok = True
    except Exception:
        pass
        
    # 2. Ping Redis
    # Note: We don't import redis_client directly here to avoid circular imports.
    # We can fetch it from app.main or write a lazy import / ping.
    try:
        # Import redis_client from main at runtime
        from app.main import redis_client
        if redis_client:
            await redis_client.ping()
            redis_ok = True
    except Exception as e:
        logger.error(f"Redis ping failed: {e}")
        
    # 3. Check Master WS
    try:
        res = db.table("accounts").select("id").eq("is_master", True).execute()
        if res.data:
            master_id = res.data[0]["id"]
            master_ws_connected = connection_manager.is_connected(master_id)
    except Exception:
        pass
        
    uptime = time.time() - START_TIME
    
    return {
        "redis_ok": redis_ok,
        "supabase_ok": supabase_ok,
        "master_ws_connected": master_ws_connected,
        "total_ws_connections": len(connection_manager.connected_account_ids),
        "environment": settings.DELTA_ENV,
        "uptime_seconds": round(uptime, 2)
    }
