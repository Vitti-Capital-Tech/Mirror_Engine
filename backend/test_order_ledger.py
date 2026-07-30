"""Regression test for the order-ID ledger and the size-aware reconciler.

Replays the real incident on C-BTC-67200-300726 (2026-07-29), reconstructed from the
backend logs - NOT the "the event was lost during an outage" story it first looked
like. There was no outage; the timeline was:

  18:26:41  master rests a full-size exit LIMIT (order 1442271322)
  18:26:45  engine mirrors it correctly -> follower order 1442271534
            master's order half-fills (master -3000 -> -1464)
            follower's limit is behind it in the queue and fills NOTHING
  20:56:43  master cancels the remainder -> engine cancels the follower's copy too,
            taking it out of the queue while it still held all 30 lots

So the follower held 30 when it should have held 15, on the CORRECT side - invisible
to the old side-only reconciler, and left there because (a) escalation to market was
switched off for exits by design, and (b) mirroring the master's cancel is literally
faithful but semantically backwards when the master got what it wanted and the
follower got nothing.

Covers all three fixes: the cancel-time settle (primary), the reconciler's size/ratio
check (backstop), and the ledger's order-ID trail. Also asserts the look-alikes do
NOT trigger a trim: a correctly-sized follower, ordinary ceil/floor rounding, a
position still being built, or master activity too recent to have settled.

No network, no Redis, no Supabase: everything is faked. Run it directly:
    python test_order_ledger.py
Exits non-zero if any check fails.
"""
import asyncio, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Fake-but-well-formed creds so the module-level Supabase client constructs.
# Nothing here ever talks to Supabase - the engine gets FakeDB injected. Set
# before importing app.* so a real .env can never be picked up by this test.
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from app.core import order_ledger as ledger


# ---------------------------------------------------------------- fake redis
class FakeRedis:
    def __init__(self):
        self.h = {}   # key -> {field: value}
        self.z = {}   # key -> {member: score}

    async def hset(self, key, field=None, value=None, mapping=None):
        d = self.h.setdefault(key, {})
        if mapping:
            d.update({str(k): str(v) for k, v in mapping.items()})
        else:
            d[str(field)] = str(value)

    async def hget(self, key, field):
        return self.h.get(key, {}).get(str(field))

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hdel(self, key, field):
        self.h.get(key, {}).pop(str(field), None)

    async def delete(self, key):
        self.h.pop(key, None)

    async def expire(self, key, ttl):
        pass

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, lo, hi):
        z = self.z.get(key, {})
        for m in [m for m, s in z.items() if lo <= s <= hi]:
            z.pop(m)

    async def zrevrange(self, key, start, stop):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in items[start:stop + 1]]

    def pipeline(self, transaction=False):
        """Mimics redis.asyncio pipelining: queue sync calls, flush on execute()."""
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def __getattr__(self, name):
        def queue(*a, **k):
            self.ops.append((name, a, k))
            return self
        return queue

    async def execute(self):
        out = []
        for name, a, k in self.ops:
            out.append(await getattr(self.r, name)(*a, **k))
        self.ops.clear()
        return out


# ------------------------------------------------------------- fake supabase
MASTER = {"id": "m1", "name": "Master", "is_master": True, "api_key": "k",
          "api_secret": "s", "environment": "demo", "allocated_balance": 100000,
          "owner_id": "u1"}
FOLLOWER = {"id": "f1", "name": "Prathav-Follower", "is_master": False,
            "status": "active", "api_key": "k", "api_secret": "s",
            "environment": "demo", "allocation_mode": "multiplier",
            "allocation_value": 0.01, "owner_id": "u1"}


class Res:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, rows): self.rows = rows; self.f = {}
    def select(self, *a, **k): return self
    def eq(self, col, val): self.f[col] = val; return self
    def execute(self):
        out = [r for r in self.rows if all(r.get(c) == v for c, v in self.f.items())]
        return Res(out)


