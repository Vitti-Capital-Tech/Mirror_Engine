import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from app.database import db
from app.models.position import AlertResponse
from app.core.auth import get_current_user, CurrentUser, scope_owned

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

@router.get("", response_model=List[AlertResponse])
async def list_alerts(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    level: Optional[str] = None,
    type: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """List the caller's alerts with pagination and optional filters."""
    try:
        query = scope_owned(db.table("alerts").select("*, accounts(name)"), user)

        if level:
            query = query.eq("level", level)
        if type:
            query = query.eq("type", type)
        if is_resolved is not None:
            query = query.eq("is_resolved", is_resolved)
            
        offset = (page - 1) * limit
        res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        alerts = res.data or []
        
        # Flatten account name
        formatted = []
        for alert in alerts:
            acc_info = alert.get("accounts") or {}
            acc_name = acc_info.get("name") or "System"
            
            formatted.append({
                "id": alert.get("id"),
                "level": alert.get("level"),
                "type": alert.get("type"),
                "account_id": alert.get("account_id"),
                "account_name": acc_name,
                "message": alert.get("message"),
                "metadata": alert.get("metadata"),
                "is_resolved": alert.get("is_resolved", False),
                "created_at": alert.get("created_at")
            })
            
        return formatted
    except Exception as e:
        logger.error(f"Error querying alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/resolve", response_model=AlertResponse)
async def resolve_alert(id: str, user: CurrentUser = Depends(get_current_user)):
    """Mark a specific alert as resolved (must be owned)."""
    try:
        chk = scope_owned(db.table("alerts").select("id").eq("id", id), user).execute()
        if not chk.data:
            raise HTTPException(status_code=404, detail="Alert not found.")
        res = db.table("alerts").update({"is_resolved": True}).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Alert not found.")
            
        alert = res.data[0]
        # Resolve account name
        if alert.get("account_id"):
            acc_res = db.table("accounts").select("name").eq("id", alert["account_id"]).execute()
            if acc_res.data:
                alert["account_name"] = acc_res.data[0]["name"]
                
        return alert
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resolving alert {id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/clear")
async def clear_resolved_alerts(user: CurrentUser = Depends(get_current_user)):
    """Clear the caller's resolved alerts."""
    try:
        res = scope_owned(db.table("alerts").delete().eq("is_resolved", True), user).execute()
        deleted_count = len(res.data or [])
        return {"success": True, "message": f"Cleared {deleted_count} resolved alerts."}
    except Exception as e:
        logger.error(f"Error clearing resolved alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
