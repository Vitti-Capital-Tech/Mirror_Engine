"""TEMPORARY desk rule: a follower's SL/TP always triggers AFTER the master's.

Run:  venv/Scripts/python.exe test_stop_after_master.py
No network, no Redis, no Supabase.

THE INCIDENT, 2026-11-23 (IST), master Dhwani -> follower Dhwani1.
The master's stops on P-BTC-59000/60800 sat at spot 61050; the follower's
copies at 61100 (+50, the side picked by follower slot). BTC fell to 61099.9
at 10:02 and bottomed at 61056.6 — the follower was stopped out, the master
was not, and the follower lost the pair.

Rule (Prathav, 2026-11-24 — "+50 upar, -50 neeche … exit always after
master"): a trigger ABOVE the current price fires on a rise, so the follower's
goes higher; one BELOW fires on a fall, so it goes lower.
"""
import asyncio
import sys

import test_end_to_end as e2e
from test_end_to_end import Exchange, Client

FAILURES = e2e.FAILURES
check = e2e.check
SYM = e2e.SYM


def test_the_rule():
    print("\nTHE RULE")
    from app.core.copy_engine import CopyEngine as CE
    f = CE._trigger_after_master
    check("incident: stop 61050 below spot 61400 -> follower 61000 (not 61100)",
          f(61050, 61400, 50), 61000.0)
    check("a stop above the price goes higher", f(61800, 61400, 50), 61850.0)
    check("the desk's offset is used", f(61050, 61400, 30), 61020.0)
    check("no offset set -> the default 50", f(61050, 61400, None), 61000.0)
    check("price unreadable -> AT the master's, never in front", f(61050, None, 50), 61050.0)
    check("would go to zero or below -> AT the master's", f(1.2, 2.0, 50), 1.2)
    check("no stop -> nothing", f(None, 61400, 50), None)


def test_the_incident_would_not_have_stopped_the_follower():
    print("\nREPLAY: BTC low 61056.6 against both stops (fall-triggered)")
    from app.core.copy_engine import CopyEngine as CE
    low = 61056.6
    master_stop = 61050.0
    follower_stop = CE._trigger_after_master(master_stop, 61400, 50)
    check("the master's stop was not hit", low <= master_stop, False)
    check("the follower's is not hit either", low <= follower_stop, False)
    check("and the follower's sits beyond the master's", follower_stop < master_stop, True)


class TickerClient(Client):
    def __init__(self, ex, acct, spot=None, mark=None, fail=False):
        super().__init__(ex, acct)
        self.spot, self.mark, self.fail = spot, mark, fail

    async def get_ticker(self, symbol):
        if self.fail:
            raise RuntimeError("ticker down")
        return {"spot_price": self.spot, "mark_price": self.mark, "close": self.mark}


def _mirror_stop(stop_price, spot, trigger="spot_price", fail=False):
    """Mirror one master stop through the real engine; return the follower's trigger."""
    eng, ex, mc, fc = e2e.build()
    tc = TickerClient(ex, "f-algo3", spot=spot, mark=40, fail=fail)
    eng.connection_manager.clients["f-algo3"] = tc

    async def run():
        ex.set_position("m-jd", SYM, -800)
        ex.set_position("f-algo3", SYM, -715)
        await eng.process_order_event({
            "action": "place", "master_order_id": "9101", "symbol": SYM,
            "side": "buy", "size": 800.0, "state": "pending",
            "order_type": "market_order", "stop_price": str(stop_price),
            "stop_order_type": "stop_loss_order", "stop_trigger_method": trigger,
            "product_id": 1, "owner_id": "u1", "master_account_id": "m-jd"})
        stops = [o for o in ex.resting("f-algo3", SYM) if o.get("stop_order_type")]
        return float(stops[0]["stop_price"]) if stops else None

    return asyncio.run(run())


def test_through_the_engine():
    print("\nTHROUGH THE ENGINE (protection mirrored to the follower)")
    # follower's tp_sl_offset in the e2e fixture is 50
    check("stop below spot -> follower 50 lower", _mirror_stop(61050, 61400), 61000.0)
    check("stop above spot -> follower 50 higher", _mirror_stop(61800, 61400), 61850.0)
    check("ticker down -> follower AT the master's", _mirror_stop(61050, 61400, fail=True), 61050.0)


if __name__ == "__main__":
    for t in (test_the_rule,
              test_the_incident_would_not_have_stopped_the_follower,
              test_through_the_engine):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
