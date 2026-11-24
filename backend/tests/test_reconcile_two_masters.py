# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: the reconciler's two-pass confirmation, with more than one master.

Run:  venv/Scripts/python.exe test_reconcile_two_masters.py
No network, no Redis, no Supabase.

WHAT THE TWO-PASS RULE IS FOR. Before the reconciler opens, closes, trims or
tops up a position it wants to see the same discrepancy twice in a row. A book
read mid-flight — an order half filled, a position still settling — looks wrong
for a moment and then isn't. Acting on the first reading churns real positions.

HOW IT BROKE. The state lived in ONE set on the engine:

    self._recon_trim_prev = current_trim

but the reconcile loop runs once per MASTER against a shared engine instance.
Each pass overwrote that set with only its own candidates. Master A's pass
recorded a pending trim; master B's pass, moments later, found nothing and
wiped it; master A's next pass started from scratch. Nothing was ever confirmed
twice, so nothing was ever acted on.

It survived months of production because there was only ever one active master.
It broke the moment a second came online (2026-11-18 13:53), and on
2026-11-19 04:10 a follower sat 2,750 lots over-exposed while the reconciler
logged "confirming next pass before trimming" thirteen times across 2m07s and
never trimmed.

These tests drive the real confirmation logic — the same dict reads and writes
the engine performs — with one master and with two, and prove the second
master's pass can no longer erase the first's memory.
"""
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

M_JIGAR = "867a7192-master"     # JD algo4
M_PRATIK = "48cb7aae-master"    # Dhwani
TRIM = ("follower-algo3", "P-BTC-61600-191126", 2750)


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def engine():
    e = CopyEngine.__new__(CopyEngine)
    e._recon_open_prev = {}
    e._recon_close_prev = {}
    e._recon_trim_prev = {}
    e._recon_topup_prev = {}
    return e


def trim_pass(e, mkey, candidates):
    """One reconcile pass for one master, mirroring the engine's own logic:
    read this master's bucket, decide, then write this master's bucket back.

    Returns what the pass would ACT on — a candidate seen on the previous pass
    too. Anything else is only recorded for next time.
    """
    current = set(candidates)
    acted = [c for c in current if c in e._recon_trim_prev.get(mkey, set())]
    e._recon_trim_prev[mkey] = current
    return acted


# ---------------------------------------------------------------------------


def test_the_old_shared_state_loses_it():
    """The bug, reproduced, so the contrast is on the record.

    This is what the engine used to do: ONE container for every master, written
    whole on each pass. Driven with the exact live sequence it acts zero times,
    which is what left 2,750 lots standing.
    """
    print("\n0. the old shared set, for comparison")
    shared = {"prev": set()}

    def old_pass(candidates):
        current = set(candidates)
        acted = [c for c in current if c in shared["prev"]]
        shared["prev"] = current          # whole-state overwrite, per master
        return acted

    acted = []
    for _ in range(13):
        acted += old_pass([TRIM])         # Jigar's loop
        old_pass([])                      # Pratik's loop, quiet — wipes it
    check("acts ZERO times (the live failure)", len(acted), 0)


def test_the_engine_really_keys_this_per_master():
    """Ties the harness above to the real code.

    The helper models the engine's read/write. If someone reverts those to a
    single shared set this test fails, rather than the suite passing against a
    design the engine no longer has.
    """
    print("\n0b. the engine's own state is per-master")
    import inspect
    from app.core import copy_engine as ce
    src = inspect.getsource(ce.CopyEngine.__init__)
    for attr in ("_recon_open_prev", "_recon_close_prev",
                 "_recon_trim_prev", "_recon_topup_prev"):
        check(f"{attr} is a dict", f"self.{attr}: dict = {{}}" in src, True)
    body = inspect.getsource(ce.CopyEngine._reconcile_positions)
    check("writes only this master's bucket",
          "self._recon_trim_prev[_mkey] = current_trim" in body, True)
    check("reads only this master's bucket",
          "self._recon_trim_prev.get(_mkey" in body, True)


def test_every_two_pass_memory_is_per_master():
    """All SIX of them, not just the ones that happened to be noticed.

    The reconcile sweep has four (open / close / trim / top-up) and the
    protection sweep has two (orphaned SL/TP, and MISSING SL/TP). The first
    four were keyed per master on 2026-11-19 and the other two were missed in
    the same pass, which is exactly how a fix leaves half a bug behind.

    _prot_missing is the worst of the six: it gates PLACING protection a
    follower lacks, so while it could not confirm, a follower with no stop-loss
    would never be given one. That is a live position left unprotected, not a
    cosmetic gap.
    """
    print("\n0c. all six confirmations are per-master")
    import inspect
    from app.core import copy_engine as ce
    src = inspect.getsource(ce.CopyEngine.__init__)
    for attr in ("_recon_open_prev", "_recon_close_prev", "_recon_trim_prev",
                 "_recon_topup_prev", "_prot_orphans_prev", "_prot_missing_prev"):
        check(f"{attr} is a dict", f"self.{attr}: dict = {{}}" in src, True)

    prot = inspect.getsource(ce.CopyEngine._sync_protection)
    check("protection reads its own bucket",
          "_prot_missing_prev.get(_pkey" in prot, True)
    check("protection writes its own bucket",
          "self._prot_missing_prev[_pkey] = current_missing" in prot, True)
    check("orphan sweep reads its own bucket",
          "_prot_orphans_prev.get(_pkey" in prot, True)


def test_one_master_confirms_on_the_second_pass():
    print("\n1. the rule itself: never act on the first sighting")
    e = engine()
    check("first pass only records", trim_pass(e, M_JIGAR, [TRIM]), [])
    check("second pass acts", trim_pass(e, M_JIGAR, [TRIM]), [TRIM])


def test_a_second_master_cannot_erase_the_first_s_memory():
    print("\n2. THE BUG: two masters, interleaved passes")
    e = engine()
    # Jigar's loop sees the over-exposure and records it.
    check("jigar pass 1 records", trim_pass(e, M_JIGAR, [TRIM]), [])
    # Pratik's loop runs moments later with nothing wrong on his desk. Before
    # the fix this wrote an empty set over the shared state.
    check("pratik pass sees nothing", trim_pass(e, M_PRATIK, []), [])
    # Jigar's next pass must still remember.
    check("jigar pass 2 STILL acts", trim_pass(e, M_JIGAR, [TRIM]), [TRIM])


def test_it_survives_many_interleavings():
    print("\n3. thirteen passes, as happened live")
    e = engine()
    acted = []
    for i in range(13):
        acted += trim_pass(e, M_JIGAR, [TRIM])
        trim_pass(e, M_PRATIK, [])          # the other desk, quiet throughout
    # Every pass after the first should act — the live run acted zero times.
    check("acted on 12 of 13 passes", len(acted), 12)
    check("never zero (the live failure)", len(acted) > 0, True)


def test_each_master_keeps_its_own_candidates():
    print("\n4. the buckets do not bleed into each other")
    e = engine()
    jigars = ("follower-algo3", "P-BTC-61600-191126", 2750)
    pratiks = ("follower-dhwani1", "C-BTC-68000-181126", 300)
    trim_pass(e, M_JIGAR, [jigars])
    trim_pass(e, M_PRATIK, [pratiks])
    check("jigar remembers only his", e._recon_trim_prev[M_JIGAR], {jigars})
    check("pratik remembers only his", e._recon_trim_prev[M_PRATIK], {pratiks})
    check("jigar acts on his", trim_pass(e, M_JIGAR, [jigars]), [jigars])
    check("pratik acts on his", trim_pass(e, M_PRATIK, [pratiks]), [pratiks])


def test_a_discrepancy_that_goes_away_is_not_acted_on():
    print("\n5. the rule still protects against mid-flight readings")
    e = engine()
    trim_pass(e, M_JIGAR, [TRIM])            # seen once
    check("settled before pass 2", trim_pass(e, M_JIGAR, []), [])
    # And it must start over, not act on a stale memory.
    check("reappearing starts over", trim_pass(e, M_JIGAR, [TRIM]), [])


def test_a_changed_excess_restarts_the_confirmation():
    print("\n6. a size still moving must not be acted on")
    e = engine()
    trim_pass(e, M_JIGAR, [("follower-algo3", "P-BTC-61600-191126", 2750)])
    moved = ("follower-algo3", "P-BTC-61600-191126", 1400)
    check("different excess, not confirmed", trim_pass(e, M_JIGAR, [moved]), [])
    check("same excess twice, confirmed", trim_pass(e, M_JIGAR, [moved]), [moved])


if __name__ == "__main__":
    for t in (test_the_old_shared_state_loses_it,
              test_the_engine_really_keys_this_per_master,
              test_every_two_pass_memory_is_per_master,
              test_one_master_confirms_on_the_second_pass,
              test_a_second_master_cannot_erase_the_first_s_memory,
              test_it_survives_many_interleavings,
              test_each_master_keeps_its_own_candidates,
              test_a_discrepancy_that_goes_away_is_not_acted_on,
              test_a_changed_excess_restarts_the_confirmation):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
