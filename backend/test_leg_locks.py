"""Regression: one leg, one writer — and a lock that can never become the jam.

Run:  venv/Scripts/python.exe test_leg_locks.py
No network, no Redis, no Supabase.

THE RACE. Two things write to a follower's book: the live mirror (event driven)
and the reconciler (every 10s). They were never mutually exclusive. The
dispatcher locked order events on their symbol, but a reconcile event carries a
list of positions and NO `symbol`, so it fell back to a single shared key and
took a different lock — which excludes nothing. Both paths could act on the
same follower and the same leg at the same moment, and what stood between that
and a double order was the two-pass confirmation and the last-look re-read:
good, and both guesses.

The key also carries the MASTER. Keyed on the symbol alone, two desks trading
the same contract blocked each other for no reason — JD algo4 and Dhwani both
hold BTC options and were on the same strike on 2026-11-19. Same mistake as the
five caches that served one master's answers to another.

THE OTHER HALF. A lock is only an improvement if it cannot become the new
failure. Two rules carry that:

  * NARROW — the reconciler locks ONE correction, never a pass, so a sweep of
    twenty legs never holds the mirror up on nineteen it does not care about.
  * BOUNDED — acquisition times out. A correction that cannot get its leg is
    late by one sweep, which is recoverable; a sweep stuck behind a hung REST
    call is not.
"""
import asyncio
import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core import leg_locks
from app.core.copy_engine import CopyEngine

FAILURES = []

JIGAR = "867a7192-master"
PRATIK = "48cb7aae-master"
SYM = "C-BTC-65000-201126"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def fresh():
    leg_locks._locks.clear()


# ---------------------------------------------------------------------------


def test_the_same_leg_is_one_writer_at_a_time():
    print("\n1. THE RACE: mirror and reconciler on the same leg")
    fresh()
    order = []

    async def mirror():
        async with leg_locks.hold(JIGAR, SYM, "mirror") as got:
            order.append(("mirror in", got))
            await asyncio.sleep(0.05)       # its REST round trips
            order.append(("mirror out", True))

    async def reconcile():
        await asyncio.sleep(0.01)           # arrives mid-place
        async with leg_locks.hold(JIGAR, SYM, "trim") as got:
            order.append(("recon in", got))
            order.append(("recon out", True))

    async def both():
        # Built inside the loop: asyncio.gather() at module/function level binds
        # its future to whatever loop exists then, which is not the one
        # asyncio.run() creates.
        await asyncio.wait_for(asyncio.gather(mirror(), reconcile()), timeout=5)

    asyncio.run(both())
    names = [n for n, _ in order]
    check("the mirror finishes before the sweep starts",
          names, ["mirror in", "mirror out", "recon in", "recon out"])
    check("both got the lock", all(g for _, g in order), True)


def test_the_old_behaviour_for_comparison():
    """Different keys exclude nothing — the dispatcher's `or "_"` fallback."""
    print("\n2. two different locks, as it was")
    fresh()
    order = []

    async def mirror():
        async with leg_locks.hold(JIGAR, SYM, "mirror") as _:
            order.append("mirror in")
            await asyncio.sleep(0.05)
            order.append("mirror out")

    async def reconcile():
        await asyncio.sleep(0.01)
        async with leg_locks.hold(JIGAR, "_", "trim") as _:   # the old fallback key
            order.append("recon in")

    async def both():
        await asyncio.gather(mirror(), reconcile())

    asyncio.run(both())
    check("the sweep walks straight in (the race)",
          order, ["mirror in", "recon in", "mirror out"])


def test_two_masters_on_one_strike_do_not_block():
    print("\n3. ISOLATION: same contract, different desks")
    fresh()
    order = []

    async def desk(master, tag):
        async with leg_locks.hold(master, SYM, tag) as _:
            order.append(tag + " in")
            await asyncio.sleep(0.05)
            order.append(tag + " out")

    async def both():
        await asyncio.gather(desk(JIGAR, "jd"), desk(PRATIK, "dhwani"))

    asyncio.run(both())
    check("both are inside at once", order[:2], ["jd in", "dhwani in"])
    check("neither waited for the other", sorted(order[2:]), ["dhwani out", "jd out"])


