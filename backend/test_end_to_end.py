"""End to end: a master's whole session, through the real engine, against a
fake exchange that actually keeps state.

Run:  venv/Scripts/python.exe test_end_to_end.py
No network, no Redis, no Supabase.

WHY THIS FILE EXISTS. Every other test here pins one decision or one bug. None
of them runs a session: master opens, adds, edits, cancels, part-exits, gets
stopped out, while the reconciler sweeps alongside. That is where this month's
faults actually came from -- not from a single wrong number, but from two code
paths meeting on the same leg, or from state surviving from one episode into
the next.

The exchange here is not a mock that returns canned answers. It holds positions
and an order book per account, fills market orders, rests limits, applies
reduce-only semantics (including Delta's habit of accepting a reduce-only order
and then killing it when there is nothing left to reduce -- the behaviour that
produced five identical 3200-lot orders on 2026-11-19), and lets an order be
edited or cancelled. The engine is the real one.

THE SESSION, in order:

   1  master opens 800            follower takes its ratio share
   2  master rests a limit        follower mirrors it, once
   3  the same event repeats      no duplicate (the reconcile re-push)
   4  master edits the limit      the follower's order is edited, not re-placed
   5  master cancels it           the follower's goes too
   6  a stale re-mirror arrives   refused; the dead order does not come back
   7  master adds to the position follower follows at ratio
   8  live copy misses a leg      the reconciler opens it
   9  follower sits over target   the reconciler trims to the ratio
  10  master is unwinding         the reconciler holds instead of trimming
  11  master's stop triggers      nothing is forced; the follower's own stop runs
  12  follower already covered    no second close is sent
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

FAILURES = []
SYM = "C-BTC-65000-201126"
MASTER_BAL = 8509.61
FOLLOWER_BAL = 7595.09
RATIO = FOLLOWER_BAL / MASTER_BAL          # 0.8925


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def share(master_lots):
    """The follower's proportional share, as the engine rounds it."""
    import math
    return int(math.ceil(abs(master_lots) * RATIO))


# ----------------------------------------------------------------- exchange
class Exchange:
    """One book and one position per account. Stateful on purpose."""

    def __init__(self):
        self.pos = {}        # acct -> {sym: signed size}
        self.book = {}       # acct -> {oid: order}
        self.seq = 1000
        self.killed = []     # reduce-only orders accepted and then dropped

    def _next(self):
        self.seq += 1
        return str(self.seq)

    def position(self, acct, sym):
        return self.pos.get(acct, {}).get(sym, 0.0)

    def set_position(self, acct, sym, size):
        self.pos.setdefault(acct, {})[sym] = float(size)

    def resting(self, acct, sym=None):
        return [o for o in self.book.get(acct, {}).values()
                if sym is None or o["product_symbol"] == sym]

    def _reducible(self, acct, sym, side):
        """Lots of this position not already covered by a resting reducer."""
        held = self.position(acct, sym)
        if not held:
            return 0.0
        reduces = (side == "buy" and held < 0) or (side == "sell" and held > 0)
        if not reduces:
            return 0.0
        covered = sum(
            abs(float(o["size"])) for o in self.resting(acct, sym)
            if o.get("reduce_only")
            and ((o["side"] == "buy" and held < 0) or (o["side"] == "sell" and held > 0))
        )
        return max(0.0, abs(held) - covered)

    def place(self, acct, **kw):
        sym = kw["symbol"]
        side = kw["side"]
        size = abs(float(kw.get("size") or 0))
        oid = self._next()
        if kw.get("reduce_only"):
            room = self._reducible(acct, sym, side)
            if room <= 0:
                # Delta accepts it and then drops it: nothing left to reduce.
                # This is what made the same rung mirror five times.
                self.killed.append(oid)
                return {"id": oid}
            size = min(size, room)
        # A stop/TP RESTS until its trigger, whatever its order_type says: a
        # stop-market is `market_order` plus a stop_price and it must not fill
        # on arrival. Getting this wrong made the first draft of this file
        # "lose" every stop it placed.
        is_stop = kw.get("stop_price") is not None or kw.get("stop_order_type")
        if not is_stop and (kw.get("order_type") or "limit_order") == "market_order":
            cur = self.position(acct, sym)
            delta = size if side == "buy" else -size
            self.set_position(acct, sym, cur + delta)
            return {"id": oid}
        self.book.setdefault(acct, {})[oid] = {
            "id": oid, "product_symbol": sym, "side": side, "size": size,
            "unfilled_size": size, "state": "open",
            "order_type": kw.get("order_type") or "limit_order",
            "limit_price": kw.get("limit_price"),
            "stop_price": kw.get("stop_price"),
            "stop_order_type": kw.get("stop_order_type"),
            "reduce_only": bool(kw.get("reduce_only")),
            "product_id": kw.get("product_id"),
        }
        return {"id": oid}

    def fill(self, acct, oid):
        o = self.book.get(acct, {}).pop(str(oid), None)
        if not o:
            return
        cur = self.position(acct, o["product_symbol"])
        delta = float(o["size"]) if o["side"] == "buy" else -float(o["size"])
        self.set_position(acct, o["product_symbol"], cur + delta)

    def cancel(self, acct, oid):
        return self.book.get(acct, {}).pop(str(oid), None) is not None


