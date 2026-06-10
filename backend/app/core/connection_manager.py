"""
ConnectionManager — manages the pool of DeltaClient instances (one per account).

Responsible for:
- Connecting / disconnecting accounts' WebSocket channels
- Providing live DeltaClient handles to the rest of the system
- Coordinating callbacks from TradeListener and PositionMonitor
"""

import asyncio
import logging
from typing import Callable, Awaitable, Dict, Optional

from app.services.delta_client import DeltaClient

logger = logging.getLogger(__name__)

OnFillCallback = Callable[[dict], Awaitable[None]]
OnPositionCallback = Callable[[dict], Awaitable[None]]


class ConnectionManager:
    """Maintains a dict of account_id → DeltaClient."""

    def __init__(self) -> None:
        self._clients: Dict[str, DeltaClient] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect_account(
        self,
        account: dict,
        on_fill: Optional[OnFillCallback] = None,
        on_position: Optional[OnPositionCallback] = None,
    ) -> DeltaClient:
        """
        Create a DeltaClient for *account* and start its WebSocket loop.

        *account* is a raw dict from Supabase with keys:
            id, api_key, api_secret, environment, name, ...
        """
        account_id: str = account["id"]
        async with self._lock:
            if account_id in self._clients:
                logger.info(
                    "Account %s (%s) already connected — reusing existing client.",
                    account.get("name"),
                    account_id,
                )
                return self._clients[account_id]

            client = DeltaClient(
                api_key=account["api_key"],
                api_secret=account["api_secret"],
                environment=account.get("environment", "demo"),
            )

            if on_fill is not None or on_position is not None:
                try:
                    await client.connect_websocket(
                        on_fill_callback=on_fill or self._noop,
                        on_position_callback=on_position or self._noop,
                    )
                    logger.info(
                        "WebSocket connected for account %s (%s)",
                        account.get("name"),
                        account_id,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to connect WebSocket for account %s: %s",
                        account.get("name"),
                        exc,
                        exc_info=True,
                    )
                    # Still store the client so REST calls work
            self._clients[account_id] = client
            return client

    async def disconnect_account(self, account_id: str) -> None:
        """Disconnect and remove the DeltaClient for *account_id*."""
        async with self._lock:
            client = self._clients.pop(account_id, None)
            if client is None:
                logger.warning("disconnect_account: no client found for %s", account_id)
                return
            try:
                await client.close()
                logger.info("Disconnected client for account %s", account_id)
            except Exception as exc:
                logger.error(
                    "Error disconnecting client for account %s: %s", account_id, exc
                )

    async def disconnect_all(self) -> None:
        """Cleanly disconnect every managed client."""
        async with self._lock:
            account_ids = list(self._clients.keys())

        # Close outside lock to avoid holding it during I/O
        await asyncio.gather(
            *[self.disconnect_account(aid) for aid in account_ids],
            return_exceptions=True,
        )
        logger.info("All %d client(s) disconnected.", len(account_ids))

    def get_client(self, account_id: str) -> Optional[DeltaClient]:
        """Return the DeltaClient for *account_id* or None if not connected."""
        return self._clients.get(account_id)

    def is_connected(self, account_id: str) -> bool:
        """Return True if there is a live WebSocket for *account_id*."""
        client = self._clients.get(account_id)
        if client is None:
            return False
        return client.ws_connected

    @property
    def connected_account_ids(self) -> list:
        return list(self._clients.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _noop(data: dict) -> None:
        """Default no-op callback when caller does not supply one."""
        pass


connection_manager = ConnectionManager()

