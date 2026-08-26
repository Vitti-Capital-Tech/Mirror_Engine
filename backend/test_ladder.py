"""Checks for ladder allocation — the scenario table, as promised before the
engine change went anywhere near the live book.

Run:  venv/Scripts/python.exe test_ladder.py
No pytest, no network. Same style as the other test_*.py scripts here.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.core.ladder import allocate, coverage_target

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got {got}, want {want}"))
    if not ok:
        FAILURES.append(name)


def check_true(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  [{detail}]"))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------- the live case
def test_the_76800_ladder():
    """2026-08-26, P-BTC-76800-260826: master laddered its whole 350-lot position
    into 150 @ 90 / 100 @ 60 / 100 @ 40. Follower held 3 lots and got NOTHING —
    every rung individually concluded "nothing to close"."""
    rungs = [("1497216150", 150), ("1497216010", 100), ("1497215927", 100)]
    # Ladder covers the master's entire position, so the follower's whole 3 lots.
    target = coverage_target(held=3, master_resting=350, master_position=350)
    check("76800: coverage target is the full position", target, 3)
    got = allocate(rungs, target)
    check("76800: every rung gets cover", got, {"1497216150": 1, "1497216010": 1, "1497215927": 1})
    check_true("76800: parts sum to the position", sum(got.values()) == 3, got)
    check_true("76800: no rung left uncovered", all(v >= 1 for v in got.values()), got)


def test_partial_ladder_scales_down():
    """A single 150-lot rung against a 350-lot master position is "close 43% of
    me" — not "close everything"."""
    target = coverage_target(held=3, master_resting=150, master_position=350)
    check("partial ladder: 43% of 3 lots", target, 1)
    check("partial ladder: the one rung takes it", allocate([("a", 150)], target), {"a": 1})


def test_larger_follower_tracks_the_shape():
    """With enough lots the allocation reproduces the master's weighting, which is
    the whole point — 150/100/100 is 43/29/29."""
    rungs = [("a", 150), ("b", 100), ("c", 100)]
    got = allocate(rungs, 35)
    check("35 lots over 150/100/100", got, {"a": 15, "b": 10, "c": 10})
    check_true("35 lots: exact sum", sum(got.values()) == 35, got)


def test_total_is_always_exact():
    """The property per-rung rounding cannot give. Ceil-each-rung overshoots,
    floor-each-rung undershoots; largest remainder lands on the number."""
    rungs = [("a", 150), ("b", 100), ("c", 100), ("d", 33), ("e", 7)]
    for total in range(0, 60):
        got = allocate(rungs, total)
        if sum(got.values()) != total:
            check_true(f"exact sum for total={total}", False, got)
            return
    check_true("exact sum for every total 0..59", True)


def test_fewer_lots_than_rungs_favours_the_big_rungs():
    """2 lots across 4 rungs: the two largest get cover, the tiny ones get none.
    Something has to give — this makes it the smallest rungs, deliberately."""
    got = allocate([("big", 300), ("mid", 200), ("small", 10), ("tiny", 5)], 2)
    check("2 lots over 4 rungs", got, {"big": 1, "mid": 1, "small": 0, "tiny": 0})


def test_single_lot_goes_to_the_largest_rung():
    check("1 lot over a ladder", allocate([("a", 100), ("b", 250), ("c", 50)], 1),
          {"a": 0, "b": 1, "c": 0})


def test_deterministic_across_passes():
    """Two passes over the same snapshot must agree, or the engine would cancel
    and replace its own orders on every re-processed event."""
    rungs = [("a", 100), ("b", 100), ("c", 100)]
    first = allocate(rungs, 2)
    repeats = {tuple(sorted(allocate(rungs, 2).items())) for _ in range(20)}
    check_true("20 passes agree", len(repeats) == 1, repeats)
    # Equal rungs, so the tie-break must be by key — stable, not arbitrary.
    check("equal rungs tie-break by key", first, {"a": 1, "b": 1, "c": 0})


def test_reordering_the_snapshot_does_not_change_the_allocation():
    a = allocate([("a", 150), ("b", 100), ("c", 100)], 3)
    b = allocate([("c", 100), ("a", 150), ("b", 100)], 3)
    check_true("order-independent", a == b, f"{a} vs {b}")


# ------------------------------------------------------------------ edge cases
def test_nothing_to_allocate():
    check("zero total", allocate([("a", 100)], 0), {"a": 0})
    check("negative total", allocate([("a", 100)], -5), {"a": 0})
    check("no rungs", allocate([], 5), {})


def test_zero_sized_rungs_are_refused_not_guessed():
    """No proportional basis to divide by. Returning zeros lets the caller fall
    back to its own sizing rather than spreading lots arbitrarily."""
    check("all-zero rungs", allocate([("a", 0), ("b", 0)], 5), {"a": 0, "b": 0})


def test_coverage_never_exceeds_what_the_follower_holds():
    """A reduce-only order larger than the position is rejected by Delta, and
    resting cover beyond the position is meaningless."""
    check("master unwinding a huge position", coverage_target(held=3, master_resting=700, master_position=350), 3)
    check("flat follower gets no cover", coverage_target(held=0, master_resting=350, master_position=350), 0)
    check("unreadable master position", coverage_target(held=3, master_resting=350, master_position=0), 0)
    check("no resting orders", coverage_target(held=3, master_resting=0, master_position=350), 0)


def test_small_fraction_still_covers_one_lot():
    """A 10% ladder on a 3-lot follower rounds to 1, not 0 — under-covering an
    exit is the failure mode this whole change exists to remove."""
    check("10% of 3 lots", coverage_target(held=3, master_resting=35, master_position=350), 1)
    check("90% of 3 lots", coverage_target(held=3, master_resting=315, master_position=350), 3)


