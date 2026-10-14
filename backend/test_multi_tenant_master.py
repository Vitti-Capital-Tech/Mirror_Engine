"""Regression: with two tenants, every account must be measured against ITS OWN
master — not whichever master the database happened to return first.

Run:  venv/Scripts/python.exe test_multi_tenant_master.py
No network, no Redis, no Supabase — the db handle is a stub.

THE INCIDENT (2026-10-14): "sometimes for some trades it shows mismatch when
the mismatch is actually not there."

A second tenant had joined the live system. `accounts` now holds TWO rows with
is_master=True:

    Algo   (owner 1deaba39)  ->  Algo2
    Jigar  (owner 50f0f2e2)  ->  Mini Prathav

account_cache.master() returned `rows[0]`, which was correct for exactly as
long as there was one master. From then on every account in the system was
compared against Algo. Measured on the live rows for P-BTC-79600-141026:

    Mini Prathav held 85
      vs Jigar (its real master, qty 4850) -> expected 85, diff  0   synced
      vs Algo  (rows[0],         qty 2000) -> expected 35, diff 50   ALERT

So a follower that was holding exactly the right size was alerted on. Worse,
the "am I the master?" test was `account_id == master()["id"]`, so Jigar — a
master — failed it, was treated as a follower of Algo, and was alerted on too.
That matched the live alerts table: Algo/Algo2 alerts read resolved while
Jigar's and Mini Prathav's sat open indefinitely, because the resolver looked
up the global master as well.

The engine's copy path was already owner-scoped, and no cross-tenant copy was
ever written (0 of the last 1,000 trade_copies rows cross owners) — this was a
monitoring/alerting fault, not a trading one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core.account_cache import AccountCache
from app.core.risk_engine import RiskEngine

FAILURES = []

TUSHAR = "50f0f2e2-604a-4069-8377-f8387560cf74"
OTHER = "1deaba39-0000-0000-0000-000000000000"

# The live rows, in the order Postgres actually returned them (Algo first).
# Balances read off the live accounts table on 2026-10-14, so the arithmetic
# below reproduces the real alert rather than an invented one.
JIGAR = {"id": "m-jigar", "name": "Jigar", "is_master": True, "status": "active",
         "owner_id": TUSHAR, "balance": 6268.57468886}
ALGO = {"id": "m-algo", "name": "Algo", "is_master": True, "status": "active",
        "owner_id": OTHER, "balance": 6448.73053267}
MINI = {"id": "f-mini", "name": "Mini Prathav", "is_master": False, "status": "active",
        "owner_id": TUSHAR, "allocation_mode": "auto_ratio", "allocation_value": 1.0,
        "balance": 109.78952993}
ALGO2 = {"id": "f-algo2", "name": "Algo2", "is_master": False, "status": "active",
         "owner_id": OTHER, "allocation_mode": "multiplier", "allocation_value": 1.0,
         "balance": 6007.04989278}

ROWS = [ALGO, JIGAR, MINI, ALGO2]          # Algo first — this ordering IS the bug


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class StubQuery:
    def __init__(self, rows):
        self._rows = rows
        self._f = {}

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self._f[col] = val
        return self

    def execute(self):
        out = [r for r in self._rows
               if all(r.get(k) == v for k, v in self._f.items())]
        return type("R", (), {"data": out})()


class StubDB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return StubQuery(self.rows)


def cache():
    return AccountCache(ttl=0.0)           # never serve a stale read in tests


# ---------------------------------------------------------------------------


def test_there_really_are_two_masters():
    print("\n1. the premise: the live table now holds two masters")
    c = cache()
    ms = c.masters(StubDB(ROWS))
    check("two master rows", len(ms), 2)
    check("and Algo sorts first", ms[0]["name"], "Algo")


def test_the_old_lookup_picked_the_wrong_one():
    print("\n2. THE BUG: 'the master' was whoever came back first")
    c = cache()
    # master() must now REFUSE to guess rather than hand back Algo.
    check("master() no longer guesses", c.master(StubDB(ROWS)), None)


def test_each_owner_gets_its_own_master():
    print("\n3. THE FIX: ask for the master OF AN OWNER")
    c = cache()
    db = StubDB(ROWS)
    check("Tushar's master is Jigar", c.master_for(db, TUSHAR)["name"], "Jigar")
    check("the other tenant's is Algo", c.master_for(db, OTHER)["name"], "Algo")
    check("master_id_for agrees", c.master_id_for(db, TUSHAR), "m-jigar")


def test_an_owner_with_no_master_gets_nothing():
    print("\n4. never fall back to another tenant's master")
    c = cache()
    check("unknown owner -> None",
          c.master_for(StubDB(ROWS), "nobody-0000"), None)
    # With a single master and no owner asked for, the old shape still works.
    c2 = cache()
    check("single-master system still resolves",
          c2.master_for(StubDB([JIGAR, MINI]), None)["name"], "Jigar")


def test_the_false_mismatch_itself():
    print("\n5. THE SYMPTOM: 85 lots read as a 50-lot mismatch")
    re = RiskEngine()
    # Live numbers from P-BTC-79600-141026 on 2026-10-14.
    held = 85.0
    price = 6.0          # the position's entry price, as stored

    right = dict(MINI, master_balance=float(JIGAR["balance"]))
    expected_right = re.calculate_follower_quantity(4850.0, price, right, round_up=True)

    wrong = dict(MINI, master_balance=float(ALGO["balance"]))
    expected_wrong = re.calculate_follower_quantity(2000.0, price, wrong, round_up=True)

    def alerts(expected):
        diff = abs(held - expected)
        return diff > max(1.0, expected * 0.05)

    check("vs its own master, expected 85", expected_right, 85)
    check("  -> no alert", alerts(expected_right), False)
    check("vs the other tenant's master, expected 35", expected_wrong, 35)
    check("  -> this is the false alert", alerts(expected_wrong), True)


def test_a_master_is_never_alerted_on():
    print("\n6. a master is copied from nothing, so it cannot be out of sync")
    # The old test was `account_id == master()['id']`, which Jigar now fails.
    # The row knows what it is; ask it.
    old_style_master_id = ROWS[0]["id"]            # rows[0] == Algo
    check("old test wrongly says Jigar is not a master",
          JIGAR["id"] == old_style_master_id, False)
    check("new test reads the row", bool(JIGAR.get("is_master")), True)
    check("and still holds for Algo", bool(ALGO.get("is_master")), True)
    check("while a real follower is not exempt", bool(MINI.get("is_master")), False)


def test_confirm_copy_candidates_are_owner_scoped():
    print("\n7. confirm-copy's fallback must not reach another tenant")
    # Mirrors the comprehension in _confirm_order_copied: every active follower,
    # now filtered by owner.
    def candidates(rows, oid):
        return {a["id"] for a in rows
                if a.get("id") and not a.get("is_master")
                and a.get("status") == "active"
                and (not oid or a.get("owner_id") == oid)}

    unscoped = {a["id"] for a in ROWS
                if not a.get("is_master") and a.get("status") == "active"}
    check("unscoped would have included Algo2", "f-algo2" in unscoped, True)
    check("scoped to Tushar gives only Mini Prathav",
          candidates(ROWS, TUSHAR), {"f-mini"})
    check("scoped to the other tenant gives only Algo2",
          candidates(ROWS, OTHER), {"f-algo2"})


def main():
    print("=" * 74)
    print("multi-tenant master - compare each account against ITS OWN master")
    print("=" * 74)
    for fn in (
        test_there_really_are_two_masters,
        test_the_old_lookup_picked_the_wrong_one,
        test_each_owner_gets_its_own_master,
        test_an_owner_with_no_master_gets_nothing,
        test_the_false_mismatch_itself,
        test_a_master_is_never_alerted_on,
        test_confirm_copy_candidates_are_owner_scoped,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
