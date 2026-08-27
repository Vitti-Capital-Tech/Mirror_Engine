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
    tg._redis = False          # force the in-memory dedupe path, no Redis
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
    check_true("labelled a skip, not a failure", "Copy Skipped" in SENT[0], SENT[0])
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
