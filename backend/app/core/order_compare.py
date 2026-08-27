"""Comparing the master's ORDERS against the followers' orders.

Why this replaces the position view
-----------------------------------
The first version of this comparison graded NET POSITION per symbol. That view is
structurally incapable of finding the bugs it was built to find, because the 15s
reconciler always repairs net position. Proven on 2026-08-27: the master sold
2750 lots of P-BTC-74500, the follower's proportional share was 31, the engine
punched **62**, and the reconciler bought 31 back a minute later. Net position
ended at exactly -31, so the position view reported that symbol as MATCHED — on
the very day the double-sizing bug was doing real damage.

Anything the reconciler can fix will read as matched. So position is the wrong
unit. The order is the right one: it records what the engine actually *did*, at
the moment it did it, before anything got cleaned up.

What a row answers
------------------
One row per master order, one leg per active follower, and per leg:

* **ratio** — did the follower punch the right SIZE? Its order should be
  ``ceil(master_size x ratio)``. 62 against a target of 31 is a 2.0x
  over-punch and reads UNMATCHED here, however tidy the end position looks.
* **time diff** — how long after the master's order did the follower's go on?
* **cancels** — the master cancelling an order and the follower not (or vice
  versa) is a mismatch in its own right, invisible in fills because neither
  side traded.
* **reason** — what the engine recorded about the leg, when it recorded anything.

Sizes compare against the follower's PROPORTIONAL target, never the master's raw
lots: a follower at 1/89th is correct punching 31 against 2750.

Ladders keep their own verdict. The master splits an exit across many rungs and
the engine deliberately mirrors it as ONE ladder, so a rung with no follower
order of its own is not a miss — grading those one-to-one produced 36 false
"missing" copies on 2026-08-26. Such a rung is labelled `ladder`, which is
neutral, and the ladder's total is checked instead.

End-state position is still computed, but as a clearly-secondary read-out. It is
useful ("did the reconciler get us there in the end?") and it must never again be
mistaken for the verdict.
"""

import asyncio
import logging
import math
from datetime import datetime
from typing import Optional

from app.core.fill_compare import (  # shared primitives — same window, same maths
    IST,
    ist_day_bounds,
    today_ist,
    to_us,
    parse_ts,
    follower_ratio,
    expected_lots,
    load_engine_legs,
    fetch_account_fills,
    group_fills,
    _iso,
    _num,
)
from app.services.delta_client import DeltaClient

logger = logging.getLogger(__name__)

# How far apart a master order and a follower order may be placed and still be
# treated as the same mirrored action when no recorded leg links them. Mirroring
# is fast (sub-second to a few seconds); this is deliberately wider so a slow
# mirror is still MATCHED-but-late rather than reported missing, since the time
# diff column is what should carry that information.
INFER_WINDOW_SEC = 120.0

# Lots of slack before a follower's order size counts as wrong. One lot: the
# sizing path ceils, so a sub-lot share rounds up by design.
SIZE_TOLERANCE = 1.0

MISMATCH_VERDICTS = {"missing", "oversized", "undersized", "cancel_missed", "extra"}

# Outcomes that are CORRECT — the engine did what it was supposed to. All three
# count as matched in the rate and render as matched in every view.
#
#   matched      — the follower punched the right size for this master order.
#   ladder       — this rung had no follower order of its own, which is exactly
#                  what should happen (the engine mirrors a whole ladder with one
#                  order), AND the ladder's total came out right.
#   cancelled_ok — the master cancelled and the follower cancelled too.
#
# `ladder` and `cancelled_ok` used to be excluded from the rate rather than
# counted, which read as 0% on a day with four correctly-covered rungs and one
# correctly-mirrored cancel. Excluding a right answer is not neutrality.
PASS_VERDICTS = {"matched", "ladder", "cancelled_ok"}

# Genuinely ungradable — we cannot say whether these were right or wrong, so they
# are left out of the rate in either direction rather than scored as a guess.
NEUTRAL_VERDICTS = {"skipped", "unsized", "unreadable"}

# Worst-first, so a master order's row takes its worst leg.
_SEVERITY = {
    "matched": 0, "cancelled_ok": 1, "ladder": 2, "skipped": 3, "unsized": 4,
    "extra": 5, "cancel_missed": 6, "undersized": 7, "oversized": 8, "missing": 9,
}


def _is_open_state(state: str) -> bool:
    return (state or "").lower() in ("open", "pending")


