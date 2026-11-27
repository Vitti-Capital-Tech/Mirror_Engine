# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""The daily report must only accuse the engine of things it actually did.

Run:  cd backend && python tests/test_report_truthfulness.py
No network, no Redis, no Supabase.

The 2026-11-25 report went out with an alarm and an 85% match rate on Pratik's
desk, on a day with SEVEN findings and nothing wrong. Every one came from the
report comparing against a number the engine never asked for:

  1. apply_ladder_context grouped by (symbol, side) across the ENTIRE DAY, with
     no time window. Three separate C-BTC-61400 sells at 10:21, 11:52 and 16:03
     became "a 3-rung ladder ... punched 355 against a target of 490". Each had
     punched exactly its recorded target -- 98 of 98, 98 of 98, 159 of 159 --
     and each was graded `matched` until this pass overwrote it.

     Measured over a week of live data, the rungs of a GENUINE ladder (one
     follower order covering several master rungs) sit 4-10s apart. Everything
     wider is a separate action.

  2. The ladder total re-derived its target from the balance ratio even when
     every rung had its target on record. C-BTC-66000: two rungs recording 2943
     and 2944, order sizes 2943 and 2944 -- "punched 5887 against a target of
     5878". The engine placed exactly what it asked for; the 9 lots were the
     ratio drifting during the day.

  3. A DERIVED target was compared to the lot. The ratio moved from 0.9797 to
     0.9814 across that day, which is five lots on a 3000-lot order, so orders
     placed in the morning read as "punched 2942 against a target of 2939".

  4. A copy the engine recorded as SKIPPED was graded against whatever follower
     order happened to sit near it in time. C-BTC-61000: the copy was skipped
     (requested 0) and, separately, the manual-exit closer flattened the leg --
     the report called that close a mirror and reported it 4 lots over.

A report that cries wolf stops being read, which costs more than the report is
worth. Each check below also pins the OPPOSITE: the real finding this report
exists for must still fire.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from app.core import order_compare as oc

FAILURES = []

T0 = datetime(2026, 11, 25, 4, 30, tzinfo=timezone.utc)
START = datetime(2026, 11, 24, 18, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)

# Pratik's desk as it actually stood: ratio 5794.53/5909.59 = 0.980529, so a
# 3000-lot master order is 2942 lots for the follower.
MASTER = {"id": "m1", "name": "Dhwani", "is_master": True, "environment": "demo",
          "api_key": "k", "api_secret": "s", "balance": 5909.5947475, "owner_id": "o1"}
FOLLOWER = {"id": "f1", "name": "Dhwani1", "is_master": False, "environment": "demo",
            "api_key": "k", "api_secret": "s", "status": "active", "owner_id": "o1",
            "allocation_mode": "auto_ratio", "balance": 5794.53080679}


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


def order(oid, symbol, side, size, at, *, filled=None, state="closed"):
    return {
        "id": oid, "product_symbol": symbol, "side": side, "size": size,
        "unfilled_size": size - (size if filled is None else filled),
        "state": state, "order_type": "limit_order", "cancellation_reason": None,
        "stop_order_type": None,
        "created_at": at.isoformat(), "updated_at": at.isoformat(),
        "limit_price": 24.0, "average_fill_price": 24.0, "reduce_only": False,
    }


def leg(master_oid, follower_oid, requested, status="filled", reason=None):
    """One row of trade_copies: the engine's own record of what it asked for."""
    return {"id": "l%s" % follower_oid, "trade_id": None, "account_id": "f1",
            "master_order_id": str(master_oid),
            "follower_order_id": (str(follower_oid) if follower_oid else None),
            "status": status, "quantity": requested,
            "requested_quantity": requested, "execution_price": 24.0,
            "slippage_pct": 0.0, "execution_time_ms": 400,
            "failure_reason": reason, "created_at": T0.isoformat()}


