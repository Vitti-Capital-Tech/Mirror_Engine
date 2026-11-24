"""One master's cancel must never resize another group's follower.

Run:  venv/Scripts/python.exe test_cancel_cross_group.py
No network, no Redis, no Supabase.

THE INCIDENT, 2026-11-24 12:00:42 IST, C-BTC-62800.
Two groups hold the same strike: JD algo4 -> JD algo3 (+500), and
Dhwani -> Dhwani1 (-3000 / -3011). JD algo4 placed a limit order and cancelled
it ~0.2s later, before any follower copy existed, so the cancel had no
follower mapping. _mirror_cancel then fell back to EVERY active follower —
"deliberately NOT owner-scoped" — and ran the exit settle on each, sizing them
against JD algo4's position. Dhwani1 was "settled" to its share of +500:
closed 2602, 3011 -> 409. The reconciler would not buy it back (Dhwani's entry
was 3.5h old), so the follower stayed out of sync.
"""
import asyncio
import os
import sys

os.environ["ORDER_CONFIRM_SETTLE_SEC"] = "0"   # no settle sleep in tests

import test_end_to_end as e2e
from test_end_to_end import Exchange, Client, Redis, Sock, ConnMgr, DB, SYM

FAILURES = e2e.FAILURES
check = e2e.check


def build():
    from app.core.copy_engine import CopyEngine
    from app.services import telegram_client as tg

    async def quiet(*a, **k): return False
    tg.send_message = quiet

    DB.two_groups = True
    ex = Exchange()
    clients = {a: Client(ex, a) for a in ("m-jd", "f-algo3", "m-dh", "f-dh1")}
    eng = CopyEngine(DB(), Redis(), Sock(), ConnMgr(clients))
    eng._get_master_client = lambda row: clients[(row or {}).get("id", "m-jd")]

    async def classify(_r, _s): return "unknown"
    eng._classify_master_exit = classify
    return eng, ex, clients


def cancel_event(master_id, oid="1710403452"):
    return {"action": "cancel", "master_order_id": oid, "symbol": SYM,
            "side": "sell", "order_type": "limit_order", "state": "cancelled",
            "owner_id": "u1", "master_account_id": master_id, "product_id": 1}


def test_an_unmapped_cancel_stays_in_its_own_group():
    print("\nJD algo4 CANCELS AN ORDER THAT WAS NEVER COPIED (2026-11-24 12:00:42)")
    eng, ex, clients = build()
    try:
        async def run():
            ex.set_position("m-jd", SYM, 500)       # JD algo4
            ex.set_position("f-algo3", SYM, 446)    # its share, untouched
            ex.set_position("m-dh", SYM, -3000)     # Dhwani
            ex.set_position("f-dh1", SYM, -3011)    # Dhwani1
            await eng.process_order_event(cancel_event("m-jd"))
            return (ex.position("f-dh1", SYM), len(clients["f-dh1"].placed),
                    ex.position("f-algo3", SYM))
        dh1, dh1_orders, algo3 = asyncio.run(run())
    finally:
        DB.two_groups = False
    check("Dhwani1 is untouched by JD algo4's cancel", dh1, -3011.0)
    check("no order sent to Dhwani1", dh1_orders, 0)
    check("JD algo3 is not changed either (it was at its share)", algo3, 446.0)


def test_the_settle_still_works_inside_the_group():
    print("\nTHE SETTLE STILL RUNS FOR THE CANCELLING MASTER'S OWN FOLLOWER")
    eng, ex, clients = build()
    try:
        async def run():
            # Dhwani's exit limit half-filled and he cancelled the rest; Dhwani1's
            # copy filled nothing, so it is still holding everything (the 67200 case).
            ex.set_position("m-dh", SYM, -1500)
            ex.set_position("f-dh1", SYM, -3011)
            ex.set_position("m-jd", SYM, 500)
            ex.set_position("f-algo3", SYM, 446)
            await eng.process_order_event(cancel_event("m-dh", oid="1710500001"))
            return ex.position("f-dh1", SYM), ex.position("f-algo3", SYM)
        dh1, algo3 = asyncio.run(run())
    finally:
        DB.two_groups = False
    import math
    share = int(math.ceil(1500 * e2e.RATIO2))
    check("Dhwani1 is settled to its share of Dhwani's -1500", dh1, -float(share))
    check("JD algo3 is not touched by Dhwani's cancel", algo3, 446.0)