def _cancelled(o: dict) -> bool:
    st = (o.get("state") or "").lower()
    return st in ("cancelled", "canceled") and _filled_of(o) <= 0


def _filled_of(o: dict) -> float:
    size = _num(o.get("size")) or 0.0
    unfilled = _num(o.get("unfilled_size"))
    if unfilled is not None:
        return max(0.0, size - unfilled)
    return _num(o.get("filled_size")) or 0.0


def normalise_order(o: dict) -> dict:
    """One raw order-history row → the fields the comparison reads."""
    return {
        "order_id": str(o.get("id")) if o.get("id") is not None else None,
        "symbol": o.get("product_symbol"),
        "side": (o.get("side") or "").lower(),
        "size": _num(o.get("size")) or 0.0,
        "filled": _filled_of(o),
        "state": (o.get("state") or "").lower(),
        "order_type": o.get("order_type"),
        "reduce_only": bool(o.get("reduce_only")),
        "limit_price": _num(o.get("limit_price")),
        "avg_fill_price": _num(o.get("average_fill_price")),
        "is_stop": bool(o.get("stop_order_type") or o.get("stop_price")),
        "cancel_reason": o.get("cancellation_reason") or o.get("reason"),
        "created_ts": parse_ts(o.get("created_at")),
        "updated_ts": parse_ts(o.get("updated_at")),
    }


async def fetch_account_orders(acc: dict, start: datetime, end: datetime) -> dict:
    """Orders AND fills for one account over a window.

    Both are needed: order history says what was asked for (and what was
    cancelled), fills say what actually executed. An unreachable account returns
    an explicit error — "this follower placed nothing" and "we could not read
    this follower" must never look the same.
    """
    client = DeltaClient(
        api_key=acc["api_key"],
        api_secret=acc["api_secret"],
        environment=acc.get("environment", "demo"),
    )
    try:
        raw_orders = await client.get_order_history_range(to_us(start), to_us(end))
        orders = [normalise_order(o) for o in raw_orders if o.get("id") is not None]
        orders.sort(key=lambda o: o["created_ts"] or datetime.min.replace(tzinfo=IST))
        return {"orders": orders, "error": None}
    except Exception as e:
        logger.warning(f"order_compare: could not read orders for {acc.get('name')}: {e}")
        return {"orders": [], "error": str(e)}
    finally:
        await client.close()


def _time_diff_ms(m: dict, f: dict) -> Optional[float]:
    """Follower order placed minus master order placed, in ms.

    Negative is possible and is reported as-is: the follower's mirror can be
    acknowledged before the master's own order event comes back to us.
    """
    a, b = m.get("created_ts"), f.get("created_ts")
    if not a or not b:
        return None
    return round((b - a).total_seconds() * 1000.0, 1)


def _infer(m: dict, candidates: list, taken: set) -> Optional[dict]:
    """Nearest unclaimed follower order on the same symbol and side."""
    best, gap_best = None, None
    mt = m.get("created_ts")
    if not mt:
        return None
    for c in candidates:
        if c["order_id"] in taken:
            continue
        if c["symbol"] != m["symbol"] or c["side"] != m["side"]:
            continue
        if not c.get("created_ts"):
            continue
        gap = abs((c["created_ts"] - mt).total_seconds())
        if gap > INFER_WINDOW_SEC:
            continue
        if gap_best is None or gap < gap_best:
            best, gap_best = c, gap
    return best


def _grade(m: dict, f: Optional[dict], target: Optional[float], leg: Optional[dict],
           unreadable: bool) -> tuple:
    """(verdict, note) for one follower against one master order."""
    leg_status = (leg or {}).get("status") or ""
    reason = (leg or {}).get("failure_reason") or ""

    if unreadable and f is None:
        return "unreadable", "follower orders could not be read"

    if f is None:
        if leg_status == "skipped":
            return "skipped", reason or "engine skipped this copy deliberately"
        if _cancelled(m) or m["state"] in ("cancelled", "canceled"):
            # The master cancelled without filling and the follower has no order
            # here either — nothing was owed.
            return "cancelled_ok", "master cancelled; follower had nothing to mirror"
        return "missing", reason or "no follower order for this master order"

    # The master cancelled: did the follower's mirror get cancelled too?
    if _cancelled(m):
        if _cancelled(f):
            return "cancelled_ok", "master cancelled, follower cancelled"
        if f["filled"] > 0:
            return "extra", (f"master cancelled without filling, but the follower "
                             f"filled {f['filled']:g}")
        if _is_open_state(f["state"]):
            return "cancel_missed", "master cancelled; follower's order is still resting"
        return "cancelled_ok", "master cancelled, follower not resting"

    if target is None:
        return "unsized", "no proportional target could be derived for this follower"

    placed = f["size"]
    if placed > target + SIZE_TOLERANCE:
        mult = placed / target if target else 0
        return "oversized", (f"punched {placed:g} against a target of {target:g}"
                             + (f" ({mult:.1f}x)" if mult >= 1.5 else ""))
    if placed + SIZE_TOLERANCE < target:
        return "undersized", f"punched {placed:g} against a target of {target:g}"
    return "matched", ""


