# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: if master and follower do not match, say so — and say so again
when it is fixed.

Run:  venv/Scripts/python.exe test_mismatch_alerts.py
No network, no Redis, no Supabase.

THE OLD RULE AND WHY IT WAS CHANGED. The engine spoke only when a human had to
act. Anything it corrected itself stayed silent, and anything it had merely
SEEN waited STUCK_AFTER_SEC. That is a sensible rule for noise and a bad one
for trust. On 2026-11-20 a client watched four legs sit off the ratio:

    C-BTC-64000  trimmed 800 -> 715      (no alert: the engine fixed it)
    C-BTC-64200  trimmed 800 -> 715      (no alert: the engine fixed it)
    C-BTC-65000  held at 3200            (no alert at all)
    C-BTC-65200  held at 3200            (no alert at all)

and reported that the ratio had stopped working on shorts. It had not — the two
held legs were the two the master was unwinding, a deliberate rule. But nothing
anywhere said so, so there was no way to tell a deliberate hold from a broken
copier.

Two whole categories were silent end to end:

  * an orphaned stop on the follower was cancelled with nothing said, so a stop
    could appear and vanish on a client's book unannounced;
  * a resting order the follower had missed was re-placed by the 30s sweep with
    nothing said beyond "re-mirrored N resting limit order(s)" in a log.

DESK CALL, 2026-11-20 (Jigar, via Tushar): position mismatch, stop-order
mismatch, open-order mismatch and missing TP/SL all alert — on time, not late.

WHAT "ON TIME" MEANS HERE. Not the first sighting. A book read mid-flight — an
order half filled, a position still settling — looks wrong for one pass and
then is not; alerting on that teaches people to ignore the channel. The second
consecutive sighting is both early (~10-20s, not 60) and the exact moment the
engine itself becomes willing to act.
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
                                  MISMATCH_ALERT_AFTER_SEC, _MISMATCH_KIND)
from app.services import telegram_client as tg

FAILURES = []
SENT = []
ROUTED = []

FOLLOWER = "JD algo3"
LEG = "C-BTC-64000-201126"


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
    tg._should_send = _always_send if not dedupe else _REAL_SHOULD_SEND


def plain(text):
    return re.sub(r"</?[a-z]+>", "", text)


# --- the message itself -----------------------------------------------------


def test_the_three_failure_kinds_are_told_apart():
    """An order the exchange rejected, the engine crashing on its own
    correction, and the engine giving up are three different problems. They all
    read "Mirror Failed" until 2026-11-20, so nobody could triage them."""
    print("\n0. three failures, three headings")
    install(dedupe=False)

    async def run():
        await tg.notify_fail(FOLLOWER, LEG, "sell", 2859, "insufficient_margin")
        await tg.notify_fail(FOLLOWER, LEG, "reconcile", 0,
                             "the close/trim check itself crashed (KeyError) - "
                             "this leg was NOT corrected")
        await tg.notify_fail(FOLLOWER, LEG, "sell", 3200,
                             "placed 3 times and the order vanished each time "
                             "without filling - not re-placing")

    asyncio.run(run())
    heads = [plain(t).splitlines()[0] for t in SENT]
    check("rejected order", "Mirror Failed" in heads[0], True)
    check("engine crash", "Engine error" in heads[1], True)
    check("gave up retrying", "Stopped retrying" in heads[2], True)
    check("three distinct headings", len(set(heads)), 3)


def test_each_kind_reads_as_english():
    """A kind nobody can read is a kind nobody acts on. These four are exactly
    the four the desk asked for."""
    print("\n1. the four categories, in words")
    install(dedupe=False)

    async def run():
        await tg.notify_mismatch(FOLLOWER, LEG, "size",
                                 follower=800, target=715, master=800)
        await tg.notify_mismatch(FOLLOWER, LEG, "stop",
                                 detail="the follower has a stop_loss_order the master has dropped")
        await tg.notify_mismatch(FOLLOWER, LEG, "protection",
                                 detail="the master has a stop on this leg and the follower has none")
        await tg.notify_mismatch(FOLLOWER, LEG, "order", master=3100)

    asyncio.run(run())
    bodies = [plain(t) for t in SENT]
    check("four messages", len(bodies), 4)
    check("position size", "POSITION SIZE" in bodies[0], True)
    check("stop the master dropped",
          "STOP ORDER THE MASTER NO LONGER HAS" in bodies[1], True)
    check("TP/SL the follower lacks",
          "TP/SL MISSING ON THE FOLLOWER" in bodies[2], True)
    check("resting order the follower lacks",
          "RESTING ORDER MISSING ON THE FOLLOWER" in bodies[3], True)


