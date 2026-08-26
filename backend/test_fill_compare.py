"""Checks for the master-vs-follower fill comparison.

Run:  venv/Scripts/python.exe test_fill_compare.py
No pytest, no network — the exchange read is stubbed out.

What these are guarding
-----------------------
The comparison exists to answer "do the two accounts match?", and the ways it
can lie are all quiet ones: calling a copy missing when the engine just failed to
write the leg down, calling a correctly-sized 1-lot follower short against a
40-lot master, or reporting an account it could not read as one that traded
nothing. Each of those has a case below.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.core import fill_compare as fc

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        FAILURES.append(name)


# ------------------------------------------------------------------ fixtures
T0 = datetime(2026, 8, 26, 6, 30, 0, tzinfo=timezone.utc)
START = datetime(2026, 8, 25, 18, 30, 0, tzinfo=timezone.utc)   # IST midnight
END = START + timedelta(days=1)

MASTER = {
    "id": "m1", "name": "Master", "is_master": True, "environment": "demo",
    "api_key": "k", "api_secret": "s", "balance": 4000.0, "owner_id": "o1",
}


def follower(fid, name, **kw):
    acc = {
        "id": fid, "name": name, "is_master": False, "environment": "demo",
        "api_key": "k", "api_secret": "s", "status": "active", "owner_id": "o1",
        "allocation_mode": "auto_ratio", "balance": 100.0,
    }
    acc.update(kw)
    return acc


def fill(order_id, symbol, side, size, price, at, commission=0.0):
    return {
        "order_id": order_id, "product_symbol": symbol, "side": side,
        "size": size, "price": price, "created_at": at.isoformat(),
        "commission": commission, "meta_data": {"order_type": "limit_order"},
    }


class _Res:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Just enough Supabase surface for load_engine_legs()."""

    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def select(self, *_a, **_k):
        return self

    def in_(self, col, values):
        self._filters.append((col, [str(v) for v in values]))
        return self

    def execute(self):
        out = self._rows
        for col, values in self._filters:
            out = [r for r in out if str(r.get(col)) in values]
        return _Res(out)


class FakeDB:
    def __init__(self, legs=None, trades=None, accounts=None):
        self.data = {
            "trade_copies": legs or [],
            "trades": trades or [],
            "accounts": accounts or [],
        }

    def table(self, name):
        return FakeTable(list(self.data.get(name, [])))


def run_compare(accounts, fills_by_account, db=None, errors=None):
    """compare() with the exchange read stubbed.

    Patching fetch_account_fills rather than DeltaClient keeps the test on the
    logic under test: grouping and matching, not HTTP signing.
    """
    errors = errors or {}
    original = fc.fetch_account_fills

    async def fake(acc, start, end):
        err = errors.get(acc["id"])
        if err:
            return {"groups": [], "raw_count": 0, "error": err}
        raw = fills_by_account.get(acc["id"], [])
        return {"groups": fc.group_fills(raw), "raw_count": len(raw), "error": None}

    fc.fetch_account_fills = fake
    try:
        return asyncio.run(fc.compare(accounts, START, END, db=db))
    finally:
        fc.fetch_account_fills = original


def leg_for(row, account_id):
    return next(l for l in row["legs"] if l["account_id"] == account_id)


# ------------------------------------------------------------------ the checks
def test_grouping():
    print("\ngroup_fills — a limit order that fills in pieces is ONE order")
    groups = fc.group_fills([
        fill("100", "BTCUSD", "buy", 30, 100.0, T0),
        fill("100", "BTCUSD", "buy", 10, 120.0, T0 + timedelta(seconds=4)),
        fill("101", "BTCUSD", "sell", 5, 200.0, T0 + timedelta(seconds=9)),
    ])
    check("two orders from three fills", len(groups), 2)
    g = next(g for g in groups if g["order_id"] == "100")
    check("lots summed", g["lots"], 40.0)
    check("price volume-weighted", g["avg_price"], 105.0)
    check("fill count kept", g["fill_count"], 2)
    check("first_ts is the earliest", g["first_ts"], T0)
    check("last_ts is the latest", g["last_ts"], T0 + timedelta(seconds=4))
    check("groups sorted by first fill", [g["order_id"] for g in groups], ["100", "101"])


