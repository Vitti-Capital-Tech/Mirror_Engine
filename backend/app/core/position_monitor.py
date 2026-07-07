import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from app.database import db
from app.websocket.socket_manager import socket_manager
from app.core.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

# Don't re-raise the same (account, symbol) position-mismatch alert more than once
# within this window — even if it resolves and re-triggers as the master trades.
MISMATCH_COOLDOWN_MIN = 30

class PositionMonitor:
    def __init__(self, db_client, socket_mgr) -> None:
        self.db = db_client
        self.socket_manager = socket_mgr
        self.risk_engine = RiskEngine()
        # Cache of latest positions to avoid redundant DB queries on every message
        # Structure: account_id -> symbol -> position_dict
        self._positions_cache: Dict[str, Dict[str, dict]] = {}

    def make_position_callback(self, account_id: str, account_name: str):
        """Bind account context to the websocket callback."""
        async def callback(position_data: dict):
            await self.on_position_update(account_id, account_name, position_data)
        return callback

    async def start_monitoring(self, accounts_data: List[dict]) -> None:
        """Initialize the positions cache from existing database records."""
        logger.info("Initializing PositionMonitor cache...")
        try:
            res = self.db.table("positions").select("*").execute()
            positions = res.data or []
            for pos in positions:
                acc_id = pos["account_id"]
                symbol = pos["symbol"]
                if acc_id not in self._positions_cache:
                    self._positions_cache[acc_id] = {}
                self._positions_cache[acc_id][symbol] = pos
            logger.info(f"PositionMonitor cache initialized with {len(positions)} positions.")
        except Exception as e:
            logger.error(f"Failed to initialize PositionMonitor cache: {e}")

    async def on_position_update(self, account_id: str, account_name: str, position_data: dict) -> None:
        """
        Callback handler for WebSocket position messages.
        1. Save/update position in Supabase
        2. Compare with master for mismatch check
        3. Emit update via Socket.IO
        """
        try:
            symbol = position_data.get("product_symbol") or position_data.get("symbol")
            if not symbol:
                return

            # Only treat as closed if 'size' is explicitly present in the payload AND is 0.
            # If 'size' is absent (partial WS update), don't infer closure — avoids false deletions.
            size_present = "size" in position_data or "quantity" in position_data
            raw_size = float(position_data.get("size") or position_data.get("quantity") or 0.0)

            # Map side: Delta REST API uses a signed 'size' field (positive=long, negative=short).
            # The WebSocket 'positions' channel may include an explicit 'side' field ('long'/'short').
            # Priority: explicit side field > sign of size value.
            explicit_side = position_data.get("side")
            if explicit_side and str(explicit_side).lower() in ("long", "short", "buy", "sell"):
                side_str = str(explicit_side).lower()
                side = "long" if side_str in ("long", "buy") else "short"
            else:
                side = "long" if raw_size >= 0 else "short"

            # Use absolute quantity for storage
            raw_qty = abs(raw_size)

            entry_price = float(position_data.get("entry_price") or 0.0)
            current_price = float(position_data.get("mark_price") or position_data.get("current_price") or entry_price)
            
            # Options contracts have a multiplier (e.g. 0.001 BTC per contract).
            # Look for 'contract_value' in the API payload. If missing, check symbol format.
            multiplier = float(position_data.get("contract_value") or (0.001 if ("-C-" in symbol or "-P-" in symbol or symbol.startswith("C-") or symbol.startswith("P-")) else 1.0))
            
            # Calculate Unrealized PnL manually to ensure direction and options calculations are correct
            # PnL = (Price Diff) * Quantity * Multiplier
            if side == "long":
                unrealized_pnl = (current_price - entry_price) * raw_qty * multiplier
            else:
                unrealized_pnl = (entry_price - current_price) * raw_qty * multiplier
            
            logger.info(f"Unrealized PnL manual calculation for {symbol}: qty={raw_qty}, multiplier={multiplier}, side={side}, pnl={unrealized_pnl:.4f}")

            realized_pnl = float(position_data.get("realized_pnl") or 0.0)
            
            sl_price = float(position_data.get("stop_loss_price")) if position_data.get("stop_loss_price") else None
            tp_price = float(position_data.get("take_profit_price")) if position_data.get("take_profit_price") else None

            logger.info(f"Position update for {account_name} on {symbol}: qty={raw_qty}, price={current_price}")

            # If qty is 0 AND size was explicitly in the payload, the position is closed.
            if raw_qty == 0 and size_present:
                # Delete position from DB
                self.db.table("positions").delete().eq("account_id", account_id).eq("symbol", symbol).execute()
                if account_id in self._positions_cache and symbol in self._positions_cache[account_id]:
                    self._positions_cache[account_id].pop(symbol)
                
                # Emit position update with qty 0
                await self.socket_manager.emit_position_update({
                    "account_id": account_id,
                    "account_name": account_name,
                    "symbol": symbol,
                    "side": side,
                    "quantity": 0,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": 0,
                    "sync_status": "synced"
                })
                
                # Check if we need to resolve mismatch alerts
                await self._resolve_mismatch_alert(account_id, symbol)
                return

            # Determine if this is a new position (not previously seen in cache)
            now_iso = datetime.utcnow().isoformat() + "Z"
            is_new = account_id not in self._positions_cache or symbol not in self._positions_cache[account_id]

            # Upsert position in Supabase
            db_pos = {
                "account_id": account_id,
                "symbol": symbol,
                "side": side,
                "quantity": raw_qty,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "last_synced_at": now_iso
            }

            # Set created_at only on first insert (new positions) — preserve on updates
            if is_new:
                db_pos["created_at"] = now_iso

            upsert_res = self.db.table("positions").upsert(db_pos, on_conflict="account_id,symbol").execute()
            if upsert_res.data:
                position_record = upsert_res.data[0]
            else:
                position_record = db_pos
                position_record["id"] = f"{account_id}-{symbol}"

            # Update cache
            if account_id not in self._positions_cache:
                self._positions_cache[account_id] = {}
            self._positions_cache[account_id][symbol] = position_record

            # Mismatch check
            sync_status = await self.check_sync_and_alert(account_id, account_name, symbol, raw_qty, entry_price, side)
            position_record["sync_status"] = sync_status

            # Emit update
            await self.socket_manager.emit_position_update({
                "id": position_record.get("id"),
                "account_id": account_id,
                "account_name": account_name,
                "symbol": symbol,
                "side": side,
                "quantity": raw_qty,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sync_status": sync_status,
                "last_synced_at": position_record.get("last_synced_at")
            })

        except Exception as e:
            logger.error(f"Error handling position update: {e}", exc_info=True)

    async def check_sync_and_alert(
        self,
        account_id: str,
        account_name: str,
        symbol: str,
        follower_qty: float,
        follower_price: float,
        side: str
    ) -> str:
        """
        Compare follower's position against the master's position.
        Returns 'synced' or 'out_of_sync'.
        """
        try:
            # 1. Fetch master account
            master_res = self.db.table("accounts").select("id").eq("is_master", True).execute()
            if not master_res.data:
                return "unknown"
            master_id = master_res.data[0]["id"]

            if account_id == master_id:
                return "synced"

            # 2. Fetch master's position for this symbol.
            # If we have NO record of the master's position for this symbol, we
            # cannot compute an expected size (e.g. options positions can't be
            # fetched over REST — Delta's options endpoint 400s — and the WS
            # snapshot may be empty). Return 'unknown' instead of raising a false
            # "expected size = 0" mismatch for a follower that is actually synced.
            master_pos_res = self.db.table("positions").select("*").eq("account_id", master_id).eq("symbol", symbol).execute()
            if not master_pos_res.data:
                return "unknown"
            master_qty = float(master_pos_res.data[0]["quantity"])

            # 3. Fetch follower account settings
            follower_acc_res = self.db.table("accounts").select("*").eq("id", account_id).execute()
            if not follower_acc_res.data:
                return "unknown"
            follower_account = follower_acc_res.data[0]

            # Fetch master balance for expected quantity calculation
            master_balance = 0.0
            try:
                master_balance_res = self.db.table("accounts").select("*").eq("is_master", True).execute()
                if master_balance_res.data:
                    master_balance = float(master_balance_res.data[0].get("allocated_balance") or master_balance_res.data[0].get("available_margin") or master_balance_res.data[0].get("balance") or 0.0)
            except Exception as e:
                logger.error(f"Failed to fetch master balance for expected sync calculation: {e}")
            
            follower_account["master_balance"] = master_balance

            # 4. Calculate expected qty
            expected_qty = self.risk_engine.calculate_follower_quantity(master_qty, follower_price, follower_account)
            if master_qty == 0:
                expected_qty = 0.0

            # 5. Check mismatch threshold (5% difference or minimum 1 lot mismatch)
            diff = abs(follower_qty - expected_qty)
            mismatch_limit = max(1.0, expected_qty * 0.05)

            if diff > mismatch_limit:
                status = "out_of_sync"
                msg = f"Position mismatch for {account_name} on {symbol}: actual size={follower_qty}, expected size={expected_qty} (diff={diff})"

                # Notify BOTH the follower and the master account, but at most
                # ONCE per (account, symbol) within MISMATCH_COOLDOWN_MIN — even
                # if the mismatch resolves and re-triggers repeatedly while the
                # master trades. Time-window based so resolve/re-create churn
                # can't defeat it.
                owner_id = follower_account.get("owner_id")
                targets = [account_id, master_id]
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=MISMATCH_COOLDOWN_MIN)).isoformat()
                recent = (
                    self.db.table("alerts")
                    .select("account_id, metadata, created_at")
                    .eq("type", "position_mismatch")
                    .in_("account_id", targets)
                    .gte("created_at", cutoff).execute()
                )
                have = {
                    (a.get("account_id"), (a.get("metadata") or {}).get("symbol"))
                    for a in (recent.data or [])
                }
                meta = {
                    "symbol": symbol,
                    "follower_qty": follower_qty,
                    "expected_qty": expected_qty,
                    "master_qty": master_qty,
                }
                to_insert = [
                    {"level": "error", "type": "position_mismatch", "account_id": aid,
                     "message": msg, "metadata": meta, "owner_id": owner_id}
                    for aid in targets if (aid, symbol) not in have
                ]
                if to_insert:
                    logger.warning(msg)
                    alert_res = self.db.table("alerts").insert(to_insert).execute()
                    for row in (alert_res.data or []):
                        await self.socket_manager.emit_alert(row)
                
                # Update position sync_status in DB
                self.db.table("positions").update({"sync_status": "out_of_sync"}).eq("account_id", account_id).eq("symbol", symbol).execute()
                return "out_of_sync"
            else:
                await self._resolve_mismatch_alert(account_id, symbol)
                self.db.table("positions").update({"sync_status": "synced"}).eq("account_id", account_id).eq("symbol", symbol).execute()
                return "synced"

        except Exception as e:
            logger.error(f"Error checking position sync: {e}")
            return "unknown"

    async def _resolve_mismatch_alert(self, account_id: str, symbol: str) -> None:
        """Resolve open position mismatch alerts for this symbol on BOTH the
        follower and the master (mismatch alerts are raised on both)."""
        try:
            account_ids = [account_id]
            try:
                mr = self.db.table("accounts").select("id").eq("is_master", True).execute()
                if mr.data:
                    account_ids.append(mr.data[0]["id"])
            except Exception:
                pass

            alerts_res = (
                self.db.table("alerts").select("*")
                .eq("type", "position_mismatch").eq("is_resolved", False)
                .in_("account_id", account_ids).execute()
            )
            for alert in (alerts_res.data or []):
                meta = alert.get("metadata") or {}
                if meta.get("symbol") == symbol:
                    self.db.table("alerts").update({"is_resolved": True}).eq("id", alert["id"]).execute()
                    alert["is_resolved"] = True
                    await self.socket_manager.emit_alert(alert)
                    logger.info(f"Resolved position mismatch alert {alert['id']} for {alert.get('account_id')} on {symbol}")
        except Exception as e:
            logger.error(f"Error resolving mismatch alerts: {e}")


position_monitor = PositionMonitor(db, socket_manager)

