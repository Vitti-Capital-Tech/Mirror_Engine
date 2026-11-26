# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Two followers of one master must not share a stop price.

Run:  cd backend && python tests/test_stop_ladder.py
No network, no Redis, no Supabase.

THE INCIDENT, 2026-11-26, Pratik's desk. Master Dhwani held C-BTC-67000-271126
with its stop at 64,000. Both of its followers sat at 64,050:

    Dhwani    SL 64,000
    Dhwani1   SL 64,050
    Dhwani2   SL 64,050     <- the same price

A pack of identical stops is the exact thing the offset was introduced to break
up: they fire in the same instant, at the same price, each making the other's
fill worse, and leave an obvious footprint in the book.

TWO SEPARATE CAUSES, both fixed here.

1. _follower_trigger fetched the follower's slot and THREW IT AWAY --
   `off, _slot = await self._tp_sl_params(...)` -- then placed every follower
   one offset beyond the master. The slot now multiplies: rung 0 at one offset,
   rung 1 at two, so they stop out in order BEHIND the master rather than
   together. That extends the desk's rule (a follower never exits before its
   master, 2026-11-24) to the followers' own ordering.

2. _tp_sl_params returned `(None, 0)` whenever the master had no tp_sl_offset
   configured -- collapsing two different facts into one. The caller then
   applied DEFAULT_TP_SL_OFFSET to a rung that was always 0, so a master who
   had tuned NOTHING got every follower on the same price. That is precisely
   the desk least likely to notice. JD algo4 has no offset set to this day.

