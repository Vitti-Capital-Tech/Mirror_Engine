import json
import logging
from typing import Optional
from app.services.delta_client import DeltaClient
from app.models.trade import TradeEvent, TradeSide, TradeType

logger = logging.getLogger(__name__)

class TradeListener:
    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self.client: Optional[DeltaClient] = None
        self.master_account: Optional[dict] = None

    async def start(self, master_account: dict) -> None:
        """
        Start the listener for the master account.
        Instantiates DeltaClient and connects to its WebSocket feed.
        """
        self.master_account = master_account
        logger.info(f"Starting TradeListener for master account: {master_account['name']}")
        
        self.client = DeltaClient(
            api_key=master_account["api_key"],
            api_secret=master_account["api_secret"],
            environment=master_account.get("environment", "demo")
        )
        
        # Connect websocket with on_fill and on_position callbacks
        await self.client.connect_websocket(
            on_fill_callback=self.on_order_fill,
            on_position_callback=self.on_position_update
        )

    async def stop(self) -> None:
        """
        Stop the listener and close connections.
        """
        if self.client:
            logger.info("Stopping TradeListener and DeltaClient websocket connection...")
            await self.client.close()
            self.client = None
        self.master_account = None

    async def on_order_fill(self, order: dict) -> None:
        """
        Callback triggered when a master order state is 'filled'.
        Parses the order and pushes a TradeEvent to Redis.
        """
        try:
            logger.info(f"Master order update received: {order}")
            
            # Double check if it is filled
            state = order.get("state")
            if state != "filled":
                logger.debug(f"Ignoring order with state {state}")
                return

            # Extract fields
            order_id = str(order.get("id"))
            symbol = order.get("product_symbol")
            side_str = order.get("side", "").lower()
            size = float(order.get("size", 0))
            # Average fill price (or fallback to limit price)
            avg_price = float(order.get("avg_fill_price") or order.get("limit_price") or 0.0)

            if not order_id or not symbol or not side_str or size <= 0:
                logger.error(f"Missing crucial fields in order fill payload: {order}")
                return

            # Map side
            side = TradeSide.buy if side_str == "buy" else TradeSide.sell

            # Map trade type
            order_type = order.get("order_type", "")
            reduce_only = order.get("reduce_only", False)
            close_on_trigger = order.get("close_on_trigger", False)

            trade_type = TradeType.entry
            if reduce_only or close_on_trigger:
                trade_type = TradeType.exit
            if "stop" in order_type.lower():
                trade_type = TradeType.sl

            # Create TradeEvent
            trade_event = TradeEvent(
                master_trade_id=order_id,
                symbol=symbol,
                side=side,
                quantity=size,
                entry_price=avg_price,
                trade_type=trade_type,
                raw_payload=order
            )

            # Push to Redis
            logger.info(f"Pushing TradeEvent to Redis queue: {trade_event.master_trade_id}")
            event_data = trade_event.dict()
            await self.redis.lpush("trade_events", json.dumps(event_data))

        except Exception as e:
            logger.error(f"Error handling master order fill callback: {e}", exc_info=True)

    async def on_position_update(self, position_data: dict) -> None:
        logger.debug(f"Master position update received: {position_data}")


trade_listener = TradeListener()

