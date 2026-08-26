"""Checks for the resting-order half of the copy history.

Run:  venv/Scripts/python.exe test_order_history.py
No pytest, no network.

The gap these cover: the history tables were fed only by the master FILL path,
while the engine actually mirrors via resting orders — and nothing at all
observed the FOLLOWER's fills, so a mirrored order that filled left its leg
reading 'placed' forever.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.core import order_history as oh

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------- a small in-memory fake
class _Res:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table, blow_up=False):
        self.store, self.table, self.blow_up = store, table, blow_up
        self.filters, self.op, self.payload, self.conflict = [], None, None, None

    # -- builders
    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None, **_k):
        self.op, self.payload, self.conflict = "upsert", payload, on_conflict
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    # -- execution
    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filters)

    def execute(self):
        if self.blow_up:
            raise RuntimeError("supabase is down")
        rows = self.store.setdefault(self.table, [])
        if self.op == "select":
            return _Res([r for r in rows if self._match(r)])
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"{self.table}-{len(rows) + 1}")
            rows.append(row)
            return _Res([row])
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return _Res(hit)
        if self.op == "upsert":
            key = self.conflict
            existing = [r for r in rows if key and r.get(key) == self.payload.get(key)]
            if existing:
                existing[0].update(self.payload)
                return _Res([existing[0]])
            row = dict(self.payload)
            row.setdefault("id", f"{self.table}-{len(rows) + 1}")
            rows.append(row)
            return _Res([row])
        raise AssertionError(self.op)


class FakeDB:
    def __init__(self, blow_up=False):
        self.store, self.blow_up = {}, blow_up

    def table(self, name):
        return FakeQuery(self.store, name, self.blow_up)

    def rows(self, name):
        return self.store.get(name, [])


ACCOUNT = {"id": "acc-1", "name": "Mini Prathav", "owner_id": "own-1"}
SYM = "P-BTC-76800-260826"


# ------------------------------------------------------------------ test cases
async def test_order_stage_row_cannot_collide_with_its_fill():
    """trades.master_trade_id is UNIQUE and process_fill's duplicate guard keys on
    it. If the order-stage row used the bare order id, the later FILL event for
    that same order would be seen as already-processed and its copies would never
    dispatch."""
    db = FakeDB()
    uuid = await oh.record_order_stage(
        db, "1497216150", symbol=SYM, side="sell", size=150, price=90,
        kind="exit", owner_id="own-1",
    )
    check_true("order stage recorded", bool(uuid), db.rows("trades"))
    check("prefixed id", db.rows("trades")[0]["master_trade_id"], "ord:1497216150")
    check_true("bare fill id is still free",
               all(r["master_trade_id"] != "1497216150" for r in db.rows("trades")),
               db.rows("trades"))
    check("exit kind maps to a legal trade_type", db.rows("trades")[0]["trade_type"], "exit")


async def test_bracket_kind_is_recorded_as_an_exit():
    """trades.trade_type is CHECK-constrained — 'bracket' would be rejected and
    the whole row lost."""
    db = FakeDB()
    await oh.record_order_stage(db, "1", symbol=SYM, side="sell", size=350,
                               price=76930, kind="bracket", owner_id="own-1")
    check("bracket -> exit", db.rows("trades")[0]["trade_type"], "exit")


async def test_repeated_order_event_updates_one_row_not_many():
    """The reconcile pass re-sees the same resting order every 30s."""
    db = FakeDB()
    for _ in range(4):
        uuid = await oh.record_order_stage(db, "1497216150", symbol=SYM, side="sell",
                                           size=150, price=90, kind="exit", owner_id="own-1")
        await oh.record_leg(db, uuid, "1497216150", ACCOUNT, status="pending",
                            requested=1, follower_order_id="f-1")
    check("one trades row", len(db.rows("trades")), 1)
    check("one copy leg", len(db.rows("trade_copies")), 1)


async def test_each_rung_gets_its_own_leg():
    """A ladder is several master orders; each must be its own row or the history
    can't show 1/1/1."""
    db = FakeDB()
    for oid, size in (("1497216150", 150), ("1497216010", 100), ("1497215927", 100)):
        uuid = await oh.record_order_stage(db, oid, symbol=SYM, side="sell", size=size,
                                          price=90, kind="exit", owner_id="own-1")
        await oh.record_leg(db, uuid, oid, ACCOUNT, status="pending", requested=1,
                            follower_order_id=f"f-{oid}")
    check("three trades rows", len(db.rows("trades")), 3)
    check("three legs", len(db.rows("trade_copies")), 3)
    check("each leg keeps its master order",
          sorted(r["master_order_id"] for r in db.rows("trade_copies")),
          ["1497215927", "1497216010", "1497216150"])


async def test_follower_fill_marks_the_leg_filled():
    """The half that was never observed."""
    db = FakeDB()
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=150,
                                      price=90, kind="exit", owner_id="own-1")
    await oh.record_leg(db, uuid, "m-1", ACCOUNT, status="pending", requested=1,
                        follower_order_id="1497287620")
    ok = await oh.record_follower_fill(db, "1497287620", account_id="acc-1",
                                       filled_qty=1, price=90.5, symbol=SYM)
    leg = db.rows("trade_copies")[0]
    check_true("fill was matched to a leg", ok, ok)
    check("status filled", leg["status"], "filled")
    check("filled quantity", leg["quantity"], 1.0)
    check("execution price", leg["execution_price"], 90.5)
    check_true("no stale reason left behind", leg.get("failure_reason") is None, leg)


