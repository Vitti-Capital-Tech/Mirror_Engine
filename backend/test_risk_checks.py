"""Regression: what the pre-trade risk check does, and what it no longer does.

Run:  venv/Scripts/python.exe test_risk_checks.py
No network, no Redis, no Supabase.

THE CHANGE (2026-11-17). The margin check is gone. It was wrong in two
independent ways and the two cancelled each other out often enough to hide
both for months:

  * It omitted the CONTRACT VALUE. A BTC option lot is 0.001 BTC, so a leg
    costing ~117 USD was computed as 11,700. Every other part of the codebase
    multiplies by it; this did not.
  * `available_margin or _FALLBACK` treated 0.0 as "unknown" and substituted
    10,000 USD, so an account with genuinely nothing free was waved through —
    the single case the check existed to catch.

Net effect: it blocked accounts that HAD free margin to report and disabled
itself for accounts running at full tilt. Observed live — one follower's whole
book was refused locally (4 legs, 4 refusals, nothing reached the exchange)
while its master held the identical legs on 2,200 USD of margin and the
follower had 4,846 free. Meanwhile the other desk, which trades at zero free
margin by design, had never once been stopped by it.

Fixing the arithmetic was rejected: option margin is risk-based and depends on
the whole cross-margin portfolio, the direction, and unrealised P&L. Delta
computes it exactly. Anything derived from qty and price is a guess at a
number the exchange already knows, so Delta is the authority — send the order,
and a refusal is alerted and retried by the reconciler.

These tests exist to stop someone reintroducing it, and to prove the four
checks that DO matter still bite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.risk_engine import RiskEngine

FAILURES = []
re = RiskEngine()


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def acct(**kw):
    base = {
        "name": "Dhwani1",
        "status": "active",
        "consecutive_failures": 0,
        "leverage_limit": 10,
        "max_position_size": None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------


def test_the_book_that_was_refused_now_passes():
    print("\n1. THE LIVE CASE: four legs, four local refusals, nothing sent")
    # Dhwani1 held nothing, so its free margin read as its whole balance.
    # A real number — which is precisely what made the old check fire.
    a = acct(available_margin=4846.39, balance=4846.39)
    for symbol, qty, price in (("C-BTC-68000-181126", 3000, 39),
                               ("C-BTC-68400-181126", 3000, 28),
                               ("C-BTC-65800-181126", 300, 279),
                               ("C-BTC-66200-181126", 300, 181)):
        # qty * price / 10 was 11,700 / 8,400 / 8,370 / 5,430 — all "too much"
        # against 4,846, while the master carried the same four on ~2,200.
        ok, reason = re.check(a, qty, price)
        check(f"{symbol} allowed", (ok, reason), (True, ""))


def test_the_desk_that_runs_at_zero_is_unchanged():
    print("\n2. the account the old check silently skipped")
    # Trading on full balance reads as 0.0 free, which `or _FALLBACK` turned
    # into 10,000 — so this desk was never stopped. It must stay unstopped.
    a = acct(name="JD algo3", available_margin=0.0, balance=6670.10)
    check("zero free margin, still allowed", re.check(a, 2685, 1.5), (True, ""))
    check("a dear leg too", re.check(a, 3000, 279), (True, ""))


def test_a_negative_balance_is_delta_s_call_not_ours():
    print("\n3. even genuinely overdrawn — Delta decides, and says so")
    a = acct(available_margin=-54.99, balance=6670.10)
    check("sent anyway", re.check(a, 1752, 5.94), (True, ""))


def test_the_checks_that_remain_still_bite():
    print("\n4. the four that matter are untouched")
    paused = re.check(acct(status="paused"), 10, 5)
    check("paused account refused", paused[0], False)
    check("...and says why", "status" in paused[1].lower(), True)

    tripped = re.check(acct(consecutive_failures=5), 10, 5)
    check("circuit breaker refused", tripped[0], False)
    check("...and says why", "circuit breaker" in tripped[1].lower(), True)

    for q in (0, -5):
        bad = re.check(acct(), q, 5)
        check(f"quantity {q} refused", bad[0], False)

    capped = re.check(acct(max_position_size=100), 101, 5)
    check("over max_position_size refused", capped[0], False)
    check("...and says why", "max_position_size" in capped[1], True)
    check("at the cap exactly, allowed", re.check(acct(max_position_size=100), 100, 5),
          (True, ""))


def test_margin_is_never_the_reason_for_a_refusal():
    print("\n5. nothing in here may refuse on margin again")
    # The shapes that used to trigger it: no margin field at all, zero, a
    # huge quantity, a dear contract. None may come back as a margin refusal.
    for name, a, qty, price in (
        ("no margin field", acct(), 5000, 500),
        ("zero free", acct(available_margin=0.0), 5000, 500),
        ("tiny free", acct(available_margin=1.0), 5000, 500),
        ("overdrawn", acct(available_margin=-500.0), 5000, 500),
    ):
        ok, reason = re.check(a, qty, price)
        check(f"{name}: allowed", ok, True)
        check(f"{name}: no margin wording", "margin" in reason.lower(), False)


def test_price_is_no_longer_required():
    print("\n6. entry_price is vestigial — nothing may depend on it")
    check("absurd price changes nothing", re.check(acct(), 10, 9_999_999), (True, ""))
    check("zero price changes nothing", re.check(acct(), 10, 0), (True, ""))
    check("omitted entirely", re.check(acct(), 10), (True, ""))


if __name__ == "__main__":
    for t in (test_the_book_that_was_refused_now_passes,
              test_the_desk_that_runs_at_zero_is_unchanged,
              test_a_negative_balance_is_delta_s_call_not_ours,
              test_the_checks_that_remain_still_bite,
              test_margin_is_never_the_reason_for_a_refusal,
              test_price_is_no_longer_required):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
