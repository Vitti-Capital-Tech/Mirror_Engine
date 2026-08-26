"""Recording the resting-order half of the copy history.

Why this exists
---------------
The history tables were fed by ONE path. process_fill (master FILL events)
writes `trades` + `trade_copies`; _mirror_place (master RESTING-ORDER events)
wrote only the Redis ledger. Resting orders are how this engine actually mirrors,
so the Trades page showed exits and almost no entries — 30 of the first 53 trades
on record had no follower leg at all, and the follower positions open on
2026-08-26 were built with no rows to show for them.

And nothing observed the FOLLOWER's fills: followers connect with an on_position
callback and no on_fill, so a mirrored resting order that filled never updated
anything. The ledger said "placed" forever.

Design notes
------------
* Order-stage rows go into `trades` with master_trade_id = "ord:<order_id>".
  The fill-stage row for the same order uses the bare "<order_id>", and
  process_fill's duplicate guard keys on master_trade_id — so sharing the id
  would make the order row swallow the fill and the copies would never dispatch.
  They are genuinely different events anyway: "the master rested this" and "the
  master's order filled".

* Writes are made DIRECTLY from the event loop, not via asyncio.to_thread.
  to_thread looked like free latency — the Supabase client is synchronous, so a
  call from the loop blocks it — but the client is SHARED process-wide and using
  it from pool threads concurrently with the loop's own calls corrupts it. The
  symptom is Cloudflare 400s ("JSON could not be generated") landing on unrelated
  callers: position_monitor, the positions API, wallet reads. That error had
  occurred 3 times in five weeks; it happened 11 times in the first hour after
  to_thread shipped, so the concurrency was removed rather than optimised.

  Keeping these off the CRITICAL path is what actually mattered, and
  asyncio.create_task already does that: the order reaches the exchange without
  waiting for its own bookkeeping. The brief loop block during the write is the
  same cost every other Supabase call in this codebase already pays.

* Nothing here may raise into the caller. A history write failing must never stop
  a copy from being placed.
"""

import logging

logger = logging.getLogger(__name__)

# Distinguishes an order-stage row from the fill-stage row for the same order id.
ORDER_PREFIX = "ord:"


def order_stage_id(master_order_id) -> str:
    return f"{ORDER_PREFIX}{master_order_id}"


async def _run(fn, *a, **kw):
    """Run a Supabase call, swallowing failures.

    Deliberately NOT asyncio.to_thread — see the module docstring. The shared
    Supabase client cannot be driven from pool threads alongside the event loop.
    """
    try:
        return fn(*a, **kw)
    except Exception as e:
        logger.warning(f"order_history: {getattr(fn, '__name__', fn)} failed: {e}")
        return None


async def record_order_stage(
    db, master_order_id, *, symbol, side, size, price, kind, owner_id, raw=None
):
    """Upsert the master's resting order as a `trades` row. Returns its uuid, or
    None if it couldn't be recorded (the caller carries on regardless)."""
    if not master_order_id or not symbol or not side:
        return None
    # trades.trade_type is CHECK-constrained; a bracket/protective order is an
    # exit in history terms.
    trade_type = "entry" if kind == "entry" else "exit"
    row = {
        "master_trade_id": order_stage_id(master_order_id),
        "symbol": symbol,
        "side": side,
        "quantity": float(size or 0),
        # entry_price is NOT NULL. A market order has no limit price, so fall back
        # to 0 rather than dropping the row entirely.
        "entry_price": float(price or 0),
        "trade_type": trade_type,
        "status": "processing",
        "owner_id": owner_id,
    }
    if raw is not None:
        row["raw_payload"] = raw

    def _upsert():
        res = (
            db.table("trades")
            .upsert(row, on_conflict="master_trade_id", ignore_duplicates=False)
            .execute()
        )
        return (res.data or [{}])[0].get("id")

    uuid = await _run(_upsert)
    if uuid:
        return uuid
    # The upsert may have raced another event for the same order; read it back.
    def _read():
        res = (
            db.table("trades")
            .select("id")
            .eq("master_trade_id", order_stage_id(master_order_id))
            .execute()
        )
        return (res.data or [{}])[0].get("id")

    return await _run(_read)