def compare_orders(master_orders: list, f_state: list, legs: dict) -> list:
    """One row per master order, one leg per follower."""
    rows = []
    for m in master_orders:
        mid = m["order_id"]
        mid_legs = {str(l.get("account_id")): l for l in legs["by_master"].get(mid, [])}
        row_legs = []
        for st in f_state:
            acc = st["account"]
            leg = mid_legs.get(str(acc["id"]))

            f, link = None, None
            foid = (leg or {}).get("follower_order_id")
            if foid and str(foid) in st["by_order"]:
                f, link = st["by_order"][str(foid)], "linked"
            if f is None:
                inferred = _infer(m, st["orders"], st["taken"])
                if inferred is not None:
                    f, link = inferred, "inferred"
                    leg = leg or legs["by_follower"].get(inferred["order_id"])
            if f is not None:
                st["taken"].add(f["order_id"])

            requested = _num((leg or {}).get("requested_quantity"))
            target, basis = expected_lots(m["size"], acc, st["master"])
            basis = f"derived — {basis}"

            verdict, note = _grade(m, f, target, leg, bool(st["error"]))
            ratio_actual = (f["size"] / m["size"]) if (f and m["size"]) else None

            row_legs.append({
                "account_id": acc["id"],
                "account_name": acc.get("name"),
                "verdict": verdict,
                "note": note,
                "link": link,
                "follower_order_id": f["order_id"] if f else None,
                "placed_lots": f["size"] if f else None,
                "filled_lots": f["filled"] if f else None,
                "target_lots": target,
                "target_basis": basis,
                "requested_lots": requested,
                "ratio_actual": round(ratio_actual, 6) if ratio_actual is not None else None,
                "ratio_target": st["ratio"],
                "state": f["state"] if f else None,
                "placed_at": _iso(f.get("created_ts")) if f else None,
                "time_diff_ms": _time_diff_ms(m, f) if f else None,
                "leg_status": (leg or {}).get("status"),
                "leg_reason": (leg or {}).get("failure_reason"),
            })

        worst = max((l["verdict"] for l in row_legs),
                    key=lambda v: _SEVERITY.get(v, 4), default="matched")
        rows.append({
            "master_order_id": mid,
            "symbol": m["symbol"],
            "side": m["side"],
            "master_lots": m["size"],
            "master_filled": m["filled"],
            "master_state": m["state"],
            "order_type": m["order_type"],
            "reduce_only": m["reduce_only"],
            "is_stop": m["is_stop"],
            "limit_price": m["limit_price"],
            "cancel_reason": m["cancel_reason"],
            "placed_at": _iso(m.get("created_ts")),
            "status": worst,
            "legs": row_legs,
        })
    return rows


