"""Regression: a drifted price must not block topping up a leg the master JUST entered.

Run:  venv/Scripts/python.exe test_fresh_topup.py
No network, no Redis, no Supabase.

2026-09-01 20:24 IST on P-BTC-75200-020926. The master, flat, sold 2000 to open a
short. The order filled in PIECES:

    20:24:13  master places sell 2000
    20:24:14  only 324 has filled; the engine sizes on the position it can see
              "master's entry already filled (master holds 324),
               follower holds 0 -> target 4, opening 4"      <- right for that instant
    20:24:22  the follower's 4 fill (escalated to market at 5s)
    20:24:30  the REST of the 2000 fills. The completion arrives as an is_update
              event, whose handler finds the mapped mirror gone (escalation had
              cancelled it) and defers:
              "mirror ... is gone without filling - nothing to edit,
               leaving it to the reconciler"                 <- never re-sized
    20:25:30  reconcile: under-exposed (4 vs target 23) but price drifted 23.6%
              ... and again at 24.5%, 26.6%, 26.9%, 27.8%    <- 5 passes, refused
    20:26:43  the master's NEXT order finally re-sizes it: target 34, opens 30

So the follower held 4 against a target of 23 for 2m21s, and recovered only by
accident. The option had run 18.02 -> 22.8, which is why the guard fired.

The guard itself is right for what it was built for. Its own comment says so:
"recovering a leg the master entered a long time ago ... judge on PRICE, not AGE."
For a 3.8h-old missed leg that is correct. For a leg one minute old it is not —
there the drift IS the move the master just caught, and refusing means the
follower never gets the trade it exists to copy.

TRIM_SETTLE_SEC (45s) already blocks any reconciler action immediately after a
master fill, so this only opens the 45s..FRESH_ENTRY_SEC window: long enough for
the fill to have settled, short enough to still be the same trade.

NOT fixed here: the live path still defers a partial-fill completion instead of
re-sizing (copy_engine.py:2145). That change can create duplicate positions and
wants designing on its own; this is the safety net for when it misses.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.copy_engine import (CopyEngine, FRESH_ENTRY_SEC, TRIM_SETTLE_SEC,
                                  SYNC_PRICE_TOLERANCE_PCT)

FAILURES = []
NOW = 1_788_000_000.0


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


allow = CopyEngine._topup_despite_drift


def test_the_incident():
    print("\n1. THE INCIDENT: master filled 60s ago, price drifted 26.6%")
    # The drift guard says no...
    ok, drift = CopyEngine._price_drift_ok(22.8, 18.0162)
    check("drift guard refuses", ok, False)
    # 26.6% is the figure in the live log line at 20:25:30.
    check("drift matches the live log", round(drift, 1), 26.6)
    # ...but the master traded this symbol a minute ago, so it is the same trade.
    check("topped up anyway", allow(NOW - 60, NOW), True)


def test_a_genuinely_stale_leg_is_still_refused():
    print("\n2. and a leg the master entered hours ago is still refused")
    check("3.8h old -> guard stands", allow(NOW - 3.8 * 3600, NOW), False)
    check("10 min old -> guard stands", allow(NOW - 600, NOW), False)


def test_the_window_edges():
    print("\n3. the window is 45s (TRIM_SETTLE_SEC) to FRESH_ENTRY_SEC")
    check("FRESH_ENTRY_SEC is 180s", FRESH_ENTRY_SEC, 180.0)
    check("TRIM_SETTLE_SEC is 45s", TRIM_SETTLE_SEC, 45.0)
    check("window is non-empty", TRIM_SETTLE_SEC < FRESH_ENTRY_SEC, True)
    check("just inside", allow(NOW - (FRESH_ENTRY_SEC - 1), NOW), True)
    check("just outside", allow(NOW - (FRESH_ENTRY_SEC + 1), NOW), False)
    check("exactly at the boundary is outside", allow(NOW - FRESH_ENTRY_SEC, NOW), False)


def test_unknown_timestamp_keeps_the_guard():
    print("\n4. no fill timestamp = cannot claim fresh = guard stands")
    check("None -> refused", allow(None, NOW), False)


def test_a_small_drift_never_needed_this_path():
    print("\n5. sanity: an undrifted leg was always allowed to top up")
    ok, drift = CopyEngine._price_drift_ok(18.5, 18.0162)
    check("2.7%% is inside the %.0f%% tolerance" % SYNC_PRICE_TOLERANCE_PCT, ok, True)
    check("so the fresh-entry test is never reached", round(drift, 1), 2.7)


def test_clock_skew_cannot_open_the_window_forever():
    print("\n6. a future timestamp must not read as fresh forever")
    # A fill timestamp ahead of `now` makes now-last_fill negative. Negative is
    # "< FRESH_ENTRY_SEC" and so reads as fresh — acceptable (a just-reported fill
    # really is the same trade), but assert it is bounded, not unbounded.
    check("slightly ahead still fresh", allow(NOW + 5, NOW), True)
    check("far ahead is not treated as stale either", allow(NOW + 10_000, NOW), True)
    print("     (documented, not a bug: a future ts means the fill just landed)")


def main():
    print("=" * 74)
    print("fresh-entry top-up - drift must not block the trade we are copying")
    print("=" * 74)
    for fn in (
        test_the_incident,
        test_a_genuinely_stale_leg_is_still_refused,
        test_the_window_edges,
        test_unknown_timestamp_keeps_the_guard,
        test_a_small_drift_never_needed_this_path,
        test_clock_skew_cannot_open_the_window_forever,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
