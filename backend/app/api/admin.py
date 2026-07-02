"""Admin-only endpoints: cross-tenant visibility over all users and their data."""

import asyncio
import logging
from datetime import date
from fastapi import APIRouter, HTTPException, Depends

from app.database import db
from app.core.auth import require_admin, CurrentUser
from app.core.trade_listener import listener_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
async def admin_overview(user: CurrentUser = Depends(require_admin)):
    """Aggregate view of every tenant: users with their account/master/PnL rollups."""
    try:
        profiles = (db.table("profiles").select("*").order("created_at").execute().data) or []
        accounts = (db.table("accounts").select("*").execute().data) or []

        today_iso = date.today().isoformat()
        copies = (db.table("trade_copies").select("owner_id, status").gte("created_at", today_iso).execute().data) or []

        # Index accounts + copies by owner
        by_owner: dict = {}
        for a in accounts:
            by_owner.setdefault(a.get("owner_id"), []).append(a)
        copies_by_owner: dict = {}
        for c in copies:
            copies_by_owner.setdefault(c.get("owner_id"), []).append(c)

        users = []
        for p in profiles:
            uid = p["id"]
            accs = by_owner.get(uid, [])
            master = next((a for a in accs if a.get("is_master")), None)
            followers = [a for a in accs if not a.get("is_master")]
            ucopies = copies_by_owner.get(uid, [])
            users.append({
                "id": uid,
                "email": p.get("email"),
                "role": p.get("role", "user"),
                "created_at": p.get("created_at"),
                "total_accounts": len(accs),
                "active_accounts": sum(1 for a in accs if a.get("status") == "active"),
                "master_name": master["name"] if master else None,
                "master_live": bool(master) and listener_manager.is_running(master["id"]),
                "follower_count": len(followers),
                "today_pnl": round(sum(float(a.get("today_pnl") or 0) for a in accs), 2),
                "total_balance": round(sum(float(a.get("balance") or 0) for a in accs), 2),
                "copies_today": len(ucopies),
                "copies_filled_today": sum(1 for c in ucopies if c.get("status") == "filled"),
            })

        # Accounts with no matching profile (orphans / pre-auth data)
        known = {p["id"] for p in profiles}
        orphan_accounts = sum(1 for a in accounts if a.get("owner_id") not in known)

        return {
            "totals": {
                "users": len(profiles),
                "admins": sum(1 for p in profiles if p.get("role") == "admin"),
                "accounts": len(accounts),
                "masters": sum(1 for a in accounts if a.get("is_master")),
                "active_listeners": listener_manager.active_count,
                "orphan_accounts": orphan_accounts,
            },
            "users": users,
        }
    except Exception as e:
        logger.error(f"Error building admin overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts")
