"""Proportional allocation of a follower's lots across a master's order ladder.

Why this exists
---------------
The master exits in rungs. On 2026-08-26 it laddered its whole 350-lot position
on P-BTC-76800-260826 into three resting sells (150 @ 90, 100 @ 60, 100 @ 40).
Each rung was sized on its own by asking "if THIS order fills, how much should
the follower still hold?" — and each rung, in isolation, left the master holding
enough that the follower's 3 lots still looked correct:

    rung 150 -> master would hold 200 -> follower target 3, holds 3 -> close 0
    rung 100 -> master would hold 250 -> follower target 3, holds 3 -> close 0
    rung 100 -> master would hold 250 -> follower target 3, holds 3 -> close 0

So no rung claimed any lots, while together they closed the master out entirely.
The follower was left holding the leg with no exit cover at all.

The fix is to stop sizing rungs in isolation. A ladder is one intent, so the
follower's lots are allocated across the whole ladder at once, by LARGEST
REMAINDER: floor every rung's exact share, then hand the leftover lots to the
rungs with the largest fractional parts. That guarantees the parts sum to
EXACTLY the total — the property per-rung rounding cannot give, which is how the
same 3 lots became either 0 (ceil each rung, cap at held, later rungs starve) or
0 (rebalance each rung, nothing to close).

What it cannot do
-----------------
Lot granularity is not negotiable. A follower 88x smaller than the master holds 3
lots where the master holds 350, so a 100-lot rung is 1.14 follower lots. Three
rungs over three indivisible lots gives 1/1/1 — 33/33/33 against the master's
43/29/29. Largest remainder makes that rounding choice ONCE, across the ladder,
instead of per rung; it does not make the follower divisible. Finer replication
comes from funding the follower higher, not from arithmetic.
"""

from typing import Dict, List, Sequence, Tuple


def allocate(rungs: Sequence[Tuple[str, float]], total: int) -> Dict[str, int]:
    """Split `total` lots across `rungs` in proportion to each rung's size.

    rungs: sequence of (key, master_size) — key is anything hashable-as-str,
           normally the master order id.
    total: whole lots the follower has to distribute (its coverage target).

    Returns {key: lots}, summing to exactly `total` whenever the rungs can carry
    it (i.e. total <= number of rungs is fine — the biggest rungs win and the
    rest get 0).

    Deterministic: ties are broken by larger rung size, then by key, so the same
    ladder always allocates the same way. Two passes over the same snapshot must
    not disagree, or the engine would cancel and replace its own orders.
    """
    total = int(total)
    if total <= 0 or not rungs:
        return {str(k): 0 for k, _ in rungs}

    sizes = [(str(k), max(0.0, float(s or 0))) for k, s in rungs]
    pool = sum(s for _, s in sizes)
    if pool <= 0:
        # No proportional basis to divide by — refuse rather than spreading
        # arbitrarily. The caller falls back to its own sizing.
        return {k: 0 for k, _ in sizes}

    exact = [(k, total * s / pool) for k, s in sizes]
    out = {k: int(v // 1) for k, v in exact}

    leftover = total - sum(out.values())
    if leftover > 0:
        size_of = dict(sizes)
        # Largest fractional part first; then the larger rung; then the key.
        order = sorted(
            exact,
            key=lambda kv: (-(kv[1] - int(kv[1])), -size_of[kv[0]], kv[0]),
        )
        for k, _ in order[:leftover]:
            out[k] += 1

    return out


def coverage_target(held: int, master_resting: float, master_position: float) -> int:
    """How many of the follower's lots the master's resting ladder is asking to
    close.

    The ladder does not always cover the master's whole position: three rungs
    totalling 350 of 350 means "close me out" (factor 1.0), while a single 150-lot
    rung against a 350-lot position means "close 43% of me". The follower's
    coverage scales the same way, and can never exceed what it actually holds —
    a reduce-only order for more than the position is rejected by Delta, and
    resting cover beyond the position is meaningless anyway.

    `held` is the follower's ACTUAL size, not its proportional target. When the
    two differ (the reconciler declines to top up a leg the master is unwinding),
    resting cover has to match the position that exists.
    """
    held = int(abs(held or 0))
    if held <= 0:
        return 0
    resting = abs(float(master_resting or 0))
    position = abs(float(master_position or 0))
    if resting <= 0 or position <= 0:
        return 0
    fraction = min(1.0, resting / position)
    # Round rather than floor: a ladder covering 90% of a 3-lot follower means 3
    # lots of cover, not 2. Over-covering is capped by `held` and by reduce_only.
    return min(held, max(1, int(round(held * fraction))))
