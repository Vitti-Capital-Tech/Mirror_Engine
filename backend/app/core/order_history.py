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
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Distinguishes an order-stage row from the fill-stage row for the same order id.
ORDER_PREFIX = "ord:"


# ---------------------------------------------------------------------------
# Fills that arrive BEFORE their leg row exists
#
# record_leg runs in a create_task so the order reaches the exchange without
# waiting for its own bookkeeping — but that means the row lands a few hundred
# milliseconds after the order is placed, and an aggressive limit fills faster
# than that. record_follower_fill then looked up trade_copies by
# follower_order_id, found nothing, returned False, and the fill was gone: the
# miss was logged at DEBUG, which is not enabled in production.
#
# Measured on the live account, 2026-10-14 — the exchange beat our own write
# every time:
#
#     order 1603459768  filled 03:17:55.617  row written 03:17:56.169  (+552ms)
#     order 1603459767  filled 03:17:55.607  row written 03:17:55.937  (+330ms)
#     order 1603208136  filled 22:59:41.027  row written 22:59:41.382  (+355ms)
#
# That is why 503 of the newest 1,000 trade_copies rows sat at 'pending' with a
# NULL quantity, 333 of them never written a second time, going back to 25 Sep
# — and why the dashboard reported a ~36% success rate for trades that had
# actually filled.
#
# So an unmatched fill is now HELD briefly instead of dropped, and record_leg
# claims it the moment the row exists. Bounded and TTL'd: this is a hand-off
# window of milliseconds in practice, never a cache to read from.
# ---------------------------------------------------------------------------

_EARLY_FILL_TTL_SEC = 600
_EARLY_FILL_MAX = 4000
_early_fills: "OrderedDict" = OrderedDict()


def _prune_early_fills() -> None:
    cutoff = time.time() - _EARLY_FILL_TTL_SEC
    while _early_fills:
        key, val = next(iter(_early_fills.items()))
        if val["at"] >= cutoff and len(_early_fills) <= _EARLY_FILL_MAX:
            break
        _early_fills.pop(key, None)


def _hold_early_fill(follower_order_id, **info) -> None:
    key = str(follower_order_id)
    info["at"] = time.time()
    prev = _early_fills.pop(key, None)
    if prev:
        # A resting order fills in pieces; each event carries the running total,
        # so the largest is the one worth keeping.
        if float(prev.get("filled_qty") or 0) > float(info.get("filled_qty") or 0):
            info["filled_qty"] = prev["filled_qty"]
            info["price"] = info.get("price") or prev.get("price")
    _early_fills[key] = info
    _prune_early_fills()


def _take_early_fill(follower_order_id):
    if not follower_order_id:
        return None
    _prune_early_fills()
    return _early_fills.pop(str(follower_order_id), None)


def _settlement(requested, prev_qty, filled_qty, price, symbol=None) -> dict:
    """The columns that turn a 'pending' leg into a settled one.

    Shared by both paths so "what counts as filled vs partial" is defined once.
    """
    qty = max(float(prev_qty or 0), float(filled_qty or 0))
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
    return upd


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
            row_id = existing.data[0]["id"]
            db.table("trade_copies").update(payload).eq("id", row_id).execute()
        else:
            ins = db.table("trade_copies").insert(payload).execute()
            row_id = (ins.data or [{}])[0].get("id")

        # Did this order already fill before we got the row down? If so the fill
        # event found nothing to update and parked itself; apply it now, or the
        # leg stays 'pending' forever with a NULL quantity.
        if row_id and status == "pending" and follower_order_id is not None:
            early = _take_early_fill(follower_order_id)
            if early:
                upd = _settlement(
                    payload.get("requested_quantity"), payload.get("quantity"),
                    early.get("filled_qty"), early.get("price"), early.get("symbol"),
                )
                db.table("trade_copies").update(upd).eq("id", row_id).execute()
                logger.info(
                    "[FILL] %s order %s had already filled %s before its leg was "
                    "written — settled as %s",
                    account.get("name"), follower_order_id,
                    early.get("filled_qty"), upd["status"],
                )

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
            # The leg row has not landed yet — our own write is in flight. Hold
            # the fill; record_leg claims it as soon as the row exists. Dropping
            # it here is what left half the history stuck on 'pending'.
            _hold_early_fill(follower_order_id, filled_qty=filled_qty, price=price,
                             symbol=symbol, side=side, account_id=account_id)
            return False
        row = rows[0]
        # A resting order can fill in pieces; each fill event carries the running
        # filled size, so take the larger rather than the latest.
        upd = _settlement(row.get("requested_quantity"), row.get("quantity"),
                          filled_qty, price, symbol)
        db.table("trade_copies").update(upd).eq("id", row["id"]).execute()
        return True

    return bool(await _run(_write))


