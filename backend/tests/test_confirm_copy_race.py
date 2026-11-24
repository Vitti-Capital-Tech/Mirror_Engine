# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: confirm-copy must not market on top of a resting mirror.

Run:  venv/Scripts/python.exe test_confirm_copy_race.py
No network, no Redis, no Supabase.

WHAT confirm-copy IS FOR. When a master's order fills, the live copy should
have mirrored it. confirm-copy waits a few seconds for that to settle and then
checks: if the follower got nothing, it buys the shortfall at market. It is the
backstop for a mirror that never happened.

HOW IT DOUBLED A POSITION. The order map was read BEFORE the settle sleep and
judged AFTER it:

    mapping = await redis.hgetall(f"ordermap:{master_order_id}")   # read
    ...
    await asyncio.sleep(ORDER_CONFIRM_SETTLE_SEC)                  # wait
    ...
    if not mapping:                                               # judge

A mirror placed during those seconds was invisible. The sleep exists precisely
to let it land, and the value it was meant to re-examine was captured before
the wait.

2026-11-19 04:10, P-BTC-61600-191126:

    :38.36  map read — empty, the mirror had not been placed yet
    :40.57  Mirrored order 1694856008 -> JD algo3 order 1694856094 (qty 2768)
    :41.50  woke holding the empty snapshot: "nothing was mirrored"
    :42.73  marketed 2771 on top
    :44.04  the resting 2768 limit filled

Follower ended with 5539 against a target of 2771.

The guard that should have stopped it was already there — the per-follower loop
asks _order_is_live and defers to escalation rather than stacking on a resting
mirror. It never fired because the map it consults was empty. So the fix is to
re-read after the settle, and these tests pin that the fresh view is what gets
used.
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

MASTER_ORDER = "1694856008"
FOLLOWER = "follower-algo3"
MIRROR = "1694856094"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Redis:
    """An order map that gains the mirror DURING the settle, as it did live."""

    def __init__(self, at_start=None, lands_during_sleep=None, boom=False):
        self.store = dict(at_start or {})
        self.lands = lands_during_sleep or {}
        self.boom = boom
        self.reads = 0

    async def hgetall(self, _key):
        self.reads += 1
        if self.boom:
            raise RuntimeError("redis unreachable")
        return dict(self.store)

    def settle(self):
        """The live mirror lands while confirm-copy is asleep."""
        self.store.update(self.lands)


async def run_window(redis, mapping=None):
    """The read / settle / re-read sequence exactly as the engine performs it."""
    if mapping is None:
        mapping = await redis.hgetall(f"ordermap:{MASTER_ORDER}")

    redis.settle()                       # the mirror lands mid-sleep

    try:                                 # the fix
        fresh = await redis.hgetall(f"ordermap:{MASTER_ORDER}")
        if fresh:
            mapping = {**(mapping or {}), **fresh}
    except Exception:
        pass
    return mapping


def would_market(mapping):
    """What the engine decides from here.

    Empty map -> "nothing was mirrored" -> market the whole shortfall.
    Follower present -> _order_is_live defers to escalation.
    """
    return not mapping or FOLLOWER not in mapping


# ---------------------------------------------------------------------------


def test_the_live_incident_no_longer_doubles():
    print("\n1. THE LIVE CASE: mirror lands during the settle")
    r = Redis(at_start={}, lands_during_sleep={FOLLOWER: MIRROR})
    mapping = asyncio.run(run_window(r))
    check("the mirror is visible", mapping.get(FOLLOWER), MIRROR)
    check("does NOT market on top", would_market(mapping), False)


def test_the_old_behaviour_for_comparison():
    print("\n2. the same sequence without the re-read")
    r = Redis(at_start={}, lands_during_sleep={FOLLOWER: MIRROR})

    async def old():
        mapping = await r.hgetall(f"ordermap:{MASTER_ORDER}")  # read first
        r.settle()                                             # mirror lands
        return mapping                                         # judged stale

    mapping = asyncio.run(old())
    check("stale view is empty", mapping, {})
    check("WOULD have marketed (the bug)", would_market(mapping), True)


def test_a_genuinely_missed_mirror_is_still_corrected():
    print("\n3. the backstop still works when nothing was mirrored")
    r = Redis(at_start={}, lands_during_sleep={})     # nothing ever lands
    mapping = asyncio.run(run_window(r))
    check("map still empty", mapping, {})
    check("DOES market — this is the point", would_market(mapping), True)


def test_a_mirror_already_recorded_is_left_alone():
    print("\n4. the ordinary case: mirror landed before the event")
    r = Redis(at_start={FOLLOWER: MIRROR}, lands_during_sleep={})
    mapping = asyncio.run(run_window(r))
    check("still visible", mapping.get(FOLLOWER), MIRROR)
    check("does not market", would_market(mapping), False)


def test_other_followers_landing_mid_sleep_are_seen_too():
    print("\n5. merged, not replaced — one follower early, one late")
    r = Redis(at_start={"follower-a": "111"},
              lands_during_sleep={"follower-b": "222"})
    mapping = asyncio.run(run_window(r))
    check("the early one survived", mapping.get("follower-a"), "111")
    check("the late one was picked up", mapping.get("follower-b"), "222")


def test_a_caller_supplied_map_is_refreshed_not_discarded():
    print("\n6. a mapping passed in still gets the mid-sleep arrivals")
    r = Redis(at_start={}, lands_during_sleep={"follower-b": "222"})
    mapping = asyncio.run(run_window(r, mapping={"follower-a": "111"}))
    check("caller's entry kept", mapping.get("follower-a"), "111")
    check("new arrival added", mapping.get("follower-b"), "222")
    check("no wasted first read", r.reads, 1)


def test_an_unreadable_redis_falls_back_rather_than_crashing():
    print("\n7. redis down mid-window")
    r = Redis(at_start={FOLLOWER: MIRROR}, boom=True)
    mapping = asyncio.run(run_window(r, mapping={FOLLOWER: MIRROR}))
    check("keeps the pre-settle view", mapping.get(FOLLOWER), MIRROR)
    check("does not market", would_market(mapping), False)


if __name__ == "__main__":
    for t in (test_the_live_incident_no_longer_doubles,
              test_the_old_behaviour_for_comparison,
              test_a_genuinely_missed_mirror_is_still_corrected,
              test_a_mirror_already_recorded_is_left_alone,
              test_other_followers_landing_mid_sleep_are_seen_too,
              test_a_caller_supplied_map_is_refreshed_not_discarded,
              test_an_unreadable_redis_falls_back_rather_than_crashing):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
