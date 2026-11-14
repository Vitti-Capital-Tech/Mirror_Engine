import logging
import os
from typing import Tuple, Optional
from app.database import db
from app.websocket.socket_manager import socket_manager

logger = logging.getLogger(__name__)

# When a copy's fill is far enough from the master's to be worth saying out loud.
#
# This was 0.0003 (0.03%), which is not a tight limit — it is an UNREACHABLE one.
# Measured on C-BTC-67800-141126: a 103-point premium, tick size 0.1. The
# threshold works out at 0.0309 points, so it is 3.2x SMALLER than the smallest
# move the contract can make. Any fill differing from the master by a single tick
# breached it. The book's own spread on that contract was 3.11%, i.e. 100x the
# "limit" just to cross it.
#
# The result was alerts on economically meaningless events: 2026-11-14 06:00
# fired at 8.74% on a fill that cost $0.75, and an older one reported 33% for a
# single tick on a 0.3 premium (percentages explode as the premium shrinks).
#
# 2% is above the noise of crossing a normal options spread while still catching
# a genuinely bad fill. Env-overridable because the right number depends on what
# is being traded, and hard-coding it is what produced 0.03% in the first place.
SLIPPAGE_ALERT_PCT = float(os.getenv("SLIPPAGE_ALERT_PCT", "0.02"))

# ...and a floor in MONEY. A percentage alone cannot tell a costly fill from a
# cheap one: the same 8.74% was 75 cents. Below this, nobody needs a
# notification, however ugly the percentage looks.
SLIPPAGE_ALERT_MIN_USD = float(os.getenv("SLIPPAGE_ALERT_MIN_USD", "5"))

# Contracts are 0.001 BTC on Delta India's BTC options, so cost in USD is
# points x lots x this. Verified against live UPNL figures, which reproduce
# exactly with it.
_CONTRACT_VALUE = float(os.getenv("CONTRACT_VALUE", "0.001"))

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
            # Deliberately does NOT write `status`. It used to hardcode 'filled',
            # which made this the second writer of a column the executor owns —
            # and a partial fill would have been stamped 'filled' here moments
            # before the executor recorded it as 'partial'.
            db.table("trade_copies").update({
                "execution_price": follower_price,
                "quantity": quantity,
                "slippage_points": points,
                "slippage_pct": pct,
                "execution_time_ms": execution_time_ms,
            }).eq("id", trade_copy_id).execute()
            
            # Check alert threshold (0.03%)
            # BOTH tests have to fail before anyone is told: far enough off the
            # master's price to be suspicious, AND enough money to care about.
            # Either alone produces noise — a percentage flags 75-cent fills on
            # cheap options, a money figure alone would flag a large position
            # that filled perfectly well.
            cost_usd = abs(points) * abs(float(quantity or 0)) * _CONTRACT_VALUE
            if pct > SLIPPAGE_ALERT_PCT and cost_usd >= SLIPPAGE_ALERT_MIN_USD:
                msg = (f"High slippage detected for {account_name} on {symbol} "
                       f"{side.upper()}: {(pct * 100):.2f}% — cost ${cost_usd:.2f} "
                       f"({abs(points):.2f} pts x {abs(float(quantity or 0)):.0f} lots, "
                       f"limit {SLIPPAGE_ALERT_PCT * 100:.2f}% / ${SLIPPAGE_ALERT_MIN_USD:.0f})")
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