# ---------------------------------------------------------------------------
# Backstop: settle legs the fill stream never closed out
#
# The fill stream is the fast path. It cannot cover everything:
#   * a CANCELLED order never fills, so no event is ever coming — those legs sat
#     at 'pending' permanently (live sample: 4 of 8 stuck rows were cancelled);
#   * a fill that lands while the process is restarting finds an empty hand-off
#     window;
#   * an event can simply be missed on a reconnect ("WS closed ... keepalive
#     ping timeout" appears in the live log).
#
# So this asks the exchange directly what became of each stale order. It is also
# what repairs the existing backlog.
# ---------------------------------------------------------------------------

SWEEP_AFTER_SEC = 90
SWEEP_BATCH = 100
SWEEP_INTERVAL_SEC = 120

# Rows the exchange cannot tell us about (too old, purged, key rotated). Parked
# so the sweeper does not re-read the same unreadable orders forever and never
# reach the rest.
_UNRESOLVED_TTL_SEC = 3600
_unresolved: "OrderedDict" = OrderedDict()


def _skip_for_now(row_id) -> None:
    _unresolved[str(row_id)] = time.time()
    while len(_unresolved) > 5000:
        _unresolved.popitem(last=False)


def _is_parked(row_id) -> bool:
    at = _unresolved.get(str(row_id))
    if at is None:
        return False
    if time.time() - at > _UNRESOLVED_TTL_SEC:
        _unresolved.pop(str(row_id), None)
        return False
    return True


async def sweep_pending_legs(db, connection_manager, *, older_than_sec=SWEEP_AFTER_SEC,
                             limit=SWEEP_BATCH) -> dict:
    """Resolve legs still 'pending' well after their order should have settled.

    Reads each order from the exchange and writes down what actually happened.
    Never places or cancels anything — this only records.
    """
    import datetime as _dt

    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(seconds=older_than_sec)).isoformat()

    def _read():
        return (
            db.table("trade_copies")
            .select("id, account_id, follower_order_id, requested_quantity, quantity")
            .eq("status", "pending")
            .lt("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(limit * 3)      # headroom: parked and id-less rows are dropped below
            .execute()
        ).data or []

    rows = await _run(_read) or []
    todo = [r for r in rows
            if r.get("follower_order_id") and not _is_parked(r["id"])][:limit]
    counts = {"checked": 0, "filled": 0, "partial": 0, "cancelled": 0,
              "still_open": 0, "unreadable": 0}
    if not todo:
        return counts

    for row in todo:
        oid = str(row["follower_order_id"])
        client = connection_manager.get_client(row.get("account_id"))
        if client is None:
            _skip_for_now(row["id"])
            counts["unreadable"] += 1
            continue
        counts["checked"] += 1
        try:
            od = (await client.get_order(oid)).get("result", {}) or {}
        except Exception as e:
            logger.debug("sweep: could not read order %s: %s", oid, e)
            _skip_for_now(row["id"])
            counts["unreadable"] += 1
            continue
        if not od:
            _skip_for_now(row["id"])
            counts["unreadable"] += 1
            continue

        state = (od.get("state") or "").lower()
        unfilled = od.get("unfilled_size")
        size = float(od.get("size") or 0)
        filled = max(0.0, size - float(unfilled)) if unfilled is not None else 0.0

        if state in ("open", "pending"):
            counts["still_open"] += 1          # genuinely resting; leave it alone
            continue

        if filled > 0:
            upd = _settlement(row.get("requested_quantity"), row.get("quantity"),
                              filled, od.get("average_fill_price"),
                              od.get("product_symbol"))
            counts["partial" if upd["status"] == "partial" else "filled"] += 1
        else:
            # Closed with nothing executed. 'cancelled' is not a permitted status
            # (003_partial_fill_accounting.sql), and it is not a failure either —
            # the copy was withdrawn, not refused. 'skipped' with the reason
            # spelled out is the honest record.
            upd = {"status": "skipped",
                   "failure_reason": f"Order {state or 'closed'} before any fill"}
            counts["cancelled"] += 1

        def _settle(_id=row["id"], _upd=upd):
            db.table("trade_copies").update(_upd).eq("id", _id).execute()

        await _run(_settle)

    if any(counts[k] for k in ("filled", "partial", "cancelled")):
        logger.info(
            "sweep: settled %d filled, %d partial, %d never-filled "
            "(%d still resting, %d unreadable, of %d checked)",
            counts["filled"], counts["partial"], counts["cancelled"],
            counts["still_open"], counts["unreadable"], counts["checked"],
        )
    return counts


async def pending_leg_sweeper(db, connection_manager, interval_sec=SWEEP_INTERVAL_SEC):
    """Run sweep_pending_legs forever. Never raises into the caller."""
    import asyncio as _asyncio

    await _asyncio.sleep(30)        # let the websockets settle after startup
    logger.info("Pending-leg sweeper started (every %ss).", interval_sec)
    while True:
        try:
            await sweep_pending_legs(db, connection_manager)
        except _asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("pending-leg sweeper: %s", e)
        await _asyncio.sleep(interval_sec)


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
