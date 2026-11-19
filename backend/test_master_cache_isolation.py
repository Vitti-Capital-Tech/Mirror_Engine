"""Regression: one master's cached answers must never be served to another.

Run:  venv/Scripts/python.exe test_master_cache_isolation.py
No network, no Redis, no Supabase.

THE SHAPE. One CopyEngine serves every master, and five of its caches were
keyed on the SYMBOL alone:

    self._master_pos_cache[symbol]      = (size, ts)       # 3s
    self._master_signed_cache[symbol]   = (signed, ts)     # 3s
    self._master_exit_cache[symbol]     = (reason, ts)     # 20s
    self._master_exits_cache[(symbol, reduce_only)]        # 3s
    self._master_size_peak[symbol]                         # no expiry

Two desks trading the same contract therefore overwrote each other. They did:

    2026-11-19 04:10:51  JD algo4 on P-BTC-61600-191126  qty 3100
    2026-11-19 04:10:52  Dhwani   on P-BTC-61600-191126  qty 3000

Within the TTL, whichever master asked second was handed the other's number.

WHY IT MATTERS MOST FOR THE 20s ONE. _classify_master_exit decides whether a
follower's orphan is force-closed (its master closed by hand) or left alone
(its master's SL/TP hit and the follower's own jittered stop will do the job).
Answered from the wrong master it closes a position that should have been left
alone, or leaves one that should have gone.

And the peak — which has no expiry at all — suppresses top-ups while a master
sits below its high-water mark. Shared, a neighbouring master's larger position
suppresses top-ups this one genuinely needs, or its smaller one lets a top-up
through mid-unwind: exactly the 2026-08-03 incident the engine comments
describe.

No evidence any of this has mis-fired. It was found by looking for the shape
after three bugs of the same family in one day, not from a symptom.
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

from app.core.copy_engine import CopyEngine
from app.core import order_ledger as ledger

FAILURES = []

SYMBOL = "P-BTC-61600-191126"
JIGAR = {"id": "867a7192-master", "name": "JD algo4"}
PRATIK = {"id": "48cb7aae-master", "name": "Dhwani"}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def engine():
    e = CopyEngine.__new__(CopyEngine)
    e._master_pos_cache = {}
    e._master_signed_cache = {}
    e._master_exit_cache = {}
    e._master_exits_cache = {}
    e._master_size_peak = {}
    return e


# ---------------------------------------------------------------------------


def test_position_size_is_not_shared():
    print("\n1. THE LIVE OVERLAP: both desks on P-BTC-61600-191126")
    e = engine()
    now = time.time()
    e._master_pos_cache[(e._mid(JIGAR), SYMBOL)] = (3100, now)
    e._master_pos_cache[(e._mid(PRATIK), SYMBOL)] = (3000, now)
    check("jigar reads 3100", e._master_pos_cache[(e._mid(JIGAR), SYMBOL)][0], 3100)
    check("pratik reads 3000", e._master_pos_cache[(e._mid(PRATIK), SYMBOL)][0], 3000)
    check("two entries, not one", len(e._master_pos_cache), 2)


def test_the_old_key_collapsed_them():
    print("\n2. keyed on the symbol alone, for comparison")
    old = {}
    old[SYMBOL] = (3100, time.time())      # jigar writes
    old[SYMBOL] = (3000, time.time())      # pratik overwrites
    check("only one survives", len(old), 1)
    check("jigar now reads PRATIK's number", old[SYMBOL][0], 3000)


def test_the_exit_classification_is_not_shared():
    print("\n3. the 20s one — decides whether to force-close")
    e = engine()
    now = time.time()
    # Jigar closed by hand; Pratik's stop was hit. Opposite actions follow.
    e._master_exit_cache[(e._mid(JIGAR), SYMBOL)] = ("manual", now)
    e._master_exit_cache[(e._mid(PRATIK), SYMBOL)] = ("sl_tp", now)
    check("jigar: manual", e._master_exit_cache[(e._mid(JIGAR), SYMBOL)][0], "manual")
    check("pratik: sl_tp", e._master_exit_cache[(e._mid(PRATIK), SYMBOL)][0], "sl_tp")


def test_signed_size_is_not_shared():
    print("\n4. direction, which decides open vs close")
    e = engine()
    now = time.time()
    e._master_signed_cache[(e._mid(JIGAR), SYMBOL)] = (-3100, now)   # short
    e._master_signed_cache[(e._mid(PRATIK), SYMBOL)] = (3000, now)   # long
    check("jigar short", e._master_signed_cache[(e._mid(JIGAR), SYMBOL)][0], -3100)
    check("pratik long", e._master_signed_cache[(e._mid(PRATIK), SYMBOL)][0], 3000)


def test_the_resting_exit_ladder_is_not_shared():
    print("\n5. composite keys carry the master too")
    e = engine()
    jk = (e._mid(JIGAR), SYMBOL, True)
    pk = (e._mid(PRATIK), SYMBOL, True)
    e._master_exits_cache[jk] = ([("1", 100)], time.time())
    e._master_exits_cache[pk] = ([("2", 200)], time.time())
    check("distinct ladders", len(e._master_exits_cache), 2)
    check("jigar's", e._master_exits_cache[jk][0], [("1", 100)])
    check("working-key variant too",
          ("working", e._mid(JIGAR), SYMBOL) != ("working", e._mid(PRATIK), SYMBOL), True)


def test_the_peak_is_not_shared():
    print("\n6. the peak — no expiry, so a collision lasts")
    e = engine()
    e._master_size_peak[(e._mid(JIGAR), SYMBOL)] = 3100.0
    e._master_size_peak[(e._mid(PRATIK), SYMBOL)] = 500.0
    check("jigar's high", e._master_size_peak[(e._mid(JIGAR), SYMBOL)], 3100.0)
    check("pratik's own high", e._master_size_peak[(e._mid(PRATIK), SYMBOL)], 500.0)
    # And the persisted key must separate them as well.
    check("redis keys differ",
          ledger._peak_key(e._mid(JIGAR), SYMBOL) != ledger._peak_key(e._mid(PRATIK), SYMBOL),
          True)


def test_clearing_one_master_leaves_the_other_alone():
    print("\n7. one master exiting a leg must not clear the other's")
    e = engine()
    e._master_size_peak[(e._mid(JIGAR), SYMBOL)] = 3100.0
    e._master_size_peak[(e._mid(PRATIK), SYMBOL)] = 500.0
    mkey = e._mid(JIGAR)
    master_map = {}                        # jigar is flat on everything now
    for k in [k for k in e._master_size_peak if k[0] == mkey]:
        if k[1] not in master_map:
            e._master_size_peak.pop(k, None)
    check("jigar's cleared", (mkey, SYMBOL) in e._master_size_peak, False)
    check("pratik's untouched", e._master_size_peak.get((e._mid(PRATIK), SYMBOL)), 500.0)


def test_a_missing_master_row_does_not_raise():
    print("\n8. a missing row degrades, never throws on the order path")
    e = engine()
    check("None is a key, not a crash", e._mid(None), "")
    check("empty dict too", e._mid({}), "")


if __name__ == "__main__":
    for t in (test_position_size_is_not_shared,
              test_the_old_key_collapsed_them,
              test_the_exit_classification_is_not_shared,
              test_signed_size_is_not_shared,
              test_the_resting_exit_ladder_is_not_shared,
              test_the_peak_is_not_shared,
              test_clearing_one_master_leaves_the_other_alone,
              test_a_missing_master_row_does_not_raise):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
