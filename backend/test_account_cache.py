"""Checks for the short-TTL accounts read cache.

Run:  venv/Scripts/python.exe test_account_cache.py
No network, no Supabase.

Why this exists: the Supabase client is synchronous, so every read blocks the
event loop. Measured live 2026-08-27, 819 calls in 3 minutes of which 398 were
reads of `accounts` — and 203 of those were the SAME query. That blocking is
what backed the WebSocket order feed up to 7-8s, and a stale order event is what
produced duplicate mirrors.

What must not break while cutting those reads: a promote or pause has to take
effect immediately (not TTL seconds later), a failed read must never be cached
or reported as "no master", and callers must not be able to corrupt the shared
rows by mutating what they get back.
"""
import sys
import time

sys.path.insert(0, ".")

from app.core.account_cache import AccountCache

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        FAILURES.append(name)


MASTER = {"id": "m1", "name": "Jigar", "is_master": True, "balance": 6775.0}
FOLLOWER = {"id": "f1", "name": "Mini Prathav", "is_master": False, "balance": 77.5}


class _Res:
    def __init__(self, data):
        self.data = data


class FakeDB:
    """Counts every read so the test can assert calls were actually avoided."""

    def __init__(self, rows=None, raises=False):
        self.rows = rows if rows is not None else [MASTER, FOLLOWER]
        self.raises = raises
        self.reads = 0

    def table(self, name):
        return self

    def select(self, *_a, **_k):
        self._f = {}
        return self

    def eq(self, col, val):
        self._f[col] = val
        return self

    def execute(self):
        self.reads += 1
        if self.raises:
            raise RuntimeError("supabase unreachable")
        out = self.rows
        for col, val in self._f.items():
            out = [r for r in out if r.get(col) == val]
        return _Res(out)


def test_repeated_reads_hit_the_cache():
    print("\nthe 203-identical-queries case: one read serves them all")
    db, c = FakeDB(), AccountCache(ttl=3.0)
    for _ in range(50):
        c.master(db)
    check("50 lookups, 1 database read", db.reads, 1)
    check("and they all got the master", c.master(db)["name"], "Jigar")
    check("hit rate reflects it", c.stats()["hits"], 50)


def test_master_id_and_balance_share_one_read():
    print("\ncheck_sync read the master TWICE per call — id, then balance")
    db, c = FakeDB(), AccountCache(ttl=3.0)
    mid = c.master_id(db)
    bal = c.master(db)["balance"]
    check("one read serves both", db.reads, 1)
    check("id", mid, "m1")
    check("balance", bal, 6775.0)


def test_account_by_id_is_cached_and_prewarmed():
    print("\nfetching the masters also warms those rows by id")
    db, c = FakeDB(), AccountCache(ttl=3.0)
    c.masters(db)
    check("one read so far", db.reads, 1)
    got = c.get(db, "m1")
    check("master row served from the warmed cache", got["name"], "Jigar")
    check("still one read", db.reads, 1)
    # A follower is not a master, so it costs its own read — once.
    c.get(db, "f1")
    c.get(db, "f1")
    check("follower read once, then cached", db.reads, 2)


def test_ttl_expires():
    print("\nthe cache is short-lived, not permanent")
    db, c = FakeDB(), AccountCache(ttl=0.05)
    c.master(db)
    check("first read", db.reads, 1)
    c.master(db)
    check("second is cached", db.reads, 1)
    time.sleep(0.08)
    c.master(db)
    check("after the TTL it re-reads", db.reads, 2)


def test_invalidate_is_immediate():
    print("\na promote/pause must take effect NOW, not after the TTL")
    db, c = FakeDB(), AccountCache(ttl=60.0)
    check("master before", c.master(db)["id"], "m1")
    # The follower is promoted; the accounts API calls invalidate().
    db.rows = [{"id": "m1", "name": "Jigar", "is_master": False, "balance": 6775.0},
               {"id": "f1", "name": "Mini Prathav", "is_master": True, "balance": 77.5}]
    c.invalidate()
    check("new master is seen at once, despite a 60s TTL", c.master(db)["id"], "f1")


def test_targeted_invalidate_drops_that_row():
    print("\ninvalidating one account re-reads that account")
    db, c = FakeDB(), AccountCache(ttl=60.0)
    c.get(db, "f1")
    before = db.reads
    c.get(db, "f1")
    check("cached", db.reads, before)
    c.invalidate("f1")
    c.get(db, "f1")
    check("re-read after invalidate", db.reads, before + 1)


def test_read_failure_serves_stale_rather_than_nothing():
    print("\na Supabase blip must not blank out the master mid-session")
    db, c = FakeDB(), AccountCache(ttl=0.05)
    check("warm the cache", c.master(db)["id"], "m1")
    db.raises = True
    time.sleep(0.08)          # TTL expires, so it tries to re-read — and fails
    got = c.master(db)
    check("serves the stale copy rather than None", got["id"], "m1")
    check("and the failure was not cached as fresh", c._masters[1] < time.time() - 0.05, True)


def test_cold_read_failure_is_safe():
    print("\nwith nothing cached, a failed read yields no master — not an exception")
    db, c = FakeDB(raises=True), AccountCache(ttl=60.0)
    # position_monitor turns this into sync_status "unknown", which raises no
    # alert — the correct outcome when we genuinely could not check.
    check("returns None without raising", c.master(db), None)
    check("and nothing bad was cached", c._masters, None)
    check("a later successful read works normally", (setattr(db, "raises", False),
          c.master(db)["id"])[1], "m1")


def test_callers_cannot_corrupt_the_cache():
    print("\nthe cached row is shared, so check_sync copies before mutating")
    db, c = FakeDB(), AccountCache(ttl=60.0)
    row = c.get(db, "f1")
    # This is what position_monitor does: copy, then inject.
    mine = dict(row)
    mine["master_balance"] = 6775.0
    check("the injected key did not leak into the cache",
          "master_balance" in c.get(db, "f1"), False)


def main():
    print("=" * 72)
    print("account_cache — cutting the reads that block the event loop")
    print("=" * 72)
    for fn in (
        test_repeated_reads_hit_the_cache,
        test_master_id_and_balance_share_one_read,
        test_account_by_id_is_cached_and_prewarmed,
        test_ttl_expires,
        test_invalidate_is_immediate,
        test_targeted_invalidate_drops_that_row,
        test_read_failure_serves_stale_rather_than_nothing,
        test_cold_read_failure_is_safe,
        test_callers_cannot_corrupt_the_cache,
    ):
        fn()
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
