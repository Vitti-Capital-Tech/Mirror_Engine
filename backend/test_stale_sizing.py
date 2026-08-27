"""Regression: a filled master order must never be counted as still resting.

Run:  venv/Scripts/python.exe test_stale_sizing.py
No network, no Redis, no Supabase.

The incident (live, 2026-08-27, P-BTC-74500-280826):

    02:18:46  master's 2750-lot sell FILLS      -> master position becomes 2750
    02:18:49  a 7.14s-STALE "state=open" event for the SAME order arrives
    02:18:50  follower opens 62 lots            -> target was 31
    02:19:46  reconciler buys 31 back

The sizing path treats an arriving event as authoritative that the order is
resting, because the REST snapshot can lag the WS. That holds for a fresh event
and breaks for a stale one: `m_pos` already contains the lots of an order that
has since filled, so injecting it as a resting rung counts the same 2750 twice —
once as the position they became, once as an order still waiting to fill.

    would_hold = 2750 (position) + 2750 (phantom rung) = 5500
    ceil(5500 x 0.011194) = 62      <-- placed
    ceil(2750 x 0.011194) = 31      <-- correct

Distinct from the duplicate-mirror bug (a49ef31): that one placed the order
TWICE, this one places ONE order of double size. Same root cause — a stale event
believed over reality — different symptom, so it survived that fix.

The existing staleness guard re-checks the exchange only above
STALE_EVENT_RECHECK_SEC (10s), and this event was 7.14s. An order id we have SEEN
fill needs no exchange call to be trusted, which is what the marker gives us.
"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core import order_ledger as ledger
from app.core.copy_engine import CopyEngine

FAILURES = []

MASTER_ORDER_ID = "1499459885"
MASTER_QTY = 2750.0
RATIO = 0.011194


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


class FakeRedis:
    def __init__(self, kv=None, raises=False):
        self.kv = kv or {}
        self.raises = raises

    async def get(self, k):
        if self.raises:
            raise RuntimeError("redis down")
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        if self.raises:
            raise RuntimeError("redis down")
        self.kv[k] = str(v)


def engine_with(redis):
    eng = CopyEngine.__new__(CopyEngine)
    eng.redis = redis
    return eng


async def size_it(eng, *, snapshot_rungs, m_pos):
    """Replay the entry-ladder sizing decision.

    Mirrors the code under test: inject this order as a resting rung only if we
    have not seen it fill, then size the follower on where the master would end
    up if every resting entry filled.
    """
    rungs = list(snapshot_rungs)
    if not await eng._master_order_filled(MASTER_ORDER_ID):
        if not any(rid == str(MASTER_ORDER_ID) for rid, _ in rungs):
            rungs = rungs + [(str(MASTER_ORDER_ID), MASTER_QTY)]
    would_hold = float(m_pos) + sum(sz for _, sz in rungs)
    return would_hold, max(0, math.ceil(would_hold * RATIO))


async def test_stale_event_after_fill_does_not_double_count():
    print("\nTHE BUG: a 7.14s-stale 'still open' event after the order filled")
    r = FakeRedis()
    eng = engine_with(r)
    # The listener saw the fill at 02:18:46 and marked the id.
    await r.set(ledger.master_filled_key(MASTER_ORDER_ID), "1")
    # The stale event arrives: the master's position already holds the 2750, and
    # the fresh snapshot correctly shows nothing resting.
    would_hold, qty = await size_it(eng, snapshot_rungs=[], m_pos=2750.0)
    check("sized on the position only, not double", would_hold, 2750.0)
    check("follower opens the correct 31", qty, 31)
    check("NOT the 62 that actually went out", qty == 62, False)


async def test_fresh_event_still_sizes_the_resting_order():
    print("\nthe normal case must keep working: order genuinely still resting")
    r = FakeRedis()
    eng = engine_with(r)
    # No fill marker — the order really is open, and the REST snapshot just
    # hasn't caught up with the WS yet, which is why the event is trusted.
    would_hold, qty = await size_it(eng, snapshot_rungs=[], m_pos=0.0)
    check("the resting order is counted", would_hold, 2750.0)
    check("follower targets it", qty, 31)


async def test_ladder_of_resting_entries_unaffected():
    print("\na real ladder still sums all its rungs")
    r = FakeRedis()
    eng = engine_with(r)
    # Two other rungs resting, plus this one arriving; master flat so far.
    would_hold, qty = await size_it(
        eng, snapshot_rungs=[("111", 1000.0), ("222", 1250.0)], m_pos=0.0)
    check("all three rungs counted", would_hold, 5000.0)
    check("sized on the whole ladder", qty, math.ceil(5000.0 * RATIO))


async def test_no_double_add_when_snapshot_already_has_it():
    print("\nthe snapshot already listing this order must not add it twice")
    r = FakeRedis()
    eng = engine_with(r)
    would_hold, _ = await size_it(
        eng, snapshot_rungs=[(MASTER_ORDER_ID, MASTER_QTY)], m_pos=0.0)
    check("counted once", would_hold, 2750.0)


async def test_filled_order_in_a_ladder_drops_only_itself():
    print("\none filled rung must not take the rest of the ladder with it")
    r = FakeRedis()
    eng = engine_with(r)
    await r.set(ledger.master_filled_key(MASTER_ORDER_ID), "1")
    # Its 2750 is now in the position; the other two rungs are still resting.
    would_hold, _ = await size_it(
        eng, snapshot_rungs=[("111", 1000.0), ("222", 1250.0)], m_pos=2750.0)
    check("position + the genuinely resting rungs", would_hold, 5000.0)


async def test_redis_failure_falls_back_to_old_behaviour():
    print("\na Redis hiccup must not stop the follower being sized at all")
    eng = engine_with(FakeRedis(raises=True))
    check("marker read reports not-filled", await eng._master_order_filled(MASTER_ORDER_ID), False)
    would_hold, qty = await size_it(eng, snapshot_rungs=[], m_pos=0.0)
    check("still sizes the order", qty, 31)
    # An over-size is trimmed by the reconciler; refusing to size leaves the
    # follower with no position at all, which is the worse failure.


async def test_marker_key_is_per_order():
    print("\nthe marker is keyed on the order id, not the symbol")
    check("key shape", ledger.master_filled_key("123"), "masterfilled:123")
    check("different orders, different keys",
          ledger.master_filled_key("123") == ledger.master_filled_key("124"), False)
    r = FakeRedis()
    eng = engine_with(r)
    await r.set(ledger.master_filled_key("999"), "1")
    check("a different order's fill does not mask this one",
          await eng._master_order_filled(MASTER_ORDER_ID), False)


async def main():
    print("=" * 74)
    print("stale sizing — a filled master order is not a resting one")
    print("=" * 74)
    for fn in (
        test_stale_event_after_fill_does_not_double_count,
        test_fresh_event_still_sizes_the_resting_order,
        test_ladder_of_resting_entries_unaffected,
        test_no_double_add_when_snapshot_already_has_it,
        test_filled_order_in_a_ladder_drops_only_itself,
        test_redis_failure_falls_back_to_old_behaviour,
        test_marker_key_is_per_order,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