# ------------------------------------------------ the master order-book snapshot
# _master_resting_exits is where the risk lives: it must tell "the master has
# nothing resting" apart from "I couldn't read the book". Confusing the two would
# allocate zero cover to every rung.
class FakeMasterClient:
    def __init__(self, orders_by_state, raise_on=()):
        self.orders_by_state = orders_by_state
        self.raise_on = raise_on
        self.calls = 0

    async def get_open_orders(self, state="open"):
        self.calls += 1
        if state in self.raise_on:
            raise RuntimeError("503 from exchange")
        return self.orders_by_state.get(state, [])


def _order(oid, symbol, size, reduce_only=True, **extra):
    o = {"id": oid, "product_symbol": symbol, "size": size,
         "reduce_only": reduce_only, "side": "sell"}
    o.update(extra)
    return o


SYM = "P-BTC-76800-260826"


def _engine(fake_client):
    from app.core.copy_engine import CopyEngine
    eng = CopyEngine(db_client=None, redis_client=None, socket_mgr=None, connection_mgr=None)
    eng._get_master_client = lambda _row: fake_client
    return eng


async def test_snapshot_reads_the_real_ladder():
    """The three rungs actually placed on 2026-08-26, plus noise that must not
    be counted: a bracket, a stop, a non-reduce-only entry, another symbol."""
    client = FakeMasterClient({"open": [
        _order("1497216150", SYM, 150),
        _order("1497216010", SYM, 100),
        _order("1497215927", SYM, 100),
        _order("1496458764", SYM, 350, stop_price="76930.0"),           # bracket/stop
        _order("9", SYM, 400, stop_order_type="stop_loss_order"),        # protective
        _order("10", SYM, 600, reduce_only=False),                       # an entry
        _order("11", "C-BTC-81400-260826", 2500),                        # other symbol
    ]})
    rungs = await _engine(client)._master_resting_exits({"id": "m"}, SYM)
    check("snapshot: only the plain reduce-only rungs",
          sorted(rungs), sorted([("1497216150", 150.0), ("1497216010", 100.0), ("1497215927", 100.0)]))

    # ...and the end-to-end decision for each rung.
    cover = coverage_target(held=3, master_resting=sum(s for _, s in rungs), master_position=350)
    alloc = allocate(rungs, cover)
    check("snapshot -> allocation is 1/1/1",
          alloc, {"1497216150": 1, "1497216010": 1, "1497215927": 1})


async def test_unreadable_book_returns_none_not_empty():
    """Both states fail. None means "don't know" — the caller falls back to
    per-order sizing instead of allocating zero to everything."""
    client = FakeMasterClient({}, raise_on=("open", "pending"))
    got = await _engine(client)._master_resting_exits({"id": "m"}, SYM)
    check_true("unreadable -> None", got is None, got)


async def test_genuinely_empty_book_is_a_real_answer():
    """No resting orders is different from an unreadable book, and must not be
    reported as a failure."""
    client = FakeMasterClient({"open": [], "pending": []})
    got = await _engine(client)._master_resting_exits({"id": "m"}, SYM)
    check("empty book -> []", got, [])


async def test_partial_read_still_counts_as_read():
    """'open' succeeds, 'pending' fails. We have a real (if partial) view, so
    fall forward on it rather than discarding the rungs we did see."""
    client = FakeMasterClient({"open": [_order("1", SYM, 150)]}, raise_on=("pending",))
    got = await _engine(client)._master_resting_exits({"id": "m"}, SYM)
    check("partial read -> the rungs we saw", got, [("1", 150.0)])


async def test_same_order_in_both_states_counted_once():
    """A double-weighted rung would skew the whole allocation."""
    client = FakeMasterClient({
        "open": [_order("1", SYM, 150)],
        "pending": [_order("1", SYM, 150), _order("2", SYM, 100)],
    })
    got = await _engine(client)._master_resting_exits({"id": "m"}, SYM)
    check("deduped by order id", sorted(got), [("1", 150.0), ("2", 100.0)])


async def test_snapshot_is_cached_for_the_ladder_burst():
    """The three rungs landed inside one second. They must share one snapshot —
    both to avoid three REST calls on the hot path, and so the allocation cannot
    disagree with itself mid-ladder."""
    client = FakeMasterClient({"open": [_order("1", SYM, 150)], "pending": []})
    eng = _engine(client)
    await eng._master_resting_exits({"id": "m"}, SYM)
    after_first = client.calls
    for _ in range(5):
        await eng._master_resting_exits({"id": "m"}, SYM)
    check("cached: no extra exchange calls", client.calls, after_first)


async def run_async_tests():
    for t in (
        test_snapshot_reads_the_real_ladder,
        test_unreadable_book_returns_none_not_empty,
        test_genuinely_empty_book_is_a_real_answer,
        test_partial_read_still_counts_as_read,
        test_same_order_in_both_states_counted_once,
        test_snapshot_is_cached_for_the_ladder_burst,
    ):
        print(f"\n{t.__name__}")
        await t()


def main():
    for t in (
        test_the_76800_ladder,
        test_partial_ladder_scales_down,
        test_larger_follower_tracks_the_shape,
        test_total_is_always_exact,
        test_fewer_lots_than_rungs_favours_the_big_rungs,
        test_single_lot_goes_to_the_largest_rung,
        test_deterministic_across_passes,
        test_reordering_the_snapshot_does_not_change_the_allocation,
        test_nothing_to_allocate,
        test_zero_sized_rungs_are_refused_not_guessed,
        test_coverage_never_exceeds_what_the_follower_holds,
        test_small_fraction_still_covers_one_lot,
    ):
        print(f"\n{t.__name__}")
        t()
    asyncio.run(run_async_tests())
    print("\n" + ("ALL PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
