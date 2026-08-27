"""Checks for the ORDER-level comparison.

Run:  venv/Scripts/python.exe test_order_compare.py
No pytest, no network.

The point of this module, and the reason it replaced the position view: the 15s
reconciler repairs net position, so a position-based comparison can only ever
report "matched". On 2026-08-27 the master sold 2750 lots, the follower's target
was 31, the engine punched 62, and the reconciler bought 31 back — net -31, which
the position view scored as a clean match on the very day the double-sizing bug
was doing damage.

The first test below is that exact case. It must read UNMATCHED here.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from app.core import order_compare as oc

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


T0 = datetime(2026, 8, 27, 2, 18, 46, tzinfo=timezone.utc)
START = datetime(2026, 8, 26, 18, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)

MASTER = {"id": "m1", "name": "Jigar", "is_master": True, "environment": "demo",
          "api_key": "k", "api_secret": "s", "balance": 6817.09, "owner_id": "o1"}


def follower(fid="f1", name="Mini Prathav", **kw):
    a = {"id": fid, "name": name, "is_master": False, "environment": "demo",
         "api_key": "k", "api_secret": "s", "status": "active", "owner_id": "o1",
         "allocation_mode": "auto_ratio", "balance": 76.31}
    a.update(kw)
    return a


def order(oid, symbol, side, size, at, *, filled=None, state="closed",
          order_type="limit_order", cancel_reason=None, stop=False):
    return {
        "id": oid, "product_symbol": symbol, "side": side, "size": size,
        "unfilled_size": size - (size if filled is None else filled),
        "state": state, "order_type": order_type,
        "cancellation_reason": cancel_reason,
        "stop_order_type": "stop_loss_order" if stop else None,
        "created_at": at.isoformat(), "updated_at": at.isoformat(),
        "limit_price": 24.0, "average_fill_price": 24.0, "reduce_only": False,
    }


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
    def __init__(self, legs=None, trades=None):
        self.data = {"trade_copies": legs or [], "trades": trades or []}

    def table(self, name):
        return FakeTable(list(self.data.get(name, [])))


def run(accounts, orders_by_account, db=None, errors=None):
    errors = errors or {}
    original = oc.fetch_account_orders

    async def fake(acc, start, end):
        if errors.get(acc["id"]):
            return {"orders": [], "error": errors[acc["id"]]}
        raw = orders_by_account.get(acc["id"], [])
        return {"orders": [oc.normalise_order(o) for o in raw], "error": None}

    oc.fetch_account_orders = fake
    try:
        return asyncio.run(oc.compare(accounts, START, END, db=db or FakeDB()))
    finally:
        oc.fetch_account_orders = original


def leg_for(row, aid):
    return next(l for l in row["legs"] if l["account_id"] == aid)


# ------------------------------------------------------------------- checks
def test_the_double_punch_is_caught():
    """THE case. ratio 76.31/6817.09 = 0.011194; 2750 x that = 30.8 -> 31."""
    print("\nTHE BUG the position view called 'matched': 62 punched against 31")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("1499459885", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [order("1499459996", "P-BTC-74500", "sell", 62, T0 + timedelta(seconds=4), filled=62)],
    })
    check("one master order row", len(out["rows"]), 1)
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "oversized")
    check("target", l["target_lots"], 31.0)
    check("what was punched", l["placed_lots"], 62.0)
    check_true("the note names the multiple", "2.0x" in l["note"], l["note"])
    check("counted as an error", out["summary"]["errors"], 1)
    check("match rate is 0%", out["summary"]["match_rate_pct"], 0.0)
    check("time diff measured", l["time_diff_ms"], 4000.0)


def test_correct_punch_is_matched():
    print("\nthe same order punched correctly reads matched")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [order("555", "P-BTC-74500", "sell", 31, T0 + timedelta(milliseconds=900), filled=31)],
    })
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "matched")
    check("no errors", out["summary"]["errors"], 0)
    check("ratio actual is reported", l["ratio_actual"], round(31 / 2750, 6))
    check("time diff in ms", l["time_diff_ms"], 900.0)


def test_undersized_is_caught():
    print("\nan under-punch is a mismatch too")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [order("555", "P-BTC-74500", "sell", 12, T0 + timedelta(seconds=1), filled=12)],
    })
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "undersized")
    check("quantified", l["note"], "punched 12 against a target of 31")


def test_missing_order():
    print("\nno follower order at all for a master order the master filled")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [],
    })
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "missing")
    check("an error", out["summary"]["errors"], 1)
    check("no invented time diff", l["time_diff_ms"], None)


def test_cancel_matching():
    print("\ncancels are compared too — invisible in fills, since nobody traded")
    f = follower()
    # Master cancelled without filling; the follower's mirror was cancelled too.
    out = run([MASTER, f], {
        "m1": [order("900", "C-BTC-81600", "sell", 2300, T0, filled=0, state="cancelled")],
        "f1": [order("555", "C-BTC-81600", "sell", 26, T0 + timedelta(seconds=2),
                     filled=0, state="cancelled")],
    })
    l = leg_for(out["rows"][0], "f1")
    check("both cancelled", l["verdict"], "cancelled_ok")
    check("not an error", out["summary"]["errors"], 0)


def test_cancel_missed_is_an_error():
    print("\nmaster cancelled but the follower's order is still resting")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "C-BTC-81600", "sell", 2300, T0, filled=0, state="cancelled")],
        "f1": [order("555", "C-BTC-81600", "sell", 26, T0 + timedelta(seconds=2),
                     filled=0, state="open")],
    })
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "cancel_missed")
    check("counted as an error", out["summary"]["errors"], 1)
    check_true("and says why", "still resting" in l["note"], l["note"])


def test_master_cancelled_follower_filled():
    print("\nmaster cancelled without filling but the follower traded anyway")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "C-BTC-81600", "sell", 2300, T0, filled=0, state="cancelled")],
        "f1": [order("555", "C-BTC-81600", "sell", 26, T0 + timedelta(seconds=2), filled=26)],
    })
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "extra")
    check("an error", out["summary"]["errors"], 1)


def test_ladder_is_not_graded_rung_by_rung():
    print("\na laddered exit: total right, so rungs are neutral not 'missing'")
    f = follower(allocation_mode="multiplier", allocation_value=0.1)
    m = [order(str(900 + i), "C-BTC-79800", "sell", 100, T0 + timedelta(minutes=i * 30), filled=100)
         for i in range(3)]
    # One follower cover order for the whole 300-lot ladder: 30 = 300 x 0.1.
    fo = [order("555", "C-BTC-79800", "sell", 30, T0 + timedelta(seconds=3), filled=30)]
    out = run([MASTER, f], {"m1": m, "f1": fo})
    verdicts = sorted(leg_for(r, "f1")["verdict"] for r in out["rows"])
    check("all three rungs neutral", verdicts, ["ladder", "ladder", "ladder"])
    check("no errors", out["summary"]["errors"], 0)


def test_ladder_that_is_genuinely_oversized():
    print("\na ladder punched at double still gets caught on its total")
    f = follower(allocation_mode="multiplier", allocation_value=0.1)
    m = [order(str(900 + i), "C-BTC-79800", "sell", 100, T0 + timedelta(minutes=i * 30), filled=100)
         for i in range(3)]
    fo = [order("555", "C-BTC-79800", "sell", 60, T0 + timedelta(seconds=3), filled=60)]
    out = run([MASTER, f], {"m1": m, "f1": fo})
    verdicts = sorted(leg_for(r, "f1")["verdict"] for r in out["rows"])
    check("the ladder is flagged", verdicts, ["oversized"] * 3)
    check_true("against the ladder total",
               "punched 60 against a target of 30" in leg_for(out["rows"][0], "f1")["note"],
               leg_for(out["rows"][0], "f1")["note"])


def test_paused_followers_excluded():
    print("\npaused followers are named, never graded")
    out = run([MASTER, follower(), follower("f2", "Paused", status="paused")], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [order("555", "P-BTC-74500", "sell", 31, T0 + timedelta(seconds=1), filled=31)],
        "f2": [order("777", "SOLUSD", "buy", 400, T0, filled=400)],
    })
    check("only the active follower has a leg", len(out["rows"][0]["legs"]), 1)
    check("no errors from the paused book", out["summary"]["errors"], 0)
    check("the paused one is named", out["excluded_followers"][0]["name"], "Paused")


def test_stop_orders_excluded():
    print("\njittered SL/TP are not one-for-one mirrors, so they are left out")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750, stop=True)],
        "f1": [],
    })
    check("no rows to grade", len(out["rows"]), 0)
    check("no errors", out["summary"]["errors"], 0)


def test_unreadable_follower():
    print("\nan account we could not read is not an account that did nothing")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
    }, errors={"f1": "401 Unauthorized"})
    l = leg_for(out["rows"][0], "f1")
    check("verdict", l["verdict"], "unreadable")
    check("not counted as an error", out["summary"]["errors"], 0)
    check_true("surfaced as a warning", any("unreadable" in w for w in out["warnings"]),
               str(out["warnings"]))


def test_time_diff_stats():
    print("\ntime diff is the headline number: median and p95, not just mean")
    f = follower(allocation_mode="multiplier", allocation_value=1.0)
    m, fo = [], []
    for i, off in enumerate([0.4, 0.8, 2.0, 30.0]):
        at = T0 + timedelta(minutes=i)
        m.append(order(str(900 + i), "BTCUSD", "buy", 10, at, filled=10))
        fo.append(order(str(500 + i), "BTCUSD", "buy", 10, at + timedelta(seconds=off), filled=10))
    out = run([MASTER, f], {"m1": m, "f1": fo})
    s = out["summary"]
    check("all four sampled", s["time_diff_samples"], 4)
    check("median is the typical case", s["median_time_diff_ms"], 2000.0)
    check("p95 exposes the slow one", s["p95_time_diff_ms"], 30000.0)
    check("max", s["max_time_diff_ms"], 30000.0)
    check("every order matched on size", s["errors"], 0)


def test_extra_order_on_untraded_symbol():
    print("\na follower order on a symbol the master never touched")
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750)],
        "f1": [order("555", "P-BTC-74500", "sell", 31, T0 + timedelta(seconds=1), filled=31),
               order("777", "SOLUSD", "buy", 9, T0 + timedelta(hours=2), filled=9)],
    })
    check("the mirror still matches", leg_for(out["rows"][0], "f1")["verdict"], "matched")
    check("the stray order is listed", len(out["extra_follower_orders"]), 1)
    check("with its id", out["extra_follower_orders"][0]["follower_order_id"], "777")


def test_renderers():
    print("\nall three renderings survive the incident data")
    from app.core import daily_report as dr
    f = follower()
    out = run([MASTER, f], {
        "m1": [order("900", "P-BTC-74500", "sell", 2750, T0, filled=2750),
               order("901", "C-BTC-81600", "sell", 2300, T0 + timedelta(minutes=4),
                     filled=0, state="cancelled")],
        "f1": [order("555", "P-BTC-74500", "sell", 62, T0 + timedelta(seconds=4), filled=62),
               order("556", "C-BTC-81600", "sell", 26, T0 + timedelta(minutes=4, seconds=2),
                     filled=0, state="open")],
    })
    out["window"]["date"] = "2026-08-27"

    text = dr.render_telegram(out)
    check_true("telegram names the day", "2026-08-27" in text, text[:100])
    check_true("leads with the unmatched count", "Unmatched" in text, text[:250])
    check_true("names the over-punch with its multiple", "2.0x" in text, text)
    check_true("and the missed cancel", "Cancel missed" in text, text)
    check_true("apostrophes are not mangled for Telegram", "&#x27;" not in text, text)
    check_true("reports time diff, not position", "Time diff" in text, text[:400])

    csv_out = dr.render_csv(out)
    lines = [l for l in csv_out.strip().splitlines() if l]
    check("csv header is the order-level shape", lines[0], ",".join(dr.CSV_COLUMNS))
    check_true("ratio columns are present",
               "ratio_actual" in lines[0] and "ratio_target" in lines[0], lines[0])
    check_true("time_diff_ms is a column", "time_diff_ms" in lines[0], lines[0])
    check_true("a row per leg", len([l for l in lines if l.startswith("2026-08-27,")]) == 2,
               str(lines[:4]))

    html = dr.render_html(out)
    check_true("html is a complete document",
               html.startswith("<!doctype html>") and html.rstrip().endswith("</html>"), html[:60])
    check_true("self-contained (no external requests)",
               "http://" not in html and "https://" not in html and "<script" not in html)
    check_true("shows the over-punch verdict", "Over-punched" in html)
    check_true("explains why orders and not positions", "reconciler repairs position" in html)

    check_true("ms formatting", (dr._ms(340), dr._ms(2500), dr._ms(90_000), dr._ms(None))
               == ("340 ms", "2.50 s", "1.5 min", "—"))
    check("a negative time diff keeps its sign", dr._ms(-250), "-250 ms")


def main():
    print("=" * 74)
    print("order_compare — grading the ORDERS, which the reconciler cannot repair")
    print("=" * 74)
    for fn in (
        test_the_double_punch_is_caught,
        test_correct_punch_is_matched,
        test_undersized_is_caught,
        test_missing_order,
        test_cancel_matching,
        test_cancel_missed_is_an_error,
        test_master_cancelled_follower_filled,
        test_ladder_is_not_graded_rung_by_rung,
        test_ladder_that_is_genuinely_oversized,
        test_paused_followers_excluded,
        test_stop_orders_excluded,
        test_unreadable_follower,
        test_time_diff_stats,
        test_extra_order_on_untraded_symbol,
        test_renderers,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
