import logging
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks, status, Depends
from app.database import db
from app.models.account import AccountCreate, AccountUpdate, AccountResponse, AccountTestResult
from app.core.connection_manager import connection_manager
from app.core.trade_listener import listener_manager
from app.core.position_monitor import position_monitor
from app.core.auth import get_current_user, CurrentUser, scope_owned, owned_account_or_404
from app.services.delta_client import DeltaClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

async def start_account_ws(account: dict) -> None:
    """Connect an account's WS feed based on its role.

    Masters run a dedicated per-user TradeListener (via the ListenerManager) so
    each user's fills copy only to that user's followers. Followers get a plain
    connection for position monitoring.
    """
    if account.get("status") != "active":
        return
    try:
        if account.get("is_master"):
            await listener_manager.start_master(account)
        else:
            await connection_manager.connect_account(
                account,
                on_position=position_monitor.make_position_callback(account["id"], account["name"])
            )
    except Exception as e:
        logger.error(f"Failed to start WebSocket for account {account.get('name')}: {e}")

async def stop_account_ws(account: dict) -> None:
    """Tear down an account's live feed based on its role."""
    try:
        if account.get("is_master"):
            await listener_manager.stop_master(account["id"])
        else:
            await connection_manager.disconnect_account(account["id"])
    except Exception as e:
        logger.warning(f"Failed to stop WebSocket for account {account.get('name')}: {e}")

