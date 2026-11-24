# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Two actions on one follower leg must not size off the same stale position.

Run:  venv/Scripts/python.exe test_concurrency_guards.py
No network, no Redis, no Supabase.

Found by an audit after 2026-11-24 11:09 IST (C-BTC-62600), when three pieces
of one master sell ran at the same time and the follower sold 503 instead of
201. The common cause: a live event that waited 5s for its leg ran UNLOCKED
alongside the copy ahead of it, and a copy takes longer than 5s.

  1. Live events now wait LIVE_ACQUIRE_TIMEOUT (30s) for the leg, so they run
     one after another; the reconciler still gives up after 5s and skips.
  2. The reconciler's OPEN re-reads the follower inside the lock (TOPUP, TRIM
     and CLOSE already did) — a live copy may have opened the leg meanwhile.
  3. A failed edit of a still-resting mirror cancels it before re-placing, so
     the follower never rests the same order twice.
"""
import asyncio
import inspect
import os
import sys
import time

os.environ["ORDER_CONFIRM_SETTLE_SEC"] = "0"   # no settle sleep in tests

import test_end_to_end as e2e
from test_end_to_end import Client, SYM

FAILURES = e2e.FAILURES
check = e2e.check


def test_live_events_wait_for_the_leg():
    print("\n1. A LIVE EVENT WAITS FOR THE COPY AHEAD OF IT")
    from app.core import leg_locks

    async def run():
        order = []

        async def first():
            async with leg_locks.hold("m-x", "SYM-A", "fill", timeout=2.0) as got:
                order.append(("first", got))
                await asyncio.sleep(0.4)          # a copy in progress
                order.append(("first-done", None))

        async def second():
            await asyncio.sleep(0.05)
            async with leg_locks.hold("m-x", "SYM-A", "fill", timeout=2.0) as got:
                order.append(("second", got))

        await asyncio.gather(first(), second())
        return order

    order = asyncio.run(run())
    check("the second event got the lock (it waited, it did not run unlocked)",
          ("second", True) in order, True)
    check("and only after the first had finished",
          order.index(("first-done", None)) < order.index(("second", True)), True)

    async def short():
        async def holder():
            async with leg_locks.hold("m-y", "SYM-B", "x", timeout=1.0):
                await asyncio.sleep(0.3)
        async def waiter():
            await asyncio.sleep(0.05)
            async with leg_locks.hold("m-y", "SYM-B", "x", timeout=0.1) as got:
                return got
        r = await asyncio.gather(holder(), waiter())
        return r[1]
    check("a short wait still gives up (the reconciler's skip still works)",
          asyncio.run(short()), False)

    import app.main as main_mod
    src = inspect.getsource(main_mod._run_fill) + inspect.getsource(main_mod._run_order)
    check("both live dispatchers use the long wait",
          src.count("timeout=leg_locks.LIVE_ACQUIRE_TIMEOUT"), 2)
    check("the long wait outlasts a copy (limit + 5s + market + confirm)",
          leg_locks.LIVE_ACQUIRE_TIMEOUT >= 20, True)


class FilledMeanwhile(Client):
    """The pass's top read says flat; by the time the lock is held, a live copy
    has opened the leg."""

    def __init__(self, ex, acct, share):
        super().__init__(ex, acct)
        self.share = share

    async def get_positions(self, strict=False):
        if strict:                                   # the top-of-pass read
            return []
        return [{"product_symbol": SYM, "size": self.share, "mark_price": 40,
                 "entry_price": 40}]                 # the last look


def test_reconcile_open_takes_a_last_look():
    print("\n2. THE RECONCILER'S OPEN RE-READS BEFORE BUYING")
    eng, ex, mc, fc = e2e.build()
    share = e2e.share(800)
    fm = FilledMeanwhile(ex, "f-algo3", share)
    eng.connection_manager.clients["f-algo3"] = fm

    async def run():
        ex.set_position("m-jd", SYM, 800)
        await eng._reconcile_positions(e2e.recon(eng, ex))      # first sighting
        eng._recon_open_ts.clear()
        await eng._reconcile_positions(e2e.recon(eng, ex))      # would open
        return [o for o in fm.placed if not o.get("reduce_only")]

    opens = asyncio.run(run())
    check("no second open on a leg the live copy already filled", len(opens), 0)


class EditFails(Client):
    async def edit_order(self, oid, product_id=None, limit_price=None, size=None):
        raise RuntimeError("429 Too Many Requests")

    async def get_order(self, oid):
        # Delta wraps it in "result"; the engine reads it that way.
        return {"result": await super().get_order(oid)}


def test_a_failed_edit_does_not_rest_twice():
    print("\n3. A FAILED EDIT OF A STILL-RESTING MIRROR")
    eng, ex, mc, fc = e2e.build()
    ef = EditFails(ex, "f-algo3")
    eng.connection_manager.clients["f-algo3"] = ef

    async def run():
        ex.set_position("m-jd", SYM, 800)
        ex.set_position("f-algo3", SYM, e2e.share(800))
        ex.place("m-jd", symbol=SYM, side="sell", size=400, order_type="limit_order", limit_price=45)
        moid = ex.resting("m-jd", SYM)[0]["id"]
        await eng.process_order_event(e2e.place_event(moid, "sell", 400, 45))
        first = len(ex.resting("f-algo3", SYM))
        await eng.process_order_event(e2e.place_event(moid, "sell", 400, 47, is_update=True))
        return first, ex.resting("f-algo3", SYM)

    first, resting = asyncio.run(run())
    check("one mirror before the edit", first, 1)
    check("still ONE resting mirror after the failed edit, not two", len(resting), 1)
    check("and it carries the new price", resting and float(resting[0]["limit_price"]) == 47.0, True)


def test_confirm_copy_waits_for_the_leg_and_rereads():
    """confirm-copy places a market OPEN. It used to run with no leg lock, sizing
    off a position read while another writer on the same leg (the reconciler's
    OPEN/TOPUP, a live copy) was about to change it — and both bought."""
    print("\n4. CONFIRM-COPY WAITS ITS TURN AND RE-READS")
    from app.core import leg_locks
    eng, ex, mc, fc = e2e.build()
    share = e2e.share(800)

    async def run():
        ex.set_position("m-jd", SYM, 800)               # master's order filled 800

        async def other_writer():
            async with leg_locks.hold("m-jd", SYM, "topup", timeout=2.0):
                await asyncio.sleep(0.3)                 # mid-correction ...
                ex.set_position("f-algo3", SYM, share)   # ... it filled the follower

        ev = {"master_order_id": "1710800001", "symbol": SYM, "side": "buy",
              "size": 800.0, "unfilled_size": 0, "order_type": "limit_order",
              "limit_price": 40, "state": "closed", "owner_id": "u1",
              "master_account_id": "m-jd", "product_id": 1}

        async def confirm():
            await asyncio.sleep(0.05)
            await eng._confirm_order_copied(ev, "1710800001", mapping={})

        await asyncio.gather(other_writer(), confirm())
        return ex.position("f-algo3", SYM), [o for o in fc.placed if not o.get("reduce_only")]

    pos, opens = asyncio.run(run())
    check("confirm-copy buys nothing on a leg that was just filled", len(opens), 0)
    check("the follower holds its share, not double", pos, float(share))


class LandsThenTimesOut(Client):
    """The first place REACHES the exchange, then the response is lost."""

    def __init__(self, ex, acct):
        super().__init__(ex, acct)
        self.calls = 0

    async def place_order(self, **kw):
        self.calls += 1
        resp = await super().place_order(**kw)
        o = self.ex.book.get(self.acct, {}).get(resp["id"])
        if o is not None:
            o["client_order_id"] = kw.get("client_order_id")
        if self.calls == 1:
            raise TimeoutError("read timed out")       # the order is on the book anyway
        return resp

    async def get_order_history(self, page_size=50):
        return []


def test_a_retry_after_a_lost_response_does_not_duplicate():
    print("\n5. THE FIRST PLACE LANDED, ITS RESPONSE WAS LOST")
    eng, ex, mc, fc = e2e.build()
    flaky = LandsThenTimesOut(ex, "f-algo3")

    async def run():
        resp = await eng._place_order_with_retry(
            flaky, delay=0.01, symbol=SYM, side="buy", size=10,
            order_type="limit_order", limit_price=40)
        return resp, ex.resting("f-algo3", SYM), flaky.calls

    resp, resting, calls = asyncio.run(run())
    check("ONE resting order, not two", len(resting), 1)
    check("it was found by its client_order_id, not placed again", calls, 1)
    check("and the caller gets that order back", bool((resp or {}).get("result", {}).get("id")), True)


def test_settle_after_cancel_counts_other_resting_exits():
    """The master's exit A filled (he is down to 500); the follower's copy of A is
    still resting, inside its 5s window. He cancels exit B. The settle used to
    market the whole gap (held - target) on top of A's resting copy — which then
    filled too, closing the follower past its share."""
    print("\n6. SETTLE AFTER A CANCEL, WITH ANOTHER EXIT STILL RESTING")
    eng, ex, mc, fc = e2e.build()
    held, after_a = e2e.share(800), e2e.share(500)

    async def run():
        ex.set_position("m-jd", SYM, 500)                 # exit A (300) filled
        ex.set_position("f-algo3", SYM, held)
        a = ex.place("f-algo3", symbol=SYM, side="sell", size=held - after_a,
                     order_type="limit_order", limit_price=45, reduce_only=True)["id"]
        b = ex.place("f-algo3", symbol=SYM, side="sell", size=100,
                     order_type="limit_order", limit_price=50, reduce_only=True)["id"]
        await eng.redis.hset("ordermap:1710900002", "f-algo3", b)
        await eng.process_order_event({
            "action": "cancel", "master_order_id": "1710900002", "symbol": SYM,
            "side": "sell", "order_type": "limit_order", "state": "cancelled",
            "owner_id": "u1", "master_account_id": "m-jd", "product_id": 1})
        markets = [o for o in fc.placed if o.get("order_type") == "market_order"]
        return markets, [o["id"] for o in ex.resting("f-algo3", SYM)], a

    markets, resting, a = asyncio.run(run())
    check("no market close on top of exit A's resting copy", len(markets), 0)
    check("exit A's copy is still resting to do its job", a in resting, True)


def _settled_engine():
    eng, ex, mc, fc = e2e.build()

    async def settled(*a, **k):
        return True                       # both master orders filled as taker
    eng._master_order_settled = settled
    return eng, ex, mc, fc


def test_two_master_entries_filling_seconds_apart():
    """Master limits A and B (800 each) both fill as taker within seconds. Our
    copy of A is placed but not yet filled when B is sized. B used to open
    "share of 1600 minus what the follower HOLDS (0)" — A's part a second time."""
    print("\n7. TWO MASTER ENTRIES FILLING SECONDS APART")
    eng, ex, mc, fc = _settled_engine()

    async def run():
        ex.set_position("m-jd", SYM, 1600)
        ex.place("f-algo3", symbol=SYM, side="buy", size=e2e.share(800),
                 order_type="limit_order", limit_price=40)            # our copy of A, resting
        before = len(fc.placed)
        await eng.process_order_event(e2e.place_event("1711000002", "buy", 800, 40))
        return sum(int(o.get("size") or 0) for o in fc.placed[before:] if o.get("side") == "buy")

    opened = asyncio.run(run())
    owed = e2e.share(1600) - e2e.share(800)
    check(f"B opens only its own part (~{owed}), not A's again", opened <= owed + 1, True)
    check("and does open it", opened >= owed - 1, True)