Note on what this does NOT change: _jitter_trigger, the older straddle (slot 0
above, slot 1 below), is no longer called by anything -- all three live stop
paths go through _follower_trigger. Its own tests in test_tp_sl_offset.py still
pass and still describe that superseded behaviour.
"""
import asyncio
import inspect
import sys

from app.core.copy_engine import CopyEngine, DEFAULT_TP_SL_OFFSET

FAILURES = []

OWNER = "u1"
MASTER_ID = "m-dh"
# Oldest first: that ordering is what decides the rungs.
F1 = {"id": "f-dh1", "name": "Dhwani1", "owner_id": OWNER, "master_id": MASTER_ID}
F2 = {"id": "f-dh2", "name": "Dhwani2", "owner_id": OWNER, "master_id": MASTER_ID}
F3 = {"id": "f-dh3", "name": "Dhwani3", "owner_id": OWNER, "master_id": MASTER_ID}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, ok, got=None):
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r" % (got,)))
    if not ok:
        FAILURES.append(name)


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, rows): self.rows, self.f = rows, {}
    def select(self, *a, **k): return self
    def eq(self, c, v): self.f[c] = v; return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self):
        return _Res([r for r in self.rows
                     if all(str(r.get(c)) == str(v) for c, v in self.f.items())])


class DB:
    def __init__(self, rows): self.rows = rows
    def table(self, n): return _Q(self.rows)


def engine(offset, followers=(F1, F2), statuses=None):
    """A CopyEngine with just the account table this lookup reads."""
    statuses = statuses or {}
    rows = [{"id": MASTER_ID, "is_master": True, "status": "active",
             "tp_sl_offset": offset, "created_at": "2026-01-01", "owner_id": OWNER,
             "master_id": None}]
    for i, f in enumerate(followers):
        rows.append({"id": f["id"], "is_master": False,
                     "status": statuses.get(f["id"], "active"),
                     "tp_sl_offset": None, "created_at": "2026-01-%02d" % (2 + i),
                     "owner_id": OWNER, "master_id": MASTER_ID})
    e = CopyEngine.__new__(CopyEngine)
    e.db = DB(rows)
    e._slot_cache = {}
    e._SLOT_TTL_SEC = 0        # never serve a cached answer inside one test
    return e


def params(eng, follower):
    return asyncio.run(eng._tp_sl_params(follower, OWNER, MASTER_ID))


def trigger(eng, follower, master_stop, spot):
    off, slot = params(eng, follower)
    return CopyEngine._trigger_after_master(master_stop, spot, offset=off, rung=slot)


# ---------------------------------------------------------------------------


def test_the_incident():
    """THE CASE. Master at 64,000 with spot at 60,755, so the stops sit above."""
    print("\n1. the 2026-11-26 case: two followers, one master")
    eng = engine(offset=60)
    a = trigger(eng, F1, 64000, 60755)
    b = trigger(eng, F2, 64000, 60755)
    check("Dhwani1 one offset beyond", a, 64060.0)
    check("Dhwani2 two offsets beyond", b, 64120.0)
    check_true("they are NOT on the same price", a != b, (a, b))
    check_true("and both are beyond the master's", a > 64000 and b > 64000, (a, b))
    check_true("in order: master, then 1, then 2", 64000 < a < b, (a, b))


def test_it_works_with_nothing_configured():
    """CAUSE 2. A master who has tuned nothing is the one most likely to end up
    with a pack of identical stops, and was the one guaranteed to."""
    print("\n2. no offset configured -> the default, still laddered")
    eng = engine(offset=None)
    check("the default is 50", DEFAULT_TP_SL_OFFSET, 50.0)
    o1, s1 = params(eng, F1)
    o2, s2 = params(eng, F2)
    check("nothing stored on the master", o1, None)
    check("but follower 1 is still rung 0", s1, 0)
    check("and follower 2 is still rung 1", s2, 1)
    check("follower 1 at +50", trigger(eng, F1, 64000, 60755), 64050.0)
    check("follower 2 at +100", trigger(eng, F2, 64000, 60755), 64100.0)


def test_a_third_follower_keeps_going():
    print("\n3. a third follower takes the next rung")
    eng = engine(offset=60, followers=(F1, F2, F3))
    check("rung 2 is three offsets out", trigger(eng, F3, 64000, 60755), 64180.0)


def test_the_other_direction():
    """A stop BELOW the current price fires on a fall, so beyond means lower."""
    print("\n4. a stop that fires on a fall ladders downwards")
    eng = engine(offset=60)
    a = trigger(eng, F1, 58000, 60755)
    b = trigger(eng, F2, 58000, 60755)
    check("follower 1 below the master", a, 57940.0)
    check("follower 2 further below", b, 57880.0)
    check_true("still in order behind the master", 58000 > a > b, (a, b))


def test_a_paused_follower_frees_its_rung():
    """Rungs pack from the front. With Dhwani1 paused, Dhwani2 moves up to the
    first rung rather than sitting at twice the offset for no reason."""
    print("\n5. a paused follower is not counted")
    eng = engine(offset=60, statuses={"f-dh1": "paused"})
    check("Dhwani2 is now rung 0", params(eng, F2)[1], 0)
    check("so it sits one offset out", trigger(eng, F2, 64000, 60755), 64060.0)


def test_one_follower_is_unchanged():
    """The common case must not move. A single follower was at one offset
    before this change and must still be."""
    print("\n6. a master with one follower is untouched")
    eng = engine(offset=60, followers=(F1,))
    check("still exactly one offset out", trigger(eng, F1, 64000, 60755), 64060.0)


def test_the_guards_still_hold():
    print("\n7. the guards")
    T = CopyEngine._trigger_after_master
    check("no reference -> the master's own price, never in front",
          T(64000, None, offset=60, rung=1), 64000.0)
    check("a rung that would reach zero -> the master's own price",
          T(40, 60755, offset=60, rung=1), 40.0)
    check("a junk rung is treated as the first", T(64000, 60755, offset=60, rung=None),
          T(64000, 60755, offset=60, rung=0))
    check("a junk offset falls back to the default",
          T(64000, 60755, offset="abc", rung=0), 64050.0)
    check("no master stop -> nothing to place", T(None, 60755, offset=60, rung=1), None)


class Ticker:
    """Just enough client for _trigger_reference."""
    def __init__(self, spot): self.spot = spot
    async def get_ticker(self, symbol):
        return {"spot_price": self.spot, "mark_price": self.spot, "close": self.spot}


def test_through_follower_trigger_itself():
    """THE ONE THAT MATTERS. Every check above calls the arithmetic directly,
    which is not where the bug was -- _follower_trigger fetched the slot and
    dropped it on the floor. Drive the real entry point, the same one all three
    live stop paths use.
    """
    print("\n8. end to end, through the function that had the bug")
    eng = engine(offset=60)
    c = Ticker(60755)

    async def go(f):
        return await eng._follower_trigger(c, f, "C-BTC-67000-271126", 64000,
                                           "spot_price", OWNER, MASTER_ID)

    a, b = asyncio.run(go(F1)), asyncio.run(go(F2))
    check("Dhwani1 at one offset", a, 64060.0)
    check("Dhwani2 at two", b, 64120.0)
    check_true("not the same price, which is the whole point", a != b, (a, b))

    # And with nothing configured -- the case that was GUARANTEED to collapse,
    # because the unset guard forced every follower to rung 0. JD algo4 has no
    # offset set to this day.
    unset = engine(offset=None)

    async def go_unset(f):
        return await unset._follower_trigger(c, f, "C-BTC-67000-271126", 64000,
                                             "spot_price", OWNER, MASTER_ID)

    check("unset: follower 1 at +50", asyncio.run(go_unset(F1)), 64050.0)
    check("unset: follower 2 at +100", asyncio.run(go_unset(F2)), 64100.0)


def test_the_slot_is_actually_used():
    """A unit test of the arithmetic proves nothing if the caller still throws
    the slot away -- which is exactly what the bug was."""
    print("\n9. and _follower_trigger really passes it on")
    src = inspect.getsource(CopyEngine._follower_trigger)
    check_true("the slot is not discarded", "_slot" not in src, src)
    check_true("it is handed to the trigger as the rung", "rung=slot" in src, True)
    p = inspect.getsource(CopyEngine._tp_sl_params)
    # The DB-failure fallback still returns (None, 0) and should: an unreadable
    # account table is no reason to stop an order going out. What must be gone
    # is the guard that collapsed the two facts on the NORMAL path.
    check_true("the unset-offset guard no longer forces rung 0",
               'if offset in (None, ""):' not in p, p)
    check_true("but a DB failure still falls back safely",
               "return None, 0" in p.split("except Exception")[-1], True)


if __name__ == "__main__":
    for t in (test_the_incident,
              test_it_works_with_nothing_configured,
              test_a_third_follower_keeps_going,
              test_the_other_direction,
              test_a_paused_follower_frees_its_rung,
              test_one_follower_is_unchanged,
              test_the_guards_still_hold,
              test_through_follower_trigger_itself,
              test_the_slot_is_actually_used):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
