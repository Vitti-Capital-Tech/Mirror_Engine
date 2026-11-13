"""Regression: unrealized PnL must be valued at the price you can exit at.

Run:  venv/Scripts/python.exe test_quote_pnl.py
No network — the ticker fetch is stubbed.

THE PROBLEM. PnL was computed off `mark_price`. On options that is nowhere near
the tradeable price. Measured live on 2026-11-13, C-BTC-72000-141126:

    mark_price  1.97        best_bid  2.8        best_ask  3

So the dashboard's PnL could not agree with Delta's own, and the gap was not
rounding — it was 40%+ on that contract. (Jigar, 2026-11-11: "mark price ki
jagah sirf bid ask use karna hai".)

A position is closed by CROSSING the book: a long sells into the bid, a short
lifts the ask. That is the price it is now valued at. The position payload
carries no quotes — only mark_price — so they come from the public ticker,
cached, because position updates arrive several times a second across ~16
symbols.
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

from app.core import quote_cache as qc

FAILURES = []
FETCHES = []

# The live book on 2026-11-13.
BID, ASK, MARK = 2.8, 3.0, 1.97024364
SYMBOL = "C-BTC-72000-141126"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def stub(bid=BID, ask=ASK, fail=False):
    qc._cache.clear()
    qc._inflight.clear()
    FETCHES.clear()

    async def _fake(symbol, rest_url):
        FETCHES.append(symbol)
        await asyncio.sleep(0)          # force a real await point
        if fail:
            qc._cache[symbol] = (qc.time.time(), None, None)
            return None, None
        qc._cache[symbol] = (qc.time.time(), bid, ask)
        return bid, ask

    qc._fetch = _fake


def pnl(side, entry, price, qty, mult):
    return ((price - entry) if side == "long" else (entry - price)) * qty * mult


# ---------------------------------------------------------------------------


def test_long_is_valued_at_the_bid():
    print("\n1. a LONG exits by selling into the BID")
    stub()
    px = asyncio.run(qc.exit_price_for(SYMBOL, "long", fallback=MARK))
    check("uses best_bid", px, BID)
    check("not the mark", px == MARK, False)


def test_short_is_valued_at_the_ask():
    print("\n2. a SHORT exits by lifting the ASK")
    stub()
    px = asyncio.run(qc.exit_price_for(SYMBOL, "short", fallback=MARK))
    check("uses best_ask", px, ASK)


def test_the_size_of_the_error():
    print("\n3. what this was costing on the live contract")
    stub()
    entry, qty, mult = 2.0, 2515, 0.001
    old = pnl("long", entry, MARK, qty, mult)
    new = pnl("long", entry, asyncio.run(qc.exit_price_for(SYMBOL, "long", fallback=MARK)), qty, mult)
    print("     mark-priced PnL : %+.2f" % old)
    print("     exit-priced PnL : %+.2f" % new)
    check("mark price showed a LOSS", old < 0, True)
    check("the book showed a PROFIT", new > 0, True)
    # Same position, opposite sign. This is why the numbers never matched.


def test_quotes_are_cached():
    print("\n4. one fetch per symbol per TTL, not one per position update")
    stub()
    for _ in range(25):
        asyncio.run(qc.exit_price_for(SYMBOL, "long", fallback=MARK))
    check("25 lookups -> 1 fetch", len(FETCHES), 1)


def test_concurrent_lookups_share_one_fetch():
    print("\n5. a burst of updates in one tick shares a single request")
    stub()

    async def burst():
        return await asyncio.gather(*[
            qc.exit_price_for(SYMBOL, "long", fallback=MARK) for _ in range(10)
        ])

    out = asyncio.run(burst())
    check("all got the bid", all(p == BID for p in out), True)
    check("but only one request went out", len(FETCHES), 1)


def test_an_unreadable_book_falls_back_to_mark():
    print("\n6. no book -> fall back to mark_price, never to nothing")
    stub(fail=True)
    px = asyncio.run(qc.exit_price_for(SYMBOL, "long", fallback=MARK))
    check("falls back", px, MARK)
    check("and does not crash", True, True)


def test_a_missing_side_of_the_book_falls_back():
    print("\n7. a one-sided book falls back for the missing side")
    stub(bid=None, ask=ASK)
    check("long has no bid -> mark",
          asyncio.run(qc.exit_price_for(SYMBOL, "long", fallback=MARK)), MARK)
    stub(bid=BID, ask=None)
    check("short has no ask -> mark",
          asyncio.run(qc.exit_price_for(SYMBOL, "short", fallback=MARK)), MARK)


def test_no_symbol_is_handled():
    print("\n8. a blank symbol must not fire a request")
    stub()
    check("returns the fallback",
          asyncio.run(qc.exit_price_for("", "long", fallback=MARK)), MARK)
    check("no fetch attempted", len(FETCHES), 0)


def main():
    print("=" * 74)
    print("quote-priced PnL - value a position at the price it can be closed at")
    print("=" * 74)
    real = qc._fetch
    try:
        for fn in (
            test_long_is_valued_at_the_bid,
            test_short_is_valued_at_the_ask,
            test_the_size_of_the_error,
            test_quotes_are_cached,
            test_concurrent_lookups_share_one_fetch,
            test_an_unreadable_book_falls_back_to_mark,
            test_a_missing_side_of_the_book_falls_back,
            test_no_symbol_is_handled,
        ):
            fn()
    finally:
        qc._fetch = real
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