def test_parse_ts():
    print("\nparse_ts — Delta returns ISO strings on some endpoints, ints on others")
    iso = fc.parse_ts("2026-08-26T06:30:00Z")
    check("ISO string with Z", iso, T0)
    check("microseconds int", fc.parse_ts(int(T0.timestamp() * 1_000_000)), T0)
    check("milliseconds int", fc.parse_ts(int(T0.timestamp() * 1_000)), T0)
    check("seconds int", fc.parse_ts(int(T0.timestamp())), T0)
    check("naive string is treated as UTC", fc.parse_ts("2026-08-26T06:30:00"), T0)
    check("garbage is None, not an exception", fc.parse_ts("not a date"), None)
    check("empty is None", fc.parse_ts(""), None)


def test_linked_match():
    print("\na copy the engine recorded, correctly sized → matched")
    f = follower("f1", "Follower A", balance=100.0)   # ratio 100/4000 = 1/40
    db = FakeDB(legs=[{
        "id": "L1", "trade_id": None, "account_id": "f1",
        "master_order_id": "900", "follower_order_id": "555",
        "status": "filled", "quantity": 1, "requested_quantity": 1,
        "execution_time_ms": 340, "slippage_pct": 0.01, "failure_reason": None,
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.2, T0 + timedelta(milliseconds=1200))],
    }, db=db)

    check("one master order row", len(out["rows"]), 1)
    row = out["rows"][0]
    leg = leg_for(row, "f1")
    check("verdict", leg["verdict"], "matched")
    check("matched via the recorded leg", leg["link"], "linked")
    check("row status", row["status"], "matched")
    check("target came from the leg", leg["target_lots"], 1.0)
    check("target basis reported", leg["target_basis"], "recorded (what the engine asked for)")
    check("delay is follower fill minus master fill", leg["delay_ms"], 1200.0)
    check("no errors", out["summary"]["errors"], 0)
    check("match rate", out["summary"]["match_rate_pct"], 100.0)
    check("nothing left unexplained", out["unmatched_follower_fills"], [])
    check("ratio surfaced", out["followers"][0]["ratio"], 0.025)


def test_proportional_not_absolute():
    print("\na follower sized 1/40th is NOT short for filling 1 against 40")
    f = follower("f1", "Follower A", balance=100.0)
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1))],
    }, db=FakeDB())   # no legs at all — target must be derived
    leg = leg_for(out["rows"][0], "f1")
    check("derived target is the ratio-scaled size", leg["target_lots"], 1.0)
    check("verdict", leg["verdict"], "matched")
    check_true("basis says it was derived", leg["target_basis"].startswith("derived"),
               leg["target_basis"])


def test_missing_copy():
    print("\nmaster filled, follower did nothing, no record → missing (an error)")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [],
    }, db=FakeDB())
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "missing")
    check("row takes its worst leg", out["rows"][0]["status"], "missing")
    check("counted as an error", out["summary"]["errors"], 1)
    check("match rate reflects it", out["summary"]["match_rate_pct"], 0.0)
    check("no delay sample invented", leg["delay_ms"], None)


def test_short_fill():
    print("\nfollower filled less than the engine asked for → short")
    f = follower("f1", "Follower A")
    db = FakeDB(legs=[{
        "id": "L1", "account_id": "f1", "master_order_id": "900",
        "follower_order_id": "555", "status": "partial",
        "quantity": 4, "requested_quantity": 10,
        "failure_reason": "Partial fill: 4 of 10 lots",
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 400, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 4, 100.0, T0 + timedelta(seconds=2))],
    }, db=db)
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "short")
    check("note quantifies the shortfall", leg["note"], "filled 4 of 10 lots")
    check("counted as an error", out["summary"]["errors"], 1)
    check("the engine's reason is carried through", leg["leg_reason"], "Partial fill: 4 of 10 lots")


