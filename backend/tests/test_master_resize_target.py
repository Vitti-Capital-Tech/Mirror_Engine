# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""When the master RESIZES an order, the recorded target moves with it.

Run:  cd backend && python tests/test_master_resize_target.py
No network, no Redis, no Supabase.

THE FALSE FINDING. The daily report grades a follower's order against
`trade_copies.requested_quantity` — what the engine asked for — rather than
against a target re-derived from today's balance ratio. That is deliberate:
re-deriving measures the ratio's DRIFT rather than the copy, and produced
thirteen false findings in one day on 2026-11-25.

But the number was written once, at placement, and never revisited. A master
who EDITS his order size leaves it stale:

    2026-11-26, Dhwani, master order 1718356740
      master places 1000  ->  followers mirror 981 and 867   (correct)
      master edits  1000 -> 1
      engine edits  both followers -> 1                      (correct)
      report compares 1 against the 981 and 867 on record    -> UNDER-SIZED

Two findings, on the only desk that had nothing wrong with it, against copies
that were perfect.

WHY NOT A TOLERANCE. Any tolerance wide enough to absorb a 1000-to-1 edit is
wide enough to hide the double-sizing bug the report exists to catch. The
engine knows the new number at the moment it sends the edit — the fix is to
write it down, not to stop looking. So these checks are in two halves: the
engine records the new target (1-5), and the report then grades the copy
correctly without any slack having been added (6-8).
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from app.core import order_compare as oc
from app.core import order_history as oh

FAILURES = []

T0 = datetime(2026, 11, 25, 4, 30, tzinfo=timezone.utc)
START = datetime(2026, 11, 24, 18, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)

MASTER = {"id": "m1", "name": "Dhwani", "is_master": True, "environment": "demo",
          "api_key": "k", "api_secret": "s", "balance": 5909.5947475, "owner_id": "o1"}
FOLLOWER = {"id": "f1", "name": "Dhwani1", "is_master": False, "environment": "demo",
            "api_key": "k", "api_secret": "s", "status": "active", "owner_id": "o1",
            "allocation_mode": "auto_ratio", "balance": 5794.53080679}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, ok, got=None):
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r" % (got,)))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------- a writable fake DB
class _Res:
    def __init__(self, data):
        self.data = data


class Table:
    def __init__(self, db, name):
        self.db, self.name, self._f, self._op, self._payload = db, name, [], "select", None

    def select(self, *_a, **_k):
        self._op = "select"; return self

    def update(self, payload, **_k):
        self._op, self._payload = "update", payload; return self

    def insert(self, payload, **_k):
        self._op, self._payload = "insert", payload; return self

    def eq(self, col, val):
        self._f.append((col, val)); return self

    def in_(self, col, vals):
        self._f.append((col, [str(v) for v in vals])); return self

    def _match(self, r):
        for col, val in self._f:
            if isinstance(val, list):
                if str(r.get(col)) not in val:
                    return False
            elif str(r.get(col)) != str(val):
                return False
        return True

    def execute(self):
        rows = self.db.data.setdefault(self.name, [])
        if self._op == "select":
            return _Res([dict(r) for r in rows if self._match(r)])
        if self._op == "insert":
            self.db.inserts.append((self.name, self._payload))
            rows.append(dict(self._payload))
            return _Res([dict(self._payload)])
        hit = [r for r in rows if self._match(r)]
        for r in hit:
            r.update(self._payload)
        self.db.updates.append((self.name, self._payload))
        return _Res([dict(r) for r in hit])


class DB:
    def __init__(self, legs=None):
        self.data = {"trade_copies": [dict(l) for l in (legs or [])], "trades": []}
        self.inserts, self.updates = [], []

    def table(self, name):
        return Table(self, name)


def leg_row(master_oid, follower_oid, requested, status="filled"):
    return {"id": "l-%s" % follower_oid, "trade_id": None, "account_id": "f1",
            "master_order_id": str(master_oid),
            "follower_order_id": (str(follower_oid) if follower_oid else None),
            "status": status, "quantity": requested,
            "requested_quantity": requested, "execution_price": 24.0,
            "slippage_pct": 0.0, "execution_time_ms": 400,
            "failure_reason": None, "created_at": T0.isoformat()}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def only(db):
    return db.data["trade_copies"][0]


# =================================================== half one: the recording
def test_a_resize_moves_the_recorded_target():
    print("\n1. the master's edit moves the recorded target")
    db = DB([leg_row("1718356740", "f-900", 981)])
    run(oh.record_leg_resize(db, "1718356740", "f1", 1))
    check("target follows the edit", only(db)["requested_quantity"], 1.0)


def test_a_replaced_order_id_is_followed():
    """Delta's edits can cancel-and-replace. A leg still pointing at the dead
    id cannot be linked, and the report falls back to inferring."""
    print("\n2. a cancel-and-replace keeps the leg pointing at the live order")
    db = DB([leg_row("1718356740", "f-900", 981)])
    run(oh.record_leg_resize(db, "1718356740", "f1", 1, follower_order_id="f-901"))
    check("follower order id updated", only(db)["follower_order_id"], "f-901")
    check("and the target too", only(db)["requested_quantity"], 1.0)


def test_it_never_invents_a_leg():
    """No row means the placement was never recorded. A row built from an edit
    alone would claim a copy whose origin we cannot see."""
    print("\n3. no row to update means no row is created")
    db = DB([])
    run(oh.record_leg_resize(db, "1718356740", "f1", 1))
    check("nothing inserted", db.inserts, [])
    check("and the table is still empty", db.data["trade_copies"], [])


