"""Comparing what the MASTER actually filled against what each FOLLOWER did.

Why this exists
---------------
Every existing view answers "what did the engine try to do?". The Trades page
lists rows the engine wrote; the ledger lists master orders the engine saw. None
of them can answer the question the desk actually asks each morning — *do the
two accounts match?* — because all of them are the engine describing its own
behaviour. A copy that never reached the exchange, a fill the engine never
observed, and a follower order placed by hand are all invisible to a view built
from the engine's own bookkeeping.

So the source of truth here is the EXCHANGE: /v2/fills for the master and for
every follower over a time window, grouped per order, then matched up. The
engine's records (`trades` / `trade_copies`) are layered on afterwards as
*annotation* — they explain a mismatch (skipped for margin, sized to 0, failed
with a reason) but they never define one. That ordering is the whole point: if
the engine's records were the input, a fill it failed to record would read as
"nothing happened", which is precisely the failure being hunted.

How a master order is matched to a follower's
---------------------------------------------
Three routes, in descending confidence, each labelled on the row so a reader
knows how much to trust it:

1. ``linked`` — a recorded leg ties the master order id to a follower order id.
   This is how the resting-order mirror path records every copy, so it covers
   the overwhelming majority.
2. ``inferred`` — no leg, but the follower has a fill on the same symbol and
   side within INFER_WINDOW_SEC of the master's. This is the case that matters
   most: a copy that reached the exchange but that the engine failed to write
   down. Reporting it as unmatched would be a false alarm; reporting it as
   ``linked`` would hide a bookkeeping bug. It gets its own label.
3. nothing matched — the follower genuinely has no fill for that master order.

Sizes are compared against the follower's PROPORTIONAL target, never against the
master's raw lots: a follower sized at 1/40th of the master is *correct* when it
fills 1 lot against 40. The target comes from the recorded leg's
``requested_quantity`` when there is one (that is literally what the engine asked
the exchange for), and is derived from the account's allocation settings when
there isn't. Which of the two was used is reported, because a derived target on
an ``auto_ratio`` account is only as good as the balances at read time.

Nothing in here places, cancels or edits anything. It is a read-only observer.
"""

import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.services.delta_client import DeltaClient

logger = logging.getLogger(__name__)

# The desk trades Delta Exchange India; "a day" is an IST calendar day, not UTC.
# Getting this wrong splits an evening's trading across two reports.
IST = timezone(timedelta(hours=5, minutes=30))

# How far apart a master fill and a follower fill may be and still be treated as
# the same mirrored event when no recorded leg links them. Generous on purpose:
# a mirrored limit rests until it fills, and the escalation path deliberately
# lets it rest for as long as the master's own order does, so a legitimate copy
# can land minutes after the master's. A window that is too tight manufactures
# "missing" rows for copies that plainly happened.
INFER_WINDOW_SEC = 180.0

# Lots of slack allowed before a fill counts as short of its target. One lot,
# because the sizing path floors opens and ceils closes — a 1-lot disagreement is
# rounding, not a copy failure.
SHORT_LOT_TOLERANCE = 1.0

# Supabase caps how much you can cram into a single `in_` filter before the URL
# gets unreasonable; chunk lookups at this size.
_IN_CHUNK = 100

# Leg statuses that mean the follower deliberately did not trade: the engine
# made a decision, rather than trying and failing. Reported separately from
# errors — a risk check doing its job is not a fault.
DELIBERATE_STATUSES = {"skipped"}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of one IST calendar day, as UTC-aware datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def today_ist() -> date:
    return datetime.now(IST).date()