def test_one_lot_rounding_is_not_a_mismatch():
    print("\na 1-lot difference is rounding (opens floor, closes ceil), not a failure")
    f = follower("f1", "Follower A")
    db = FakeDB(legs=[{
        "id": "L1", "account_id": "f1", "master_order_id": "900",
        "follower_order_id": "555", "status": "filled",
        "quantity": 9, "requested_quantity": 10, "failure_reason": None,
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 400, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 9, 100.0, T0 + timedelta(seconds=1))],
    }, db=db)
    check("verdict", leg_for(out["rows"][0], "f1")["verdict"], "matched")
    check("no error raised", out["summary"]["errors"], 0)


def test_inferred_match():
    print("\na copy that reached the exchange but was never recorded → inferred, not missing")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        # Same symbol and side, 30s later, and no leg links the two.
        "f1": [fill("777", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=30))],
    }, db=FakeDB())
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "matched")
    check("labelled inferred so the reader knows", leg["link"], "inferred")
    check("the follower order id is reported", leg["follower_order_id"], "777")
    check("not double-counted as unexplained", out["unmatched_follower_fills"], [])


def test_late_copy_is_measured_not_condemned():
    print("\na late-but-real copy keeps its delay instead of being called a miss")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("777", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=600))],
    }, db=FakeDB())
    leg = leg_for(out["rows"][0], "f1")
    check("still a match — the accounts agree", leg["verdict"], "matched")
    check("and the lateness is measured, not hidden", leg["delay_ms"], 600_000.0)
    check("not counted as an error", out["summary"]["errors"], 0)
    check("nothing unexplained — the master traded this symbol", out["unmatched_follower_fills"], [])


def test_fill_on_untraded_symbol_is_unexplained():
    print("\na follower fill on a symbol the master never traded IS unexplained")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("777", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=2)),
               # The master has no SOLUSD order at all — the follower's own book.
               fill("888", "SOLUSD", "buy", 9, 210.0, T0 + timedelta(seconds=3))],
    }, db=FakeDB())
    check("the BTCUSD copy still matches", leg_for(out["rows"][0], "f1")["verdict"], "matched")
    check("the SOLUSD fill is listed", len(out["unmatched_follower_fills"]), 1)
    u = out["unmatched_follower_fills"][0]
    check("with its order id", u["follower_order_id"], "888")
    check("and an explanation", u["explanation"],
          "master never traded this symbol/side today — follower's own trade?")
    check("counted in the summary", out["summary"]["unmatched_follower_fills"], 1)


def test_side_mismatch_is_not_inferred():
    print("\ninference never crosses symbol or side")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("777", "BTCUSD", "sell", 1, 100.0, T0 + timedelta(seconds=2))],
    }, db=FakeDB())
    check("opposite side is not a match", leg_for(out["rows"][0], "f1")["verdict"], "missing")
    check("it shows up as unexplained instead", len(out["unmatched_follower_fills"]), 1)


def test_one_fill_claimed_once():
    print("\ntwo master orders cannot both claim the same follower fill")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [
            fill("900", "BTCUSD", "buy", 40, 100.0, T0),
            fill("901", "BTCUSD", "buy", 40, 100.0, T0 + timedelta(seconds=5)),
        ],
        "f1": [fill("777", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1))],
    }, db=FakeDB())
    claimed = [leg_for(r, "f1")["follower_order_id"] for r in out["rows"]]
    check("exactly one row claimed the fill", sum(1 for c in claimed if c), 1)
    check("and it claimed the right one", [c for c in claimed if c], ["777"])
    check("the other row claimed nothing", sorted(claimed, key=lambda c: c or ""), [None, "777"])


def test_unreadable_follower():
    print("\nan account we could not read must not be reported as one that didn't trade")
    f = follower("f1", "Follower A")
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
    }, db=FakeDB(), errors={"f1": "401 Unauthorized"})
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "unreadable")
    check("not counted as an error", out["summary"]["errors"], 0)
    check("flagged on the follower", out["followers"][0]["unreadable"], True)
    check_true("and surfaced as a warning", any("unreadable" in w for w in out["warnings"]),
               str(out["warnings"]))


