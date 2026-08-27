"""A short-TTL read cache for the `accounts` table.

Why this exists
---------------
The Supabase client is SYNCHRONOUS, so every call blocks the event loop for the
whole round trip (~170ms to the hosted instance). Measured on the live backend,
2026-08-27: 819 Supabase calls in three minutes, evenly spaced, of which **398
were reads of `accounts`** — roughly one second of blocked loop per second of
wall clock. The WebSocket worker is a single serial consumer, so anything that
blocks the loop backs the order feed up directly: order events were arriving
7-8s stale during bursts, and a stale "this order is still open" event processed
after the copy had already filled is what produced duplicate mirrors.

The wasteful part is that those reads are almost entirely the SAME rows:

    203x  accounts?select=id&is_master=eq.True     <- identical query, 1.1/sec
     71x  accounts?select=*&id=eq.<uuid>
     64x  accounts?select=*&is_master=eq.True

`check_sync` alone read the master twice per call — once for its id and once for
its balance — plus the follower row, three round trips for data that changes on
the order of minutes.

Note the fix that was NOT chosen: pushing these calls off the loop with
asyncio.to_thread. That was tried and reverted (see order_history.py) — the
Supabase client is shared process-wide and driving it from pool threads
concurrently with the loop corrupts it, producing Cloudflare 400s on unrelated
callers. Not making the call at all has none of that risk.

Staleness
---------
TTL is deliberately short and the cached data is deliberately boring:

* Role and id (`is_master`) change only when someone promotes an account, and
  every mutating route in the accounts API calls invalidate().
* Balances are refreshed by the position poller on a 4s cycle, so a 3s cache adds
  at most 3s to data that is already up to 4s old by construction.

This is for the read-only MONITORING path. The order-placement path in
copy_engine deliberately still reads through, because sizing an order is the one
place where a few seconds of balance staleness could actually cost money.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Long enough to collapse the repeated reads inside a single monitoring pass and
# across the ~1/sec position updates; short enough that a paused or promoted
# account takes effect almost immediately even if an invalidate() were missed.
DEFAULT_TTL = 3.0


class AccountCache:
    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self.ttl = ttl
        self._masters: Optional[tuple] = None      # (rows, fetched_at)
        self._by_id: dict = {}                     # id -> (row, fetched_at)
        self.hits = 0
        self.misses = 0

    # -- internals ---------------------------------------------------------
    def _fresh(self, stamped) -> bool:
        return stamped is not None and (time.time() - stamped[1]) < self.ttl

    # -- reads -------------------------------------------------------------
    def masters(self, db) -> list:
        """Every master account row, full columns.

        One query serves both "what is the master's id?" and "what is the
        master's balance?" — they used to be two separate round trips issued
        within a few lines of each other.
        """
        if self._fresh(self._masters):
            self.hits += 1
            return self._masters[0]
        try:
            rows = (db.table("accounts").select("*").eq("is_master", True).execute().data) or []
        except Exception as e:
            # A read failure must not be cached, and must not look like "there is
            # no master" — that would make a follower read as unsynced. Serve the
            # stale copy if we have one, otherwise let the caller see empty.
            logger.warning("account_cache: master read failed (%s)", e)
            return self._masters[0] if self._masters else []
        self.misses += 1
        self._masters = (rows, time.time())
        for r in rows:
            if r.get("id"):
                self._by_id[r["id"]] = (r, time.time())
        return rows

    def master(self, db) -> Optional[dict]:
        rows = self.masters(db)
        return rows[0] if rows else None

    def master_id(self, db) -> Optional[str]:
        m = self.master(db)
        return m.get("id") if m else None

    def get(self, db, account_id: str) -> Optional[dict]:
        """One account row by id."""
        if not account_id:
            return None
        stamped = self._by_id.get(account_id)
        if self._fresh(stamped):
            self.hits += 1
            return stamped[0]
        try:
            rows = (db.table("accounts").select("*").eq("id", account_id).execute().data) or []
        except Exception as e:
            logger.warning("account_cache: account read failed for %s (%s)", account_id, e)
            return stamped[0] if stamped else None
        self.misses += 1
        row = rows[0] if rows else None
        if row:
            self._by_id[account_id] = (row, time.time())
        return row

    # -- writes ------------------------------------------------------------
    def invalidate(self, account_id: Optional[str] = None) -> None:
        """Drop cached rows. Called by every mutating accounts route so a
        promote / pause / re-key takes effect on the next read rather than up to
        TTL seconds later."""
        self._masters = None
        if account_id:
            self._by_id.pop(account_id, None)
        else:
            self._by_id.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(self.hits / total * 100, 1) if total else 0.0,
            "ttl_sec": self.ttl,
        }


# Process-wide instance. The monitoring path is the hot one; see the module
# docstring for why the order-placement path deliberately does not use this.
account_cache = AccountCache()
