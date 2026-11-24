# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: don't cover a position that is already covered, and don't
re-place an order that keeps disappearing.

Run:  venv/Scripts/python.exe test_close_cover.py
No network, no Redis, no Supabase.

WHAT HAPPENED. 2026-11-19 22:19-22:21 UTC, JD algo3 on C-BTC-65000-201126 and
C-BTC-65200-201126:

    22:19:07  Mirrored 1697426756 -> JD algo3 1697427386 (qty 3200)
    22:19:57  Mirrored 1697426756 -> JD algo3 1697427932 (qty 3200)
    22:20:27  Mirrored 1697426756 -> JD algo3 1697428400 (qty 3200)
    22:20:58  Mirrored 1697426756 -> JD algo3 1697428872 (qty 3200)
    22:21:28  Mirrored 1697426756 -> JD algo3 1697429300 (qty 3200)

The same master rung, five times, roughly every thirty seconds, a fresh
3200-lot reduce-only each time. None of them filled. None of them was still
resting when the next sweep looked, so each sweep concluded the mirror had
never landed and placed another.

Ruled out from the logs: the master had not cancelled (his order was live
throughout), the resize path (ordersize read 3200 against a master size of
3200), the escalation (it backs off while the master's own order is live), and
pagination (get_open_orders pages through with the `after` cursor).

What was left: the follower's position was ALREADY fully covered by resting
orders — that account was being traded directly by its owner as well — so a
further reduce-only had nothing to reduce and did not survive at the exchange.
A POST that succeeds and then vanishes raises nothing, so the engine believed
each placement had worked.

TWO RULES PINNED HERE.

1. A close is capped by the room left on the position, not by the position
   alone. The follower's resting cover must never exceed what it holds.

2. A mirror that is placed and vanishes without filling is re-placed twice,
   then stopped and announced. The cap above should prevent this particular
   cause; the backstop is for every cause nobody has enumerated yet.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import asyncio
import time

from app.core.copy_engine import CopyEngine, VANISH_GIVE_UP

FAILURES = []

SYMBOL = "C-BTC-65000-201126"
FOLLOWER = {"id": "follower-algo3", "name": "JD algo3"}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def engine(raw_orders):
    e = CopyEngine.__new__(CopyEngine)
    e._open_orders_cache = {}
    e._open_orders_raw = {FOLLOWER["id"]: (raw_orders, time.time())}
    e._OPEN_ORDERS_TTL = 3.0
    return e


def order(side, size, symbol=SYMBOL, **extra):
    o = {"id": "x", "product_symbol": symbol, "side": side, "size": size}
    o.update(extra)
    return o


def cover(e, signed):
    return asyncio.run(
        e._follower_resting_cover(None, FOLLOWER["id"], SYMBOL, signed))


# --- how much is already covered -------------------------------------------


def test_the_live_case_reads_as_fully_covered():
    """Follower short 3200, its owner's own buy covering all of it."""
    print("\n1. THE LIVE CASE: 3200 short, 3200 already covered")
    e = engine([order("buy", 3200)])
    check("all of it", cover(e, -3200.0), 3200.0)


def test_a_long_is_covered_by_sells():
    print("\n2. the reducing side depends on which way the position is")
    e = engine([order("sell", 800)])
    check("a sell covers a long", cover(e, 800.0), 800.0)
    check("a sell does NOT cover a short", cover(e, -800.0), 0.0)
    e2 = engine([order("buy", 800)])
    check("a buy covers a short", cover(e2, -800.0), 800.0)
    check("a buy does NOT cover a long", cover(e2, 800.0), 0.0)


def test_protection_is_not_counted_as_cover():
    """A stop is there to protect the leg, not to exit it. Counting it would
    mean never being able to close a protected position."""
    print("\n3. stops, TPs and brackets are not an exit in flight")
    e = engine([
        order("buy", 3200, stop_order_type="stop_loss_order"),
        order("buy", 3200, stop_price="64000"),
        order("buy", 3200, bracket_order=True),
    ])
    check("none of them count", cover(e, -3200.0), 0.0)


def test_other_symbols_are_ignored():
    print("\n4. only this leg's orders")
    e = engine([order("buy", 3200, symbol="C-BTC-65200-201126")])
    check("a different strike is not cover", cover(e, -3200.0), 0.0)


def test_partial_fills_count_only_what_is_left():
    """A 3200-lot order with 1200 left covers 1200, not 3200."""
    print("\n5. an order half filled covers only its remainder")
    e = engine([order("buy", 3200, unfilled_size=1200)])
    check("the remainder", cover(e, -3200.0), 1200.0)


def test_several_orders_add_up():
    print("\n6. the whole resting ladder, not one rung")
    e = engine([order("buy", 1200), order("buy", 900), order("buy", 1100)])
    check("summed", cover(e, -3200.0), 3200.0)


def test_a_flat_leg_needs_no_lookup():
    print("\n7. nothing held, nothing to cover")
    e = engine([order("buy", 3200)])
    check("short-circuits", cover(e, 0.0), 0.0)


# --- capping the close ------------------------------------------------------


def cap(e, qty, held, side="buy", signed=None):
    return asyncio.run(e._cap_close_to_room(
        None, FOLLOWER, SYMBOL, side, qty, held,
        signed if signed is not None else (-held if side == "buy" else held)))


def test_the_live_case_now_places_nothing():
    print("\n8. THE FIX: a fully covered leg gets no further cover")
    e = engine([order("buy", 3200)])
    check("placed 0, not 3200", cap(e, 3200, 3200), 0)


def test_a_partly_covered_leg_gets_the_remainder():
    print("\n9. partly covered — cover only the rest")
    e = engine([order("buy", 2000)])
    check("1200 left", cap(e, 3200, 3200), 1200)


def test_an_uncovered_leg_is_untouched():
    """The ordinary case must behave exactly as it did before."""
    print("\n10. nothing resting — the close is unchanged")
    e = engine([])
    check("full size", cap(e, 3200, 3200), 3200)
    check("a smaller rung is not inflated", cap(e, 800, 3200), 800)


def test_the_cap_never_increases_an_order():
    print("\n11. the cap only ever reduces")
    e = engine([])
    check("stays at the rung's share", cap(e, 500, 3200), 500)


def test_an_unreadable_book_does_not_block_a_close():
    """Opposite direction from the trim guards, deliberately: refusing to
    close on an unknown leaves a position open that the master has exited."""
    print("\n12. an unreadable book must not stop an exit")
    e = CopyEngine.__new__(CopyEngine)
    e._open_orders_cache = {}
    e._open_orders_raw = {}
    e._OPEN_ORDERS_TTL = 3.0

    async def no_read(_client, _acct, fresh=False):
        return None

    e._live_order_ids = no_read
    check("closes at full size", cap(e, 3200, 3200), 3200)


# --- the vanish backstop ----------------------------------------------------


class Redis:
    def __init__(self, boom=False):
        self.n = {}
        self.boom = boom

    async def incr(self, k):
        if self.boom:
            raise RuntimeError("redis down")
        self.n[k] = self.n.get(k, 0) + 1
        return self.n[k]

    async def expire(self, *_a):
        return True


def test_it_retries_twice_then_stops():
    print("\n13. placed, vanished, placed, vanished — then stop")
    e = CopyEngine.__new__(CopyEngine)
    e.redis = Redis()

    async def run():
        return [await e._count_vanished("1697426756", "follower-algo3")
                for _ in range(5)]

    counts = asyncio.run(run())
    acted = [n < VANISH_GIVE_UP for n in counts]
    check("counts up", counts, [1, 2, 3, 4, 5])
    check("re-places twice, then gives up", acted, [True, True, False, False, False])
    check("the live run would have stopped at 3, not 5",
          sum(1 for a in acted if a) + 1, 3)


def test_the_threshold_is_sane():
    print("\n14. enough to ride out a transient, not enough to loop")
    check("more than one retry", VANISH_GIVE_UP >= 2, True)
    check("and not a whole session", VANISH_GIVE_UP <= 5, True)


def test_an_unreadable_counter_never_blocks_a_mirror():
    """Same direction as every other fallback: a Redis hiccup must not stop a
    legitimate copy. A missed mirror is a follower that silently stops
    tracking its master."""
    print("\n15. Redis down — keep mirroring")
    e = CopyEngine.__new__(CopyEngine)
    e.redis = Redis(boom=True)
    check("counts as zero",
          asyncio.run(e._count_vanished("1697426756", "follower-algo3")), 0)
    check("so the engine goes ahead", 0 < VANISH_GIVE_UP, True)


# --- wiring -----------------------------------------------------------------


def test_the_engine_uses_both():
    print("\n16. both are actually wired into the mirror path")
    src = inspect.getsource(CopyEngine._mirror_place)
    check("the close is capped", "self._cap_close_to_room(" in src, True)
    check("only for reduce-only", "if reduce_only and int(qty) >= 1" in src, True)
    check("the backstop counts", "self._count_vanished(" in src, True)
    check("and gives up", "_gone_at >= VANISH_GIVE_UP" in src, True)
    check("saying so", 'key=f"vanish:{follower[\'id\']}:{symbol}"' in src, True)
    check("a surviving mirror clears the count",
          'self.redis.delete(f"vanish:{master_order_id}' in src, True)


def test_the_cover_lookup_is_free():
    """It reuses the listing _order_is_live already fetched. Paying for a
    second identical REST call on the close path is the volume that delayed
    orders past Delta's signature window in July."""
    print("\n17. no extra REST call on the hot path")
    src = inspect.getsource(CopyEngine._live_order_ids)
    check("the raw listing is kept", "self._open_orders_raw[account_id]" in src, True)
    cov = inspect.getsource(CopyEngine._follower_resting_cover)
    check("and reused", "self._open_orders_raw.get(account_id)" in cov, True)


if __name__ == "__main__":
    for t in (test_the_live_case_reads_as_fully_covered,
              test_a_long_is_covered_by_sells,
              test_protection_is_not_counted_as_cover,
              test_other_symbols_are_ignored,
              test_partial_fills_count_only_what_is_left,
              test_several_orders_add_up,
              test_a_flat_leg_needs_no_lookup,
              test_the_live_case_now_places_nothing,
              test_a_partly_covered_leg_gets_the_remainder,
              test_an_uncovered_leg_is_untouched,
              test_the_cap_never_increases_an_order,
              test_an_unreadable_book_does_not_block_a_close,
              test_it_retries_twice_then_stops,
              test_the_threshold_is_sane,
              test_an_unreadable_counter_never_blocks_a_mirror,
              test_the_engine_uses_both,
              test_the_cover_lookup_is_free):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
