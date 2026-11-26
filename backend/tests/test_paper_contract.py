# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""PaperClient must answer to the SAME CALLS the engine makes of DeltaClient.

Run:  cd backend && python tests/test_paper_contract.py
No network, no Redis, no Supabase.

WHY THIS FILE EXISTS. On 2026-11-26 every paper bracket failed, all session,
on

    PaperClient.place_bracket() missing 1 required positional argument: 'symbol'

Mini 1 and Mini 2 held open positions with no stop and no take-profit, while
the log recorded that protection had been requested for them. The cause was not
subtle: `place_bracket` had been written with an invented signature instead of
DeltaClient's, and the engine -- which calls both through one line, naming
every argument -- could not call it at all.

Nothing caught it. test_paper_client.py has thirteen scenarios and does not
call `place_bracket` once; it tested the methods that were on the author's mind
rather than the methods the engine actually uses. A paper account is a MOCK,
and the only interesting property of a mock is that it is interchangeable with
the thing it stands in for. Testing it in isolation cannot show that.

So this file tests the SEAM, not either side of it:

  1. every call the engine makes must BIND against both clients
  2. no argument in DeltaClient's contract may vanish into paper's **kwargs
     (the quiet half of the same bug: `edit_order` accepted `stop_price`,
     dropped it, and reported success -- a stop that never moved, with
     "Updated bracket @ <new price>" in the log saying it had)
  3. every method the engine reaches for must EXIST on paper, discovered by
     reading copy_engine.py rather than from a list kept by hand here
  4. the RETURN SHAPE the caller destructures must match