def test_deliberate_skip_is_not_an_error():
    print("\na risk check doing its job is not a copy failure")
    f = follower("f1", "Follower A")
    db = FakeDB(legs=[{
        "id": "L1", "account_id": "f1", "master_order_id": "900",
        "follower_order_id": None, "status": "skipped",
        "failure_reason": "Insufficient margin", "requested_quantity": 1,
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [],
    }, db=db)
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "skipped")
    check("the reason is shown", leg["note"], "Insufficient margin")
    check("not an error", out["summary"]["errors"], 0)
    check("but not counted as a match either", out["summary"]["match_rate_pct"], 100.0)


def test_resting_order_is_not_missing():
    print("\na mirrored order still resting is doing its job, not failing")
    f = follower("f1", "Follower A")
    db = FakeDB(legs=[{
        "id": "L1", "account_id": "f1", "master_order_id": "900",
        "follower_order_id": "555", "status": "pending",
        "requested_quantity": 1, "failure_reason": None,
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [],   # placed, not yet filled
    }, db=db)
    check("verdict", leg_for(out["rows"][0], "f1")["verdict"], "resting")
    check("not an error", out["summary"]["errors"], 0)


def test_fill_path_legs_reached_via_trades():
    print("\nlegs from the master-FILL path are found through trades.master_trade_id")
    f = follower("f1", "Follower A")
    db = FakeDB(
        trades=[{"id": "T1", "master_trade_id": "900"},
                {"id": "T2", "master_trade_id": "ord:900"}],
        legs=[{"id": "L1", "trade_id": "T1", "account_id": "f1",
               "master_order_id": None, "follower_order_id": "555",
               "status": "filled", "quantity": 1, "requested_quantity": 1,
               "execution_time_ms": 210, "failure_reason": None}],
    )
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(milliseconds=800))],
    }, db=db)
    leg = leg_for(out["rows"][0], "f1")
    check("linked through the parent trade row", leg["link"], "linked")
    check("verdict", leg["verdict"], "matched")
    check("placement latency carried over", leg["place_latency_ms"], 210.0)


def test_delay_stats():
    print("\ndelay stats — median and p95, and a negative delay reported honestly")
    f = follower("f1", "Follower A")
    # Four copies: 100ms, 500ms, 2000ms, and one that filled 250ms BEFORE the
    # master's own order did (both rest at the same price, so this happens).
    offsets = [0.1, 0.5, 2.0, -0.25]
    m_fills, f_fills, legs = [], [], []
    for i, off in enumerate(offsets):
        oid, foid = str(900 + i), str(500 + i)
        at = T0 + timedelta(minutes=i)
        m_fills.append(fill(oid, "BTCUSD", "buy", 40, 100.0, at))
        f_fills.append(fill(foid, "BTCUSD", "buy", 1, 100.0, at + timedelta(seconds=off)))
        legs.append({"id": f"L{i}", "account_id": "f1", "master_order_id": oid,
                     "follower_order_id": foid, "status": "filled",
                     "quantity": 1, "requested_quantity": 1, "failure_reason": None})
    out = run_compare([MASTER, f], {"m1": m_fills, "f1": f_fills}, db=FakeDB(legs=legs))

    s = out["summary"]
    check("all four sampled", s["delay_samples"], 4)
    check("a negative delay is kept, not clamped",
          sorted(l["delay_ms"] for r in out["rows"] for l in r["legs"])[0], -250.0)
    check("mean", s["avg_delay_ms"], round((100 + 500 + 2000 - 250) / 4, 1))
    check("median is the typical case, not the outlier", s["median_delay_ms"], 500.0)
    check("p95 surfaces the tail", s["p95_delay_ms"], 2000.0)
    check("max", s["max_delay_ms"], 2000.0)
    pf = s["per_follower"][0]
    check("per-follower median too", pf["median_delay_ms"], 500.0)
    check("per-follower lots", pf["filled_lots"], 4.0)


