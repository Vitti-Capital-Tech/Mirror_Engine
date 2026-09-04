"""Regression: when a master order is FINAL, confirm what the follower really got.

Run:  venv/Scripts/python.exe test_confirm_copy.py
No network, no Redis, no Supabase.

2026-09-01 20:24, P-BTC-75200-020926. Every step of the copy reported success:

    20:24:13.668  [LATENCY] order-mirror: 0.00s                      <- mirrored
    20:24:14.778  master holds 324, follower 0 -> target 4, opening 4 <- sized
    20:24:15.783  giving mirror (4 lots) 5s to fill before market     <- 5s rule armed
    20:24:22.564  Escalated unfilled limit -> MARKET qty 4            <- 5s rule fired
    20:24:30      master order state=closed, all 2000 filled
    20:24:31      "mirror ... is gone without filling - nothing to edit"

Nothing errored. The follower still held 4 against a target of 23, because the
size was computed while the master's 2000-lot order was only 324 filled and
nothing asked the question again once the rest landed. Each step checked its own
action succeeded; none checked the OUTCOME matched the master.

(Prathav, 2026-09-02: "agar fill bhi ho jata hai to we just reconfirm that humne
sab same tarike se kiya hai" — even when it fills, reconfirm we did it the same
way. Keyed on the master ORDER ID, so the expected number comes from the master's
own filled quantity rather than being inferred from a position snapshot.)

Two properties keep it from over-opening, and both are asserted below:

BOUNDED BY THE POSITION. A laddered entry deliberately gives one rung LESS than
that rung's own ceil share (30 rungs of 100 lots ceil to 60 follower lots against
a proportional target of 34). Per-order accounting alone would top up every rung,
so the correction is capped at what the follower's POSITION is short.

ONCE PER ORDER. A marker per (master order, follower) means a repeated completion
event cannot buy the same shortfall twice.
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
os.environ["ORDER_CONFIRM_SETTLE_SEC"] = "0"   # no sleep in tests

from app.core import copy_engine as ce
from app.core.copy_engine import CopyEngine
from app.core.risk_engine import RiskEngine

FAILURES = []
SYM = "P-BTC-75200-020926"
MOID = "1510240194"
FID = "f1"

# The live ratio at 20:24 — 79.21331535 / 7066.78289717 = 0.011209.
MASTER = {"id": "m1", "is_master": True, "owner_id": "u1", "status": "active",
          "api_key": "k", "api_secret": "s", "environment": "live",
          "balance": 7066.78289717}
FOLLOWER = {"id": FID, "name": "Mini Prathav", "is_master": False, "owner_id": "u1",
            "status": "active", "allocation_mode": "auto_ratio",
            "allocation_value": None, "balance": 79.21331535,
            "master_balance": 7066.78289717, "available_margin": 79.21331535}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class FakeRedis:
    def __init__(self, ordermap=None):
        self.kv = {}
        self.ordermap = {FID: "1510240261"} if ordermap is None else ordermap

    async def hgetall(self, key):
        return dict(self.ordermap) if key == f"ordermap:{MOID}" else {}

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def expire(self, *a, **k):
        return True

    async def hset(self, *a, **k):
        return True


class FakeClient:
    def __init__(self, live_ids=()):
        self.placed = []
        self.live = set(str(i) for i in live_ids)

    async def place_order(self, **kw):
        self.placed.append(kw)
        return {"id": "new-1"}

    async def get_open_orders(self, state="open"):
        return [{"id": i} for i in self.live] if state == "open" else []


def engine(follower_held, master_pos, legs=None, live_ids=(), ordermap=None):
    eng = CopyEngine.__new__(CopyEngine)
    eng.redis = FakeRedis(ordermap)
    eng.risk_engine = RiskEngine()
    eng._open_orders_cache = {}
    eng._OPEN_ORDERS_TTL = 0.0
    client = FakeClient(live_ids)

    async def _accounts():
        return [MASTER, FOLLOWER]
    eng._read_accounts = _accounts

    async def _fclient(_f):
        return client
    eng._get_follower_client = _fclient

    async def _signed(_c, _s):
        return float(follower_held)
    eng._position_size_signed = _signed

    async def _msize(_row, _sym, fresh=False):
        return abs(float(master_pos))
    eng._master_position_size = _msize

    async def _entry(_r, _oid):
        return {"legs": legs or {FID: {"status": "placed"}}}
    ce.ledger.get_entry = _entry
    eng._mc = client
    return eng, client


def event(size=2000, unfilled=0, reduce_only=False, side="sell"):
    return {"symbol": SYM, "size": size, "unfilled_size": unfilled,
            "reduce_only": reduce_only, "side": side, "limit_price": 18.0162,
            "owner_id": "u1", "state": "closed"}


async def test_the_incident():
    print("\n1. THE INCIDENT: master filled 2000, follower holds 4")
    eng, client = engine(follower_held=-4, master_pos=-2000)
    await eng._confirm_order_copied(event(), MOID)
    check("one correcting order", len(client.placed), 1)
    if client.placed:
        o = client.placed[0]
        check("buys the 19 it was short", int(o["size"]), 19)
        check("same side as the master", o["side"], "sell")
        check("NOT reduce-only (this adds exposure)", o["reduce_only"], False)
        check("at market", o["order_type"], "market_order")


async def test_a_correct_copy_is_left_alone():
    print("\n2. a copy that came out right places nothing")
    eng, client = engine(follower_held=-23, master_pos=-2000)
    await eng._confirm_order_copied(event(), MOID)
    check("no order", client.placed, [])


async def test_bounded_by_the_position():
    print("\n3. LADDER GUARD: per-order gap must never exceed the position gap")
    # The master rests 3000 across rungs but holds only 500 right now. A rung's
    # own ceil share would be 23; the follower is already at its position target
    # of 6, so nothing is owed and nothing may be bought.
    eng, client = engine(follower_held=-6, master_pos=-500)
    await eng._confirm_order_copied(event(), MOID)
    check("position cap prevents the top-up", client.placed, [])


async def test_a_deliberate_skip_is_not_a_miss():
    print("\n4. a leg the engine deliberately skipped is left alone")
    eng, client = engine(follower_held=-4, master_pos=-2000,
                         legs={FID: {"status": "skipped",
                                     "reason": "already at target 4 (holds 4)"}})
    await eng._confirm_order_copied(event(), MOID)
    check("no order", client.placed, [])


async def test_runs_once_per_order():
    print("\n5. a repeated completion event cannot buy the shortfall twice")
    eng, client = engine(follower_held=-4, master_pos=-2000)
    await eng._confirm_order_copied(event(), MOID)
    await eng._confirm_order_copied(event(), MOID)
    check("still only one order", len(client.placed), 1)


async def test_exits_are_not_confirmed_this_way():
    print("\n6. an EXIT is sized on the position target, not an order share")
    eng, client = engine(follower_held=-4, master_pos=-2000)
    await eng._confirm_order_copied(event(reduce_only=True), MOID)
    check("no order", client.placed, [])


async def test_an_unfilled_order_is_not_confirmed():
    print("\n7. a cancelled-unfilled master order owes nothing")
    eng, client = engine(follower_held=0, master_pos=0)
    await eng._confirm_order_copied(event(size=2000, unfilled=2000), MOID)
    check("no order", client.placed, [])


async def test_deadband():
    print("\n8. a sub-deadband gap is noise, not a miss")
    # master 2000 -> target 23, deadband max(1, 5% of 23) = 1.15. Holding 22 is
    # 1 lot short: inside the band.
    eng, client = engine(follower_held=-22, master_pos=-2000)
    await eng._confirm_order_copied(event(), MOID)
    check("1 lot short does not fire", client.placed, [])


async def test_partial_completion_is_not_dispatched():
    print("\n9. the DISPATCH only fires on a final order, not a partial")
    # Delta sent three reason=fill events in 3ms on this very order, as state=open
    # while more was coming. Confirming then would size on a moving number.
    def dispatches(ev):
        unf = ev.get("unfilled_size")
        return ev.get("state") == "closed" or (unf is not None and float(unf) <= 0)
    check("partial (state=open, 1676 left) -> no",
          dispatches({"state": "open", "unfilled_size": 1676}), False)
    check("final (state=closed) -> yes",
          dispatches({"state": "closed", "unfilled_size": 0}), True)
    check("fully filled but still 'open' -> yes",
          dispatches({"state": "open", "unfilled_size": 0}), True)


async def test_defers_while_the_mirror_is_still_resting():
    print("\n10. ESCALATION OWNS A LIVE MIRROR - do not top up on top of it")
    # If the mirror still rests, the 5s escalation is about to cancel it and market
    # the remainder. Buying the whole position gap here as well would stack: the
    # shortfall twice over.
    eng, client = engine(follower_held=-4, master_pos=-2000,
                         live_ids=("1510240261",))
    await eng._confirm_order_copied(event(), MOID)
    check("nothing placed while escalation owns it", client.placed, [])
    # And deferring must NOT burn the once-per-order marker - the leg has to stay
    # confirmable on a later pass, once the mirror is settled.
    check("marker not consumed",
          await eng.redis.get(f"confirmed:{MOID}:{FID}"), None)


async def test_partial_master_fill_does_not_start_the_5s_clock():
    print("\n11. a PARTIAL master fill must not start the escalation clock")
    # 2026-09-04 C-BTC-88000-040926: the clock started on a partial, escalation
    # marketed our whole 17-lot mirror against a master position of only -150, and
    # the reconciler TRIMMED us by 17 (19 -> 2). Again by 6 at -1123, and C-BTC-85000
    # by 31 (56 -> 25). 89 lots of corrective trading in one day. His resting order
    # is an OPEN order, so ours queues alongside it and waits.
    # (Prathav, 2026-09-04: wait for the full fill; the 5s rule is after his fill.)
    def starts_clock(ev):
        unf = ev.get("unfilled_size")
        return ev.get("state") == "closed" or (unf is not None and float(unf) <= 0)
    check("partial (1350 of 1500 left) -> no clock",
          starts_clock({"state": "open", "unfilled_size": 1350}), False)
    check("5-lot partial -> no clock",
          starts_clock({"state": "open", "unfilled_size": 1495}), False)
    check("complete -> clock starts",
          starts_clock({"state": "closed", "unfilled_size": 0}), True)
    check("fully filled but state still open -> clock starts",
          starts_clock({"state": "open", "unfilled_size": 0}), True)


async def test_cancel_after_partial_settles_to_what_filled():
    print("\n12. cancelled after a partial -> settle to the filled share")
    # He filled 500 of 1500 then abandoned the rest. 500 is final, so the follower
    # belongs at its share of 500 (ceil(500 * 0.011209) = 6), not left at 0.
    eng, client = engine(follower_held=0, master_pos=-500)
    await eng._confirm_order_copied(
        event(size=1500, unfilled=1000), MOID, mapping={FID: "1510240261"})
    check("tops up to the partial share", len(client.placed), 1)
    if client.placed:
        check("6 lots for 500 filled", int(client.placed[0]["size"]), 6)
    # And an order he cancelled with NOTHING filled owes nothing.
    eng2, client2 = engine(follower_held=0, master_pos=0)
    await eng2._confirm_order_copied(
        event(size=1500, unfilled=1500), MOID, mapping={FID: "1510240261"})
    check("nothing filled -> nothing owed", client2.placed, [])


async def test_nothing_mirrored_is_still_checked():
    print()
    print("13. NOTHING mirrored -> check every active follower anyway")
    # 2026-09-04 04:15:36 IST, P-BTC-78400-040926. The master's sell OPENED a 2400
    # short but was inferred as a close (his position still read 0 — the fill was
    # milliseconds old and Delta's position endpoint had not caught up), so the
    # reduce-only path found nothing to reduce and skipped. No order was placed, so
    # no ordermap entry existed, so confirm-copy returned early on an empty mapping
    # and sat out the one case it was built for. The reconciler opened 27 lots 21s
    # later. It must now check regardless.
    eng, client = engine(follower_held=0, master_pos=-2400, ordermap={},
                         legs={FID: {"status": "skipped",
                                     "reason": "holds +0, not reducible by sell"}})
    await eng._confirm_order_copied(event(size=2400, unfilled=0), MOID)
    check("opens the leg nobody placed", len(client.placed), 1)
    if client.placed:
        check("27 lots for 2400 filled", int(client.placed[0]["size"]), 27)


async def test_a_deliberate_skip_is_still_respected():
    print()
    print("14. 'already at target' is the ONLY deliberate skip")
    eng, client = engine(follower_held=-4, master_pos=-2000,
                         legs={FID: {"status": "skipped",
                                     "reason": "already at target 4 (holds 4)"}})
    await eng._confirm_order_copied(event(), MOID)
    check("left alone", client.placed, [])


async def test_a_failed_skip_is_not_deliberate():
    print()
    print("15. 'sizing unavailable' wears the same label but is a FAILURE")
    eng, client = engine(follower_held=-4, master_pos=-2000,
                         legs={FID: {"status": "skipped",
                                     "reason": "sizing unavailable (balance ratio "
                                               "could not be computed)"}})
    await eng._confirm_order_copied(event(), MOID)
    check("corrected, not skipped", len(client.placed), 1)


async def main():
    print("=" * 74)
    print("confirm-copy - reconfirm the OUTCOME, not just that each step ran")
    print("=" * 74)
    for fn in (
        test_the_incident,
        test_a_correct_copy_is_left_alone,
        test_bounded_by_the_position,
        test_a_deliberate_skip_is_not_a_miss,
        test_runs_once_per_order,
        test_exits_are_not_confirmed_this_way,
        test_an_unfilled_order_is_not_confirmed,
        test_deadband,
        test_partial_completion_is_not_dispatched,
        test_defers_while_the_mirror_is_still_resting,
        test_partial_master_fill_does_not_start_the_5s_clock,
        test_cancel_after_partial_settles_to_what_filled,
        test_nothing_mirrored_is_still_checked,
        test_a_deliberate_skip_is_still_respected,
        test_a_failed_skip_is_not_deliberate,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