class FakeDB:
    def table(self, name):
        return Query([MASTER, FOLLOWER] if name == "accounts" else [])


# --------------------------------------------------------------- fake client
class FakeClient:
    def __init__(self, positions, orders=()):
        self._pos = positions
        self._orders = list(orders)
        self.placed = []
        self.cancelled = []
        self.order_reads = 0
        self.orders_fail = False

    async def get_positions(self): return self._pos
    async def get_open_orders(self, state=None):
        self.order_reads += 1
        if self.orders_fail:
            raise RuntimeError("simulated exchange read failure")
        return self._orders if state == "open" else []
    async def place_order(self, **kw): self.placed.append(kw); return {"id": "new1"}
    async def cancel_order(self, oid, product_id=None):
        self.cancelled.append(str(oid))
    async def close(self): pass


class FakeConnMgr:
    def __init__(self, client): self.client = client
    def get_client(self, _id): return self.client
    async def connect_account(self, _acc): return self.client


class FakeSocket:
    async def emit_trade_copy(self, *a, **k): pass
    async def emit_account_update(self, *a, **k): pass


SYM = "C-BTC-67200-300726"


def build(follower_size, master_size, orders=(), last_master_fill_age=600.0,
          mark=1.2, entry=None):
    from app.core.copy_engine import CopyEngine
    client = FakeClient([{"product_symbol": SYM, "size": follower_size}], orders)
    eng = CopyEngine(FakeDB(), FakeRedis(), FakeSocket(), FakeConnMgr(client))
    # Stub the REST-backed helpers the reconciler uses.
    age = last_master_fill_age
    async def fake_fresh(_master_row):
        return {SYM: time.time() - age}
    eng._master_recent_fill_ts = fake_fresh
    async def fake_classify(_m, _s): return "unknown"
    eng._classify_master_exit = fake_classify
    async def fake_master_size(_row, _sym, fresh=False): return abs(master_size)
    eng._master_position_size = fake_master_size
    event = {"owner_id": "u1", "positions": (
        [{"symbol": SYM, "size": master_size, "mark": mark, "entry": entry}]
        if master_size else []
    )}
    return eng, client, event


async def passes(eng, event, n=2):
    for _ in range(n):
        await eng._reconcile_positions(event)


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' --- ' + detail) if detail else ''}")
    return cond