def test_two_master_exits_filling_seconds_apart():
    """Master exits A and B (300 each, from 800) both fill within seconds. Our
    copy of A is still resting when B is sized. B used to close "holds - share
    of 200" — A's part included — and the room cap only stops at zero."""
    print("\n8. TWO MASTER EXITS FILLING SECONDS APART")
    eng, ex, mc, fc = _settled_engine()

    async def run():
        ex.set_position("m-jd", SYM, 200)
        held = e2e.share(800)
        ex.set_position("f-algo3", SYM, held)
        a_part = held - e2e.share(500)
        ex.place("f-algo3", symbol=SYM, side="sell", size=a_part,
                 order_type="limit_order", limit_price=45, reduce_only=True)  # copy of A, resting
        before = len(fc.placed)
        await eng.process_order_event(e2e.place_event("1711000004", "sell", 300, 45, reduce_only=True))
        sold = sum(int(o.get("size") or 0) for o in fc.placed[before:] if o.get("side") == "sell")
        return sold, held, a_part

    sold, held, a_part = asyncio.run(run())
    owed = held - e2e.share(200) - a_part
    check(f"B closes only its own part (~{owed}), not A's again", sold <= owed + 1, True)
    check("so with A's copy the follower lands at its share, not below",
          held - sold - a_part >= e2e.share(200) - 1, True)


if __name__ == "__main__":
    for t in (test_live_events_wait_for_the_leg,
              test_two_master_entries_filling_seconds_apart,
              test_two_master_exits_filling_seconds_apart,
              test_settle_after_cancel_counts_other_resting_exits,
              test_a_retry_after_a_lost_response_does_not_duplicate,
              test_reconcile_open_takes_a_last_look,
              test_a_failed_edit_does_not_rest_twice,
              test_confirm_copy_waits_for_the_leg_and_rereads):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
