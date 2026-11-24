# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Regression: "Make this the master" inside a multi-master group.

Run:  venv/Scripts/python.exe test_promote_master.py
No network, no Redis, no Supabase — the db handle is a stub.

WHAT IS BEING PROMISED. The menu item says: "Make <X> the master? The current
master becomes a copy." Inside one group that should be a clean swap — the
follower becomes the master, the old master becomes one of its copies, the
other copies follow the new master — and NOTHING outside that group moves.

WHY IT IS DANGEROUS. promote_account was written when an owner could only have
ONE master, so it found "the current master" by owner alone and never touched
master_id, because master_id did not exist. Both of those are now wrong:

  * an owner has up to five masters, so "the master for this owner" picks an
    arbitrary one — possibly the master of a group the click had nothing to do
    with, whose listener then gets torn down, and
  * master_id is what defines a group now. Leaving it alone leaves the new
    master pointing at a follower, the old master pointing at nothing, and the
    sibling copies pointing at an account that is no longer a master.

And the swap runs as separate writes with no transaction, in an order that
fails UNSAFE: the old master is demoted first. The db stub below enforces the
real CHECK constraint from 006_multi_master_groups.sql, which is what turns
"the group is wrong afterwards" into "the group has no master at all".
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

import app.api.accounts as accounts_api

FAILURES = []
OWNER = "one-email"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def fresh_rows():
    """One owner, two independent groups. Group A is the one being changed."""
    return [
        {"id": "mA", "name": "Master A", "is_master": True, "status": "active",
         "owner_id": OWNER, "master_id": None, "api_key": "k", "api_secret": "s"},
        {"id": "a1", "name": "A-copy-1", "is_master": False, "status": "active",
         "owner_id": OWNER, "master_id": "mA", "api_key": "k", "api_secret": "s"},
        {"id": "a2", "name": "A-copy-2", "is_master": False, "status": "active",
         "owner_id": OWNER, "master_id": "mA", "api_key": "k", "api_secret": "s"},
        {"id": "mB", "name": "Master B", "is_master": True, "status": "active",
         "owner_id": OWNER, "master_id": None, "api_key": "k", "api_secret": "s"},
        {"id": "b1", "name": "B-copy-1", "is_master": False, "status": "active",
         "owner_id": OWNER, "master_id": "mB", "api_key": "k", "api_secret": "s"},
    ]


class ConstraintViolation(Exception):
    """What Postgres raises, so the test fails where production would fail."""


def enforce(row):
    """accounts_master_id_check, verbatim from 006_multi_master_groups.sql:

        (is_master = true  and master_id is null)
        or
        (is_master = false and (master_id is null or master_id <> id))
    """
    ok = ((row.get("is_master") is True and row.get("master_id") is None)
          or (row.get("is_master") is False
              and (row.get("master_id") is None or row["master_id"] != row["id"])))
    if not ok:
        raise ConstraintViolation(
            'new row for relation "accounts" violates check constraint '
            '"accounts_master_id_check" — id=%r is_master=%r master_id=%r'
            % (row.get("id"), row.get("is_master"), row.get("master_id")))


class Q:
    def __init__(self, rows):
        self.rows, self.f, self.patch = rows, {}, None

    def select(self, *a, **kw):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, c, v):
        self.f[c] = v
        return self

    def order(self, *a, **kw):
        return self

    def _match(self):
        return [r for r in self.rows
                if all(str(r.get(k)) == str(v) for k, v in self.f.items())]

    def execute(self):
        hit = self._match()
        if self.patch is not None:
            for r in hit:
                after = {**r, **self.patch}
                enforce(after)          # the db gets a say, as it does live
                r.update(self.patch)
        return type("R", (), {"data": [dict(r) for r in hit]})()


class DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _n):
        return Q(self.rows)


class _Noop:
    """The WS plumbing. Records what it was asked to tear down."""
    def __init__(self):
        self.stopped = []
        self.disconnected = []

    async def stop_master(self, mid):
        self.stopped.append(mid)

    async def disconnect_account(self, aid):
        self.disconnected.append(aid)


def run_promote(rows, target_id):
    """Call the real endpoint with everything external stubbed out."""
    db = DB(rows)
    plumbing = _Noop()

    accounts_api.db = db
    accounts_api.listener_manager = plumbing
    accounts_api.connection_manager = plumbing
    accounts_api.account_cache = type("C", (), {"invalidate": staticmethod(lambda *a: None)})()
    accounts_api.owned_account_or_404 = lambda i, u: next(r for r in rows if r["id"] == i)

    async def _ws(_acc):
        return None
    accounts_api.start_account_ws = _ws

    err = None
    try:
        asyncio.run(accounts_api.promote_account(target_id, user=None))
    except Exception as e:  # noqa: BLE001 — the point is to see what escapes
        err = e
    return rows, plumbing, err