async def admin_accounts(user: CurrentUser = Depends(require_admin)):
    """Every account across all tenants, with owner email and live status."""
    try:
        profiles = (db.table("profiles").select("id, email").execute().data) or []
        email_by_id = {p["id"]: p.get("email") for p in profiles}
        from app.core.crypto import decrypt
        accounts = (db.table("accounts").select("*").order("created_at").execute().data) or []
        out = []
        for a in accounts:
            key = decrypt(a.get("api_key") or "")
            out.append({
                "id": a["id"],
                "name": a.get("name"),
                "owner_id": a.get("owner_id"),
                "owner_email": email_by_id.get(a.get("owner_id")) or "—",
                "is_master": bool(a.get("is_master")),
                "status": a.get("status"),
                "environment": a.get("environment"),
                "balance": a.get("balance"),
                "allocated_balance": a.get("allocated_balance"),
                "today_pnl": a.get("today_pnl"),
                "api_key_hint": f"...{key[-4:]}" if len(key) >= 4 else "…",
                "live": bool(a.get("is_master")) and listener_manager.is_running(a["id"]),
                "created_at": a.get("created_at"),
            })
        return out
    except Exception as e:
        logger.error(f"Error listing admin accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def admin_positions(user: CurrentUser = Depends(require_admin)):
    """Live positions for every tenant, grouped by user → master + followers.

    Fetches straight from Delta (same source as the trader Positions page) so it
    always matches the exchange."""
    try:
        from app.api.positions import _fetch_account_positions

        profiles = (db.table("profiles").select("id, email").execute().data) or []
        email_by_id = {p["id"]: p.get("email") for p in profiles}
        accounts = (db.table("accounts").select("*").execute().data) or []

        results = await asyncio.gather(
            *[_fetch_account_positions(a) for a in accounts],
            return_exceptions=True,
        )

        by_owner: dict = {}
        for acc, res in zip(accounts, results):
            positions = res if isinstance(res, list) else []
            owner = acc.get("owner_id")
            entry = by_owner.setdefault(owner, {
                "id": owner,
                "email": email_by_id.get(owner) or "—",
                "accounts": [],
            })
            entry["accounts"].append({
                "id": acc["id"],
                "name": acc.get("name"),
                "is_master": bool(acc.get("is_master")),
                "status": acc.get("status"),
                "environment": acc.get("environment"),
                "live": bool(acc.get("is_master")) and listener_manager.is_running(acc["id"]),
                "balance": acc.get("balance"),
                "allocated_balance": acc.get("allocated_balance"),
                "today_pnl": acc.get("today_pnl"),
                "positions": positions,
            })

        users = []
        for e in by_owner.values():
            e["accounts"].sort(key=lambda a: (not a["is_master"], a["name"] or ""))
            e["total_positions"] = sum(len(a["positions"]) for a in e["accounts"])
            e["total_upnl"] = round(sum(
                float(p.get("unrealized_pnl") or 0) for a in e["accounts"] for p in a["positions"]
            ), 2)
            e["today_pnl"] = round(sum(float(a.get("today_pnl") or 0) for a in e["accounts"]), 2)
            users.append(e)
        users.sort(key=lambda u: (u["email"] or "").lower())
        return {"users": users}
    except Exception as e:
        logger.error(f"Error building admin positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def admin_trades(limit: int = 100, user: CurrentUser = Depends(require_admin)):
    """Recent trades across all tenants with owner email and copy roll-up."""
    try:
        profiles = (db.table("profiles").select("id, email").execute().data) or []
        email_by_id = {p["id"]: p.get("email") for p in profiles}
        trades = (db.table("trades")
                  .select("*, copies:trade_copies(status)")
                  .order("created_at", desc=True).limit(limit).execute().data) or []
        out = []
        for t in trades:
            copies = t.get("copies") or []
            out.append({
                "id": t.get("id"),
                "owner_email": email_by_id.get(t.get("owner_id")) or "—",
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "quantity": t.get("quantity"),
                "trade_type": t.get("trade_type"),
                "entry_price": t.get("entry_price"),
                "status": t.get("status"),
                "copies_total": len(copies),
                "copies_filled": sum(1 for c in copies if c.get("status") == "filled"),
                "created_at": t.get("created_at"),
            })
        return out
    except Exception as e:
        logger.error(f"Error listing admin trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts")
async def admin_alerts(limit: int = 150, user: CurrentUser = Depends(require_admin)):
    """Recent alerts across all tenants with owner email and account name."""
    try:
        profiles = (db.table("profiles").select("id, email").execute().data) or []
        email_by_id = {p["id"]: p.get("email") for p in profiles}
        alerts = (db.table("alerts")
                  .select("*, accounts(name)")
                  .order("created_at", desc=True).limit(limit).execute().data) or []
        out = []
        for a in alerts:
            acc = a.get("accounts") or {}
            out.append({
                "id": a.get("id"),
                "owner_email": email_by_id.get(a.get("owner_id")) or "—",
                "account_name": acc.get("name") or "System",
                "level": a.get("level"),
                "type": a.get("type"),
                "message": a.get("message"),
                "is_resolved": a.get("is_resolved", False),
                "created_at": a.get("created_at"),
            })
        return out
    except Exception as e:
        logger.error(f"Error listing admin alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/role")
async def set_user_role(user_id: str, role: str, user: CurrentUser = Depends(require_admin)):
    """Promote/demote a user between 'user' and 'admin'."""
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'admin'")
    res = db.table("profiles").update({"role": role}).eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "id": user_id, "role": role}