class _Res:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, rows):
        self._rows, self._f = rows, []

    def select(self, *_a, **_k):
        return self

    def in_(self, col, vals):
        self._f.append((col, [str(v) for v in vals]))
        return self

    def execute(self):
        out = self._rows
        for col, vals in self._f:
            out = [r for r in out if str(r.get(col)) in vals]
        return _Res(out)


class FakeDB:
    def __init__(self, legs=None):
        self.data = {"trade_copies": legs or [], "trades": []}

    def table(self, name):
        return FakeTable(list(self.data.get(name, [])))


def run(master_orders, follower_orders, legs=None):
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        raw = (master_orders if acc["id"] == "m1" else follower_orders)
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        return asyncio.run(oc.compare([MASTER, FOLLOWER], START, END,
                                      db=FakeDB(legs or [])))
    finally:
        oc.fetch_account_orders = original


def verdicts(out):
    return sorted(l["verdict"] for r in out["rows"] for l in r["legs"])


def notes(out):
    return [l["note"] for r in out["rows"] for l in r["legs"] if l["note"]]


# ---------------------------------------------------------------------------


def test_separate_trades_are_not_one_ladder():
    """THE 61400 CASE. Three sells on one contract, hours apart, each mirrored
    correctly on its own."""
    print("\n1. three separate sells on one contract, hours apart")
    m = [order("901", "C-BTC-61400-251126", "sell", 100, T0, filled=100),
         order("902", "C-BTC-61400-251126", "sell", 100,
               T0 + timedelta(seconds=2055), filled=100),
         order("903", "C-BTC-61400-251126", "sell", 300,
               T0 + timedelta(seconds=20116), filled=300)]
    f = [order("501", "C-BTC-61400-251126", "sell", 98,
               T0 + timedelta(seconds=1), filled=98),
         order("502", "C-BTC-61400-251126", "sell", 98,
               T0 + timedelta(seconds=2056), filled=98),
         order("503", "C-BTC-61400-251126", "sell", 159,
               T0 + timedelta(seconds=20117), filled=159)]
    out = run(m, f, [leg("901", "501", 98), leg("902", "502", 98),
                     leg("903", "503", 159)])
    check("all three matched", verdicts(out), ["matched"] * 3)
    check("no findings", out["summary"]["errors"], 0)
    check_true("and nothing calls them a ladder",
               not any("ladder" in n for n in notes(out)), notes(out))


def test_a_real_ladder_is_still_grouped():
    """The carve-out must be narrow. A burst of rungs covered by ONE follower
    order is the case apply_ladder_context exists for -- 36 false 'missing'
    copies on 2026-08-26 before it did."""
    print("\n2. a genuine ladder: rungs seconds apart, one cover order")
    m = [order(str(910 + i), "C-BTC-79800-251126", "sell", 100,
               T0 + timedelta(seconds=i * 5), filled=100) for i in range(3)]
    f = [order("555", "C-BTC-79800-251126", "sell", 295,
               T0 + timedelta(seconds=3), filled=295)]
    out = run(m, f)
    check("rungs read as ladder, not missing", verdicts(out), ["ladder"] * 3)
    check("no findings", out["summary"]["errors"], 0)


def test_a_real_ladder_punched_double_is_still_caught():
    print("\n3. the same ladder punched at double")
    m = [order(str(920 + i), "C-BTC-79800-251126", "sell", 100,
               T0 + timedelta(seconds=i * 5), filled=100) for i in range(3)]
    f = [order("556", "C-BTC-79800-251126", "sell", 590,
               T0 + timedelta(seconds=3), filled=590)]
    out = run(m, f)
    check("flagged", verdicts(out), ["oversized"] * 3)
    check_true("on the ladder's total",
               any("590" in n and "ladder" in n for n in notes(out)), notes(out))


