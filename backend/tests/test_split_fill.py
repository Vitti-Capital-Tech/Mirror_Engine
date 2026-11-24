# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A master market order that fills in PIECES must be copied in full.

Run:  venv/Scripts/python.exe test_split_fill.py
No network, no Redis, no Supabase.

THE INCIDENT, 2026-11-22 03:01 UTC (08:31 IST), master Dhwani -> follower Dhwani1.
The master opened a six-leg spread with market orders. Two of them filled in two
pieces, and Delta sends one order update per piece, all with the SAME order id:

    1704167399  C-BTC-62800  sell 3000   piece 1:    1   piece 2: 2999
    1704167410  C-BTC-62400  sell 3000   piece 1:  830   piece 2: 2170

process_fill's duplicate guard keyed on the order id alone. Piece 1 was copied,
wrote the `trades` row, and piece 2 hit "Master trade ... already exists in DB,
skipping". The follower held 2 lots instead of 3001 on 62800 and 840 instead of
3001 on 62400 until the position reconciler topped both up ~30s later — five
Telegram messages for one trade.

The order payload says how much of the order has filled SO FAR: size minus
unfilled_size (3000 - 2999 = 1, then 3000 - 0 = 3000). So the engine copies the
difference between that and what it has already copied for this order id. A
re-delivered event has no difference, so the guard against real duplicates still
holds; a late piece 1 arriving after piece 2 has a negative one, so it is ignored.
"""
import asyncio
import math
import sys

import test_end_to_end as e2e
from test_end_to_end import Exchange, Client, Redis, Sock, ConnMgr

FAILURES = e2e.FAILURES
check = e2e.check

SYM = "C-BTC-62800-221126"
MASTER = dict(e2e.MASTER2)       # Dhwani
FOLLOWER = dict(e2e.FOLLOWER2)   # Dhwani1
RATIO = e2e.RATIO2


def share(lots):
    return int(math.ceil(abs(lots) * RATIO))


# ------------------------------------------------- a DB that keeps `trades`
class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.op, self.payload = [], "select", None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def upsert(self, p, **_k): self.op, self.payload = "insert", p; return self
    def eq(self, c, v): self.filters.append((c, v)); return self
    def neq(self, *_a): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def _match(self, r):
        for c, v in self.filters:
            got = r.get(c)
            try:
                if float(got) == float(v):
                    continue
            except (TypeError, ValueError):
                pass
            if str(got) != str(v):
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "select":
            return _Res([dict(r) for r in rows if self._match(r)])
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", "%s-%d" % (self.table, len(rows) + 1))
            rows.append(row)
            return _Res([dict(row)])
        hit = [r for r in rows if self._match(r)]
        for r in hit:
            r.update(self.payload)
        return _Res([dict(r) for r in hit])


class DB:
    def __init__(self):
        self.store = {"accounts": [MASTER, FOLLOWER]}

    def table(self, n):
        return _Q(self.store, n)


def build(delay=0.0):
    from app.core import copy_engine as ce
    from app.services import telegram_client as tg

    async def quiet(*a, **k): return False
    tg.send_message = quiet

    ex = Exchange()
    mc, fc = Client(ex, MASTER["id"]), Client(ex, FOLLOWER["id"])
    db = DB()
    eng = ce.CopyEngine(db, Redis(), Sock(),
                        ConnMgr({MASTER["id"]: mc, FOLLOWER["id"]: fc}))
    eng._get_master_client = lambda row: mc

    # The follower's order goes to the stateful exchange as a market order;
    # this test is about WHAT gets sent, not about limit-then-market escalation.
    async def execute(client, account, trade_id, symbol, side, quantity,
                      master_price, trade_type="entry"):
        if delay:
            # A real copy takes seconds (limit, wait, market fallback), and
            # other events for the same order are processed meanwhile.
            await asyncio.sleep(delay)
        await client.place_order(symbol=symbol, side=side, size=int(quantity),
                                 order_type="market_order",
                                 reduce_only=trade_type in ("exit", "sl"))
        return {"status": "filled", "filled_quantity": int(quantity),
                "account_id": account["id"], "account_name": account["name"]}
    ce.order_executor.execute = execute
    return eng, ex, fc, db


def fill_event(oid, side, cum, order_size=3000, price=10.0, reduce_only=False):
    """What TradeListener._push_fill_event puts on the queue for one piece."""
    return {"master_trade_id": str(oid), "symbol": SYM, "side": side,
            "quantity": float(cum), "entry_price": price,
            "trade_type": "exit" if reduce_only else "entry",
            "raw_payload": {"id": oid, "size": order_size,
                            "unfilled_size": order_size - cum},
            "owner_id": "u1", "master_account_id": MASTER["id"]}


# ---------------------------------------------------------------------------


def test_an_entry_filled_in_two_pieces_is_copied_in_full():
    print("\nENTRY filled 1 + 2999 (C-BTC-62800, 2026-11-22)")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1704167399", "sell", 1))
        after_first = ex.position(FOLLOWER["id"], SYM)
        await eng.process_fill(fill_event("1704167399", "sell", 3000))
        return after_first, ex.position(FOLLOWER["id"], SYM), len(fc.placed)

    after_first, final, orders = asyncio.run(run())
    check("piece 1 is copied straight away", after_first, -float(share(1)))
    check("piece 2 is copied too — follower at its share of 3000",
          final, -float(share(3000)))
    check("two follower orders, one per piece", orders, 2)
    check("one trades row for the one master order",
          len([r for r in db.store.get("trades", [])
               if r["master_trade_id"] == "1704167399"]), 1)


def test_the_same_piece_delivered_twice_is_copied_once():
    print("\nTHE SAME EVENT TWICE (a WS re-delivery)")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1704167402", "sell", 3000))
        await eng.process_fill(fill_event("1704167402", "sell", 3000))
        return ex.position(FOLLOWER["id"], SYM), len(fc.placed)

    final, orders = asyncio.run(run())
    check("follower at its share, not double", final, -float(share(3000)))
    check("one follower order", orders, 1)


def test_pieces_arriving_out_of_order_are_not_double_counted():
    print("\nPIECE 2 ARRIVES BEFORE PIECE 1")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1704167410", "sell", 3000))
        await eng.process_fill(fill_event("1704167410", "sell", 830))
        return ex.position(FOLLOWER["id"], SYM), len(fc.placed)

    final, orders = asyncio.run(run())
    check("the full order is copied once", final, -float(share(3000)))
    check("the stale piece sends nothing", orders, 1)


def test_overlapping_entry_pieces_add_up_once():
    print("\nENTRY, ONE ORDER IN THREE OVERLAPPING PIECES")
    eng, ex, fc, db = build(delay=0.05)

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await asyncio.gather(*(eng.process_fill(fill_event("1710300001", "sell", c))
                               for c in (1, 1200, 3000)))
        return ex.position(FOLLOWER["id"], SYM)

    check("the follower ends at its share of 3000 — pieces add up, not repeat",
          asyncio.run(run()), -float(share(3000)))


# --- exits filling in pieces: each piece sells ITS share (2026-11-24) -------

def _sells(fc):
    return [o for o in fc.placed if o.get("side") == "sell"]


def _sold(fc):
    return sum(int(o.get("size") or 0) for o in _sells(fc))


def test_overlapping_exit_pieces_sell_their_parts_not_the_whole():
    """2026-11-24 11:09 IST, C-BTC-62600, Dhwani -> Dhwani1. The master sold 200
    of 500 in ONE order that filled in three pieces (2, 111, 200). Each piece
    used to size the WHOLE exit (held - target = 201); the pieces ran at the
    same time (the first copy took ~9s; the leg lock gives up after 5s), each
    read the follower at 503, and each sold 201 — the follower sold all 503 and
    was bought back 301 at 11:09:57. Now each piece sells only its part."""
    print("\nEXIT, ONE ORDER IN THREE OVERLAPPING PIECES (C-BTC-62600, 2026-11-24)")
    eng, ex, fc, db = build(delay=0.05)

    async def run():
        ex.set_position(MASTER["id"], SYM, 300)              # after his sell of 200
        ex.set_position(FOLLOWER["id"], SYM, share(500))      # before
        await asyncio.gather(*(eng.process_fill(fill_event("1710277843", "sell", c, order_size=200))
                               for c in (2, 111, 200)))
        return ex.position(FOLLOWER["id"], SYM), _sold(fc)

    pos, sold = asyncio.run(run())
    check("the follower is NOT sold flat", pos > 0, True)
    check("it ends at its share of the master's 300 (within the 1-lot rounding)",
          share(300) <= pos <= share(300) + 1, True)
    check("total sold is the share of 200, not 3 x the whole exit",
          sold <= share(500) - share(300), True)


def test_slow_exit_pieces_each_sell_their_part():
    print("\nEXIT, PIECES ARRIVING SLOWLY (the master's book moves between them)")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, share(500))
        ex.set_position(MASTER["id"], SYM, 400)               # first 100 filled
        await eng.process_fill(fill_event("1710400001", "sell", 100, order_size=200))
        after_first = ex.position(FOLLOWER["id"], SYM)
        ex.set_position(MASTER["id"], SYM, 300)               # the rest filled
        await eng.process_fill(fill_event("1710400001", "sell", 200, order_size=200))
        return after_first, ex.position(FOLLOWER["id"], SYM), len(_sells(fc))

    first, final, n = asyncio.run(run())
    check("piece 1 sells its part (follower near its share of 400)",
          share(400) <= first <= share(400) + 1, True)
    check("piece 2 sells its part too — not left for the safety check",
          share(300) <= final <= share(300) + 1, True)
    check("two sells, one per piece", n, 2)


def test_an_exit_piece_delivered_twice_sells_once():
    print("\nEXIT, THE SAME PIECE TWICE")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, share(500))
        ex.set_position(MASTER["id"], SYM, 400)
        await eng.process_fill(fill_event("1710400002", "sell", 100, order_size=200))
        await eng.process_fill(fill_event("1710400002", "sell", 100, order_size=200))
        return len(_sells(fc))

    check("one sell", asyncio.run(run()), 1)


def test_exit_pieces_out_of_order_sell_once():
    print("\nEXIT, THE LAST PIECE ARRIVES FIRST")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, share(500))
        ex.set_position(MASTER["id"], SYM, 300)
        await eng.process_fill(fill_event("1710400003", "sell", 200, order_size=200))
        await eng.process_fill(fill_event("1710400003", "sell", 111, order_size=200))
        return ex.position(FOLLOWER["id"], SYM), len(_sells(fc))

    pos, n = asyncio.run(run())
    check("the follower ends exactly at its share", pos, float(share(300)))
    check("the stale piece sells nothing", n, 1)


def test_an_exit_piece_never_goes_below_the_share():
    print("\nEXIT PIECE AFTER THE FOLLOWER WAS ALREADY TRIMMED")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, share(500))
        ex.set_position(MASTER["id"], SYM, 300)
        await eng.process_fill(fill_event("1710400004", "sell", 50, order_size=200))
        # The safety check trimmed the follower to its share meanwhile.
        ex.set_position(FOLLOWER["id"], SYM, share(300))
        await eng.process_fill(fill_event("1710400004", "sell", 200, order_size=200))
        return ex.position(FOLLOWER["id"], SYM)

    check("the follower stays at its share", asyncio.run(run()), float(share(300)))


def test_an_exit_in_one_go_is_unchanged():
    print("\nEXIT FILLED IN ONE GO (the normal case)")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, share(500))
        ex.set_position(MASTER["id"], SYM, 300)
        await eng.process_fill(fill_event("1710400005", "sell", 200, order_size=200))
        return ex.position(FOLLOWER["id"], SYM), len(_sells(fc))

    pos, n = asyncio.run(run())
    check("closed to exactly its share of the master's remaining 300", pos, float(share(300)))
    check("one sell", n, 1)


def test_exit_pieces_to_flat_leave_nothing():
    print("\nEXIT IN PIECES THAT TAKES THE MASTER FLAT")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(FOLLOWER["id"], SYM, -share(3000))
        ex.set_position(MASTER["id"], SYM, -2000)
        await eng.process_fill(fill_event("1705000001", "buy", 1000, reduce_only=True))
        after_first = ex.position(FOLLOWER["id"], SYM)
        ex.set_position(MASTER["id"], SYM, 0)
        await eng.process_fill(fill_event("1705000001", "buy", 3000, reduce_only=True))
        return after_first, ex.position(FOLLOWER["id"], SYM)

    first, final = asyncio.run(run())
    check("piece 1 trims to the share of what is left", first, -float(share(2000)))
    check("the last piece takes the follower flat — no stranded lot", final, 0.0)


def test_a_late_piece_does_not_overshoot_a_follower_already_topped_up():
    print("\nTHE RECONCILER GOT THERE FIRST")
    eng, ex, fc, db = build()

    async def run():
        ex.set_position(MASTER["id"], SYM, -3000)
        await eng.process_fill(fill_event("1704167399", "sell", 1))
        # The sweep topped the follower up before piece 2 was processed.
        ex.set_position(FOLLOWER["id"], SYM, -share(3000))
        await eng.process_fill(fill_event("1704167399", "sell", 3000))
        return ex.position(FOLLOWER["id"], SYM)

    check("still exactly the share — no second top-up",
          asyncio.run(run()), -float(share(3000)))


def test_the_listener_reports_how_much_has_filled_so_far():
    print("\nTHE LISTENER'S QUANTITY IS THE ORDER'S FILLED-SO-FAR")
    import json
    from app.core.trade_listener import TradeListener

    class Q:
        def __init__(self): self.pushed = []
        async def lpush(self, k, v): self.pushed.append(json.loads(v))

    tl = TradeListener.__new__(TradeListener)
    tl.redis = Q()
    tl.master_account = {"id": MASTER["id"], "owner_id": "u1", "name": "Dhwani"}
    tl._owner_id = lambda: "u1"
    base = {"id": 1704167399, "product_symbol": SYM, "side": "sell",
            "order_type": "market_order", "size": 3000, "average_fill_price": "10"}

    async def run():
        # Payloads as stored in `trades.raw_payload` on 2026-11-22.
        await tl._push_fill_event(dict(base, filled_size="1", unfilled_size=2999,
                                       state="open"))
        # The closing update said 2999 for this piece; what matters is how much
        # of the order has filled in total, which unfilled_size states exactly.
        await tl._push_fill_event(dict(base, filled_size="2999", unfilled_size=0,
                                       state="closed"))
        # A payload without unfilled_size still works off filled_size.
        await tl._push_fill_event(dict(base, filled_size="3000"))
        return [p["quantity"] for p in tl.redis.pushed]

    check("1, then 3000, then 3000", asyncio.run(run()), [1.0, 3000.0, 3000.0])


if __name__ == "__main__":
    for t in (test_an_entry_filled_in_two_pieces_is_copied_in_full,
              test_overlapping_entry_pieces_add_up_once,
              test_the_same_piece_delivered_twice_is_copied_once,
              test_pieces_arriving_out_of_order_are_not_double_counted,
              test_a_late_piece_does_not_overshoot_a_follower_already_topped_up,
              test_overlapping_exit_pieces_sell_their_parts_not_the_whole,
              test_slow_exit_pieces_each_sell_their_part,
              test_an_exit_piece_delivered_twice_sells_once,
              test_exit_pieces_out_of_order_sell_once,
              test_an_exit_piece_never_goes_below_the_share,
              test_an_exit_in_one_go_is_unchanged,
              test_exit_pieces_to_flat_leave_nothing,
              test_the_listener_reports_how_much_has_filled_so_far):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
