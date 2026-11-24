"""Regression: a reconcile pass must never die on an unbound local.

Run:  venv/Scripts/python.exe test_reconcile_unbound.py
No network, no Redis, no Supabase.

WHAT HAPPENED. _reconcile_positions walks a follower's book in one pass --
CLOSE, OPEN, TRIM, TOP-UP -- inside ONE try/except per follower. On 2026-11-19
an alerting change added this to the CLOSE branch:

    _stuck_detail[("close", fid, sym)] = (fol.get("name"), held, 0, msz)

`held` is bound in the TRIM branch of the same loop. Python function scope
means it survives from an earlier iteration, so this only raises when a close
candidate is reached before any trim candidate has set it -- which is why it
read fine and then fired live:

    22:12:06 .. 22:13:58 UTC, twelve consecutive passes for JD algo3
    "reconcile_positions error: cannot access local variable 'held'"

Because that try/except wraps the WHOLE pass, the exception did not drop one
alert line -- it abandoned close, open, trim and top-up for that follower on
every pass. A silent, total reconciler outage, introduced by an alerting
change. The follower sat over-exposed on four legs throughout.

HOW THIS IS CHECKED. Not by reading the source -- a human already read it and
missed it. CPython's own compiler tracks exactly this: since 3.11 it emits
LOAD_FAST_CHECK (rather than LOAD_FAST) for any local it cannot prove is bound
at that point. So the question "could this raise UnboundLocalError?" is
answerable from the bytecode, with the compiler's own flow analysis.

Most of what it flags is provably fine at runtime and only conservatively
flagged. That is why the engine's current set is PINNED below rather than
required to be empty: the test fails when a NEW possibly-unbound local appears,
which is precisely what `held` was.
"""
import dis
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

# The engine's possibly-unbound locals as of the fix. Each is a latent
# UnboundLocalError the compiler could not rule out; none is known to fire.
# Adding a name here is a deliberate act -- do it only after convincing
# yourself the path cannot be reached, and say why in the commit.
BASELINE = {
    # Each is bound in one branch and read in another that only runs when
    # the first did -- `_prev_v` under `if _resized`, `foid`/`leg`/`sl`
    # inside the bracket self-heal, `follower_order_id` and `resp_obj`
    # after a successful place. Correlated, so unreachable in practice,
    # and not provable. Audited 2026-11-20.
    "_mirror_place": {
        # "leg" and "sl" left on 2026-11-24 when the stop placement became one
        # awaited call (_follower_trigger) — no longer possibly unbound.
        "_prev_v", "foid", "follower_order_id", "held", "leg_key",
        "resp_obj", "result", "why_zero",
    },
    # The escalation sweep's loop variables, plus two `except ... as`
    # names that Python deletes at the end of their handler. Same
    # pattern: correlated conditions, audited 2026-11-20.
    "_reconcile_positions": {
        "reason",
        "_age", "_e", "_held", "_kind", "_leg_err", "_live",
        "_msz", "_name", "_nm", "_now", "_qty", "_sk",
        "_target", "key", "why",
    },
}

SAFE = set(dir(__builtins__)) | {"self"}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def maybe_unbound(func) -> set:
    """Locals CPython cannot prove are bound where they are read."""
    code = getattr(func, "__code__", None)
    if code is None:
        return set()
    return {i.argval for i in dis.get_instructions(code)
            if i.opname == "LOAD_FAST_CHECK"}


def engine_methods():
    for name, obj in sorted(vars(CopyEngine).items()):
        if getattr(obj, "__code__", None) is not None:
            yield name, obj


# ---------------------------------------------------------------------------


def test_the_compiler_really_reports_this():
    """Prove the instrument works before trusting its verdict.

    `b` is read on a path where only the other branch bound it -- the exact
    shape of the live bug, reduced to four lines.
    """
    print("\n1. the check itself, on the shape that broke production")
    ns = {}
    exec("def f(flag):\n"
         "    if flag:\n"
         "        b = 1\n"
         "    return b\n", ns)
    check("flagged", maybe_unbound(ns["f"]), {"b"})

    ns2 = {}
    exec("def g(flag):\n"
         "    b = 0\n"
         "    if flag:\n"
         "        b = 1\n"
         "    return b\n", ns2)
    check("and does not cry wolf", maybe_unbound(ns2["g"]), set())


def test_held_is_bound_on_every_path():
    """THE TWO THAT FIRED. Reverting either one-line fix puts the name straight
    back into this set -- both checked against the pre-fix file.

    `held` broke the CLOSE correction. `target` broke the OPEN one the same
    way: the alert line read it seven lines before it was computed, borrowing
    whatever the trim pass had left behind, and raising when the trim pass had
    left nothing. OPEN is the correction that fires when the follower has NO
    position at all, so losing it means a missed leg is never recovered --
    reproduced in test_order_ledger.py section 9b, which went from recovering
    the leg to placing nothing.
    """
    print("\n2. THE LIVE BUGS: `held` in close, `target` in open")
    flagged = maybe_unbound(CopyEngine._reconcile_positions)
    check("`held` cannot be unbound", "held" in flagged, False)
    check("`target` cannot be unbound", "target" in flagged, False)


