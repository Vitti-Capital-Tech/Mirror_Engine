"""Regression: two escalations on one follower order must not both market it.

Run:  venv/Scripts/python.exe test_escalation_race.py
No network, no Redis, no Supabase.

The incident (live, 2026-08-28, C-BTC-82000-280826):

    04:01:25.307  Escalated unfilled limit -> MARKET qty 20 (order 1501921046)
    04:01:25.307  WARN  Escalation cancel failed for 1501920742: 400
    04:01:28.100  ERROR Escalation market order failed [sell qty=20]: 400

Two escalation tasks, one follower order. One is spawned when the mirror is
placed; the other when the master's own limit fills, because the first returns
without acting while the master is still resting. Both then sleep
ESCALATE_WAIT_SEC and wake up wanting to cancel-and-market the same 20 lots.

The follower ended correct at 20 only because Delta rejected the second market
order. Nothing in the code stopped it. The guard asked

    if not cancelled and self._order_done(od): return

and a CANCELLED-but-unfilled order is not "done" — state 'cancelled',
unfilled_size still 20 — so the loser of the race fell straight through and
marketed the full 20 again. Had Delta accepted it, the follower would have been
short 40 against a target of 20.

The same guard had a second hole with the same shape: a cancel that merely
FAILED while the limit was still resting also fell through, marketing on top of
a live order.

Both close the same way: CANCELLING IS THE CLAIM. Only one caller can
successfully cancel a live order, so a successful cancel is the licence to
market and nothing else is. A Redis SET NX claim in front of it makes the
exclusivity explicit instead of leaning on Delta's error semantics.
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

import app.core.copy_engine as ce
from app.core.copy_engine import CopyEngine

FAILURES = []

SYMBOL = "C-BTC-82000-280826"
ORDER_ID = "1501920742"          # the follower's resting mirror
PRODUCT_ID = 136320
QTY = 20

FOLLOWER = {"id": "f1", "name": "Mini Prathav", "owner_id": "o1"}


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


class FakeRedis:
    def __init__(self, broken=False):
        self.kv = {}
        self.broken = broken

    async def get(self, k):
        if self.broken:
            raise RuntimeError("redis down")
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if self.broken:
            raise RuntimeError("redis down")
        if nx and k in self.kv:
            return False
        self.kv[k] = str(v)
        return True


class SharedBook:
    """One order book both racing tasks act on, so the race is real.

    cancel_order succeeds only for an order that is still open — which is what
    makes a successful cancel a mutex, and is exactly Delta's behaviour (it
    returned 400 to the loser in the incident).
    """

    def __init__(self, state="open", filled=0):
        self.order = {
            "id": ORDER_ID, "state": state, "size": QTY,
            "unfilled_size": QTY - filled, "filled_size": filled,
            "product_id": PRODUCT_ID, "side": "sell",
        }
        self.market_orders = []
        self.cancels = 0

    async def cancel_order(self, order_id, product_id=None):
        self.cancels += 1
        if (self.order.get("state") or "").lower() not in ("open", "pending"):
            raise RuntimeError("400 Bad Request: order is not open")
        self.order["state"] = "cancelled"
        return {"result": {"id": order_id}}

    async def get_order(self, order_id):
        return {"result": dict(self.order)}

    async def place_order(self, **kw):
        self.market_orders.append(kw)
        return {"result": {"id": f"mkt{len(self.market_orders)}"}}

    async def get_positions(self):
        return []


def build_engine(redis=None):
    eng = CopyEngine.__new__(CopyEngine)
    eng.redis = redis if redis is not None else FakeRedis()
    eng._master_pos_cache = {}
    eng._MASTER_POS_TTL = 3.0
    eng._master_signed_cache = {}
    eng._master_clients = {}
    eng._get_master_client = lambda row: None
    return eng


async def escalate(eng, book, **over):
    """Run one escalation task against the shared book.

    master_row is None and master_order_id omitted, so the 'is the master still
    resting?' branch is skipped and we land straight on the cancel-and-market
    section under test. reduce_only False keeps the close-sizing cap out of it.
    """
    kw = dict(follower=FOLLOWER, client=book, order_id=ORDER_ID,
              product_id=PRODUCT_ID, symbol=SYMBOL, side="sell", qty=QTY,
              reduce_only=False, master_row=None, limit_price=46.0)
    kw.update(over)
    return await eng._escalate_unfilled_limit(**kw)


async def test_two_escalations_market_once():
    print("\nTHE BUG: two tasks, one order — only ONE market order may go out")
    eng = build_engine()
    book = SharedBook()
    await asyncio.gather(escalate(eng, book), escalate(eng, book))
    check("exactly one market order", len(book.market_orders), 1)
    check("for the right size", int(book.market_orders[0]["size"]) if book.market_orders else None, QTY)
    check("NOT the 40 lots the race could have sold",
          sum(int(o["size"]) for o in book.market_orders), QTY)


async def test_three_way_race_still_markets_once():
    print("\nthe same must hold with more contenders")
    eng = build_engine()
    book = SharedBook()
    await asyncio.gather(*(escalate(eng, book) for _ in range(3)))
    check("still exactly one market order", len(book.market_orders), 1)


async def test_loser_of_the_race_does_not_market():
    print("\nthe loser: order already cancelled by someone else, never filled")
    # The exact state the old guard mis-read: 'cancelled' with unfilled_size
    # still 20, which _order_done() reports as NOT done.
    eng = build_engine()
    book = SharedBook(state="cancelled")
    check("_order_done says not done (this is why it slipped through)",
          CopyEngine._order_done(book.order), False)
    await escalate(eng, book)
    check("nothing marketed", book.market_orders, [])


async def test_cancel_failure_on_a_live_order_does_not_market():
    print("\nthe other hole: cancel failed while the limit is STILL resting")
    eng = build_engine()
    book = SharedBook()

    async def failing_cancel(order_id, product_id=None):
        book.cancels += 1
        raise RuntimeError("network blip")

    book.cancel_order = failing_cancel
    await escalate(eng, book)
    check("did not market on top of a live limit", book.market_orders, [])
    check("the limit is left resting for the reconciler",
          book.order["state"], "open")


async def test_filled_during_the_wait_is_not_marketed():
    print("\nunchanged: an order that filled during the wait is left alone")
    eng = build_engine()
    book = SharedBook(state="closed", filled=QTY)
    await escalate(eng, book)
    check("nothing marketed", book.market_orders, [])
    check("and no cancel was attempted", book.cancels, 0)


async def test_lone_escalation_still_markets():
    print("\nunchanged: the ordinary single escalation still forces the fill")
    eng = build_engine()
    book = SharedBook()
    await escalate(eng, book)
    check("one market order", len(book.market_orders), 1)
    check("full size", int(book.market_orders[0]["size"]), QTY)
    check("same side as the mirror", book.market_orders[0]["side"], "sell")
    check("as a market order", book.market_orders[0]["order_type"], "market_order")


async def test_partial_fill_markets_only_the_remainder():
    print("\nunchanged: a partly-filled limit escalates only what is left")
    eng = build_engine()
    book = SharedBook(filled=8)          # 8 of 20 done
    await escalate(eng, book)
    check("markets the remaining 12", int(book.market_orders[0]["size"]), 12)


async def test_redis_down_still_escalates_once():
    print("\nRedis down: escalation must still work, and still only once")
    # The claim answers True when Redis is unreachable — refusing would strand
    # the follower's limit. The cancel-is-the-claim rule still holds the line.
    eng = build_engine(redis=FakeRedis(broken=True))
    book = SharedBook()
    await asyncio.gather(escalate(eng, book), escalate(eng, book))
    check("still escalates", len(book.market_orders), 1)


async def test_claim_is_per_order():
    print("\nthe claim must not leak between different orders")
    eng = build_engine()
    check("first order wins its claim", await eng._claim_escalation("A"), True)
    check("same order cannot be claimed twice", await eng._claim_escalation("A"), False)
    check("a different order is unaffected", await eng._claim_escalation("B"), True)


async def test_claim_not_taken_when_the_task_bails_early():
    print("\na task that never acts must not hold the claim against a later one")
    # An escalation that returns early (order already done) must leave the order
    # claimable, or the real escalation that follows would be blocked.
    eng = build_engine()
    book = SharedBook(state="closed", filled=QTY)
    await escalate(eng, book)
    check("claim still free afterwards", await eng._claim_escalation(ORDER_ID), True)


async def main():
    print("=" * 74)
    print("escalation race — one order, one market order")
    print("=" * 74)
    ce.ESCALATE_WAIT_SEC = 0        # no real waiting in tests
    for fn in (
        test_two_escalations_market_once,
        test_three_way_race_still_markets_once,
        test_loser_of_the_race_does_not_market,
        test_cancel_failure_on_a_live_order_does_not_market,
        test_filled_during_the_wait_is_not_marketed,
        test_lone_escalation_still_markets,
        test_partial_fill_markets_only_the_remainder,
        test_redis_down_still_escalates_once,
        test_claim_is_per_order,
        test_claim_not_taken_when_the_task_bails_early,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
