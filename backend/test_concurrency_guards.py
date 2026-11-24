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
import sys
import time

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


if __name__ == "__main__":
    for t in (test_live_events_wait_for_the_leg,
              test_reconcile_open_takes_a_last_look,
              test_a_failed_edit_does_not_rest_twice):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
