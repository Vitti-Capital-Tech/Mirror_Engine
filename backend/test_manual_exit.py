"""Regression: a MANUAL master exit must be identified, not waited out.

Run:  venv/Scripts/python.exe test_manual_exit.py
No network, no Redis, no Supabase.

2026-09-01 01:27:40 UTC, three master legs inside one second:

    01:27:40.829  sell P-BTC-78200 limit_order state=open       <- manual close, 1000 lots
    01:27:41.044  master position -> 0                          <- flat FIRST
    01:27:41.087  sell P-BTC-78200 limit_order closed reason=fill
    01:27:41.109  sell P-BTC-78200 market_order cancelled reason=stop_cancel
                  ^ the exchange tidying away the leftover bracket
    01:27:41.886  "Filled-entry open ... master holds 0, follower holds 12
                   -> target 0, opening 0"                      <- evaluated LAST
    01:27:42.197  "Master exited P-BTC-78200 - leaving followers to their own
                   jittered SL/TP (no forced close)."
    01:34:10.787  reconciler closed the follower's 12           <- 6m30s late

Three faults stacked, and every one of them had to hold for the 12 lots to sit:

1. FIELD NAME. _classify_master_exit tested o["filled_size"]. /v2/orders/history
   does not return that field - verified live 2026-09-01, 0 of 100 rows carry the
   key while 69 of 100 are genuinely executed. Every order read as unexecuted, so
   the answer was "unknown" for every exit since the function was written (11h of
   logs: zero classifications, zero REST errors). Everything fell through to the
   orphan timer.

2. CLEANUP READ AS AN EVENT. reason=stop_cancel means the bracket was REMOVED,
   which the exchange does whenever a position closes - however it closed. Read as
   "his SL/TP fired", so the follower was left to a jittered stop sitting nowhere
   near 205 that was never going to trigger.

3. MISROUTED. The close was sized by the ENTRY path, which can only open. It
   worked out target 0, said "follower holds 12 of target 0" out loud, and
   returned, because closing is not that path's job.

The fix is not "close whenever the master is flat" - if the master's SL/TP really
fired, the follower's own jittered stop closes that leg and force-closing churns
it (Prathav, 2026-09-01). Every path now ASKS, and acts only on positive evidence
of a manual close. sl_tp and unknown are both left alone.
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

from app.core.copy_engine import CopyEngine, STALE_ORPHAN_SEC
from app.core.risk_engine import RiskEngine

FAILURES = []
SYM = "P-BTC-78200-010926"
MASTER_ROW = {"id": "m1", "api_key": "k", "api_secret": "s", "environment": "live"}

# The live ratio at 01:27:41 - follower 77.99850969 / master 6980.00663611.
FOLLOWER = {"id": "f1", "name": "Mini Prathav", "allocation_mode": "auto_ratio",
            "allocation_value": None, "balance": 77.99850969,
            "master_balance": 6980.00663611, "available_margin": 77.99850969}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Rows shaped exactly as live /v2/orders/history returns them: unfilled_size is
# present, filled_size is ABSENT. That absence is the whole first bug, so the
# fixtures must not quietly supply it.
# ---------------------------------------------------------------------------
def hist_row(oid, symbol, side, size, unfilled, stop_type=None,
             created="2026-09-01T01:27:41Z"):
    row = {
        "id": oid, "product_symbol": symbol, "side": side, "state": "closed",
        "size": size, "unfilled_size": unfilled, "order_type": "limit_order",
        "stop_order_type": stop_type, "reduce_only": False,
        "average_fill_price": "205", "created_at": created,
    }
    assert "filled_size" not in row, "fixture must mirror live Delta"
    return row


MANUAL_CLOSE = hist_row(1508812193, SYM, "sell", 1000, 0)
STOP_CLOSE = hist_row(1508794038, SYM, "sell", 1000, 0, stop_type="stop_loss_order")
NEVER_FILLED = hist_row(1508786521, SYM, "buy", 1000, 1000,
                        created="2026-09-01T01:03:23Z")


class MasterClient:
    def __init__(self, history):
        self.history = history
        self.calls = 0

    async def get_order_history(self, page_size=100):
        self.calls += 1
        return list(self.history)


def engine(history):
    eng = CopyEngine.__new__(CopyEngine)
    eng._master_exit_cache = {}
    eng._MASTER_EXIT_TTL = 5.0
    eng._master_clients = {}
    client = MasterClient(history)
    eng._get_master_client = lambda row: client
    eng._mc = client
    eng.risk_engine = RiskEngine()
    return eng


async def classify(history):
    return await engine(history)._classify_master_exit(MASTER_ROW, SYM)


# ------------------------------------------------- 1. the field-name bug

async def test_manual_close_is_identified():
    print("\n1. a plain limit close reads 'manual' (was 'unknown' forever)")
    check("classified", await classify([MANUAL_CLOSE]), "manual")


async def test_stop_close_is_identified():
    print("\n2. a stop-triggered close still reads 'sl_tp' - jitter rule intact")
    check("classified", await classify([STOP_CLOSE]), "sl_tp")


async def test_old_test_would_have_said_unknown():
    print("\n3. THE BUG: the old filled_size test on real Delta rows")
    # Exactly what the old line computed, against a row that DID execute.
    old = float(MANUAL_CLOSE.get("filled_size") or 0)
    check("old test saw 0 lots filled", old, 0.0)
    check("so the loop skipped a filled order", old <= 0, True)
    # The fixed test reads the same row correctly.
    check("fixed test sees 1000 filled", CopyEngine._filled_size(MANUAL_CLOSE), 1000)


async def test_unfilled_order_is_not_mistaken_for_an_exit():
    print("\n4. a cancelled-unfilled order must not be read as the close")
    # P-BTC-75600 01:03:23: 1000 requested, 0 filled, later cancelled. Reading it
    # as the exit would invent a close that never happened.
    check("no executed order -> unknown", await classify([NEVER_FILLED]), "unknown")


async def test_partial_fill_counts():
    print("\n5. a partly-filled close is still a close")
    partial = hist_row(1, SYM, "sell", 1000, 940)   # 60 lots done
    check("60 filled is executed", CopyEngine._filled_size(partial), 60)
    check("classified", await classify([partial]), "manual")


async def test_most_recent_executed_order_wins():
    print("\n6. the LAST executed order decides, not the last order")
    # A stop fired at 01:11, then a manual close at 01:27. The manual one is the
    # one that took the master flat.
    older_stop = hist_row(2, SYM, "sell", 500, 0, stop_type="take_profit_order",
                          created="2026-09-01T01:11:24Z")
    check("newest executed wins", await classify([older_stop, MANUAL_CLOSE]), "manual")
    # And a resting order created AFTER the close must not shadow it.
    later_resting = hist_row(3, SYM, "buy", 100, 100,
                             created="2026-09-01T01:30:00Z")
    check("unfilled newer row ignored",
          await classify([MANUAL_CLOSE, later_resting]), "manual")


async def test_other_symbols_are_ignored():
    print("\n7. a close on another strike must not answer for this one")
    other = hist_row(4, "P-BTC-77000-010926", "buy", 2470, 0)
    check("wrong symbol -> unknown", await classify([other]), "unknown")


async def test_result_is_cached_not_refetched():
    print("\n8. the 10s reconcile loop must not re-pull history every pass")
    eng = engine([MANUAL_CLOSE])
    check("first answer", await eng._classify_master_exit(MASTER_ROW, SYM), "manual")
    check("second answer", await eng._classify_master_exit(MASTER_ROW, SYM), "manual")
    check("history fetched once", eng._mc.calls, 1)


# ------------------------------------------- 2. routing close vs open

async def test_flat_master_routes_a_filled_close_to_the_exit_path():
    print("\n9. ROUTING: master flat + order settled = a close, not an entry")
    # 01:27:41.886 - the position had already gone to 0, so the "opposite side of
    # the position" test cannot see the long it reduced.
    why = CopyEngine._infer_reduce_only("sell", 0, master_order_filled=True)
    check("inferred as a close", bool(why), True)
    check("names the reason", "FLAT" in (why or ""), True)


async def test_the_old_marker_answer_misrouted_it():
    print("\n10. and with the old marker silent, it was routed to the OPEN path")
    why = CopyEngine._infer_reduce_only("sell", 0, master_order_filled=False)
    check("no close inferred - the bug", why, None)


async def test_master_still_holding_needs_no_extra_call():
    print("\n11. the normal case is decided on the position alone")
    # side=sell against a long: the first test fires and master_order_filled is
    # not consulted, so the routing gate must not pay for a get_order.
    why = CopyEngine._infer_reduce_only("sell", 1000, master_order_filled=False)
    check("inferred as a close", bool(why), True)
    check("reads the position", "holds" in (why or ""), True)


async def test_a_reversal_is_not_treated_as_a_close():
    print("\n12. a sell that adds to a short is not a close")
    check("adding to a short is not a close",
          CopyEngine._infer_reduce_only("sell", -500, master_order_filled=False),
          None)


# ------------------------------------------- 3. the stranded lot (min_one)

def target(master_remaining, min_one):
    return RiskEngine().calculate_follower_quantity(
        master_remaining, 205.0, FOLLOWER, round_up=True, min_one=min_one)


async def test_flat_master_gives_a_zero_target():
    print("\n13. P-BTC-77000: exit settle must be allowed to reach 0")
    check("target with the fix", target(0, min_one=False), 0)
    check("closes all 28", max(0, 28 - target(0, min_one=False)), 28)
    check("the old default gave 1", target(0, min_one=True), 1)
    check("which closed only 27", max(0, 28 - target(0, min_one=True)), 27)


async def test_a_small_remaining_position_never_targets_zero():
    print("\n14. min_one=False must NOT dump a follower while the master holds")
    # The worry: 40 lots left is 0.45 of a lot. Ceil keeps it at 1, so a live
    # master position can never produce target 0 and the follower is never
    # prematurely flattened.
    for left in (1, 5, 40, 88):
        check("master holds %d -> target >= 1" % left,
              target(left, min_one=False) >= 1, True)
    check("40 lots -> exactly 1", target(40, min_one=False), 1)


async def test_a_laddered_exit_lands_flat_only_at_the_end():
    print("\n15. a master exiting in 40-lot clips: follower flat exactly at 0")
    held, closes = 12, []
    for left in range(1000, -1, -40):
        t = target(left, min_one=False)
        if held > t:
            closes.append(held - t)
            held = t
    check("follower ends flat", held, 0)
    check("every step closed >= 1 lot", all(c >= 1 for c in closes), True)
    # The old behaviour on the same ladder: identical until the last step.
    held_old = 12
    for left in range(1000, -1, -40):
        t = target(left, min_one=True)
        if held_old > t:
            held_old = t
    check("the old path stranded a lot", held_old, 1)


# ------------------------------------------- 4. what the timer is for

async def test_the_orphan_timer_is_now_a_last_resort():
    print("\n16. STALE_ORPHAN_SEC applies to 'unknown' only")
    check("dropped to 60s", STALE_ORPHAN_SEC, 60.0)
    # A detected SL/TP goes hands-off and is never subject to the timer; a
    # detected manual close is acted on at once. Only "unknown" waits.
    check("manual is actionable", await classify([MANUAL_CLOSE]) == "manual", True)
    check("sl_tp is not", await classify([STOP_CLOSE]) == "manual", False)
    check("unknown is not", await classify([NEVER_FILLED]) == "manual", False)


class FollowerClient:
    """Captures what the settle path actually sends to the exchange."""

    def __init__(self):
        self.orders = []

    async def place_order(self, **kw):
        self.orders.append(kw)
        return {"id": "f-1"}


def _const(value):
    async def _f(*a, **k):
        return value
    return _f


async def run_settle(master_now, follower_signed):
    """Drive _settle_exit_after_cancel for real and return the orders it placed.

    Tests 13-15 pass min_one explicitly, so they only prove what the sizing
    function does - not that this call site asks it correctly. An earlier version
    of this test grepped the source for "min_one=False" and passed even with the
    fix reverted, because the phrase also appears in the comment above the call.
    So exercise the path instead and look at the order.
    """
    import app.core.copy_engine as ce
    eng = engine([MANUAL_CLOSE])
    eng.redis = None
    eng._position_size_signed = _const(follower_signed)
    eng._master_position_size = _const(master_now)
    client = FollowerClient()
    _orig = ce.ledger.is_hands_off
    ce.ledger.is_hands_off = _const(None)
    try:
        await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER_ROW, 205.0)
    finally:
        ce.ledger.is_hands_off = _orig
    return client.orders


async def test_exit_settle_closes_the_whole_holding_when_master_is_flat():
    print("\n17. P-BTC-77000 end to end: the settle path must close all 28")
    # 01:27:43.561 - master had gone to 0, the follower was short 28. It closed 27.
    orders = await run_settle(master_now=0, follower_signed=-28)
    check("placed exactly one order", len(orders), 1)
    if orders:
        o = orders[0]
        check("closes all 28, not 27", int(o.get("size")), 28)
        check("buys back a short", o.get("side"), "buy")
        check("reduce-only", bool(o.get("reduce_only")), True)
        check("at market - the master abandoned its price",
              o.get("order_type"), "market_order")


async def test_exit_settle_will_not_dump_a_follower_while_the_master_holds():
    print("\n18. and the same path must NOT flatten on a sub-1-lot share")
    # The worry about min_one=False: master still holds 40 (0.45 of a lot for us),
    # follower holds 1. Ceil keeps the target at 1, so nothing is owed and no
    # order may be sent.
    check("no order placed", await run_settle(master_now=40, follower_signed=-1), [])
    # And a genuine partial: master still holds 2470, follower holds 40 against a
    # target of 28, so only the excess goes.
    orders = await run_settle(master_now=2470, follower_signed=-40)
    check("trims the excess only", [int(o["size"]) for o in orders], [12])


async def test_an_opening_order_must_not_read_as_a_close():
    print("\n19. an OPENING order whose position has not landed yet")
    # 2026-09-02 05:09 IST, C-BTC-79400-020926. The master's sell OPENED a 3000
    # short. The cached position read 0, the order had filled, so the flat-and-
    # filled test concluded "this order closed the position", the reduce-only path
    # found nothing to reduce and placed NOTHING, and the reconciler recovered 34
    # lots 29s later. Pointing this test at _master_order_settled made it fire more
    # often and so made it worse: 0 recovered legs in the 10h before, 7 in the 31h
    # after. The routing now resolves the zero with a FRESH read first.
    #
    # Sell against a SHORT is adding, not closing - whatever the fill marker says.
    check("sell into a short is not a close",
          CopyEngine._infer_reduce_only("sell", -3000, master_order_filled=True), None)
    check("buy into a long is not a close",
          CopyEngine._infer_reduce_only("buy", 3000, master_order_filled=True), None)
    # A genuinely flat master after a fill still routes as a close.
    check("still flat after a fresh read -> a close",
          bool(CopyEngine._infer_reduce_only("sell", 0, master_order_filled=True)), True)
    # And the routing must ASK fresh before trusting a zero.
    import inspect
    src = inspect.getsource(CopyEngine._mirror_place)
    check("routing re-reads the position fresh on a zero",
          "fresh=True" in src.split("if msigned == 0:")[1][:220], True)


async def main():
    print("=" * 74)
    print("manual master exit - identify it, do not wait it out (2026-09-01)")
    print("=" * 74)
    for fn in (
        test_manual_close_is_identified,
        test_stop_close_is_identified,
        test_old_test_would_have_said_unknown,
        test_unfilled_order_is_not_mistaken_for_an_exit,
        test_partial_fill_counts,
        test_most_recent_executed_order_wins,
        test_other_symbols_are_ignored,
        test_result_is_cached_not_refetched,
        test_flat_master_routes_a_filled_close_to_the_exit_path,
        test_the_old_marker_answer_misrouted_it,
        test_master_still_holding_needs_no_extra_call,
        test_a_reversal_is_not_treated_as_a_close,
        test_flat_master_gives_a_zero_target,
        test_a_small_remaining_position_never_targets_zero,
        test_a_laddered_exit_lands_flat_only_at_the_end,
        test_the_orphan_timer_is_now_a_last_resort,
        test_exit_settle_closes_the_whole_holding_when_master_is_flat,
        test_exit_settle_will_not_dump_a_follower_while_the_master_holds,
        test_an_opening_order_must_not_read_as_a_close,
    ):
        await fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