async def main():
    ok = True

    # ---- 1. The 67200 bug: master trimmed to 1464, follower still holds 30.
    eng, client, event = build(-30, -1464)
    await eng._reconcile_positions(event)
    ok &= check("pass 1 does NOT act (two-pass confirmation)", client.placed == [],
                f"placed={client.placed}")
    await eng._reconcile_positions(event)
    ok &= check("pass 2 trims the missed partial exit", len(client.placed) == 1)
    if client.placed:
        o = client.placed[0]
        ok &= check("trim is buy 15 reduce-only market on the right symbol",
                    o["side"] == "buy" and o["size"] == 15 and o["reduce_only"] is True
                    and o["order_type"] == "market_order" and o["symbol"] == SYM,
                    str(o))

    # ---- 2. No false positive when the follower is already correct.
    eng, client, event = build(-15, -1464)
    await passes(eng, event)
    ok &= check("correctly-sized follower is left alone", client.placed == [],
                f"placed={client.placed}")

    # ---- 3. Rounding must never look like over-exposure (ceil on open).
    for msz, held in ((-150, -2), (-100, -1), (-101, -2), (-3000, -30)):
        eng, client, event = build(held, msz)
        await passes(eng, event)
        ok &= check(f"rounding safe: master {msz} / follower {held}",
                    client.placed == [], f"placed={client.placed}")

    # ---- 4. A resting order that would ADD to the position means the size isn't
    #         settled (follower may be building) -> don't trim.
    eng, client, event = build(-30, -1464, orders=[
        {"product_symbol": SYM, "id": "o9", "side": "sell"}])  # sell adds to a short
    await passes(eng, event)
    ok &= check("resting ADD order suppresses the trim", client.placed == [],
                f"placed={client.placed}")

    # ---- 4a. An order with an unreadable side is treated as adding (conservative).
    eng, client, event = build(-30, -1464, orders=[{"product_symbol": SYM, "id": "o9"}])
    await passes(eng, event)
    ok &= check("unknown-side resting order suppresses the trim", client.placed == [],
                f"placed={client.placed}")

    # ---- 4c. THE 67200 CASE: the follower's mirrored EXIT is resting and stuck
    #          (buy, reducing a short) and never filled. The old guard skipped the
    #          trim for as long as it rested - i.e. forever. It must now cancel the
    #          stuck close FIRST (so it can't fill on top of the market close) and
    #          then trim.
    eng, client, event = build(-30, -1464, orders=[
        {"product_symbol": SYM, "id": "1442271534", "side": "buy", "product_id": 42}])
    await passes(eng, event)
    ok &= check("stuck EXIT order does NOT suppress the trim", len(client.placed) == 1,
                f"placed={client.placed}")
    ok &= check("stuck EXIT order is cancelled before the trim",
                client.cancelled == ["1442271534"], f"cancelled={client.cancelled}")

    # ---- 4b. A resting SL/TP must NOT suppress it (followers almost always
    #          have one, so keying off it would disable trimming entirely).
    eng, client, event = build(-30, -1464, orders=[
        {"product_symbol": SYM, "id": "o9", "stop_order_type": "stop_loss_order"}])
    await passes(eng, event)
    ok &= check("resting SL/TP does NOT suppress the trim", len(client.placed) == 1,
                f"placed={client.placed}")

    # ---- 5. Too soon after the master's fill -> wait (don't race a live close).
    eng, client, event = build(-30, -1464, last_master_fill_age=5.0)
    await passes(eng, event)
    ok &= check("recent master fill defers the trim", client.placed == [],
                f"placed={client.placed}")

    # ---- 6. Stale-orphan escape: master FLAT, follower holds behind a stop,
    #         classify returns 'unknown' (grid master, close rolled out of the
    #         history window). Old behaviour: deferred forever.
    eng, client, event = build(-15, 0, orders=[
        {"product_symbol": SYM, "id": "o9", "stop_order_type": "stop_loss_order"}])
    await passes(eng, event)
    ok &= check("stale unknown-exit orphan gets closed", len(client.placed) >= 1,
                f"placed={client.placed}")

    # ---- 6b. ...but a RECENT unknown exit still waits for the follower's stop.
    eng, client, event = build(-15, 0, orders=[
        {"product_symbol": SYM, "id": "o9", "stop_order_type": "stop_loss_order"}],
        last_master_fill_age=10.0)
    await passes(eng, event)
    ok &= check("recent unknown-exit is still deferred", client.placed == [],
                f"placed={client.placed}")

    # ---- 7. Cancel-time settle - the primary 67200 fix. At 20:56 the master
    #         cancelled the remainder of its exit limit after getting ~half filled.
    #         The follower's mirrored exit had filled NOTHING, so cancelling its copy
    #         left it holding everything. The settle must finish the exit instead.
    eng, client, event = build(-30, -1464)
    await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER, ref_price=1.2)
    ok &= check("cancel settle finishes the follower's unfilled exit",
                len(client.placed) == 1, f"placed={client.placed}")
    if client.placed:
        o = client.placed[0]
        ok &= check("settle closes exactly 15 (30 -> target 15), reduce-only market",
                    o["side"] == "buy" and o["size"] == 15 and o["reduce_only"] is True
                    and o["order_type"] == "market_order", str(o))

    # ---- 7b. Same target math as the reconciler's trim - if the two disagreed they
    #          would fight each other forever (settle closes to 14, trim wants 15).
    eng, client, event = build(-15, -1464)
    await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER, ref_price=1.2)
    ok &= check("settle is a no-op when the follower is already at target",
                client.placed == [], f"placed={client.placed}")

    # ---- 7c. A cancel on a symbol the follower is flat on must do nothing.
    eng, client, event = build(0, -1464)
    await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER, ref_price=1.2)
    ok &= check("settle is a no-op when the follower is flat", client.placed == [],
                f"placed={client.placed}")

    # ---- 8. UNDER-exposed top-up. The 2026-07-30 audit found 4 symbols where the
    #         follower sat below target with nothing to correct it: the trim only
    #         handles over-exposure and the open path only fires when FLAT.
    #         (C-BTC-64800: master 500 long, follower 3, target 5.)
    eng, client, event = build(3, 500, mark=1.2, entry=1.2)
    await eng._reconcile_positions(event)
    ok &= check("top-up pass 1 does NOT act", client.placed == [], f"placed={client.placed}")
    await eng._reconcile_positions(event)
    ok &= check("top-up pass 2 buys the shortfall", len(client.placed) == 1,
                f"placed={client.placed}")
    if client.placed:
        o = client.placed[0]
        ok &= check("top-up is buy 2 (3 -> target 5), NOT reduce-only",
                    o["side"] == "buy" and o["size"] == 2 and o["reduce_only"] is False,
                    str(o))

    # ---- 8b. Same, short side: master -3050, follower -29, target 31 -> sell 2.
    eng, client, event = build(-29, -3050, mark=1.2, entry=1.2)
    await passes(eng, event)
    ok &= check("top-up on a short sells to increase the short",
                len(client.placed) == 1 and client.placed[0]["side"] == "sell"
                and client.placed[0]["size"] == 2, f"placed={client.placed}")

    # ---- 8c. Price guard blocks a top-up when the market has run away from the
    #          master's entry (default tolerance 15%; 1.2 -> 2.0 is +67%).
    eng, client, event = build(3, 500, mark=2.0, entry=1.2)
    await passes(eng, event)
    ok &= check("top-up blocked when price drifted beyond tolerance",
                client.placed == [], f"placed={client.placed}")

    # ---- 9. Stale MISSING leg. Master's last fill is 600s old (> FRESH_ENTRY_SEC),
    #         which used to mean "SKIP stale entry" forever. With the price still
    #         near the master's entry it must now be recovered.
    eng, client, event = build(0, -1500, mark=1.2, entry=1.15)
    await passes(eng, event)
    ok &= check("stale missing leg IS recovered when price is close",
                len(client.placed) == 1 and client.placed[0]["side"] == "sell"
                and client.placed[0]["reduce_only"] is False, f"placed={client.placed}")

    # ---- 9b. ...but not when the price has run away.
    eng, client, event = build(0, -1500, mark=3.0, entry=1.15)
    await passes(eng, event)
    ok &= check("stale missing leg is NOT recovered when price drifted",
                client.placed == [], f"placed={client.placed}")

    # ---- 9c. Unknown entry price must not block recovery (a missing leg is a
    #          certain divergence; unknown drift is only a possible cost).
    eng, client, event = build(0, -1500, mark=1.2, entry=None)
    await passes(eng, event)
    ok &= check("unknown master entry still recovers the leg", len(client.placed) == 1,
                f"placed={client.placed}")

    # ---- 10. ROOT CAUSE of the missing TP/SL. A master SL/TP is reduce_only but is
    #          NOT a close-now order. It used to fall into the close-rebalance branch,
    #          which asks "how much must this follower close right now?" - and for a
    #          correctly-sized follower that is always "nothing", so the protection
    #          was dropped. Live logs showed this 175x for C-BTC-65400-300726
    #          ("nothing to rest (holds 1, target 1)") while the follower had no TP.
    prot_event = {
        "action": "place", "master_order_id": "1442619685", "symbol": SYM,
        "product_id": 144020, "side": "sell", "size": 150,
        "order_type": "market_order", "limit_price": None, "stop_price": 5.0,
        "stop_order_type": "take_profit_order", "stop_trigger_method": "mark_price",
        "reduce_only": True, "is_bracket": False, "is_update": False, "owner_id": "u1",
    }
    eng, client, _ = build(2, 150)
    await eng._mirror_place(prot_event, "1442619685")
    ok &= check("master TP/SL is now MIRRORED, not dropped as a no-op close",
                len(client.placed) == 1, f"placed={client.placed}")
    if client.placed:
        o = client.placed[0]
        ok &= check("mirrored protection carries the stop type and is sized to the position",
                    o.get("stop_order_type") == "take_profit_order"
                    and o.get("size") == 2 and o.get("reduce_only") is True, str(o))
        ok &= check("mirrored protection trigger is jittered off the master's 5.0",
                    o.get("stop_price") is not None and o["stop_price"] != 5.0,
                    f"stop_price={o.get('stop_price')}")

    # ---- 10b. Nothing to protect -> skip cleanly (don't place a naked stop).
    eng, client, _ = build(0, 150)
    await eng._mirror_place(prot_event, "1442619685")
    ok &= check("no protection placed when the follower holds nothing",
                client.placed == [], f"placed={client.placed}")

    # ---- 11. DEADBAND. auto_ratio derives the target from a LIVE balance ratio, so
    #          it drifts continuously; a 3050-lot master leg sat right on the 30/31
    #          boundary. Acting on that 1-lot difference churned real money live on
    #          2026-07-30 (trimmed P-BTC-60000 and P-BTC-60500 by 1 each, which the
    #          top-up would then buy back). Differences within 5% must be ignored.
    # master -3000 -> target ceil(30.0) = 30, so +/-1 lot is 3.3% - inside the band.
    eng, client, event = build(-31, -3000, mark=1.2, entry=1.2)
    await passes(eng, event)
    ok &= check("1-lot noise on a 30-lot leg does NOT trim",
                client.placed == [], f"placed={client.placed}")

    eng, client, event = build(-29, -3000, mark=1.2, entry=1.2)
    await passes(eng, event)
    ok &= check("1-lot noise on a 30-lot leg does NOT top up",
                client.placed == [], f"placed={client.placed}")

    # ...but a genuine miss still clears the deadband comfortably.
    eng, client, event = build(-15, -3000, mark=1.2, entry=1.2)
    await passes(eng, event)
    ok &= check("a real 15-lot shortfall still tops up", len(client.placed) == 1,
                f"placed={client.placed}")

    eng, client, _ = build(-30, -1464)
    await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER, ref_price=1.2)
    ok &= check("settle still fires on a real 15-lot excess", len(client.placed) == 1,
                f"placed={client.placed}")

    eng, client, _ = build(-31, -3000)
    await eng._settle_exit_after_cancel(FOLLOWER, client, SYM, MASTER, ref_price=1.2)
    ok &= check("settle ignores 1 lot on a 30-lot leg (no churn against the trim)",
                client.placed == [], f"placed={client.placed}")

    # ---- 12. Liveness cache. This is the hottest call in the engine (every repeat
    #          of a master resting order asked the exchange twice PER FOLLOWER), and
    #          that REST volume delayed order placement past Delta's ~5s signature
    #          window - orders were rejected with expired_signature 13-21s late.
    #          The cache must be ASYMMETRIC: a hit may only ever answer "live".
    #          A stale "not live" would place a DUPLICATE order.
    eng, client, _ = build(-30, -1464, orders=[{"product_symbol": SYM, "id": "555"}])
    ok &= check("first liveness check reads the exchange",
                await eng._order_is_live(client, "555", "f1") is True and client.order_reads > 0,
                f"reads={client.order_reads}")
    before = client.order_reads
    ok &= check("repeat check for a LIVE order is served from cache (no REST call)",
                await eng._order_is_live(client, "555", "f1") is True
                and client.order_reads == before, f"reads={client.order_reads}")

    # An id NOT in the cached set must be re-verified fresh, never answered "gone"
    # from cache - that is the direction that causes duplicate orders.
    before = client.order_reads
    ok &= check("unknown id is re-checked fresh, not answered from cache",
                await eng._order_is_live(client, "999", "f1") is False
                and client.order_reads > before, f"reads={client.order_reads}")

    # A read failure must answer "live" so we never double-place on a hiccup.
    eng, client, _ = build(-30, -1464, orders=[{"product_symbol": SYM, "id": "555"}])
    client.orders_fail = True
    ok &= check("exchange read failure answers LIVE (never risk a duplicate)",
                await eng._order_is_live(client, "555", "f1") is True)

    # ---- 12b. Master client reuse. Four hot helpers used to construct AND close a
    #           DeltaClient per call - a fresh TLS handshake to Delta every time,
    #           several times per 15s reconcile pass. That handshake cost sat on the
    #           event loop and helped push signed orders past the ~5s window.
    eng, _, _ = build(-30, -1464)
    c1 = eng._get_master_client(MASTER)
    c2 = eng._get_master_client(MASTER)
    ok &= check("master client is reused across calls (no TLS handshake per call)",
                c1 is c2 and c1 is not None)

    rotated = dict(MASTER, api_key="rotated-key")
    c3 = eng._get_master_client(rotated)
    ok &= check("rotated credentials build a fresh client, not a stale one",
                c3 is not c1)
    ok &= check("only one client kept per master after rotation",
                len([k for k in eng._master_clients if k[0] == MASTER["id"]]) == 1,
                f"clients={list(eng._master_clients)}")
    ok &= check("a missing master row yields no client rather than raising",
                eng._get_master_client(None) is None)

    # ---- 13. The ledger itself.
    r = FakeRedis()
    await ledger.record_master_order(r, "1442114746", symbol=SYM, side="sell",
                                     size=3000, kind="entry", owner_id="u1")
    await ledger.record_follower_leg(r, "1442114746", "f1", status="filled", qty=30)
    await ledger.record_master_order(r, "1442271322", symbol=SYM, side="buy",
                                     size=1536, kind="exit", owner_id="u1")
    # (no follower leg --- this is the exit that vanished)
    await ledger.record_master_order(r, "1442552128", symbol=SYM, side="buy",
                                     size=1464, kind="exit", owner_id="u1")
    await ledger.record_follower_leg(r, "1442552128", "f1", status="filled", qty=15)

    missing = await ledger.missing_for_follower(r, "u1", SYM, "f1", kind="exit")
    ids = [m["master_order_id"] for m in missing]
    ok &= check("ledger names exactly the un-mirrored exit order",
                ids == ["1442271322"], f"got {ids}")

    entry = await ledger.get_entry(r, "1442114746")
    ok &= check("ledger round-trips the master order + follower leg",
                entry["symbol"] == SYM and entry["size"] == 3000
                and entry["legs"]["f1"]["status"] == "filled"
                and entry["legs"]["f1"]["qty"] == 30, str(entry))

    await ledger.record_follower_leg(r, "1442271322", "f1", status="skipped",
                                     reason="already at target")
    missing = await ledger.missing_for_follower(r, "u1", SYM, "f1", kind="exit")
    ok &= check("a deliberate 'skipped' is accounted for, not missing",
                missing == [], f"got {[m['master_order_id'] for m in missing]}")

    await ledger.record_follower_leg(r, "1442271322", "f1", status="failed",
                                     reason="insufficient margin")
    missing = await ledger.missing_for_follower(r, "u1", SYM, "f1", kind="exit")
    ok &= check("a FAILED leg counts as missing",
                [m["master_order_id"] for m in missing] == ["1442271322"])

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
