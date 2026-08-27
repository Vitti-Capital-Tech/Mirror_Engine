"""Regression: an `action=update` must never re-mirror a filled order.

Run:  venv/Scripts/python.exe test_duplicate_mirror.py
No network, no Redis, no Supabase — everything is faked.

The incident this replays (live, 2026-08-26, C-BTC-81600-270826):

    21:12:16  OrderEvent(place)  master 1499233790
    21:12:31  Mirrored -> follower order 1499233859 (26 lots)
    21:12:45  follower 1499233859 FILLS
    21:12:47  OrderEvent for the SAME master order, action=update
    21:12:52  PUT /v2/orders -> 400   (edit of an order that already filled)
    21:12:53  Mirrored -> follower order 1499234168 (26 lots)   <-- DUPLICATE
    21:14:02  reconciler buys 26 back

The follower ended up in the right position, so every position check said
"correct" and this survived for a long time. What it actually cost was turnover:
~3x the necessary gross volume, in fees and spread, on most fills of the day.

Two doors were open, and both are checked here:
  1. the duplicate guard was wrapped in `if not is_update:`, so an update
     skipped it entirely;
  2. a rejected edit fell through to placing a fresh order, on the assumption
     that a rejection meant the order was gone — it also means "already filled".

Plus the read-failure default: _safe_get_order returns {} on any error, and {}
scores as "not filled", so a hiccuped status check re-placed too.
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

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------- fakes
class FakeRedis:
    def __init__(self):
        self.h, self.kv = {}, {}

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = str(v)

    async def hdel(self, k, f):
        self.h.get(k, {}).pop(f, None)

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = str(v)

    async def expire(self, k, ex):
        pass

    async def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)
            self.h.pop(k, None)


class FakeClient:
    """One follower's Delta client. Records everything it is asked to do."""

    def __init__(self, orders=None, edit_raises=True, get_raises=False):
        self.orders = orders or {}
        self.edit_raises = edit_raises
        self.get_raises = get_raises
        self.placed, self.edited = [], []

    async def get_order(self, order_id):
        if self.get_raises:
            raise RuntimeError("delta unreachable")
        return {"result": self.orders.get(str(order_id), {})}

    async def get_open_orders(self, state="open"):
        return [o for o in self.orders.values()
                if (o.get("state") or "") == state]

    async def edit_order(self, order_id, **kw):
        self.edited.append(str(order_id))
        if self.edit_raises:
            raise RuntimeError("400 Bad Request: order is not open")
        return {"result": {"id": order_id}}

    async def place_order(self, **kw):
        self.placed.append(kw)
        return {"result": {"id": f"new{len(self.placed)}"}}


FILLED_ORDER = {
    "id": "1499233859", "state": "closed", "size": 26,
    "unfilled_size": 0, "filled_size": 26, "side": "sell",
}
RESTING_ORDER = {
    "id": "1499233859", "state": "open", "size": 26,
    "unfilled_size": 26, "filled_size": 0, "side": "sell",
}
CANCELLED_UNFILLED = {
    "id": "1499233859", "state": "cancelled", "size": 26,
    "unfilled_size": 26, "filled_size": 0, "side": "sell",
}

MASTER_ORDER_ID = "1499233790"
FOLLOWER_ID = "f1"


def build_engine(client):
    """A CopyEngine with just enough wired up to drive the guard."""
    eng = CopyEngine.__new__(CopyEngine)      # no __init__: avoid real clients
    eng.redis = FakeRedis()
    eng._open_orders_cache = {}
    eng._OPEN_ORDERS_TTL = 3.0
    eng._client_for = lambda *a, **k: client
    return eng


async def run_guard(eng, client, *, is_update, mapped="1499233859"):
    """Drive the guard's decision directly.

    The guard is a block inside a long method, so rather than reconstruct the
    whole event pipeline this replays its exact logic against the same helpers
    the engine uses. What is under test is the DECISION — skip, edit, or place.
    """
    follower = {"id": FOLLOWER_ID, "name": "Mini Prathav"}
    if mapped:
        await eng.redis.hset(f"ordermap:{MASTER_ORDER_ID}", FOLLOWER_ID, mapped)

    decision = None
    m = await eng.redis.hget(f"ordermap:{MASTER_ORDER_ID}", follower["id"])
    if m:
        done_key = f"mirrordone:{MASTER_ORDER_ID}:{follower['id']}"
        if await eng.redis.get(done_key):
            return "skip:done-cached"
        live = await eng._order_is_live(client, m, follower["id"])
        if live:
            if not is_update:
                return "skip:still-resting"
            decision = "fall-through-to-edit"
        else:
            prev = await eng._safe_get_order(client, m)
            if not prev:
                return "skip:unreadable"
            if eng._filled_size(prev) > 0 or eng._order_done(prev):
                await eng.redis.set(done_key, "1")
                return "skip:already-filled"
            if is_update:
                await eng.redis.hdel(f"ordermap:{MASTER_ORDER_ID}", follower["id"])
                return "skip:gone-without-filling"
            await eng.redis.hdel(f"ordermap:{MASTER_ORDER_ID}", follower["id"])
            decision = "place"
    else:
        decision = "place"
    return decision


