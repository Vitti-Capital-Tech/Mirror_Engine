"""Regression: transferring an account must not stop the copying.

Run:  venv/Scripts/python.exe test_owner_handover.py
No network, no Redis, no Supabase — the db handle is a stub.

THE INCIDENT (2026-11-11). Both live accounts were transferred from
admin@vitti.capital to shahkajal991@gmail.com while the engine was running.
TradeListener.start() captures the master row ONCE:

    self.master_account = master_account

and every event stamped its owner_id straight from that snapshot. copy_engine
scopes the follower lookup by the event's owner_id, so from the moment the
accounts changed hands the listener kept stamping the PREVIOUS owner and the
engine looked for followers belonging to someone who no longer owned any.

What that cost, from the live log:

    02:50:36  transfer lands
    ...       "master balance read as 0 — sizing on the last known good value"
              x391, every sizing call for the next 25 minutes
    03:11:54  master closes C-BTC-74000 (sell 400)   -> "No active follower
    03:11:58  master closes C-BTC-75000 (buy 2650)       accounts found."
    03:16:01  an unrelated deploy restarts the process; the snapshot rebuilds
    03:16:23  reconcile: closed JD algo3 C-BTC-75000 -2390 (master +0)
    03:16:24  reconcile: closed JD algo3 C-BTC-74000 +361  (master +0)

Neither close was mirrored. The follower held positions the master had already
exited for ~4.5 minutes, and the only reason it ever recovered is that a deploy
happened to restart the process — it self-healed by accident, not by design.
"No active follower accounts found" appears exactly twice in the whole log, both
in that minute.

The owner is now read from the account cache by account ID (which does not
change), so a transfer takes effect within the cache's 3s TTL and needs no
restart.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import app.core.trade_listener as tl
from app.core.account_cache import AccountCache
from app.core.copy_engine import CopyEngine

FAILURES = []

OLD_OWNER = "5f41f9cf-admin"
NEW_OWNER = "867a7192-kajal"

MASTER = {"id": "m-jd4", "name": "JD algo4", "is_master": True,
          "status": "active", "owner_id": OLD_OWNER, "balance": 6164.93}
FOLLOWER = {"id": "f-jd3", "name": "JD algo3", "is_master": False,
            "status": "active", "owner_id": OLD_OWNER, "balance": 5558.67,
            "allocation_mode": "auto_ratio", "allocation_value": 1.0}


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Query:
    def __init__(self, rows):
        self.rows, self.f = rows, {}

    def select(self, *a, **kw):
        return self

    def eq(self, c, v):
        self.f[c] = v
        return self

    def execute(self):
        out = [r for r in self.rows
               if all(str(r.get(k)) == str(v) for k, v in self.f.items())]
        return type("R", (), {"data": out})()


class DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _n):
        return Query(self.rows)


def listener_for(rows):
    """A listener holding the ORIGINAL snapshot, as start() left it."""
    lis = tl.TradeListener.__new__(tl.TradeListener)
    lis.master_account = dict(MASTER)          # frozen at start: OLD_OWNER
    tl.db = DB(rows)
    tl.account_cache = AccountCache(ttl=0.0)   # never serve a stale read here
    return lis


# ---------------------------------------------------------------------------


def test_normal_case_unchanged():
    print("\n1. nothing transferred — the owner is what it always was")
    rows = [dict(MASTER), dict(FOLLOWER)]
    check("owner stamped", listener_for(rows)._owner_id(), OLD_OWNER)


def test_the_incident():
    print("\n2. THE INCIDENT: accounts change hands under a running listener")
    # Both rows move to Kajal. The listener's snapshot still says admin.
    rows = [dict(MASTER, owner_id=NEW_OWNER), dict(FOLLOWER, owner_id=NEW_OWNER)]
    lis = listener_for(rows)
    check("snapshot is stale", lis.master_account["owner_id"], OLD_OWNER)
    check("but the stamp follows the account", lis._owner_id(), NEW_OWNER)


def test_what_the_stale_stamp_cost():
    print("\n3. why that mattered: the follower lookup is owner-scoped")
    rows = [dict(MASTER, owner_id=NEW_OWNER), dict(FOLLOWER, owner_id=NEW_OWNER)]

    # What the OLD code stamped — the snapshot's owner.
    m, f, bal = CopyEngine._split_accounts(rows, OLD_OWNER)
    check("stale owner finds no master", m, None)
    check("stale owner finds NO FOLLOWERS", len(f), 0)
    check("and so sizes on a balance of 0", bal, 0.0)
    # ^ exactly the live log: "master balance read as 0" and
    #   "No active follower accounts found."

    # What the fixed code stamps.
    m2, f2, bal2 = CopyEngine._split_accounts(rows, listener_for(rows)._owner_id())
    check("live owner finds the master", (m2 or {}).get("name"), "JD algo4")
    check("live owner finds the follower", [x["name"] for x in f2], ["JD algo3"])
    check("and a real balance", bal2, 6164.93)


def test_a_db_failure_falls_back_not_crashes():
    print("\n4. an unreadable accounts table must not stop events being stamped")
    class Broken:
        def table(self, *a):
            raise RuntimeError("supabase down")
    lis = listener_for([dict(MASTER)])
    tl.db = Broken()
    tl.account_cache = AccountCache(ttl=0.0)
    check("falls back to the snapshot", lis._owner_id(), OLD_OWNER)


def test_a_missing_row_falls_back():
    print("\n5. a master row that cannot be found falls back too")
    lis = listener_for([])                     # no rows at all
    check("falls back to the snapshot", lis._owner_id(), OLD_OWNER)


def test_no_master_account_at_all():
    print("\n6. no snapshot either — None, never a crash")
    lis = tl.TradeListener.__new__(tl.TradeListener)
    lis.master_account = None
    tl.db = DB([])
    tl.account_cache = AccountCache(ttl=0.0)
    check("returns None", lis._owner_id(), None)


def main():
    print("=" * 74)
    print("owner handover - transferring an account must not stop the copying")
    print("=" * 74)
    for fn in (
        test_normal_case_unchanged,
        test_the_incident,
        test_what_the_stale_stamp_cost,
        test_a_db_failure_falls_back_not_crashes,
        test_a_missing_row_falls_back,
        test_no_master_account_at_all,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