A list maintained by hand would rot the first time somebody added a call. (3)
reads the engine's source so it cannot.
"""
import asyncio
import inspect
import re
import sys

from app.services import paper_client as pc
from app.services.delta_client import DeltaClient
from app.services.paper_client import PaperClient

FAILURES = []

SYM = "C-BTC-63000-261126"
PRODUCT_ID = 167088
ACC = {"id": "p-1", "name": "Paper1", "is_master": False, "status": "active",
       "owner_id": "u1", "master_id": "m-dh", "allocation_mode": "auto_ratio",
       "balance": 5000.0, "environment": "paper"}


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


# --------------------------------------------------------------- minimal DB
class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.f, self.op, self.payload, self.conflict = [], "select", None, None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, p, **_k): self.op, self.payload = "insert", p; return self
    def update(self, p, **_k): self.op, self.payload = "update", p; return self

    def upsert(self, p, on_conflict=None, **_k):
        self.op, self.payload, self.conflict = "upsert", p, on_conflict
        return self

    def eq(self, c, v): self.f.append((c, v)); return self
    def neq(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self
    def lte(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def _match(self, r):
        return all(str(r.get(c)) == str(v) for c, v in self.f)

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "select":
            return _Res([dict(r) for r in rows if self._match(r)])
        if self.op == "upsert" and self.conflict:
            keys = [k.strip() for k in self.conflict.split(",")]
            for r in rows:
                if all(str(r.get(k)) == str(self.payload.get(k)) for k in keys):
                    r.update(self.payload)
                    return _Res([dict(r)])
            rows.append(dict(self.payload))
            return _Res([dict(self.payload)])
        if self.op in ("insert", "upsert"):
            rows.append(dict(self.payload))
            return _Res([dict(self.payload)])
        hit = [r for r in rows if self._match(r)]
        for r in hit:
            r.update(self.payload)
        return _Res([dict(r) for r in hit])


class DB:
    def __init__(self):
        self.store = {"accounts": [ACC]}

    def table(self, n):
        return _Q(self.store, n)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def build(mark=100.0):
    """A PaperClient with both remote calls stubbed. Nothing here is online."""
    pc._marks.clear()
    pc._symbols.clear()

    async def fake_mark(_symbol):
        return mark

    async def fake_symbol(product_id):
        return SYM if str(product_id) == str(PRODUCT_ID) else None

    pc._mark = fake_mark
    pc._symbol_for = fake_symbol
    return PaperClient(dict(ACC), DB())


# ------------------------------------------------------------- the contract
#
# EVERY CALL THE ENGINE MAKES, written exactly as copy_engine.py makes it --
# same method, same argument NAMES. Keep this in step with the engine; test 3
# fails if a method appears there that is missing here or on PaperClient.
ENGINE_CALLS = [
    ("place_order", dict(symbol=SYM, side="sell", size=1, order_type="market_order",
                         limit_price=None, reduce_only=False, stop_price=None,
                         stop_order_type=None, stop_trigger_method=None,
                         time_in_force=None, client_order_id="abc")),
    ("place_bracket", dict(product_id=PRODUCT_ID,
                           stop_loss={"order_type": "market_order", "stop_price": "64050.0"},
                           take_profit=None, trigger_method="mark_price")),
    ("edit_order", dict(order_id="1", product_id=PRODUCT_ID, stop_price=64050.0,
                        stop_trigger_method="mark_price")),
    ("edit_order", dict(order_id="1", product_id=PRODUCT_ID, limit_price=10.0, size=2)),
    ("cancel_order", dict(order_id="1", product_id=PRODUCT_ID)),
    ("get_order", dict(order_id="1")),
    ("get_open_orders", dict(state="pending")),
    ("get_positions", dict()),
    ("get_wallet", dict()),
    ("get_ticker", dict(symbol=SYM)),
    ("get_order_history", dict(page_size=50)),
    ("get_order_history_range", dict(start_time_us=1, end_time_us=2, page_size=50)),
    ("get_fills", dict(page_size=50)),
    ("get_fills_range", dict(start_time_us=1, end_time_us=2, page_size=50)),
]


def test_every_engine_call_binds_against_both_clients():
    """The bug itself: the engine's call could not be made of PaperClient.

    Binding is exactly the check that failed in production -- Python got as far
    as matching arguments to parameters and stopped there."""
    print("\n1. every call the engine makes binds against both clients")
    for method, kwargs in ENGINE_CALLS:
        for cls in (DeltaClient, PaperClient):
            fn = getattr(cls, method, None)
            if fn is None:
                check_true("%s exists on %s" % (method, cls.__name__), False)
                continue
            try:
                inspect.signature(fn).bind(None, **kwargs)
                ok, why = True, None
            except TypeError as e:
                ok, why = False, str(e)
            check_true("%s binds on %s" % (method, cls.__name__), ok, why)


def test_paper_never_silently_swallows_an_argument():
    """The quiet half. An argument accepted into **kwargs and dropped is worse
    than one that raises: the caller is told it worked."""
    print("\n2. no argument in Delta's contract disappears into paper's **kwargs")
    for name in dir(DeltaClient):
        if name.startswith("_"):
            continue
        d = getattr(DeltaClient, name, None)
        p = getattr(PaperClient, name, None)
        if not callable(d) or p is None or not callable(p):
            continue
        try:
            dp = inspect.signature(d).parameters
            pp = inspect.signature(p).parameters
        except (TypeError, ValueError):
            continue
        var_kw = any(v.kind == v.VAR_KEYWORD for v in pp.values())
        named = [k for k in dp if k != "self" and k not in pp]
        check_true("%s: paper names every argument Delta takes%s"
                   % (name, "" if not named else " (missing: %s)" % ", ".join(named)),
                   not (named and var_kw) and not named,
                   named or None)


def test_the_engine_calls_no_method_paper_lacks():
    """Read copy_engine.py rather than trusting the list above.

    A hand-kept list is exactly what let the first bug through: it described
    the author's idea of the interface, not the interface."""
    print("\n3. every client method the engine calls exists on PaperClient")
    src = open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "app", "core", "copy_engine.py"),
        encoding="utf-8").read()
    called = set(re.findall(r"\bclient\.([a-z_][a-z0-9_]*)\s*\(", src))
    called |= set(re.findall(r"\bmaster_client\.([a-z_][a-z0-9_]*)\s*\(", src))
    called = {m for m in called if not m.startswith("_")}
    check_true("found call sites to inspect", len(called) >= 5, sorted(called))
    for m in sorted(called):
        on_delta = callable(getattr(DeltaClient, m, None))
        on_paper = callable(getattr(PaperClient, m, None))
        if not on_delta:
            continue          # a helper on something that is not a client
        check_true("engine calls %s -> PaperClient has it" % m, on_paper)
        if on_paper and m not in {c[0] for c in ENGINE_CALLS}:
            check_true("engine calls %s -> it is covered by ENGINE_CALLS above" % m, False)


