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


def build(mark=10.0, account=None, slow=False, unpriced=False, spot=None):
    """A PaperClient with the public ticker stubbed. `slow`/`unpriced` stand in
    for a dead ticker endpoint.

    The TICKER is stubbed, not `_mark`, so the real _mark and _trigger_px run:
    they are the two that have to keep the contract's own price and the
    underlying apart, and a stub in front of them would hide it. `spot` is the
    underlying -- what a stop on an option is actually measured against.
    Defaults to the mark so the plain futures-shaped cases read unchanged.
    """
    pc._quotes.clear()
    FILLS.clear()
    POSITIONS.clear()

    async def fake_quote(_symbol):
        if slow:
            await asyncio.sleep(5)      # longer than any caller should tolerate
        if unpriced:
            return {}
        return {"mark_price": mark, "close": mark,
                "spot_price": mark if spot is None else spot}
    pc._quote = fake_quote

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
        # price falls to the limit. Stub the TICKER, never _mark: assigning
        # pc._mark here left it replaced for every later test in the file.
        async def reached(_s):
            return {"mark_price": 9.0, "close": 9.0, "spot_price": 9.0}
        pc._quote = reached
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
        # Stub the TICKER, not _mark. This line used to assign pc._mark, which
        # is a module global: it stayed replaced for every test that ran after
        # this one, and only went unnoticed because build() happened to
        # overwrite _mark each time. The moment build() stopped doing that,
        # test 10 started seeing this 16.0 and "never invents a price" passed a
        # price it had invented here.
        async def moved(_s):
            return {"mark_price": 16.0, "close": 16.0, "spot_price": 16.0}
        pc._quote = moved
        await c._sweep()
        return r, pending, await c.get_positions()

    r, pending, pos = run(go())
    check("it rests as pending, like Delta's", r["state"], "pending")
    check("one pending order", len(pending), 1)
    check("and the stop closed the leg", pos, [])


def test_an_option_stop_triggers_off_the_underlying_not_the_premium():
    """The 2026-11-26 book-wipe, in one test.

    A stop on C-BTC-62400 is a BTC level (61284). The contract's own price is
    the premium (~38). Comparing those says "already through it" for every
    resting sell, so the instant Mini 1 and Mini 2 finally had protection, the
    first sweep closed every leg they held.
    """
    print("\n6b. an option stop reads the underlying, not its own premium")
    # Premium 38, BTC at 60500, and a protective sell stop at 59900.
    c, _ = build(mark=38.0, spot=60500.0)

    async def go():
        await c.place_order(symbol=SYM, side="buy", size=10,
                            order_type="market_order")           # long 10
        await c.place_order(symbol=SYM, side="sell", size=10,
                            order_type="market_order", reduce_only=True,
                            stop_price=59900.0, stop_order_type="stop_loss_order",
                            stop_trigger_method="spot_price")
        await c._sweep()
        return await c.get_positions()

    pos = run(go())
    check_true("BTC is above the stop, so the leg is untouched", len(pos) == 1, pos)

    # Now BTC falls through it. The premium is irrelevant either way.
    c2, _ = build(mark=38.0, spot=59800.0)

    async def go2():
        await c2.place_order(symbol=SYM, side="buy", size=10,
                             order_type="market_order")
        await c2.place_order(symbol=SYM, side="sell", size=10,
                             order_type="market_order", reduce_only=True,
                             stop_price=59900.0, stop_order_type="stop_loss_order",
                             stop_trigger_method="spot_price")
        await c2._sweep()
        return await c2.get_positions()

    check("BTC through the stop closes it", run(go2()), [])


def test_a_stop_whose_reference_cannot_be_read_waits():
    """Firing on a price we do not have is the same bug wearing a hat."""
    print("\n6c. an unreadable trigger reference does not fire the stop")
    c, _ = build(mark=38.0, spot=60500.0)

    async def go():
        await c.place_order(symbol=SYM, side="buy", size=10,
                            order_type="market_order")
        await c.place_order(symbol=SYM, side="sell", size=10,
                            order_type="market_order", reduce_only=True,
                            stop_price=59900.0, stop_order_type="stop_loss_order",
                            stop_trigger_method="spot_price")

        async def no_spot(_s):          # premium readable, underlying missing
            return {"mark_price": 38.0, "close": 38.0, "spot_price": None}
        pc._quote = no_spot
        await c._sweep()
        return await c.get_positions()

    check_true("the leg is still held", len(run(go())) == 1)


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


def test_every_remote_call_is_bounded():
    """Rule 1. A paper follower sits in the same follower loop, under the same
    leg lock, as the real ones, so an unbounded wait here delays a REAL
    follower's copy.

    This used to assert there was exactly ONE remote call. That counted the
    wrong thing: when a second one was added (the product_id -> symbol lookup
    a bracket needs) the check went red on a change that was perfectly safe,
    which teaches you to edit the number and move on -- and the day the number
    is bumped past something genuinely unbounded, nothing says so.

    What actually matters is that EVERY remote call carries the timeout. So
    that is what is counted: one `timeout=MARK_TIMEOUT_SEC` per client opened.
    A new fetch without the bound fails this; a new fetch with it does not.
    """
    print("\n11. no remote call can hang a real follower")
    src = inspect.getsource(pc)
    clients = src.count("httpx.AsyncClient(")
    bounded = src.count("timeout=MARK_TIMEOUT_SEC")
    check_true("there is at least one remote call to bound", clients >= 1, clients)
    check_true("every remote call carries the timeout (%d client(s), %d bounded)"
               % (clients, bounded), clients == bounded, (clients, bounded))
    check_true("well under the live leg-lock wait",
               pc.MARK_TIMEOUT_SEC < 30.0, pc.MARK_TIMEOUT_SEC)
    # The product lookup is cached for the life of the process -- ids at Delta
    # are immutable -- so it is paid once per contract, not once per bracket.
    check_true("the product lookup is cached, not repeated per call",
               "_symbols[key] = sym" in src and "if key in _symbols:" in src)


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
              test_an_option_stop_triggers_off_the_underlying_not_the_premium,
              test_a_stop_whose_reference_cannot_be_read_waits,
              test_cancel_and_edit_behave_like_the_real_thing,
              test_the_balance_is_whatever_the_row_says,
              test_a_paper_account_can_never_be_a_master,
              test_it_never_invents_a_price,
              test_every_remote_call_is_bounded,
              test_every_local_call_answers_immediately,
              test_paper_order_ids_cannot_collide_with_real_ones):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
