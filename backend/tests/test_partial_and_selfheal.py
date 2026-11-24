# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A part-filled master order is still resting; a self-healed cancel never guesses.

Run:  venv/Scripts/python.exe test_partial_and_selfheal.py
No network, no Redis, no Supabase.

Both found from JD algo4 -> JD algo3's Telegram alerts on 2026-11-24 (IST):

  07:41 / 07:56  JD algo4's 500-lot limit buy filled 15 on arrival and rested
                 485. The listener marked the order "filled" on that FIRST fill,
                 and the engine's settled-check counted any filled_size > 0 as
                 settled — so the follower opened 15 and no resting copy was
                 placed for the other 485. confirm-copy bought 489 at market 30s
                 later when the rest filled.

  12:00:44       a cancel with no follower mapping and no price self-healed by
                 "any same-side plain order on the product" — and cancelled
                 JD algo3's copy of a DIFFERENT master order (1710310326). The
                 30s sweep put it back at 12:01.
"""
import asyncio
import json
import sys

import test_end_to_end as e2e
from test_end_to_end import Client, SYM

FAILURES = e2e.FAILURES
check = e2e.check


# ------------------------------------------------------------------- 1. listener
def test_a_partial_fill_does_not_mark_the_order_filled():
    print("\n1. THE LISTENER MARKS AN ORDER FILLED ONLY WHEN IT IS")
    from app.core.trade_listener import TradeListener
    from app.core.order_ledger import master_filled_key

    class R:
        def __init__(self): self.kv = {}
        async def set(self, k, v, ex=None, nx=False): self.kv[k] = v; return True
        async def lpush(self, k, v): pass
        async def get(self, k): return self.kv.get(k)

    tl = TradeListener.__new__(TradeListener)
    tl.redis = R()
    tl.master_account = {"id": "m-jd", "owner_id": "u1", "name": "JD algo4"}
    tl._owner_id = lambda: "u1"
    base = {"id": 1709746248, "product_symbol": SYM, "product_id": 1, "side": "buy",
            "order_type": "limit_order", "limit_price": "110", "size": 500}

    async def run():
        await tl.on_order_fill(dict(base, state="open", reason="fill", unfilled_size=485))
        after_partial = master_filled_key(1709746248) in tl.redis.kv
        await tl.on_order_fill(dict(base, state="closed", reason="fill", unfilled_size=0))
        after_full = master_filled_key(1709746248) in tl.redis.kv
        return after_partial, after_full

    p, f = asyncio.run(run())
    check("a first, partial fill does NOT mark it filled (485 still rest)", p, False)
    check("the final fill does", f, True)


# ------------------------------------------------------------------- 2. engine
class LaggingMaster(Client):
    """The master's REST book has not caught up with the order yet; asking for
    the order itself says it is open with 15 of 500 filled."""

    async def get_open_orders(self, state=None):
        return []

    async def get_order(self, oid):
        return {"result": {"id": str(oid), "state": "open", "size": 500,
                           "unfilled_size": 485, "product_symbol": SYM}}


def _resting_buys(ex, acct):
    return [o for o in ex.resting(acct, SYM) if o["side"] == "buy"]


def test_a_part_filled_order_gets_a_resting_copy():
    print("\n2. A PART-FILLED MASTER ORDER IS MIRRORED AS RESTING")
    eng, ex, mc, fc = e2e.build()
    lm = LaggingMaster(ex, "m-jd")
    eng._get_master_client = lambda row: lm

    async def run():
        ex.set_position("m-jd", SYM, 15)              # 15 of his 500 filled on arrival
        before = len(fc.placed)
        await eng.process_order_event(e2e.place_event("1709746248", "buy", 500, 110))
        new = fc.placed[before:]
        return new, _resting_buys(ex, "f-algo3")

    new, resting = asyncio.run(run())
    check("the follower gets a RESTING copy, not only the 15 that filled",
          sum(int(o["size"]) for o in resting) >= e2e.share(500) - e2e.share(15) - 1, True)
    check("sized to its share of the whole order, not of the 15",
          sum(int(o.get("size") or 0) for o in new) <= e2e.share(500) + 1, True)


def test_a_part_filled_rung_counts_only_what_still_rests():
    print("\n3. THE RESTING BOOK COUNTS A PART-FILLED ORDER BY WHAT IS LEFT")
    eng, ex, mc, fc = e2e.build()

    async def run():
        ex.set_position("m-jd", SYM, 15)
        moid = ex.place("m-jd", symbol=SYM, side="buy", size=500,
                        order_type="limit_order", limit_price=110)["id"]
        ex.book["m-jd"][moid]["unfilled_size"] = 485  # 15 already in his position
        await eng.process_order_event(e2e.place_event(moid, "buy", 500, 110))
        return sum(int(o["size"]) for o in _resting_buys(ex, "f-algo3"))

    resting = asyncio.run(run())
    check("the copy is the share of 500, not of 515 (15 counted twice)",
          abs(resting - e2e.share(500)) <= 1, True)


# ------------------------------------------------------------------- 3. self-heal
def test_a_self_healed_cancel_never_takes_another_orders_copy():
    print("\n4. A CANCEL WITH NO MAPPING CANCELS ONLY THE EXACT COPY")
    eng, ex, mc, fc = e2e.build()

    async def run():
        ex.set_position("m-jd", SYM, 500)
        ex.set_position("f-algo3", SYM, e2e.share(500))
        other = ex.place("f-algo3", symbol=SYM, side="sell", size=200,
                         order_type="limit_order", limit_price=45, reduce_only=True)["id"]
        ex.book["f-algo3"][other]["client_order_id"] = "mo1710310326_a1b2c3"   # copy of ANOTHER order
        ev = {"action": "cancel", "master_order_id": "1710403452", "symbol": SYM,
              "side": "sell", "order_type": "limit_order", "state": "cancelled",
              "owner_id": "u1", "master_account_id": "m-jd", "product_id": None}
        await eng.process_order_event(ev)                                # no price
        await eng.process_order_event(dict(ev, limit_price=45))          # same price
        still_there = other in [o["id"] for o in ex.resting("f-algo3", SYM)]

        mine = ex.place("f-algo3", symbol=SYM, side="sell", size=100,
                        order_type="limit_order", limit_price=50, reduce_only=True)["id"]
        ex.book["f-algo3"][mine]["client_order_id"] = "mo1710403452_d4e5f6"   # ITS copy
        await eng.process_order_event(ev)
        ids = [o["id"] for o in ex.resting("f-algo3", SYM)]
        return still_there, mine not in ids, other in ids

    still, mine_gone, other_kept = asyncio.run(run())
    check("another order's copy is NOT cancelled (no price, or same price)", still, True)
    check("the cancelled order's own tagged copy IS cancelled", mine_gone, True)
    check("and the other copy is still left alone", other_kept, True)


def test_mirrors_carry_their_master_order_id():
    print("\n5. A MIRROR NAMES THE MASTER ORDER IT COPIES")
    eng, ex, mc, fc = e2e.build()

    async def run():
        ex.set_position("m-jd", SYM, 800)
        ex.set_position("f-algo3", SYM, e2e.share(800))
        moid = ex.place("m-jd", symbol=SYM, side="sell", size=400,
                        order_type="limit_order", limit_price=45)["id"]
        await eng.process_order_event(e2e.place_event(moid, "sell", 400, 45))
        return moid, [o.get("client_order_id") for o in fc.placed]

    moid, cids = asyncio.run(run())
    check("tagged mo<master order id>_...", any(str(c or "").startswith(f"mo{moid}_") for c in cids), True)
    check("within Delta's 32-character limit", all(len(str(c or "")) <= 32 for c in cids), True)


if __name__ == "__main__":
    for t in (test_a_partial_fill_does_not_mark_the_order_filled,
              test_a_part_filled_order_gets_a_resting_copy,
              test_a_part_filled_rung_counts_only_what_still_rests,
              test_a_self_healed_cancel_never_takes_another_orders_copy,
              test_mirrors_carry_their_master_order_id):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