async def record_leg(
    db,
    trade_uuid,
    master_order_id,
    account: dict,
    *,
    status: str,
    requested=None,
    filled=None,
    follower_order_id=None,
    reason=None,
):
    """Record (or update) one follower's leg of a master resting order.

    Keyed on (master_order_id, account_id) so a re-processed order event updates
    its existing row instead of adding a second one — the reconcile pass re-sees
    the same resting orders every 30s.
    """
    if not trade_uuid or not master_order_id or not account:
        return
    account_id = account.get("id")
    if not account_id:
        return
    payload = {
        "trade_id": trade_uuid,
        "account_id": account_id,
        "master_order_id": str(master_order_id),
        "status": status,
        "owner_id": account.get("owner_id"),
    }
    if requested is not None:
        payload["requested_quantity"] = float(requested)
    if filled is not None:
        payload["quantity"] = float(filled)
    if follower_order_id is not None:
        payload["follower_order_id"] = str(follower_order_id)
    payload["failure_reason"] = reason

    def _write():
        existing = (
            db.table("trade_copies")
            .select("id")
            .eq("master_order_id", str(master_order_id))
            .eq("account_id", account_id)
            .execute()
        )
        if existing.data:
            db.table("trade_copies").update(payload).eq("id", existing.data[0]["id"]).execute()
        else:
            db.table("trade_copies").insert(payload).execute()

    await _run(_write)


async def record_order_status(db, trade_uuid, status: str):
    """Roll the order-stage row up to a final status once its legs are known."""
    if not trade_uuid:
        return

    def _write():
        db.table("trades").update({"status": status}).eq("id", trade_uuid).execute()

    await _run(_write)


async def record_follower_fill(
    db, follower_order_id, *, account_id, filled_qty, price, symbol=None, side=None
):
    """A FOLLOWER's own order filled. Find its leg by follower_order_id and record
    what actually executed.

    This is the half that was never observed: followers had no on_fill callback,
    so a mirrored resting order that filled left its leg reading 'placed'
    indefinitely and the fill history simply had no entry for it.

    Returns True if a leg was updated, False if this fill belongs to no leg we
    recorded (e.g. an order the desk placed by hand on the follower account).
    """
    if not follower_order_id:
        return False

    def _write():
        res = (
            db.table("trade_copies")
            .select("id, trade_id, requested_quantity, quantity")
            .eq("follower_order_id", str(follower_order_id))
            .execute()
        )
        rows = res.data or []
        if not rows:
            return False
        row = rows[0]
        # A resting order can fill in pieces; each fill event carries the running
        # filled size, so take the larger rather than the latest.
        prev = float(row.get("quantity") or 0)
        qty = max(prev, float(filled_qty or 0))
        requested = row.get("requested_quantity")
        upd = {"quantity": qty}
        if price:
            upd["execution_price"] = float(price)
        if requested is not None and qty + 1e-9 < float(requested):
            upd["status"] = "partial"
            upd["failure_reason"] = (
                f"Partial fill: {qty:.0f} of {float(requested):.0f} lots"
                + (f" on {symbol}" if symbol else "")
            )
        else:
            upd["status"] = "filled"
            upd["failure_reason"] = None
        db.table("trade_copies").update(upd).eq("id", row["id"]).execute()
        return True

    return bool(await _run(_write))


def make_follower_fill_recorder(db, account: dict):
    """Build the on_fill callback for a FOLLOWER account's own order stream.

    Followers were connected with an on_position callback and NO on_fill, so the
    engine never saw a follower fill — it only ever listened to the master. Every
    mirrored resting order that filled left its leg reading 'placed' and the fill
    history had no entry for it at all. That is the half of "the order and fill
    history should match" that was simply absent.

    This only RECORDS. It deliberately triggers no copying, no sizing and no
    cancels: a follower fill is an outcome to write down, and giving it the power
    to act would let follower activity feed back into the mirror loop.
    """
    account_id = account.get("id")
    account_name = account.get("name")

    async def _on_follower_fill(order: dict) -> None:
        try:
            oid = order.get("id")
            if not oid:
                return
            state = (order.get("state") or "").lower()
            reason = (order.get("reason") or "").lower()
            # A partial fill arrives as state 'open' with reason 'fill', so the
            # reason is what identifies a fill — not the state.
            if reason != "fill" and state not in ("filled", "closed"):
                return
            unfilled = order.get("unfilled_size")
            if unfilled is not None:
                filled = float(order.get("size") or 0) - float(unfilled)
            else:
                filled = float(order.get("filled_size") or 0)
            if filled <= 0:
                return  # cancelled or otherwise closed without executing
            px = order.get("average_fill_price") or order.get("avg_fill_price")
            recorded = await record_follower_fill(
                db, oid, account_id=account_id, filled_qty=filled,
                price=float(px) if px else None,
                symbol=order.get("product_symbol"), side=order.get("side"),
            )
            if recorded:
                logger.info(
                    f"[FILL] {account_name} order {oid} filled {filled:.0f} "
                    f"{order.get('product_symbol')} @ {px} — recorded"
                )
            else:
                # Not a leg we placed: a manual order on the follower account, or
                # one whose leg predates this recording path. Worth seeing, not a
                # problem to solve here.
                logger.debug(
                    f"[FILL] {account_name} order {oid} filled {filled:.0f} "
                    f"{order.get('product_symbol')} — no matching copy leg"
                )
        except Exception as e:
            logger.warning(f"follower fill recorder ({account_name}): {e}")

    return _on_follower_fill
