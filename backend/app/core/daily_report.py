"""The daily "do the orders match?" report — Telegram summary, CSV, shareable doc.

Renders the ORDER-level comparison (app.core.order_compare). It deliberately no
longer reports net position as the verdict: the 15s reconciler repairs position,
so a position-based report can only ever say "matched" — it scored 2026-08-27 as
a clean day while the engine was punching double-sized orders and unwinding them
a minute later. Orders record what the engine actually did, before anything
cleaned up after it.

Three renderings, because the question gets asked at three depths:

* Telegram — the one-screen answer, read on a phone. Leads with the mismatch
  count and names the worst offenders, since a bare count just means opening the
  full report to find out what broke.
* CSV — one row per (master order, follower), for sorting and pivoting.
* HTML — the shareable doc. Self-contained (no external CSS, fonts or scripts) so
  it survives being attached to a message, opened offline, or printed to PDF.

The scheduler runs once per IST day and is idempotent through a Redis marker, so
the restart on every deploy cannot re-send a report that already went out.
"""

import asyncio
import csv
import io
import logging
from datetime import date, datetime, timedelta
from html import escape

from app.core import fill_compare as fc
from app.core import order_compare as oc
from app.services import telegram_client as tg

logger = logging.getLogger(__name__)

# Sent-marker TTL. Comfortably longer than a day so a restart late in the day
# still sees the marker, short enough that the keys don't accumulate forever.
_SENT_TTL = 60 * 60 * 72

_VERDICT_LABEL = {
    "matched": "Matched",
    "oversized": "Over-punched",
    "undersized": "Under-punched",
    "missing": "Missing",
    "cancel_missed": "Cancel missed",
    "extra": "Unwanted fill",
    # Correct outcomes, so they read as Matched. The CSV keeps the raw verdict.
    "cancelled_ok": "Matched",
    "ladder": "Matched",
    "skipped": "Skipped",
    "unsized": "No target",
    "unreadable": "Unreadable",
}


def _ms(v) -> str:
    """Milliseconds → something a human reads at a glance."""
    if v is None:
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a < 1000:
        return f"{sign}{a:.0f} ms"
    if a < 60_000:
        return f"{sign}{a / 1000:.2f} s"
    return f"{sign}{a / 60_000:.1f} min"


def _lots(v) -> str:
    return "—" if v is None else f"{float(v):g}"


def _ratio(v) -> str:
    return "—" if v is None else f"{float(v):.5f}"


def _clock(iso) -> str:
    """ISO timestamp → HH:MM:SS in IST, the timezone the desk thinks in."""
    dt = fc.parse_ts(iso)
    return dt.astimezone(fc.IST).strftime("%H:%M:%S") if dt else "—"


