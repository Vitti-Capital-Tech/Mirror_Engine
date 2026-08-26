"""Standalone checks for OrderExecutor's fill accounting and latency.

Run:  venv/Scripts/python.exe test_order_executor.py
No pytest, no network, no Redis — same style as the other test_*.py scripts here.
Every case below is a bug that reached production; they are pinned so a future
refactor of the two-phase fill policy can't quietly reintroduce them.
"""
import asyncio
import sys
import time
from unittest.mock import patch

sys.path.insert(0, ".")

from app.core import order_executor as oe


# ---------------------------------------------------------------- fake Supabase
class _Res:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._payload, self._op = None, None

    def insert(self, payload):
        self._payload, self._op = payload, "insert"
        return self

    def update(self, payload):
        self._payload, self._op = payload, "update"
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        self.store.setdefault(self.name, []).append((self._op, self._payload))
        if self._op == "insert":
            return _Res([{"id": f"{self.name}-1", **(self._payload or {})}])
        return _Res([])


class FakeDB:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeTable(self.store, name)

    def rows(self, name):
        return self.store.get(name, [])

    def last(self, name, op="update"):
        rows = [p for o, p in self.rows(name) if o == op]
        return rows[-1] if rows else None


# ---------------------------------------------------------------- fake exchange
RAISE = object()


class FakeClient:
    """Scripted Delta client, keyed by ORDER and PHASE rather than by call count —
    the executor's poll count depends on wall-clock timing, so a positional
    script silently tests the wrong phase.

    limit_unfilled        unfilled_size for the resting limit during the poll window
    after_cancel_unfilled unfilled_size for that limit once a cancel was attempted
                          (defaults to limit_unfilled)
    market_unfilled       unfilled_size for the market fallback order
    RAISE in any slot makes get_order blow up there — the REST-hiccup case that
    used to be read as 'zero filled'.
    """

    def __init__(self, limit_unfilled=0, after_cancel_unfilled=None,
                 market_unfilled=0, cancel_ok=True):
        self.limit_unfilled = limit_unfilled
        self.after_cancel_unfilled = (
            limit_unfilled if after_cancel_unfilled is None else after_cancel_unfilled
        )
        self.market_unfilled = market_unfilled
        self.cancel_ok = cancel_ok
        self.placed = []          # every order that reached the "exchange"
        self.cancelled = []
        self._sizes = {}          # order id -> size, so each order reports its own

    async def place_order(self, **kw):
        self.placed.append(kw)
        oid = f"o{len(self.placed)}"
        self._sizes[oid] = kw["size"]
        return {"result": {"id": oid, "product_id": 1}}

    async def get_order(self, order_id):
        oid = str(order_id)
        if oid == "o1":
            v = self.after_cancel_unfilled if self.cancelled else self.limit_unfilled
        else:
            v = self.market_unfilled
        if v is RAISE:
            raise RuntimeError("502 from exchange")
        return {"result": {"size": self._sizes.get(oid, 0), "unfilled_size": v,
                           "average_fill_price": 100.0}}

    async def cancel_order(self, order_id, product_id=None):
        self.cancelled.append(order_id)
        if not self.cancel_ok:
            raise RuntimeError("cancel rejected")
        return {}


ACCOUNT = {
    "id": "acc-1", "name": "Mini Prathav", "status": "active",
    "consecutive_failures": 0, "leverage_limit": 10,
    "available_margin": 10_000.0, "owner_id": "own-1",
}


async def _noop_slippage(**kw):
    return 0.0, 0.0


async def _noop(*a, **k):
    return None


async def run(client, qty=10, trade_type="entry"):
    db = FakeDB()
    with patch.object(oe, "db", db), \
         patch.object(oe.slippage_tracker, "record_and_alert", new=_noop_slippage), \
         patch.object(oe.socket_manager, "emit_account_update", new=_noop), \
         patch.object(oe.socket_manager, "emit_alert", new=_noop):
        t0 = time.time()
        res = await oe.order_executor.execute(
            client=client, account=ACCOUNT, trade_id="t-1", symbol="C-BTC-65600-120826",
            side="buy", quantity=qty, master_price=100.0, trade_type=trade_type,
        )
        return res, db, time.time() - t0


# ------------------------------------------------------------------ test cases
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def market_orders(client):
    return [p for p in client.placed if p.get("order_type") == "market_order"]