def test_place_bracket_returns_the_shape_the_caller_reads():
    """The caller does result["stop_loss_order"]["id"] to map the follower's
    order back to the master's. The first version returned a LIST, so even with
    the signature fixed the id would never have been recorded and a later
    cancel or edit from the master could not have found this order."""
    print("\n4. place_bracket returns a dict keyed by leg type, with ids")
    c = build()
    run(c.place_order(symbol=SYM, side="buy", size=4, order_type="market_order"))
    resp = run(c.place_bracket(
        product_id=PRODUCT_ID,
        stop_loss={"order_type": "market_order", "stop_price": "90.0"},
        take_profit={"order_type": "market_order", "stop_price": "120.0"},
        trigger_method="mark_price"))
    result = resp.get("result")
    check_true("result is a dict, not a list", isinstance(result, dict), type(result).__name__)
    check_true("stop_loss_order present", isinstance(result, dict)
               and "stop_loss_order" in result)
    check_true("take_profit_order present", isinstance(result, dict)
               and "take_profit_order" in result)
    sl = (result or {}).get("stop_loss_order") or {}
    check_true("the stop-loss leg carries an id", bool(sl.get("id")), sl.get("id"))
    check("the stop rests until triggered", sl.get("state"), "pending")
    check("it closes the long", sl.get("side"), "sell")
    check("it is reduce-only", bool(sl.get("reduce_only")), True)
    check("it covers the whole position", abs(float(sl.get("size") or 0)), 4.0)


def test_an_unresolvable_product_is_loud_not_silent():
    """Protection that was not placed must never report success."""
    print("\n5. a product that cannot be resolved raises, it does not pretend")
    c = build()
    run(c.place_order(symbol=SYM, side="buy", size=1, order_type="market_order"))
    try:
        run(c.place_bracket(product_id=999999,
                            stop_loss={"order_type": "market_order", "stop_price": "90.0"},
                            trigger_method="mark_price"))
        check_true("raised rather than claiming success", False)
    except RuntimeError as e:
        check_true("raised rather than claiming success", "cannot resolve" in str(e), str(e))


def test_editing_a_stop_actually_moves_it():
    """`stop_price` fell into **kwargs and was dropped, so a master who dragged
    his SL had the edit logged as applied and the paper book left untouched."""
    print("\n6. editing a stop moves it, and says so truthfully")
    c = build()
    run(c.place_order(symbol=SYM, side="buy", size=2, order_type="market_order"))
    resp = run(c.place_bracket(
        product_id=PRODUCT_ID,
        stop_loss={"order_type": "market_order", "stop_price": "90.0"},
        trigger_method="mark_price"))
    oid = ((resp.get("result") or {}).get("stop_loss_order") or {}).get("id")
    check_true("placed a stop to edit", bool(oid), oid)
    run(c.edit_order(oid, product_id=PRODUCT_ID, stop_price=85.0,
                     stop_trigger_method="spot_price"))
    after = run(c.get_order(oid)).get("result") or {}
    check("the trigger moved", float(after.get("stop_price")), 85.0)
    check("the trigger method moved too", after.get("stop_trigger_method"), "spot_price")


def test_a_bracket_on_a_flat_leg_is_a_no_op_not_a_crash():
    print("\n7. a bracket on a flat leg is a harmless no-op")
    c = build()
    resp = run(c.place_bracket(
        product_id=PRODUCT_ID,
        stop_loss={"order_type": "market_order", "stop_price": "90.0"},
        trigger_method="mark_price"))
    check("nothing placed", resp.get("result"), {})
    check("and it did not fail", resp.get("success"), True)


if __name__ == "__main__":
    for t in (test_every_engine_call_binds_against_both_clients,
              test_paper_never_silently_swallows_an_argument,
              test_the_engine_calls_no_method_paper_lacks,
              test_place_bracket_returns_the_shape_the_caller_reads,
              test_an_unresolvable_product_is_loud_not_silent,
              test_editing_a_stop_actually_moves_it,
              test_a_bracket_on_a_flat_leg_is_a_no_op_not_a_crash):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
