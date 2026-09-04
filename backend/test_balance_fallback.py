"""Regression: a transient master balance of 0 must not silently drop an event.

Run:  venv/Scripts/python.exe test_balance_fallback.py
No network, no Redis, no Supabase.

auto_ratio divides by the master's balance. risk_engine REFUSES to size when it
is 0 rather than fall back to 1:1, and that refusal is right — the 1:1 fallback
once produced a 610-lot target on a 70 USD account (2026-08-02).

But refusing drops the event ENTIRELY and tells nobody. On 2026-09-04 it fired 9
times in one day:

    Auto Ratio UNAVAILABLE — skipping sizing
    (follower_balance=79.60213467, master_balance=0.0). Refusing to fall back to 1:1

while the master's Supabase row read a healthy 7000+ throughout and the accounts
query returned 200 every time. So nine events were sized on nothing, nothing
alerted, and the only reason it surfaced was reading the logs by hand.
(Prathav, 2026-09-04: "4-5th we should have a failure mechanism".)

The fix substitutes the last balance that read as a real number. That is safe in a
way a 1:1 fallback is not: it is a REAL master balance, just not this instant's.
And the ratio is built on TOTAL balance precisely because that barely moves
(2026-08-03: available_margin swung 30% on unchanged positions), so a seconds-old
figure is a faithful stand-in. It warns every time it substitutes, and alerts once
per episode; with no cached value it still refuses, and now says so.
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

from app.core.copy_engine import CopyEngine
from app.core.risk_engine import RiskEngine

FAILURES = []
MASTER = {"name": "Jigar", "is_master": True, "status": "active",
          "balance": 7123.59368212}
FOLLOWER = {"id": "f1", "name": "Mini Prathav", "allocation_mode": "auto_ratio",
            "allocation_value": None, "balance": 79.60213467,
            "available_margin": 43.94695397}

# The live rows on 2026-09-04, in the shape _split_accounts reads them.
ROWS = [MASTER, dict(FOLLOWER, is_master=False, status="active")]


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def engine():
    eng = CopyEngine.__new__(CopyEngine)
    eng._last_master_balance = 0.0
    eng.risk_engine = RiskEngine()
    return eng


def test_a_real_balance_is_used_and_remembered():
    print("\n1. a real balance is used, and remembered for later")
    eng = engine()
    check("returns it", eng._master_balance_or_last(7123.59368212, MASTER), 7123.59368212)
    check("cached", eng._last_master_balance, 7123.59368212)


def test_a_transient_zero_falls_back():
    print("\n2. THE INCIDENT: a transient 0 uses the last good value")
    eng = engine()
    eng._master_balance_or_last(7123.59368212, MASTER)      # a good read first
    check("substitutes", eng._master_balance_or_last(0.0, MASTER), 7123.59368212)
    check("and again on a repeat", eng._master_balance_or_last(0, MASTER), 7123.59368212)
    check("cache not clobbered by the zero", eng._last_master_balance, 7123.59368212)


def test_the_event_is_now_sizeable():
    print("\n3. and the event can actually be sized again")
    eng = engine()
    eng._master_balance_or_last(7123.59368212, MASTER)
    bal = eng._master_balance_or_last(0.0, MASTER)
    fol = dict(FOLLOWER, master_balance=bal)
    qty = eng.risk_engine.calculate_follower_quantity(2400, 25.0, fol, round_up=True)
    check("2400 master lots -> 27", qty, 27)
    # What it did before the fix: master_balance 0 -> refuse -> 0 lots -> dropped.
    refused = eng.risk_engine.calculate_follower_quantity(
        2400, 25.0, dict(FOLLOWER, master_balance=0.0), round_up=True)
    check("the old behaviour returned 0", refused, 0)


def test_no_cached_value_still_refuses():
    print("\n4. with nothing cached it still REFUSES — never 1:1")
    eng = engine()
    check("returns 0, not a guess", eng._master_balance_or_last(0.0, MASTER), 0.0)
    # And risk_engine still refuses on that 0, which is the 2026-08-02 guard.
    q = eng.risk_engine.calculate_follower_quantity(
        2400, 25.0, dict(FOLLOWER, master_balance=0.0), round_up=True)
    check("sizing refused", q, 0)


def test_garbage_is_treated_as_missing():
    print("\n5. unusable input is treated as missing, not crashed on")
    eng = engine()
    eng._master_balance_or_last(7123.59368212, MASTER)
    check("None -> last good", eng._master_balance_or_last(None, MASTER), 7123.59368212)
    check("'' -> last good", eng._master_balance_or_last("", MASTER), 7123.59368212)
    check("junk -> last good", eng._master_balance_or_last("x", MASTER), 7123.59368212)
    check("negative is not 'real'", eng._master_balance_or_last(-5, MASTER), 7123.59368212)


def test_split_accounts_still_reads_the_row_correctly():
    print("\n6. sanity: the live rows do yield a real balance")
    # The master row on 04 Sep had allocated_balance=None and balance=7123.59, so
    # the zeros were NOT the row being empty — hence a fallback, not a row fix.
    m, f, bal = CopyEngine._split_accounts(ROWS)
    check("master found", (m or {}).get("name"), "Jigar")
    check("one active follower", len(f), 1)
    check("balance read from `balance`", bal, 7123.59368212)


def main():
    print("=" * 74)
    print("master balance fallback - a transient 0 must not drop the event")
    print("=" * 74)
    for fn in (
        test_a_real_balance_is_used_and_remembered,
        test_a_transient_zero_falls_back,
        test_the_event_is_now_sizeable,
        test_no_cached_value_still_refuses,
        test_garbage_is_treated_as_missing,
        test_split_accounts_still_reads_the_row_correctly,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
