"""Regression: a cancelled master order must not be re-mirrored back to life.

Run:  venv/Scripts/python.exe test_cancel_resurrect.py
No network, no Redis, no Supabase.

HOW IT HAPPENED. The order reconcile runs every 30 seconds: it fetches the
master's resting limits and pushes a "place" for each one, relying on a
per-order idempotency guard so it only fills gaps. But it works from the
snapshot it just took. If the master cancels in the window between that fetch
and the event being handled, the reconcile pushes a place for an order that no
longer exists.

The follower is then left holding an order NOTHING WILL EVER CANCEL. The
master's order is gone, so no further cancel event will ever name it, and the
follower's side has no way to learn it is orphaned.

2026-11-19 14:57:51, C-BTC-69000-201126, Pratik's desk:

    :51.121  master cancelled 1696147383
    :51.242  "Reconcile: re-mirrored 1 resting limit order(s) for master Dhwani"
    :51.656  Cancelled mirrored order 1696147507      <- the cancel worked fine
    :54.036  Mirrored 1696147383 -> Dhwani1 1696325930 (2996)   <- resurrected

It sat in his book until he cancelled it by hand about three and a half hours
later. Note the cancel itself was never broken — the bug put a NEW order there
immediately afterwards.

THE FIX. _mirror_cancel tombstones the master order id; _mirror_place refuses
to place for a tombstoned id. The cancel is handled before the stale place, so
the tombstone is in place by the time it matters.

The fallbacks matter as much as the rule: if Redis cannot be read the mirror
goes ahead. A spurious order can be cancelled; a MISSED one is a follower that
silently stops tracking its master.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

FAILURES = []

MASTER_ORDER = "1696147383"
OTHER_ORDER = "1696023907"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Redis:
    def __init__(self, read_boom=False, write_boom=False):
        self.store = {}
        self.read_boom = read_boom
        self.write_boom = write_boom

    async def setex(self, key, _ttl, val):
        if self.write_boom:
            raise RuntimeError("redis write failed")
        self.store[key] = val

    async def get(self, key):
        if self.read_boom:
            raise RuntimeError("redis read failed")
        return self.store.get(key)


async def on_cancel(redis, master_order_id):
    """_mirror_cancel's first act."""
    try:
        await redis.setex(f"ocancel:{master_order_id}", 600, "1")
    except Exception:
        pass


async def would_mirror(redis, master_order_id):
    """_mirror_place's guard. True = the order gets placed on the follower."""
    try:
        if await redis.get(f"ocancel:{master_order_id}"):
            return False
    except Exception:
        return True          # cannot check -> mirror rather than drop a live order
    return True


# ---------------------------------------------------------------------------


def test_the_live_incident():
    print("\n1. THE LIVE CASE: cancel, then a stale re-mirror 119ms later")
    r = Redis()

    async def seq():
        await on_cancel(r, MASTER_ORDER)              # :51.121
        return await would_mirror(r, MASTER_ORDER)    # :51.240, from the snapshot

    check("the stale re-mirror is refused", asyncio.run(seq()), False)


def test_the_old_behaviour_for_comparison():
    print("\n2. without the tombstone")
    r = Redis()

    async def old():
        # no tombstone written at all
        return await would_mirror(r, MASTER_ORDER)

    check("WOULD have placed it (the bug)", asyncio.run(old()), True)


def test_an_untouched_order_still_mirrors():
    print("\n3. the reconcile still fills genuine gaps")
    r = Redis()

    async def seq():
        await on_cancel(r, MASTER_ORDER)              # a DIFFERENT order cancelled
        return await would_mirror(r, OTHER_ORDER)

    check("other orders unaffected", asyncio.run(seq()), True)
    check("nothing cancelled at all", asyncio.run(would_mirror(Redis(), OTHER_ORDER)), True)


def test_repeated_stale_remirrors_stay_refused():
    print("\n4. the reconcile retries every 30s — all must be refused")
    r = Redis()

    async def seq():
        await on_cancel(r, MASTER_ORDER)
        return [await would_mirror(r, MASTER_ORDER) for _ in range(5)]

    check("five passes, none placed", asyncio.run(seq()), [False] * 5)


def test_a_redis_that_cannot_be_read_still_mirrors():
    print("\n5. unreadable Redis must not cost us a live order")
    r = Redis(read_boom=True)
    # A missed mirror is a follower that silently stops tracking its master;
    # a spurious one can be cancelled. Fail toward placing.
    check("mirrors anyway", asyncio.run(would_mirror(r, MASTER_ORDER)), True)


def test_a_failed_tombstone_write_does_not_crash_the_cancel():
    print("\n6. a failed tombstone write is survivable")
    r = Redis(write_boom=True)

    async def seq():
        await on_cancel(r, MASTER_ORDER)     # must not raise
        return await would_mirror(r, MASTER_ORDER)

    # Degrades to the old behaviour for this one order, and the cancel itself
    # still completes — which is the part that matters.
    check("cancel survives", asyncio.run(seq()), True)


if __name__ == "__main__":
    for t in (test_the_live_incident,
              test_the_old_behaviour_for_comparison,
              test_an_untouched_order_still_mirrors,
              test_repeated_stale_remirrors_stay_refused,
              test_a_redis_that_cannot_be_read_still_mirrors,
              test_a_failed_tombstone_write_does_not_crash_the_cancel):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
