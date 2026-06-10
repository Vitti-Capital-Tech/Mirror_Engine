import logging
from typing import Tuple, Optional
from app.database import db
from app.websocket.socket_manager import socket_manager

logger = logging.getLogger(__name__)

SLIPPAGE_ALERT_PCT = 0.0003  # 0.03%

class SlippageTracker:
    def calculate(self, master_price: float, follower_price: float, side: str) -> Tuple[float, float]:
        """
        Calculate slippage points and percentage.
        For buy: slippage = follower_price - master_price (positive is bad, means higher price)
        For sell: slippage = master_price - follower_price (positive is bad, means lower price)
        
        Returns (slippage_points, slippage_pct)
        """
        if master_price <= 0 or follower_price <= 0:
            return 0.0, 0.0
            
        if side.lower() == "buy":
            points = follower_price - master_price
        else:
            points = master_price - follower_price
            
        pct = points / master_price
        return points, pct

    async def record_and_alert(
        self,
        trade_copy_id: str,
        account_id: str,
        account_name: str,
        symbol: str,
        side: str,
        master_price: float,
        follower_price: float,
        quantity: float,
        execution_time_ms: int
    ) -> Tuple[float, float]:
        """
        Calculate slippage, update trade_copies table in Supabase, and trigger an alert if it exceeds threshold.
        """
        points, pct = self.calculate(master_price, follower_price, side)
        
        try:
            # Update trade_copies table
            db.table("trade_copies").update({
                "execution_price": follower_price,
                "quantity": quantity,
                "slippage_points": points,
                "slippage_pct": pct,
                "execution_time_ms": execution_time_ms,
                "status": "filled"
            }).eq("id", trade_copy_id).execute()
            
            # Check alert threshold (0.03%)
            if pct > SLIPPAGE_ALERT_PCT:
                msg = f"High slippage detected for {account_name} on {symbol} {side.upper()}: {(pct * 100):.4f}% (limit: {SLIPPAGE_ALERT_PCT * 100}%)"
                logger.warning(msg)
                
                # Insert alert into Supabase
                alert_data = {
                    "level": "warning",
                    "type": "high_slippage",
                    "account_id": account_id,
                    "trade_copy_id": trade_copy_id,
                    "message": msg,
                    "metadata": {
                        "master_price": master_price,
                        "follower_price": follower_price,
                        "slippage_pct": pct,
                        "symbol": symbol,
                        "quantity": quantity
                    }
                }
                alert_result = db.table("alerts").insert(alert_data).execute()
                
                # Emit alert via Socket.IO
                if alert_result.data:
                    await socket_manager.emit_alert(alert_result.data[0])
                    
        except Exception as e:
            logger.error(f"Failed to record slippage and check alert: {e}")
            
        return points, pct

slippage_tracker = SlippageTracker()
