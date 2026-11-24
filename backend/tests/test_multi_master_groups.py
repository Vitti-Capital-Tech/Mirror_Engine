# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: several masters under one login, each with its own followers.

Run:  venv/Scripts/python.exe test_multi_master_groups.py
No network, no Redis, no Supabase — the db handle is a stub.

THE CHANGE. One email may now run up to 5 masters, each with up to 2 followers,
and the groups must not interfere.

WHY IT IS DANGEROUS. A follower used to be tied to its master IMPLICITLY, by
sharing an owner_id — nothing recorded which master it actually mirrored,
because with one master per owner there was only one answer. Put two masters
under that email and "my followers" resolves to EVERY follower the email owns,
so each master's fills copy into the other master's accounts. That is not a
display bug; it is real orders on real accounts that never asked for them.

So a follower now points at its master, and a group is master_id — not owner_id.
These tests are mostly about the negative: group A's master must never reach
group B's followers, however the lookup is phrased.
"""
import asyncio
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
OWNER = "one-email"

# ONE owner, TWO independent groups.
MA = {"id": "mA", "name": "Master A", "is_master": True, "status": "active",
      "owner_id": OWNER, "master_id": None, "balance": 10000.0,
      "tp_sl_offset": 50, "created_at": "2026-01-01"}
MB = {"id": "mB", "name": "Master B", "is_master": True, "status": "active",
      "owner_id": OWNER, "master_id": None, "balance": 20000.0,
      "tp_sl_offset": 80, "created_at": "2026-01-02"}
A1 = {"id": "a1", "name": "A-follower-1", "is_master": False, "status": "active",
      "owner_id": OWNER, "master_id": "mA", "created_at": "2026-01-03"}
A2 = {"id": "a2", "name": "A-follower-2", "is_master": False, "status": "active",
      "owner_id": OWNER, "master_id": "mA", "created_at": "2026-01-04"}
B1 = {"id": "b1", "name": "B-follower-1", "is_master": False, "status": "active",
      "owner_id": OWNER, "master_id": "mB", "created_at": "2026-01-05"}
ALL = [MA, MB, A1, A2, B1]


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Q:
    def __init__(self, rows):
        self.rows, self.f = rows, {}

    def select(self, *a, **kw):
        return self

    def eq(self, c, v):
        self.f[c] = v
        return self

    def order(self, *a, **kw):
        return self

    def execute(self):
        out = [r for r in self.rows
               if all(str(r.get(k)) == str(v) for k, v in self.f.items())]
        return type("R", (), {"data": out})()


class DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _n):
        return Q(self.rows)


def engine():
    e = CopyEngine.__new__(CopyEngine)
    e._slot_cache = {}
    e._SLOT_TTL_SEC = 10.0
    e.db = DB(ALL)
    return e


# ---------------------------------------------------------------------------


def test_each_master_gets_only_its_own_followers():
    print("\n1. THE WHOLE POINT: groups do not see each other")
    m, f, bal = CopyEngine._split_accounts(ALL, master_id="mA")
    check("master A found", (m or {}).get("name"), "Master A")
    check("A's followers only", sorted(x["name"] for x in f),
          ["A-follower-1", "A-follower-2"])
    check("A's balance", bal, 10000.0)

    m2, f2, bal2 = CopyEngine._split_accounts(ALL, master_id="mB")
    check("master B found", (m2 or {}).get("name"), "Master B")
    check("B's follower only", [x["name"] for x in f2], ["B-follower-1"])
    check("B's balance", bal2, 20000.0)


def test_the_old_owner_scoping_would_have_crossed_the_groups():
    print("\n2. what owner-scoping alone would have done")
    _, f, _ = CopyEngine._split_accounts(ALL, owner_id=OWNER)
    # Every follower the EMAIL owns — both groups at once.
    check("owner scope sees all three", len(f), 3)
    check("including the other group's", "B-follower-1" in [x["name"] for x in f], True)
    # i.e. Master A's fill would have been copied onto B-follower-1.


def test_a_follower_with_no_master_is_not_adopted():
    print("\n3. an unassigned follower joins NO group")
    orphan = dict(A1, id="x1", name="unassigned", master_id=None)
    rows = ALL + [orphan]
    _, f, _ = CopyEngine._split_accounts(rows, master_id="mA")
    check("not picked up by A", "unassigned" in [x["name"] for x in f], False)
    _, f2, _ = CopyEngine._split_accounts(rows, master_id="mB")
    check("not picked up by B", "unassigned" in [x["name"] for x in f2], False)


def test_paused_followers_are_still_excluded():
    print("\n4. paused followers stay out, as before")
    rows = [dict(A1, status="paused") if r is A1 else r for r in ALL]
    _, f, _ = CopyEngine._split_accounts(rows, master_id="mA")
    check("only the active one", [x["name"] for x in f], ["A-follower-2"])


def test_offsets_are_per_master():
    print("\n5. each master's own TP/SL offset applies to its own copies")
    eng = engine()
    oA, sA = asyncio.run(eng._tp_sl_params(A1, OWNER, "mA"))
    oB, sB = asyncio.run(eng._tp_sl_params(B1, OWNER, "mB"))
    check("A uses 50", oA, 50)
    check("B uses 80", oB, 80)
    check("A's first follower is slot 0", sA, 0)
    check("B's first follower is slot 0 TOO", sB, 0)
    # Both are slot 0 because slots are per-GROUP. Keyed by owner they would
    # have been 0 and 2, spreading one group and stacking the other.


def test_slots_straddle_within_a_group():
    print("\n6. a master's two copies straddle it, +x and -x")
    eng = engine()
    o1, s1 = asyncio.run(eng._tp_sl_params(A1, OWNER, "mA"))
    o2, s2 = asyncio.run(eng._tp_sl_params(A2, OWNER, "mA"))
    check("slot 0", s1, 0)
    check("slot 1", s2, 1)
    p1 = CopyEngine._jitter_trigger(70000, seed="a1", offset=o1, slot=s1)
    p2 = CopyEngine._jitter_trigger(70000, seed="a2", offset=o2, slot=s2)
    check("one above", p1, 70050.0)
    check("one below", p2, 69950.0)


def test_an_unset_offset_still_defaults_to_50():
    print("\n7. a master with no offset set still gets the default")
    eng = CopyEngine.__new__(CopyEngine)
    eng._slot_cache = {}
    eng._SLOT_TTL_SEC = 10.0
    eng.db = DB([dict(MA, tp_sl_offset=None), A1])
    off, slot = asyncio.run(eng._tp_sl_params(A1, OWNER, "mA"))
    check("nothing stored", off, None)
    check("default applied", CopyEngine._jitter_trigger(70000, seed="a1",
                                                        offset=off, slot=slot), 70050.0)


def test_group_caches_do_not_collide():
    print("\n8. two groups under one owner get separate cache entries")
    eng = engine()
    asyncio.run(eng._tp_sl_params(A1, OWNER, "mA"))
    asyncio.run(eng._tp_sl_params(B1, OWNER, "mB"))
    check("two entries, not one", len(eng._slot_cache), 2)
    check("keyed by master", ("m", "mA") in eng._slot_cache and ("m", "mB") in eng._slot_cache, True)


def main():
    print("=" * 74)
    print("multi-master groups - one login, several masters, no crossover")
    print("=" * 74)
    for fn in (
        test_each_master_gets_only_its_own_followers,
        test_the_old_owner_scoping_would_have_crossed_the_groups,
        test_a_follower_with_no_master_is_not_adopted,
        test_paused_followers_are_still_excluded,
        test_offsets_are_per_master,
        test_slots_straddle_within_a_group,
        test_an_unset_offset_still_defaults_to_50,
        test_group_caches_do_not_collide,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