class Client:
    def __init__(self, ex, acct):
        self.ex, self.acct = ex, acct
        self.placed, self.cancelled, self.edited = [], [], []

    async def get_positions(self):
        return [{"product_symbol": s, "size": v, "mark_price": 40, "entry_price": 40}
                for s, v in self.ex.pos.get(self.acct, {}).items() if v]

    async def get_open_orders(self, state=None):
        # Delta splits the book: plain limits sit in 'open', stops and TPs in
        # 'pending'. The engine reads both and must not see either twice.
        book = list(self.ex.book.get(self.acct, {}).values())
        if state == "open":
            return [o for o in book if not o.get("stop_order_type")]
        if state == "pending":
            return [o for o in book if o.get("stop_order_type")]
        return []

    async def get_order(self, oid):
        o = self.ex.book.get(self.acct, {}).get(str(oid))
        return o or {"id": str(oid), "state": "cancelled", "unfilled_size": 0, "size": 0}

    async def place_order(self, **kw):
        self.placed.append(kw)
        return self.ex.place(self.acct, **kw)

    async def cancel_order(self, oid, product_id=None):
        self.cancelled.append(str(oid))
        return self.ex.cancel(self.acct, oid)

    async def edit_order(self, oid, product_id=None, limit_price=None, size=None):
        self.edited.append((str(oid), limit_price, size))
        o = self.ex.book.get(self.acct, {}).get(str(oid))
        if not o:
            raise RuntimeError("no such order")
        if limit_price is not None:
            o["limit_price"] = limit_price
        if size is not None:
            o["size"] = o["unfilled_size"] = abs(float(size))
        return {"result": {"id": str(oid)}}

    async def close(self):
        pass


# -------------------------------------------------------------- other fakes
class Redis:
    def __init__(self):
        self.h, self.kv = {}, {}

    async def hset(self, k, field=None, value=None, mapping=None):
        d = self.h.setdefault(k, {})
        d.update({str(a): str(b) for a, b in (mapping or {}).items()}) if mapping \
            else d.__setitem__(str(field), str(value))

    async def hget(self, k, f): return self.h.get(k, {}).get(str(f))
    async def hgetall(self, k): return dict(self.h.get(k, {}))
    async def hdel(self, k, f): self.h.get(k, {}).pop(str(f), None)
    async def get(self, k): return self.kv.get(k)
    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = str(v)
        return True
    async def setex(self, k, ttl, v): self.kv[k] = str(v)
    async def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, 0)) + 1)
        return int(self.kv[k])
    async def delete(self, k): self.h.pop(k, None); self.kv.pop(k, None)
    async def expire(self, k, t): pass
    async def zadd(self, k, mapping): pass
    async def zremrangebyscore(self, k, a, b): pass
    async def zrevrange(self, k, a, b): return []
    async def sadd(self, k, *v): pass
    async def srem(self, k, *v): pass
    async def smembers(self, k): return set()
    def pipeline(self, transaction=False): return Pipe(self)


class Pipe:
    def __init__(self, r): self.r, self.ops = r, []
    def __getattr__(self, n):
        def q(*a, **k):
            self.ops.append((n, a, k)); return self
        return q
    async def execute(self):
        out = []
        for n, a, k in self.ops:
            out.append(await getattr(self.r, n)(*a, **k))
        self.ops.clear()
        return out


MASTER = {"id": "m-jd", "name": "JD algo4", "is_master": True, "status": "active",
          "api_key": "k", "api_secret": "s", "environment": "demo",
          "owner_id": "u1", "balance": MASTER_BAL}