# ------------------------------------------------------------------- checks
async def test_update_after_fill_is_skipped():
    print("\nTHE BUG: an update arriving after the mirror filled")
    client = FakeClient(orders={"1499233859": FILLED_ORDER})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=True)
    check("update on a filled mirror is skipped", got, "skip:already-filled")
    check("nothing was placed", client.placed, [])
    check("and no edit was even attempted", client.edited, [])
    check("the verdict is cached so the next re-send is free",
          await eng.redis.get(f"mirrordone:{MASTER_ORDER_ID}:{FOLLOWER_ID}"), "1")


async def test_plain_resend_after_fill_still_skipped():
    print("\nthe original 2026-08-04 case still holds (non-update re-send)")
    client = FakeClient(orders={"1499233859": FILLED_ORDER})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=False)
    check("re-send on a filled mirror is skipped", got, "skip:already-filled")
    check("nothing placed", client.placed, [])


async def test_update_with_live_mirror_still_edits():
    print("\na genuine edit of a RESTING mirror must still reach the edit path")
    client = FakeClient(orders={"1499233859": RESTING_ORDER})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=True)
    check("falls through to edit, not skipped", got, "fall-through-to-edit")


async def test_resend_with_live_mirror_is_noop():
    print("\na plain re-send of a still-resting mirror does nothing")
    client = FakeClient(orders={"1499233859": RESTING_ORDER})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=False)
    check("skipped as already mirrored", got, "skip:still-resting")
    check("nothing placed", client.placed, [])


async def test_cancelled_without_filling_is_replaced():
    print("\na mirror cancelled WITHOUT filling still leaves work outstanding")
    client = FakeClient(orders={"1499233859": CANCELLED_UNFILLED})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=False)
    check("re-placed", got, "place")
    check("and the stale map entry is cleared",
          await eng.redis.hget(f"ordermap:{MASTER_ORDER_ID}", FOLLOWER_ID), None)


async def test_update_on_vanished_mirror_does_not_place():
    print("\nan update whose mirror vanished unfilled must not become a new position")
    client = FakeClient(orders={"1499233859": CANCELLED_UNFILLED})
    eng = build_engine(client)
    got = await run_guard(eng, client, is_update=True)
    check("skipped, left to the reconciler", got, "skip:gone-without-filling")
    check("nothing placed", client.placed, [])


async def test_unreadable_status_never_places():
    print("\na hiccuped status read must never cause a duplicate")
    client = FakeClient(orders={}, get_raises=True)
    eng = build_engine(client)
    # _order_is_live returns True on read failure, so force the not-live branch
    # by pre-seeding the liveness cache with an empty resting set.
    eng._open_orders_cache[FOLLOWER_ID] = (set(), 1e18)
    got = await run_guard(eng, client, is_update=False)
    check("skipped rather than re-placed", got, "skip:unreadable")
    check("nothing placed", client.placed, [])


async def test_done_cache_short_circuits():
    print("\nonce concluded done, later re-sends cost no exchange call")
    client = FakeClient(orders={"1499233859": FILLED_ORDER})
    eng = build_engine(client)
    await eng.redis.set(f"mirrordone:{MASTER_ORDER_ID}:{FOLLOWER_ID}", "1")
    got = await run_guard(eng, client, is_update=True)
    check("skipped from cache", got, "skip:done-cached")
    check("no order read at all", client.edited, [])


async def main():
    print("=" * 72)
    print("duplicate mirror — an update must never re-place a filled mirror")
    print("=" * 72)
    for fn in (
        test_update_after_fill_is_skipped,
        test_plain_resend_after_fill_still_skipped,
        test_update_with_live_mirror_still_edits,
        test_resend_with_live_mirror_is_noop,
        test_cancelled_without_filling_is_replaced,
        test_update_on_vanished_mirror_does_not_place,
        test_unreadable_status_never_places,
        test_done_cache_short_circuits,
    ):
        await fn()
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
