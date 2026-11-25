# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: a leg held on purpose must say so, and must not read as a fault.

Run:  venv/Scripts/python.exe test_hold_alert.py
No network, no Redis, no Supabase.

THE RULE BEING PROTECTED. When the master is working an order that reduces his
own position, the reconciler does NOT trim the follower back to target. His
order fills in pieces, so his size drops while the follower's has not moved —
the gap is a copy mid-flight, and trimming into it fills the follower at a
different price from the master's, twice, for a gap that settles itself in one
step when his order terminates. Held with no size cap and no time limit: desk
call, Prathav 2026-09-25.

WHAT WENT WRONG WITH IT. Nothing. The rule worked exactly as written, and said
nothing at all while it did.

2026-11-20, JD algo3, four legs over target after a resume:

    C-BTC-64000  master +800   no resting master order   -> TRIMMED 800 -> 715
    C-BTC-64200  master +800   no resting master order   -> TRIMMED 800 -> 715
    C-BTC-65000  master -3200  master unwinding          -> held at 3200
    C-BTC-65200  master -3200  master unwinding          -> held at 3200

The two that happened to be shorts were the two the master was unwinding. The
client saw longs tracking the ratio and shorts not, and reported that the ratio
had stopped working on shorts. It had not — but nothing in any alert, anywhere,
said the engine was waiting on purpose, so there was no way to tell a deliberate
hold from a broken one.

TWO THINGS PINNED HERE.

1. A hold that outlives a normal correction is announced — same 60s threshold
   and same throttle as every other unsettled discrepancy, so a book caught
   mid-flight still stays quiet.

2. It is announced as a HOLD, never as a failure. "Not corrected" on a leg the
   engine chose not to correct sends someone hunting a bug that is not there,
   which is the exact confusion this exists to end.

