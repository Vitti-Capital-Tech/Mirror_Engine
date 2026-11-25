# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""One follower's failure must not cost its siblings their copy.

Run:  cd backend && python tests/test_copy_isolation.py
No network, no Redis, no Supabase.

process_fill sizes and dispatches each follower in turn, in a ~145-line loop,
and that loop had NO guard of its own. Two consequences, both only visible once
a master has more than one follower:

  1. A follower raising anywhere in the sizing half abandoned the loop. Every
     follower AFTER it was never even considered for that master fill.

  2. The dispatch half ended in a bare `asyncio.gather(*tasks)`. order_executor
     .execute returns a result dict for the failures it knows about, but it is
     ~310 lines and an unexpected raise escapes it -- and a bare gather re-raises
     the first exception, throwing away every other follower's RESULT. Those
     orders had already been placed; what was lost was their ledger entries,
     their trade_copies rows and their notifications. A sibling's copy became
     INVISIBLE rather than wrong, which is worse: the reconciler then goes
     chasing a leg that already exists.

Neither is a crash -- main.py catches and logs -- so the only outward trace was
a "missing" leg in the next day's report.

It never showed while every master had exactly one follower. Pratik's desk has
had two since 2026-11-26, and the follower order comes from a DB read, so either
one can be the one that gets skipped.

This is the same isolation the reconcile sweep was given on 2026-11-20, after
its close/trim pass aborted a whole master's pass on one bad leg. The live
mirror never got it.
"""
import asyncio
import inspect
import sys

import test_end_to_end as e2e
from test_end_to_end import Exchange, Client, Redis, Sock, ConnMgr

FAILURES = []

SYM = "C-BTC-63000-261126"
MASTER = dict(e2e.MASTER)        # JD algo4
FOLLOWER = dict(e2e.FOLLOWER)    # JD algo3
SIBLING = dict(e2e.SIBLING)      # JD algo3b -- the second follower


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


# ------------------------------------------------------------------ fake DB
class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.op, self.payload = [], "select", None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def upsert(self, p, **_k): self.op, self.payload = "insert", p; return self
    def eq(self, c, v): self.filters.append((c, v)); return self
    def neq(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def _match(self, r):
        for c, v in self.filters:
            got = r.get(c)
            try:
                if float(got) == float(v):
                    continue
            except (TypeError, ValueError):
                pass
            if str(got) != str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "select":
            return _Res([dict(r) for r in rows if self._match(r)])
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", "%s-%d" % (self.table, len(rows) + 1))
            rows.append(row)
            return _Res([dict(row)])
        hit = [r for r in rows if self._match(r)]
        for r in hit:
            r.update(self.payload)
        return _Res([dict(r) for r in hit])


class DB:
    def __init__(self, accounts):
        self.store = {"accounts": accounts}

    def table(self, n):
        return _Q(self.store, n)


SENT = []


def build(poison_sizing=None, poison_execute=None, order=(FOLLOWER, SIBLING)):
    """A real CopyEngine over the fake exchange, with two followers on one
    master. `order` fixes which follower the loop reaches first."""
    from app.core import copy_engine as ce
    from app.services import telegram_client as tg

    SENT.clear()

    async def fake_fail(account, symbol, side, lots, reason, **kw):
        SENT.append((account, symbol, reason))
    tg.notify_fail = fake_fail

    async def quiet(*a, **k):
        return False
    tg.send_message = quiet

    ex = Exchange()
    mc = Client(ex, MASTER["id"])
    clients = {MASTER["id"]: mc}
    for f in order:
        clients[f["id"]] = Client(ex, f["id"])
    db = DB([MASTER] + list(order))
    eng = ce.CopyEngine(db, Redis(), Sock(), ConnMgr(clients))
    eng._get_master_client = lambda row: mc

    if poison_sizing:
        real_qty = eng.risk_engine.calculate_follower_quantity

        def qty(master_qty, price, account, **kw):
            if account.get("id") == poison_sizing:
                raise RuntimeError("sizing blew up")
            return real_qty(master_qty, price, account, **kw)
        eng.risk_engine.calculate_follower_quantity = qty

    async def execute(client, account, trade_id, symbol, side, quantity,
                      master_price, trade_type="entry"):
        if poison_execute and account.get("id") == poison_execute:
            raise RuntimeError("executor blew up")
        await client.place_order(symbol=symbol, side=side, size=int(quantity),
                                 order_type="market_order",
                                 reduce_only=trade_type in ("exit", "sl"))
        return {"status": "filled", "filled_quantity": int(quantity),
                "account_id": account["id"], "account_name": account["name"]}
    ce.order_executor.execute = execute
    return eng, ex, clients, db


def fill_event(oid="1716100001", side="sell", qty=3000.0, price=10.0):
    return {"master_trade_id": str(oid), "symbol": SYM, "side": side,
            "quantity": float(qty), "entry_price": price, "trade_type": "entry",
            "raw_payload": {"id": oid, "size": qty, "unfilled_size": 0},
            "owner_id": "u1", "master_account_id": MASTER["id"]}


def legs(db, account_id):
    return [r for r in db.store.get("trade_copies", [])
            if str(r.get("account_id")) == str(account_id)]


def drive(coro_fn, fallback):
    """Run one copy and come back with the positions.

    WITHOUT the guard the exception escapes process_fill altogether (main.py is
    what caught it in production), so swallow it here and report it as the named
    failure it is -- a test that dies with a traceback tells the next reader
    nothing about what broke.
    """
    try:
        return asyncio.run(coro_fn()), None
    except Exception as e:
        return fallback, e


# ---------------------------------------------------------------------------


def test_a_sizing_crash_does_not_cost_the_sibling_its_copy():
    """GAP 1. The poisoned follower is reached FIRST, so under the old loop the
    sibling was never even considered."""
    print("\n1. the first follower crashes while being sized")
    eng, ex, clients, db = build(poison_sizing=FOLLOWER["id"])

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event())
        return (ex.position(FOLLOWER["id"], SYM),
                ex.position(SIBLING["id"], SYM))

    (bad, good), escaped = drive(run, (0.0, 0.0))
    check("the copy chain did not abort", escaped, None)
    check("the crashing follower got nothing", bad, 0.0)
    check_true("the sibling still got its copy", good < 0, good)
    check_true("and the crash was reported",
               any(s[0] == FOLLOWER["name"] and "crash" in s[2] for s in SENT), SENT)
    check_true("with a failed leg on record",
               any(r.get("status") == "failed" for r in legs(db, FOLLOWER["id"])),
               legs(db, FOLLOWER["id"]))


def test_it_works_in_the_other_order_too():
    """The DB decides the order, so neither position may be the safe one."""
    print("\n2. the SECOND follower is the one that crashes")
    eng, ex, clients, db = build(poison_sizing=SIBLING["id"])

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1716100002"))
        return (ex.position(FOLLOWER["id"], SYM),
                ex.position(SIBLING["id"], SYM))

    (good, bad), escaped = drive(run, (0.0, 0.0))
    check("the copy chain did not abort", escaped, None)
    check_true("the healthy follower copied", good < 0, good)
    check("the crashing one got nothing", bad, 0.0)


def test_an_executor_crash_does_not_hide_the_siblings_result():
    """GAP 2. Both orders go out; the bare gather used to re-raise and throw the
    sibling's RESULT away, leaving its copy unrecorded."""
    print("\n3. one follower's executor raises")
    eng, ex, clients, db = build(poison_execute=FOLLOWER["id"])

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1716100003"))
        return ex.position(SIBLING["id"], SYM)

    good, escaped = drive(run, 0.0)
    check("the copy chain did not abort", escaped, None)
    check_true("the sibling's order was placed", good < 0, good)
    check_true("and the crash is reported against the right account",
               any(s[0] == FOLLOWER["name"] and "crash" in s[2] for s in SENT), SENT)


