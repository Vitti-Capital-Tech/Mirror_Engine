# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A follower stopped out by its OWN stop stays out until the master closes.

Run:  venv/Scripts/python.exe test_stop_out.py
No network, no Redis, no Supabase.

THE INCIDENT, 2026-11-23, master Dhwani -> follower Dhwani1 (times IST).
Follower SL/TP sit tp_sl_offset (50) away from the master's, so either side can
trigger first.

  08:58  master opens P-BTC-59000 short / P-BTC-60800 long; copied in full
  10:02  BTC touches 61099.9: the FOLLOWER's stops (61100) fire and close the
         pair. The master's (61050) do not — BTC's low was 61056.6
  10:21  master SELLS 50 of 60800 (a partial exit). Any fresh master fill
         counted as a "fresh entry", so the reconciler bought the follower 253
         of 60800 two seconds later — one leg of a spread it had been stopped
         out of. The trader had to step in.

Desk call: the follower stays out of that leg — no recovery, no copied adds —
until the master closes it, and nothing alerts, because nothing is wrong. The
mirror case (the MASTER's stop first) was already hands-off; its "unmatched AND
unprotected" orphan alert was false too and is silenced here.

The checks that matter as much as the fix: a GENUINE miss is still recovered,
a stop from an earlier position does not freeze a new one, and the master
re-entering from flat is copied.
"""
import asyncio
import math
import sys
import time

import test_end_to_end as e2e
import test_split_fill as sf
from test_end_to_end import Exchange, Client, Redis, Sock, ConnMgr

FAILURES = e2e.FAILURES
check = e2e.check

SYM = "P-BTC-60800-231126"
MASTER, FOLLOWER = sf.MASTER, sf.FOLLOWER       # Dhwani, Dhwani1
MID, FID = MASTER["id"], FOLLOWER["id"]
RATIO = sf.RATIO


def share(lots):
    return int(math.ceil(abs(lots) * RATIO))


class MasterClient(Client):
    def __init__(self, ex, acct):
        super().__init__(ex, acct)
        self.fills = []

    async def get_fills(self, page_size=100):
        return list(self.fills)


class SetRedis(Redis):
    """The shared fake drops set members; the stopped-out index needs them."""

    def __init__(self):
        super().__init__()
        self.sets = {}

    async def sadd(self, k, *v): self.sets.setdefault(k, set()).update(str(x) for x in v)
    async def srem(self, k, *v):
        for x in v:
            self.sets.get(k, set()).discard(str(x))
    async def smembers(self, k): return set(self.sets.get(k, set()))


class FollowerClient(Client):
    def __init__(self, ex, acct):
        super().__init__(ex, acct)
        self.history = []

    async def get_order_history(self, page_size=50):
        return list(self.history)


def build():
    from app.core import copy_engine as ce
    from app.services import telegram_client as tg

    sent = []

    def recorder(name):
        async def rec(*a, **k):
            sent.append(name)
        return rec
    for name in ("notify_fail", "notify_correction", "notify_mismatch",
                 "notify_stuck", "notify_holding"):
        setattr(tg, name, recorder(name))

    ex = Exchange()
    mc, fc = MasterClient(ex, MID), FollowerClient(ex, FID)
    redis = SetRedis()
    eng = ce.CopyEngine(sf.DB(), redis, Sock(), ConnMgr({MID: mc, FID: fc}))
    eng._get_master_client = lambda row: mc

    async def classify(_r, _s): return "unknown"
    eng._classify_master_exit = classify

    async def execute(client, account, trade_id, symbol, side, quantity,
                      master_price, trade_type="entry"):
        await client.place_order(symbol=symbol, side=side, size=int(quantity),
                                 order_type="market_order",
                                 reduce_only=trade_type in ("exit", "sl"))
        return {"status": "filled", "filled_quantity": int(quantity),
                "account_id": account["id"], "account_name": account["name"]}
    ce.order_executor.execute = execute
    return eng, ex, mc, fc, redis, sent


def recon(ex):
    return {"owner_id": "u1", "master_account_id": MID,
            "positions": [{"symbol": s, "size": v, "mark": 250, "entry": 197}
                          for s, v in ex.pos.get(MID, {}).items() if v]}


def own_stop(at, size=303, side="sell", oid="1706662513", stop_type="take_profit_order"):
    """A FILLED follower SL/TP, as /v2/orders/history returns it."""
    return {"id": oid, "product_symbol": SYM, "side": side, "size": size,
            "unfilled_size": 0, "state": "closed", "stop_order_type": stop_type,
            "stop_price": "61100", "updated_at": at, "created_at": at - 3600}


def fill(at, side, size):
    return {"product_symbol": SYM, "side": side, "size": size, "created_at": at}


async def passes(eng, ex, n=3):
    for _ in range(n):
        eng._recon_open_ts.clear()
        await eng._reconcile_positions(recon(ex))
    await asyncio.sleep(0)   # let fire-and-forget alert tasks run


def stopped(redis):
    from app.core import order_ledger as ledger
    return asyncio.run(ledger.is_stopped_out(redis, "u1", FID, SYM))


# ---------------------------------------------------------------------------


def test_the_incident():
    print("\n2026-11-23: follower stopped out, master part-exits")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        out = {}
        # Master long 300 since 08:58 (stale); follower's own TP closed it 30s ago.
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 3800, "buy", 300)]
        fc.history = [own_stop(now - 30)]
        await passes(eng, ex)
        out["after_stop"] = ex.position(FID, SYM)
        out["alerts_after_stop"] = len(sent)

        # 10:21 — the master SELLS 50. This made the leg look "fresh".
        ex.set_position(MID, SYM, 250)
        mc.fills = [fill(now - 2, "sell", 50)] + mc.fills
        await passes(eng, ex)
        out["after_part_exit"] = ex.position(FID, SYM)
        out["orders"] = len(fc.placed)
        out["alerts"] = len(sent)
        return out

    r = asyncio.run(run())
    check("the follower's stop-out is left alone", r["after_stop"], 0.0)
    check("and no alert is sent for it", r["alerts_after_stop"], 0)
    check("the master's partial exit does NOT buy the follower back", r["after_part_exit"], 0.0)
    check("no order sent to the follower at all", r["orders"], 0)
    check("still no alert", r["alerts"], 0)
    check("the leg is marked stopped-out", bool(stopped(redis)), True)


def test_master_adds_are_not_copied_while_stopped_out():
    print("\nTHE MASTER ADDS TO THE LEG")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 3800, "buy", 300)]
        fc.history = [own_stop(now - 30)]
        await passes(eng, ex)
        # a market add …
        ex.set_position(MID, SYM, 400)
        await eng.process_fill(sf.fill_event("1707000001", "buy", 100) | {"symbol": SYM})
        after_market = ex.position(FID, SYM)
        # … and a resting limit add
        ex.place(MID, symbol=SYM, side="buy", size=100, order_type="limit_order", limit_price=200)
        moid = ex.resting(MID, SYM)[0]["id"]
        await eng.process_order_event({
            "action": "place", "master_order_id": str(moid), "symbol": SYM, "side": "buy",
            "size": 100.0, "state": "open", "order_type": "limit_order", "limit_price": 200,
            "owner_id": "u1", "master_account_id": MID, "product_id": 1})
        return after_market, len(ex.resting(FID, SYM))

    after_market, resting = asyncio.run(run())
    check("a market add is not copied", after_market, 0.0)
    check("a limit add is not mirrored", resting, 0)


def test_master_closing_ends_it_and_the_next_entry_copies():
    print("\nTHE MASTER CLOSES, THEN OPENS AGAIN")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 3800, "buy", 300)]
        fc.history = [own_stop(now - 30)]
        await passes(eng, ex)
        ex.set_position(MID, SYM, 0)
        await passes(eng, ex, 1)
        from app.core import order_ledger as ledger
        cleared = await ledger.is_stopped_out(redis, "u1", FID, SYM)
        ex.set_position(MID, SYM, 300)
        await eng.process_fill(sf.fill_event("1707000002", "buy", 300) | {"symbol": SYM})
        return cleared, ex.position(FID, SYM)

    cleared, follower = asyncio.run(run())
    check("the mark clears once the master is flat", cleared, None)
    check("the master's NEW position is copied", follower, float(share(300)))


def test_a_reentry_faster_than_the_sweep_is_still_copied():
    print("\nCLOSE AND RE-OPEN INSIDE ONE 10s SWEEP")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 3800, "buy", 300)]
        fc.history = [own_stop(now - 30)]
        await passes(eng, ex)
        # flat and back in before any reconcile pass sees it
        ex.set_position(MID, SYM, 200)
        await eng.process_fill(sf.fill_event("1707000003", "buy", 200) | {"symbol": SYM})
        return ex.position(FID, SYM)

    check("the new position is copied, not blocked by the old mark",
          asyncio.run(run()), float(share(200)))


def test_a_genuine_miss_is_still_recovered():
    print("\nNO STOP FIRED — A REAL MISS")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 30, "buy", 300)]   # fresh entry, copy never landed
        fc.history = []
        await passes(eng, ex)
        return ex.position(FID, SYM), len(sent)

    pos, alerts = asyncio.run(run())
    check("the reconciler opens the missing leg", pos, float(share(300)))
    check("and says so", alerts >= 1, True)


def test_a_stop_from_an_earlier_position_does_not_count():
    print("\nAN OLD STOP, A NEW POSITION")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 30, "buy", 300)]   # entered 30s ago …
        fc.history = [own_stop(now - 600)]         # … the stop is from before that
        await passes(eng, ex)
        return ex.position(FID, SYM)

    check("the new position is recovered as a miss", asyncio.run(run()), float(share(300)))


def test_a_master_exit_is_not_a_fresh_entry():
    print("\nA STALE MISS, THEN A MASTER EXIT (no stop involved)")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 250)
        mc.fills = [fill(now - 2, "sell", 50), fill(now - 3800, "buy", 300)]
        fc.history = []
        await passes(eng, ex)
        return ex.position(FID, SYM)

    check("a partial exit does not make a stale leg recoverable", asyncio.run(run()), 0.0)


def test_a_manual_reentry_resumes_normal_syncing():
    print("\nTHE TRADER RE-ENTERS THE FOLLOWER BY HAND")
    eng, ex, mc, fc, redis, sent = build()
    now = time.time()

    async def run():
        ex.set_position(MID, SYM, 300)
        mc.fills = [fill(now - 3800, "buy", 300)]
        fc.history = [own_stop(now - 30)]
        await passes(eng, ex)
        ex.set_position(FID, SYM, 300)
        await passes(eng, ex, 1)
        from app.core import order_ledger as ledger
        return await ledger.is_stopped_out(redis, "u1", FID, SYM)

    check("the mark clears; the leg is synced normally again", asyncio.run(run()), None)


def test_master_stop_first_no_false_orphan_alert():
    print("\nTHE MASTER'S STOP FIRES FIRST — FOLLOWER STILL HOLDS")
    from app.core.position_monitor import PositionMonitor
    from app.core import order_ledger as ledger
    from app.services import telegram_client as tg

    async def run(hands_off):
        sent = []

        async def notify_fail(*a, **k):
            sent.append(a)
        tg.notify_fail = notify_fail
        pm = PositionMonitor(None, None)
        pm.redis = SetRedis()
        if hands_off:
            await ledger.mark_hands_off(pm.redis, "u1", FID, SYM, "master SL/TP triggered")
        now = time.time()
        pm._orphan_since[(FID, SYM)] = (now - 120, now - 1)
        await pm._maybe_alert_orphan(FID, "Dhwani1", SYM, 303, owner_id="u1")
        return len(sent)

    real = tg.notify_fail
    try:
        check("hands-off (master's stop fired): no orphan alert", asyncio.run(run(True)), 0)
        check("a real orphan still alerts", asyncio.run(run(False)), 1)
    finally:
        tg.notify_fail = real


if __name__ == "__main__":
    for t in (test_the_incident,
              test_master_adds_are_not_copied_while_stopped_out,
              test_master_closing_ends_it_and_the_next_entry_copies,
              test_a_reentry_faster_than_the_sweep_is_still_copied,
              test_a_genuine_miss_is_still_recovered,
              test_a_stop_from_an_earlier_position_does_not_count,
              test_a_master_exit_is_not_a_fresh_entry,
              test_a_manual_reentry_resumes_normal_syncing,
              test_master_stop_first_no_false_orphan_alert):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