def test_a_ladder_total_uses_the_recorded_targets():
    """THE 66000 CASE. Both rungs recorded, both punched exactly -- the total is
    2943 + 2944 = 5887 and must not be re-derived from today's ratio."""
    print("\n4. a two-rung burst where both rungs have a recorded target")
    m = [order("931", "C-BTC-66000-261126", "sell", 3000, T0, filled=3000),
         order("932", "C-BTC-66000-261126", "sell", 3000,
               T0 + timedelta(seconds=8), filled=3000)]
    f = [order("561", "C-BTC-66000-261126", "sell", 2943,
               T0 + timedelta(seconds=1), filled=2943),
         order("562", "C-BTC-66000-261126", "sell", 2944,
               T0 + timedelta(seconds=9), filled=2944)]
    out = run(m, f, [leg("931", "561", 2943), leg("932", "562", 2944)])
    # Eight seconds apart, so these two ARE one action: they group, and the
    # group's total is right, so both read `ladder` (a correct outcome, counted
    # as matched). What matters is the number it compared against.
    check("both neutral", verdicts(out), ["ladder"] * 2)
    check("no findings", out["summary"]["errors"], 0)
    check_true("the total came from the record, not the ratio",
               any("5887 of 5887" in n for n in notes(out)), notes(out))


def test_a_derived_target_is_not_compared_to_the_lot():
    """THE 2942-vs-2939 CASE. No leg on record, so the target is estimated from
    a ratio that moved during the day. Three lots in 3000 is the estimate's own
    error, not the engine's."""
    print("\n5. no recorded target, a few lots apart")
    m = [order("941", "C-BTC-62200-251126", "buy", 3000, T0, filled=3000)]
    f = [order("571", "C-BTC-62200-251126", "buy", 2945,
               T0 + timedelta(seconds=1), filled=2945)]
    out = run(m, f)                       # no legs: the target is derived
    l = out["rows"][0]["legs"][0]
    check_true("the target was derived",
               str(l["target_basis"]).startswith("derived"), l["target_basis"])
    check("matched", l["verdict"], "matched")


def test_a_derived_target_still_catches_a_real_miss():
    print("\n6. and a real over-punch on a derived target is still reported")
    m = [order("942", "C-BTC-62200-251126", "buy", 3000, T0, filled=3000)]
    f = [order("572", "C-BTC-62200-251126", "buy", 5884,
               T0 + timedelta(seconds=1), filled=5884)]
    out = run(m, f)
    l = out["rows"][0]["legs"][0]
    check("flagged", l["verdict"], "oversized")
    check_true("the note admits the target is an estimate",
               "estimated target" in l["note"] and "none on record" in l["note"],
               l["note"])
    check_true("and names the multiple", "2.0x" in l["note"], l["note"])


def test_a_skipped_copy_is_not_graded_on_a_nearby_order():
    """THE 61000 CASE. The engine recorded this copy as skipped, so it placed no
    mirror; the 298-lot order beside it was the manual-exit closer flattening
    the leg, not a mirror of this master order."""
    print("\n7. a copy the engine skipped, with an unrelated order beside it")
    m = [order("951", "C-BTC-61000-261126", "sell", 300, T0, filled=300)]
    f = [order("581", "C-BTC-61000-261126", "sell", 298,
               T0 + timedelta(seconds=2), filled=298)]
    out = run(m, f, [leg("951", None, 0.0, status="skipped",
                         reason="master leg already flat")])
    l = out["rows"][0]["legs"][0]
    check("reported as skipped", l["verdict"], "skipped")
    check("not counted as a finding", out["summary"]["errors"], 0)


def test_a_linked_order_on_a_skipped_leg_is_still_graded():
    """Narrow again: if the engine recorded a FOLLOWER ORDER ID on a leg it also
    called skipped, that order really is unexplained and must be reported."""
    print("\n8. a skipped leg that nonetheless recorded a mirror")
    m = [order("952", "C-BTC-61000-261126", "sell", 300, T0, filled=300)]
    f = [order("582", "C-BTC-61000-261126", "sell", 298,
               T0 + timedelta(seconds=2), filled=298)]
    out = run(m, f, [leg("952", "582", 0.0, status="skipped")])
    l = out["rows"][0]["legs"][0]
    check_true("still graded, not waved through",
               l["verdict"] in oc.MISMATCH_VERDICTS, l["verdict"])


