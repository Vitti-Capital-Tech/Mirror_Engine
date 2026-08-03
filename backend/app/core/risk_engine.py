"""
RiskEngine — pre-trade safety checks before copying an order to a follower.

Checks performed (in order):
1. Account status must be 'active'
2. Consecutive failures must be < 5 (circuit-breaker guard)
3. Quantity must be positive
4. Estimated margin (qty * price / leverage) must not exceed available_margin
5. Position size must not exceed account's max_position_size (if set)
"""

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# If available margin cannot be determined, allow trades up to this notional USD
_FALLBACK_MAX_MARGIN_USD = 10_000.0
_MAX_CONSECUTIVE_FAILURES = 5


class RiskEngine:
    """Stateless risk checker.  All methods are synchronous (no I/O)."""

    def check(
        self,
        account: dict,
        quantity: float,
        entry_price: float,
    ) -> Tuple[bool, str]:
        """
        Run all risk checks for *account* and the proposed *quantity* / *entry_price*.

        Returns
        -------
        (True, '')           — trade is allowed
        (False, reason_str)  — trade is blocked, with human-readable reason
        """
        # 1. Status check
        status = account.get("status", "paused")
        if status != "active":
            return False, f"Account status is '{status}' (must be 'active')"

        # 2. Circuit-breaker: consecutive failure guard
        consecutive_failures: int = account.get("consecutive_failures", 0)
        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            return (
                False,
                f"Circuit breaker: {consecutive_failures} consecutive failures — account paused",
            )

        # 3. Quantity sanity
        if quantity <= 0:
            return False, f"Invalid quantity: {quantity}"

        # 4. Margin check
        leverage_limit: int = account.get("leverage_limit", 50) or 50
        estimated_margin = (quantity * entry_price) / leverage_limit
        available_margin: float = account.get("available_margin") or _FALLBACK_MAX_MARGIN_USD

        if estimated_margin > available_margin:
            return (
                False,
                (
                    f"Insufficient margin: need ~{estimated_margin:.2f} USD "
                    f"but only {available_margin:.2f} USD available"
                ),
            )

        # 5. Max position-size guard
        max_position_size: float | None = account.get("max_position_size")
        if max_position_size is not None and quantity > max_position_size:
            return (
                False,
                f"Quantity {quantity} exceeds max_position_size {max_position_size}",
            )

        return True, ""

    def calculate_follower_quantity(
        self,
        master_quantity: float,
        master_price: float,
        account: dict,
        round_up: bool = False,
        min_one: bool = True,
    ) -> int:
        """
        Compute the order size for a follower account based on its allocation_mode.

        Returns the quantity as an integer (Delta Exchange uses integer lot sizes).

        round_up=True: ceil the result — used for all ORDER PLACEMENT (opens and
        reduce-only closes) so a small master order whose follower share is a
        fraction (e.g. 0.5) still punches ≥1 lot instead of flooring to 0 and
        being silently dropped. Over-exposure is at most <1 lot on opens, and
        reduce_only caps closes so they can't over-close.
        round_up=False: floor — used ONLY for a rebalance TARGET (how much the
        follower should still hold), which must be able to reach 0.
        """
        # An UNCONFIGURED account must not be sized by guesswork. Defaulting a
        # missing mode to "multiplier" and a missing value to 1.0 silently means
        # "copy the master lot-for-lot" — the same catastrophic outcome as the
        # auto_ratio 1:1 fallback removed below (a 610-lot target on a 70 USD
        # account). Refuse instead; the reconciler retries once it is configured.
        allocation_mode = account.get("allocation_mode")
        raw_value = account.get("allocation_value")
        if not allocation_mode:
            logger.error(
                "No allocation_mode set for account %s — refusing to size (would "
                "otherwise default to copying the master 1:1)", account.get("id"),
            )
            return 0
        if allocation_mode != "auto_ratio" and raw_value is None:
            logger.error(
                "No allocation_value set for account %s (mode=%s) — refusing to size",
                account.get("id"), allocation_mode,
            )
            return 0
        allocation_value: float = float(raw_value) if raw_value is not None else 0.0
        available_margin: float = account.get("available_margin") or 0.0
        leverage_limit: int = account.get("leverage_limit", 50) or 50

        if allocation_mode == "fixed":
            qty = allocation_value

        elif allocation_mode == "multiplier":
            qty = master_quantity * allocation_value

        elif allocation_mode == "capital_pct":
            # allocation_value is a percentage (e.g. 10 → 10%)
            budget = available_margin * (allocation_value / 100.0)
            notional_per_lot = master_price / leverage_limit
            qty = budget / notional_per_lot if notional_per_lot > 0 else 0

        elif allocation_mode == "auto_ratio":
            # Dynamic balance ratio (follower balance / master balance).
            # An explicit allocated_balance overrides the real balance for the
            # ratio — lets you size copies as if the accounts were comparable
            # (e.g. allocate 60 on a 4000-balance master to test 1-lot copies).
            # EQUITY, not free margin. available_margin shrinks as positions lock
            # margin up and grows as they close, so a ratio built on it swings with
            # the book — observed 2026-08-03: the same unchanged positions were
            # "expected 22" at one moment and "expected 30" half an hour later, a
            # 30% move, which fired four false Position Mismatch alerts and would
            # have the reconciler resizing correct positions back and forth.
            # Total balance is stable, so the follower's share of the master stays
            # put unless someone actually deposits or withdraws.
            master_balance = float(account.get("master_balance") or 0.0)
            follower_balance = float(
                account.get("allocated_balance")
                or account.get("balance")
                or account.get("available_margin")
                or 0.0
            )
            
            if master_balance > 0 and follower_balance > 0:
                ratio = follower_balance / master_balance
                qty = master_quantity * ratio
                logger.info(f"Auto Balance Ratio: follower_bal={follower_balance}, master_bal={master_balance}, ratio={ratio:.6f}, calculated_qty={qty:.4f}")
            else:
                # NEVER fall back to 1:1 here. auto_ratio exists precisely because
                # the follower is far smaller than the master, so copying the
                # master's size verbatim is the single most dangerous thing this
                # function can return. Observed live 2026-08-02: the master's
                # balance read as 0.0 five times in a day, and the reconciler
                # computed a target of 610 lots for a 70 USD account. It only
                # failed to execute because the two-pass confirmation happened to
                # break first — luck, not safety.
                #
                # Return 0 so callers skip. A missed copy is recoverable (the
                # reconciler retries once balances read again); a 100x oversized
                # order is not.
                logger.error(
                    "Auto Ratio UNAVAILABLE — skipping sizing (follower_balance=%s, "
                    "master_balance=%s). Refusing to fall back to 1:1.",
                    follower_balance, master_balance,
                )
                return 0

        else:
            # An unrecognised mode (typo, new mode not handled here) previously
            # mirrored the master EXACTLY — the same 1:1 hazard by another route.
            logger.error(
                "Unknown allocation_mode %r for account %s — refusing to size",
                allocation_mode, account.get("id"),
            )
            return 0

        import math
        # Opens floor (never over-expose); closes ceil (never leave a residual,
        # reduce_only caps it so it can't over-close).
        rounded = math.ceil(qty) if round_up else math.floor(qty)
        # min_one=True (default) never returns 0 — good for OPENS (a copy is at
        # least 1 lot). For a rebalance TARGET (how much the follower should
        # still hold) we need a true 0, so callers pass min_one=False.
        result = max(1, rounded) if min_one else max(0, rounded)
        max_pos: float | None = account.get("max_position_size")
        if max_pos is not None:
            result = min(result, int(max_pos))

        logger.debug(
            "Follower qty for account %s: mode=%s value=%s qty=%d",
            account.get("id"),
            allocation_mode,
            allocation_value,
            result,
        )
        return result
