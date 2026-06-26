import logging
from typing import List, Dict
from fastapi import APIRouter, HTTPException
from app.database import db
from app.models.position import PositionResponse
from app.services.delta_client import DeltaClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])

@router.get("", response_model=List[PositionResponse])
async def list_positions():
    """List open positions across all accounts with joined account name, strictly ordered."""
    try:
        res = db.table("positions").select("*, accounts(name)").order("symbol").execute()
        positions = res.data or []
        
        # Also sort by account name as secondary check
        positions.sort(key=lambda x: (x.get("accounts", {}).get("name", ""), x.get("symbol", "")))
        
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
                "created_at": pos.get("created_at")
            })
            
        return formatted
    except Exception as e:
        logger.error(f"Error querying positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sync-status")
async def get_sync_status() -> Dict[str, int]:
    """Retrieve summary of master vs followers position synchronization states."""
    try:
        res = db.table("positions").select("sync_status").execute()
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
async def list_account_positions(account_id: str):
    """List open positions specifically for one account."""
    try:
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
                "created_at": pos.get("created_at")
            })
            
        return formatted
    except Exception as e:
        logger.error(f"Error querying positions for account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync-live")
async def sync_live_positions():
    """Fetch live positions directly from Delta Exchange for all active accounts and update DB."""
    try:
        acc_res = db.table("accounts").select("*").eq("status", "active").execute()
        accounts = acc_res.data or []
        
        sync_results = []
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
                
                db_pos_res = db.table("positions").select("symbol").eq("account_id", acc["id"]).execute()
                db_symbols = {p["symbol"] for p in db_pos_res.data or []}
                
                active_symbols = set()
                
                for pos in live_positions:
                    symbol = pos.get("product_symbol") or pos.get("symbol")
                    if not symbol:
                        continue
                    active_symbols.add(symbol)
                    
                    from app.core.position_monitor import position_monitor
                    await position_monitor.on_position_update(
                        account_id=acc["id"],
                        account_name=acc["name"],
                        position_data=pos
                    )
                
                closed_symbols = db_symbols - active_symbols
                for symbol in closed_symbols:
                    from app.core.position_monitor import position_monitor
                    await position_monitor.on_position_update(
                        account_id=acc["id"],
                        account_name=acc["name"],
                        position_data={
                            "symbol": symbol,
                            "size": 0,
                            "entry_price": 0,
                            "mark_price": 0,
                            "unrealized_pnl": 0
                        }
                    )
                
                sync_results.append({"account_name": acc["name"], "status": "success", "positions_count": len(live_positions)})
            except Exception as e:
                logger.error(f"Failed to sync live positions for account {acc['name']}: {e}")
                sync_results.append({"account_name": acc["name"], "status": "failed", "error": str(e)})
            finally:
                await client.close()
                
        return {"success": True, "results": sync_results}
    except Exception as e:
        logger.error(f"Error in sync_live_positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/master/open-orders")
async def get_master_open_orders():
    """Fetch live open orders directly from Delta Exchange for the Master account."""
    try:
        master_res = db.table("accounts").select("*").eq("is_master", True).execute()
        if not master_res.data:
            return []
            
        master = master_res.data[0]
        client = DeltaClient(
            api_key=master["api_key"],
            api_secret=master["api_secret"],
            environment=master.get("environment", "demo")
        )
        try:
            open_orders = await client.get_open_orders()
            formatted = []
            for order in open_orders:
                formatted.append({
                    "id": order.get("id"),
                    "symbol": order.get("product_symbol"),
                    "side": order.get("side"),
                    "quantity": float(order.get("size", 0)),
                    "limit_price": float(order.get("limit_price")) if order.get("limit_price") else None,
                    "stop_price": float(order.get("stop_price")) if order.get("stop_price") else None,
                    "order_type": order.get("order_type"),
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
