"""Best bid/ask per symbol, cached.

Why this exists
---------------
Unrealized PnL was computed off `mark_price`, which on options is nowhere near
the price you can actually trade at. Measured on the live book, 2026-11-13,
C-BTC-72000-141126:

    mark_price  1.97
    best_bid    2.8
    best_ask    3

So the dashboard's PnL could not agree with Delta's own, and the gap was not a
rounding difference — it was 40%+ on that contract. (Jigar, 2026-11-11: "mark
price ki jagah sirf bid ask use karna hai".)

The exit price is the side you have to CROSS: a long is closed by selling into
the bid, a short by lifting the ask. That is what `exit_price_for` returns.

The position payload does not carry quotes — only mark_price — so this has to
come from the ticker. Position updates arrive a few times a second across ~16
symbols, and one REST call per update would be absurd, so quotes are cached per
symbol for QUOTE_TTL_SEC. The endpoint is PUBLIC (no signature), which is why
this can be a plain client and does not need an account's keys.

A stale or missing quote never blocks anything: callers fall back to mark_price,
which is exactly today's behaviour.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Long enough to collapse the burst of position updates that arrive together,
# short enough that the number on screen is still the live book. Quotes move
# constantly; this is a display figure, not something an order is sized on.
QUOTE_TTL_SEC = 3.0

# symbol -> (fetched_at, best_bid, best_ask)
_cache: dict = {}
# One in-flight fetch per symbol. Without this, the burst of position updates
# that arrive in the same tick would each miss the cache and fire their own
# request for the same symbol.
_inflight: dict = {}

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=6.0)
    return _client


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


async def _fetch(symbol: str, rest_url: str):
    try:
        r = await _http().get(f"{rest_url}/v2/tickers/{symbol}")
        r.raise_for_status()
        data = r.json()
        result = data.get("result", data) if isinstance(data, dict) else {}
        quotes = result.get("quotes") or {}
        bid = _num(quotes.get("best_bid"))
        ask = _num(quotes.get("best_ask"))
        _cache[symbol] = (time.time(), bid, ask)
        return bid, ask
    except Exception as e:
        # Cache the miss briefly too. A symbol with no book (expired, delisted)
        # would otherwise be re-requested on every single position update.
        _cache[symbol] = (time.time(), None, None)
        logger.debug("quote fetch failed for %s: %s", symbol, e)
        return None, None


async def get_quote(symbol: str, environment: str = "production"):
    """(best_bid, best_ask) for `symbol`, or (None, None) if unavailable."""
    if not symbol:
        return None, None
    hit = _cache.get(symbol)
    if hit and (time.time() - hit[0]) < QUOTE_TTL_SEC:
        return hit[1], hit[2]

    existing = _inflight.get(symbol)
    if existing is not None:
        return await existing

    rest_url = (settings.DELTA_DEMO_REST_URL if environment == "demo"
                else settings.DELTA_LIVE_REST_URL)
    task = asyncio.ensure_future(_fetch(symbol, rest_url))
    _inflight[symbol] = task
    try:
        return await task
    finally:
        _inflight.pop(symbol, None)


async def exit_price_for(symbol: str, side: str, fallback=None,
                         environment: str = "production"):
    """The price this position would actually be CLOSED at.

    A long exits by selling into the best bid; a short exits by lifting the best
    ask. Returns `fallback` (the mark price, at every call site) when the book
    cannot be read, so a quote outage degrades to the old behaviour rather than
    showing nothing.
    """
    bid, ask = await get_quote(symbol, environment)
    want = bid if str(side).lower() == "long" else ask
    return want if want is not None else fallback