def test_the_close_alert_uses_the_close_branch_s_own_size():
    print("\n3. and it reports the size that branch actually has")
    body = inspect.getsource(CopyEngine._reconcile_positions)
    check("built from fsz", 'fol.get("name"), int(abs(fsz)), 0, msz)' in body, True)
    check("no longer from held", 'fol.get("name"), held, 0, msz)' in body, False)


def test_no_new_possibly_unbound_locals():
    """The general rule, not just this variable.

    The next one of these will not be called `held`, and will not be in the
    close branch. It will be a name someone adds to an alert, a log line or an
    early-exit path -- cheap to add, impossible to spot, and it takes the whole
    reconciler down for that master until the condition changes.
    """
    print("\n4. no NEW possibly-unbound local anywhere in the engine")
    new = {}
    for name, fn in engine_methods():
        extra = maybe_unbound(fn) - BASELINE.get(name, set())
        if extra:
            new[name] = sorted(extra)
    check("nothing new", new, {})


def test_the_baseline_has_not_silently_healed():
    """If a name leaves the baseline, the baseline is stale -- drop it, so the
    list stays a real inventory rather than a list of names nobody checked."""
    print("\n5. the pinned list still describes the code")
    stale = {}
    for name, expected in BASELINE.items():
        fn = getattr(CopyEngine, name, None)
        gone = expected - maybe_unbound(fn)
        if gone:
            stale[name] = sorted(gone)
    check("no stale entries", stale, {})


def test_one_raise_costs_the_whole_pass():
    """Why a single bad name mattered this much: the corrections shared one
    try/except, so whichever leg raised first cancelled every other leg -- and
    the OPEN pass below it as well."""
    print("\n6. the blast radius it used to have")
    body = inspect.getsource(CopyEngine._reconcile_positions)
    check("the pass is still wrapped per follower",
          "reconcile_positions error for" in body, True)
    for kind in ("topup", "trim", "close", "open"):
        check(f"{kind} correction lives inside it",
              f'_stuck_detail[("{kind}"' in body, True)


def _sweep(legs, isolated):
    """One reconcile sweep over several legs, with and without per-leg
    isolation. `legs` maps symbol -> True when correcting it raises.

    Returns the symbols actually corrected.
    """
    done = []
    if isolated:
        for sym, boom in legs.items():
            try:
                if boom:
                    raise UnboundLocalError("cannot access local variable 'held'")
                done.append(sym)
            except Exception:
                continue
    else:
        try:
            for sym, boom in legs.items():
                if boom:
                    raise UnboundLocalError("cannot access local variable 'held'")
                done.append(sym)
        except Exception:
            pass
    return done


def test_a_bad_leg_no_longer_takes_the_others_with_it():
    """THE LIVE SHAPE: four legs, the first one raises.

    On 2026-11-19 all four legs were unmatched and unprotected, the first one
    raised, and the other three were never even looked at -- twelve passes
    running.
    """
    print("\n7. one bad leg costs that leg, not the book")
    legs = {"C-BTC-65000": True, "C-BTC-64000": False,
            "C-BTC-65200": False, "C-BTC-64200": False}
    check("old shape corrects NOTHING (the live failure)",
          _sweep(legs, isolated=False), [])
    check("isolated shape corrects the other three",
          _sweep(legs, isolated=True),
          ["C-BTC-64000", "C-BTC-65200", "C-BTC-64200"])


def test_both_correction_loops_isolate_their_legs():
    print("\n8. and the engine really is built that way")
    body = inspect.getsource(CopyEngine._reconcile_positions)
    check("close/trim legs are isolated",
          'f"reconcile: close/trim pass failed for "' in body, True)
    check("open legs are isolated",
          'f"reconcile: open pass failed for "' in body, True)
    check("a failed leg is skipped, not swallowed whole",
          body.count("except Exception as _leg_err:"), 2)


def test_a_crashed_correction_is_announced():
    """The failure nobody can see from outside: the engine crashing where it
    was supposed to CORRECT. The position simply stays wrong. It ran twelve
    times as a log WARNING while four legs sat unmatched."""
    print("\n9. a crashed correction reaches Telegram, not just the log")
    body = inspect.getsource(CopyEngine._reconcile_positions)
    check("it alerts", body.count('key=f"legerr:{fid}:{sym}"'), 2)
    check("once per episode, not once per pass",
          body.count('key=f"legerr:{fid}:{sym}", window=tg.ONCE_WINDOW,'), 2)
    check("and says the leg was NOT corrected",
          "was NOT corrected and will not be until this is fixed" in body, True)


if __name__ == "__main__":
    for t in (test_the_compiler_really_reports_this,
              test_held_is_bound_on_every_path,
              test_the_close_alert_uses_the_close_branch_s_own_size,
              test_no_new_possibly_unbound_locals,
              test_the_baseline_has_not_silently_healed,
              test_one_raise_costs_the_whole_pass,
              test_a_bad_leg_no_longer_takes_the_others_with_it,
              test_both_correction_loops_isolate_their_legs,
              test_a_crashed_correction_is_announced):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
