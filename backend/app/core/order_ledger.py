"""
Order-ID ledger — an auditable, per-master-order record of what the copy engine
did on each follower.

WHY THIS EXISTS
---------------
Reconciliation used to be purely POSITION/NET based: compare the follower's size
to the master's and fix the difference. That tells you a mismatch exists but never
*why*, and the two answers call for completely different fixes.

C-BTC-67200-300726 (2026-07-29) is the case in point. It looked like a lost event;
the logs showed the opposite. Master order 1442271322 (a full-size exit limit) was
mirrored correctly in 4 seconds to follower order 1442271534. The master's order
half-filled; the follower's, behind it in the queue, filled NOTHING — and 2.5h
later the master cancelled, so the follower's copy was cancelled with it. The
copy pipeline did its job perfectly at every step. The problem was that the
follower's exits weren't *filling*, which no amount of retry logic addresses.

A position-only view cannot distinguish "the order never got placed" from "the
order was placed and never filled". This ledger can, because it records, keyed on
the MASTER order id:
  • the master order itself (symbol, side, size, entry/exit, when), and
  • one leg per follower — mirrored (with the follower's own order id),
    deliberately skipped (with the reason), or failed.

So "the sizes don't match" becomes "order 1442271534 was placed and never filled",
which points at the actual, fixable problem.

Best-effort by design: every helper swallows its own errors. A ledger write must
never be able to break, block or slow a live copy.

Redis layout
------------
  oledger:{master_order_id}          HASH  symbol, side, size, price, kind,
                                           source, owner_id, ts, state,
                                           f:{follower_id} -> leg JSON
  oledger:sym:{owner_id}:{symbol}    ZSET  master_order_id scored by ts
"""

import json
import logging
import time

logger = logging.getLogger(__name__)

# Keep a week of history — same retention as the ordermap it complements.
LEDGER_TTL = 7 * 24 * 3600

# Leg statuses that mean "the follower got this master order".
MIRRORED_STATUSES = ("placed", "filled")
# Statuses that mean "we consciously decided this follower needed nothing".
# These are accounted for and are NOT reported as missing.
ACCOUNTED_STATUSES = MIRRORED_STATUSES + ("skipped", "cancelled")


def _key(master_order_id) -> str:
    return f"oledger:{master_order_id}"


def _idx(owner_id, symbol) -> str:
    return f"oledger:sym:{owner_id or '_'}:{symbol}"


async def record_master_order(
    redis,
    master_order_id,
    *,
    symbol,
    side,
    size,
    price=None,
    kind="entry",
    owner_id=None,
    source="fill",
    ts=None,
) -> None:
    """Upsert the master side of a ledger entry.

    `kind` is "entry" or "exit" (an exit = reduce-only / close / trim — the class
    of order whose loss leaves a follower over-exposed). `source` records which
    path saw it ("fill" for a market fill, "order" for a resting order) so the
    audit trail shows how the engine learned about it. Idempotent: repeated calls
    for the same order (the resting-order path re-sees orders on every WS update
    and reconcile pass) just refresh the same fields.
    """
    if not redis or not master_order_id or not symbol:
        return
    try:
        now = float(ts or time.time())
        key = _key(master_order_id)
        idx = _idx(owner_id, symbol)
        # ONE round trip, not five. The master re-sends the same resting order
        # constantly (one stop was pushed 556 times in a session), so this runs on
        # the hot path — five sequential awaits each cost event-loop time and were
        # measurably delaying order placement. transaction=False: these are
        # independent writes, we need the batching, not atomicity.
        pipe = redis.pipeline(transaction=False)
        pipe.hset(key, mapping={
            "master_order_id": str(master_order_id),
            "symbol": str(symbol),
            "side": str(side or ""),
            "size": str(size or 0),
            "price": str(price if price is not None else ""),
            "kind": str(kind or "entry"),
            "source": str(source or ""),
            "owner_id": str(owner_id or ""),
            "ts": str(now),
        })
        pipe.expire(key, LEDGER_TTL)
        pipe.zadd(idx, {str(master_order_id): now})
        pipe.zremrangebyscore(idx, 0, now - LEDGER_TTL)
        pipe.expire(idx, LEDGER_TTL)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"ledger: record_master_order failed for {master_order_id}: {e}")


