# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: a correction must be sized against the position as it is NOW.

Run:  venv/Scripts/python.exe test_last_look.py
No network, no Redis, no Supabase.

THE GAP. A reconcile pass reads the follower's book at the top, then spends
several exchange round trips deciding what to do -- checking whether the master
is still working an order, looking up missed exit ids, cancelling the
follower's own stuck closes -- and only then places. The size it places is the
one it computed before all of that.

    excess = held - target          <- computed here
    ... master order book read      <- round trip
    ... missed exit lookup          <- round trip
    ... cancel stuck closes         <- round trip(s)
    place_order(size=excess)        <- sent here, on the old number

reduce_only caps the damage on a trim or a close: the follower can never be
flipped to the other side. It does NOT stop an over-trim once the leg has
already shrunk, and it does not apply at all to a top-up, which adds -- a
top-up sized against a leg that has since filled opens exposure nobody asked
for.

Desk call, Jigar via Tushar 2026-11-20: re-read the position right before
acting, the same way the mirror path re-reads the order map before punching.

THE RULE. Always the SMALLER of what was planned and what is now true.
Under-correcting is picked up by the next pass ten seconds later;
over-correcting is a real trade in the wrong direction.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.copy_engine import CopyEngine

FAILURES = []
act = CopyEngine._still_worth_acting


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


# --- trim: long 800, target 715, planned cut 85 -----------------------------


def test_trim_when_nothing_moved():
    print("\n1. TRIM — the leg is exactly where we left it")
    check("cuts the planned 85", act(85, 800.0, 800.0, target=715), 85)


def test_trim_when_the_leg_shrank():
    """The master's exit mirrored while we were deciding. Cutting 85 off a
    position that is now 760 overshoots the target by 40."""
    print("\n2. TRIM — the leg shrank while we decided")
    check("cuts only what is still excess", act(85, 760.0, 800.0, target=715), 45)
    check("never more than planned", act(85, 900.0, 800.0, target=715), 85)


def test_trim_when_it_settled_itself():
    print("\n3. TRIM — already at target, or past it")
    check("at target: stand down", act(85, 715.0, 800.0, target=715), 0)
    check("below target: stand down", act(85, 700.0, 800.0, target=715), 0)


def test_trim_when_the_leg_is_gone():
    print("\n4. TRIM — the leg closed entirely")
    check("flat: stand down", act(85, 0.0, 800.0, target=715), 0)


def test_trim_when_the_side_flipped():
    """The follower is now SHORT where it was long. The correction was computed
    for a long; firing it here would add to the short."""
    print("\n5. TRIM — the side flipped underneath us")
    check("stand down", act(85, -800.0, 800.0, target=715), 0)


def test_an_unreadable_book_never_acts():
    """Same rule as everywhere else in this engine: an unknown is not
    permission. The next pass retries ten seconds later."""
    print("\n6. the read failed — act on nothing")
    check("trim stands down", act(85, None, 800.0, target=715), 0)
    check("close stands down", act(800, None, 800.0), 0)
    check("top-up stands down", act(85, None, 800.0, target=800, adding=True), 0)


# --- close: hold 3200, close all of it --------------------------------------


def test_close_covers_what_is_actually_held():
    print("\n7. CLOSE — cover what is there, not what was there")
    check("unchanged", act(3200, 3200.0, 3200.0), 3200)
    check("partly closed already", act(3200, 1200.0, 3200.0), 1200)
    check("gone", act(3200, 0.0, 3200.0), 0)
    check("flipped", act(3200, -3200.0, 3200.0), 0)


def test_close_never_sends_more_than_planned():
    """If the leg GREW, closing the new larger size would exceed what this
    correction was authorised for. The next pass sizes the rest."""
    print("\n8. CLOSE — a leg that grew is not an invitation")
    check("capped at the plan", act(3200, 5000.0, 3200.0), 3200)


# --- top-up: hold 700, target 800, planned add 100 --------------------------


def test_topup_when_nothing_moved():
    print("\n9. TOP-UP — the shortfall is still there")
    check("adds the planned 100", act(100, 700.0, 700.0, target=800, adding=True), 100)


def test_topup_when_the_leg_filled_meanwhile():
    """THE DANGEROUS ONE. A top-up adds, so reduce_only is not there to cap it.
    Sized against a leg that has since filled, it opens exposure nobody asked
    for."""
    print("\n10. TOP-UP — the leg filled while we decided")
    check("already at target: stand down",
          act(100, 800.0, 700.0, target=800, adding=True), 0)
    check("past target: stand down",
          act(100, 900.0, 700.0, target=800, adding=True), 0)
    check("partly filled: add only the rest",
          act(100, 760.0, 700.0, target=800, adding=True), 40)


def test_topup_on_a_flipped_or_empty_leg():
    print("\n11. TOP-UP — nothing to add to")
    check("flat: stand down", act(100, 0.0, 700.0, target=800, adding=True), 0)
    check("flipped: stand down", act(100, -700.0, 700.0, target=800, adding=True), 0)


def test_it_never_returns_a_negative():
    print("\n12. a correction is never a negative order")
    for args in ((85, 700.0, 800.0, 715), (100, 900.0, 700.0, 800)):
        planned, live, want, tgt = args
        check(f"planned {planned} vs live {live}",
              act(planned, live, want, target=tgt) >= 0, True)


# --- the engine actually calls it -------------------------------------------


def test_all_three_corrections_take_the_last_look():
    print("\n13. trim, close and top-up all re-read before acting")
    src = inspect.getsource(CopyEngine._reconcile_positions)
    # Four since 2026-11-24: OPEN gained a last look (it could double a leg a
    # live copy had just opened). It sizes inline, not via _still_worth_acting.
    check("four fresh reads (open re-reads too)", src.count("self._live_signed_fresh(client, sym)"), 4)
    check("open stands down when the leg is there", "not opening" in src, True)
    check("three re-sizes", src.count("self._still_worth_acting("), 3)
    check("trim stands down", "not trimming" in src, True)
    check("close stands down", "not closing" in src, True)
    check("top-up stands down", "not topping up" in src, True)


def test_the_fresh_read_is_really_fresh():
    """Reading through the engine's 3s position cache here would defeat the
    whole point."""
    print("\n14. the re-read bypasses the cache")
    src = inspect.getsource(CopyEngine._live_signed_fresh)
    check("goes to the exchange", "await client.get_positions()" in src, True)
    check("no cache lookup", "_pos_cache" in src, False)
    check("a failed read is not reported as flat", "return None" in src, True)


if __name__ == "__main__":
    for t in (test_trim_when_nothing_moved,
              test_trim_when_the_leg_shrank,
              test_trim_when_it_settled_itself,
              test_trim_when_the_leg_is_gone,
              test_trim_when_the_side_flipped,
              test_an_unreadable_book_never_acts,
              test_close_covers_what_is_actually_held,
              test_close_never_sends_more_than_planned,
              test_topup_when_nothing_moved,
              test_topup_when_the_leg_filled_meanwhile,
              test_topup_on_a_flipped_or_empty_leg,
              test_it_never_returns_a_negative,
              test_all_three_corrections_take_the_last_look,
              test_the_fresh_read_is_really_fresh):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