The decision itself is unchanged. Nothing is trimmed that was not trimmed
before.
"""
import asyncio
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1000000")

from app.core.copy_engine import (CopyEngine, STUCK_AFTER_SEC,
                                  MISMATCH_ALERT_AFTER_SEC)
from app.services import telegram_client as tg

FAILURES = []
SENT = []
ROUTED = []

FOLLOWER = "JD algo3"
SHORT_LEG = "C-BTC-65000-201126"
LONG_LEG = "C-BTC-64000-201126"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


async def _fake_send(text, account=None, account_id=None):
    SENT.append(text)
    ROUTED.append(account)
    return True


async def _always_send(_key, _window):
    return True


_REAL_SHOULD_SEND = tg._should_send


def install(dedupe=True):
    SENT.clear()
    ROUTED.clear()
    tg.send_message = _fake_send
    # RESTORE, don't just skip setting it. Replacing _should_send and leaving
    # it replaced made the throttle test pass twelve messages and look like a
    # missing throttle -- a test that lies about the thing it is testing.
    tg._should_send = _always_send if not dedupe else _REAL_SHOULD_SEND
    # No Redis in tests. _should_send tries Redis first and falls back to
    # the in-process _dedupe on any error -- but the fallback only happens
    # after the client's connect timeout, about four seconds a call. Ten
    # calls is forty seconds, which is most of why this file took 82s and
    # why it blew the runner's 300s timeout on a loaded machine.
    #
    # Pointing _r at None skips straight to _dedupe. Same dedupe logic,
    # same assertions, without waiting on a socket that is not there.
    tg._r = lambda: None


def plain(text):
    return re.sub(r"</?[a-z]+>", "", text)


# ---------------------------------------------------------------------------


def test_the_hold_is_announced_as_a_hold():
    print("\n1. the wording: a hold, not a failure")
    install(dedupe=False)
    asyncio.run(tg.notify_holding(FOLLOWER, SHORT_LEG, 3200, 2859,
                                  master=-3200, seconds=73))
    check("one message", len(SENT), 1)
    body = plain(SENT[0])
    check("says it is holding on purpose", "Holding on purpose" in body, True)
    check("does NOT say failed", "Failed" in body or "FAILED" in body, False)
    check("does NOT say not corrected", "Not corrected" in body, False)
    check("names the account", FOLLOWER in body, True)
    check("names the leg", SHORT_LEG in body, True)


def test_it_shows_both_numbers_and_the_reason():
    print("\n2. enough to decide without opening the exchange")
    install(dedupe=False)
    asyncio.run(tg.notify_holding(FOLLOWER, SHORT_LEG, 3200, 2859,
                                  master=-3200, seconds=73))
    body = plain(SENT[0])
    check("what it holds", "3200" in body, True)
    check("what it should hold", "2859" in body, True)
    check("the master's own size", "-3200" in body, True)
    check("why it is waiting",
          "the master is closing this leg himself" in body, True)
    check("and that nobody needs to act", "no action needed" in body, True)
    check("how long it has waited", "73s" in body, True)


def test_it_is_routed_to_the_right_desk():
    """Alerts go to the owner's own group — Pratik's never land in the main
    one. A new alert type that forgets to pass the account is a leak."""
    print("\n3. routed like every other alert")
    install(dedupe=False)
    asyncio.run(tg.notify_holding(FOLLOWER, SHORT_LEG, 3200, 2859))
    check("carries the account", ROUTED, [FOLLOWER])


def test_it_does_not_become_a_stream():
    """The hold is re-detected every 10s and can last minutes — roughly eleven
    of them on 2026-09-21. One message per episode, not one per pass."""
    print("\n4. throttled per account and symbol")
    install()
    tg._SENT_KEYS = {} if hasattr(tg, "_SENT_KEYS") else None

    async def run():
        out = []
        for _ in range(12):
            SENT.clear()
            await tg.notify_holding(FOLLOWER, SHORT_LEG, 3200, 2859, seconds=120)
            out.append(len(SENT))
        return out

    sends = asyncio.run(run())
    check("first pass speaks", sends[0], 1)
    check("the next eleven stay quiet", sum(sends[1:]), 0)


def test_a_different_leg_still_gets_its_own_message():
    print("\n5. the throttle is per leg, not per account")
    install()

    async def run():
        SENT.clear()
        await tg.notify_holding(FOLLOWER, "C-BTC-65200-201126", 3200, 2859, seconds=120)
        return len(SENT)

    check("a second symbol is not suppressed", asyncio.run(run()), 1)


# --- the engine side --------------------------------------------------------


def test_the_engine_records_the_hold():
    print("\n6. the engine tracks a deferred trim as its own kind")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("a per-pass set exists", "current_hold: set = set()" in src, True)
    check("the deferral records into it",
          "current_hold.add((fid, sym))" in src, True)
    check("with what to say about it",
          '_stuck_detail[("hold", fid, sym)]' in src, True)


def test_the_hold_is_not_also_a_trim_candidate():
    """It must not land in current_trim: that set is what the next pass
    confirms against, and a confirmed trim is a trim. The whole point is that
    this leg is NOT cut."""
    print("\n7. recording it must not cause it to be acted on")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    hold_at = src.index("current_hold.add((fid, sym))")
    trim_at = src.index("current_trim.add(tkey)")
    check("the hold branch is before the trim branch", hold_at < trim_at, True)
    tail = src[hold_at:trim_at]
    check("and it leaves the pass", "continue" in tail, True)


def test_it_runs_through_the_same_threshold():
    """Quiet on the first sighting, spoken on the second.

    This was 60s when the hold alert was first written, matching the old
    "escalate only what the reconciler failed to settle" rule. The desk then
    asked for every mismatch to be announced as it is found rather than a
    minute later, so a hold now speaks at the same second-sighting point as
    everything else -- early enough to be useful, late enough that a book read
    mid-flight does not trigger it.
    """
    print("\n8. quiet on the first sighting, spoken on the second")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("the hold joins the escalation sweep",
          '("hold", current_hold)' in src, True)
    check("nothing speaks on the first sighting",
          "if _age < MISMATCH_ALERT_AFTER_SEC:" in src, True)
    check("the hold stops there and does not also escalate",
          'if _kind == "hold" or _age < STUCK_AFTER_SEC:' in src, True)
    check("that threshold is under one pass interval",
          MISMATCH_ALERT_AFTER_SEC <= 10, True)
    check("and not instant",
          MISMATCH_ALERT_AFTER_SEC >= 5, True)
    check("while the give-up threshold stays well above a normal fix",
          STUCK_AFTER_SEC >= 45, True)


def test_the_escalation_picks_the_right_notifier():
    print("\n9. and the right wording comes out of it")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("hold -> notify_holding", 'if _kind == "hold":' in src, True)
    check("holding notifier is called", "tg.notify_holding(" in src, True)
    check("everything else still -> notify_stuck", "tg.notify_stuck(" in src, True)


def test_the_trim_decision_itself_is_untouched():
    """Nothing about WHAT gets trimmed changes. If this fails, the alert
    change has quietly altered the desk's rule."""
    print("\n10. the rule is unchanged")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("still defers while the master is working an order",
          "working = await self._master_working_reducer(master_row, sym, msz)" in src,
          True)
    check("still refuses to trim on an unreadable book",
          "not trimming on an unknown" in src, True)


if __name__ == "__main__":
    for t in (test_the_hold_is_announced_as_a_hold,
              test_it_shows_both_numbers_and_the_reason,
              test_it_is_routed_to_the_right_desk,
              test_it_does_not_become_a_stream,
              test_a_different_leg_still_gets_its_own_message,
              test_the_engine_records_the_hold,
              test_the_hold_is_not_also_a_trim_candidate,
              test_it_runs_through_the_same_threshold,
              test_the_escalation_picks_the_right_notifier,
              test_the_trim_decision_itself_is_untouched):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