def test_it_promises_nothing():
    """It must not say a fix is coming. On 2026-11-20 the correction itself
    crashed on four legs and nothing was corrected at all -- an alert that
    guarantees a fix which never arrives is worse than one that guarantees
    nothing, because the reader stops checking."""
    print("\n1b. no promise about what happens next")
    install(dedupe=False)
    asyncio.run(tg.notify_mismatch(FOLLOWER, LEG, "size",
                                   follower=800, target=715, master=800))
    body = plain(SENT[0])
    check("does not claim it is fixing it", "correcting it now" in body, False)
    check("states the fact instead", "not corrected yet" in body, True)


def test_the_numbers_are_all_three():
    """Has / should have / master has. Two of the three is not enough to judge
    whether the engine or the account is wrong."""
    print("\n2. what it holds, what it should, what the master has")
    install(dedupe=False)
    asyncio.run(tg.notify_mismatch(FOLLOWER, LEG, "size",
                                   follower=800, target=715, master=800))
    body = plain(SENT[0])
    check("follower's size", "follower has 800" in body, True)
    check("the target", "should have 715" in body, True)
    check("the master's", "master has 800" in body, True)
    check("and the leg", LEG in body, True)


def test_it_goes_to_the_right_desk():
    print("\n3. routed per owner, like every other alert")
    install(dedupe=False)
    asyncio.run(tg.notify_mismatch(FOLLOWER, LEG, "size", follower=800, target=715))
    check("carries the account", ROUTED, [FOLLOWER])


def test_one_per_episode_then_silence():
    print("\n4. the sweep re-detects every 10s — one message, not one per pass")
    install()

    async def run():
        counts = []
        for _ in range(10):
            SENT.clear()
            await tg.notify_mismatch(FOLLOWER, LEG, "size", follower=800, target=715)
            counts.append(len(SENT))
        return counts

    counts = asyncio.run(run())
    check("first speaks", counts[0], 1)
    check("the rest stay quiet", sum(counts[1:]), 0)


def test_a_settled_episode_lets_the_next_one_speak():
    """Without the clear, a leg that goes wrong, is fixed, and goes wrong again
    an hour later is swallowed for a week by the throttle."""
    print("\n5. clearing re-opens the channel for the same leg")
    install()

    async def run():
        SENT.clear()
        await tg.notify_mismatch(FOLLOWER, "C-BTC-70000-201126", "size",
                                 follower=10, target=9)
        first = len(SENT)
        SENT.clear()
        await tg.notify_mismatch(FOLLOWER, "C-BTC-70000-201126", "size",
                                 follower=10, target=9)
        suppressed = len(SENT)
        await tg.notify_mismatch_cleared(FOLLOWER, "C-BTC-70000-201126", "size")
        SENT.clear()
        await tg.notify_mismatch(FOLLOWER, "C-BTC-70000-201126", "size",
                                 follower=10, target=9)
        return first, suppressed, len(SENT)

    first, suppressed, after = asyncio.run(run())
    check("the episode speaks once", first, 1)
    check("and is then quiet", suppressed, 0)
    check("a later episode speaks again", after, 1)


def test_the_kinds_are_tracked_apart():
    print("\n6. a size problem and a missing stop are different problems")
    install()

    async def run():
        SENT.clear()
        await tg.notify_mismatch(FOLLOWER, "C-BTC-71000-201126", "size", follower=1, target=2)
        await tg.notify_mismatch(FOLLOWER, "C-BTC-71000-201126", "protection",
                                 detail="no stop")
        return len(SENT)

    check("both get through", asyncio.run(run()), 2)


# --- the engine side --------------------------------------------------------