def test_multiple_followers():
    print("\nseveral followers on one master order, graded independently")
    fa = follower("f1", "Follower A", balance=100.0)
    fb = follower("f2", "Follower B", balance=200.0)
    db = FakeDB(legs=[
        {"id": "L1", "account_id": "f1", "master_order_id": "900",
         "follower_order_id": "555", "status": "filled",
         "quantity": 1, "requested_quantity": 1, "failure_reason": None},
    ])
    out = run_compare([MASTER, fa, fb], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1))],
        "f2": [],
    }, db=db)
    row = out["rows"][0]
    check("both followers get a leg", len(row["legs"]), 2)
    check("A matched", leg_for(row, "f1")["verdict"], "matched")
    check("B missing", leg_for(row, "f2")["verdict"], "missing")
    check("the row reads as its worst leg", row["status"], "missing")
    check("one error", out["summary"]["errors"], 1)
    per = {p["account_id"]: p for p in out["summary"]["per_follower"]}
    check("A scores 100%", per["f1"]["match_rate_pct"], 100.0)
    check("B scores 0%", per["f2"]["match_rate_pct"], 0.0)
    check("B's ratio is double A's", per["f2"]["ratio"], 0.05)


def test_paused_followers_are_not_graded():
    """Regression — live run, 2026-08-26.

    Two of three followers were PAUSED. The engine copies only to
    status='active', so their fills are their own book, but they were graded
    anyway: one had traded ~996 lots on strikes the master never touched, which
    produced 36 phantom 'missing' legs, 44 phantom 'unexplained' fills, and
    buried the one active follower's real 72% under a 0% headline.
    """
    print("\npaused followers are named but never graded")
    active = follower("f1", "Active", status="active")
    paused = follower("f2", "Paused", status="paused")
    out = run_compare([MASTER, active, paused], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1))],
        # The paused account trades its own book, on a symbol the master never
        # touched. None of this may reach the verdict.
        "f2": [fill("666", "SOLUSD", "buy", 400, 210.0, T0 + timedelta(seconds=1))],
    }, db=FakeDB())

    check("only the active follower has a leg", len(out["rows"][0]["legs"]), 1)
    check("and it is the active one", out["rows"][0]["legs"][0]["account_name"], "Active")
    check("row is clean", out["rows"][0]["status"], "matched")
    check("no phantom errors", out["summary"]["errors"], 0)
    check("match rate is the active follower's", out["summary"]["match_rate_pct"], 100.0)
    check("paused fills are not 'unexplained'", out["summary"]["unmatched_follower_fills"], 0)
    check("only the active follower is summarised", len(out["summary"]["per_follower"]), 1)
    check("the paused one is named, not dropped", len(out["excluded_followers"]), 1)
    check("with its name", out["excluded_followers"][0]["name"], "Paused")
    check("and why", out["excluded_followers"][0]["reason"],
          "status is 'paused' — the engine does not copy to it")


def test_laddered_exit_is_one_exit():
    """Regression — live run, 2026-08-26.

    The master laddered a sell across nine orders spanning 5.7 hours on
    C-BTC-79800-260826, and the engine mirrors a ladder as ONE ladder. Grading
    rung-by-rung called eight of the nine 'missing' on a day the follower was
    tracking correctly.
    """
    print("\na laddered exit is judged on its total, not rung by rung")
    f = follower("f1", "Follower A", allocation_mode="multiplier", allocation_value=0.1)
    # Master ladders 300 lots across 3 rungs, hours apart.
    m_fills = [
        fill("900", "C-BTC-79800", "sell", 100, 5.0, T0),
        fill("901", "C-BTC-79800", "sell", 100, 5.1, T0 + timedelta(hours=2)),
        fill("902", "C-BTC-79800", "sell", 100, 5.2, T0 + timedelta(hours=4)),
    ]
    # The follower covers the whole ladder with ONE order: 30 lots = 300 x 0.1.
    f_fills = [fill("555", "C-BTC-79800", "sell", 30, 5.05, T0 + timedelta(seconds=3))]
    out = run_compare([MASTER, f], {"m1": m_fills, "f1": f_fills}, db=FakeDB())

    check("three master order rows", len(out["rows"]), 3)
    verdicts = sorted(leg_for(r, "f1")["verdict"] for r in out["rows"])
    check("every rung is neutral", verdicts, ["ladder", "ladder", "ladder"])
    check("NOT reported as missing", "missing" in verdicts, False)
    check("NOT reported as over either", "over" in verdicts, False)
    check("and each rung explains itself", leg_for(out["rows"][0], "f1")["note"],
          "part of a 3-rung ladder on C-BTC-79800 sell — the total reconciles (30 of 30 lots)")

    check("one group for the symbol/side", len(out["groups"]), 1)
    g = out["groups"][0]
    check("group verdict", g["verdict"], "matched")
    check("group knows it was laddered", g["laddered"], True)
    check("across three rungs", g["master_orders"], 3)
    check("master total", g["master_lots"], 300.0)
    check("target is the scaled total", g["target_lots"], 30.0)
    check("follower total", g["filled_lots"], 30.0)

    check("zero errors", out["summary"]["errors"], 0)
    check("match rate 100%", out["summary"]["match_rate_pct"], 100.0)
    check("the cover order is not 'unexplained'", out["summary"]["unmatched_follower_fills"], 0)


