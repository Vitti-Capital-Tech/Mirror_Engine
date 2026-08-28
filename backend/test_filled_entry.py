"""Regression: a master ENTRY that has already filled must still be copied NOW.

Run:  venv/Scripts/python.exe test_filled_entry.py
No network, no Redis, no Supabase.

The incident (live, 2026-08-27/28 — 11 times in 7.5 hours):

    22:37:55  master's 3000-lot C-BTC-83000-280826 sell FILLS (taker)
    22:37:56  the "state=open" event for the SAME order id arrives
    22:37:59  "Ladder open ... rung 3000 of 0 resting ... to open 34, this rung 0"
              "No open needed for Mini Prathav: holds 0 of target 34"
    22:38:22  reconciler opens 34 at MARKET                      <- 26s late

39f154e correctly stopped injecting a FILLED order into the master's resting
book — its lots are already in the position, and counting them twice sized the
follower at double. But not injecting leaves `entry_rungs` holding only what
still RESTS, which for a taker fill is nothing at all. ladder.allocate([], 34)
returns {}, the rung scores 0, and the live copy places NOTHING: no resting order
on the follower, entry only via the 15s reconciler, at market, 20-30s late.

Worse when the reconciler then declines to act. C-BTC-84000-280826 held 12 lots
against a target of 23 for the rest of the session, because the price had drifted
past the top-up guard — the leg simply never recovered.

The fix is the exact mirror of the exit-side `filled_exit` branch (0e1c7e5): a
filled entry needs no ladder maths, so size the follower on the master's POSITION
— its proportional share of what now exists — which is also what the reconciler
computes, so the two cannot disagree.
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

from app.core.copy_engine import CopyEngine

FAILURES = []

# The live incident's numbers.
MASTER_ORDER_ID = "1501612297"
RATIO = 0.011194  # Mini Prathav / master, unchanged throughout the logs


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def target_fn(lots):
    """Stand-in for risk_engine.calculate_follower_quantity(round_up=True,
    min_one=False) — ceil the proportional share, and 0 stays 0."""
    return max(0, math.ceil(float(lots) * RATIO))


def size(filled_entry, rungs, m_pos, held_now, master_qty=3000.0):
    """Drive the REAL decision function, not a copy of it."""
    return CopyEngine._entry_open_qty(
        filled_entry=filled_entry, entry_rungs=rungs, m_pos=m_pos,
        held_now=held_now, master_order_id=MASTER_ORDER_ID,
        master_qty=master_qty, target_fn=target_fn,
    )


async def test_the_incident():
    print("\nTHE BUG: master's entry filled as taker, nothing rests, follower flat")
    # Exactly the 22:37:59 state: the order filled, so the snapshot is empty and
    # the fill marker is set. The master holds the 3000 it just bought.
    qty, target, to_open, rungs = size(True, [], m_pos=3000.0, held_now=0)
    check("target is the follower's share of the position", target, 34)
    check("the live copy opens all 34 NOW", qty, 34)
    check("NOT the 0 that actually went out", qty == 0, False)
    check("no phantom rung invented", rungs, [])


async def test_the_partial_top_up_that_never_recovered():
    print("\nC-BTC-84000: master added to a position the follower was short of")
    # 00:15:56 — master holds 2000 after the fill, follower holds 12, target 23.
    # The live copy scored this rung 0; the reconciler then refused to top up
    # because the price had drifted, so the follower stayed at 12 all session.
    qty, target, _to, _r = size(True, [], m_pos=2000.0, held_now=12, master_qty=500.0)
    check("target", target, 23)
    check("tops the follower up by 11", qty, 11)


async def test_filled_entry_does_not_front_run_rungs_still_resting():
    print("\na filled rung must not pull the REST of the ladder forward")
    # This 1000 filled; 4000 more still rests below. The follower belongs at its
    # share of the 1000 that exists, not of the 5000 that might.
    qty, target, _to, rungs = size(
        True, [("111", 2500.0), ("222", 1500.0)], m_pos=1000.0, held_now=0,
        master_qty=1000.0)
    check("sized on the position only", target, math.ceil(1000 * RATIO))
    check("opens only what filled", qty, math.ceil(1000 * RATIO))
    check("the resting rungs are left alone", rungs, [("111", 2500.0), ("222", 1500.0)])


async def test_already_at_target_still_skips():
    print("\na filled entry the follower already matches must still place nothing")
    qty, target, _to, _r = size(True, [], m_pos=3000.0, held_now=34)
    check("target", target, 34)
    check("nothing to open", qty, 0)


async def test_over_held_never_goes_negative():
    print("\nan over-held follower must not produce a negative open")
    qty, _t, to_open, _r = size(True, [], m_pos=3000.0, held_now=60)
    check("qty floors at 0", qty, 0)
    check("to_open floors at 0", to_open, 0)


# ---- what must NOT change: the resting-ladder path 39f154e and c2c0d20 built ----

async def test_resting_order_is_still_injected():
    print("\nSTILL RESTING: the arriving order is counted even if the snapshot lags")
    # The REST listing hasn't caught up with the WS, so the snapshot is empty —
    # the event is authoritative that this order rests.
    qty, target, to_open, rungs = size(False, [], m_pos=0.0, held_now=0)
    check("injected as a rung", rungs, [(MASTER_ORDER_ID, 3000.0)])
    check("target from would_hold", target, 34)
    check("the whole 34 goes to the only rung", qty, 34)
    check("to_open", to_open, 34)


async def test_real_ladder_still_splits_by_largest_remainder():
    print("\nSTILL RESTING: a 3-rung ladder is sized as ONE ladder, not per rung")
    # Master flat, three rungs: this one (3000) plus 1000 and 1250 resting.
    qty, target, to_open, rungs = size(
        False, [("111", 1000.0), ("222", 1250.0)], m_pos=0.0, held_now=0)
    check("all three rungs counted", len(rungs), 3)
    check("target on the whole book", target, math.ceil(5250 * RATIO))
    check("the rungs sum to exactly the target", to_open, target)
    # Proportional share of 3000/5250, not ceil(3000 * ratio) in isolation —
    # per-rung ceiling is what over-bought the follower before c2c0d20.
    check("this rung gets its proportional share", qty, 34)
    check("under the per-rung ceil", qty <= math.ceil(3000 * RATIO), True)


async def test_snapshot_already_listing_the_order_does_not_double_add():
    print("\nSTILL RESTING: the snapshot already listing this order must not dupe it")
    _q, target, _to, rungs = size(
        False, [(MASTER_ORDER_ID, 3000.0)], m_pos=0.0, held_now=0)
    check("counted once", len(rungs), 1)
    check("target on 3000, not 6000", target, 34)


async def test_resting_ladder_respects_what_the_follower_holds():
    print("\nSTILL RESTING: an already-full follower is not topped up again")
    qty, target, _to, _r = size(False, [], m_pos=0.0, held_now=34)
    check("target", target, 34)
    check("nothing left to open", qty, 0)


async def main():
    print("=" * 74)
    print("filled entry — a master entry that already filled must still copy NOW")
    print("=" * 74)
    for fn in (
        test_the_incident,
        test_the_partial_top_up_that_never_recovered,
        test_filled_entry_does_not_front_run_rungs_still_resting,
        test_already_at_target_still_skips,
        test_over_held_never_goes_negative,
        test_resting_order_is_still_injected,
        test_real_ladder_still_splits_by_largest_remainder,
        test_snapshot_already_listing_the_order_does_not_double_add,
        test_resting_ladder_respects_what_the_follower_holds,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
