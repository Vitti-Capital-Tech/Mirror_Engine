"""Regression: a copy leg must never be left reading 'pending' forever.

Run:  venv/Scripts/python.exe test_pending_legs.py
No network, no Redis, no Supabase — db and the exchange client are stubs.

THE INCIDENT (2026-10-14): the Trades page showed "pending" for trades that had
really filled, and the dashboard reported a ~36% success rate. 503 of the
newest 1,000 trade_copies rows were stuck at status='pending' with a NULL
quantity, 333 of them never written a second time, going back to 25 Sep.

Two distinct causes, both reproduced below.

1. A RACE. record_leg runs in a create_task so the order reaches the exchange
   without waiting for its own bookkeeping — so the row lands a few hundred ms
   after the order is placed, and an aggressive limit fills faster than that.
   record_follower_fill looked the row up by follower_order_id, found nothing,
   returned False, and the fill was gone; the miss was logged at DEBUG, which
   production does not enable. Measured against the live account:

       order 1603459768  filled 03:17:55.617  row written 03:17:56.169  +552ms
       order 1603459767  filled 03:17:55.607  row written 03:17:55.937  +330ms
       order 1603208136  filled 22:59:41.027  row written 22:59:41.382  +355ms

   An unmatched fill is now HELD and claimed by record_leg when the row lands.

2. NO FILL IS EVER COMING. A cancelled order produces no fill event, so those
   legs could never be settled by the stream at any speed — half the live
   sample (4 of 8) were cancelled orders sitting at 'pending'. sweep_pending_legs
   asks the exchange what became of each stale order and writes it down.
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

from app.core import order_history as oh

FAILURES = []
ACCOUNT = {"id": "f-mini", "name": "Mini Prathav", "owner_id": "own-1"}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# --- stubs -----------------------------------------------------------------

class Table:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._f, self._mode, self._payload = {}, None, None

    def select(self, *a, **kw):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._mode, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._f[col] = val
        return self

    def lt(self, col, val):
        self._f["__lt__" + col] = val
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def _match(self, r):
        for k, v in self._f.items():
            if k.startswith("__lt__"):
                if not (str(r.get(k[6:])) < str(v)):
                    return False
            elif str(r.get(k)) != str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", "row-%d" % (len(rows) + 1))
            row.setdefault("created_at", "2026-10-14T00:00:00+00:00")
            rows.append(row)
            return type("R", (), {"data": [row]})()
        hits = [r for r in rows if self._match(r)]
        if self._mode == "update":
            for r in hits:
                r.update(self._payload)
            return type("R", (), {"data": hits})()
        return type("R", (), {"data": hits})()


class DB:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return Table(self.store, name)

    def copies(self):
        return self.store.get("trade_copies", [])


class Order:
    """One order as the exchange reports it."""
    def __init__(self, state, size, unfilled, px=None, sym="C-BTC-85000-141026"):
        self.d = {"state": state, "size": size, "unfilled_size": unfilled,
                  "average_fill_price": px, "product_symbol": sym}


class Client:
    def __init__(self, orders, fail=False):
        self.orders, self.fail = orders, fail

    async def get_order(self, oid):
        if self.fail:
            raise RuntimeError("exchange unreachable")
        o = self.orders.get(str(oid))
        return {"result": o.d if o else {}}


class CM:
    def __init__(self, client):
        self.client = client

    def get_client(self, _account_id):
        return self.client


def reset():
    oh._early_fills.clear()
    oh._unresolved.clear()


# --- tests -----------------------------------------------------------------

def test_the_normal_case_still_works():
    print("\n1. the ordinary path: row first, then the fill")
    reset()
    db = DB()
    asyncio.run(oh.record_leg(db, "t-1", "m-1", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-1"))
    check("leg starts pending", db.copies()[0]["status"], "pending")
    ok = asyncio.run(oh.record_follower_fill(db, "o-1", account_id="f-mini",
                                             filled_qty=5, price=42.0))
    check("fill recorded", ok, True)
    check("status settled", db.copies()[0]["status"], "filled")
    check("quantity written", db.copies()[0]["quantity"], 5.0)
    check("price written", db.copies()[0]["execution_price"], 42.0)


def test_the_race_the_fill_arrives_first():
    print("\n2. THE BUG: the exchange fills before our row is written")
    reset()
    db = DB()
    # The fill event lands with no row to update — as it did 552ms early live.
    ok = asyncio.run(oh.record_follower_fill(db, "o-2", account_id="f-mini",
                                             filled_qty=5, price=42.0))
    check("nothing to update yet", ok, False)
    check("but the fill is HELD, not dropped", len(oh._early_fills), 1)
    check("no row written by the fill path", len(db.copies()), 0)

    # Now our own write lands, as 'pending'.
    asyncio.run(oh.record_leg(db, "t-2", "m-2", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-2"))
    row = db.copies()[0]
    check("the held fill is claimed -> filled", row["status"], "filled")
    check("quantity is no longer NULL", row["quantity"], 5.0)
    check("price carried over", row["execution_price"], 42.0)
    check("hold released", len(oh._early_fills), 0)
    # Before the fix this row read status='pending', quantity=None, forever.


def test_a_partial_early_fill_is_labelled_partial():
    print("\n3. an early PARTIAL fill still reads as partial")
    reset()
    db = DB()
    asyncio.run(oh.record_follower_fill(db, "o-3", account_id="f-mini",
                                        filled_qty=2, price=10.0,
                                        symbol="C-BTC-85000-141026"))
    asyncio.run(oh.record_leg(db, "t-3", "m-3", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-3"))
    row = db.copies()[0]
    check("status", row["status"], "partial")
    check("quantity", row["quantity"], 2.0)
    check("reason explains it", "2 of 5" in (row.get("failure_reason") or ""), True)


def test_the_largest_running_total_wins():
    print("\n4. a resting order fills in pieces — keep the running total")
    reset()
    db = DB()
    asyncio.run(oh.record_follower_fill(db, "o-4", account_id="f-mini", filled_qty=2, price=10.0))
    asyncio.run(oh.record_follower_fill(db, "o-4", account_id="f-mini", filled_qty=5, price=10.0))
    asyncio.run(oh.record_leg(db, "t-4", "m-4", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-4"))
    check("5 lots, not 2", db.copies()[0]["quantity"], 5.0)
    check("and reads filled", db.copies()[0]["status"], "filled")


def test_a_held_fill_never_resurrects_a_settled_leg():
    print("\n5. a hold is consumed once, and only by its own order")
    reset()
    db = DB()
    asyncio.run(oh.record_follower_fill(db, "o-5", account_id="f-mini", filled_qty=5, price=1.0))
    # A DIFFERENT order's leg must not pick it up.
    asyncio.run(oh.record_leg(db, "t-6", "m-6", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-OTHER"))
    check("other leg untouched", db.copies()[0]["status"], "pending")
    check("hold still waiting for its own order", len(oh._early_fills), 1)
    asyncio.run(oh.record_leg(db, "t-5", "m-5", ACCOUNT, status="pending",
                              requested=5, follower_order_id="o-5"))
    check("its own leg settles", db.copies()[1]["status"], "filled")
    check("hold consumed exactly once", len(oh._early_fills), 0)


def test_sweeper_settles_a_fill_the_stream_missed():
    print("\n6. BACKSTOP: a filled order the stream never reported")
    reset()
    db = DB()
    db.store["trade_copies"] = [{
        "id": "r1", "account_id": "f-mini", "follower_order_id": "o-9",
        "requested_quantity": 5.0, "quantity": None, "status": "pending",
        "created_at": "2026-10-14T00:00:00+00:00"}]
    cm = CM(Client({"o-9": Order("closed", 5, 0, px=7.5)}))
    c = asyncio.run(oh.sweep_pending_legs(db, cm))
    check("counted as filled", c["filled"], 1)
    check("row settled", db.copies()[0]["status"], "filled")
    check("quantity recorded", db.copies()[0]["quantity"], 5.0)


def test_sweeper_closes_a_cancelled_order():
    print("\n7. BACKSTOP: a cancelled order — no fill is EVER coming")
    reset()
    db = DB()
    db.store["trade_copies"] = [{
        "id": "r2", "account_id": "f-mini", "follower_order_id": "o-10",
        "requested_quantity": 5.0, "quantity": None, "status": "pending",
        "created_at": "2026-10-14T00:00:00+00:00"}]
    cm = CM(Client({"o-10": Order("cancelled", 5, 5)}))
    c = asyncio.run(oh.sweep_pending_legs(db, cm))
    check("counted", c["cancelled"], 1)
    row = db.copies()[0]
    # 'cancelled' is not a permitted status (003_partial_fill_accounting.sql).
    check("recorded as skipped, not failed", row["status"], "skipped")
    check("reason says why", "before any fill" in row["failure_reason"], True)
    check("status is a legal one",
          row["status"] in ("pending", "filled", "partial", "failed", "skipped", "retrying"), True)


def test_sweeper_leaves_a_resting_order_alone():
    print("\n8. a genuinely resting order is still legitimately pending")
    reset()
    db = DB()
    db.store["trade_copies"] = [{
        "id": "r3", "account_id": "f-mini", "follower_order_id": "o-11",
        "requested_quantity": 5.0, "quantity": None, "status": "pending",
        "created_at": "2026-10-14T00:00:00+00:00"}]
    cm = CM(Client({"o-11": Order("open", 5, 5)}))
    c = asyncio.run(oh.sweep_pending_legs(db, cm))
    check("left as-is", db.copies()[0]["status"], "pending")
    check("counted as still resting", c["still_open"], 1)


def test_an_unreadable_order_is_parked_not_retried_forever():
    print("\n9. an unreadable order must not block the rest of the queue")
    reset()
    db = DB()
    db.store["trade_copies"] = [{
        "id": "r4", "account_id": "f-mini", "follower_order_id": "o-12",
        "requested_quantity": 5.0, "quantity": None, "status": "pending",
        "created_at": "2026-10-14T00:00:00+00:00"}]
    cm = CM(Client({}, fail=True))
    c1 = asyncio.run(oh.sweep_pending_legs(db, cm))
    check("first pass tries it", c1["checked"], 1)
    check("and gives up on it", c1["unreadable"], 1)
    c2 = asyncio.run(oh.sweep_pending_legs(db, cm))
    check("second pass does NOT re-read it", c2["checked"], 0)
    check("row untouched", db.copies()[0]["status"], "pending")


def test_sweeper_never_raises():
    print("\n10. the backstop must never take the engine down")
    reset()
    db = DB()

    class Broken:
        def table(self, *a):
            raise RuntimeError("supabase down")

    try:
        c = asyncio.run(oh.sweep_pending_legs(Broken(), CM(Client({}))))
        check("swallowed", c["checked"], 0)
    except Exception as e:
        check("swallowed", "raised %s" % type(e).__name__, "no raise")


def main():
    print("=" * 74)
    print("pending legs - a copy leg must never read 'pending' forever")
    print("=" * 74)
    for fn in (
        test_the_normal_case_still_works,
        test_the_race_the_fill_arrives_first,
        test_a_partial_early_fill_is_labelled_partial,
        test_the_largest_running_total_wins,
        test_a_held_fill_never_resurrects_a_settled_leg,
        test_sweeper_settles_a_fill_the_stream_missed,
        test_sweeper_closes_a_cancelled_order,
        test_sweeper_leaves_a_resting_order_alone,
        test_an_unreadable_order_is_parked_not_retried_forever,
        test_sweeper_never_raises,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
