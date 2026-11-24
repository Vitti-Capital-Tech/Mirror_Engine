# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A master editing only the PRICE of an order must not resize the follower's copy.

Run:  cd backend && python tests/test_edit_keeps_size.py
No network, no Redis, no Supabase.

2026-11-24 (IST), C-BTC-63800, JD algo4 -> JD algo3:

  07:34  JD algo4 sold 3000; JD algo3 sold 2998 (its share)
  07:58  JD algo4 rested a buy 3000 to close it all; JD algo3's copy: 2998
  08:18  JD algo4 moved the PRICE of that close. The edit re-sent the
         follower's size too, recomputed as 3000 x the ratio at that moment —
         which had drifted — so the copy became 2997
  11:35  the close filled: JD algo4 flat, JD algo3 left holding 1 lot, closed
         by the reconciler 15s later (another Telegram message)

The size is now re-sent only when the master actually changed his size.
"""
import asyncio
import sys

import test_end_to_end as e2e
from test_end_to_end import SYM

FAILURES = e2e.FAILURES
check = e2e.check


def _setup():
    eng, ex, mc, fc = e2e.build()

    async def place():
        ex.set_position("m-jd", SYM, -3000)
        ex.set_position("f-algo3", SYM, -e2e.share(3000))
        moid = ex.place("m-jd", symbol=SYM, side="buy", size=3000, order_type="limit_order",
                        limit_price=1.2, reduce_only=True)["id"]
        await eng.process_order_event(e2e.place_event(moid, "buy", 3000, 1.2, reduce_only=True))
        return moid
    return eng, ex, fc, place


def test_a_price_only_edit_keeps_the_follower_size():
    print("\n1. THE MASTER MOVES THE PRICE ONLY (C-BTC-63800, 08:18)")
    eng, ex, fc, place = _setup()
    saved = e2e.FOLLOWER["balance"]

    async def run():
        moid = await place()
        before = [o for o in ex.resting("f-algo3", SYM)][0]["size"]
        e2e.FOLLOWER["balance"] = saved * 0.997           # the ratio drifts a little
        await eng.process_order_event(e2e.place_event(moid, "buy", 3000, 1.1,
                                                      reduce_only=True, is_update=True))
        after = ex.resting("f-algo3", SYM)
        return before, after, fc.edited

    try:
        before, after, edits = asyncio.run(run())
    finally:
        e2e.FOLLOWER["balance"] = saved
    check("the copy covered the follower's whole holding before the edit",
          int(before), e2e.share(3000))
    check("one copy still resting", len(after), 1)
    check("the price moved", after and float(after[0]["limit_price"]) == 1.1, True)
    check("the size did NOT change", after and int(after[0]["size"]), int(before))
    check("the edit sent no size", edits and edits[-1][2], None)


def test_a_size_edit_still_resizes_the_copy():
    print("\n2. THE MASTER CHANGES THE SIZE — THE COPY FOLLOWS")
    eng, ex, fc, place = _setup()

    async def run():
        moid = await place()
        await eng.process_order_event(e2e.place_event(moid, "buy", 1500, 1.2,
                                                      reduce_only=True, is_update=True))
        return ex.resting("f-algo3", SYM), fc.edited

    after, edits = asyncio.run(run())
    check("the edit sent a size", bool(edits) and edits[-1][2] is not None, True)
    check("the copy was resized down", after and int(after[0]["size"]) < e2e.share(3000), True)


if __name__ == "__main__":
    for t in (test_a_price_only_edit_keeps_the_follower_size,
              test_a_size_edit_still_resizes_the_copy):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
