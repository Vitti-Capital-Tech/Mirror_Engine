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
        self.api_key = api_key
        self.api_secret = api_secret
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
        """Fetch all open margined positions."""
        path = "/v2/positions/margined"
        headers = self._get_headers("GET", path)
        resp = await self._client.get(f"{self.rest_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def place_order(
        self,
        symbol: str,
        side: str,
        size: int,
        order_type: str = "market_order",
        limit_price: Optional[float] = None,
    ) -> dict:
        """Place a market or limit order."""
        path = "/v2/orders"
        body_dict: dict = {
            "product_symbol": symbol,
            "side": side,
            "size": size,
            "order_type": order_type,
        }
        if limit_price is not None and order_type == "limit_order":
            body_dict["limit_price"] = str(limit_price)

        body = json.dumps(body_dict)
        headers = self._get_headers("POST", path, body)
        resp = await self._client.post(
            f"{self.rest_url}{path}", headers=headers, content=body
        )
        resp.raise_for_status()
        return resp.json()

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by order ID."""
        path = f"/v2/orders/{order_id}"
        headers = self._get_headers("DELETE", path)
        resp = await self._client.delete(f"{self.rest_url}{path}", headers=headers)
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

    async def _ws_loop(self) -> None:
        """Main WebSocket reconnection loop."""
        retry_delay = 5
        while self._running:
            try:
                ws_endpoint = f"{self.ws_url}/live"
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
                        try:
                            data = json.loads(raw_message)
                            await self._handle_ws_message(data)
                        except json.JSONDecodeError:
                            logger.warning("WS received non-JSON message: %s", raw_message[:200])
                        except Exception as exc:
                            logger.error("WS message handler error: %s", exc, exc_info=True)

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
            state = order.get("state", "")
            if state in ("filled", "closed") and self._on_fill:
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
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
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
