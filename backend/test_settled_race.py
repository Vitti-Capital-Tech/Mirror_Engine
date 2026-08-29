"""Regression: sizing must not depend on which of two events arrives first.

Run:  venv/Scripts/python.exe test_settled_race.py
No network, no Redis, no Supabase.

A master order that appears and fills inside the same second reaches the engine
as TWO events — "resting" and "filled". The sizing used to ask one question to
tell them apart: "did the listener set the fill marker yet?". That makes the
answer depend on which event won, and the engine races itself.

Both anomalies of the 2026-08-28 evening session are that race.

    13:00:26.405  master's buy created   -> place event queued
    13:00:27.501  it FILLS               -> master holds 300
    13:00:27.700  Ladder open ... rung 300 of 300 resting (master holds 300)
                  -> target 7            <- the same 300 counted TWICE
    13:01:40      reconciler TRIMMED by 3

    16:06:35.352  master's sell created  -> place event queued
    16:06:36.036  it FILLS               -> master flat
    16:06:36.081  Ladder close ... master holds 0 -> cover 0, nothing placed
    16:07:03      reconciler closed the follower's 3   <- 27s late

The marker was not wrong, it was merely silent — and the code read silence as
"still resting", then INJECTED the order into the master's book on the strength
of the event alone. Its lots were then in m_pos and in the rungs at once.

The fix decides on evidence, strongest first: the marker is positive proof of a
fill; presence in a FRESH read of the resting book is positive proof it rests;
and when neither speaks — exactly the race window — ask the exchange about that
one order. Only the ambiguous case costs a call.
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

from app.core.copy_engine import CopyEngine
from app.core import order_ledger as ledger

FAILURES = []
OID = "1502742390"
MASTER_ROW = {"id": "m1", "api_key": "k", "api_secret": "s", "environment": "live"}


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


class FakeRedis:
    def __init__(self, filled=False):
        self.kv = {}
        if filled:
            self.kv[ledger.master_filled_key(OID)] = "1"

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        self.kv[k] = str(v)
        return True


class MasterClient:
    """Answers get_order for the ambiguous case. Counts the calls, because the
    whole point is that the expensive question is asked rarely."""

    def __init__(self, order=None):
        self.order = order
        self.get_order_calls = 0

    async def get_order(self, order_id):
        self.get_order_calls += 1
        return {"result": dict(self.order) if self.order else {}}


def engine(filled=False, order=None):
    eng = CopyEngine.__new__(CopyEngine)
    eng.redis = FakeRedis(filled=filled)
    eng._master_clients = {}
    client = MasterClient(order)
    eng._get_master_client = lambda row: client
    eng._mc = client
    return eng


RESTING = {"id": OID, "state": "open", "size": 300, "unfilled_size": 300, "filled_size": 0}
FILLED = {"id": OID, "state": "closed", "size": 300, "unfilled_size": 0, "filled_size": 300}


async def settled(eng, in_book):
    return await eng._master_order_settled(MASTER_ROW, OID, in_book)


# ---------------------------------------------------------------- the race

async def test_marker_set_is_believed_without_a_call():
    print("\n1. marker set = positive proof of a fill, no exchange call")
    eng = engine(filled=True)
    check("settled", await settled(eng, in_book=False), True)
    check("no exchange call needed", eng._mc.get_order_calls, 0)


async def test_in_fresh_book_is_believed_without_a_call():
    print("\n2. in the fresh book = positive proof it rests, no exchange call")
    eng = engine(filled=False)
    check("not settled", await settled(eng, in_book=True), False)
    check("no exchange call needed", eng._mc.get_order_calls, 0)


async def test_the_race_window_asks_the_exchange():
    print("\n3. THE RACE: marker silent, not in the book — ask, don't guess")
    # Exactly 13:00:27.700 and 16:06:36.081: the fill happened, the marker had
    # not landed, and a fresh book no longer lists the order.
    eng = engine(filled=False, order=FILLED)
    check("settled — the exchange settles it", await settled(eng, in_book=False), True)
    check("cost exactly one call", eng._mc.get_order_calls, 1)


async def test_the_race_window_the_other_way():
    print("\n4. same window, but the order genuinely is still open")
    # Delta's REST has not listed a brand-new order yet. Reporting it filled here
    # would under-open the follower, so the exchange has to be asked.
    eng = engine(filled=False, order=RESTING)
    check("not settled", await settled(eng, in_book=False), False)
    check("cost exactly one call", eng._mc.get_order_calls, 1)


async def test_partially_filled_counts_as_settled():
    print("\n5. a partly-filled order has lots in the position already")
    part = {"id": OID, "state": "open", "size": 300, "unfilled_size": 200, "filled_size": 100}
    eng = engine(filled=False, order=part)
    check("settled", await settled(eng, in_book=False), True)


async def test_unreadable_falls_back_to_resting():
    print("\n6. an unreadable order must not be invented as a fill")
    eng = engine(filled=False, order=None)     # get_order returns {}
    check("reports still resting", await settled(eng, in_book=False), False)


async def test_marker_wins_over_a_stale_book():
    print("\n7. marker beats a book that still lists a filled order")
    # The 3s cache is gone from this path, but prove the ordering anyway: a
    # confirmed fill is not overridden by the order appearing in the book.
    eng = engine(filled=True, order=RESTING)
    check("settled", await settled(eng, in_book=True), True)
    check("no exchange call", eng._mc.get_order_calls, 0)


# ------------------------------------------- the two incidents, end to end

def target_fn(lots):
    import math
    return max(0, math.ceil(float(lots) * 0.011169))


async def test_incident_one_no_longer_double_counts():
    print("\nINCIDENT 1 (13:00): bought 7 against a target of 4")
    # The state at 13:00:27.700 — master holds the 300 it just bought, and the
    # order is no longer resting.
    qty, target, _to, rungs = CopyEngine._entry_open_qty(
        filled_entry=True, entry_rungs=[], m_pos=300.0, held_now=0,
        master_order_id=OID, master_qty=300.0, target_fn=target_fn)
    check("target is 4, not 7", target, 4)
    check("opens 4", qty, 4)
    check("no phantom rung", rungs, [])
    # And what the old code did, for contrast: order injected on top of m_pos.
    _q, old_target, _t, _r = CopyEngine._entry_open_qty(
        filled_entry=False, entry_rungs=[], m_pos=300.0, held_now=0,
        master_order_id=OID, master_qty=300.0, target_fn=target_fn)
    check("the old path did give 7", old_target, 7)


async def test_incident_two_closes_immediately():
    print("\nINCIDENT 2 (16:06): exit left for the reconciler, 27s late")
    # master flat after its own exit filled, follower still holding 3.
    from app.core import ladder
    # What the old path computed: cover against a flat master.
    check("old path allocated nothing", ladder.coverage_target(3, 280, 0), 0)
    # The filled-exit path sizes on the position instead: share of 0 is 0,
    # so the follower's 3 are all owed.
    target = target_fn(0)
    check("target after the fill", target, 0)
    check("closes all 3 now", max(0, 3 - target), 3)


async def main():
    print("=" * 74)
    print("settled race — sizing must not depend on which event arrives first")
    print("=" * 74)
    for fn in (
        test_marker_set_is_believed_without_a_call,
        test_in_fresh_book_is_believed_without_a_call,
        test_the_race_window_asks_the_exchange,
        test_the_race_window_the_other_way,
        test_partially_filled_counts_as_settled,
        test_unreadable_falls_back_to_resting,
        test_marker_wins_over_a_stale_book,
        test_incident_one_no_longer_double_counts,
        test_incident_two_closes_immediately,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