def test_ladder_that_really_is_short():
    print("\na ladder whose total falls short is still caught")
    f = follower("f1", "Follower A", allocation_mode="multiplier", allocation_value=0.1)
    m_fills = [
        fill("900", "C-BTC-79800", "sell", 100, 5.0, T0),
        fill("901", "C-BTC-79800", "sell", 100, 5.1, T0 + timedelta(hours=2)),
        fill("902", "C-BTC-79800", "sell", 100, 5.2, T0 + timedelta(hours=4)),
    ]
    # Covered only 12 of the 30 lots it owed.
    f_fills = [fill("555", "C-BTC-79800", "sell", 12, 5.05, T0 + timedelta(seconds=3))]
    out = run_compare([MASTER, f], {"m1": m_fills, "f1": f_fills}, db=FakeDB())

    g = out["groups"][0]
    check("group verdict", g["verdict"], "short")
    check("quantified against the total", g["note"],
          "filled 12 of 30 lots across 3 master order(s)")
    check("counted once, not once per rung", out["summary"]["errors"], 1)
    check("match rate 0%", out["summary"]["match_rate_pct"], 0.0)
    # The unfilled rungs stay 'missing' — the group did not reconcile, so there
    # is nothing to excuse them.
    verdicts = sorted(leg_for(r, "f1")["verdict"] for r in out["rows"])
    check("every rung reports the ladder's real verdict", verdicts, ["short", "short", "short"])
    check("with the group's reason, not a per-rung artefact",
          leg_for(out["rows"][0], "f1")["note"],
          "3-rung ladder: filled 12 of 30 lots across 3 master order(s)")


def test_per_rung_rounding_is_not_over_filling():
    """Regression — live run, 2026-08-26.

    The sizing path CEILS every placement, so each rung of a ladder can round up
    by just under a lot. A 9-rung exit filled 11 against a floored target of 7
    and was flagged 'over' — by-design rounding reported as a fault.
    """
    print("\naccumulated per-rung rounding is not over-filling")
    # ratio 100/4000 = 0.025. Nine rungs of 30 lots = 270 master lots.
    f = follower("f1", "Follower A", balance=100.0)
    m_fills, f_fills = [], []
    for i in range(9):
        at = T0 + timedelta(minutes=i * 20)
        m_fills.append(fill(str(900 + i), "C-BTC-79800", "sell", 30, 5.0, at))
        # Each rung ceils 30*0.025 = 0.75 -> 1 lot. Nine rungs -> 9 lots, where a
        # single ceiled total would be ceil(270*0.025) = 7.
        f_fills.append(fill(str(500 + i), "C-BTC-79800", "sell", 1, 5.0,
                            at + timedelta(seconds=2)))
    out = run_compare([MASTER, f], {"m1": m_fills, "f1": f_fills}, db=FakeDB())

    g = out["groups"][0]
    check("target is ceiled, matching the sizing path", g["target_lots"], 7.0)
    check("follower total", g["filled_lots"], 9.0)
    check("verdict is not 'over'", g["verdict"], "matched")
    check("no error", out["summary"]["errors"], 0)

    # But a real over-fill, beyond what rounding could explain, is still caught.
    fat = [fill("777", "C-BTC-79800", "sell", 40, 5.0, T0 + timedelta(seconds=2))]
    out2 = run_compare([MASTER, f], {"m1": m_fills, "f1": fat}, db=FakeDB())
    g2 = out2["groups"][0]
    check("a genuine over-fill is still flagged", g2["verdict"], "over")
    check_true("and the allowance is stated", "per-rung rounding" in g2["note"], g2["note"])
    check("counted as an error", out2["summary"]["errors"], 1)


