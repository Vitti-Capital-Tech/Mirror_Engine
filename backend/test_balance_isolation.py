"""Regression: one master's balance must never size another master's follower.

Run:  venv/Scripts/python.exe test_balance_isolation.py
No network, no Redis, no Supabase.

THE SHAPE, AGAIN. auto_ratio divides by the master's balance. When that balance
reads 0 the engine does not fall back to 1:1 (that once produced a 610-lot
target on a 70 USD account, 2026-08-02) and does not drop the event either
(that fired nine times silently on 2026-09-04) -- it sizes on the last balance
that read above zero.

That last value lived in ONE slot on the engine:

    self._last_master_balance = 0.0

One engine serves every master. So the fallback handed whichever master read 0
the last good figure from whichever master had been read most recently -- a
real balance, from the wrong desk.

On 2026-11-19 the two live masters sat at 8509.61 (JD algo4) and 5243.16
(Dhwani). A crossed fallback sizes a Dhwani follower against 8509 instead of
5243: every leg of that event ~38% under, silently, with the log line still
saying "sizing on the last known good value".

Same family as the five symbol-keyed caches fixed on 2026-11-19 -- and missed
in that pass, because it is a scalar rather than a dict and did not look like a
cache.

The dedupe keys went the same way: `mbal:stale` and `mbal:none` were global, so
one desk's sizing alert silenced the other's for a week.
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

JIGAR = {"id": "867a7192-master", "name": "JD algo4"}
PRATIK = {"id": "48cb7aae-master", "name": "Dhwani"}
JD_BAL = 8509.61408251
DH_BAL = 5243.16203542


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def engine():
    e = CopyEngine.__new__(CopyEngine)
    e._last_master_balance = {}
    return e


def bal(e, master, value):
    return e._master_balance_or_last(value, master)


# ---------------------------------------------------------------------------


def test_the_live_pair_do_not_cross():
    """THE BUG: both masters live, Dhwani's balance reads 0."""
    print("\n1. THE LIVE PAIR: 8509 and 5243 on the same engine")
    e = engine()
    check("jigar reads his own", bal(e, JIGAR, JD_BAL), JD_BAL)
    check("pratik reads his own", bal(e, PRATIK, DH_BAL), DH_BAL)
    # Dhwani's next read fails. JD was recorded most recently in the old slot.
    check("pratik falls back to PRATIK's last, not jigar's",
          bal(e, PRATIK, 0.0), DH_BAL)
    check("and jigar is untouched by it", bal(e, JIGAR, 0.0), JD_BAL)


def test_the_old_single_slot_for_comparison():
    """What one shared slot did, driven with the same sequence."""
    print("\n2. one slot, for comparison")
    slot = {"v": 0.0}

    def old(value):
        if value > 0:
            slot["v"] = value
            return value
        return slot["v"]

    old(DH_BAL)
    old(JD_BAL)                      # jigar read most recently
    check("pratik is sized on JIGAR's balance", old(0.0), JD_BAL)
    check("which is 62% too large", round(JD_BAL / DH_BAL, 2), 1.62)


def test_a_master_with_no_history_is_refused():
    """No cached figure for THIS master means no sizing — not a borrowed one
    from a desk that happens to have been read."""
    print("\n3. an unknown master is refused, not lent a number")
    e = engine()
    bal(e, JIGAR, JD_BAL)
    check("pratik gets nothing", bal(e, PRATIK, 0.0), 0.0)


def test_a_bad_value_does_not_poison_the_store():
    print("\n4. garbage in does not become the fallback")
    e = engine()
    bal(e, JIGAR, JD_BAL)
    check("a string is not stored", bal(e, JIGAR, "oops"), JD_BAL)
    check("None is not stored", bal(e, JIGAR, None), JD_BAL)
    check("negative is not stored", bal(e, JIGAR, -5.0), JD_BAL)
    check("still exactly one entry", len(e._last_master_balance), 1)


def test_a_missing_master_row_still_works():
    print("\n5. a missing row degrades, never throws on the sizing path")
    e = engine()
    check("binds to the empty key", bal(e, None, JD_BAL), JD_BAL)
    check("and reads back", bal(e, None, 0.0), JD_BAL)


def test_an_engine_upgraded_in_place_does_not_crash():
    """The attribute was a float before this change. A worker that somehow
    carries the old shape must degrade, not raise, on the order path."""
    print("\n6. the old scalar shape is survivable")
    e = engine()
    e._last_master_balance = 0.0            # pre-upgrade state
    check("no crash, no borrowed number", bal(e, JIGAR, 0.0), 0.0)
    check("and it recovers", bal(e, JIGAR, JD_BAL), JD_BAL)


def test_the_store_really_is_per_master():
    print("\n7. the engine declares it per master")
    src = inspect.getsource(CopyEngine.__init__)
    check("a dict, not a scalar",
          "self._last_master_balance: dict = {}" in src, True)
    body = inspect.getsource(CopyEngine._master_balance_or_last)
    check("written per master", "self._last_master_balance[_mk] = b" in body, True)
    check("read per master", "self._last_master_balance.get(_mk)" in body, True)
    check("and normalised before either", "isinstance(getattr(self, \"_last_master_balance\", None), dict)" in body, True)


def test_the_alerts_are_per_master_too():
    """A global dedupe key means one desk's sizing failure silences the
    other's for ONCE_WINDOW — a week."""
    print("\n8. and so are its alerts")
    body = inspect.getsource(CopyEngine._master_balance_or_last)
    check("stale key carries the master", 'f"mbal:stale:{_mk}"' in body, True)
    check("none key carries the master", 'f"mbal:none:{_mk}"' in body, True)
    check("no global key left", '"mbal:stale"' in body, False)


if __name__ == "__main__":
    for t in (test_the_live_pair_do_not_cross,
              test_the_old_single_slot_for_comparison,
              test_a_master_with_no_history_is_refused,
              test_a_bad_value_does_not_poison_the_store,
              test_a_missing_master_row_still_works,
              test_an_engine_upgraded_in_place_does_not_crash,
              test_the_store_really_is_per_master,
              test_the_alerts_are_per_master_too):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