def apply_ladder_context(rows: list, f_state: list) -> None:
    """Neutralise per-rung verdicts inside a laddered action.

    The master splits one exit across many rungs and the engine mirrors it as ONE
    ladder, so the follower's single cover order pairs with whichever rung it
    happened to land near: that rung reads oversized (it carries every rung's
    lots) and the rest read missing (they have no order of their own). Both are
    artefacts of pairing one order against N, not findings — grading them
    literally produced 36 false "missing" copies on 2026-08-26.

    So for a laddered group the legs become `ladder` (neutral) when the ladder's
    TOTAL is right, and carry the total's verdict when it is not. A single-rung
    action is untouched: there the per-order verdict is the whole truth, which is
    exactly what catches a 62-against-31 double-punch.
    """
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["symbol"], r["side"]), []).append(r)

    for (symbol, side), grp in groups.items():
        if len(grp) < 2:
            continue                      # not a ladder — leave it alone
        master_total = sum(r["master_lots"] for r in grp)
        for st in f_state:
            acc_id = st["account"]["id"]
            legs = [l for r in grp for l in r["legs"] if l["account_id"] == acc_id]
            if not legs:
                continue
            placed_total = sum(l["placed_lots"] or 0 for l in legs)
            target_total, _ = expected_lots(master_total, st["account"], st["master"])
            if target_total is None:
                continue
            # Each rung may round up by just under a lot, so N rungs can carry
            # N-1 lots of legitimate rounding.
            allowance = SIZE_TOLERANCE + max(0, len(grp) - 1)
            if placed_total > target_total + allowance:
                verdict = "oversized"
                note = (f"{len(grp)}-rung ladder on {symbol} {side}: punched "
                        f"{placed_total:g} against a target of {target_total:g}")
            elif placed_total + SIZE_TOLERANCE < target_total:
                verdict = "undersized"
                note = (f"{len(grp)}-rung ladder on {symbol} {side}: punched "
                        f"{placed_total:g} against a target of {target_total:g}")
            else:
                verdict = "ladder"
                note = (f"part of a {len(grp)}-rung ladder on {symbol} {side} — the "
                        f"total is right ({placed_total:g} of {target_total:g})")
            for l in legs:
                if l["verdict"] in MISMATCH_VERDICTS or l["verdict"] == "matched":
                    l["verdict"] = verdict
                    l["note"] = note
        for r in grp:
            r["status"] = max((l["verdict"] for l in r["legs"]),
                              key=lambda v: _SEVERITY.get(v, 4), default="matched")