def _bad_legs(cmp: dict) -> list:
    """Every mismatched leg, worst first, with its master order attached."""
    out = []
    for r in cmp.get("rows") or []:
        for l in r["legs"]:
            if l["verdict"] in oc.MISMATCH_VERDICTS:
                out.append((r, l))
    out.sort(key=lambda rl: -oc._SEVERITY.get(rl[1]["verdict"], 0))
    return out


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def render_telegram(cmp: dict, *, label: str = "") -> str:
    s = cmp["summary"]
    m = cmp.get("master") or {}
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    ok = s["errors"] == 0 and not cmp.get("warnings")

    head = "✅" if ok else ("🚨" if s["errors"] else "⚠️")
    lines = [
        f"{head} <b>Order Match Report — {day}</b>" + (f" · {escape(label)}" if label else ""),
        "",
        f"<b>Master</b> {escape(str(m.get('name') or '—'))}: "
        f"{m.get('order_count', 0)} orders, {_lots(m.get('lots'))} lots",
        f"<b>Match rate</b> {s['match_rate_pct']}%  "
        f"({s['matched']}/{s['legs']} order legs punched correctly)",
        f"<b>Unmatched</b> {s['errors']}"
        + ("  (" + ", ".join(
            f"{_VERDICT_LABEL.get(v, v).lower()} {s['by_verdict'][v]}"
            for v in ("oversized", "undersized", "missing", "cancel_missed", "extra")
            if s["by_verdict"].get(v)
          ) + ")" if s["errors"] else ""),
        f"<b>Time diff</b> median {_ms(s['median_time_diff_ms'])} · "
        f"avg {_ms(s['avg_time_diff_ms'])} · p95 {_ms(s['p95_time_diff_ms'])} · "
        f"max {_ms(s['max_time_diff_ms'])}",
    ]

    if s["per_follower"]:
        lines += ["", "<b>Per follower</b>"]
        for f in s["per_follower"]:
            rate = "—" if f["match_rate_pct"] is None else f"{f['match_rate_pct']}%"
            lines.append(
                f"· {escape(str(f['account_name']))} (ratio {_ratio(f['ratio'])}): "
                f"{rate} matched, {f['errors']} unmatched, "
                f"median {_ms(f['median_time_diff_ms'])}"
                + (" ⚠️ unreadable" if f["unreadable"] else "")
            )

    bad = _bad_legs(cmp)
    if bad:
        lines += ["", f"<b>Needs attention</b> ({len(bad)})"]
        for r, l in bad[:8]:
            lines.append(
                f"· {_clock(r['placed_at'])} {escape(str(r['symbol']))} {r['side']} "
                f"{_lots(r['master_lots'])} → {escape(str(l['account_name']))} "
                f"<b>{_VERDICT_LABEL.get(l['verdict'], l['verdict'])}</b>"
                + (f": {escape(l['note'], quote=False)}" if l.get("note") else "")
            )
        if len(bad) > 8:
            lines.append(f"· …and {len(bad) - 8} more")

    if cmp.get("extra_follower_orders"):
        lines += ["", f"<b>Orders on symbols the master never traded</b> "
                      f"{len(cmp['extra_follower_orders'])}"]

    if cmp.get("excluded_followers"):
        names = ", ".join(escape(str(e["name"])) for e in cmp["excluded_followers"])
        lines += ["", f"<i>Not graded (not being copied to): {names}</i>"]

    for w in (cmp.get("warnings") or [])[:4]:
        lines += [f"⚠️ {escape(w)}"]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "date", "time_ist", "symbol", "side", "order_type", "master_order_id",
    "master_lots", "master_filled", "master_state",
    "follower", "verdict", "follower_order_id",
    "punched_lots", "target_lots", "filled_lots", "follower_state",
    "ratio_actual", "ratio_target", "time_diff_ms", "link", "reason",
]


def render_csv(cmp: dict) -> str:
    """One row per (master order, follower).

    One row per LEG rather than a wide row per order with a column group per
    follower: the wide shape cannot be filtered or pivoted and breaks the moment
    a follower is added. Every row repeats its master-order columns so any single
    row stands on its own.
    """
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in cmp.get("rows") or []:
        for l in r["legs"]:
            w.writerow([
                day, _clock(r["placed_at"]), r["symbol"], r["side"],
                r.get("order_type") or "", r["master_order_id"],
                _lots(r["master_lots"]), _lots(r.get("master_filled")),
                r.get("master_state") or "",
                l["account_name"], l["verdict"], l.get("follower_order_id") or "",
                _lots(l.get("placed_lots")), _lots(l.get("target_lots")),
                _lots(l.get("filled_lots")), l.get("state") or "",
                _ratio(l.get("ratio_actual")), _ratio(l.get("ratio_target")),
                "" if l.get("time_diff_ms") is None else l["time_diff_ms"],
                l.get("link") or "", l.get("note") or l.get("leg_reason") or "",
            ])
    if cmp.get("extra_follower_orders"):
        w.writerow([])
        w.writerow(["FOLLOWER ORDERS ON SYMBOLS THE MASTER NEVER TRADED"])
        w.writerow(["time_ist", "follower", "symbol", "side", "lots", "filled",
                    "state", "order_id", "explanation"])
        for e in cmp["extra_follower_orders"]:
            w.writerow([_clock(e["placed_at"]), e["account_name"], e["symbol"],
                        e["side"], _lots(e["lots"]), _lots(e["filled"]),
                        e["state"], e["follower_order_id"], e["explanation"]])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML doc
