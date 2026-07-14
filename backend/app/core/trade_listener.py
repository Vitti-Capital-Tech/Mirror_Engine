import asyncio
import json
import logging
import time
from typing import Optional
from app.services.delta_client import DeltaClient
from app.models.trade import TradeEvent, TradeSide, TradeType

logger = logging.getLogger(__name__)

# How often to reconcile the master's resting limit orders onto followers, as a
# safety net for any order the WS create event missed (or that was resting while
# the backend was down). Idempotent, so it only ever places what's missing.
RECONCILE_INTERVAL_SEC = 30


class TradeListener:
    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self.client: Optional[DeltaClient] = None
        self.master_account: Optional[dict] = None
        self._running = False
        self._reconcile_task: Optional[asyncio.Task] = None

    async def start(self, master_account: dict) -> None:
        """
        Start the listener for the master account.
        Instantiates DeltaClient and connects to its WebSocket feed.
        """
        self.master_account = master_account
        self._running = True
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

        # Safety net: periodically re-mirror the master's resting limit orders.
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        """
        Stop the listener and close connections.
        """
        self._running = False
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None
        if self.client:
            logger.info("Stopping TradeListener and DeltaClient websocket connection...")
            await self.client.close()
            self.client = None
        self.master_account = None

    async def _reconcile_loop(self) -> None:
        """Every RECONCILE_INTERVAL_SEC, ensure each of the master's resting plain
        limit ENTRY orders is mirrored. The copy engine is idempotent per
        (master_order_id, follower), so this only fills gaps — it never dupes.
        Deliberately scoped to plain limits: bracket SL/TP are placed via the
        position-open path and aren't tracked by master_order_id, so reconciling
        them could double-place."""
        # small initial delay so the WS snapshot/auth settles first
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        while self._running:
            try:
                await self._reconcile_orders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Order reconcile pass failed: %s", e)
            try:
                await asyncio.sleep(RECONCILE_INTERVAL_SEC)
            except asyncio.CancelledError:
                break

    async def _reconcile_orders(self) -> None:
        if not self.client or not self.master_account:
            return
        try:
            orders = await self.client.get_open_orders(state="open")
        except Exception as e:
            logger.warning("Reconcile: could not fetch master open orders: %s", e)
            return
        pushed = 0
        for o in orders or []:
            order_type = o.get("order_type") or ""
            is_stop = bool(o.get("stop_order_type") or o.get("stop_price"))
            is_bracket = bool(o.get("bracket_order")) or str((o.get("meta_data") or {}).get("order_source") or "").startswith("positions_TP_SL")
            # Resting limit orders (entries AND reduce-only closes). Brackets/stops
            # are excluded: they're placed via the position-open path and aren't
            # tracked by master_order_id, so reconciling them could double-place.
            # Plain limits are safe — the copy engine's per-order idempotency
            # guard means this only fills genuine gaps.
            if order_type != "limit_order" or is_stop or is_bracket:
                continue
            if (o.get("state") or "").lower() != "open":
                continue
            await self._push_order_event(o, "place")
            pushed += 1
        if pushed:
            logger.info("Reconcile: re-mirrored %d resting limit order(s) for master %s", pushed, self.master_account.get("name"))

    async def on_order_fill(self, order: dict) -> None:
        """
        Master order lifecycle handler. Routes each event:
          - market-order fill        -> copy as a market order (position/close)
          - resting limit/stop placed -> mirror as a resting order on followers
          - order cancelled           -> cancel the mirrored follower orders
        Limit/stop *fills* are NOT copied again — their mirrored resting orders
        fill on their own (avoids double fills).
        """
        try:
            state = (order.get("state") or "").lower()
            reason = order.get("reason")
            action = (order.get("action") or "").lower()
            order_type = order.get("order_type", "") or ""
            is_stop = bool(order.get("stop_order_type") or order.get("stop_price"))

            logger.info(
                "Master order update: %s %s type=%s state=%s reason=%s action=%s",
                order.get("side"), order.get("product_symbol"),
                order_type, state, reason or "-", action or "-",
            )

            # ---- 1. Fills ----
            if reason == "fill" or state == "filled":
                # Only market (non-stop) fills are copied as market orders.
                # Limit and stop orders were mirrored as resting orders that fill
                # on their own, so we skip copying their fills.
                if order_type == "market_order" and not is_stop:
                    await self._push_fill_event(order)
                else:
                    logger.info("Skipping fill copy for resting %s (mirrored separately)", order_type)
                return

            # ---- 2. Cancellation ----
            if state in ("cancelled", "canceled") or (action == "delete" and reason != "fill"):
                await self._push_order_event(order, "cancel")
                return

            # ---- 3. Resting order present (limit / stop) in open/pending ----
            # Mirror it. Deliberately NOT gated on `action`: Delta's orders
            # channel doesn't reliably send create/update, which was silently
            # dropping plain limit ENTRY orders (only bracket SL/TP, placed via
            # the position-open path, were reaching followers). The copy engine
            # is idempotent per (master_order_id, follower), so repeated open/
            # pending updates for the same order don't place duplicates.
            if state in ("open", "pending"):
                if order_type == "limit_order" or is_stop:
                    await self._push_order_event(order, "place")
                return

        except Exception as e:
            logger.error(f"Error handling master order event: {e}", exc_info=True)

    async def _push_fill_event(self, order: dict) -> None:
        """Push a filled market order to Redis for the copy engine."""
        order_id = str(order.get("id"))
        symbol = order.get("product_symbol")
        side_str = order.get("side", "").lower()
        size = float(order.get("filled_size") or order.get("size") or 0)
        avg_price = float(
            order.get("average_fill_price")
            or order.get("avg_fill_price")
            or order.get("limit_price")
            or 0.0
        )
        if not order_id or not symbol or not side_str or size <= 0:
            logger.error(f"Missing crucial fields in order fill payload: {order}")
            return

        side = TradeSide.buy if side_str == "buy" else TradeSide.sell
        reduce_only = order.get("reduce_only", False)
        close_on_trigger = order.get("close_on_trigger", False)
        trade_type = TradeType.exit if (reduce_only or close_on_trigger) else TradeType.entry

        trade_event = TradeEvent(
            master_trade_id=order_id,
            symbol=symbol,
            side=side,
            quantity=size,
            entry_price=avg_price,
            trade_type=trade_type,
            raw_payload=order,
        )
        logger.info(f"Pushing TradeEvent to Redis queue: {trade_event.master_trade_id}")
        payload = trade_event.dict()
        payload["owner_id"] = (self.master_account or {}).get("owner_id")
        payload["ts"] = time.time()  # detection time, for latency measurement
        await self.redis.lpush("trade_events", json.dumps(payload))

    async def _push_order_event(self, order: dict, action: str) -> None:
        """Push a resting-order place/cancel event to Redis for the copy engine."""
        payload = {
            "action": action,  # 'place' or 'cancel'
            "master_order_id": str(order.get("id")),
            "symbol": order.get("product_symbol"),
            "product_id": order.get("product_id"),
            "side": (order.get("side") or "").lower(),
            "size": float(order.get("size") or 0),
            "order_type": order.get("order_type") or "limit_order",
            "limit_price": order.get("limit_price"),
            "stop_price": order.get("stop_price"),
            "stop_order_type": order.get("stop_order_type"),
            "stop_trigger_method": order.get("stop_trigger_method"),
            "reduce_only": bool(order.get("reduce_only")),
            # A bracket order is an SL/TP attached to a position (set via the
            # position TP/SL UI). These need Delta's bracket endpoint, not /v2/orders.
            "is_bracket": bool(order.get("bracket_order")) or str((order.get("meta_data") or {}).get("order_source") or "").startswith("positions_TP_SL"),
            # stop_update / action 'update' => the master EDITED an existing SL/TP.
            "is_update": order.get("reason") == "stop_update" or order.get("action") == "update",
            "owner_id": (self.master_account or {}).get("owner_id"),
            "ts": time.time(),  # detection time, for latency measurement
        }
        logger.info(f"Pushing OrderEvent ({action}) to Redis: {payload['master_order_id']} {payload['symbol']}")
        await self.redis.lpush("order_events", json.dumps(payload))

    async def on_position_update(self, position_data: dict) -> None:
        logger.info(f"Master position update received: {position_data}")
        if self.master_account:
            from app.core.position_monitor import position_monitor
            await position_monitor.on_position_update(
                self.master_account["id"],
                self.master_account["name"],
                position_data
            )