def test_nothing_speaks_on_a_first_sighting():
    print("\n7. a book read mid-flight must not raise an alarm")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("there is a threshold",
          "if _age < MISMATCH_ALERT_AFTER_SEC:" in src, True)
    check("it is under one pass interval (10s)",
          MISMATCH_ALERT_AFTER_SEC <= 10, True)
    check("and not zero", MISMATCH_ALERT_AFTER_SEC > 0, True)
    check("far earlier than the old give-up threshold",
          MISMATCH_ALERT_AFTER_SEC < STUCK_AFTER_SEC, True)


def test_every_position_kind_maps_to_a_word():
    print("\n8. all four position corrections are covered")
    check("open -> missing", _MISMATCH_KIND["open"], "missing")
    check("close -> extra", _MISMATCH_KIND["close"], "extra")
    check("trim -> size", _MISMATCH_KIND["trim"], "size")
    check("topup -> size", _MISMATCH_KIND["topup"], "size")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("and the sweep uses the map",
          "_MISMATCH_KIND.get(_kind, _kind)" in src, True)


def test_the_sweep_still_escalates_what_never_settles():
    """Stage one says it does not match. Stage two, much later, says it is
    still not fixed — which is the one that means a human must look."""
    print("\n9. the 60s 'still not corrected' alert survives")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("stage one", "tg.notify_mismatch(" in src, True)
    check("stage two", "tg.notify_stuck(" in src, True)
    check("and they are not the same threshold",
          "_age < STUCK_AFTER_SEC" in src, True)


def test_a_settled_leg_clears_its_throttle():
    print("\n10. the engine clears the episode when the leg settles")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    check("names are remembered for it",
          "self._recon_alert_names[_sk] = _name" in src, True)
    check("and released on the prune",
          "self._recon_alert_names.pop(_sk, None)" in src, True)
    check("by clearing the throttle",
          "tg.notify_mismatch_cleared(" in src, True)


def test_the_orphan_stop_is_no_longer_silent():
    print("\n11. an orphaned stop: the gap AND the cancel")
    src = inspect.getsource(CopyEngine._sync_protection)
    check("the mismatch is announced", '"stop",' in src, True)
    check("so is the cancel that fixes it", '"CANCELLED", None,' in src, True)
    check("a failed cancel is announced too",
          "could not cancel the orphaned" in src, True)
    check("and the episode is closed",
          "tg.notify_mismatch_cleared(" in src, True)


def test_missing_protection_announces_the_gap_not_only_the_fix():
    print("\n12. an unprotected follower is visible BEFORE it is protected")
    src = inspect.getsource(CopyEngine._sync_protection)
    check("the gap is announced", '"protection",' in src, True)
    check("the restore still alerts", "RESTORED" in src, True)


def test_a_recovered_resting_order_is_announced():
    """Only for a place that came from the 30s sweep. A live mirror placing an
    order is the system working, not a mismatch — alerting on those would make
    the channel unreadable within an hour."""
    print("\n13. a resting order the follower had missed")
    src = inspect.getsource(CopyEngine._mirror_place)
    check("gated on the sweep",
          '(event or {}).get("source") == "reconcile"' in src, True)
    check("the gap is announced", '"order",' in src, True)
    check("and the recovery", '"PLACED", int(qty),' in src, True)

    listener = inspect.getsource(
        __import__("app.core.trade_listener", fromlist=["TradeListener"]).TradeListener)
    check("the sweep marks its events",
          '_push_order_event(o, "place", source="reconcile")' in listener, True)
    check("and the default stays live",
          'source: str = "live"' in listener, True)


if __name__ == "__main__":
    for t in (test_the_three_failure_kinds_are_told_apart,
              test_each_kind_reads_as_english,
              test_it_promises_nothing,
              test_the_numbers_are_all_three,
              test_it_goes_to_the_right_desk,
              test_one_per_episode_then_silence,
              test_a_settled_episode_lets_the_next_one_speak,
              test_the_kinds_are_tracked_apart,
              test_nothing_speaks_on_a_first_sighting,
              test_every_position_kind_maps_to_a_word,
              test_the_sweep_still_escalates_what_never_settles,
              test_a_settled_leg_clears_its_throttle,
              test_the_orphan_stop_is_no_longer_silent,
              test_missing_protection_announces_the_gap_not_only_the_fix,
              test_a_recovered_resting_order_is_announced):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
