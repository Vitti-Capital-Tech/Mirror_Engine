"""Regression: a configurable TP/SL offset, set on the master.

Run:  venv/Scripts/python.exe test_tp_sl_offset.py
No network, no Redis, no Supabase — the db handle is a stub.

WHY. Followers must not sit on the master's exact stop price: a pack of
identical stops fires in the same instant, which is a worse fill for all of them
and an obvious footprint in the book. The engine has always offset them, by a
deterministic +/-(0..20) hashed from the follower id and the price.

That range was arbitrary and invisible. Nobody chose 20, it could not be tuned,
and it does not travel: on a contract trading at 2.8 an offset of 20 is absurd,
on one at 70,000 it is nothing.

So the desk sets one number on the MASTER, in price units, and its followers
straddle the master's trigger — first at +x, second at -x. Unset, the old hash
jitter stays, which is what every existing account has.
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
OWNER = "owner-1"
MASTER = {"id": "m1", "is_master": True, "status": "active", "created_at": "2026-01-01"}
F1 = {"id": "f1", "is_master": False, "status": "active", "created_at": "2026-01-02",
      "owner_id": OWNER}
F2 = {"id": "f2", "is_master": False, "status": "active", "created_at": "2026-01-03",
      "owner_id": OWNER}


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
        self.reads = 0

    def table(self, _n):
        self.reads += 1
        return Q(self.rows)


def engine(offset=None, followers=(F1, F2)):
    eng = CopyEngine.__new__(CopyEngine)
    eng._slot_cache = {}
    eng._SLOT_TTL_SEC = 10.0
    rows = [dict(MASTER, owner_id=OWNER, tp_sl_offset=offset)]
    rows += [dict(f) for f in followers]
    eng.db = DB(rows)
    return eng


# ---------------------------------------------------------------------------


def test_unset_keeps_the_old_hash_jitter():
    print("\n1. no offset configured -> the existing behaviour, unchanged")
    eng = engine(offset=None)
    off, slot = asyncio.run(eng._tp_sl_params(F1, OWNER))
    check("no offset returned", off, None)
    a = CopyEngine._jitter_trigger(70000, seed="f1", offset=off, slot=slot)
    b = CopyEngine._jitter_trigger(70000, seed="f1")
    check("identical to the old call", a, b)
    check("and it did move off the master's price", a != 70000.0, True)


def test_two_followers_straddle_the_master():
    print("\n2. THE ASK: follower 1 at +x, follower 2 at -x")
    eng = engine(offset=5)
    o1, s1 = asyncio.run(eng._tp_sl_params(F1, OWNER))
    o2, s2 = asyncio.run(eng._tp_sl_params(F2, OWNER))
    check("offset read from the master", o1, 5)
    check("follower 1 is slot 0", s1, 0)
    check("follower 2 is slot 1", s2, 1)
    p1 = CopyEngine._jitter_trigger(70000, seed="f1", offset=o1, slot=s1)
    p2 = CopyEngine._jitter_trigger(70000, seed="f2", offset=o2, slot=s2)
    check("follower 1 sits ABOVE at +5", p1, 70005.0)
    check("follower 2 sits BELOW at -5", p2, 69995.0)
    check("they straddle the master", (p1 > 70000) and (p2 < 70000), True)


def test_it_is_a_real_price_offset_not_a_percentage():
    print("\n3. the offset is in price units, so it travels across contracts")
    eng = engine(offset=0.2)
    off, slot = asyncio.run(eng._tp_sl_params(F1, OWNER))
    # The whole complaint about 0..20: absurd on a contract trading at 2.8.
    check("works on a 2.8 contract",
          CopyEngine._jitter_trigger(2.8, seed="f1", offset=off, slot=slot), 3.0)


def test_the_same_price_gives_the_same_answer():
    print("\n4. deterministic — a pair sharing a trigger must not come apart")
    eng = engine(offset=5)
    off, slot = asyncio.run(eng._tp_sl_params(F1, OWNER))
    a = CopyEngine._jitter_trigger(70000, seed="f1", offset=off, slot=slot)
    b = CopyEngine._jitter_trigger(70000, seed="f1", offset=off, slot=slot)
    check("same in, same out", a, b)


def test_the_lookup_is_cached():
    print("\n5. one read per owner per TTL, not one per order")
    eng = engine(offset=5)
    for _ in range(20):
        asyncio.run(eng._tp_sl_params(F1, OWNER))
    check("20 orders -> 1 read", eng.db.reads, 1)


def test_an_unknown_follower_does_not_break():
    print("\n6. a follower not in the list still gets a valid side")
    eng = engine(offset=5)
    off, slot = asyncio.run(eng._tp_sl_params({"id": "ghost"}, OWNER))
    check("defaults to the + side", slot, 0)
    check("offset still applies", off, 5)


def test_a_db_failure_falls_back_to_the_hash():
    print("\n7. an unreadable accounts table must not stop an order")
    eng = engine(offset=5)

    class Broken:
        def table(self, *a):
            raise RuntimeError("supabase down")

    eng.db = Broken()
    off, slot = asyncio.run(eng._tp_sl_params(F1, OWNER))
    check("no offset -> hash jitter", off, None)
    check("and a usable price comes out",
          CopyEngine._jitter_trigger(70000, seed="f1", offset=off, slot=slot) is not None, True)


def test_zero_and_junk_are_ignored():
    print("\n8. a nonsense offset falls back rather than stacking on the master")
    # 0 would put every follower on the master's exact price — the one outcome
    # this feature exists to prevent. The DB constraint rejects it; belt and
    # braces here too.
    check("0 -> hash jitter, not the raw price",
          CopyEngine._jitter_trigger(70000, seed="f1", offset=0, slot=0) != 70000.0, True)
    check("junk -> hash jitter",
          CopyEngine._jitter_trigger(70000, seed="f1", offset="abc", slot=0) != 70000.0, True)
    check("None price stays None",
          CopyEngine._jitter_trigger(None, seed="f1", offset=5, slot=0), None)


def main():
    print("=" * 74)
    print("tp/sl offset - the desk decides how far apart its copies sit")
    print("=" * 74)
    for fn in (
        test_unset_keeps_the_old_hash_jitter,
        test_two_followers_straddle_the_master,
        test_it_is_a_real_price_offset_not_a_percentage,
        test_the_same_price_gives_the_same_answer,
        test_the_lookup_is_cached,
        test_an_unknown_follower_does_not_break,
        test_a_db_failure_falls_back_to_the_hash,
        test_zero_and_junk_are_ignored,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
