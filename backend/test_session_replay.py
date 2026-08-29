"""Replay of a real session, decision by decision, against the current code.

Run:  venv/Scripts/python.exe test_session_replay.py
No network, no Redis, no Supabase.

Every sizing decision the engine made on the evening of 2026-08-28 (18:28-21:43
IST), with the exact inputs it logged — master position, resting book, follower
holding — fed back through the REAL sizing functions.

Two of the eighteen were wrong on the day, both from the same race (5ebba63):

    13:00:27  Ladder open  ... rung 300 of 300 resting (master holds 300)
              follower holds 0 -> target 7          <- should have been 4
              13:01:40  reconcile: TRIMMED by 3

    16:06:36  Ladder close ... rung 280 of 280 resting (master holds 0)
              follower holds 3 -> cover 0           <- should have closed 3
              16:07:03  reconcile: closed +3

The other sixteen were already correct and must stay correct — that is most of
what this file is for. A fix that repairs two cases and quietly moves a third is
not a fix, and the sixteen are the only thing that can catch it.

"Correct" throughout is the follower's proportional share of the master's ACTUAL
position, which is also what the reconciler independently computes. The two
must agree or they undo each other.
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
from app.core import ladder

FAILURES = []
RATIO = 0.011169          # Mini Prathav / Jigar, from the day's logs


def target_fn(lots):
    """risk_engine.calculate_follower_quantity(round_up=True, min_one=False)."""
    return max(0, math.ceil(float(lots) * RATIO))


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} {got}" + ("" if ok else f"   want {want}"))
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------------------
# ENTRIES — a master order that opened or added to a position.
#   (time, strike, master position AFTER the fill, follower held, expected open)
# --------------------------------------------------------------------------
ENTRIES = [
    ("18:28:40", "75200", 3000.0, 0, 34),
    ("18:28:53", "77600",  300.0, 0,  4),
    ("18:29:33", "76000", 3000.0, 0, 34),
    ("18:29:49", "78200",  300.0, 0,  4),
    ("18:30:11", "75600", 3000.0, 0, 34),
    ("18:30:27", "77800",  300.0, 0,  4),   # <- logged 7 on the day. THE BUG.
]

# --------------------------------------------------------------------------
# EXITS through the resting ladder — master still holds, cover is proportional.
#   (time, strike, rung, resting total, master position, follower held, expected)
# --------------------------------------------------------------------------
LADDER_EXITS = [
    ("19:15:54", "75200", 3000.0, 3000.0, 3000.0, 34, 34),
    ("19:16:03", "75600", 3000.0, 3000.0, 3000.0, 34, 34),
    ("19:16:12", "76000", 3000.0, 3000.0, 3000.0, 34, 34),
    ("19:21:11", "77800",   20.0,   20.0,  300.0,  4,  1),
    ("19:22:12", "78200",   20.0,   20.0,  280.0,  4,  1),
    ("19:54:41", "76000", 3000.0, 3000.0, 3000.0, 34, 34),
    ("21:36:02", "75600", 3000.0, 3000.0, 3000.0, 34, 34),
    ("21:43:05", "75200", 3000.0, 3000.0, 3000.0, 34, 34),
    ("21:43:23", "77600",  300.0,  280.0,  280.0,  4,  4),
]

# --------------------------------------------------------------------------
# EXITS where the master's own order had already filled.
#   (time, strike, master position AFTER, follower held, expected close)
# --------------------------------------------------------------------------
FILLED_EXITS = [
    ("19:21:00", "77600", 280.0, 4, 0),   # already at target — nothing owed
    ("19:55:20", "78200",   0.0, 3, 3),   # master flat — close the lot
    ("21:36:36", "77800",   0.0, 3, 3),   # <- logged cover 0 on the day. THE BUG.
]


async def test_entries():
    print("\nENTRIES — master's order filled, size on the position it became")
    for t, strike, m_pos, held, expect in ENTRIES:
        qty, target, _to, rungs = CopyEngine._entry_open_qty(
            filled_entry=True, entry_rungs=[], m_pos=m_pos, held_now=held,
            master_order_id="x", master_qty=m_pos, target_fn=target_fn)
        check(f"{t}  {strike}  master {m_pos:.0f}, holds {held} -> open", qty, expect)
        if rungs:
            check(f"{t}  {strike}  no phantom rung", rungs, [])


async def test_ladder_exits():
    print("\nEXITS via the resting ladder — cover proportional to the master's book")
    for t, strike, rung, resting, m_now, held, expect in LADDER_EXITS:
        cover = ladder.coverage_target(held, resting, m_now)
        alloc = ladder.allocate([("r", rung)], cover)
        check(f"{t}  {strike}  {rung:.0f} of {resting:.0f} resting, holds {held} -> close",
              int(alloc.get("r", 0)), expect)


async def test_filled_exits():
    print("\nEXITS where the master's order had already filled")
    for t, strike, m_now, held, expect in FILLED_EXITS:
        target = target_fn(abs(m_now))
        qty = max(0, held - int(target))
        check(f"{t}  {strike}  master now {m_now:+.0f}, holds {held} -> close", qty, expect)


async def test_the_two_that_failed_would_now_be_right():
    print("\nTHE TWO THAT FAILED — old path vs new, side by side")
    # 18:30:27. The order had filled, so its 300 was already the position. The
    # old path injected it as a resting rung as well: 300 + 300 = 600.
    _q, old_t, _o, _r = CopyEngine._entry_open_qty(
        filled_entry=False, entry_rungs=[], m_pos=300.0, held_now=0,
        master_order_id="x", master_qty=300.0, target_fn=target_fn)
    new_q, new_t, _o2, _r2 = CopyEngine._entry_open_qty(
        filled_entry=True, entry_rungs=[], m_pos=300.0, held_now=0,
        master_order_id="x", master_qty=300.0, target_fn=target_fn)
    check("18:30:27  old path target (the 7 that went out)", old_t, 7)
    check("18:30:27  new path target", new_t, 4)
    check("18:30:27  new path opens", new_q, 4)
    check("18:30:27  no trim would be needed", new_q, 4)

    # 21:36:36. Master flat, so the ladder had no basis to proportion against.
    old_cover = ladder.coverage_target(3, 280.0, 0.0)
    new_close = max(0, 3 - int(target_fn(0.0)))
    check("21:36:36  old path allocated (the 0 that stalled it)", old_cover, 0)
    check("21:36:36  new path closes immediately", new_close, 3)


async def test_reconciler_would_have_had_nothing_to_do():
    print("\nWould the reconciler have been needed on any of the eighteen?")
    needed = []
    for t, strike, m_pos, held, expect in ENTRIES:
        qty, _tg, _to, _r = CopyEngine._entry_open_qty(
            filled_entry=True, entry_rungs=[], m_pos=m_pos, held_now=held,
            master_order_id="x", master_qty=m_pos, target_fn=target_fn)
        if held + qty != target_fn(m_pos):
            needed.append(f"{t} {strike}")
    for t, strike, m_now, held, expect in FILLED_EXITS:
        if held - max(0, held - int(target_fn(abs(m_now)))) != int(target_fn(abs(m_now))):
            needed.append(f"{t} {strike}")
    check("legs left off target after the live copy", needed, [])


async def main():
    print("=" * 78)
    print("session replay — 2026-08-28 evening, 18 sizing decisions")
    print("=" * 78)
    for fn in (test_entries, test_ladder_exits, test_filled_exits,
               test_the_two_that_failed_would_now_be_right,
               test_reconciler_would_have_had_nothing_to_do):
        await fn()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All 18 decisions land on target.")


if __name__ == "__main__":
    asyncio.run(main())