# ---------------------------------------------------------------------------

_VERDICT_COLOR = {
    "matched": "#0f7b46", "ladder": "#0f7b46", "cancelled_ok": "#0f7b46",
    "oversized": "#b91c1c", "undersized": "#b45309", "missing": "#b91c1c",
    "cancel_missed": "#b91c1c", "extra": "#b45309",
    "skipped": "#64748b", "unsized": "#7c3aed", "unreadable": "#b91c1c",
}

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:#f6f7f9; color:#16181d;
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:1280px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; letter-spacing:-.01em; }
h2 { font-size:15px; margin:32px 0 10px; text-transform:uppercase;
     letter-spacing:.08em; color:#5b6472; }
.sub { color:#5b6472; font-size:13px; margin:0 0 24px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.card { background:#fff; border:1px solid #e3e6eb; border-radius:10px; padding:14px 16px; }
.card .k { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:#6b7280; }
.card .v { font-size:22px; font-weight:650; margin-top:4px; letter-spacing:-.02em; }
.card.bad .v { color:#b91c1c; } .card.ok .v { color:#0f7b46; }
table { width:100%; border-collapse:collapse; background:#fff; font-size:12.5px;
        border:1px solid #e3e6eb; border-radius:10px; overflow:hidden; }
th { text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.06em;
     color:#6b7280; padding:9px 10px; background:#fafbfc; border-bottom:1px solid #e3e6eb;
     white-space:nowrap; }
td { padding:8px 10px; border-bottom:1px solid #f0f2f5; vertical-align:top; }
tr:last-child td { border-bottom:none; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
tr.bad td { background:#fef2f2; }
tr.bad td:first-child { box-shadow: inset 3px 0 0 #b91c1c; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:11.5px; }
.pill { display:inline-block; padding:1px 7px; border-radius:20px; font-size:10.5px;
        font-weight:650; border:1px solid currentColor; white-space:nowrap; }
.muted { color:#6b7280; }
.warn { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412;
        padding:10px 14px; border-radius:8px; margin:0 0 8px; font-size:13px; }
.side-buy { color:#0f7b46; font-weight:650; } .side-sell { color:#b91c1c; font-weight:650; }
.scroll { overflow-x:auto; }
footer { margin-top:36px; color:#8b93a1; font-size:11.5px; }
@media print { body { background:#fff; padding:0; } .card, table { break-inside:avoid; } }
"""


def _pill(verdict: str) -> str:
    return (f'<span class="pill" style="color:{_VERDICT_COLOR.get(verdict, "#6b7280")}">'
            f'{escape(_VERDICT_LABEL.get(verdict, verdict))}</span>')


def _card(k: str, v: str, tone: str = "") -> str:
    return f'<div class="card {tone}"><div class="k">{escape(k)}</div><div class="v">{v}</div></div>'


def render_html(cmp: dict, *, label: str = "") -> str:
    s = cmp["summary"]
    m = cmp.get("master") or {}
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    title = f"Order Match Report — {day}"

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{escape(title)}</h1>",
        f"<p class='sub'>Master <strong>{escape(str(m.get('name') or '—'))}</strong> vs "
        f"{len(cmp.get('followers') or [])} active follower(s)"
        + (f" · {escape(label)}" if label else "")
        + f" · IST day · generated {datetime.now(fc.IST).strftime('%d %b %Y %H:%M IST')}</p>",
    ]

    for w in cmp.get("warnings") or []:
        parts.append(f"<div class='warn'>⚠️ {escape(w)}</div>")
    if cmp.get("excluded_followers"):
        names = ", ".join(f"<strong>{escape(str(e['name']))}</strong> ({escape(str(e['status']))})"
                          for e in cmp["excluded_followers"])
        parts.append(f"<div class='warn'>Not graded, because the engine does not copy to "
                     f"them: {names}.</div>")

    parts += [
        "<div class='cards'>",
        _card("Master orders", str(s["master_orders"])),
        _card("Order legs", str(s["legs"])),
        _card("Match rate", f"{s['match_rate_pct']}%",
              "ok" if s["match_rate_pct"] >= 100 else "bad"),
        _card("Unmatched", str(s["errors"]), "bad" if s["errors"] else "ok"),
        _card("Median time diff", _ms(s["median_time_diff_ms"])),
        _card("p95 time diff", _ms(s["p95_time_diff_ms"])),
        _card("Max time diff", _ms(s["max_time_diff_ms"])),
        "</div>",
    ]

    parts += ["<h2>Per follower</h2><div class='scroll'><table><thead><tr>",
              "<th>Follower</th><th class='n'>Ratio</th><th class='n'>Order legs</th>",
              "<th class='n'>Matched</th><th class='n'>Unmatched</th>",
              "<th class='n'>Median</th><th class='n'>Avg</th><th class='n'>Max</th>",
              "<th>Breakdown</th></tr></thead><tbody>"]
    for f in s["per_follower"]:
        # Group by DISPLAY label: ladder and cancelled_ok both read "Matched",
        # so counting them separately would print Matched twice.
        merged: dict = {}
        for k, v in f["by_verdict"].items():
            label = _VERDICT_LABEL.get(k, k)
            merged[label] = merged.get(label, 0) + v
        breakdown = " · ".join(f"{escape(k)} {v}" for k, v in sorted(merged.items())) or "—"
        rate = "—" if f["match_rate_pct"] is None else f"{f['match_rate_pct']}%"
        flag = " <span class='pill' style='color:#b91c1c'>Unreadable</span>" if f["unreadable"] else ""
        parts.append(
            f"<tr><td><strong>{escape(str(f['account_name']))}</strong>{flag}</td>"
            f"<td class='n mono' title=\"{escape(str(f.get('ratio_basis') or ''))}\">{_ratio(f['ratio'])}</td>"
            f"<td class='n'>{f['orders']}</td><td class='n'>{rate}</td>"
            f"<td class='n'>{f['errors']}</td>"
            f"<td class='n'>{_ms(f['median_time_diff_ms'])}</td>"
            f"<td class='n'>{_ms(f['avg_time_diff_ms'])}</td>"
            f"<td class='n'>{_ms(f['max_time_diff_ms'])}</td>"
            f"<td class='muted'>{breakdown}</td></tr>"
        )
    parts.append("</tbody></table></div>")

    parts += [f"<h2>Order comparison ({len(cmp.get('rows') or [])})</h2>",
              "<p class='sub'>One row per master order per follower. This is the verdict — "
              "it records what the engine did at the moment it did it, which is what a "
              "position check cannot show once the reconciler has tidied up.</p>",
              "<div class='scroll'><table><thead><tr><th>Time (IST)</th><th>Symbol</th>",
              "<th>Side</th><th class='n'>Master</th><th>Follower</th><th>Verdict</th>",
              "<th class='n'>Ratio</th><th class='n'>Target / Punched</th>",
              "<th class='n'>Time diff</th><th>Reason</th></tr></thead><tbody>"]
    rows = cmp.get("rows") or []
    if not rows:
        parts.append("<tr><td colspan='10' class='muted'>No master orders in this window.</td></tr>")
    for r in rows:
        span = max(1, len(r["legs"]))
        side_cls = "side-buy" if r["side"] == "buy" else "side-sell"
        for i, l in enumerate(r["legs"] or [None]):
            bad = bool(l and l["verdict"] in oc.MISMATCH_VERDICTS)
            cells = ""
            if i == 0:
                # Master lots carries its order state underneath rather than
                # spending a whole column on one word.
                cells = (
                    f"<td rowspan='{span}' class='mono'>{_clock(r['placed_at'])}</td>"
                    f"<td rowspan='{span}' class='mono'>{escape(str(r['symbol']))}</td>"
                    f"<td rowspan='{span}' class='{side_cls}'>{escape(str(r['side'] or '').upper())}</td>"
                    f"<td rowspan='{span}' class='n'>{_lots(r['master_lots'])}"
                    f"<div class='muted' style='font-size:10px'>"
                    f"{escape(str(r.get('master_state') or ''))}</div></td>"
                )
            if l is None:
                parts.append(f"<tr>{cells}<td colspan='6' class='muted'>No active followers.</td></tr>")
                continue
            punched = _lots(l.get("placed_lots"))
            punched_html = (f"<strong style='color:#b91c1c'>{punched}</strong>" if bad
                            else f"<strong>{punched}</strong>")
            parts.append(
                f"<tr class='{'bad' if bad else ''}'>{cells}"
                f"<td>{escape(str(l['account_name']))}</td>"
                f"<td>{_pill(l['verdict'])}</td>"
                f"<td class='n mono' title=\"target ratio {_ratio(l.get('ratio_target'))}\">"
                f"{_ratio(l.get('ratio_actual'))}</td>"
                f"<td class='n mono' title=\"{escape(str(l.get('target_basis') or ''))}\">"
                f"<span class='muted'>{_lots(l.get('target_lots'))}</span> / {punched_html}</td>"
                f"<td class='n'>{_ms(l.get('time_diff_ms'))}</td>"
                # Reason only where something is actually wrong — on a clean row
                # it is noise that makes the real ones harder to spot.
                f"<td class='muted'>"
                f"{escape(l.get('note') or l.get('leg_reason') or '') if bad else ''}</td></tr>"
            )
    parts.append("</tbody></table></div>")

    if cmp.get("extra_follower_orders"):
        parts += [f"<h2>Orders on symbols the master never traded "
                  f"({len(cmp['extra_follower_orders'])})</h2>",
                  "<div class='scroll'><table><thead><tr><th>Time (IST)</th><th>Follower</th>",
                  "<th>Symbol</th><th>Side</th><th class='n'>Lots</th><th class='n'>Filled</th>",
                  "<th>State</th><th>Order id</th></tr></thead><tbody>"]
        for e in cmp["extra_follower_orders"]:
            side_cls = "side-buy" if e["side"] == "buy" else "side-sell"
            parts.append(
                f"<tr><td class='mono'>{_clock(e['placed_at'])}</td>"
                f"<td>{escape(str(e['account_name']))}</td>"
                f"<td class='mono'>{escape(str(e['symbol']))}</td>"
                f"<td class='{side_cls}'>{escape(str(e['side'] or '').upper())}</td>"
                f"<td class='n'>{_lots(e['lots'])}</td><td class='n'>{_lots(e['filled'])}</td>"
                f"<td class='muted'>{escape(str(e['state']))}</td>"
                f"<td class='mono'>{escape(str(e['follower_order_id']))}</td></tr>"
            )
        parts.append("</tbody></table></div>")

    parts += [
        "<footer>Orders read from Delta Exchange (<code>/v2/orders/history</code>) for the "
        "master and every ACTIVE follower, matched per order. A follower's order should be "
        "<code>ceil(master lots × ratio)</code>; anything else is Over- or Under-punched, "
        "however tidy the resulting position looks — the reconciler repairs position, which "
        "is why position is not the verdict here. Cancels are compared too: the master "
        "cancelling and the follower not is a mismatch no fill would ever show. A rung of a "
        "laddered exit is judged on the ladder's total, not on its own, because the engine "
        "mirrors a ladder as one action. Jittered SL/TP orders are excluded — they are not "
        "one-for-one mirrors by design. Time diff is when the follower's order was placed "
        "minus the master's.</footer>",
        "</div></body></html>",
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# Scheduled send
# ---------------------------------------------------------------------------

def _owners_with_masters(db) -> list:
    """Owners that actually have a master, with their email for labelling."""
    try:
        accounts = (db.table("accounts").select("owner_id, is_master").execute().data) or []
    except Exception as e:
        logger.error(f"daily_report: could not list accounts: {e}")
        return []
    owners = sorted({a["owner_id"] for a in accounts if a.get("is_master") and a.get("owner_id")})
    emails = {}
    try:
        profiles = (db.table("profiles").select("id, email").execute().data) or []
        emails = {p["id"]: p.get("email") for p in profiles}
    except Exception as e:
        logger.warning(f"daily_report: could not resolve owner emails: {e}")
    return [{"owner_id": o, "email": emails.get(o) or o} for o in owners]


async def send_daily_report(db, day: date, redis=None, force: bool = False) -> dict:
    """Build and Telegram the report for `day`, for every owner with a master.

    Idempotent through a Redis marker: the backend restarts on every deploy, and
    a report that re-sends itself after each one is noise the desk learns to
    ignore — which is worse than not sending it. `force=True` is the manual
    "send it again now" path.
    """
    marker = f"daily_report:sent:{day.isoformat()}"
    if redis is not None and not force:
        try:
            if await redis.get(marker):
                return {"sent": False, "reason": "already sent for this day", "day": day.isoformat()}
        except Exception as e:
            logger.warning(f"daily_report: marker read failed, sending anyway: {e}")

    owners = _owners_with_masters(db)
    if not owners:
        return {"sent": False, "reason": "no owner has a master account", "day": day.isoformat()}

    sent, failures = 0, []
    for o in owners:
        try:
            cmp = await oc.compare_for_day(db, o["owner_id"], day)
            text = render_telegram(cmp, label=o["email"] if len(owners) > 1 else "")
            if await tg.send_message(text):
                sent += 1
            else:
                failures.append(f"{o['email']}: telegram send failed or disabled")
        except Exception as e:
            logger.error(f"daily_report: failed for owner {o['owner_id']}: {e}", exc_info=True)
            failures.append(f"{o['email']}: {e}")

    if sent and redis is not None:
        try:
            await redis.set(marker, "1", ex=_SENT_TTL)
        except Exception as e:
            logger.warning(f"daily_report: could not write sent marker: {e}")

    return {"sent": bool(sent), "owners": len(owners), "messages": sent,
            "failures": failures, "day": day.isoformat()}


async def daily_report_scheduler(db, redis, hour: int, minute: int) -> None:
    """Fire send_daily_report once per IST day at hour:minute.

    Which day gets reported follows from when the send fires: an evening or
    late-night send-time reports the day that is ending (today), a morning one
    reports yesterday. Both are "the day just traded" — the split at noon IST is
    what makes a single setting work for either preference, instead of a morning
    report cheerfully covering a session that is four hours old and still open.
    """
    logger.info(f"Daily order-match report scheduled for {hour:02d}:{minute:02d} IST.")
    while True:
        now = datetime.now(fc.IST)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        try:
            await asyncio.sleep((target - now).total_seconds())
        except asyncio.CancelledError:
            break
        fire = datetime.now(fc.IST)
        day = fire.date() if fire.hour >= 12 else (fire.date() - timedelta(days=1))
        try:
            res = await send_daily_report(db, day, redis=redis)
            logger.info(f"Daily order-match report: {res}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Daily order-match report failed: {e}", exc_info=True)
        # Step off the exact firing minute so the next loop can't compute a
        # zero-length wait and send twice.
        await asyncio.sleep(90)
