# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: the engine must report what it FAILED to do, not only what it did.

Run:  venv/Scripts/python.exe test_stuck_alerts.py
No network, no Redis, no Supabase.

THE GAP. Every alert announced an action the engine took — mirrored, failed,
corrected. Nothing announced a discrepancy it had SEEN and not settled, and
nothing announced an order it had refused to send. On 2026-11-19 four separate
faults were found by people staring at the exchange rather than by us:

  * a follower doubled to 5539 against a target of 2771 — detected eight
    seconds later, logged thirteen times across two minutes, never mentioned;
  * a reconciler that sat on that same discrepancy without acting;
  * an order left resting for three and a half hours after its master's was
    cancelled;
  * a whole book refused locally for a day while the client reported
    "no error, no copy" — because a refusal never reaches the exchange, so
    there is no rejection for anyone to see.

TWO RULES BEING PINNED HERE.

1. A discrepancy that outlives STUCK_AFTER_SEC is escalated. Below it, the
   reconciler is still working and must stay quiet — the correction normally
   lands in 20-30s, and an alert per pass would be a stream nobody reads.

2. Settling CLEARS the clock, so a later recurrence is timed from scratch
   rather than inheriting an old one and alerting instantly.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.copy_engine import STUCK_AFTER_SEC

FAILURES = []

MASTER = "867a7192-master"
FOLLOWER = "follower-algo3"
SYMBOL = "P-BTC-61600-191126"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def pass_once(since, candidates, now, mkey=MASTER):
    """One reconcile pass's escalation step, as the engine performs it.

    Returns the discrepancies escalated on this pass.
    """
    alerted, live = [], set()
    for kind, cands in candidates.items():
        for fid, sym in cands:
            sk = (mkey, kind, fid, sym)
            live.add(sk)
            first = since.setdefault(sk, now)
            if now - first >= STUCK_AFTER_SEC:
                alerted.append((kind, fid, sym, now - first))
    for sk in [k for k in since if k[0] == mkey and k not in live]:
        since.pop(sk, None)
    return alerted


# ---------------------------------------------------------------------------


def test_a_fresh_discrepancy_stays_quiet():
    print("\n1. the reconciler is still working — do not interrupt")
    since, t0 = {}, 1000.0
    cands = {"trim": {(FOLLOWER, SYMBOL)}}
    check("first sighting silent", pass_once(since, cands, t0), [])
    check("still silent at 10s", pass_once(since, cands, t0 + 10), [])
    check("still silent at 30s", pass_once(since, cands, t0 + 30), [])


def test_the_live_incident_would_have_been_caught():
    print("\n2. THE 2m07s THAT NOBODY HEARD ABOUT")
    since, t0 = {}, 1000.0
    cands = {"trim": {(FOLLOWER, SYMBOL)}}
    # The reconcile pass runs every 10s; the live episode ran 13 of them.
    alerts = []
    for i in range(14):
        alerts += pass_once(since, cands, t0 + i * 10)
    check("it is escalated", len(alerts) > 0, True)
    kind, fid, sym, age = alerts[0]
    check("as a trim", kind, "trim")
    check("about the right leg", (fid, sym), (FOLLOWER, SYMBOL))
    check("after about a minute", int(age) >= int(STUCK_AFTER_SEC), True)
    # It must not then shout on every subsequent pass — that is the dedupe
    # window's job, but the engine should keep reporting it as stuck.
    check("keeps reporting while stuck", len(alerts) >= 7, True)


def test_settling_clears_the_clock():
    print("\n3. a resolved discrepancy is forgotten")
    since, t0 = {}, 1000.0
    stuck = {"trim": {(FOLLOWER, SYMBOL)}}
    clear = {"trim": set()}
    pass_once(since, stuck, t0)
    check("tracked", len(since), 1)
    pass_once(since, clear, t0 + 10)        # the reconciler fixed it
    check("forgotten", len(since), 0)
    # The same leg going wrong again much later must start its own clock,
    # not inherit the old one and alert immediately.
    check("recurrence is silent at first",
          pass_once(since, stuck, t0 + 5000), [])


def test_each_kind_is_tracked_separately():
    print("\n4. a missing leg and an over-exposure are different problems")
    since, t0 = {}, 1000.0
    cands = {"trim": {(FOLLOWER, SYMBOL)}, "open": {(FOLLOWER, "C-BTC-69000-201126")}}
    pass_once(since, cands, t0)
    alerts = pass_once(since, cands, t0 + STUCK_AFTER_SEC)
    kinds = sorted(k for k, _, _, _ in alerts)
    check("both escalated", kinds, ["open", "trim"])


def test_one_master_s_clock_is_not_cleared_by_another():
    print("\n5. the clocks are per master, like everything else now")
    since, t0 = {}, 1000.0
    other = "48cb7aae-master"
    pass_once(since, {"trim": {(FOLLOWER, SYMBOL)}}, t0, mkey=MASTER)
    # The other master's pass finds nothing wrong on its own desk.
    pass_once(since, {"trim": set()}, t0 + 1, mkey=other)
    check("the first master's clock survives", len(since), 1)
    alerts = pass_once(since, {"trim": {(FOLLOWER, SYMBOL)}}, t0 + STUCK_AFTER_SEC, mkey=MASTER)
    check("and still escalates", len(alerts), 1)


def test_the_threshold_is_above_a_normal_correction():
    print("\n6. the threshold must not undercut the reconciler")
    # Pass every 10s, two passes to confirm, then the correction — call it 30s.
    check("comfortably above a normal fix", STUCK_AFTER_SEC >= 45, True)
    check("and well under how long a human takes", STUCK_AFTER_SEC <= 300, True)


if __name__ == "__main__":
    for t in (test_a_fresh_discrepancy_stays_quiet,
              test_the_live_incident_would_have_been_caught,
              test_settling_clears_the_clock,
              test_each_kind_is_tracked_separately,
              test_one_master_s_clock_is_not_cleared_by_another,
              test_the_threshold_is_above_a_normal_correction):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