def test_it_does_not_touch_status():
    """A leg that has since filled stays filled — the resize is about size."""
    print("\n4. an already-filled leg keeps its status")
    db = DB([leg_row("1718356740", "f-900", 981, status="filled")])
    run(oh.record_leg_resize(db, "1718356740", "f1", 1))
    check("status untouched", only(db)["status"], "filled")
    check("only these fields were written",
          sorted(db.updates[0][1].keys()), ["requested_quantity"])


def test_another_follower_is_not_touched():
    print("\n5. the edit writes to this follower's leg only")
    other = dict(leg_row("1718356740", "f-800", 867), id="l-other", account_id="f2")
    db = DB([leg_row("1718356740", "f-900", 981), other])
    run(oh.record_leg_resize(db, "1718356740", "f1", 1))
    rows = {r["account_id"]: r["requested_quantity"] for r in db.data["trade_copies"]}
    check("f1 moved", rows["f1"], 1.0)
    check("f2 left alone", rows["f2"], 867)

    db2 = DB([leg_row("1718356740", "f-900", 981)])
    run(oh.record_leg_resize(db2, "9999999999", "f1", 1))
    check("a different master order does nothing", only(db2)["requested_quantity"], 981)

    db3 = DB([leg_row("1718356740", "f-900", 981)])
    run(oh.record_leg_resize(db3, "1718356740", "f1", None))
    check("no target means no write", only(db3)["requested_quantity"], 981)


# ====================================================== half two: the report
def order(oid, symbol, side, size, at, *, filled=None, state="closed"):
    return {
        "id": oid, "product_symbol": symbol, "side": side, "size": size,
        "unfilled_size": size - (size if filled is None else filled),
        "state": state, "order_type": "limit_order", "cancellation_reason": None,
        "stop_order_type": None,
        "created_at": at.isoformat(), "updated_at": at.isoformat(),
        "limit_price": 24.0, "average_fill_price": 24.0, "reduce_only": False,
    }


def report(master_orders, follower_orders, legs):
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        raw = master_orders if acc["id"] == "m1" else follower_orders
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        return asyncio.run(oc.compare([MASTER, FOLLOWER], START, END, db=DB(legs)))
    finally:
        oc.fetch_account_orders = original


def verdicts(out):
    return sorted(l["verdict"] for r in out["rows"] for l in r["legs"])


# The order book AFTER the edit — which is what the report reads at 23:45.
# Master's 1000-lot order is now 1 lot, and the follower's copy is 1 lot.
SYM = "C-BTC-63000-261126"
MASTER_AFTER = [order("1718356740", SYM, "buy", 1, T0, filled=1)]
FOLLOWER_AFTER = [order("f-900", SYM, "buy", 1, T0 + timedelta(seconds=1), filled=1)]


def test_the_stale_target_is_what_produced_the_false_finding():
    """The bug itself, kept as a test so the fix cannot be quietly undone: with
    the ledger still holding the pre-edit 981, a perfect copy reads as wrong."""
    print("\n6. a stale recorded target calls a perfect copy under-sized")
    out = report(MASTER_AFTER, FOLLOWER_AFTER, [leg_row("1718356740", "f-900", 981)])
    check("the false finding", verdicts(out), ["undersized"])


def test_the_updated_target_grades_it_correctly():
    print("\n7. with the target moved, the same copy is correct")
    out = report(MASTER_AFTER, FOLLOWER_AFTER, [leg_row("1718356740", "f-900", 1)])
    check("no finding", verdicts(out), ["matched"])
    check("and the desk is clean", out["summary"]["match_rate_pct"], 100.0)
    check("with no errors counted", out["summary"]["errors"], 0)


def test_a_real_oversize_is_still_caught():
    """The guard this must not weaken. No tolerance was added, so a copy that
    really is twice the size it should be is still a finding.

    The master edits 1000 down to 100, not to 1: with a 1-lot target the
    pre-existing SIZE_TOLERANCE of one lot already covers a 2-lot punch, which
    is rounding slack rather than anything this change did."""
    print("\n8. a genuinely double-sized copy is still caught")
    m100 = [order("1718356740", SYM, "buy", 100, T0, filled=100)]
    doubled = [order("f-900", SYM, "buy", 196, T0 + timedelta(seconds=1), filled=196)]
    out = report(m100, doubled, [leg_row("1718356740", "f-900", 98)])
    check("still reported", verdicts(out), ["oversized"])

    # And the edit itself does not launder a bad copy: target moved to 98,
    # follower still sitting on its pre-edit 981.
    stale_follower = [order("f-900", SYM, "buy", 981, T0 + timedelta(seconds=1), filled=981)]
    out_s = report(m100, stale_follower, [leg_row("1718356740", "f-900", 98)])
    check("a follower that did NOT track the edit is caught",
          verdicts(out_s), ["oversized"])

    # And the pre-edit shape: master 1000, follower punched 1962 against 981.
    big_master = [order("1718356740", SYM, "buy", 1000, T0, filled=1000)]
    big_double = [order("f-900", SYM, "buy", 1962, T0 + timedelta(seconds=1), filled=1962)]
    out2 = report(big_master, big_double, [leg_row("1718356740", "f-900", 981)])
    check("a 2x punch on the full size too", verdicts(out2), ["oversized"])


if __name__ == "__main__":
    for t in (test_a_resize_moves_the_recorded_target,
              test_a_replaced_order_id_is_followed,
              test_it_never_invents_a_leg,
              test_it_does_not_touch_status,
              test_another_follower_is_not_touched,
              test_the_stale_target_is_what_produced_the_false_finding,
              test_the_updated_target_grades_it_correctly,
              test_a_real_oversize_is_still_caught):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