FOLLOWER = {"id": "f-algo3", "name": "JD algo3", "is_master": False,
            "status": "active", "api_key": "k2", "api_secret": "s2",
            "environment": "demo", "owner_id": "u1", "master_id": "m-jd",
            "allocation_mode": "auto_ratio", "balance": FOLLOWER_BAL,
            "tp_sl_offset": 50}


class Res:
    def __init__(self, d): self.data = d


class Q:
    def __init__(self, rows): self.rows, self.f = rows, {}
    def select(self, *a, **k): return self
    def eq(self, c, v): self.f[c] = v; return self
    def neq(self, c, v): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def update(self, *a, **k): return self
    def insert(self, *a, **k): return self
    def upsert(self, *a, **k): return self
    def execute(self):
        return Res([r for r in self.rows
                    if all(r.get(c) == v for c, v in self.f.items())])


class DB:
    def table(self, n):
        return Q([MASTER, FOLLOWER] if n == "accounts" else [])


class Sock:
    async def emit_trade_copy(self, *a, **k): pass
    async def emit_account_update(self, *a, **k): pass


class ConnMgr:
    def __init__(self, clients): self.clients = clients
    def get_client(self, acct_id): return self.clients.get(acct_id)
    async def connect_account(self, acc): return self.clients.get(acc.get("id"))


def build():
    """A real CopyEngine wired to the fake exchange."""
    from app.core.copy_engine import CopyEngine
    from app.services import telegram_client as tg

    async def quiet(*a, **k): return False
    tg.send_message = quiet

    ex = Exchange()
    mc, fc = Client(ex, "m-jd"), Client(ex, "f-algo3")
    eng = CopyEngine(DB(), Redis(), Sock(), ConnMgr({"m-jd": mc, "f-algo3": fc}))
    eng._get_master_client = lambda row: mc

    async def fresh(_row):
        # Older than TRIM_SETTLE_SEC (10s), so the size counts as settled and
        # the sweep is allowed to correct; younger than FRESH_ENTRY_SEC (180s),
        # so a missed leg is still worth recovering. Inside 10s the reconciler
        # deliberately does nothing at all -- the position is still moving --
        # which is what the first draft of this file got wrong.
        return {SYM: time.time() - 30}
    eng._master_recent_fill_ts = fresh

    async def classify(_r, _s): return "unknown"
    eng._classify_master_exit = classify
    return eng, ex, mc, fc


def recon(eng, ex):
    """One reconcile pass, built from the exchange's real master book."""
    return {"owner_id": "u1", "master_account_id": "m-jd",
            "positions": [{"symbol": s, "size": v, "mark": 40, "entry": 40}
                          for s, v in ex.pos.get("m-jd", {}).items() if v]}


def place_event(oid, side, size, price=40, **extra):
    e = {"action": "place", "master_order_id": str(oid), "symbol": SYM,
         "side": side, "size": float(size), "state": "open",
         "order_type": "limit_order", "limit_price": price,
         "owner_id": "u1", "master_account_id": "m-jd", "product_id": 1}
    e.update(extra)
    return e


# ---------------------------------------------------------------------------


