"""Regression: a follower left holding a position the master has exited must alert.

Run:  venv/Scripts/python.exe test_orphan_alert.py
No network, no Redis, no Supabase — telegram is stubbed.

THE GAP (2026-11-11). The follower held C-BTC-74000 and C-BTC-75000 after the
master had fully closed both. Its TP/SL were already cancelled — cancels are
matched by order id, so that half of the copy worked while the close did not —
so for 4.5 minutes it carried two unmatched, unprotected positions.

NOTHING alerted. Querying the alerts table for 03:05-03:25 returns zero rows.

The reason is a blind spot in the sync check. A closed symbol has its position
row DELETED rather than zeroed — verified against the live table, which holds no
rows at quantity 0 — so "no master row" cannot be told apart from "the master is
flat", and the check returns "unknown" and stays silent. That is sensible on its
own: options positions cannot be read over REST (Delta's endpoint 400s) and the
WS snapshot can be briefly empty, so treating a missing row as "master has zero"
would alert constantly on healthy accounts.

So the fix is TIMED rather than immediate. A snapshot gap clears in seconds; a
real orphan does not. Only a position that outlives _ORPHAN_ALERT_SEC with no
master row behind it is called out.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.position_monitor import PositionMonitor

FAILURES = []
SENT = []


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def monitor():
    import app.core.position_monitor as pm

    async def fake_notify(account, symbol, side, lots, reason, key=None, window=None):
        SENT.append({"account": account, "symbol": symbol, "lots": lots,
                     "reason": reason, "key": key})

    pm.tg.notify_fail = fake_notify
    m = PositionMonitor.__new__(PositionMonitor)
    m._orphan_since = {}
    SENT.clear()
    return m


def age(m, key, seconds):
    """Pretend the episode started `seconds` ago and was seen just now."""
    m._orphan_since[key] = (time.time() - seconds, time.time())


def go_quiet(m, key, seconds):
    """Pretend the symbol has not been observed for `seconds` — i.e. the
    follower closed it and the symbol stopped being checked."""
    first, _ = m._orphan_since[key]
    m._orphan_since[key] = (first, time.time() - seconds)


# ---------------------------------------------------------------------------


def test_a_transient_gap_stays_quiet():
    print("\n1. a brief snapshot gap must NOT alert")
    m = monitor()
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    check("nothing sent on first sight", len(SENT), 0)
    check("but it is being timed", ("f1", "C-BTC-74000-111126") in m._orphan_since, True)
    # Seen again a moment later — still inside the window.
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    check("still quiet a moment later", len(SENT), 0)


def test_a_real_orphan_alerts():
    print("\n2. THE INCIDENT: a position that outlives the window")
    m = monitor()
    key = ("f1", "C-BTC-74000-111126")
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    age(m, key, m._ORPHAN_ALERT_SEC + 1)
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    check("alert sent", len(SENT), 1)
    check("names the account", SENT[0]["account"], "JD algo3")
    check("names the symbol", SENT[0]["symbol"], "C-BTC-74000-111126")
    check("reports the size held", SENT[0]["lots"], 361)
    check("says it is unmatched", "still holds it" in SENT[0]["reason"], True)
    check("says it is unprotected", "unprotected" in SENT[0]["reason"], True)


def test_it_clears_when_the_position_closes():
    print("\n3. once the position is closed, the timer resets")
    m = monitor()
    key = ("f1", "C-BTC-75000-111126")
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-75000-111126", 2390))
    check("being timed", key in m._orphan_since, True)
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-75000-111126", 0))
    check("timer cleared at zero", key in m._orphan_since, False)
    check("and nothing was sent", len(SENT), 0)


def test_a_flat_follower_never_alerts():
    print("\n4. no position, no alert — however long it waits")
    m = monitor()
    for _ in range(3):
        asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 0))
    check("silent", len(SENT), 0)
    check("nothing tracked", len(m._orphan_since), 0)


def test_each_symbol_is_tracked_separately():
    print("\n5. two orphans are two alerts, not one")
    m = monitor()
    for sym, qty in (("C-BTC-74000-111126", 361), ("C-BTC-75000-111126", 2390)):
        asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", sym, qty))
        age(m, ("f1", sym), m._ORPHAN_ALERT_SEC + 1)
        asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", sym, qty))
    check("both alerted", len(SENT), 2)
    check("different dedupe keys", len({s["key"] for s in SENT}), 2)


def test_a_reopened_symbol_does_not_inherit_an_old_timer():
    print("\n6. THE FALSE ALARM: a symbol closed and reopened starts clean")
    # Live on 2026-11-13 04:19:26. C-BTC-71600 fired seven seconds after the
    # follower opened 2515, while the master was taking 2800 in the same breath.
    # Both were flat at 04:19:19. The "orphan" was a leftover timer from an
    # earlier position in the same symbol: the follower had gone flat, so the
    # symbol stopped being checked, so the clear-on-zero never ran.
    m = monitor()
    key = ("f1", "C-BTC-71600-131126")
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-71600-131126", 280))
    age(m, key, 600)              # an episode from ten minutes ago...
    go_quiet(m, key, 300)         # ...last observed five minutes ago
    # The follower now OPENS the symbol again. This is the first check of a new
    # position, and it must not inherit the old clock.
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-71600-131126", 2515))
    check("no alert on a freshly opened position", len(SENT), 0)
    first, last = m._orphan_since[key]
    check("the clock restarted", round(time.time() - first) < 2, True)
    # And it still alerts if THIS episode genuinely persists.
    age(m, key, m._ORPHAN_ALERT_SEC + 1)
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-71600-131126", 2515))
    check("but a real orphan still alerts", len(SENT), 1)


def test_continuous_observation_still_ages():
    print("\n7. an episode observed throughout still ages normally")
    m = monitor()
    key = ("f1", "C-BTC-74000-111126")
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    # Seen repeatedly, no gap — the clock must keep running.
    age(m, key, m._ORPHAN_ALERT_SEC + 1)
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    check("alerts", len(SENT), 1)


def test_a_telegram_failure_never_breaks_the_monitor():
    print("\n6. the alert must not take the position monitor down")
    import app.core.position_monitor as pm
    m = monitor()

    async def boom(*a, **kw):
        raise RuntimeError("telegram down")

    pm.tg.notify_fail = boom
    key = ("f1", "C-BTC-74000-111126")
    asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
    age(m, key, m._ORPHAN_ALERT_SEC + 1)
    try:
        asyncio.run(m._maybe_alert_orphan("f1", "JD algo3", "C-BTC-74000-111126", 361))
        check("swallowed", True, True)
    except Exception as e:
        check("swallowed", "raised %s" % type(e).__name__, True)


def main():
    print("=" * 74)
    print("orphan alert - an unmatched, unprotected follower position must shout")
    print("=" * 74)
    for fn in (
        test_a_transient_gap_stays_quiet,
        test_a_real_orphan_alerts,
        test_it_clears_when_the_position_closes,
        test_a_flat_follower_never_alerts,
        test_each_symbol_is_tracked_separately,
        test_a_reopened_symbol_does_not_inherit_an_old_timer,
        test_continuous_observation_still_ages,
        test_a_telegram_failure_never_breaks_the_monitor,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