def test_shortfall_gets_no_rounding_allowance():
    print("\nrounding up cannot cause a shortfall, so shortfalls get no allowance")
    f = follower("f1", "Follower A", balance=100.0)
    m_fills = [fill(str(900 + i), "C-BTC-79800", "sell", 100, 5.0,
                    T0 + timedelta(minutes=i * 30)) for i in range(4)]
    # 400 master lots x 0.025 = 10 expected; the follower covered 2.
    out = run_compare([MASTER, f], {
        "m1": m_fills,
        "f1": [fill("555", "C-BTC-79800", "sell", 2, 5.0, T0 + timedelta(seconds=2))],
    }, db=FakeDB())
    g = out["groups"][0]
    check("still short", g["verdict"], "short")
    check("quantified", g["note"], "filled 2 of 10 lots across 4 master order(s)")
    check("one error", out["summary"]["errors"], 1)


def test_group_missing_when_nothing_filled():
    print("\na symbol the follower never touched is one error, not N")
    f = follower("f1", "Follower A", allocation_mode="multiplier", allocation_value=0.1)
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "C-BTC-79800", "sell", 100, 5.0, T0),
               fill("901", "C-BTC-79800", "sell", 100, 5.1, T0 + timedelta(hours=2))],
        "f1": [],
    }, db=FakeDB())
    check("both rungs missing", [leg_for(r, "f1")["verdict"] for r in out["rows"]],
          ["missing", "missing"])
    check("but ONE group error", out["summary"]["errors"], 1)
    check("group says so plainly", out["groups"][0]["note"],
          "no follower fill on C-BTC-79800 sell at all")


def test_no_master():
    print("\nno master configured → say so rather than reporting a clean match")
    out = run_compare([follower("f1", "Follower A")], {"f1": []}, db=FakeDB())
    check("no rows", out["rows"], [])
    check("master is null", out["master"], None)
    check_true("and it is stated", any("No master" in w for w in out["warnings"]),
               str(out["warnings"]))


def test_ratio_refuses_to_guess():
    print("\nan unconfigured follower gets no invented target")
    unconfigured = follower("f1", "Follower A", allocation_mode=None)
    ratio, why = fc.follower_ratio(unconfigured, MASTER)
    check("no ratio", ratio, None)
    check("with a reason", why, "no allocation_mode set")

    zero_bal = follower("f2", "Follower B", balance=0.0, allocated_balance=None)
    ratio, why = fc.follower_ratio(zero_bal, MASTER)
    check("a zero balance does not become 1:1", ratio, None)
    check_true("and says why", "balance" in why, why)

    mult = follower("f3", "Follower C", allocation_mode="multiplier", allocation_value=0.5)
    check("multiplier is used directly", fc.follower_ratio(mult, MASTER)[0], 0.5)
    check("and scales the target", fc.expected_lots(40, mult, MASTER)[0], 20.0)

    fixed = follower("f4", "Follower D", allocation_mode="fixed", allocation_value=3)
    check("a fixed size ignores the master's lots", fc.expected_lots(40, fixed, MASTER)[0], 3.0)


def test_unsized_follower_filled():
    print("\na follower that filled with no derivable target is flagged, not passed")
    f = follower("f1", "Follower A", allocation_mode=None)
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0)],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1))],
    }, db=FakeDB())
    leg = leg_for(out["rows"][0], "f1")
    check("verdict", leg["verdict"], "unsized")
    check("no target", leg["target_lots"], None)
    check("not an error, but not a match either", out["summary"]["errors"], 0)
    check("excluded from the match rate", out["summary"]["match_rate_pct"], 100.0)