def test_a_follower_added_today_is_not_blamed_for_yesterday():
    """THE 2026-11-26 CASE. A follower cannot have copied an order placed
    before it existed, and saying it is missing one is an accusation about a
    period it was not present for.

    Dhwani2 was added mid-afternoon and the day's report graded it against
    every master order back to 18:55 the previous evening: 36 "missing" legs,
    taking that desk from clean to 39%. Two paper followers added at 15:36 the
    same day contributed 40 each, putting a third desk at 28%. Three desks
    would have been reported as broken for behaving perfectly.
    """
    print("\n9. a follower added after the master's order")
    # The master trades at T0; the follower is created an hour later.
    born = (T0 + timedelta(hours=1)).isoformat()
    newbie = dict(FOLLOWER, id="f2", name="Dhwani2", created_at=born)
    m = [order("971", "C-BTC-64600-261126", "sell", 3000, T0, filled=3000)]
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        raw = m if acc["id"] == "m1" else []
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        out = asyncio.run(oc.compare([MASTER, newbie], START, END, db=FakeDB()))
    finally:
        oc.fetch_account_orders = original
    l = out["rows"][0]["legs"][0]
    check("not reported as missing", l["verdict"], "not_yet")
    check("and not counted as an error", out["summary"]["errors"], 0)
    check_true("the note says why",
               "added after" in (l["note"] or ""), l["note"])


def test_a_follower_that_existed_is_still_blamed():
    """Narrow. The same shape with the follower created BEFORE the order is a
    genuine missing copy and must still be reported -- that is the finding this
    report exists for."""
    print("\n10. and one that was already there")
    born = (T0 - timedelta(hours=1)).isoformat()
    old = dict(FOLLOWER, id="f3", name="Dhwani1", created_at=born)
    m = [order("972", "C-BTC-64600-261126", "sell", 3000, T0, filled=3000)]
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        raw = m if acc["id"] == "m1" else []
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        out = asyncio.run(oc.compare([MASTER, old], START, END, db=FakeDB()))
    finally:
        oc.fetch_account_orders = original
    check("still missing", out["rows"][0]["legs"][0]["verdict"], "missing")
    check("and still an error", out["summary"]["errors"], 1)


def test_an_unknown_creation_date_is_graded_normally():
    """An unparseable or absent date is not evidence of anything. Excusing a
    leg on a guess would be the same mistake in the other direction."""
    print("\n11. no creation date on the account")
    nodate = dict(FOLLOWER, id="f4", name="NoDate")
    nodate.pop("created_at", None)
    m = [order("973", "C-BTC-64600-261126", "sell", 3000, T0, filled=3000)]
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        raw = m if acc["id"] == "m1" else []
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        out = asyncio.run(oc.compare([MASTER, nodate], START, END, db=FakeDB()))
    finally:
        oc.fetch_account_orders = original
    check("graded, not excused", out["rows"][0]["legs"][0]["verdict"], "missing")