async def record_follower_leg(
    redis,
    master_order_id,
    follower_id,
    *,
    status,
    follower_order_id=None,
    qty=None,
    reason=None,
) -> None:
    """Record what happened to ONE follower for this master order.

    status: "placed"  — a resting order was mirrored onto the follower
            "filled"  — the follower's copy executed
            "skipped" — deliberately nothing to do (reason says why)
            "failed"  — we tried and the exchange rejected it
            "cancelled" — the mirrored order was later cancelled
    """
    if not redis or not master_order_id or not follower_id or not status:
        return
    try:
        leg = {"status": str(status), "ts": time.time()}
        if follower_order_id is not None:
            leg["order_id"] = str(follower_order_id)
        if qty is not None:
            leg["qty"] = int(float(qty))
        if reason:
            leg["reason"] = str(reason)[:200]
        key = _key(master_order_id)
        pipe = redis.pipeline(transaction=False)  # one round trip, see above
        pipe.hset(key, f"f:{follower_id}", json.dumps(leg))
        pipe.expire(key, LEDGER_TTL)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"ledger: record_follower_leg failed for {master_order_id}/{follower_id}: {e}")


async def mark_state(redis, master_order_id, state: str) -> None:
    """Stamp a lifecycle state on the master entry (e.g. "cancelled")."""
    if not redis or not master_order_id:
        return
    try:
        await redis.hset(_key(master_order_id), "state", str(state))
    except Exception:
        pass


def _parse(raw: dict) -> dict:
    """Turn a raw ledger hash into a structured entry with its follower legs."""
    entry = {
        "master_order_id": raw.get("master_order_id"),
        "symbol": raw.get("symbol"),
        "side": raw.get("side"),
        "kind": raw.get("kind"),
        "source": raw.get("source"),
        "state": raw.get("state"),
        # Carried through so API callers can enforce tenant ownership on a
        # lookup by raw order id (there's no other owner reference on it).
        "owner_id": raw.get("owner_id") or None,
        "legs": {},
    }
    for fld in ("size", "price", "ts"):
        try:
            entry[fld] = float(raw.get(fld)) if raw.get(fld) not in (None, "") else None
        except (TypeError, ValueError):
            entry[fld] = None
    for k, v in (raw or {}).items():
        if not str(k).startswith("f:"):
            continue
        try:
            entry["legs"][str(k)[2:]] = json.loads(v)
        except Exception:
            entry["legs"][str(k)[2:]] = {"status": "unknown"}
    return entry


async def get_entry(redis, master_order_id) -> dict:
    """Full ledger entry for one master order id (empty dict if unknown)."""
    if not redis or not master_order_id:
        return {}
    try:
        raw = await redis.hgetall(_key(master_order_id))
    except Exception as e:
        logger.debug(f"ledger: get_entry failed for {master_order_id}: {e}")
        return {}
    return _parse(raw) if raw else {}


async def recent(redis, owner_id, symbol, limit: int = 50) -> list:
    """Ledger entries for a symbol, newest first."""
    if not redis or not symbol:
        return []
    try:
        oids = await redis.zrevrange(_idx(owner_id, symbol), 0, max(0, limit - 1))
    except Exception as e:
        logger.debug(f"ledger: recent failed for {symbol}: {e}")
        return []
    out = []
    for oid in oids or []:
        e = await get_entry(redis, oid)
        if e:
            out.append(e)
    return out


async def missing_for_follower(
    redis, owner_id, symbol, follower_id, *, kind=None, since=None, limit: int = 50
) -> list:
    """Master orders on `symbol` that never reached `follower_id`.

    "Never reached" = no leg recorded at all, or a leg that FAILED. A leg marked
    "skipped" was a deliberate decision (e.g. the follower was already at its
    rebalance target) and is therefore accounted for, not missing.

    Pass kind="exit" to ask the question that matters most for a size mismatch:
    which of the master's closes/trims did this follower never get?
    """
    out = []
    for e in await recent(redis, owner_id, symbol, limit=limit):
        if kind and e.get("kind") != kind:
            continue
        if since is not None and (e.get("ts") or 0) < since:
            continue
        leg = (e.get("legs") or {}).get(str(follower_id))
        if leg and leg.get("status") in ACCOUNTED_STATUSES:
            continue
        out.append(e)
    return out
