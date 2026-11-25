"""
ConnectionManager — manages the pool of exchange clients (one per account).
A client is a DeltaClient, or a PaperClient for a paper account; see
app/services/client_factory.py.

Responsible for:
- Connecting / disconnecting accounts' WebSocket channels
- Providing live client handles to the rest of the system
- Coordinating callbacks from TradeListener and PositionMonitor
"""

import asyncio
import logging
from typing import Callable, Awaitable, Dict, Optional

from app.database import db
from app.core import order_history as oh
from app.services.client_factory import ExchangeClient, client_for
from app.services.paper_client import is_paper

logger = logging.getLogger(__name__)

OnFillCallback = Callable[[dict], Awaitable[None]]
OnPositionCallback = Callable[[dict], Awaitable[None]]


class ConnectionManager:
    """Maintains a dict of account_id → exchange client."""

    def __init__(self) -> None:
        self._clients: Dict[str, ExchangeClient] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect_account(
        self,
        account: dict,
        on_fill: Optional[OnFillCallback] = None,
        on_position: Optional[OnPositionCallback] = None,
    ) -> ExchangeClient:
        """
        Create the client for *account* and start its WebSocket loop.

        *account* is a raw dict from Supabase with keys:
            id, api_key, api_secret, environment, name, ...

        A FOLLOWER with no on_fill of its own gets the history recorder by
        default. Defaulting it here rather than at each call site is deliberate:
        connect_account is called from four places (startup, the accounts API and
        twice from the copy engine) and three of them passed no on_fill at all, so
        whichever ran first decided whether that follower's fills were ever
        observed. A follower activated through the API, or first connected by the
        engine's REST path, silently had no fill stream — and its mirrored orders
        then filled with nothing recording it.
        """
        account_id: str = account["id"]
        if on_fill is None and not account.get("is_master"):
            on_fill = oh.make_follower_fill_recorder(db, account)
        async with self._lock:
            if account_id in self._clients:
                logger.info(
                    "Account %s (%s) already connected — reusing existing client.",
                    account.get("name"),
                    account_id,
                )
                return self._clients[account_id]

            # THE ONE BRANCH. A paper account is a follower with a name and a
            # balance and no keys; everything downstream of this line -- the
            # engine, the reconciler, the position monitor, the report, the
            # alerting -- goes on talking to "a client" and cannot tell the
            # difference. Keeping the choice here, and in exactly one other
            # place (CopyEngine._get_master_client, which refuses outright),
            # is what keeps paper out of the live path.
            #
            # `is True` on purpose, not a truthiness test. Getting this wrong
            # in the other direction -- a REAL account handed a PaperClient --
            # means its trades are never placed and nothing says so, which is
            # the worst outcome this file can produce. Only the boolean true
            # qualifies.
            client = client_for(account, db)
            if is_paper(account):
                logger.warning(
                    "Account %s (%s) is a PAPER account — no orders will reach "
                    "any exchange.", account.get("name"), account_id,
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
        """Disconnect and remove the client for *account_id*."""
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

    def get_client(self, account_id: str) -> Optional[ExchangeClient]:
        """Return the client for *account_id* or None if not connected."""
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

