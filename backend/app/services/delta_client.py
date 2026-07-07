"""
DeltaClient — fully async client for the Delta Exchange REST + WebSocket APIs.

Authentication uses HMAC-SHA256 signed headers as documented at:
https://docs.delta.exchange/#authentication
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Callable, Awaitable, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.config import settings

logger = logging.getLogger(__name__)

OnFillCallback = Callable[[dict], Awaitable[None]]
OnPositionCallback = Callable[[dict], Awaitable[None]]


class DeltaClient:
    """Async client for Delta Exchange.  One instance per account."""

    def __init__(self, api_key: str, api_secret: str, environment: str = "demo") -> None:
        # Secrets are stored encrypted at rest; decrypt transparently on use.
        # decrypt() is a no-op for legacy plaintext values.
        from app.core.crypto import decrypt
        self.api_key = decrypt(api_key)
        self.api_secret = decrypt(api_secret)
        self.environment = environment
        self.rest_url = (
            settings.DELTA_DEMO_REST_URL if environment == "demo" else settings.DELTA_LIVE_REST_URL
        )
        self.ws_url = (
            settings.DELTA_DEMO_WS_URL if environment == "demo" else settings.DELTA_LIVE_WS_URL
        )
        self._client = httpx.AsyncClient(timeout=10.0)
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._on_fill: Optional[OnFillCallback] = None
        self._on_position: Optional[OnPositionCallback] = None
        self._running = False
        # Decouple socket reading from processing: the read loop drains the
        # socket instantly into these queues; a worker processes them, taking
        # FILLS first so a real trade never waits behind the order-churn flood.
        self._fill_q: asyncio.Queue = asyncio.Queue()
        self._other_q: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._OTHER_Q_MAX = 1000  # cap order-churn backlog to bound memory

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _generate_signature(self, method: str, path: str, body: str = "") -> tuple[str, str]:
        """Return (timestamp, hex-signature) for a request."""
        timestamp = str(int(time.time()))
        message = method + timestamp + path + body
        signature = hmac.new(
            self.api_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    def _get_headers(self, method: str, path: str, body: str = "") -> dict:
        timestamp, signature = self._generate_signature(method, path, body)
        return {
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json",
            "User-Agent": "delta-copy-trader/1.0",
        }

    # ------------------------------------------------------------------
    # REST methods
    # ------------------------------------------------------------------

    async def get_wallet(self) -> dict:
        """Fetch wallet / margin balances."""
        path = "/v2/wallet/balances"
        headers = self._get_headers("GET", path)
        resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def get_positions(self) -> list:
        """Fetch all open positions — both futures (margined) and options."""
        all_positions = []

        # 1. Futures / margined positions
        try:
            path = "/v2/positions/margined"
            headers = self._get_headers("GET", path)
            resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
            if resp.status_code == 200:
                all_positions.extend(resp.json().get("result", []))
        except Exception as e:
            logger.warning(f"Could not fetch margined positions: {e}")

        # 2. Options positions
        try:
            path = "/v2/positions?product_types=put_options,call_options"
            headers = self._get_headers("GET", path)
            resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                opts = data.get("result", []) if isinstance(data, dict) else data
                all_positions.extend(opts)
        except Exception as e:
            logger.warning(f"Could not fetch options positions: {e}")

        return all_positions

    async def get_wallet_transactions(self, start_time_us: Optional[int] = None, page_size: int = 200) -> list:
        """Fetch wallet ledger transactions (used to sum realized PnL for the day)."""
        q = f"?page_size={page_size}"
        if start_time_us:
            q += f"&start_time={start_time_us}"
        path = f"/v2/wallet/transactions{q}"
        headers = self._get_headers("GET", path)
        resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []) if isinstance(data, dict) else (data or [])

    async def place_order(
        self,
        symbol: str,
        side: str,
        size: int,
        order_type: str = "market_order",
        limit_price: Optional[float] = None,
        reduce_only: bool = False,
        stop_price: Optional[float] = None,
        stop_order_type: Optional[str] = None,
        stop_trigger_method: Optional[str] = None,
        time_in_force: Optional[str] = None,
    ) -> dict:
        """Place a market/limit order, optionally a stop (triggered) order.

        reduce_only=True can only reduce/close an existing position (never flips).
        Pass stop_price (+ optional stop_order_type/stop_trigger_method) to place
        a stop / take-profit / bracket-style triggered order.
        time_in_force='fok' = fill-or-kill (fills the whole size or nothing).
        """
        path = "/v2/orders"
        body_dict: dict = {
            "product_symbol": symbol,
            "side": side,
            "size": size,
            "order_type": order_type,
        }
        if time_in_force:
            body_dict["time_in_force"] = time_in_force
        if limit_price is not None and order_type == "limit_order":
            body_dict["limit_price"] = str(limit_price)
        if reduce_only:
            body_dict["reduce_only"] = True
        if stop_price is not None:
            body_dict["stop_price"] = str(stop_price)
            body_dict["stop_order_type"] = stop_order_type or "stop_loss_order"
            body_dict["stop_trigger_method"] = stop_trigger_method or "mark_price"

        body = json.dumps(body_dict)
        headers = self._get_headers("POST", path, body)
        resp = await self._client.post(
            f"{self.rest_url}{path}", headers=headers, content=body
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_order(self, order_id: str, product_id: Optional[int] = None) -> dict:
        """Cancel an order. Delta India expects DELETE /v2/orders with a JSON
        body {id, product_id} — the path form /v2/orders/{id} 404s."""
        path = "/v2/orders"
        body_dict: dict = {"id": int(order_id)}
        if product_id is not None:
            body_dict["product_id"] = int(product_id)
        body = json.dumps(body_dict)
        headers = self._get_headers("DELETE", path, body)
        resp = await self._client.request(
            "DELETE", f"{self.rest_url}{path}", headers=headers, content=body
        )
        resp.raise_for_status()
        return resp.json()

    async def edit_order(
        self,
        order_id: str,
        product_id: int,
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_trigger_method: Optional[str] = None,
    ) -> dict:
        """Edit an existing order's trigger/limit price and trigger method
        (PUT /v2/orders). Used to mirror SL/TP modifications in place — also
        keeps the trigger reference (mark/index) in sync."""
        path = "/v2/orders"
        body_dict: dict = {"id": int(order_id), "product_id": int(product_id)}
        if stop_price is not None:
            body_dict["stop_price"] = str(stop_price)
        if limit_price is not None:
            body_dict["limit_price"] = str(limit_price)
        if stop_trigger_method:
            body_dict["stop_trigger_method"] = stop_trigger_method
        body = json.dumps(body_dict)
        headers = self._get_headers("PUT", path, body)
        resp = await self._client.put(f"{self.rest_url}{path}", headers=headers, content=body)
        resp.raise_for_status()
        return resp.json()

    async def place_bracket(
        self,
        product_id: int,
        stop_loss: Optional[dict] = None,
        take_profit: Optional[dict] = None,
        trigger_method: str = "mark_price",
    ) -> dict:
        """Attach a bracket (stop-loss / take-profit) to an existing position via
        Delta's bracket endpoint. trigger_method is the reference price the stop
        prices are evaluated against (mark_price / spot_price / last_traded_price)
        and MUST match the original order — using the wrong one makes an
        index-priced stop look already-triggered ('immediate execution')."""
        path = "/v2/orders/bracket"
        body_dict: dict = {
            "product_id": int(product_id),
            "bracket_stop_trigger_method": trigger_method,
        }
        if stop_loss:
            body_dict["stop_loss_order"] = stop_loss
        if take_profit:
            body_dict["take_profit_order"] = take_profit
        body = json.dumps(body_dict)
        headers = self._get_headers("POST", path, body)
        resp = await self._client.post(f"{self.rest_url}{path}", headers=headers, content=body)
        resp.raise_for_status()
        return resp.json()

    async def edit_bracket_order(
        self,
        order_id: str,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
    ) -> dict:
        """Modify the stop-loss / take-profit of a bracket order."""
        path = f"/v2/orders/{order_id}/bracket"
        body_dict: dict = {}
        if sl_price is not None:
            body_dict["stop_loss_price"] = str(sl_price)
        if tp_price is not None:
            body_dict["take_profit_price"] = str(tp_price)

        body = json.dumps(body_dict)
        headers = self._get_headers("PUT", path, body)
        resp = await self._client.put(
            f"{self.rest_url}{path}", headers=headers, content=body
        )
        resp.raise_for_status()
        return resp.json()

    async def get_order(self, order_id: str) -> dict:
        """Fetch a single order by ID."""
        path = f"/v2/orders/{order_id}"
        headers = self._get_headers("GET", path)
        resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def get_open_orders(self, state: str = "open") -> list:
        """Fetch active orders in the given state ('open' or 'pending')."""
        path = f"/v2/orders?state={state}"
        headers = self._get_headers("GET", path)
        resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json().get("result", [])

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_websocket(
        self,
        on_fill_callback: OnFillCallback,
        on_position_callback: OnPositionCallback,
    ) -> None:
        """Start the WebSocket loop in the background."""
        self._on_fill = on_fill_callback
        self._on_position = on_position_callback
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop(), name=f"ws-{self.api_key[:8]}")
        self._worker_task = asyncio.create_task(self._ws_worker(), name=f"wsw-{self.api_key[:8]}")

    @staticmethod
    def _is_fill_event(data: dict) -> bool:
        if data.get("type") != "orders":
            return False
        o = data.get("order", data)
        return o.get("reason") == "fill" or (o.get("state") or "").lower() == "filled"

    async def _ws_worker(self) -> None:
        """Process queued WS messages, FILLS first (so trades copy without waiting
        behind the master's resting-order churn)."""
        while self._running:
            try:
                try:
                    data = self._fill_q.get_nowait()
                except asyncio.QueueEmpty:
                    try:
                        data = await asyncio.wait_for(self._other_q.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                await self._handle_ws_message(data)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("WS worker error: %s", exc, exc_info=True)

    async def _ws_loop(self) -> None:
        """Main WebSocket reconnection loop."""
        retry_delay = 5
        while self._running:
            try:
                ws_endpoint = self.ws_url
                logger.info("Connecting to Delta WS: %s", ws_endpoint)

                async with websockets.connect(
                    ws_endpoint,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    retry_delay = 5  # reset on successful connect

                    # Authenticate
                    await self._ws_authenticate(ws)

                    # Subscribe to orders + positions
                    await ws.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "payload": {
                                    "channels": [
                                        {"name": "orders", "symbols": ["all"]},
                                        {"name": "positions", "symbols": ["all"]},
                                    ]
                                },
                            }
                        )
                    )
                    logger.info("WS subscribed to orders + positions for key %s", self.api_key[:8])

                    async for raw_message in ws:
                        # Drain the socket INSTANTLY — never block the reader on
                        # downstream work. Fills go to the priority queue; the
                        # order-churn queue is capped (drop oldest) to bound memory.
                        try:
                            data = json.loads(raw_message)
                        except json.JSONDecodeError:
                            logger.warning("WS received non-JSON message: %s", raw_message[:200])
                            continue
                        if self._is_fill_event(data):
                            self._fill_q.put_nowait(data)
                        else:
                            if self._other_q.qsize() >= self._OTHER_Q_MAX:
                                try:
                                    self._other_q.get_nowait()  # drop oldest churn
                                except asyncio.QueueEmpty:
                                    pass
                            self._other_q.put_nowait(data)

            except (ConnectionClosedOK, ConnectionClosedError) as exc:
                if not self._running:
                    break
                logger.warning("WS closed (%s). Reconnecting in %ds...", exc, retry_delay)
            except Exception as exc:
                if not self._running:
                    break
                logger.error("WS error: %s. Reconnecting in %ds...", exc, retry_delay)

            if self._running:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # exponential back-off up to 60 s

        logger.info("WS loop exited for key %s", self.api_key[:8])

    async def _ws_authenticate(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Send key-auth frame and wait for confirmation."""
        timestamp = str(int(time.time()))
        sig_msg = "GET" + timestamp + "/live"
        sig = hmac.new(
            self.api_secret.encode(), sig_msg.encode(), hashlib.sha256
        ).hexdigest()

        await ws.send(
            json.dumps(
                {
                    "type": "key-auth",
                    "payload": {
                        "api-key": self.api_key,
                        "signature": sig,
                        "timestamp": timestamp,
                    },
                }
            )
        )
        # Read the auth response (first message after connect)
        try:
            auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
            logger.info("WS auth response for %s: %s", self.api_key[:8], auth_resp[:200])
        except asyncio.TimeoutError:
            raise RuntimeError("WS auth response timed out")

    async def _handle_ws_message(self, data: dict) -> None:
        """Route incoming WS messages to registered callbacks."""
        msg_type = data.get("type")

        if msg_type == "orders":
            order = data.get("order", data)
            # Measure how stale this event is by the time we process it: compares
            # Delta's own event timestamp (µs) to now. A high value means the WS
            # read loop is backed up (heavy order flood) or Delta delivered late.
            ts = data.get("timestamp") or order.get("timestamp")
            if ts:
                try:
                    lag = time.time() - float(ts) / 1_000_000.0
                    if lag > 1.0:
                        logger.warning(f"[WSLAG] orders event {lag:.2f}s stale at processing ({order.get('product_symbol')} {order.get('state')}/{order.get('reason')})")
                except Exception:
                    pass
            # Forward every order lifecycle event (create/fill/cancel); the
            # listener decides how to handle each.
            if self._on_fill:
                await self._on_fill(order)

        elif msg_type == "positions":
            position = data.get("position", data)
            if self._on_position:
                await self._on_position(position)

        elif msg_type in ("subscriptions", "info"):
            logger.debug("WS system message: %s", data)

        elif msg_type == "error":
            logger.error("WS server error: %s", data)

    async def disconnect_websocket(self) -> None:
        """Cancel the WS loop task and close the socket."""
        self._running = False
        for _task in (self._ws_task, self._worker_task):
            if _task and not _task.done():
                _task.cancel()
                try:
                    await _task
                except asyncio.CancelledError:
                    pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    async def close(self) -> None:
        """Cleanly shut down the client."""
        await self.disconnect_websocket()
        await self._client.aclose()

    @property
    def ws_connected(self) -> bool:
        return (
            self._ws is not None
            and not self._ws.closed
            and self._ws_task is not None
            and not self._ws_task.done()
        )