def test_a_held_leg_times_out_rather_than_waits_forever():
    """A hung REST call must cost one sweep, not the sweep."""
    print("\n4. the lock cannot become the jam")
    fresh()
    leg_locks.ACQUIRE_TIMEOUT = 0.05
    try:
        result = {}

        async def holder():
            async with leg_locks.hold(JIGAR, SYM, "stuck") as _:
                await asyncio.sleep(0.3)        # a call that hangs

        async def waiter():
            await asyncio.sleep(0.01)
            t0 = time.monotonic()
            async with leg_locks.hold(JIGAR, SYM, "trim") as got:
                result["got"] = got
                result["waited"] = time.monotonic() - t0

        async def both():
            await asyncio.wait_for(asyncio.gather(holder(), waiter()), timeout=5)

        asyncio.run(both())
        check("it gives up rather than acting", result["got"], False)
        check("and gives up quickly", result["waited"] < 0.25, True)
    finally:
        leg_locks.ACQUIRE_TIMEOUT = 5.0


def test_a_raising_action_still_releases():
    print("\n5. an exception inside must not strand the leg")
    fresh()

    async def boom():
        async with leg_locks.hold(JIGAR, SYM, "trim") as _:
            raise RuntimeError("place_order failed")

    async def run():
        try:
            await boom()
        except RuntimeError:
            pass
        async with leg_locks.hold(JIGAR, SYM, "next") as got:
            return got

    check("the next correction gets in", asyncio.run(run()), True)


def test_idle_legs_are_evicted_but_held_ones_are_not():
    """Delta's BTC options expire daily, so every day mints strikes that will
    never be seen again."""
    print("\n6. the registry does not grow forever")
    fresh()

    async def run():
        for i in range(70):
            leg_locks.get(JIGAR, f"C-BTC-{60000+i*200}-201126")
        # age everything, then hold one and force a sweep
        for v in leg_locks._locks.values():
            v[1] = 0.0
        held = leg_locks.get(JIGAR, SYM)
        await held.acquire()
        try:
            leg_locks._evict(time.time())
            return len(leg_locks._locks), (JIGAR, SYM) in leg_locks._locks
        finally:
            held.release()

    left, kept = asyncio.run(run())
    check("the idle ones are dropped", left, 1)
    check("the held one is kept", kept, True)


def test_a_fresh_leg_is_never_refused():
    print("\n7. an uncontended leg is instant")
    fresh()

    async def run():
        t0 = time.monotonic()
        async with leg_locks.hold(PRATIK, "P-ETH-1550-201126", "open") as got:
            return got, time.monotonic() - t0

    got, took = asyncio.run(run())
    check("granted", got, True)
    check("immediately", took < 0.05, True)


# --- wiring -----------------------------------------------------------------


def test_all_four_corrections_take_the_lock():
    print("\n8. every correction that places an order holds its leg")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    for what in ("trim", "close", "topup", "open"):
        check(f"{what} locks", f'leg_locks.hold(_mkey, sym, "{what}")' in src, True)
    check("and stands down when it cannot get it",
          src.count("if not _got:"), 4)


def test_the_sweep_holds_nothing_between_legs():
    """If a pass took one lock for its whole body, the mirror would wait on a
    sweep of every symbol rather than the one it cares about."""
    print("\n9. narrow, not pass-wide")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    first_loop = src.index("for sym, fsz in list(fpos.items()):")
    first_hold = src.index("leg_locks.hold(")
    check("the lock is taken inside the loop, not around it",
          first_hold > first_loop, True)


def test_the_dispatcher_keys_on_the_master_too():
    import io
    print("\n10. the live mirror uses the same key shape")
    main = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "app", "main.py"), encoding="utf-8").read()
    check("mirror events lock per leg",
          'leg_locks.hold(_event_master(event), sym, "mirror")' in main, True)
    check("fills too",
          'leg_locks.hold(_event_master(event_dict), sym, "fill")' in main, True)
    check("the master comes off the event",
          '"master_account_id"' in main, True)
    check("an event with no symbol is not locked on a shared fallback key",
          'or "_"' in main, False)


if __name__ == "__main__":
    for t in (test_the_same_leg_is_one_writer_at_a_time,
              test_the_old_behaviour_for_comparison,
              test_two_masters_on_one_strike_do_not_block,
              test_a_held_leg_times_out_rather_than_waits_forever,
              test_a_raising_action_still_releases,
              test_idle_legs_are_evicted_but_held_ones_are_not,
              test_a_fresh_leg_is_never_refused,
              test_all_four_corrections_take_the_lock,
              test_the_sweep_holds_nothing_between_legs,
              test_the_dispatcher_keys_on_the_master_too):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