def test_the_session():
    print("\nA FULL SESSION, step by step")
    eng, ex, mc, fc = build()

    async def run():
        out = {}

        # 1 ---- master opens 800, follower is flat. The sweep recovers it.
        ex.set_position("m-jd", SYM, 800)
        await eng._reconcile_positions(recon(eng, ex))     # first sighting
        out["after_one_pass"] = ex.position("f-algo3", SYM)
        await eng._reconcile_positions(recon(eng, ex))     # confirmed
        out["opened"] = ex.position("f-algo3", SYM)

        # 2 ---- master rests a limit; the follower mirrors it once
        ex.place("m-jd", symbol=SYM, side="sell", size=400,
                 order_type="limit_order", limit_price=45)
        moid = ex.resting("m-jd", SYM)[0]["id"]
        await eng.process_order_event(place_event(moid, "sell", 400, 45))
        out["mirrored"] = len(ex.resting("f-algo3", SYM))
        out["mirror_size"] = int(ex.resting("f-algo3", SYM)[0]["size"]) if \
            ex.resting("f-algo3", SYM) else 0

        # 3 ---- the same order arrives again (the 30s sweep re-pushes it)
        await eng.process_order_event(place_event(moid, "sell", 400, 45,
                                                  source="reconcile"))
        out["after_repeat"] = len(ex.resting("f-algo3", SYM))

        # 4 ---- master edits the price; the follower's order is edited
        await eng.process_order_event(place_event(moid, "sell", 400, 47,
                                                  is_update=True))
        out["edits"] = len(fc.edited)
        out["after_edit"] = len(ex.resting("f-algo3", SYM))

        # 5 ---- master cancels; the follower's goes too
        ex.cancel("m-jd", moid)
        await eng.process_order_event(
            {"action": "cancel", "master_order_id": str(moid), "symbol": SYM,
             "owner_id": "u1", "master_account_id": "m-jd", "product_id": 1})
        out["after_cancel"] = len(ex.resting("f-algo3", SYM))

        # 6 ---- a stale re-mirror of the dead order must not resurrect it
        await eng.process_order_event(place_event(moid, "sell", 400, 47,
                                                  source="reconcile"))
        out["after_stale_remirror"] = len(ex.resting("f-algo3", SYM))

        # 7 ---- follower drifts under target; the sweep tops it up
        #
        # The engine will not top up a leg it has only just opened (the
        # RECON_OPEN_DEBOUNCE), which is right: in a real session minutes pass
        # between step 1 and a drift, and this file compresses the whole thing
        # into milliseconds. Clear the clock rather than sleep.
        eng._recon_open_ts.clear()
        ex.set_position("f-algo3", SYM, 500)
        await eng._reconcile_positions(recon(eng, ex))
        await eng._reconcile_positions(recon(eng, ex))
        out["topped_up"] = ex.position("f-algo3", SYM)

        # 8 ---- follower over target; the sweep trims it
        ex.set_position("f-algo3", SYM, 800)
        await eng._reconcile_positions(recon(eng, ex))
        await eng._reconcile_positions(recon(eng, ex))
        out["trimmed"] = ex.position("f-algo3", SYM)
        return out

    r = asyncio.run(run())

    print("\n1. the master opens, the follower follows at its ratio")
    check("nothing on the first sighting", r["after_one_pass"], 0.0)
    check("opened at the ratio share", r["opened"], float(share(800)))

    print("\n2. a resting limit is mirrored, once")
    check("one order on the follower", r["mirrored"], 1)
    check("sized to the ratio", r["mirror_size"], share(400))

    print("\n3. the same order again does not duplicate")
    check("still one", r["after_repeat"], 1)

    print("\n4. an edit edits, it does not re-place")
    check("the order was edited", r["edits"] >= 1, True)
    check("and there is still only one", r["after_edit"], 1)

    print("\n5. a cancel reaches the follower")
    check("the follower's order is gone", r["after_cancel"], 0)

    print("\n6. a dead order does not come back")
    check("the stale re-mirror is refused", r["after_stale_remirror"], 0)

    print("\n7. under target — topped up to the ratio")
    check("back to the share", r["topped_up"], float(share(800)))

    print("\n8. over target — trimmed to the ratio")
    check("back to the share", r["trimmed"], float(share(800)))


def test_the_master_unwinding_is_left_alone():
    """Desk call, Prathav 2026-09-25: while the master is working an order that
    reduces his own position, the follower is not trimmed into it."""
    print("\n9. the master is unwinding — hold, do not trim")
    eng, ex, mc, fc = build()

    async def run():
        ex.set_position("m-jd", SYM, -3200)
        ex.set_position("f-algo3", SYM, -3200)          # 1:1, over its target
        ex.place("m-jd", symbol=SYM, side="buy", size=3200,
                 order_type="limit_order", limit_price=30, reduce_only=True)
        for _ in range(3):
            await eng._reconcile_positions(recon(eng, ex))
        return ex.position("f-algo3", SYM), len(fc.placed)

    held, placed = asyncio.run(run())
    check("the follower is untouched", held, -3200.0)
    check("nothing was sent", placed, 0)


def test_a_covered_leg_gets_no_second_close():
    """The 2026-11-19 loop: a position already fully covered by resting orders
    got covered again, the exchange dropped the surplus, and the next sweep
    placed it once more."""
    print("\n10. already covered — no second close")
    eng, ex, mc, fc = build()

    async def run():
        ex.set_position("m-jd", SYM, -3200)
        ex.set_position("f-algo3", SYM, -3200)
        # the follower's owner already has the whole position covered
        ex.place("f-algo3", symbol=SYM, side="buy", size=3200,
                 order_type="limit_order", limit_price=30, reduce_only=True)
        ex.place("m-jd", symbol=SYM, side="buy", size=3200,
                 order_type="limit_order", limit_price=30, reduce_only=True)
        moid = [o["id"] for o in ex.resting("m-jd", SYM)][0]
        for _ in range(4):
            await eng.process_order_event(
                place_event(moid, "buy", 3200, 30, reduce_only=True,
                            source="reconcile"))
        return len(ex.resting("f-algo3", SYM)), len(ex.killed)

    resting, killed = asyncio.run(run())
    check("still just the one cover order", resting, 1)
    check("and nothing was thrown away by the exchange", killed, 0)