def test_an_expiry_settlement_is_not_a_copy():
    """THE OTHER 2026-11-26 CASE, and the larger one.

    Delta's BTC options expire at 12:00 UTC on the date in the symbol, and at
    that moment the exchange CLOSES every open position on the series itself.
    Those closes land on the master's book and the follower's as ordinary
    orders, so the report paired them and graded the sizes -- when neither
    account placed either one and the engine was never involved.

    Seven of them arrived between 12:00:00.18 and 12:00:02.47 on the -261126
    series, each reading "punched 2939 against an estimated target of ~2652".
    They were most of what put that desk at 75%. The sizes were simply the
    positions the two accounts held, opened in proportion hours earlier.

    Told apart by the CONTRACT: an order stamped at or after its own
    contract's expiry cannot be a trade in it, because the contract no longer
    exists to trade.
    """
    print("\n12. the exchange settling an expired contract")
    expiring = "C-BTC-63000-261126"          # expires 2026-11-26 12:00 UTC
    settle_at = datetime(2026, 11, 26, 12, 0, 2, tzinfo=timezone.utc)
    m = [order("981", expiring, "buy", 3000, settle_at, filled=3000)]
    f = [order("591", expiring, "buy", 2939, settle_at, filled=2939)]
    out = run(m, f)                           # no engine leg: nothing copied it
    l = out["rows"][0]["legs"][0]
    check("not graded as a copy", l["verdict"], "settled")
    check("and not counted as an error", out["summary"]["errors"], 0)
    check_true("the note says what it was",
               "settled this contract" in (l["note"] or ""), l["note"])


def test_a_real_trade_before_expiry_is_still_graded():
    """Narrow. The same contract, traded EIGHT MINUTES before it expires, is an
    ordinary trade and a bad copy of it is still a finding."""
    print("\n13. a real trade earlier the same day")
    expiring = "C-BTC-63000-261126"
    before = datetime(2026, 11, 26, 11, 52, 0, tzinfo=timezone.utc)
    m = [order("982", expiring, "buy", 3000, before, filled=3000)]
    f = [order("592", expiring, "buy", 5884, before + timedelta(seconds=1), filled=5884)]
    out = run(m, f)
    l = out["rows"][0]["legs"][0]
    check("still graded", l["verdict"], "oversized")
    check_true("and still names the multiple", "2.0x" in (l["note"] or ""), l["note"])


def test_a_symbol_with_no_expiry_is_graded_normally():
    """A perpetual, or anything whose tail will not parse as a date, is not
    excused -- a symbol we cannot read is not evidence of anything."""
    print("\n14. a non-expiring symbol at the same instant")
    settle_at = datetime(2026, 11, 26, 12, 0, 2, tzinfo=timezone.utc)
    m = [order("983", "BTCUSD", "buy", 3000, settle_at, filled=3000)]
    f = [order("593", "BTCUSD", "buy", 5884, settle_at + timedelta(seconds=1), filled=5884)]
    out = run(m, f)
    check("graded, not excused", out["rows"][0]["legs"][0]["verdict"], "oversized")


def test_a_ladder_does_not_charge_for_a_rung_it_was_never_asked_to_copy():
    """A rung the engine deliberately SKIPPED owed this follower nothing, so
    counting the master's lots for it inflates the group's target and reports a
    shortfall that never existed.

    2026-11-26, C-BTC-66800: the master sold 1034 and then 238 two seconds
    later. Dhwani1 mirrored the first exactly -- 1014 of a recorded 1014 -- and
    the engine skipped the second, recording "already at target 1014 (holds
    1014)". The group charged it for all 1272 and reported "punched 1014
    against a target of 1125", against a perfect leg and a rung it was never
    asked to copy. The skipped rung also carries a recorded target of 0, which
    forced the group onto a DERIVED total as well.
    """
    print("\n15. a ladder containing a deliberately skipped rung")
    t0 = T0
    m = [order("991", "C-BTC-66800-261126", "sell", 1034, t0, filled=1034),
         order("992", "C-BTC-66800-261126", "sell", 238,
               t0 + timedelta(seconds=2), filled=238)]
    f = [order("601", "C-BTC-66800-261126", "sell", 1014,
               t0 + timedelta(seconds=1), filled=1014)]
    legs = [leg("991", "601", 1014),
            leg("992", None, 0.0, status="skipped",
                reason="already at target 1014 (holds 1014)")]
    out = run(m, f, legs)
    verds = sorted(l["verdict"] for r in out["rows"] for l in r["legs"])
    check("the copied rung is not blamed", verds, ["ladder", "skipped"])
    check("and nothing is counted as an error", out["summary"]["errors"], 0)


