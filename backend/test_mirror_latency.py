"""Regression: a no-op mirror must not pay for sizing it will not use.

Run:  venv/Scripts/python.exe test_mirror_latency.py
No network, no Redis, no Supabase.

Unlike test_duplicate_mirror.py and test_stale_sizing.py — which replay the
engine's logic in the test file — this drives the REAL `_mirror_place` and counts
what it asks the exchange for. That distinction matters: both of those tests kept
passing while the entry path was dead (1003ecc) and while every no-op mirror was
making five exchange round trips, because a replayed copy of the logic cannot
observe either fault.

Measured by this file against both orderings: a no-op used to cost the master
get_positions + get_open_orders(open) + get_open_orders(pending) + get_positions
and the follower get_positions — five round trips. It now costs one.

The fault this pins
-------------------
The sizing reads the master's resting book, the master's position and the
follower's holdings — three round trips. The already-mirrored guard then answers,
from one Redis GET, whether there is anything to do at all. With the expensive
half running first, every no-op paid for those calls, and the 30s reconcile
re-pushes EVERY resting order the master has — so ~8 orders x 3 calls fired every
half minute purely to conclude "already done".

Same-symbol events are serialised and Delta's rate limit is shared, so a
genuinely new order queues behind that. Measured 2026-08-28, C-BTC-81000-280826:
the WS delivered the master's order in 0.00s and the follower was not filled for
8-10s, ~5.7s of it inside this loop before any sizing decision was reached, while
a reconcile burst drained (latency climbing 0.00 -> 6.94s in the same log).

What must NOT change is WHAT gets decided — only how much work happens first. So
the placing cases below assert the sizes too, not just the call counts.
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
from app.core.risk_engine import RiskEngine

FAILURES = []

SYMBOL = "C-BTC-81000-280826"
MASTER_ORDER_ID = "1501815805"
FOLLOWER_ID = "f1"
MASTER_ID = "m1"


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------------- fakes

class FakeRedis:
    def __init__(self):
        self.kv, self.h = {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return False
        self.kv[k] = str(v)
        return True

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = str(v)

    async def hdel(self, k, f):
        self.h.get(k, {}).pop(f, None)

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def expire(self, k, ttl):
        return True

    async def lpush(self, k, v):
        return 1


class CountingClient:
    """Records every exchange call. `calls` is what the assertions read."""

    def __init__(self, positions=None, orders=None, name="?"):
        self._positions = positions or []
        self._orders = orders or []
        self.name = name
        self.calls = []
        self.placed = []

    async def get_positions(self):
        self.calls.append("get_positions")
        return list(self._positions)

    async def get_open_orders(self, state="open"):
        self.calls.append(f"get_open_orders:{state}")
        return [o for o in self._orders if (o.get("state") or "open") == state]

    async def get_order(self, order_id):
        self.calls.append("get_order")
        return {"result": next((o for o in self._orders
                                if str(o.get("id")) == str(order_id)), {})}

    async def place_order(self, **kw):
        self.calls.append("place_order")
        self.placed.append(kw)
        return {"result": {"id": "fnew1"}}

    async def edit_order(self, order_id, **kw):
        self.calls.append("edit_order")
        return {"result": {"id": order_id}}


class FakeTable:
    def __init__(self, rows):
        self.rows, self._f = rows, {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._f[col] = val
        return self

    def upsert(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        out = [r for r in self.rows
               if all(r.get(c) == v for c, v in self._f.items())]
        return type("R", (), {"data": out})()


class FakeDB:
    """Counts reads of `accounts`, which is the whole point of the count checks.

    The Supabase client is synchronous, so each of these blocks the event loop
    for its round trip — not just for its own event, but for every event queued
    behind it on the single order consumer.
    """

    def __init__(self, accounts):
        self.accounts = accounts
        self.account_reads = 0

    def table(self, name):
        if name == "accounts":
            self.account_reads += 1
            return FakeTable(self.accounts)
        return FakeTable([])


MASTER_ROW = {
    "id": MASTER_ID, "name": "Jigar", "is_master": True, "status": "active",
    "owner_id": "o1", "api_key": "k", "api_secret": "s", "environment": "demo",
    "allocated_balance": 100000.0,
}
FOLLOWER_ROW = {
    "id": FOLLOWER_ID, "name": "Mini Prathav", "is_master": False,
    "status": "active", "owner_id": "o1", "api_key": "k2", "api_secret": "s2",
    "environment": "demo", "allocation_mode": "auto_ratio",
    "allocated_balance": 1116.9,
}


def build_engine(master_client, follower_client):
    eng = CopyEngine.__new__(CopyEngine)          # no __init__: no real clients
    eng.redis = FakeRedis()
    eng.db = FakeDB([MASTER_ROW, FOLLOWER_ROW])
    eng.risk_engine = RiskEngine()
    eng._open_orders_cache = {}
    eng._OPEN_ORDERS_TTL = 3.0
    eng._master_pos_cache = {}
    eng._MASTER_POS_TTL = 3.0
    eng._follower_pos_cache = {}
    eng._FOLLOWER_POS_TTL = 3.0
    eng._master_signed_cache = {}
    eng._master_exits_cache = {}
    eng._MASTER_EXITS_TTL = 3.0
    eng._master_exit_cache = {}
    eng._MASTER_EXIT_TTL = 3.0
    eng._master_clients = {}
    eng._get_master_client = lambda row: master_client
    eng._get_follower_client = _async_return(follower_client)
    return eng


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


def event(**over):
    e = {
        "action": "place", "master_order_id": MASTER_ORDER_ID, "symbol": SYMBOL,
        "product_id": 136316, "side": "buy", "size": 400.0,
        "order_type": "limit_order", "limit_price": 111.0, "stop_price": None,
        "stop_order_type": None, "reduce_only": False, "is_bracket": False,
        "is_update": False, "owner_id": "o1",
    }
    e.update(over)
    return e


async def drain():
    """_mirror_place fires history writes as tasks; let them settle so their
    exceptions surface here instead of leaking into the next test."""
    for _ in range(3):
        await asyncio.sleep(0)


# --------------------------------------------------------------------- checks

async def test_already_done_mirror_costs_nothing():
    print("\nTHE FIX: a mirror already known done costs no per-follower calls")
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(name="follower")
    eng = build_engine(master, follower)
    # The state the 30s reconcile re-pushes ~8 times a minute: mapped, and
    # already concluded done.
    await eng.redis.hset(f"ordermap:{MASTER_ORDER_ID}", FOLLOWER_ID, "fold1")
    await eng.redis.set(f"mirrordone:{MASTER_ORDER_ID}:{FOLLOWER_ID}", "1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    check("no calls to the FOLLOWER's exchange at all", follower.calls, [])
    check("the master's resting book is NOT read", 
          [c for c in master.calls if c.startswith("get_open_orders")], [])
    # One master read remains, and it is deliberate rather than overlooked: the
    # reduce-only inference at the TOP of _mirror_place needs the master's signed
    # position to tell a close from an entry, and it runs once per event, before
    # any follower is considered. It is cached ~3s, so a ladder burst pays for it
    # once. Removing it means deferring the ledger write that depends on its
    # verdict, which is a bookkeeping change, not a latency one — left alone
    # deliberately. Pinned here so the cost stays ONE call and cannot creep.
    check("exactly one master position read (the reduce-only inference)",
          master.calls, ["get_positions"])
    check("nothing placed", follower.placed, [])
    # One accounts read, not two: the followers and the master used to be
    # fetched by separate queries four lines apart. Same rows, same freshness —
    # this is a round-trip count, not a caching change.
    check("one accounts read, not two", eng.db.account_reads, 1)


async def test_new_order_still_places_the_right_size():
    print("\nunchanged: a genuinely new order is still sized and placed")
    # Master holds 900 after this 400 filled; ratio 1116.9/100000 = 0.011169.
    # target = ceil(900 * 0.011169) = 11, follower holds 6 -> open 5.
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 6}],
                              name="follower")
    eng = build_engine(master, follower)
    await eng.redis.set(f"masterfilled:{MASTER_ORDER_ID}", "1")  # taker fill

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    check("one order placed", len(follower.placed), 1)
    if follower.placed:
        p = follower.placed[0]
        check("opens the 5 it is short", int(p.get("size")), 5)
        check("same side as the master", p.get("side"), "buy")
        check("at the master's price", float(p.get("limit_price")), 111.0)
        check("not reduce-only", bool(p.get("reduce_only")), False)


async def test_new_order_pays_for_its_lookups():
    print("\nthe sizing calls are still made when there IS something to decide")
    # The fix must not have silently disabled the ladder sizing — a real order
    # still reads the master's book and position and the follower's holdings.
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 6}],
                              name="follower")
    eng = build_engine(master, follower)
    await eng.redis.set(f"masterfilled:{MASTER_ORDER_ID}", "1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    check("master position was read", "get_positions" in master.calls, True)
    check("an order went out", "place_order" in follower.calls, True)
    check("still exactly one accounts read", eng.db.account_reads, 1)


async def test_mapped_but_still_resting_is_a_noop_without_sizing():
    print("\na mirror still resting is a no-op, and must not re-price the ladder")
    # Mapped, not in the done-cache, and the follower's order is still open.
    # The guard resolves this with ONE open-orders read; no position lookups,
    # no master book read, nothing placed.
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(
        orders=[{"id": "fold1", "state": "open", "size": 5, "unfilled_size": 5}],
        name="follower")
    eng = build_engine(master, follower)
    await eng.redis.hset(f"ordermap:{MASTER_ORDER_ID}", FOLLOWER_ID, "fold1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    check("nothing placed", follower.placed, [])
    check("the master's resting book is NOT read",
          [c for c in master.calls if c.startswith("get_open_orders")], [])
    check("master cost is still just the one inference read",
          master.calls, ["get_positions"])
    check("only the liveness read on the follower",
          [c for c in follower.calls if not c.startswith("get_open_orders")], [])
    check("one accounts read, not two", eng.db.account_reads, 1)


async def test_unmapped_order_is_not_short_circuited():
    print("\nan order we have never mirrored must NOT be skipped by the guard")
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 0}],
                              name="follower")
    eng = build_engine(master, follower)
    await eng.redis.set(f"masterfilled:{MASTER_ORDER_ID}", "1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    # ceil(900 * 0.011169) = 11, follower flat -> opens all 11.
    check("one order placed", len(follower.placed), 1)
    if follower.placed:
        check("full target opened", int(follower.placed[0].get("size")), 11)


async def test_done_cache_is_per_follower_and_per_order():
    print("\nthe short-circuit must not leak across orders")
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 6}],
                              name="follower")
    eng = build_engine(master, follower)
    await eng.redis.set(f"masterfilled:{MASTER_ORDER_ID}", "1")
    # A DIFFERENT master order is done. This one must still be mirrored.
    await eng.redis.set(f"mirrordone:9999999:{FOLLOWER_ID}", "1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    check("this order still placed", len(follower.placed), 1)


async def test_partition_excludes_paused_and_other_owners():
    print("\nthe Python partition must exclude exactly what the SQL filters did")
    # The two queries filtered on is_master/status/owner_id in the database.
    # Now Python does it, so prove the same rows are selected: a paused follower
    # and another owner's follower must both stay out.
    paused = dict(FOLLOWER_ROW, id="f2", name="Paused", status="paused")
    other = dict(FOLLOWER_ROW, id="f3", name="Other owner", owner_id="o2")
    master = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 900}],
                            name="master")
    follower = CountingClient(positions=[{"product_symbol": SYMBOL, "size": 0}],
                              name="follower")
    eng = build_engine(master, follower)
    eng.db = FakeDB([MASTER_ROW, FOLLOWER_ROW, paused, other])
    await eng.redis.set(f"masterfilled:{MASTER_ORDER_ID}", "1")

    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()

    # Only the one active follower of owner o1 is mirrored to. If `paused` or
    # `other` leaked in, there would be more than one order.
    check("exactly one follower acted on", len(follower.placed), 1)
    check("one accounts read", eng.db.account_reads, 1)


async def test_no_master_row_does_not_crash():
    print("\nan accounts table with no master must not raise")
    # master_row used to come from its own query whose failure was swallowed;
    # now it is a lookup in the shared list, so prove the empty case is handled.
    master = CountingClient(name="master")
    follower = CountingClient(name="follower")
    eng = build_engine(master, follower)
    eng.db = FakeDB([FOLLOWER_ROW])          # no master at all
    await eng._mirror_place(event(), MASTER_ORDER_ID)
    await drain()
    check("no order placed on an unknown ratio", follower.placed, [])


async def main():
    print("=" * 74)
    print("mirror latency — a no-op must not pay for sizing it will not use")
    print("=" * 74)
    for fn in (
        test_already_done_mirror_costs_nothing,
        test_new_order_still_places_the_right_size,
        test_new_order_pays_for_its_lookups,
        test_mapped_but_still_resting_is_a_noop_without_sizing,
        test_unmapped_order_is_not_short_circuited,
        test_done_cache_is_per_follower_and_per_order,
        test_partition_excludes_paused_and_other_owners,
        test_no_master_row_does_not_crash,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
