"""What Telegram is allowed to say.

Run:  venv/Scripts/python.exe test_telegram_notify.py
No network — send_message is stubbed.

The channel used to fire on every mirrored fill, so it was a running trade log.
A channel that is mostly routine is one nobody reads closely enough to catch the
message that matters — and on 2026-08-27 the message that mattered never existed
at all: the engine punched 62 lots against a target of 31 and the reconciler
quietly trimmed 31 back, on most orders of the day, with nothing sent.

So: routine fills are silent, and RECONCILER CORRECTIONS are loud, because a
correction is the outward sign that the engine got something wrong.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["TELEGRAM_CHAT_ID"] = "test-chat"

from app.services import telegram_client as tg

FAILURES = []
SENT = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        FAILURES.append(name)


async def _fake_send(text: str) -> bool:
    SENT.append(text)
    return True


def reset():
    SENT.clear()
    tg._seen.clear()
    tg._redis = False          # force the in-memory paths, no Redis
    tg._last_fail.clear()
    tg.send_message = _fake_send


async def test_correction_names_the_bug():
    """The real incident: 62 punched against 31, reconciler trimmed 31."""
    print("\na trim must show held -> target, not just 'trimmed 31'")
    reset()
    await tg.notify_correction(
        "Mini Prathav", "P-BTC-74500-280826", "TRIMMED", 31,
        held=62, target=31, master=2750,
        why="the follower was over-exposed — an over-sized punch",
    )
    check("one message", len(SENT), 1)
    t = SENT[0]
    check_true("says the reconciler corrected", "Reconciler corrected" in t, t)
    check_true("names the account", "Mini Prathav" in t, t)
    check_true("names the symbol", "P-BTC-74500-280826" in t, t)
    check_true("says what it did", "TRIMMED" in t and "31" in t, t)
    # This is the whole point: 62 -> 31 is what makes the BUG visible. Without it
    # the message only says the safety net ran, not what it was covering for.
    check_true("shows what the engine got wrong (62 -> 31)", "62" in t and "→ 31" in t, t)
    check_true("and what the master held", "2750" in t, t)
    check_true("and why", "over-exposed" in t, t)


async def test_correction_without_context_still_sends():
    print("\na correction with no held/target still reports the action")
    reset()
    await tg.notify_correction("Follower A", "BTCUSD", "OPENED", 5, master=400)
    check("sent", len(SENT), 1)
    check_true("names the action", "OPENED" in SENT[0], SENT[0])
    check_true("no dangling arrow when there is nothing to compare",
               "→" not in SENT[0], SENT[0])


async def test_repeat_correction_is_collapsed():
    print("\nthe sweep runs every 15s — an identical correction is one episode")
    reset()
    for _ in range(4):
        await tg.notify_correction("Mini Prathav", "BTCUSD", "TRIMMED", 31,
                                   held=62, target=31, master=2750)
    check("sent once, not four times", len(SENT), 1)


async def test_a_different_correction_still_gets_through():
    print("\nbut a genuinely different correction is not swallowed")
    reset()
    await tg.notify_correction("Mini Prathav", "BTCUSD", "TRIMMED", 31, held=62, target=31)
    await tg.notify_correction("Mini Prathav", "BTCUSD", "TRIMMED", 12, held=20, target=8)
    await tg.notify_correction("Mini Prathav", "ETHUSD", "TRIMMED", 31, held=62, target=31)
    await tg.notify_correction("Follower B", "BTCUSD", "TRIMMED", 31, held=62, target=31)
    check("four distinct corrections, four messages", len(SENT), 4)


async def test_failures_still_alert():
    print("\nfailures were always the point — they still send")
    reset()
    await tg.notify_fail("Mini Prathav", "BTCUSD", "buy", 5, "insufficient_margin")
    check("sent", len(SENT), 1)
    check_true("labelled as a failure", "Mirror Failed" in SENT[0], SENT[0])
    check_true("carries the exchange's reason", "insufficient_margin" in SENT[0], SENT[0])


async def test_deliberate_skip_is_not_labelled_a_failure():
    print("\na deliberate skip must not read as something breaking")
    reset()
    await tg.notify_fail("Mini Prathav", "BTCUSD", "topup", 5, "price drifted 70%")
    check_true("labelled a skip, not a failure", "Left out of sync" in SENT[0], SENT[0])
    check_true("says nothing broke", "nothing failed" in SENT[0], SENT[0])


async def test_routine_trade_notifications_have_no_callers():
    """The functions still exist; nothing in the engine may call them."""
    print("\nno routine open/close notification may be wired up")
    import pathlib
    src = pathlib.Path("app/core/copy_engine.py").read_text(encoding="utf-8")
    check("no notify_open call sites", src.count("tg.notify_open("), 0)
    check("no notify_close call sites", src.count("tg.notify_close("), 0)
    check_true("corrections ARE wired up", src.count("tg.notify_correction(") >= 4,
               src.count("tg.notify_correction("))
    check_true("failures are still wired up", src.count("tg.notify_fail(") > 0)


async def test_position_mismatch_is_not_forwarded():
    print("\nposition_mismatch is covered by better messages — muted on Telegram")
    reset()
    await tg.send_alert({"level": "error", "type": "position_mismatch",
                         "message": "Mini Prathav out of sync on BTCUSD"})
    check("nothing sent", len(SENT), 0)
    # ...but other alert types still go through.
    await tg.send_alert({"level": "warning", "type": "high_slippage",
                         "message": "0.05% on BTCUSD"})
    check("high_slippage still sends", len(SENT), 1)


async def test_persistent_condition_alerts_once_not_hourly():
    print("\na condition that holds all day is announced ONCE")
    reset()
    for _ in range(200):        # the sweep runs every 15s
        await tg.notify_fail("Mini Prathav", "C-BTC-80400", "topup", 2,
                             "price drifted 70% from master entry",
                             key="drift:f1:C-BTC-80400", window=tg.ONCE_WINDOW)
    check("one message for the whole episode", len(SENT), 1)
    check_true("and it says it was deliberate",
               "Left out of sync" in SENT[0], SENT[0])


async def test_resolved_condition_re_arms():
    print("\nbut once it resolves, the NEXT occurrence is announced again")
    reset()
    await tg.notify_fail("Mini Prathav", "BTCUSD", "buy", 5, "insufficient_margin",
                         key="recon:f1:BTCUSD", window=tg.ONCE_WINDOW)
    check("first occurrence sent", len(SENT), 1)
    await tg.notify_fail("Mini Prathav", "BTCUSD", "buy", 5, "insufficient_margin",
                         key="recon:f1:BTCUSD", window=tg.ONCE_WINDOW)
    check("still suppressed while it persists", len(SENT), 1)
    # The reconciler fixes it and clears the key.
    await tg.clear_alert("recon:f1:BTCUSD")
    await tg.notify_fail("Mini Prathav", "BTCUSD", "buy", 5, "insufficient_margin",
                         key="recon:f1:BTCUSD", window=tg.ONCE_WINDOW)
    check("a fresh episode alerts again", len(SENT), 2)


async def test_correction_quotes_the_earlier_failure():
    print("\ncause and cure in one message")
    reset()
    await tg.notify_fail("Mini Prathav", "C-BTC-81600", "buy", 26, "insufficient_margin")
    await tg.notify_correction("Mini Prathav", "C-BTC-81600", "OPENED", 26,
                               held=0, target=26, master=2300,
                               why="the follower had no leg at all")
    check("two messages", len(SENT), 2)
    check_true("the correction names the exchange's reason",
               "insufficient_margin" in SENT[1], SENT[1])
    check_true("labelled as the earlier failure",
               "earlier failure" in SENT[1], SENT[1])


async def test_deliberate_skip_is_not_recorded_as_a_cause():
    print("\na deliberate skip must not be quoted as a failure cause")
    reset()
    await tg.notify_fail("Mini Prathav", "ETHUSD", "topup", 2, "price drifted 70%")
    await tg.notify_correction("Mini Prathav", "ETHUSD", "TOPPED UP", 2,
                               held=1, target=3, master=200)
    check_true("no phantom cause on the correction",
               "earlier failure" not in SENT[1], SENT[1])


async def test_silent_when_not_configured():
    print("\nunconfigured Telegram sends nothing rather than erroring")
    reset()
    tok, chat = tg.settings.TELEGRAM_BOT_TOKEN, tg.settings.TELEGRAM_CHAT_ID
    tg.settings.TELEGRAM_BOT_TOKEN = ""
    tg.settings.TELEGRAM_CHAT_ID = ""
    try:
        await tg.notify_correction("A", "BTCUSD", "TRIMMED", 1, held=2, target=1)
        await tg.notify_fail("A", "BTCUSD", "buy", 1, "nope")
        check("nothing sent", len(SENT), 0)
    finally:
        tg.settings.TELEGRAM_BOT_TOKEN, tg.settings.TELEGRAM_CHAT_ID = tok, chat


async def main():
    print("=" * 72)
    print("telegram — failures and reconciler corrections, not a trade log")
    print("=" * 72)
    original = tg.send_message
    try:
        for fn in (
            test_correction_names_the_bug,
            test_correction_without_context_still_sends,
            test_repeat_correction_is_collapsed,
            test_a_different_correction_still_gets_through,
            test_failures_still_alert,
            test_deliberate_skip_is_not_labelled_a_failure,
            test_routine_trade_notifications_have_no_callers,
            test_position_mismatch_is_not_forwarded,
        test_persistent_condition_alerts_once_not_hourly,
        test_resolved_condition_re_arms,
        test_correction_quotes_the_earlier_failure,
        test_deliberate_skip_is_not_recorded_as_a_cause,
        test_silent_when_not_configured,
        ):
            await fn()
    finally:
        tg.send_message = original
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