def test_confirm_copy_with_nothing_mirrored_stays_in_its_group():
    """confirm-copy checks every follower when nothing was mirrored for a master
    order. That fallback was owner-wide and took "the owner's first master", so
    with two groups under one owner a fill by one master was checked against —
    and could top up — the other group's follower."""
    print("\nCONFIRM-COPY, NOTHING MIRRORED, TWO GROUPS UNDER ONE OWNER")
    eng, ex, clients = build()
    try:
        async def run():
            # Same side in both groups: Dhwani1 is exactly at ITS master's share,
            # but small next to JD algo4's book — the case where sizing it
            # against the wrong master would buy it hundreds of lots.
            ex.set_position("m-dh", SYM, 100)
            ex.set_position("f-dh1", SYM, 100)
            ex.set_position("m-jd", SYM, 800)
            ex.set_position("f-algo3", SYM, 714)
            ev = {"master_order_id": "1710600001", "symbol": SYM, "side": "buy",
                  "size": 800.0, "unfilled_size": 0, "order_type": "limit_order",
                  "limit_price": 40, "state": "closed", "owner_id": "u1",
                  "master_account_id": "m-jd", "product_id": 1}
            await eng._confirm_order_copied(ev, "1710600001", mapping={})
            return len(clients["f-dh1"].placed), ex.position("f-dh1", SYM)
        n, pos = asyncio.run(run())
    finally:
        DB.two_groups = False
    check("no order sent to the other group's follower", n, 0)
    check("its position is untouched", pos, 100.0)


def test_the_master_is_the_one_the_event_names():
    print("\nWHICH MASTER: THE EVENT'S, NOT THE OWNER'S FIRST")
    from app.core.copy_engine import CopyEngine as CE
    accts = [e2e.MASTER, e2e.FOLLOWER, e2e.MASTER2, e2e.FOLLOWER2]   # same owner u1
    check("names Dhwani -> Dhwani (not JD, the owner's first master)",
          (CE._event_master(accts, {"owner_id": "u1", "master_account_id": "m-dh"}) or {}).get("id"), "m-dh")
    demoted = dict(e2e.MASTER2, is_master=False)
    check("a master demoted since the event -> nobody, not another master",
          CE._event_master([e2e.MASTER, demoted], {"owner_id": "u1", "master_account_id": "m-dh"}), None)
    check("an old event with no master id -> the owner's master",
          (CE._event_master(accts, {"owner_id": "u1"}) or {}).get("id"), "m-jd")
    check("a follower with no master follows nobody",
          CE._follows(dict(e2e.FOLLOWER2, master_id=None), e2e.MASTER2), False)


def test_a_follower_without_a_master_is_never_swept_in():
    print("\nA FOLLOWER WHOSE MASTER WAS DELETED (master_id NULL)")
    eng, ex, clients = build()
    saved = e2e.FOLLOWER2.get("master_id")
    e2e.FOLLOWER2["master_id"] = None
    try:
        async def run():
            ex.set_position("m-jd", SYM, 500)
            ex.set_position("f-algo3", SYM, 446)
            ex.set_position("f-dh1", SYM, -3011)
            await eng.process_order_event(cancel_event("m-jd", oid="1710700001"))
            return ex.position("f-dh1", SYM), len(clients["f-dh1"].placed)
        pos, n = asyncio.run(run())
    finally:
        e2e.FOLLOWER2["master_id"] = saved
        DB.two_groups = False
    check("the masterless follower is untouched", pos, -3011.0)
    check("no order sent to it", n, 0)


if __name__ == "__main__":
    for t in (test_an_unmapped_cancel_stays_in_its_own_group,
              test_the_settle_still_works_inside_the_group,
              test_confirm_copy_with_nothing_mirrored_stays_in_its_group,
              test_the_master_is_the_one_the_event_names,
              test_a_follower_without_a_master_is_never_swept_in):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
