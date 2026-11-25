# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""The reconciler must not announce a gap that closed while it was deciding.

Run:  cd backend && python tests/test_alert_last_look.py
No network, no Redis, no Supabase.

THE INCIDENT. 2026-11-25 22:55 IST, Dhwani1 on C-BTC-63200-261126 (Pratik's
desk). The master bought his -3000 short back by hand, filling in two pieces.
The follower's limit did not fill, so the copy fell back to market and that
fill took 7.2 seconds. For those seconds the master was flat and the follower
still showed 473 lots.

  22:55:31  master BUY 2500 of 3000 -> Dhwani1 sells 2471
  22:55:39  master fill reaches 3000 -> Dhwani1 sells the remaining 473
  22:55:47.4  market fallback fills; Dhwani1 is FLAT
  22:55:49.7  "leg busy ... (close) — skipping this pass"  <- the lock worked
  22:55:51.4  Telegram: "LEG THE MASTER DOES NOT HOLD — follower 473, target 0"

The force-close was correctly blocked by the leg lock, so no double order. The
only thing that escaped was the alert, and it described a leg that had been
flat for four seconds.

WHY. Every correction path takes a last look before it acts -- `fsz` is the
pass's opening read and several exchange round trips have gone by since. The
ALERT path had no last look at all. It spoke from the opening read.
"""
import asyncio
import inspect
import sys

from app.core import leg_locks
from app.core.copy_engine import CopyEngine

FAILURES = []
SYM = "C-BTC-63200-261126"
MKEY = "dde03e51-7c78-4a12-9320-f91a62497c20"
FOL = {"id": "f-dh1", "name": "Dhwani1"}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Client:
    """A follower whose live position is whatever the test says it is."""
    def __init__(self, size, raises=False):
        self.size, self.raises, self.reads = size, raises, 0

    async def get_positions(self):
        self.reads += 1
        if self.raises:
            raise RuntimeError("read timed out")
        if self.size is None:
            return []
        return [{"product_symbol": SYM, "size": self.size}]


def engine(client):
    e = CopyEngine.__new__(CopyEngine)

    async def _get(fol):
        return client
    e._get_follower_client = _get
    return e


def ask(kind, live_size, target, raises=False, busy=False):
    """Would the engine still raise this alert?"""
    leg_locks._locks.clear()
    client = Client(live_size, raises)
    e = engine(client)

    async def go():
        if busy:
            lock = leg_locks.get(MKEY, SYM)
            await lock.acquire()
            try:
                return await e._discrepancy_still_true(MKEY, kind, FOL, SYM, target)
            finally:
                lock.release()
        return await e._discrepancy_still_true(MKEY, kind, FOL, SYM, target)

    return asyncio.run(go())


# ---------------------------------------------------------------------------


def test_the_live_case_stays_quiet():
    """THE INCIDENT: 'extra leg' on a follower that is already flat."""
    print("\n1. the 22:55 case — follower flat by the time we speak")
    check("no alert", ask("close", live_size=0.0, target=0), False)


def test_a_real_extra_leg_still_alerts():
    """The carve-out must be narrow. A follower that genuinely still holds a
    leg the master does not is the whole reason this alert exists."""
    print("\n2. the same alert when the leg is really still there")
    check("alerts", ask("close", live_size=-473.0, target=0), True)


def test_a_copy_in_flight_is_not_a_mismatch():
    """The live mirror holding this leg is positive evidence of a copy in
    flight. That is a different thing from a fault."""
    print("\n3. the live mirror is mid-flight on this leg")
    check("no alert", ask("close", live_size=-473.0, target=0, busy=True), False)


def test_an_unreadable_book_still_alerts():
    """An unreadable book is never permission to stay quiet — the same rule
    every correction path in this engine follows."""
    print("\n4. the position read fails")
    check("alerts anyway", ask("close", live_size=0.0, target=0, raises=True), True)


def test_a_missing_leg_that_arrived():
    print("\n5. 'leg missing' on a follower that has since opened it")
    check("arrived: no alert", ask("open", live_size=-2944.0, target=2944), False)
    check("still absent: alerts", ask("open", live_size=0.0, target=2944), True)


def test_a_size_gap_that_closed():
    print("\n6. a size gap that settled, and one that did not")
    check("settled: no alert", ask("trim", live_size=-2944.0, target=2944), False)
    check("1% off is inside tolerance",
          ask("trim", live_size=-2960.0, target=2944), False)
    check("still 2x: alerts", ask("trim", live_size=-5888.0, target=2944), True)


def test_the_alert_path_really_asks():
    """A unit test of the helper proves nothing if the alert path never calls
    it. Pin the wiring: the re-check must sit BEFORE the notify, and before the
    name is recorded for the later 'cleared' message."""
    print("\n7. and the alert path consults it")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("it re-checks", "_discrepancy_still_true(" in src, True)
    i_check = src.index("_discrepancy_still_true(")
    check("before notify_mismatch", i_check < src.index("tg.notify_mismatch("), True)
    check("before notify_holding", i_check < src.index("tg.notify_holding("), True)
    check("before the name is recorded",
          i_check < src.index("self._recon_alert_names[_sk] = _name"), True)
    check("and it does not create a lock just to ask",
          "entry = _locks.get(" in inspect.getsource(leg_locks.is_busy), True)


if __name__ == "__main__":
    for t in (test_the_live_case_stays_quiet,
              test_a_real_extra_leg_still_alerts,
              test_a_copy_in_flight_is_not_a_mismatch,
              test_an_unreadable_book_still_alerts,
              test_a_missing_leg_that_arrived,
              test_a_size_gap_that_closed,
              test_the_alert_path_really_asks):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