def test_ist_day_bounds():
    print("\nan IST trading day, not a UTC one")
    from datetime import date as _date
    start, end = fc.ist_day_bounds(_date(2026, 8, 26))
    check("starts at IST midnight", start.isoformat(), "2026-08-25T18:30:00+00:00")
    check("ends 24h later", end.isoformat(), "2026-08-26T18:30:00+00:00")
    check("exactly one day wide", end - start, timedelta(days=1))


def test_renderers():
    print("\nthe three report renderings survive real data")
    from app.core import daily_report as dr
    f = follower("f1", "Follower A")
    db = FakeDB(legs=[{
        "id": "L1", "account_id": "f1", "master_order_id": "900",
        "follower_order_id": "555", "status": "filled",
        "quantity": 1, "requested_quantity": 1, "failure_reason": None,
    }])
    out = run_compare([MASTER, f], {
        "m1": [fill("900", "BTCUSD", "buy", 40, 100.0, T0),
               fill("901", "BTCUSD", "sell", 40, 105.0, T0 + timedelta(minutes=5))],
        "f1": [fill("555", "BTCUSD", "buy", 1, 100.0, T0 + timedelta(seconds=1)),
               fill("999", "ETHUSD", "buy", 2, 50.0, T0 + timedelta(hours=3))],
    }, db=db)
    out["window"]["date"] = "2026-08-26"

    text = dr.render_telegram(out)
    check_true("telegram names the day", "2026-08-26" in text, text[:120])
    check_true("telegram leads with the error count", "Errors" in text, text[:200])
    check_true("telegram lists what needs attention", "Needs attention" in text, text[:400])
    check_true("telegram flags the unexplained fill",
               "Unexplained follower fills" in text, text[-300:])

    csv_out = dr.render_csv(out)
    lines = [l for l in csv_out.strip().splitlines() if l]
    check_true("csv leads with the reconciliation — that is the verdict",
               lines[0].startswith('"SYMBOL/SIDE RECONCILIATION'), lines[0])
    check_true("csv still carries the per-order detail header",
               ",".join(dr.CSV_COLUMNS) in lines, str(lines[:6]))
    check_true("csv has a row per leg", len([l for l in lines if l.startswith("2026-08-26,")]) >= 2,
               str(lines[:6]))
    check_true("csv carries the unexplained section",
               "UNEXPLAINED FOLLOWER FILLS" in csv_out, csv_out[-200:])

    html = dr.render_html(out)
    check_true("html is a complete document",
               html.startswith("<!doctype html>") and html.rstrip().endswith("</html>"), html[:60])
    check_true("html is self-contained (no external requests)",
               "http://" not in html and "https://" not in html and "<script" not in html)
    check_true("html shows the master name", "Master" in html)
    check_true("html shows a verdict pill", "Matched" in html)

    check_true("ms formatting is readable", (dr._ms(340), dr._ms(2500), dr._ms(90_000), dr._ms(None))
               == ("340 ms", "2.50 s", "1.5 min", "—"))
    check("a negative delay keeps its sign", dr._ms(-250), "-250 ms")


def main():
    print("=" * 72)
    print("fill_compare — master vs follower fill comparison")
    print("=" * 72)
    for fn in (
        test_grouping, test_parse_ts, test_linked_match, test_proportional_not_absolute,
        test_missing_copy, test_short_fill, test_one_lot_rounding_is_not_a_mismatch,
        test_inferred_match, test_late_copy_is_measured_not_condemned,
        test_fill_on_untraded_symbol_is_unexplained,
        test_side_mismatch_is_not_inferred, test_one_fill_claimed_once,
        test_unreadable_follower, test_deliberate_skip_is_not_an_error,
        test_resting_order_is_not_missing, test_fill_path_legs_reached_via_trades,
        test_delay_stats, test_multiple_followers,
        test_paused_followers_are_not_graded, test_laddered_exit_is_one_exit,
        test_ladder_that_really_is_short, test_per_rung_rounding_is_not_over_filling,
        test_shortfall_gets_no_rounding_allowance, test_group_missing_when_nothing_filled,
        test_no_master,
        test_ratio_refuses_to_guess, test_unsized_follower_filled,
        test_ist_day_bounds, test_renderers,
    ):
        fn()
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