def to_us(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


def parse_ts(v) -> Optional[datetime]:
    """Delta hands timestamps back as either an ISO string or an integer number
    of microseconds, depending on the endpoint. Accept both."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        # Microseconds since epoch (Delta's convention). Seconds and milliseconds
        # are accepted too so a change of unit doesn't silently produce dates in
        # 1970 or the year 55000.
        n = float(v)
        if n > 1e17:
            n /= 1_000_000_000  # nanoseconds
        elif n > 1e14:
            n /= 1_000_000      # microseconds
        elif n > 1e11:
            n /= 1_000          # milliseconds
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Grouping exchange fills into one row per order
# ---------------------------------------------------------------------------

def group_fills(fills: list) -> list[dict]:
    """Raw /v2/fills rows → one entry per exchange ORDER.

    A resting limit order fills in pieces, and each piece is its own fill row.
    Comparing piece-by-piece would report a master order that filled in 3 clips
    against a follower order that filled in 1 as a mismatch, when the totals
    agree perfectly. So the unit of comparison is the order, with lots summed and
    price volume-weighted.
    """
    groups: dict[str, dict] = {}
    for f in fills or []:
        oid = f.get("order_id")
        if oid is None:
            continue
        oid = str(oid)
        lots = _num(f.get("size")) or 0.0
        px = _num(f.get("price"))
        ts = parse_ts(f.get("created_at"))
        g = groups.get(oid)
        if g is None:
            g = groups[oid] = {
                "order_id": oid,
                "symbol": f.get("product_symbol"),
                "side": (f.get("side") or "").lower(),
                "lots": 0.0,
                "notional_px": 0.0,   # Σ lots·price, divided out below
                "commission": 0.0,
                "fill_count": 0,
                "first_ts": ts,
                "last_ts": ts,
                "fill_type": f.get("fill_type"),
                "order_type": ((f.get("meta_data") or {}).get("order_type")),
            }
        g["lots"] += lots
        if px is not None:
            g["notional_px"] += lots * px
        g["commission"] += _num(f.get("commission")) or 0.0
        g["fill_count"] += 1
        if ts:
            if not g["first_ts"] or ts < g["first_ts"]:
                g["first_ts"] = ts
            if not g["last_ts"] or ts > g["last_ts"]:
                g["last_ts"] = ts

    out = []
    for g in groups.values():
        g["avg_price"] = round(g["notional_px"] / g["lots"], 6) if g["lots"] else None
        g.pop("notional_px", None)
        g["commission"] = round(g["commission"], 8)
        out.append(g)
    out.sort(key=lambda g: g["first_ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return out


async def fetch_account_fills(acc: dict, start: datetime, end: datetime) -> dict:
    """Grouped fills for one account over a window.

    An unreachable account returns an explicit ``error`` rather than an empty
    list. The distinction is load-bearing: "this follower filled nothing" and "we
    could not read this follower" look identical in a list of zero fills, and
    reporting the second as the first would state that the accounts match when
    nobody actually checked.
    """
    client = DeltaClient(
        api_key=acc["api_key"],
        api_secret=acc["api_secret"],
        environment=acc.get("environment", "demo"),
    )
    try:
        raw = await client.get_fills_range(to_us(start), to_us(end))
        return {"groups": group_fills(raw), "raw_count": len(raw), "error": None}
    except Exception as e:
        logger.warning(f"fill_compare: could not read fills for {acc.get('name')}: {e}")
        return {"groups": [], "raw_count": 0, "error": str(e)}
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Proportional targets
# ---------------------------------------------------------------------------

def follower_ratio(follower: dict, master: dict) -> tuple[Optional[float], str]:
    """The follower's size ratio against the master, and how it was arrived at.

    Mirrors risk_engine.calculate_follower_quantity's intent without importing
    its rounding: this is for *explaining* an expected size in a report, so the
    ratio itself is the useful number. Returns (None, reason) when the account
    isn't configured well enough to derive one — which is itself a finding, not
    something to paper over with a 1:1 default.
    """
    mode = follower.get("allocation_mode")
    value = _num(follower.get("allocation_value"))
    if not mode:
        return None, "no allocation_mode set"
    if mode == "multiplier":
        return (value, "multiplier") if value else (None, "no allocation_value set")
    if mode == "fixed":
        return None, "fixed lot size (not a ratio)"
    if mode == "capital_pct":
        return None, "capital_pct (depends on price and leverage)"
    if mode == "auto_ratio":
        m_bal = _num(master.get("allocated_balance")) or _num(master.get("balance"))
        f_bal = _num(follower.get("allocated_balance")) or _num(follower.get("balance"))
        if m_bal and f_bal and m_bal > 0:
            return f_bal / m_bal, "auto_ratio (balance-derived)"
        return None, "auto_ratio but a balance reads 0"
    return None, f"unknown allocation_mode {mode!r}"


def expected_lots(master_lots: float, follower: dict, master: dict) -> tuple[Optional[float], str]:
    """The lots this follower *should* have filled against a master order.

    Opens floor and closes ceil in the live sizing path; a report can't know
    which a given order was, so it floors and leans on SHORT_LOT_TOLERANCE to
    absorb the 1-lot difference. Better to under-report a shortfall by a lot than
    to raise a mismatch on every single row because of rounding.
    """
    mode = follower.get("allocation_mode")
    if mode == "fixed":
        v = _num(follower.get("allocation_value"))
        return (float(v), "fixed lot size") if v else (None, "no allocation_value set")
    ratio, why = follower_ratio(follower, master)
    if ratio is None:
        return None, why
    return max(1.0, math.floor(master_lots * ratio)), why


# ---------------------------------------------------------------------------
# Engine records — annotation only
# ---------------------------------------------------------------------------

def _chunks(items: list, size: int = _IN_CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_engine_legs(db, master_order_ids: list[str], follower_order_ids: list[str]) -> dict:
    """Recorded legs for these orders, indexed for lookup.

    Fetched by ORDER ID rather than by created_at window on purpose. A leg is
    written when the copy is attempted, which for a master order placed just
    before midnight can be on the other side of the day boundary — a time-window
    query would drop exactly the rows a boundary-crossing trade needs.

    Both history paths are covered:
      * the resting-order path writes trade_copies.master_order_id directly;
      * the fill path reaches its legs through trades.master_trade_id, which
        holds the master's bare order id (its order-stage sibling uses the
        'ord:' prefix).

    Returns {"by_master": {master_order_id: [leg]}, "by_follower": {follower_order_id: leg}}.
    """
    by_master: dict[str, list] = {}
    by_follower: dict[str, dict] = {}
    if db is None:
        return {"by_master": by_master, "by_follower": by_follower}

    legs: list[dict] = []
    cols = ("id, trade_id, account_id, master_order_id, follower_order_id, status, "
            "quantity, requested_quantity, execution_price, slippage_pct, "
            "execution_time_ms, failure_reason, created_at")

    # Route 1 — legs that name the master order outright.
    for chunk in _chunks([str(i) for i in master_order_ids]):
        try:
            res = db.table("trade_copies").select(cols).in_("master_order_id", chunk).execute()
            legs.extend(res.data or [])
        except Exception as e:
            logger.warning(f"fill_compare: leg lookup by master_order_id failed: {e}")

    # Route 2 — legs reached via their parent trade row (the fill path).
    trade_ids: dict[str, str] = {}   # trades.id -> master order id
    wanted = [str(i) for i in master_order_ids]
    wanted += [f"ord:{i}" for i in wanted]
    for chunk in _chunks(wanted):
        try:
            res = db.table("trades").select("id, master_trade_id").in_("master_trade_id", chunk).execute()
            for r in res.data or []:
                mid = str(r.get("master_trade_id") or "")
                trade_ids[r["id"]] = mid[4:] if mid.startswith("ord:") else mid
        except Exception as e:
            logger.warning(f"fill_compare: trade lookup failed: {e}")
    for chunk in _chunks(list(trade_ids.keys())):
        try:
            res = db.table("trade_copies").select(cols).in_("trade_id", chunk).execute()
            for leg in res.data or []:
                if not leg.get("master_order_id"):
                    leg["master_order_id"] = trade_ids.get(leg.get("trade_id"))
                legs.append(leg)
        except Exception as e:
            logger.warning(f"fill_compare: leg lookup by trade_id failed: {e}")

    # Route 3 — legs that name a follower order we saw fill. Catches a leg whose
    # master link is missing, which would otherwise leave the follower's fill
    # looking like an unexplained extra trade.
    for chunk in _chunks([str(i) for i in follower_order_ids]):
        try:
            res = db.table("trade_copies").select(cols).in_("follower_order_id", chunk).execute()
            legs.extend(res.data or [])
        except Exception as e:
            logger.warning(f"fill_compare: leg lookup by follower_order_id failed: {e}")

    seen: set = set()
    for leg in legs:
        if leg.get("id") in seen:
            continue
        seen.add(leg.get("id"))
        mid = leg.get("master_order_id")
        if mid:
            by_master.setdefault(str(mid), []).append(leg)
        foid = leg.get("follower_order_id")
        if foid:
            by_follower[str(foid)] = leg
    return {"by_master": by_master, "by_follower": by_follower}


# ---------------------------------------------------------------------------
# The comparison itself
# ---------------------------------------------------------------------------

def _delay_ms(master_g: dict, follower_g: dict) -> Optional[float]:
    """Follower's first fill minus the master's first fill, in milliseconds.

    Can legitimately be NEGATIVE: both accounts rest a limit at the same price,
    so the follower's can trade first. Reported as-is rather than clamped to 0 —
    a clamp would quietly turn "these filled simultaneously" into a fictional
    lag, and the sign is exactly what tells you the mirror wasn't chasing.
    """
    m, f = master_g.get("first_ts"), follower_g.get("first_ts")
    if not m or not f:
        return None
    return round((f - m).total_seconds() * 1000.0, 1)


def _infer_match(master_g: dict, candidates: list[dict], taken: set) -> Optional[dict]:
    """Nearest unclaimed follower fill on the same symbol and side."""
    best, best_gap = None, None
    m_ts = master_g.get("first_ts")
    for g in candidates:
        if g["order_id"] in taken:
            continue
        if g.get("symbol") != master_g.get("symbol") or g.get("side") != master_g.get("side"):
            continue
        if not m_ts or not g.get("first_ts"):
            continue
        gap = abs((g["first_ts"] - m_ts).total_seconds())
        if gap > INFER_WINDOW_SEC:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = g, gap
    return best


def _classify(filled: float, target: Optional[float], leg: Optional[dict], matched: bool) -> tuple[str, str]:
    """(verdict, note) for one follower against one master order."""
    leg_status = (leg or {}).get("status") or ""
    reason = (leg or {}).get("failure_reason") or ""

    if not matched or filled <= 0:
        if leg_status in DELIBERATE_STATUSES:
            return "skipped", reason or "engine skipped this copy deliberately"
        if leg_status == "failed":
            return "missing", reason or "copy failed"
        if leg_status == "pending":
            # A mirrored order that is resting on the exchange, unfilled. Not a
            # miss yet — it is doing what the master's own resting order is doing.
            return "resting", reason or "mirrored order placed, not filled"
        return "missing", "no follower fill and no copy recorded"

    if target is None:
        return "unsized", "follower filled, but no proportional target could be derived"
    if filled + SHORT_LOT_TOLERANCE < target:
        return "short", f"filled {filled:g} of {target:g} lots"
    if filled > target + SHORT_LOT_TOLERANCE:
        return "over", f"filled {filled:g} against a target of {target:g} lots"
    return "matched", ""


# A row is only as good as its worst leg, so verdicts are ranked and the row
# takes the worst one present.
_SEVERITY = {"matched": 0, "skipped": 1, "resting": 2, "unsized": 3,
             "over": 4, "short": 5, "missing": 6}

MISMATCH_VERDICTS = {"missing", "short", "over"}


async def compare(
    accounts: list[dict],
    start: datetime,
    end: datetime,
    db=None,
) -> dict:
    """Compare master vs follower fills over a window for ONE owner's accounts.

    `accounts` is that owner's full account list (master + followers) with
    credentials. All accounts are read concurrently — a follower per master is
    the normal shape, and reading them in series made the page feel broken.
    """
    master = next((a for a in accounts if a.get("is_master")), None)
    followers = [a for a in accounts if not a.get("is_master")]

    window = {
        "start": _iso(start),
        "end": _iso(end),
        "timezone": "Asia/Kolkata (IST)",
    }
    if not master:
        return {
            "window": window, "master": None, "followers": [], "rows": [],
            "unmatched_follower_fills": [], "summary": _empty_summary(),
            "warnings": ["No master account configured — nothing to compare against."],
        }

    results = await asyncio.gather(
        fetch_account_fills(master, start, end),
        *[fetch_account_fills(f, start, end) for f in followers],
    )
    master_res, follower_res = results[0], list(results[1:])

    warnings: list[str] = []
    if master_res["error"]:
        warnings.append(f"Master '{master.get('name')}' fills unreadable: {master_res['error']}")
    for f, r in zip(followers, follower_res):
        if r["error"]:
            warnings.append(f"Follower '{f.get('name')}' fills unreadable: {r['error']}")

    master_groups = master_res["groups"]
    legs = load_engine_legs(
        db,
        [g["order_id"] for g in master_groups],
        [g["order_id"] for f in follower_res for g in f["groups"]],
    )

    # Per-follower working state: its fills by order id, and which ones have been
    # claimed by a master row (so two master orders can't both claim one fill).
    f_state = []
    for f, r in zip(followers, follower_res):
        ratio, ratio_why = follower_ratio(f, master)
        f_state.append({
            "account": f,
            "groups": r["groups"],
            "by_order": {g["order_id"]: g for g in r["groups"]},
            "taken": set(),
            "error": r["error"],
            "ratio": round(ratio, 6) if ratio is not None else None,
            "ratio_basis": ratio_why,
        })

    rows = []
    for mg in master_groups:
        mid = mg["order_id"]
        mid_legs = {str(l.get("account_id")): l for l in legs["by_master"].get(mid, [])}
        row_legs = []
        for st in f_state:
            acc = st["account"]
            leg = mid_legs.get(str(acc["id"]))

            fg, link = None, None
            # 1. Recorded leg naming the follower order.
            foid = (leg or {}).get("follower_order_id")
            if foid and str(foid) in st["by_order"]:
                fg, link = st["by_order"][str(foid)], "linked"
            # 2. No usable link — fall back to symbol/side/time proximity.
            if fg is None:
                inferred = _infer_match(mg, st["groups"], st["taken"])
                if inferred is not None:
                    fg, link = inferred, "inferred"
                    # An inferred fill may carry its own leg, reached by follower
                    # order id; prefer that leg's reason text over none at all.
                    leg = leg or legs["by_follower"].get(inferred["order_id"])
            if fg is not None:
                st["taken"].add(fg["order_id"])

            filled = float(fg["lots"]) if fg else 0.0
            requested = _num((leg or {}).get("requested_quantity"))
            if requested is not None:
                target, target_basis = requested, "recorded (what the engine asked for)"
            else:
                target, target_basis = expected_lots(mg["lots"], acc, master)
                target_basis = f"derived — {target_basis}"

            verdict, note = _classify(filled, target, leg, fg is not None)
            # An account we could not read must never be reported as "missing" —
            # that asserts a fact nobody established. Say so instead.
            if st["error"] and fg is None:
                verdict, note = "unreadable", "follower fills could not be read"

            row_legs.append({
                "account_id": acc["id"],
                "account_name": acc.get("name"),
                "verdict": verdict,
                "note": note,
                "link": link,
                "follower_order_id": fg["order_id"] if fg else None,
                "filled_lots": filled if fg else None,
                "target_lots": target,
                "target_basis": target_basis,
                "avg_price": fg.get("avg_price") if fg else None,
                "first_fill_at": _iso(fg.get("first_ts")) if fg else None,
                "delay_ms": _delay_ms(mg, fg) if fg else None,
                "slippage_pct": _num((leg or {}).get("slippage_pct")),
                "place_latency_ms": _num((leg or {}).get("execution_time_ms")),
                "leg_status": (leg or {}).get("status"),
                "leg_reason": (leg or {}).get("failure_reason"),
            })

        worst = max((l["verdict"] for l in row_legs),
                    key=lambda v: _SEVERITY.get(v, 3), default="matched")
        rows.append({
            "master_order_id": mid,
            "symbol": mg.get("symbol"),
            "side": mg.get("side"),
            "master_lots": mg["lots"],
            "master_avg_price": mg.get("avg_price"),
            "master_fill_count": mg["fill_count"],
            "master_first_fill_at": _iso(mg.get("first_ts")),
            "master_last_fill_at": _iso(mg.get("last_ts")),
            "order_type": mg.get("order_type"),
            "status": worst,
            "legs": row_legs,
        })

    # Follower fills no master order claimed. Not automatically wrong — a mirror
    # of a master order from just before the window opens here, and the desk does
    # place the occasional manual order — but it is unexplained by definition, so
    # it gets listed rather than counted as agreement.
    unmatched = []
    for st in f_state:
        for g in st["groups"]:
            if g["order_id"] in st["taken"]:
                continue
            leg = legs["by_follower"].get(g["order_id"])
            unmatched.append({
                "account_id": st["account"]["id"],
                "account_name": st["account"].get("name"),
                "follower_order_id": g["order_id"],
                "symbol": g.get("symbol"),
                "side": g.get("side"),
                "lots": g["lots"],
                "avg_price": g.get("avg_price"),
                "first_fill_at": _iso(g.get("first_ts")),
                "master_order_id": (leg or {}).get("master_order_id"),
                "explanation": (
                    "mirrors a master order outside this window"
                    if leg else "no copy record — placed outside the engine?"
                ),
            })
    unmatched.sort(key=lambda u: u["first_fill_at"] or "")

    return {
        "window": window,
        "master": {
            "id": master["id"],
            "name": master.get("name"),
            "environment": master.get("environment"),
            "balance": _num(master.get("balance")),
            "allocated_balance": _num(master.get("allocated_balance")),
            "order_count": len(master_groups),
            "fill_count": master_res["raw_count"],
            "lots": round(sum(g["lots"] for g in master_groups), 8),
            "unreadable": bool(master_res["error"]),
        },
        "followers": [{
            "id": st["account"]["id"],
            "name": st["account"].get("name"),
            "status": st["account"].get("status"),
            "allocation_mode": st["account"].get("allocation_mode"),
            "allocation_value": _num(st["account"].get("allocation_value")),
            "ratio": st["ratio"],
            "ratio_basis": st["ratio_basis"],
            "order_count": len(st["groups"]),
            "lots": round(sum(g["lots"] for g in st["groups"]), 8),
            "unreadable": bool(st["error"]),
        } for st in f_state],
        "rows": rows,
        "unmatched_follower_fills": unmatched,
        "summary": summarise(rows, unmatched, f_state),
        "warnings": warnings,
    }


def _empty_summary() -> dict:
    return {
        "master_orders": 0, "matched_rows": 0, "mismatched_rows": 0,
        "legs": 0, "by_verdict": {}, "errors": 0,
        "avg_delay_ms": None, "median_delay_ms": None, "max_delay_ms": None,
        "p95_delay_ms": None, "delay_samples": 0,
        "match_rate_pct": 100.0, "per_follower": [], "unmatched_follower_fills": 0,
    }


def summarise(rows: list[dict], unmatched: list[dict], f_state: list[dict]) -> dict:
    """Headline figures: match rate, delay distribution, error count.

    Delay is reported as median and p95 alongside the mean. The mean alone is a
    poor summary here — one mirrored limit that rested for four minutes before
    filling drags it into uselessness while the typical copy landed in under a
    second — and the daily report is read as "is this normal?", which is a
    question about the typical case and the tail, not the average.
    """
    all_legs = [l for r in rows for l in r["legs"]]
    by_verdict: dict[str, int] = {}
    for l in all_legs:
        by_verdict[l["verdict"]] = by_verdict.get(l["verdict"], 0) + 1

    delays = sorted(l["delay_ms"] for l in all_legs if l.get("delay_ms") is not None)

    def pct(p: float) -> Optional[float]:
        if not delays:
            return None
        idx = min(len(delays) - 1, max(0, int(round((len(delays) - 1) * p))))
        return delays[idx]

    mismatched = sum(1 for r in rows if r["status"] in MISMATCH_VERDICTS)
    # "Errors" is the number the desk asks for each morning, so it counts only
    # things that need a human: a leg that is short, missing or oversized. A
    # deliberate skip, a resting order and an unreadable account are surfaced on
    # their own so they can't be mistaken for silent agreement.
    errors = sum(by_verdict.get(v, 0) for v in MISMATCH_VERDICTS)

    per_follower = []
    for st in f_state:
        fid = st["account"]["id"]
        mine = [l for l in all_legs if l["account_id"] == fid]
        f_delays = sorted(l["delay_ms"] for l in mine if l.get("delay_ms") is not None)
        counts: dict[str, int] = {}
        for l in mine:
            counts[l["verdict"]] = counts.get(l["verdict"], 0) + 1
        graded = [l for l in mine if l["verdict"] in MISMATCH_VERDICTS or l["verdict"] == "matched"]
        per_follower.append({
            "account_id": fid,
            "account_name": st["account"].get("name"),
            "ratio": st["ratio"],
            "legs": len(mine),
            "by_verdict": counts,
            "errors": sum(counts.get(v, 0) for v in MISMATCH_VERDICTS),
            "match_rate_pct": (
                round(counts.get("matched", 0) / len(graded) * 100, 2) if graded else None
            ),
            "avg_delay_ms": round(sum(f_delays) / len(f_delays), 1) if f_delays else None,
            "median_delay_ms": f_delays[len(f_delays) // 2] if f_delays else None,
            "max_delay_ms": max(f_delays) if f_delays else None,
            "filled_lots": round(sum(l["filled_lots"] or 0 for l in mine), 8),
            "unreadable": bool(st["error"]),
        })

    graded_rows = [r for r in rows if r["status"] in MISMATCH_VERDICTS or r["status"] == "matched"]
    return {
        "master_orders": len(rows),
        "matched_rows": sum(1 for r in rows if r["status"] == "matched"),
        "mismatched_rows": mismatched,
        "legs": len(all_legs),
        "by_verdict": by_verdict,
        "errors": errors,
        "avg_delay_ms": round(sum(delays) / len(delays), 1) if delays else None,
        "median_delay_ms": pct(0.5),
        "p95_delay_ms": pct(0.95),
        "max_delay_ms": delays[-1] if delays else None,
        "delay_samples": len(delays),
        "match_rate_pct": (
            round(sum(1 for r in graded_rows if r["status"] == "matched") / len(graded_rows) * 100, 2)
            if graded_rows else 100.0
        ),
        "per_follower": per_follower,
        "unmatched_follower_fills": len(unmatched),
    }


# ---------------------------------------------------------------------------
# Entry points that resolve accounts themselves
# ---------------------------------------------------------------------------

def owner_accounts(db, owner_id: str) -> list[dict]:
    """One owner's accounts, credentials included (needed to read the exchange)."""
    try:
        res = db.table("accounts").select("*").eq("owner_id", owner_id).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"fill_compare: could not load accounts for {owner_id}: {e}")
        return []


async def compare_for_owner(db, owner_id: str, start: datetime, end: datetime) -> dict:
    accounts = owner_accounts(db, owner_id)
    out = await compare(accounts, start, end, db=db)
    out["owner_id"] = owner_id
    return out


async def compare_for_day(db, owner_id: str, day: date) -> dict:
    start, end = ist_day_bounds(day)
    out = await compare_for_owner(db, owner_id, start, end)
    out["window"]["date"] = day.isoformat()
    return out