async def test_short_follower_fill_is_recorded_as_partial():
    db = FakeDB()
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=2900,
                                       price=3, kind="exit", owner_id="own-1")
    await oh.record_leg(db, uuid, "m-1", ACCOUNT, status="pending", requested=34,
                        follower_order_id="f-9")
    await oh.record_follower_fill(db, "f-9", account_id="acc-1", filled_qty=1,
                                  price=3.0, symbol=SYM)
    leg = db.rows("trade_copies")[0]
    check("short fill -> partial", leg["status"], "partial")
    check_true("shortfall spelled out", "1 of 34" in (leg.get("failure_reason") or ""), leg)


async def test_running_fill_size_never_goes_backwards():
    """A resting order fills in pieces and each event carries the RUNNING filled
    size. Taking the latest rather than the max would let a re-delivered earlier
    event undo a later one."""
    db = FakeDB()
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=300,
                                       price=90, kind="exit", owner_id="own-1")
    await oh.record_leg(db, uuid, "m-1", ACCOUNT, status="pending", requested=3,
                        follower_order_id="f-1")
    await oh.record_follower_fill(db, "f-1", account_id="acc-1", filled_qty=3, price=90.0)
    await oh.record_follower_fill(db, "f-1", account_id="acc-1", filled_qty=1, price=90.0)
    leg = db.rows("trade_copies")[0]
    check("kept the larger fill", leg["quantity"], 3.0)
    check("still filled, not downgraded", leg["status"], "filled")


async def test_fill_for_an_unknown_order_is_not_an_error():
    """A manual order placed on the follower account by hand."""
    db = FakeDB()
    ok = await oh.record_follower_fill(db, "not-ours", account_id="acc-1",
                                       filled_qty=5, price=1.0)
    check("unknown order -> False", ok, False)
    check("nothing written", len(db.rows("trade_copies")), 0)


async def test_a_dead_database_never_raises_into_the_caller():
    """A history write failing must not stop a copy from being placed."""
    db = FakeDB(blow_up=True)
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=1,
                                       price=1, kind="exit", owner_id="own-1")
    check("order stage -> None, no raise", uuid, None)
    await oh.record_leg(db, "t-1", "m-1", ACCOUNT, status="pending", requested=1)
    ok = await oh.record_follower_fill(db, "f-1", account_id="acc-1", filled_qty=1, price=1)
    check("fill -> False, no raise", ok, False)
    await oh.record_order_status(db, "t-1", "copied")
    check_true("survived a dead database throughout", True)


# --------------------------------------------------- the follower fill callback
async def test_callback_ignores_events_that_are_not_fills():
    db = FakeDB()
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=150,
                                       price=90, kind="exit", owner_id="own-1")
    await oh.record_leg(db, uuid, "m-1", ACCOUNT, status="pending", requested=1,
                        follower_order_id="f-1")
    cb = oh.make_follower_fill_recorder(db, ACCOUNT)

    # A cancel: closed, but nothing executed.
    await cb({"id": "f-1", "state": "closed", "reason": "cancel",
              "size": 1, "unfilled_size": 1})
    check("cancel leaves the leg pending", db.rows("trade_copies")[0]["status"], "pending")

    # An order merely resting.
    await cb({"id": "f-1", "state": "open", "reason": "create",
              "size": 1, "unfilled_size": 1})
    check("resting leaves the leg pending", db.rows("trade_copies")[0]["status"], "pending")

    # A real fill.
    await cb({"id": "f-1", "state": "closed", "reason": "fill", "size": 1,
              "unfilled_size": 0, "average_fill_price": "90.5",
              "product_symbol": SYM, "side": "sell"})
    check("fill records", db.rows("trade_copies")[0]["status"], "filled")


async def test_callback_handles_a_partial_fill_event():
    """state 'open' with reason 'fill' — the state is not what identifies a fill."""
    db = FakeDB()
    uuid = await oh.record_order_stage(db, "m-1", symbol=SYM, side="sell", size=300,
                                       price=90, kind="exit", owner_id="own-1")
    await oh.record_leg(db, uuid, "m-1", ACCOUNT, status="pending", requested=3,
                        follower_order_id="f-1")
    cb = oh.make_follower_fill_recorder(db, ACCOUNT)
    await cb({"id": "f-1", "state": "open", "reason": "fill", "size": 3,
              "unfilled_size": 1, "average_fill_price": "90.0", "product_symbol": SYM})
    leg = db.rows("trade_copies")[0]
    check("partial fill counted", leg["quantity"], 2.0)
    check("partial fill flagged", leg["status"], "partial")


async def test_callback_survives_a_malformed_event():
    db = FakeDB()
    cb = oh.make_follower_fill_recorder(db, ACCOUNT)
    for bad in ({}, {"id": None}, {"id": "x", "reason": "fill", "size": "abc"},
                {"id": "x", "reason": "fill"}):
        await cb(bad)
    check_true("no raise on malformed events", True)


async def main():
    for t in (
        test_order_stage_row_cannot_collide_with_its_fill,
        test_bracket_kind_is_recorded_as_an_exit,
        test_repeated_order_event_updates_one_row_not_many,
        test_each_rung_gets_its_own_leg,
        test_follower_fill_marks_the_leg_filled,
        test_short_follower_fill_is_recorded_as_partial,
        test_running_fill_size_never_goes_backwards,
        test_fill_for_an_unknown_order_is_not_an_error,
        test_a_dead_database_never_raises_into_the_caller,
        test_callback_ignores_events_that_are_not_fills,
        test_callback_handles_a_partial_fill_event,
        test_callback_survives_a_malformed_event,
    ):
        print(f"\n{t.__name__}")
        await t()
    print("\n" + ("ALL PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
