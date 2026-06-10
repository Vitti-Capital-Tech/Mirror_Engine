"""
Socket.IO manager — server → browser real-time events.

Uses python-socketio AsyncServer in ASGI mode.  The `sio` and `socket_app`
objects are imported by main.py and the routers; `socket_manager` is the
high-level helper for emitting named events.
"""

import logging
from typing import Optional

import socketio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core Socket.IO objects — module-level singletons
# ---------------------------------------------------------------------------

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)

socket_app = socketio.ASGIApp(sio)


# ---------------------------------------------------------------------------
# Event handlers (registered on the module-level sio)
# ---------------------------------------------------------------------------

@sio.event
async def connect(sid: str, environ: dict, auth: Optional[dict] = None) -> None:
    logger.info("Socket.IO client connected: %s", sid)
    await sio.emit(
        "system_status",
        {"status": "connected", "message": "Welcome to Mirror Engine"},
        to=sid,
    )


@sio.event
async def disconnect(sid: str) -> None:
    logger.info("Socket.IO client disconnected: %s", sid)


@sio.event
async def ping(sid: str, data: dict) -> None:
    """Simple ping/pong for client-side keep-alive."""
    await sio.emit("pong", {"ts": data.get("ts")}, to=sid)


# ---------------------------------------------------------------------------
# High-level emit helper
# ---------------------------------------------------------------------------

class SocketManager:
    """
    Thin wrapper that provides named-event helpers so the rest of the
    application does not need to import `sio` directly.
    """

    @property
    def connected_clients(self) -> int:
        """Return the number of currently connected Socket.IO sessions."""
        try:
            return len(sio.manager.rooms.get("/", {}).get(None, set()))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Emit helpers
    # ------------------------------------------------------------------

    async def emit_trade_copy(self, data: dict) -> None:
        """Broadcast a completed trade-copy event to all browsers."""
        try:
            await sio.emit("trade_copy", data)
        except Exception as exc:
            logger.error("emit_trade_copy failed: %s", exc)

    async def emit_position_update(self, data: dict) -> None:
        """Broadcast a position update."""
        try:
            await sio.emit("position_update", data)
        except Exception as exc:
            logger.error("emit_position_update failed: %s", exc)

    async def emit_alert(self, data: dict) -> None:
        """Broadcast an alert (slippage, out-of-sync, circuit-break, etc.)."""
        try:
            await sio.emit("alert", data)
        except Exception as exc:
            logger.error("emit_alert failed: %s", exc)

    async def emit_account_update(self, data: dict) -> None:
        """Broadcast an account status / balance update."""
        try:
            await sio.emit("account_update", data)
        except Exception as exc:
            logger.error("emit_account_update failed: %s", exc)

    async def emit_system_status(self, data: dict) -> None:
        """Broadcast a system health update."""
        try:
            await sio.emit("system_status", data)
        except Exception as exc:
            logger.error("emit_system_status failed: %s", exc)

    async def emit_to_client(self, sid: str, event: str, data: dict) -> None:
        """Send an event to a specific Socket.IO session."""
        try:
            await sio.emit(event, data, to=sid)
        except Exception as exc:
            logger.error("emit_to_client(%s, %s) failed: %s", sid, event, exc)


# Module-level singleton
socket_manager = SocketManager()