trade_listener = TradeListener()


class ListenerManager:
    """Runs one TradeListener per user's master account, so each user's master
    fills copy only into that user's followers (true multi-tenant trading)."""

    def __init__(self) -> None:
        self._listeners: dict = {}  # master_account_id -> TradeListener
        self.redis = None

    async def start_master(self, master_account: dict) -> None:
        aid = master_account["id"]
        await self.stop_master(aid)  # replace any existing
        tl = TradeListener(self.redis)
        try:
            await tl.start(master_account)
            self._listeners[aid] = tl
            logger.info("Listener started for master %s (owner %s)", master_account.get("name"), master_account.get("owner_id"))
        except Exception as e:
            logger.error("Failed to start listener for master %s: %s", master_account.get("name"), e)

    async def stop_master(self, master_account_id: str) -> None:
        tl = self._listeners.pop(master_account_id, None)
        if tl:
            try:
                await tl.stop()
            except Exception as e:
                logger.warning("Error stopping listener %s: %s", master_account_id, e)

    async def stop_all(self) -> None:
        for aid in list(self._listeners.keys()):
            await self.stop_master(aid)

    def is_running(self, master_account_id: str) -> bool:
        return master_account_id in self._listeners

    @property
    def active_count(self) -> int:
        return len(self._listeners)


listener_manager = ListenerManager()