def summarise(rows: list, f_state: list, extra_orders: list) -> dict:
    all_legs = [l for r in rows for l in r["legs"]]
    by_verdict: dict = {}
    for l in all_legs:
        by_verdict[l["verdict"]] = by_verdict.get(l["verdict"], 0) + 1

    diffs = sorted(l["time_diff_ms"] for l in all_legs if l.get("time_diff_ms") is not None)

    def pct(p):
        if not diffs:
            return None
        return diffs[min(len(diffs) - 1, max(0, int(round((len(diffs) - 1) * p))))]

    def rate(items):
        """Of the legs we could actually judge, how many were right."""
        graded = [i for i in items
                  if i["verdict"] in MISMATCH_VERDICTS or i["verdict"] in PASS_VERDICTS]
        if not graded:
            return None
        passed = sum(1 for i in graded if i["verdict"] in PASS_VERDICTS)
        return round(passed / len(graded) * 100, 2)

    per_follower = []
    for st in f_state:
        fid = st["account"]["id"]
        mine = [l for l in all_legs if l["account_id"] == fid]
        d = sorted(l["time_diff_ms"] for l in mine if l.get("time_diff_ms") is not None)
        counts: dict = {}
        for l in mine:
            counts[l["verdict"]] = counts.get(l["verdict"], 0) + 1
        per_follower.append({
            "account_id": fid,
            "account_name": st["account"].get("name"),
            "ratio": st["ratio"],
            "ratio_basis": st["ratio_basis"],
            "orders": len(mine),
            "by_verdict": counts,
            "errors": sum(counts.get(v, 0) for v in MISMATCH_VERDICTS),
            "matched": sum(counts.get(v, 0) for v in PASS_VERDICTS),
            "graded": sum(counts.get(v, 0) for v in PASS_VERDICTS | MISMATCH_VERDICTS),
            "match_rate_pct": rate(mine),
            "avg_time_diff_ms": round(sum(d) / len(d), 1) if d else None,
            "median_time_diff_ms": d[len(d) // 2] if d else None,
            "max_time_diff_ms": max(d) if d else None,
            "unreadable": bool(st["error"]),
        })

    r = rate(all_legs)
    return {
        "master_orders": len(rows),
        "legs": len(all_legs),
        "by_verdict": by_verdict,
        "matched": sum(by_verdict.get(v, 0) for v in PASS_VERDICTS),
        "graded": sum(by_verdict.get(v, 0)
                      for v in PASS_VERDICTS | MISMATCH_VERDICTS),
        "errors": sum(by_verdict.get(v, 0) for v in MISMATCH_VERDICTS),
        "match_rate_pct": 100.0 if r is None else r,
        "avg_time_diff_ms": round(sum(diffs) / len(diffs), 1) if diffs else None,
        "median_time_diff_ms": pct(0.5),
        "p95_time_diff_ms": pct(0.95),
        "max_time_diff_ms": diffs[-1] if diffs else None,
        "time_diff_samples": len(diffs),
        "per_follower": per_follower,
        "extra_follower_orders": len(extra_orders),
    }


async def compare(accounts: list, start: datetime, end: datetime, db=None) -> dict:
    master = next((a for a in accounts if a.get("is_master")), None)
    followers = [a for a in accounts
                 if not a.get("is_master") and a.get("status") == "active"]
    excluded = [a for a in accounts
                if not a.get("is_master") and a.get("status") != "active"]

    window = {"start": _iso(start), "end": _iso(end), "timezone": "Asia/Kolkata (IST)"}
    excluded_out = [{
        "id": a["id"], "name": a.get("name"), "status": a.get("status"),
        "reason": f"status is {a.get('status')!r} — the engine does not copy to it",
    } for a in excluded]

    if not master:
        return {"window": window, "master": None, "followers": [], "rows": [],
                "excluded_followers": excluded_out, "extra_follower_orders": [],
                "position": [], "summary": summarise([], [], []),
                "warnings": ["No master account configured — nothing to compare against."]}

    results = await asyncio.gather(
        fetch_account_orders(master, start, end),
        *[fetch_account_orders(f, start, end) for f in followers],
    )
    m_res, f_res = results[0], list(results[1:])

    warnings = []
    if m_res["error"]:
        warnings.append(f"Master '{master.get('name')}' orders unreadable: {m_res['error']}")
    for f, r in zip(followers, f_res):
        if r["error"]:
            warnings.append(f"Follower '{f.get('name')}' orders unreadable: {r['error']}")

    # Protective stop/bracket orders are jittered per follower by design and are
    # not one-for-one mirrors, so they are excluded from the size comparison
    # rather than reported as endless mismatches.
    master_orders = [o for o in m_res["orders"] if not o["is_stop"]]

    f_state = []
    for f, r in zip(followers, f_res):
        ratio, why = follower_ratio(f, master)
        f_state.append({
            "account": f, "master": master,
            "orders": [o for o in r["orders"] if not o["is_stop"]],
            "by_order": {o["order_id"]: o for o in r["orders"]},
            "taken": set(), "error": r["error"],
            "ratio": round(ratio, 6) if ratio is not None else None,
            "ratio_basis": why,
        })

    legs = load_engine_legs(
        db,
        [o["order_id"] for o in master_orders],
        [o["order_id"] for st in f_state for o in st["orders"]],
    )

    rows = compare_orders(master_orders, f_state, legs)
    apply_ladder_context(rows, f_state)

    # Follower orders no master order accounts for, on a symbol the master never
    # traded. A symbol the master DID trade is covered by the rows above.
    master_keys = {o["symbol"] for o in master_orders}
    extra = []
    for st in f_state:
        for o in st["orders"]:
            if o["symbol"] in master_keys or o["order_id"] in st["taken"]:
                continue
            extra.append({
                "account_id": st["account"]["id"],
                "account_name": st["account"].get("name"),
                "follower_order_id": o["order_id"],
                "symbol": o["symbol"], "side": o["side"],
                "lots": o["size"], "filled": o["filled"], "state": o["state"],
                "placed_at": _iso(o.get("created_ts")),
                "explanation": "master never traded this symbol today",
            })
    extra.sort(key=lambda x: x["placed_at"] or "")

    return {
        "window": window,
        "master": {
            "id": master["id"], "name": master.get("name"),
            "environment": master.get("environment"),
            "order_count": len(master_orders),
            "lots": round(sum(o["size"] for o in master_orders), 8),
            "unreadable": bool(m_res["error"]),
        },
        "followers": [{
            "id": st["account"]["id"], "name": st["account"].get("name"),
            "ratio": st["ratio"], "ratio_basis": st["ratio_basis"],
            "order_count": len(st["orders"]),
            "unreadable": bool(st["error"]),
        } for st in f_state],
        "rows": rows,
        "excluded_followers": excluded_out,
        "extra_follower_orders": extra,
        "summary": summarise(rows, f_state, extra),
        "warnings": warnings,
    }


async def compare_for_day(db, owner_id: str, day) -> dict:
    from app.core.fill_compare import owner_accounts
    start, end = ist_day_bounds(day)
    out = await compare(owner_accounts(db, owner_id), start, end, db=db)
    out["window"]["date"] = day.isoformat()
    out["owner_id"] = owner_id
    return out


async def compare_for_owner(db, owner_id: str, start: datetime, end: datetime) -> dict:
    from app.core.fill_compare import owner_accounts
    out = await compare(owner_accounts(db, owner_id), start, end, db=db)
    out["owner_id"] = owner_id
    return out