def test_a_ladder_with_a_genuine_shortfall_is_still_caught():
    """Narrow. The same two rungs, both genuinely owed, with the follower short
    on the total -- still reported."""
    print("\n16. and a ladder that really is short")
    t0 = T0
    m = [order("993", "C-BTC-66800-261126", "sell", 1034, t0, filled=1034),
         order("994", "C-BTC-66800-261126", "sell", 1000,
               t0 + timedelta(seconds=2), filled=1000)]
    f = [order("602", "C-BTC-66800-261126", "sell", 1014,
               t0 + timedelta(seconds=1), filled=1014)]
    out = run(m, f)        # no legs recorded: both rungs owed, total derived
    verds = [l["verdict"] for r in out["rows"] for l in r["legs"]]
    check_true("still reported short", "undersized" in verds, verds)


def test_a_day_with_nothing_graded_is_not_reported_as_100_percent():
    """Tushar's desk, 2026-11-26: every leg settled at expiry, so NOTHING was
    judged — and the headline read 100% match.

    That is a claim the report cannot support. A rate over zero graded legs is
    not perfect, it is absent, and the difference matters precisely on the days
    nobody looked closely. The per-follower column has always rendered a dash
    for this; only the headline rounded nothing up to perfect.
    """
    print("\n17. a day where nothing could be graded")
    expiring = "C-BTC-63000-261126"
    settle_at = datetime(2026, 11, 26, 12, 0, 2, tzinfo=timezone.utc)
    m = [order("981", expiring, "buy", 3000, settle_at, filled=3000)]
    f = [order("591", expiring, "buy", 2939, settle_at, filled=2939)]
    out = run(m, f)
    check("nothing was graded", out["summary"]["graded"], 0)
    check("so there is no match rate", out["summary"]["match_rate_pct"], None)
    check("and still no errors", out["summary"]["errors"], 0)


def test_a_single_graded_leg_still_reports_a_rate():
    """The boundary: one judged leg is enough to have a rate again."""
    print("\n18. one graded leg is enough for a real rate")
    sym = "C-BTC-63000-261126"
    at = datetime(2026, 11, 26, 4, 0, tzinfo=timezone.utc)
    m = [order("981", sym, "buy", 3000, at, filled=3000)]
    f = [order("591", sym, "buy", 2942, at + timedelta(seconds=1), filled=2942)]
    out = run(m, f, [leg("981", "591", 2942)])
    check("graded", out["summary"]["graded"], 1)
    check("and the rate is real", out["summary"]["match_rate_pct"], 100.0)


if __name__ == "__main__":
    for t in (test_separate_trades_are_not_one_ladder,
              test_a_real_ladder_is_still_grouped,
              test_a_real_ladder_punched_double_is_still_caught,
              test_a_ladder_total_uses_the_recorded_targets,
              test_a_derived_target_is_not_compared_to_the_lot,
              test_a_derived_target_still_catches_a_real_miss,
              test_a_skipped_copy_is_not_graded_on_a_nearby_order,
              test_a_linked_order_on_a_skipped_leg_is_still_graded,
              test_a_follower_added_today_is_not_blamed_for_yesterday,
              test_a_follower_that_existed_is_still_blamed,
              test_an_unknown_creation_date_is_graded_normally,
              test_an_expiry_settlement_is_not_a_copy,
              test_a_real_trade_before_expiry_is_still_graded,
              test_a_symbol_with_no_expiry_is_graded_normally,
              test_a_ladder_does_not_charge_for_a_rung_it_was_never_asked_to_copy,
              test_a_ladder_with_a_genuine_shortfall_is_still_caught,
              test_a_day_with_nothing_graded_is_not_reported_as_100_percent,
              test_a_single_graded_leg_still_reports_a_rate):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