def test_both_followers_copy_when_nothing_is_wrong():
    """The guard must not change the happy path."""
    print("\n4. nothing poisoned — both copy as before")
    eng, ex, clients, db = build()

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1716100004"))
        return (ex.position(FOLLOWER["id"], SYM),
                ex.position(SIBLING["id"], SYM))

    (a, b), escaped = drive(run, (0.0, 0.0))
    check("the copy chain did not abort", escaped, None)
    check_true("first follower copied", a < 0, a)
    check_true("second follower copied", b < 0, b)
    check("and nothing was reported as a failure",
          [s for s in SENT if "crash" in s[2]], [])


def test_the_isolation_is_in_the_code():
    """A behaviour test can pass for the wrong reason. Pin the shape too."""
    print("\n5. and the isolation really is in process_fill")
    from app.core.copy_engine import CopyEngine
    src = inspect.getsource(CopyEngine.process_fill)
    check_true("the follower loop is guarded",
               "except Exception as _copy_err:" in src, True)
    check_true("gather tolerates one task's exception",
               "return_exceptions=True" in src, True)
    check_true("and a cancellation is still re-raised, not swallowed",
               "raise _res" in src, True)
    i_for = src.index("for follower in followers:")
    check_true("the guard opens inside the loop, not around it",
               i_for < src.index("except Exception as _copy_err:"), True)


if __name__ == "__main__":
    for t in (test_a_sizing_crash_does_not_cost_the_sibling_its_copy,
              test_it_works_in_the_other_order_too,
              test_an_executor_crash_does_not_hide_the_siblings_result,
              test_both_followers_copy_when_nothing_is_wrong,
              test_the_isolation_is_in_the_code):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