async def test_fast_fill_is_not_padded_to_a_full_second():
    """A limit that fills immediately used to cost ~1.15s: the poll slept a flat
    1.0s before its FIRST look. It must now be seen in ~the settle time."""
    c = FakeClient(limit_unfilled=0)       # fully filled on the first read
    res, db, elapsed = await run(c)
    check("fast fill -> status filled", res["status"] == "filled", res)
    check("fast fill -> no market fallback", not market_orders(c), c.placed)
    check(f"fast fill detected fast ({elapsed * 1000:.0f}ms < 300ms)", elapsed < 0.3, f"{elapsed:.3f}s")


async def test_partial_fill_is_reported_as_partial():
    """Target 34 lots, only 1 available. This was stored as 'filled, quantity 1'
    with no alert and no shortfall (live 2026-08-12, C-BTC-65600)."""
    # limit never fills; the market fallback for 34 gets only 1 lot away.
    c = FakeClient(limit_unfilled=34, market_unfilled=33)
    res, db, _ = await run(c, qty=34)
    row = db.last("trade_copies", "update")
    inserts = [p for o, p in db.rows("trade_copies") if o == "insert"]
    check("partial -> status partial", res["status"] == "partial", res)
    check("partial -> db row says partial", (row or {}).get("status") == "partial", row)
    check("partial -> filled qty recorded", (row or {}).get("quantity") == 1, row)
    check("partial -> target kept for the history",
          any(p.get("requested_quantity") == 34 for p in inserts), inserts)
    check("partial -> shortfall in reason", "33 short" in ((row or {}).get("failure_reason") or ""), row)
    check("partial -> alert raised", len(db.rows("alerts")) == 1, db.rows("alerts"))


async def test_full_fill_stays_filled():
    # limit misses, market fills all 34
    res, db, _ = await run(FakeClient(limit_unfilled=34, market_unfilled=0), qty=34)
    row = db.last("trade_copies", "update")
    check("full fill -> status filled", res["status"] == "filled", res)
    check("full fill -> no partial alert", not db.rows("alerts"), db.rows("alerts"))
    check("full fill -> reason cleared", (row or {}).get("failure_reason") is None, row)


async def test_unreadable_and_uncancellable_limit_does_not_market_on_top():
    """The over-fill path: get_order raises AND the cancel fails, so the limit may
    still be resting and may still fill. Marketing a remainder here doubles the
    follower's size. It must place nothing more and report failed."""
    c = FakeClient(limit_unfilled=RAISE, cancel_ok=False)
    res, db, _ = await run(c, qty=10)
    check("unknown state -> only the limit was placed", len(c.placed) == 1, c.placed)
    check("unknown state -> no market order", not market_orders(c), c.placed)
    check("unknown state -> reported failed", res["status"] == "failed", res)


async def test_fill_between_confirm_and_cancel_is_counted():
    """The limit fills right as we cancel. Because the count is now taken AFTER
    the cancel, the fill is seen and no market remainder is sent."""
    # nothing during the poll window; the post-cancel read shows it filled in full
    c = FakeClient(limit_unfilled=10, after_cancel_unfilled=0)
    res, db, _ = await run(c, qty=10)
    check("late fill -> counted as filled", res["status"] == "filled", res)
    check("late fill -> no market order on top", not market_orders(c), c.placed)
    check("late fill -> cancel was attempted", len(c.cancelled) == 1, c.cancelled)


async def test_unreadable_market_order_is_assumed_filled():
    """A market order the exchange accepted but we can't read back: assuming it
    did NOT fill is what makes the reconciler stack a second order on top."""
    c = FakeClient(limit_unfilled=10, market_unfilled=RAISE)
    res, db, _ = await run(c, qty=10)
    check("unreadable market -> assumed filled", res["status"] == "filled", res)
    check("unreadable market -> exactly 2 orders", len(c.placed) == 2, c.placed)


async def main():
    # Keep the suite quick: shrink the fill window, keep the poll:window ratio.
    oe.FILL_WAIT_SEC = 0.6
    oe.FILL_POLL_SEC = 0.05
    oe.CONFIRM_SETTLE_SEC = 0.01
    for t in (
        test_fast_fill_is_not_padded_to_a_full_second,
        test_partial_fill_is_reported_as_partial,
        test_full_fill_stays_filled,
        test_unreadable_and_uncancellable_limit_does_not_market_on_top,
        test_fill_between_confirm_and_cancel_is_counted,
        test_unreadable_market_order_is_assumed_filled,
    ):
        print(f"\n{t.__name__}")
        await t()
    print("\n" + ("ALL PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