def by_id(rows, i):
    return next(r for r in rows if r["id"] == i)


# ---------------------------------------------------------------------------


def test_promoting_a_copy_swaps_roles_within_its_own_group():
    print("\n1. the swap the menu promises")
    rows, _, err = run_promote(fresh_rows(), "a2")
    check("no error", err if err is None else "%s: %s" % (type(err).__name__, err), None)
    check("the copy is now the master", by_id(rows, "a2")["is_master"], True)
    check("...and points at no master", by_id(rows, "a2")["master_id"], None)
    check("the old master is now a copy", by_id(rows, "mA")["is_master"], False)
    check("...of the new master", by_id(rows, "mA")["master_id"], "a2")


def test_the_other_copy_in_the_group_follows_the_new_master():
    print("\n2. the sibling copy is not orphaned")
    rows, _, _ = run_promote(fresh_rows(), "a2")
    check("a1 now copies a2", by_id(rows, "a1")["master_id"], "a2")
    check("a1 is still a copy", by_id(rows, "a1")["is_master"], False)


def test_the_other_group_is_untouched():
    print("\n3. THE DANGEROUS ONE: group B must not move")
    rows, plumbing, _ = run_promote(fresh_rows(), "a2")
    check("Master B is still the master", by_id(rows, "mB")["is_master"], True)
    check("B's copy still follows B", by_id(rows, "b1")["master_id"], "mB")
    check("B's listener was never stopped", "mB" in plumbing.stopped, False)


def test_a_group_is_never_left_without_a_master():
    print("\n4. and it must never fail halfway")
    rows, _, _ = run_promote(fresh_rows(), "a2")
    masters = [r["id"] for r in rows
               if r["is_master"] and r["master_id"] is None and r["owner_id"] == OWNER]
    check("still exactly two masters for this owner", sorted(masters), ["a2", "mB"])


def test_it_does_not_depend_on_what_order_the_db_returns_rows_in():
    print("\n6. the arbitrary-master bug, pinned down")
    # The old lookup was:
    #     select * from accounts where is_master and owner_id = <owner>  -> [0]
    # with no ORDER BY, so "the current master" was whichever row Postgres felt
    # like returning first. Here is that same pick against a perfectly legal
    # ordering — it lands on group B, which the click never mentioned:
    rows = fresh_rows()
    rows = [by_id(rows, i) for i in ("mB", "b1", "mA", "a1", "a2")]
    would_have_picked = [r for r in rows if r["is_master"] and r["owner_id"] == OWNER][0]
    check("the old owner-wide lookup grabs group B", would_have_picked["id"], "mB")

    # The group is now read off the target's own master_id, so row order
    # cannot reach it. Same promotion, rows the other way round:
    rows, plumbing, err = run_promote(rows, "a2")
    check("no error", err, None)
    check("a2 took over group A", by_id(rows, "mA")["master_id"], "a2")
    check("Master B untouched", by_id(rows, "mB")["is_master"], True)
    check("B's listener never stopped", plumbing.stopped, ["mA"])


def test_a_copy_that_follows_nothing_just_becomes_a_master():
    print("\n7. an unassigned copy has no one to demote")
    rows = fresh_rows()
    by_id(rows, "a1")["master_id"] = None
    _, plumbing, err = run_promote(rows, "a1")
    check("no error", err, None)
    check("it is a master now", by_id(rows, "a1")["is_master"], True)
    check("nobody was demoted", plumbing.stopped, [])
    check("group A's master is still Master A", by_id(rows, "mA")["is_master"], True)


def test_promoting_an_account_that_is_already_master_is_refused():
    print("\n5. promoting a master is still a 400, not a swap")
    rows = fresh_rows()
    _, _, err = run_promote(rows, "mA")
    check("rejected", getattr(err, "status_code", None), 400)
    check("nothing moved", by_id(rows, "mA")["is_master"], True)


if __name__ == "__main__":
    for t in (test_promoting_a_copy_swaps_roles_within_its_own_group,
              test_the_other_copy_in_the_group_follows_the_new_master,
              test_the_other_group_is_untouched,
              test_a_group_is_never_left_without_a_master,
              test_it_does_not_depend_on_what_order_the_db_returns_rows_in,
              test_a_copy_that_follows_nothing_just_becomes_a_master,
              test_promoting_an_account_that_is_already_master_is_refused):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
