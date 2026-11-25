# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A paper account must behave like an account, and must never be dangerous.

Run:  cd backend && python tests/test_paper_client.py
No network, no Redis, no Supabase. The one remote call PaperClient makes (the
mark price) is stubbed here; nothing in this file reaches the internet.

WHAT THIS IS FOR. A paper follower mirrors a real master with no money at risk,
so the copy path can be exercised against live master flow. Everything
downstream of an account -- the engine, the reconciler, the position monitor,
the report, the alerting -- talks to it through one object, so the whole trick
is a class with DeltaClient's method surface. These checks are about the two
things that makes true or false:

  * does it answer like an account (fills, positions, resting orders, events)?
  * is it incapable of hurting the real system (never a master, never slow)?

The third rule -- the balance is whatever the row says -- is checked here too,
because a paper balance that drifted would make a copy bug and ratio drift look
identical, which is the whole reason it is fixed.
"""
import asyncio
import inspect
import sys
import time

from app.services import paper_client as pc
from app.services.paper_client import PaperClient

FAILURES = []

SYM = "C-BTC-63000-261126"
ACC = {"id": "p-1", "name": "Paper1", "is_master": False, "status": "active",
       "owner_id": "u1", "master_id": "m-dh", "allocation_mode": "auto_ratio",
       "balance": 5000.0, "is_paper": True}


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
    """Enough Supabase for two tables, including the upsert key and ranges."""
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.f, self.op, self.payload, self.conflict = [], "select", None, None
        self._lim, self._desc = None, False

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, p, **_k): self.op, self.payload = "insert", p; return self
    def update(self, p, **_k): self.op, self.payload = "update", p; return self

    def upsert(self, p, on_conflict=None, **_k):
        self.op, self.payload, self.conflict = "upsert", p, on_conflict
        return self

    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def neq(self, *_a, **_k): return self
    def gte(self, c, v): self.f.append(("gte", c, v)); return self
    def lte(self, c, v): self.f.append(("lte", c, v)); return self
    def order(self, _c, desc=False, **_k): self._desc = desc; return self
    def limit(self, n, **_k): self._lim = n; return self

    def _match(self, r):
        for op, c, v in self.f:
            got = r.get(c)
            if op == "eq":
                if str(got) != str(v):
                    return False
            elif op == "gte" and not (got is not None and str(got) >= str(v)):
                return False
            elif op == "lte" and not (got is not None and str(got) <= str(v)):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=self._desc)
            return _Res(out[:self._lim] if self._lim else out)
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
    def __init__(self, accounts=None):
        self.store = {"accounts": list(accounts or [ACC])}

    def table(self, n):
        return _Q(self.store, n)


FILLS, POSITIONS = [], []


def build(mark=10.0, account=None, slow=False, unpriced=False):
    """A PaperClient with the mark price stubbed. `slow`/`unpriced` stand in for
    a dead ticker endpoint."""
    pc._marks.clear()
    FILLS.clear()
    POSITIONS.clear()

    async def fake_mark(symbol):
        if slow:
            await asyncio.sleep(5)      # longer than any caller should tolerate
        if unpriced:
            return None
        return mark
    pc._mark = fake_mark

    db = DB([dict(account or ACC)])
    c = PaperClient(dict(account or ACC), db)

    async def on_fill(o): FILLS.append(o)
    async def on_position(p): POSITIONS.append(p)
    c._on_fill, c._on_position = on_fill, on_position
    return c, db


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------


def test_a_market_order_fills_and_moves_the_position():
    print("\n1. a market order fills at the mark")
    c, db = build(mark=12.5)

    async def go():
        r = await c.place_order(symbol=SYM, side="sell", size=2943,
                                order_type="market_order")
        return r, await c.get_positions()

    r, pos = run(go())
    check("filled", r["state"], "closed")
    check("at the mark", r["average_fill_price"], "12.5")
    check("nothing left to fill", r["unfilled_size"], 0.0)
    check("one position", len(pos), 1)
    check("short 2943", pos[0]["size"], -2943.0)
    check("entry is the fill price", pos[0]["entry_price"], "12.5")


def test_it_raises_the_events_the_engine_waits_for():
    """THE POINT. The engine is event driven; a paper account that only answered
    REST reads would leave every copy hanging forever."""
    print("\n2. a fill raises an order event AND a position event")
    c, _ = build(mark=12.5)
    run(c.place_order(symbol=SYM, side="buy", size=100, order_type="market_order"))
    check("one order event", len(FILLS), 1)
    check("it reads as a fill", FILLS[0].get("reason"), "fill")
    check_true("and names the contract", FILLS[0].get("product_symbol") == SYM, FILLS)
    check("one position event", len(POSITIONS), 1)
    check("with the new size", POSITIONS[0].get("size"), 100.0)


def test_a_limit_order_rests_until_the_mark_reaches_it():
    print("\n3. a limit order rests, then fills when price comes to it")
    c, _ = build(mark=20.0)

    async def go():
        r = await c.place_order(symbol=SYM, side="buy", size=50,
                                order_type="limit_order", limit_price=10.0)
        resting = await c.get_open_orders("open")
        # price falls to the limit
        async def reached(_s): return 9.0
        pc._mark = reached
        await c._sweep()
        return r, resting, await c.get_positions()

    r, resting, pos = run(go())
    check("it did not fill at once", r["state"], "open")
    check("it is resting", len(resting), 1)
    check("then it fills", len(pos), 1)
    check("at the limit price, not the mark", pos[0]["entry_price"], "10.0")


def test_reduce_only_is_clamped_and_never_flips():
    print("\n4. reduce_only cannot flip a position")
    c, _ = build(mark=10.0)

    async def go():
        await c.place_order(symbol=SYM, side="sell", size=100,
                            order_type="market_order")          # short 100
        # Ask to buy back 500 reduce-only: only 100 is reducible.
        r = await c.place_order(symbol=SYM, side="buy", size=500,
                                order_type="market_order", reduce_only=True)
        return r, await c.get_positions()

    r, pos = run(go())
    check("clamped to what was held", r["size"], 100.0)
    check("and the leg is flat, not flipped", pos, [])


def test_reduce_only_on_a_flat_leg_is_a_no_op_not_a_crash():
    """The engine closes legs it believes are open. A raise here would read as a
    copy failure and send the reconciler chasing it."""
    print("\n5. reduce_only with nothing to reduce")
    c, _ = build(mark=10.0)
    r = run(c.place_order(symbol=SYM, side="buy", size=100,
                          order_type="market_order", reduce_only=True))
    check("recorded as a no-op", r["reason"], "noop")
    check("and it has an id like any order", bool(r.get("id")), True)


def test_a_stop_rests_as_pending_and_fires_when_crossed():
    print("\n6. a stop waits, then triggers")
    c, _ = build(mark=10.0)

    async def go():
        await c.place_order(symbol=SYM, side="sell", size=100,
                            order_type="market_order")          # short 100
        r = await c.place_order(symbol=SYM, side="buy", size=100,
                                order_type="market_order", reduce_only=True,
                                stop_price=15.0, stop_order_type="stop_loss_order")
        pending = await c.get_open_orders("pending")
        async def moved(_s): return 16.0        # through the stop
        pc._mark = moved
        await c._sweep()
        return r, pending, await c.get_positions()

    r, pending, pos = run(go())
    check("it rests as pending, like Delta's", r["state"], "pending")
    check("one pending order", len(pending), 1)
    check("and the stop closed the leg", pos, [])


def test_cancel_and_edit_behave_like_the_real_thing():
    print("\n7. cancel and edit")
    c, _ = build(mark=20.0)

    async def go():
        r = await c.place_order(symbol=SYM, side="buy", size=50,
                                order_type="limit_order", limit_price=10.0)
        e = await c.edit_order(str(r["id"]), limit_price=11.0)
        x = await c.cancel_order(str(r["id"]))
        still = await c.get_open_orders("open")
        return e, x, still

    e, x, still = run(go())
    check("edit moved the price", e["limit_price"], "11.0")
    check("cancel cancelled it", x["state"], "cancelled")
    check("nothing resting afterwards", len(still), 0)


def test_the_balance_is_whatever_the_row_says():
    """Rule 3. A drifting paper balance would make a copy bug and ratio drift
    indistinguishable."""
    print("\n8. the balance is fixed, and re-read live")
    c, db = build(mark=10.0)
    w = run(c.get_wallet())
    check("reads the row", w["result"][0]["balance"], "5000.0")
    check("in the shape the dashboard parses",
          w["result"][0]["asset_symbol"], "USD")
    run(c.place_order(symbol=SYM, side="sell", size=100, order_type="market_order"))
    w2 = run(c.get_wallet())
    check("trading does not move it", w2["result"][0]["balance"], "5000.0")
    db.store["accounts"][0]["balance"] = 7777.0
    w3 = run(c.get_wallet())
    check("editing the row does", w3["result"][0]["balance"], "7777.0")


def test_a_paper_account_can_never_be_a_master():
    """Rule 2. A fake master would drive REAL followers off invented trades."""
    print("\n9. a paper master is refused outright")
    bad = dict(ACC, is_master=True)
    try:
        PaperClient(bad, DB([bad]))
        check("refused", "constructed", "ValueError")
    except ValueError as e:
        check_true("refused at construction", "cannot be a master" in str(e), str(e))


def test_it_never_invents_a_price():
    """A fill at a made-up price corrupts every number downstream — the ratio,
    the report, the P&L. Refuse instead."""
    print("\n10. no mark price available")
    c, _ = build(unpriced=True)
    r = run(c.place_order(symbol=SYM, side="sell", size=100,
                          order_type="market_order"))
    check("not filled", r["reason"], "no_price")
    check("and no position invented", run(c.get_positions()), [])


def test_the_one_remote_call_is_bounded():
    """Rule 1. A paper follower sits in the same follower loop, under the same
    leg lock, as the real ones."""
    print("\n11. the mark fetch cannot hang a real follower")
    src = inspect.getsource(pc)
    check_true("the ticker fetch has a timeout",
               "timeout=MARK_TIMEOUT_SEC" in src, True)
    check_true("well under the live leg-lock wait",
               pc.MARK_TIMEOUT_SEC < 30.0, pc.MARK_TIMEOUT_SEC)
    check_true("and nothing else in the class is remote",
               src.count("httpx.AsyncClient(") == 1, src.count("httpx.AsyncClient("))


def test_every_local_call_answers_immediately():
    print("\n12. the local calls are instant")
    c, _ = build(mark=10.0)

    async def go():
        t0 = time.time()
        await c.get_positions()
        await c.get_open_orders("open")
        await c.get_order_history(50)
        await c.get_wallet()
        return time.time() - t0

    took = run(go())
    check_true("four reads in well under a second", took < 0.5, took)


def test_paper_order_ids_cannot_collide_with_real_ones():
    """The ledger and the report pair orders by id. A paper id that landed in
    Delta's range would pair a paper order with a real one."""
    print("\n13. paper order ids sit outside Delta's range")
    c, _ = build(mark=10.0)
    r = run(c.place_order(symbol=SYM, side="sell", size=1, order_type="market_order"))
    check_true("far above Delta's sequence", int(r["id"]) > 9_000_000_000, r["id"])


if __name__ == "__main__":
    for t in (test_a_market_order_fills_and_moves_the_position,
              test_it_raises_the_events_the_engine_waits_for,
              test_a_limit_order_rests_until_the_mark_reaches_it,
              test_reduce_only_is_clamped_and_never_flips,
              test_reduce_only_on_a_flat_leg_is_a_no_op_not_a_crash,
              test_a_stop_rests_as_pending_and_fires_when_crossed,
              test_cancel_and_edit_behave_like_the_real_thing,
              test_the_balance_is_whatever_the_row_says,
              test_a_paper_account_can_never_be_a_master,
              test_it_never_invents_a_price,
              test_the_one_remote_call_is_bounded,
              test_every_local_call_answers_immediately,
              test_paper_order_ids_cannot_collide_with_real_ones):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
