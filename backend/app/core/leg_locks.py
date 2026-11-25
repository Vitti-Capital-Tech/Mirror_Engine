"""One lock per (master, symbol), shared by the live mirror and the reconciler.

WHY THIS EXISTS. Two things write to a follower's book: the live mirror, which
is event driven, and the reconciler, which sweeps every 10 seconds. They were
never mutually exclusive. The dispatcher locked order events on their symbol,
but a reconcile event carries a list of positions and no `symbol` field at all,
so it fell back to a single shared key and took a DIFFERENT lock -- which
excludes nothing. Both paths could place an order on the same follower and the
same leg at the same moment.

What stood between that and a double order was the reconciler's two-pass
confirmation and its last-look re-read: both good, both heuristics. Most of the
rules in the reconcile sweep -- "the follower is still building", "the master is
still working an order", "a just-filled mirror may not be reflected yet" -- are
there to avoid a collision that a lock removes outright.

The key carries the MASTER as well as the symbol. Keyed on the symbol alone,
two desks trading the same contract block each other for no reason: JD algo4
and Dhwani both hold BTC options and were on the same strike on 2026-11-19.
That is the same mistake that had five of the engine's caches serving one
master's answers to another.

TWO RULES, and they are what keep a lock from becoming the new problem.

1. NARROW. The reconciler takes the lock around ONE correction, never around a
   pass. A pass holds no locks between legs, so the live mirror is never waiting
   on a sweep of twenty symbols to finish.

2. NEVER BLOCK FOREVER. Acquisition is bounded. If the lock cannot be had in
   ACQUIRE_TIMEOUT the caller skips that leg and the next sweep retries ten
   seconds later -- the correction is late, which is recoverable, rather than
   the sweep being stuck behind a hung REST call, which is not.

The locks are not reentrant, so a path that already holds one must not ask for
it again. The dispatcher locks the live-mirror events; the reconciler locks its
own corrections; neither runs inside the other.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# How long a correction will wait for the leg to be free before giving up on
# this pass. Comfortably longer than a mirror's REST round trips (a place plus
# its guards measured ~1s), short enough that a hung call costs one sweep.
ACQUIRE_TIMEOUT = 5.0

# How long a LIVE master event (a fill, a resting order, a cancel) waits for the
# leg. It cannot skip — a master trade must be copied — so after this it runs
# anyway. 5s was shorter than one copy takes (limit, a 5s fill wait, market
# fallback, confirm: ~5-10s), so the next event on the same leg routinely ran
# UNLOCKED alongside it, both sizing off the same not-yet-updated position.
# 2026-11-24 11:09 IST, C-BTC-62600: three pieces of one master sell ran at
# once that way and the follower sold 503 instead of 201. 30s outlasts a copy,
# so events on one leg now run one after another; a genuinely hung call still
# only holds the leg for 30s. Other legs are never affected.
LIVE_ACQUIRE_TIMEOUT = 30.0

# A lock is dropped once it has been idle this long. Delta's BTC options expire
# daily, so every trading day mints a fresh set of strikes that will never be
# seen again; without this the registry grows for the life of the process.
IDLE_EVICT_SEC = 3600.0

_locks: dict = {}       # {(master, symbol): [asyncio.Lock, last_used_ts]}


def _key(master_id, symbol) -> tuple:
    return (str(master_id or ""), str(symbol or ""))


def _evict(now: float) -> None:
    """Drop locks nobody has touched in a while, and never one that is held."""
    for k in [k for k, v in _locks.items()
              if now - v[1] > IDLE_EVICT_SEC and not v[0].locked()]:
        _locks.pop(k, None)


def get(master_id, symbol) -> asyncio.Lock:
    """The lock for this leg, created on first use."""
    now = time.time()
    k = _key(master_id, symbol)
    entry = _locks.get(k)
    if entry is None:
        entry = [asyncio.Lock(), now]
        _locks[k] = entry
        if len(_locks) % 64 == 0:
            _evict(now)
    entry[1] = now
    return entry[0]


@asynccontextmanager
async def hold(master_id, symbol, what: str = "", timeout: float = None):
    """Hold this leg for the duration of ONE action.

    Yields True when the lock was taken, False when it could not be had within
    ACQUIRE_TIMEOUT. A caller that gets False must do nothing to the leg: the
    other path is mid-flight on it, which is exactly the case this exists to
    prevent, and the next sweep will see the result of whatever it did.
    """
    lock = get(master_id, symbol)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout or ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(
            "leg busy: %s on %s held longer than %.0fs%s — skipping this pass",
            master_id, symbol, timeout or ACQUIRE_TIMEOUT, f" ({what})" if what else "")
        yield False
        return
    except Exception as e:
        # Never let the lock itself stop a correction. A missed correction is a
        # follower drifting from its master; the collision this guards against
        # is the rarer of the two.
        logger.warning("leg lock unavailable for %s on %s: %s", master_id, symbol, e)
        yield True
        return
    try:
        yield True
    finally:
        try:
            lock.release()
        except RuntimeError:
            pass


def stats() -> dict:
    return {"locks": len(_locks), "held": sum(1 for v in _locks.values() if v[0].locked())}


def is_busy(master_id, symbol) -> bool:
    """Is another path mid-action on this leg RIGHT NOW?

    Positive evidence that a copy is IN FLIGHT, which is a different thing from
    a discrepancy that is merely unresolved. The reconciler's alert path uses it
    to tell the two apart: a follower that still holds a leg while the live
    mirror is working that same leg is a book caught mid-flight, not a fault.

    2026-11-25 22:55:51 IST, Dhwani1 on C-BTC-63200-261126: the master closed
    his -3000 manually, the follower's limit did not fill and its market
    fallback took 7.2s. For those seconds the master was flat and the follower
    still showed 473 lots, so the sweep raised "LEG THE MASTER DOES NOT HOLD" —
    and the message landed four seconds AFTER the fallback had filled and the
    leg was flat.

    Does not create a lock: asking must never mint registry entries for every
    symbol anyone ever alerts about.
    """
    entry = _locks.get(_key(master_id, symbol))
    return bool(entry and entry[0].locked())
