# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A hands-off leg must not be reported as a position mismatch.

Run:  cd backend && python tests/test_handsoff_no_mismatch.py
No network, no Redis, no Supabase.

When the MASTER's SL/TP triggers, the engine does not force the follower out.
The follower's own stop sits tp_sl_offset away and closes that leg itself, and
the reconciler marks the leg hands-off and leaves it alone. In the window
between the two stops the master is flat and the follower still holds.

The position monitor is a separate sweep and did not know about hands-off. It
compared the follower against the master, found it holding against an expected
size of zero, and raised a Telegram alert -- about the engine's own deliberate
decision.

2026-11-24 14:30:34 IST, Dhwani -> Dhwani1:

  14:30:34  both master stops trigger (P-BTC-58400, P-BTC-59800)
            "Protective order vanished (reason stop_trigger) — leaving
             follower stops in place to trigger on their own"
  14:30:40  reconcile: "leaving Dhwani1 P-BTC-58400 -3006 alone — hands-off",
            and the same for P-BTC-59800, repeating every ten seconds
  14:31:32  Position Mismatch alert for one leg
  14:31:34  Position Mismatch alert for the other

Since the follower's stops are now placed deliberately BEYOND the master's,
the master's fires FIRST on every stop-out -- so this window opens on every
single one, and both alerts were going to fire every time.

This is the same mistake, in the same file, as the master-below-peak carve-out
directly above it: alerting on our own correct behaviour. That one was found
on 2026-08-26 and fixed for under-exposure only; hands-off is an OVER-exposure
against an expected zero, so it slipped past.
"""
import asyncio
import sys

from app.core.position_monitor import PositionMonitor

FAILURES = []
SENT = []

FOLLOWER = {"id": "f-dh1", "name": "Dhwani1", "owner_id": "u1",
            "master_id": "m-dh", "allocation_mode": "auto_ratio",
            "balance": 5235.97, "is_master": False, "status": "active"}
MASTER = {"id": "m-dh", "name": "Dhwani", "owner_id": "u1", "is_master": True,
          "balance": 5243.16, "status": "active"}
SYM = "P-BTC-58400-241126"


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Res:
    def __init__(self, d): self.data = d


class Q:
    """Just enough Supabase to answer 'what does the master hold?' and to
    swallow the status write."""
    def __init__(self, rows, log): self.rows, self.log, self.f = rows, log, {}
    def select(self, *a, **k): return self
    def eq(self, c, v): self.f[c] = v; return self
    def neq(self, *a, **k): return self
    def in_(self, c, vals): self.f[c] = ('in', tuple(vals)); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def insert(self, d, *a, **k): self.log.append(("insert", d)); return self
    def update(self, d, *a, **k): self.log.append(("update", d)); return self
    def execute(self):
        def hit(r, c, v):
            if isinstance(v, tuple) and v and v[0] == "in":
                return r.get(c) in v[1]
            return r.get(c) == v
        return Res([r for r in self.rows
                    if all(hit(r, c, v) for c, v in self.f.items())])


class DB:
    def __init__(self, master_qty, log):
        self.master_qty, self.log = master_qty, log

    def table(self, n):
        if n == "positions":
            rows = ([] if self.master_qty is None else
                    [{"account_id": "m-dh", "symbol": SYM,
                      "quantity": self.master_qty, "entry_price": 40}])
            return Q(rows, self.log)
        if n == "accounts":
            return Q([MASTER, FOLLOWER], self.log)
        return Q([], self.log)          # alerts: none open


class Redis:
    def __init__(self, hands_off=False):
        self.hands_off = hands_off
    async def get(self, key):
        if "hoff" in key or "handsoff" in key or "hands_off" in key:
            return '{"reason": "master SL/TP triggered"}' if self.hands_off else None
        return None
    async def set(self, *a, **k): return True
    async def setex(self, *a, **k): return True
    async def delete(self, *a, **k): return True
    async def expire(self, *a, **k): return True


def monitor(master_qty, hands_off):
    """A PositionMonitor with only the pieces this check touches."""
    import app.core.position_monitor as pm
    from app.core.risk_engine import RiskEngine

    async def fake_notify(*a, **k):
        SENT.append((a, k))

    pm.tg.notify_fail = fake_notify
    pm.account_cache.get = lambda db, aid: (FOLLOWER if aid == "f-dh1" else MASTER)
    pm.account_cache.master_for = lambda db, oid: MASTER

    log = []
    m = PositionMonitor.__new__(PositionMonitor)
    m.db = DB(master_qty, log)
    m.redis = Redis(hands_off)
    m.risk_engine = RiskEngine()
    m._orphan_since = {}
    m.alerts = []

    async def no_alert(*a, **k):
        m.alerts.append(("raised", a, k))
    m._create_mismatch_alert = no_alert

    resolved = []
    async def resolve(*a, **k):
        resolved.append(a)
    m._resolve_mismatch_alert = resolve
    m.resolved = resolved
    m.writes = log
    SENT.clear()
    return m


def run(master_qty, follower_qty, hands_off):
    m = monitor(master_qty, hands_off)
    out = asyncio.run(m.check_sync_and_alert(
        "f-dh1", "Dhwani1", SYM, follower_qty, 40.0, "short"))
    return out, m


# ---------------------------------------------------------------------------


def test_the_live_case_is_not_a_mismatch():
    """THE INCIDENT: master stopped out, follower still holding its own leg."""
    print("\n1. master flat, follower holding, leg hands-off")
    out, m = run(master_qty=0.0, follower_qty=3006.0, hands_off=True)
    check("reported as synced", out, "synced")
    check("no Telegram alert", len(SENT), 0)
    check("and any open alert is resolved", len(m.resolved) >= 1, True)


def test_without_the_mark_it_is_still_a_mismatch():
    """The carve-out must be narrow. The same numbers WITHOUT a hands-off mark
    are a genuine problem and must still be reported."""
    print("\n2. the same gap with no hands-off mark")
    out, m = run(master_qty=0.0, follower_qty=3006.0, hands_off=False)
    check("still out of sync", out, "out_of_sync")


def test_a_matching_leg_is_synced_either_way():
    print("\n3. a leg that matches is synced, mark or no mark")
    out, _ = run(master_qty=3000.0, follower_qty=2996.0, hands_off=False)
    check("no mark: synced", out, "synced")
    out, _ = run(master_qty=3000.0, follower_qty=2996.0, hands_off=True)
    check("with a mark: still synced", out, "synced")


def test_the_check_is_in_the_code():
    print("\n4. and the monitor really consults the mark")
    import inspect
    src = inspect.getsource(PositionMonitor.check_sync_and_alert)
    check("it reads hands-off", "ledger.is_hands_off(" in src, True)
    check("before deciding out_of_sync",
          src.index("is_hands_off(") < src.index('status = "out_of_sync"'), True)
    check("and resolves the stale alert",
          "_resolve_mismatch_alert" in src, True)


if __name__ == "__main__":
    for t in (test_the_live_case_is_not_a_mismatch,
              test_without_the_mark_it_is_still_a_mismatch,
              test_a_matching_leg_is_synced_either_way,
              test_the_check_is_in_the_code):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