@router.get("", response_model=List[AccountResponse])
async def list_accounts(user: CurrentUser = Depends(get_current_user)):
    """List the caller's accounts (all accounts for admins)."""
    try:
        res = scope_owned(db.table("accounts").select("*"), user).order("created_at").execute()
        accounts = res.data or []
        
        # Mask secrets
        for acc in accounts:
            if acc.get("api_key"):
                key = acc["api_key"]
                acc["api_key"] = f"...{key[-4:]}" if len(key) >= 4 else "..."
            acc["api_secret"] = "******"
            
        return accounts
    except Exception as e:
        logger.error(f"Error listing accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(account_data: AccountCreate, user: CurrentUser = Depends(get_current_user)):
    """Create a new account owned by the caller, and connect WS if active."""
    try:
        # One master per owner
        if account_data.is_master:
            existing_master = db.table("accounts").select("id").eq("is_master", True).eq("owner_id", user.id).execute()
            if existing_master.data:
                raise HTTPException(status_code=400, detail="You already have a master account. Only one master per user is supported.")

        # Save to DB (stamped with owner)
        data = account_data.model_dump()
        data["owner_id"] = user.id
        res = db.table("accounts").insert(data).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Failed to create account in database.")
        
        new_account = res.data[0]
        
        # Start WebSocket if status is active
        if new_account.get("status") == "active":
            await start_account_ws(new_account)
            
        resp_acc = dict(new_account)
        resp_acc["api_key"] = f"...{resp_acc['api_key'][-4:]}" if len(resp_acc['api_key']) >= 4 else "..."
        resp_acc["api_secret"] = "******"
        return resp_acc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{id}", response_model=AccountResponse)
async def get_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Get a single account by ID (must be owned by the caller)."""
    acc = owned_account_or_404(id, user)
    acc["api_key"] = f"...{acc['api_key'][-4:]}" if len(acc['api_key']) >= 4 else "..."
    acc["api_secret"] = "******"
    return acc

@router.put("/{id}", response_model=AccountResponse)
async def update_account(id: str, account_data: AccountUpdate, user: CurrentUser = Depends(get_current_user)):
    """Update account settings, handle WS reconnect if needed."""
    try:
        existing = owned_account_or_404(id, user)

        # One master per owner
        if account_data.is_master:
            existing_master = db.table("accounts").select("id").eq("is_master", True).eq("owner_id", existing["owner_id"]).neq("id", id).execute()
            if existing_master.data:
                raise HTTPException(status_code=400, detail="You already have a master account.")

        update_data = account_data.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update.")
            
        update_res = db.table("accounts").update(update_data).eq("id", id).execute()
        if not update_res.data:
            raise HTTPException(status_code=500, detail="Failed to update account in database.")
            
        updated = update_res.data[0]
        
        # Disconnect old feed (routes master->listener, follower->connection_manager)
        await stop_account_ws(updated)

        # Reconnect if active
        if updated.get("status") == "active":
            await start_account_ws(updated)
            
        resp_acc = dict(updated)
        resp_acc["api_key"] = f"...{resp_acc['api_key'][-4:]}" if len(resp_acc['api_key']) >= 4 else "..."
        resp_acc["api_secret"] = "******"
        return resp_acc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{id}")
async def delete_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Delete account and disconnect its WS."""
    try:
        acc = owned_account_or_404(id, user)
        await stop_account_ws(acc)

        res = db.table("accounts").delete().eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Account not found.")
            
        return {"success": True, "message": "Account deleted successfully."}
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/pause", response_model=AccountResponse)
async def pause_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Pause copying and disconnect WS."""
    try:
        owned_account_or_404(id, user)
        res = db.table("accounts").update({"status": "paused"}).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Account not found.")

        await stop_account_ws(res.data[0])

        acc = res.data[0]
        acc["api_key"] = f"...{acc['api_key'][-4:]}" if len(acc['api_key']) >= 4 else "..."
        acc["api_secret"] = "******"
        return acc
    except Exception as e:
        logger.error(f"Error pausing account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/resume", response_model=AccountResponse)
async def resume_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Resume copying and reconnect WS."""
    try:
        owned_account_or_404(id, user)
        res = db.table("accounts").update({"status": "active"}).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Account not found.")
            
        acc = res.data[0]
        await start_account_ws(acc)
        
        acc["api_key"] = f"...{acc['api_key'][-4:]}" if len(acc['api_key']) >= 4 else "..."
        acc["api_secret"] = "******"
        return acc
    except Exception as e:
        logger.error(f"Error resuming account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/reset", response_model=AccountResponse)
async def reset_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Reset consecutive failures to 0 and set status to active."""
    try:
        owned_account_or_404(id, user)
        res = db.table("accounts").update({"consecutive_failures": 0, "status": "active"}).eq("id", id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Account not found.")
            
        acc = res.data[0]
        await start_account_ws(acc)
        
        acc["api_key"] = f"...{acc['api_key'][-4:]}" if len(acc['api_key']) >= 4 else "..."
        acc["api_secret"] = "******"
        return acc
    except Exception as e:
        logger.error(f"Error resetting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/promote", response_model=AccountResponse)
async def promote_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Promote a follower to master. Demotes the current master to follower.

    Only one master is ever allowed, so this is an atomic swap:
      - current master  -> follower
      - target account   -> master
    WebSocket feeds are rewired: the new master listens for fills, the demoted
    account switches to position monitoring.
    """
    try:
        target = owned_account_or_404(id, user)

        if target.get("is_master"):
            raise HTTPException(status_code=400, detail="Account is already the master.")

        # Find the current master for THIS owner (if any)
        master_res = db.table("accounts").select("*").eq("is_master", True).eq("owner_id", target["owner_id"]).execute()
        old_master = master_res.data[0] if master_res.data else None

        # Tear down both feeds: old master's per-user listener + target's follower feed
        if old_master:
            await listener_manager.stop_master(old_master["id"])
        await connection_manager.disconnect_account(id)

        # Swap roles in the DB
        if old_master:
            db.table("accounts").update({"is_master": False}).eq("id", old_master["id"]).execute()
        upd = db.table("accounts").update({"is_master": True}).eq("id", id).execute()
        if not upd.data:
            raise HTTPException(status_code=500, detail="Failed to promote account.")
        new_master = upd.data[0]

        # Rewire WebSocket feeds for the new roles
        await start_account_ws(new_master)  # master -> on_fill callback
        if old_master:
            demoted = {**old_master, "is_master": False}
            await start_account_ws(demoted)  # follower -> position callback

        resp_acc = dict(new_master)
        resp_acc["api_key"] = f"...{resp_acc['api_key'][-4:]}" if len(resp_acc['api_key']) >= 4 else "..."
        resp_acc["api_secret"] = "******"
        return resp_acc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error promoting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{id}/test", response_model=AccountTestResult)
async def test_account(id: str, user: CurrentUser = Depends(get_current_user)):
    """Instantiate DeltaClient, fetch wallet balances, and update account balance in DB."""
    try:
        account = owned_account_or_404(id, user)
        
        client = DeltaClient(
            api_key=account["api_key"],
            api_secret=account["api_secret"],
            environment=account.get("environment", "demo")
        )
        
        try:
            wallet_data = await client.get_wallet()
            
            balance_val = 0.0
            available_margin = 0.0
            
            if isinstance(wallet_data, list):
                balances = wallet_data
            else:
                balances = wallet_data.get("result", []) or wallet_data.get("balances", [])
                
            if balances:
                usdt_balance = next((b for b in balances if b.get("asset") == "USDT" or b.get("asset_symbol") == "USDT"), None)
                if not usdt_balance and len(balances) > 0:
                    usdt_balance = balances[0]
                if usdt_balance:
                    balance_val = float(usdt_balance.get("balance") or usdt_balance.get("wallet_balance") or 0.0)
                    available_margin = float(usdt_balance.get("available_margin") or usdt_balance.get("margin_balance") or balance_val)
            
            db.table("accounts").update({
                "balance": balance_val,
                "available_margin": available_margin
            }).eq("id", id).execute()
            
            return {
                "success": True,
                "message": f"Connection successful. Balance: {balance_val} USDT.",
                "balance": balance_val
            }
        except Exception as e:
            logger.error(f"API call failed during test connection for {account['name']}: {e}")
            return {
                "success": False,
                "message": f"API connection failed: {e}",
                "balance": None
            }
        finally:
            await client.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))
