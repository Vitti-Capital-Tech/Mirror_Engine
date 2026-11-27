# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Delta lists the same resting order under BOTH state filters. Nothing that
counts orders may count it twice.

Run:  cd backend && python tests/test_resting_double_count.py
No network, no Redis, no Supabase.

THE BUG. `get_open_orders(state="open")` and `get_open_orders(state="pending")`
return the SAME order. Proven against the live account on 2026-11-27:

    state=open    -> ['1719620374']
    state=pending -> ['1719620374']

Two functions read both pages and SUMMED what they saw, so every resting order
weighed double:

  * _follower_resting_cover — JD algo3 on P-BTC-58000-271126. The master was
    working two exit rungs of 2750 each. The follower held 5498 with ONE copy
    of 2749 resting, which was counted as 5498 — a fully covered leg. So the
    second rung was refused, "wanted 2749 but 5498 of 5498 is already covered
    by resting orders — placing 0", every 30 seconds for hours. The follower
    would have exited at the first rung and then nothing, left holding 2749
    against a flat master.
  * _resting_same_side — the entry-side twin. Doubled, the follower looks like
    it already has twice the lots working that it does, so its next entry is
    sized short or skipped.

The overlap was ALREADY handled in four other places — _master_exit_rungs
("keep one rung per id or it would be double-weighted in the allocation"), the
protection sweep, trade_listener and the positions API. It was missed in
exactly the two that size orders, which is the only place it changes a number
rather than a log line.
"""
import asyncio
import sys

from app.core import copy_engine as ce

FAILURES = []

SYM = "P-BTC-58000-271126"


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


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Client:
    """Delta's actual behaviour: both state filters return the same orders.

    `calls` records what was asked for, so a fix that simply stopped reading
    one of the two pages — which would lose the stops that only appear under
    'pending' — is not mistaken for a fix.
    """
    def __init__(self, orders, overlap=True):
        self.orders, self.overlap, self.calls = orders, overlap, []

    async def get_open_orders(self, state="open"):
        self.calls.append(state)
        if self.overlap:
            return [dict(o) for o in self.orders]
        return [dict(o) for o in self.orders if o.get("_state", "open") == state]


def order(oid, side, size, symbol=SYM, **kw):
    o = {"id": oid, "product_symbol": symbol, "side": side,
         "size": size, "unfilled_size": size, "reduce_only": True}
    o.update(kw)
    return o


def engine():
    """Just the fields these two readers touch — no DB, Redis or socket."""
    eng = object.__new__(ce.CopyEngine)
    eng._open_orders_cache = {}
    eng._open_orders_raw = {}
    return eng


# --------------------------------------------------------------------------
def test_cover_counts_each_order_once():
    print("\n1. a resting order listed under both states is counted ONCE")
    c = Client([order("1719620374", "sell", 2749)])
    eng = engine()
    cover = run(eng._follower_resting_cover(c, "acc-1", SYM, signed_pos=5498.0))
    check("2749 resting against a 5498 long", cover, 2749.0)
    check_true("it still read BOTH pages", c.calls == ["open", "pending"], c.calls)


def test_the_second_rung_is_placed():
    """The production symptom, end to end through the cap that refused it."""
    print("\n2. the ladder's second rung is no longer refused")
    c = Client([order("1719620374", "sell", 2749)])
    eng = engine()
    fol = {"id": "f-1", "name": "JD algo3"}
    qty = run(eng._cap_close_to_room(c, fol, SYM, "sell", 2749, 5498, 5498.0))
    check("wanted 2749, room for 2749", qty, 2749)


def test_the_cap_still_caps_when_it_should():
    """The guard this was protecting is NOT weakened: a leg genuinely covered
    by resting orders still refuses more cover. (2026-11-19, JD algo3 on
    C-BTC-65000: the same rung mirrored five times into a full book.)"""
    print("\n3. a genuinely covered leg is still refused")
    c = Client([order("a", "sell", 2749), order("b", "sell", 2749)])
    eng = engine()
    fol = {"id": "f-1", "name": "JD algo3"}
    qty = run(eng._cap_close_to_room(c, fol, SYM, "sell", 2749, 5498, 5498.0))
    check("5498 of 5498 already covered -> place 0", qty, 0)

    # And the partial case: half covered, half the room.
    c2 = Client([order("a", "sell", 2749)])
    eng2 = engine()
    qty2 = run(eng2._cap_close_to_room(c2, fol, SYM, "sell", 5498, 5498, 5498.0))
    check("2749 covered of 5498 -> room for 2749", qty2, 2749)


def test_resting_same_side_counts_each_order_once():
    print("\n4. the entry-side reader counts each order once too")
    c = Client([order("o1", "buy", 300, reduce_only=False)])
    total = run(ce.CopyEngine._resting_same_side(c, SYM, "buy"))
    check("300 resting on the buy side", total, 300)
    check_true("it still read BOTH pages", c.calls == ["open", "pending"], c.calls)

    # The other side is still not counted.
    c2 = Client([order("o1", "buy", 300, reduce_only=False)])
    check("nothing resting on the sell side",
          run(ce.CopyEngine._resting_same_side(c2, SYM, "sell")), 0)


def test_protection_is_still_excluded_from_cover():
    """Stops and take-profits are cover, not an exit in flight — counting them
    would stop us ever closing a protected leg."""
    print("\n5. stops and TPs are still not counted as cover")
    c = Client([
        order("stop", "sell", 5498, stop_order_type="stop_loss_order", stop_price="59900"),
        order("plain", "sell", 2749),
    ])
    eng = engine()
    cover = run(eng._follower_resting_cover(c, "acc-2", SYM, signed_pos=5498.0))
    check("only the plain order counts", cover, 2749.0)


def test_other_symbols_and_sides_are_untouched():
    print("\n6. another symbol, or an order that would not reduce, is ignored")
    c = Client([
        order("other", "sell", 9999, symbol="C-BTC-61200-271126"),
        order("adds", "buy", 9999),          # buy does not reduce a LONG
        order("mine", "sell", 2749),
    ])
    eng = engine()
    cover = run(eng._follower_resting_cover(c, "acc-3", SYM, signed_pos=5498.0))
    check("only this symbol's reducing order", cover, 2749.0)


def test_a_short_position_is_covered_by_buys():
    print("\n7. the sign convention still holds for a short")
    c = Client([order("b", "buy", 1200), order("s", "sell", 7777)])
    eng = engine()
    cover = run(eng._follower_resting_cover(c, "acc-4", SYM, signed_pos=-1200.0))
    check("a short is covered by the BUY", cover, 1200.0)


def test_an_unreadable_book_still_returns_nothing_rather_than_guessing():
    print("\n8. an unreadable book behaves as before")

    class Dead:
        async def get_open_orders(self, state="open"):
            raise RuntimeError("exchange down")

    eng = engine()
    check("cover is 0 when the book cannot be read",
          run(eng._follower_resting_cover(Dead(), "acc-5", SYM, signed_pos=5498.0)), 0.0)
    check("and the entry reader says None, not 0",
          run(ce.CopyEngine._resting_same_side(Dead(), SYM, "buy")), None)


def test_ids_are_unaffected_by_the_dedupe():
    """_live_order_ids feeds _order_is_live, which decides whether to re-place a
    mirror. A dedupe that lost an id would cause DUPLICATE orders — strictly
    worse than the bug being fixed."""
    print("\n9. the live-order id set is unchanged")
    c = Client([order("a", "sell", 1), order("b", "buy", 2)])
    eng = engine()
    ids = run(eng._live_order_ids(c, "acc-6", fresh=True))
    check("both ids present", ids, {"a", "b"})
    raw = eng._open_orders_raw["acc-6"][0]
    check("and the raw listing holds one entry per order", len(raw), 2)


def test_an_order_with_no_id_is_still_counted():
    """Defensive: a listing without ids must not collapse into one order."""
    print("\n10. orders without ids are not collapsed together")
    a, b = order(None, "sell", 100), order(None, "sell", 200)
    c = Client([a, b], overlap=False)
    eng = engine()
    cover = run(eng._follower_resting_cover(c, "acc-7", SYM, signed_pos=5498.0))
    check("both counted", cover, 300.0)


if __name__ == "__main__":
    for t in (test_cover_counts_each_order_once,
              test_the_second_rung_is_placed,
              test_the_cap_still_caps_when_it_should,
              test_resting_same_side_counts_each_order_once,
              test_protection_is_still_excluded_from_cover,
              test_other_symbols_and_sides_are_untouched,
              test_a_short_position_is_covered_by_buys,
              test_an_unreadable_book_still_returns_nothing_rather_than_guessing,
              test_ids_are_unaffected_by_the_dedupe,
              test_an_order_with_no_id_is_still_counted):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