def test_protection_is_mirrored_with_a_jitter():
    """The follower gets its own stop, offset from the master's. Identical
    triggers fire in the same instant against the same book, which is the
    whole reason the offset exists."""
    print("\n11. the master's stop reaches the follower, offset")
    eng, ex, mc, fc = build()

    async def run():
        ex.set_position("m-jd", SYM, 800)
        ex.set_position("f-algo3", SYM, 715)
        ex.place("m-jd", symbol=SYM, side="sell", size=800,
                 order_type="market_order", stop_price="64000",
                 stop_order_type="stop_loss_order")
        moid = [o["id"] for o in ex.resting("m-jd", SYM)][0] if ex.resting("m-jd", SYM) else "9001"
        await eng.process_order_event({
            "action": "place", "master_order_id": moid, "symbol": SYM,
            "side": "sell", "size": 800.0, "state": "pending",
            "order_type": "market_order", "stop_price": "64000",
            "stop_order_type": "stop_loss_order", "product_id": 1,
            "owner_id": "u1", "master_account_id": "m-jd"})
        stops = [o for o in ex.resting("f-algo3", SYM) if o.get("stop_order_type")]
        return stops

    stops = asyncio.run(run())
    check("the follower has one stop", len(stops), 1)
    if stops:
        trig = float(stops[0]["stop_price"])
        check("sized to the follower's own position", int(stops[0]["size"]), 715)
        check("and NOT at the master's exact trigger", trig == 64000.0, False)
        check("but близко enough to be the same stop".replace("близко", "close"),
              abs(trig - 64000.0) <= 200, True)


def test_a_master_stop_that_triggers_forces_nothing():
    """Hands-off. The follower has its own jittered stop at ~the same level; a
    forced market close on top of it is a sell-then-buy-back round trip with a
    bad fill in exactly the move that triggered it."""
    print("\n12. the master's stop fires — the follower is left to its own")
    eng, ex, mc, fc = build()

    async def run():
        ex.set_position("m-jd", SYM, 800)
        ex.set_position("f-algo3", SYM, 715)
        await eng.process_order_event({
            "action": "master_stop_filled", "master_order_id": "9002",
            "symbol": SYM, "side": "sell", "size": 800.0,
            "stop_order_type": "stop_loss_order", "product_id": 1,
            "owner_id": "u1", "master_account_id": "m-jd"})
        ex.set_position("m-jd", SYM, 0)          # his stop took him out
        # the follower still holds, with its own stop resting behind it
        ex.place("f-algo3", symbol=SYM, side="sell", size=715,
                 order_type="market_order", stop_price="63950",
                 stop_order_type="stop_loss_order")
        before = len(fc.placed)
        for _ in range(3):
            await eng._reconcile_positions(recon(eng, ex))
        return ex.position("f-algo3", SYM), len(fc.placed) - before

    held, placed = asyncio.run(run())
    check("the follower still holds its position", held, 715.0)
    check("nothing was forced onto it", placed, 0)


def test_a_master_who_goes_flat_takes_the_follower_with_him():
    """The other side of the same coin: the master is flat, the follower has
    NO stop of its own behind the leg, so nothing else is going to close it."""
    print("\n13. master flat, follower unprotected — close it")
    eng, ex, mc, fc = build()

    async def run():
        ex.set_position("m-jd", SYM, 800)
        ex.set_position("f-algo3", SYM, 715)
        await eng._reconcile_positions(recon(eng, ex))   # learn the leg
        ex.set_position("m-jd", SYM, 0)
        seen = []
        for _ in range(3):
            await eng._reconcile_positions(recon(eng, ex))
            seen.append(ex.position("f-algo3", SYM))
        return seen

    seen = asyncio.run(run())
    check("not on the first sighting", seen[0], 715.0)
    check("closed once confirmed", seen[-1], 0.0)


if __name__ == "__main__":
    for t in (test_the_session,
              test_the_master_unwinding_is_left_alone,
              test_a_covered_leg_gets_no_second_close,
              test_protection_is_mirrored_with_a_jitter,
              test_a_master_stop_that_triggers_forces_nothing,
              test_a_master_who_goes_flat_takes_the_follower_with_him):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
