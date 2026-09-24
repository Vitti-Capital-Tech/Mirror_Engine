"""Checks for the "don't trim while the master is still working an order" gate.

Run:  venv/Scripts/python.exe test_trim_hold.py
No pytest, no network. Same style as the other test_*.py scripts here.

The case this exists for (21 Sep 2026, P-BTC-79600): one 2400-lot master cover
filled in pieces over eleven minutes. His position dropped at each piece, ours did
not, and the reconciler markets the difference every time — five market trims,
27 -> 21 -> 18 -> 13 -> 11 -> 4, spread paid five times, while our mirrored exit sat
behind his in the queue and never got the chance to fill at his price.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.core.copy_engine import CopyEngine

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


SYM = "P-BTC-79600-210926"


class FakeMasterClient:
    """Scripted master order book. raise_on makes get_open_orders blow up for that
    state, so "unreadable" can be told apart from "nothing working"."""

    def __init__(self, orders_by_state=None, raise_on=()):
        self.orders_by_state = orders_by_state or {}
        self.raise_on = raise_on
        self.calls = 0

    async def get_open_orders(self, state="open"):
        self.calls += 1
        if state in self.raise_on:
            raise RuntimeError("503 from exchange")
        return self.orders_by_state.get(state, [])


def _order(oid, symbol, side, size, **extra):
    o = {"id": oid, "product_symbol": symbol, "side": side, "size": size}
    o.update(extra)
    return o


def _engine(client):
    eng = CopyEngine(db_client=None, redis_client=None, socket_mgr=None, connection_mgr=None)
    eng._get_master_client = lambda _row: client
    return eng


MROW = {"id": "m", "api_key": "k", "api_secret": "s"}


# ---------------------------------------------------------------- the live case
async def test_his_cover_still_working_holds_the_trim():
    """21 Sep: master SHORT, buying to cover. His buy is still on the book, so the
    follower's excess is a copy in flight — not something to market against."""
    c = FakeMasterClient({"open": [_order("1497216150", SYM, "buy", 1877)]})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("his cover order is working -> hold", got, True)


async def test_order_finished_releases_the_trim():
    """Nothing left on the book: his order filled or was cancelled, so whatever gap
    remains is real and the reconciler should act."""
    c = FakeMasterClient({"open": [], "pending": []})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-276)
    check("no working order -> trim allowed", got, False)


async def test_unreadable_book_is_not_permission_to_trim():
    """Must be None, never False. Reading a failed fetch as "he has nothing
    working" would market against an order that is still filling."""
    c = FakeMasterClient({}, raise_on=("open", "pending"))
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("unreadable -> None", got, None)


# --------------------------------------------------------------- what counts
async def test_an_order_that_would_ADD_does_not_hold():
    """He is short and has a SELL resting — that adds to the short, it is not the
    exit we are waiting on. The follower's excess is unrelated and real."""
    c = FakeMasterClient({"open": [_order("9", SYM, "sell", 500)]})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("adding order -> no hold", got, False)


async def test_reduce_side_is_derived_not_taken_from_the_flag():
    """Delta does not always set reduce_only. Deriving from the side against his
    position is what makes the gate reliable."""
    c = FakeMasterClient({"open": [_order("1", SYM, "buy", 1877)]})  # no reduce_only key
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("no reduce_only flag, still detected", got, True)


async def test_long_position_reducing_side_is_the_opposite():
    """He is LONG and selling to reduce."""
    c = FakeMasterClient({"open": [_order("1", SYM, "sell", 1500)]})
    check("sell reduces a long -> hold",
          await _engine(c)._master_working_reducer(MROW, SYM, msz=+2400), True)
    c2 = FakeMasterClient({"open": [_order("1", SYM, "buy", 1500)]})
    check("buy adds to a long -> no hold",
          await _engine(c2)._master_working_reducer(MROW, SYM, msz=+2400), False)


async def test_stops_and_brackets_do_not_hold_the_trim():
    """Protection rests until triggered — it is not an exit in flight, and treating
    it as one would hold the trim for as long as the position exists."""
    for extra in ({"stop_order_type": "stop_loss_order"},
                  {"stop_price": "76930.0"},
                  {"bracket_order": True}):
        c = FakeMasterClient({"open": [_order("1", SYM, "buy", 2400, **extra)]})
        got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
        check(f"protection ({list(extra)[0]}) -> no hold", got, False)


async def test_another_symbol_does_not_hold_this_one():
    c = FakeMasterClient({"open": [_order("1", "C-BTC-81400-260826", "buy", 2400)]})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("other symbol -> no hold", got, False)


async def test_a_pending_order_counts_as_working():
    c = FakeMasterClient({"open": [], "pending": [_order("1", SYM, "buy", 1877)]})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=-2400)
    check("pending state counts", got, True)


async def test_flat_master_never_holds():
    """He holds nothing, so nothing he has resting is 'reducing'. The follower's
    position is an orphan and must stay trimmable."""
    c = FakeMasterClient({"open": [_order("1", SYM, "buy", 2400)]})
    got = await _engine(c)._master_working_reducer(MROW, SYM, msz=0)
    check("master flat -> no hold", got, False)
    check("and it costs no exchange call", c.calls, 0)


async def test_snapshot_is_cached():
    """The reconcile pass asks per follower; one read should serve them all."""
    c = FakeMasterClient({"open": [_order("1", SYM, "buy", 1877)], "pending": []})
    eng = _engine(c)
    await eng._master_working_reducer(MROW, SYM, msz=-2400)
    after_first = c.calls
    for _ in range(5):
        await eng._master_working_reducer(MROW, SYM, msz=-2400)
    check("cached: no extra exchange calls", c.calls, after_first)


# ------------------------------------------- the 21 Sep episode, end to end
async def test_the_21_sep_episode():
    """Every observation from the table: his order was live at all five, so all
    five trims are held. Today they were five market orders."""
    observed = [  # (his unfilled remainder, follower held, target)
        (1877, 27, 21),
        (1592, 27, 18),
        (1145, 27, 13),
        (976, 27, 11),
        (276, 27, 4),
    ]
    trims = 0
    for remaining, held, target in observed:
        c = FakeMasterClient({"open": [_order("x", SYM, "buy", remaining)]})
        if not await _engine(c)._master_working_reducer(MROW, SYM, msz=-remaining):
            trims += 1
    check("21 Sep: market trims during his cover", trims, 0)

    # ...and once his order is gone, the gap settles in one step.
    c = FakeMasterClient({"open": [], "pending": []})
    check("21 Sep: trim released once his order terminates",
          await _engine(c)._master_working_reducer(MROW, SYM, msz=-276), False)


async def main():
    for t in (
        test_his_cover_still_working_holds_the_trim,
        test_order_finished_releases_the_trim,
        test_unreadable_book_is_not_permission_to_trim,
        test_an_order_that_would_ADD_does_not_hold,
        test_reduce_side_is_derived_not_taken_from_the_flag,
        test_long_position_reducing_side_is_the_opposite,
        test_stops_and_brackets_do_not_hold_the_trim,
        test_another_symbol_does_not_hold_this_one,
        test_a_pending_order_counts_as_working,
        test_flat_master_never_holds,
        test_snapshot_is_cached,
        test_the_21_sep_episode,
    ):
        print(f"\n{t.__name__}")
        await t()
    print("\n" + ("ALL PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
