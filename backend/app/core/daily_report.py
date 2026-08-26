"""The daily "do the accounts match?" report — Telegram summary, CSV, and a
shareable HTML doc.

Three renderings of the same comparison, because they answer the question at
three different depths:

* Telegram — the one-screen answer. Read on a phone, first thing. It leads with
  the mismatch and error counts because those are the only reason to open the
  full report.
* CSV — one row per (master order, follower) leg, for anyone who wants to sort,
  filter or diff it in a spreadsheet.
* HTML — the shareable doc. Self-contained (no external CSS, no fonts, no
  scripts) so it survives being attached to a message, opened offline, or
  printed to PDF without turning into unstyled text.

The scheduler runs once per IST day and is idempotent through a Redis marker, so
a backend restart — which happens on every deploy — cannot re-send a report that
already went out.
"""

import asyncio
import csv
import io
import logging
from datetime import date, datetime, timedelta
from html import escape

from app.core import fill_compare as fc
from app.services import telegram_client as tg

logger = logging.getLogger(__name__)

# Sent-marker TTL. Comfortably longer than a day so a restart late in the day
# still sees the marker, short enough that the keys don't accumulate forever.
_SENT_TTL = 60 * 60 * 72

_VERDICT_LABEL = {
    "matched": "Matched",
    "ladder": "Ladder rung",
    "short": "Short fill",
    "missing": "Missing",
    "over": "Over-filled",
    "resting": "Resting",
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
    if v is None:
        return "—"
    return f"{float(v):g}"


def _clock(iso: str | None) -> str:
    """ISO timestamp → HH:MM:SS in IST, the timezone the desk thinks in."""
    dt = fc.parse_ts(iso)
    return dt.astimezone(fc.IST).strftime("%H:%M:%S") if dt else "—"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def render_telegram(cmp: dict, *, label: str = "") -> str:
    s = cmp["summary"]
    m = cmp.get("master") or {}
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    verdict_ok = s["errors"] == 0 and not cmp.get("warnings")

    head = "✅" if verdict_ok else ("🚨" if s["errors"] else "⚠️")
    lines = [
        f"{head} <b>Fill Match Report — {day}</b>" + (f" · {escape(label)}" if label else ""),
        "",
        f"<b>Master</b> {escape(str(m.get('name') or '—'))}: "
        f"{m.get('order_count', 0)} orders, {_lots(m.get('lots'))} lots",
        f"<b>Match rate</b> {s['match_rate_pct']}%  "
        f"({s['groups_matched']}/{s['groups']} symbol/side groups reconcile"
        f" · {s['master_orders']} master orders)",
        f"<b>Errors</b> {s['errors']}"
        + (f"  (missing {s['groups_by_verdict'].get('missing', 0)}, "
           f"short {s['groups_by_verdict'].get('short', 0)}, "
           f"over {s['groups_by_verdict'].get('over', 0)})" if s["errors"] else ""),
        f"<b>Delay</b> median {_ms(s['median_delay_ms'])} · "
        f"avg {_ms(s['avg_delay_ms'])} · p95 {_ms(s['p95_delay_ms'])} · "
        f"max {_ms(s['max_delay_ms'])}",
    ]

    if s["per_follower"]:
        lines += ["", "<b>Per follower</b>"]
        for f in s["per_follower"]:
            rate = "—" if f["match_rate_pct"] is None else f"{f['match_rate_pct']}%"
            lines.append(
                f"· {escape(str(f['account_name']))}: {rate} matched, "
                f"{f['errors']} err, median {_ms(f['median_delay_ms'])}, "
                f"{_lots(f['filled_lots'])} lots"
                + (" ⚠️ unreadable" if f["unreadable"] else "")
            )

    # The worst few rows, named. A count with no examples means opening the full
    # report to find out what broke, which defeats the point of the summary.
    bad = [g for g in cmp.get("groups") or [] if g["verdict"] in fc.MISMATCH_VERDICTS]
    if bad:
        lines += ["", f"<b>Needs attention</b> ({len(bad)})"]
        for g in bad[:8]:
            lines.append(
                f"· {escape(str(g['symbol']))} {g['side']} — "
                f"{escape(str(g['account_name']))} "
                f"{_VERDICT_LABEL.get(g['verdict'], g['verdict'])}: "
                f"{_lots(g['filled_lots'])} of {_lots(g['target_lots'])} lots "
                f"(master {_lots(g['master_lots'])}"
                + (f" over {g['master_orders']} rungs" if g["laddered"] else "")
                + ")"
            )
        if len(bad) > 8:
            lines.append(f"· …and {len(bad) - 8} more")

    if cmp.get("unmatched_follower_fills"):
        lines += ["", f"<b>Unexplained follower fills</b> {len(cmp['unmatched_follower_fills'])}"
                      " (symbols the master never traded today)"]

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
    "date", "master_order_id", "symbol", "side", "order_type",
    "master_lots", "master_avg_price", "master_first_fill_ist",
    "follower", "verdict", "link", "follower_order_id",
    "follower_lots", "target_lots", "target_basis", "follower_avg_price",
    "follower_first_fill_ist", "delay_ms", "slippage_pct",
    "place_latency_ms", "leg_status", "note",
]


def render_csv(cmp: dict) -> str:
    """One row per (master order, follower) leg.

    Deliberately one row per LEG rather than per order: a wide row with a column
    group per follower cannot be filtered or pivoted, and breaks the moment a
    follower is added. Every row repeats its master-order columns so any single
    row stands on its own.
    """
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    # Lead with the group reconciliation — it IS the verdict. A reader handed
    # only rung-by-rung rows would have to reconstruct the totals by hand, which
    # is exactly the mistake that made a laddered exit look like nine misses.
    if cmp.get("groups"):
        w.writerow(["SYMBOL/SIDE RECONCILIATION (the verdict — totals, ladder-safe)"])
        w.writerow(["date", "follower", "symbol", "side", "verdict", "master_orders",
                    "laddered", "master_lots", "target_lots", "filled_lots",
                    "target_basis", "note"])
        for g in cmp["groups"]:
            w.writerow([day, g["account_name"], g["symbol"], g["side"], g["verdict"],
                        g["master_orders"], "yes" if g["laddered"] else "no",
                        _lots(g["master_lots"]), _lots(g["target_lots"]),
                        _lots(g["filled_lots"]), g["target_basis"], g["note"]])
        w.writerow([])
        w.writerow(["PER-ORDER DETAIL (supporting rows)"])
    w.writerow(CSV_COLUMNS)
    for r in cmp["rows"]:
        for l in r["legs"]:
            w.writerow([
                day, r["master_order_id"], r["symbol"], r["side"], r.get("order_type") or "",
                _lots(r["master_lots"]), r.get("master_avg_price") or "",
                _clock(r["master_first_fill_at"]),
                l["account_name"], l["verdict"], l.get("link") or "",
                l.get("follower_order_id") or "",
                _lots(l.get("filled_lots")), _lots(l.get("target_lots")),
                l.get("target_basis") or "", l.get("avg_price") or "",
                _clock(l.get("first_fill_at")),
                "" if l.get("delay_ms") is None else l["delay_ms"],
                "" if l.get("slippage_pct") is None else l["slippage_pct"],
                "" if l.get("place_latency_ms") is None else l["place_latency_ms"],
                l.get("leg_status") or "", l.get("note") or "",
            ])
    # Unexplained follower fills belong in the same file. Shipped as a separate
    # attachment they get lost; as a trailing section they travel with the data
    # they contradict.
    if cmp.get("unmatched_follower_fills"):
        w.writerow([])
        w.writerow(["UNEXPLAINED FOLLOWER FILLS (no master order matched)"])
        w.writerow(["follower", "follower_order_id", "symbol", "side", "lots",
                    "avg_price", "first_fill_ist", "master_order_id", "explanation"])
        for u in cmp["unmatched_follower_fills"]:
            w.writerow([
                u["account_name"], u["follower_order_id"], u["symbol"], u["side"],
                _lots(u["lots"]), u.get("avg_price") or "", _clock(u["first_fill_at"]),
                u.get("master_order_id") or "", u["explanation"],
            ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML doc
# ---------------------------------------------------------------------------

_VERDICT_COLOR = {
    "matched": "#0f7b46", "short": "#b45309", "over": "#b45309",
    "missing": "#b91c1c", "resting": "#0369a1", "skipped": "#64748b",
    "unsized": "#7c3aed", "unreadable": "#b91c1c",
}

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:#f6f7f9; color:#16181d;
       font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:1180px; margin:0 auto; }
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
    title = f"Fill Match Report — {day}"

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{escape(title)}</h1>",
        f"<p class='sub'>Master <strong>{escape(str(m.get('name') or '—'))}</strong> vs "
        f"{len(cmp.get('followers') or [])} follower(s)"
        + (f" · {escape(label)}" if label else "")
        + f" · IST day · generated {datetime.now(fc.IST).strftime('%d %b %Y %H:%M IST')}</p>",
    ]

    for w in cmp.get("warnings") or []:
        parts.append(f"<div class='warn'>⚠️ {escape(w)}</div>")

    parts += [
        "<div class='cards'>",
        _card("Master orders", str(s["master_orders"])),
        _card("Groups reconciled", f"{s['groups_matched']}/{s['groups']}",
              "ok" if s["groups_matched"] == s["groups"] else "bad"),
        _card("Master lots", _lots(m.get("lots"))),
        _card("Match rate", f"{s['match_rate_pct']}%",
              "ok" if s["match_rate_pct"] >= 100 else "bad"),
        _card("Errors", str(s["errors"]), "bad" if s["errors"] else "ok"),
        _card("Median delay", _ms(s["median_delay_ms"])),
        _card("Avg delay", _ms(s["avg_delay_ms"])),
        _card("p95 delay", _ms(s["p95_delay_ms"])),
        _card("Max delay", _ms(s["max_delay_ms"])),
        "</div>",
    ]

    if cmp.get("excluded_followers"):
        names = ", ".join(
            f"<strong>{escape(str(e['name']))}</strong> ({escape(str(e['status']))})"
            for e in cmp["excluded_followers"]
        )
        parts.append(
            f"<div class='warn'>Not graded, because the engine does not copy to them: {names}. "
            "Any trading on these accounts is their own book, not a mirror.</div>"
        )

    # The verdict lives here rather than in the per-order table: a laddered exit
    # spreads one decision across many rungs, so totals per symbol and side are
    # the honest unit of comparison.
    if cmp.get("groups"):
        parts += ["<h2>Symbol / side reconciliation</h2>",
                  "<p class='sub'>The verdict. Totals per follower per symbol and side, so a "
                  "laddered exit is judged as one exit rather than rung by rung.</p>",
                  "<div class='scroll'><table><thead><tr><th>Follower</th><th>Symbol</th>",
                  "<th>Side</th><th>Verdict</th><th class='n'>Master lots</th>",
                  "<th class='n'>Rungs</th><th class='n'>Target</th><th class='n'>Filled</th>",
                  "<th>Note</th></tr></thead><tbody>"]
        for g in cmp["groups"]:
            side_cls = "side-buy" if g["side"] == "buy" else "side-sell"
            parts.append(
                f"<tr><td><strong>{escape(str(g['account_name']))}</strong></td>"
                f"<td class='mono'>{escape(str(g['symbol']))}</td>"
                f"<td class='{side_cls}'>{escape(str(g['side'] or '').upper())}</td>"
                f"<td>{_pill(g['verdict'])}</td>"
                f"<td class='n'>{_lots(g['master_lots'])}</td>"
                f"<td class='n'>{g['master_orders']}</td>"
                f"<td class='n' title=\"{escape(g['target_basis'])}\">{_lots(g['target_lots'])}</td>"
                f"<td class='n'>{_lots(g['filled_lots'])}</td>"
                f"<td class='muted'>{escape(g['note'] or '')}</td></tr>"
            )
        parts.append("</tbody></table></div>")

    # Per-follower rollup
    parts += ["<h2>Per follower</h2><div class='scroll'><table><thead><tr>",
              "<th>Follower</th><th class='n'>Ratio</th><th class='n'>Legs</th>",
              "<th class='n'>Matched</th><th class='n'>Errors</th><th class='n'>Lots</th>",
              "<th class='n'>Median delay</th><th class='n'>Avg delay</th>",
              "<th class='n'>Max delay</th><th>Breakdown</th></tr></thead><tbody>"]
    for f in s["per_follower"]:
        breakdown = " · ".join(
            f"{escape(_VERDICT_LABEL.get(k, k))} {v}" for k, v in sorted(f["by_verdict"].items())
        ) or "—"
        rate = "—" if f["match_rate_pct"] is None else f"{f['match_rate_pct']}%"
        ratio = "—" if f["ratio"] is None else f"{f['ratio']:.4f}"
        flag = " <span class='pill' style='color:#b91c1c'>Unreadable</span>" if f["unreadable"] else ""
        parts.append(
            f"<tr><td><strong>{escape(str(f['account_name']))}</strong>{flag}</td>"
            f"<td class='n'>{ratio}</td>"
            f"<td class='n'>{f['legs']}</td><td class='n'>{rate}</td>"
            f"<td class='n'>{f['errors']}</td><td class='n'>{_lots(f['filled_lots'])}</td>"
            f"<td class='n'>{_ms(f['median_delay_ms'])}</td>"
            f"<td class='n'>{_ms(f['avg_delay_ms'])}</td>"
            f"<td class='n'>{_ms(f['max_delay_ms'])}</td>"
            f"<td class='muted'>{breakdown}</td></tr>"
        )
    parts.append("</tbody></table></div>")

    # Full order-by-order comparison
    parts += [f"<h2>Order comparison ({len(cmp['rows'])})</h2><div class='scroll'>",
              "<table><thead><tr><th>Time (IST)</th><th>Symbol</th><th>Side</th>",
              "<th class='n'>Master lots</th><th class='n'>Master px</th>",
              "<th>Follower</th><th>Verdict</th><th class='n'>Filled</th>",
              "<th class='n'>Target</th><th class='n'>Px</th><th class='n'>Delay</th>",
              "<th>Link</th><th>Note</th></tr></thead><tbody>"]
    if not cmp["rows"]:
        parts.append("<tr><td colspan='13' class='muted'>No master fills in this window.</td></tr>")
    for r in cmp["rows"]:
        span = max(1, len(r["legs"]))
        side_cls = "side-buy" if r["side"] == "buy" else "side-sell"
        for i, l in enumerate(r["legs"] or [None]):
            cells = ""
            if i == 0:
                cells = (
                    f"<td rowspan='{span}' class='mono'>{_clock(r['master_first_fill_at'])}</td>"
                    f"<td rowspan='{span}' class='mono'>{escape(str(r['symbol']))}</td>"
                    f"<td rowspan='{span}' class='{side_cls}'>{escape(str(r['side'] or '').upper())}</td>"
                    f"<td rowspan='{span}' class='n'>{_lots(r['master_lots'])}</td>"
                    f"<td rowspan='{span}' class='n mono'>{r.get('master_avg_price') or '—'}</td>"
                )
            if l is None:
                parts.append(f"<tr>{cells}<td colspan='8' class='muted'>No followers configured.</td></tr>")
                continue
            parts.append(
                f"<tr>{cells}"
                f"<td>{escape(str(l['account_name']))}</td>"
                f"<td>{_pill(l['verdict'])}</td>"
                f"<td class='n'>{_lots(l.get('filled_lots'))}</td>"
                f"<td class='n'>{_lots(l.get('target_lots'))}</td>"
                f"<td class='n mono'>{l.get('avg_price') or '—'}</td>"
                f"<td class='n'>{_ms(l.get('delay_ms'))}</td>"
                f"<td class='muted'>{escape(l.get('link') or '—')}</td>"
                f"<td class='muted'>{escape(l.get('note') or '')}</td></tr>"
            )
    parts.append("</tbody></table></div>")

    if cmp.get("unmatched_follower_fills"):
        parts += [f"<h2>Unexplained follower fills ({len(cmp['unmatched_follower_fills'])})</h2>",
                  "<div class='scroll'><table><thead><tr><th>Time (IST)</th><th>Follower</th>",
                  "<th>Symbol</th><th>Side</th><th class='n'>Lots</th><th class='n'>Px</th>",
                  "<th>Order id</th><th>Explanation</th></tr></thead><tbody>"]
        for u in cmp["unmatched_follower_fills"]:
            side_cls = "side-buy" if u["side"] == "buy" else "side-sell"
            parts.append(
                f"<tr><td class='mono'>{_clock(u['first_fill_at'])}</td>"
                f"<td>{escape(str(u['account_name']))}</td>"
                f"<td class='mono'>{escape(str(u['symbol']))}</td>"
                f"<td class='{side_cls}'>{escape(str(u['side'] or '').upper())}</td>"
                f"<td class='n'>{_lots(u['lots'])}</td>"
                f"<td class='n mono'>{u.get('avg_price') or '—'}</td>"
                f"<td class='mono'>{escape(str(u['follower_order_id']))}</td>"
                f"<td class='muted'>{escape(u['explanation'])}</td></tr>"
            )
        parts.append("</tbody></table></div>")

    parts += [
        "<footer>Fills read from Delta Exchange (<code>/v2/fills</code>) for the master and every "
        "ACTIVE follower, then matched per order; the engine's own copy records annotate each leg "
        "but never define a match. The <strong>verdict is taken per symbol and side</strong>, not "
        "per order, because the master ladders an exit across many rungs and the engine mirrors it "
        "as one ladder — a rung with no fill of its own reads <em>Ladder rung</em>, not a miss, when "
        "the total reconciles. <em>linked</em> = a recorded copy ties the two orders together; "
        "<em>inferred</em> = matched on symbol, side and timing because no copy record linked "
        f"them (within {int(fc.INFER_WINDOW_SEC)}s). Delay is the follower's first fill minus the "
        "master's — negative means the follower traded first.</footer>",
        "</div></body></html>",
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# Scheduled send
# ---------------------------------------------------------------------------

def _owners_with_masters(db) -> list[dict]:
    """Owners that actually have a master, with their email for labelling."""
    try:
        accounts = (db.table("accounts").select("owner_id, is_master").execute().data) or []
    except Exception as e:
        logger.error(f"daily_report: could not list accounts: {e}")
        return []
    owners = sorted({a["owner_id"] for a in accounts if a.get("is_master") and a.get("owner_id")})
    emails: dict = {}
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
            cmp = await fc.compare_for_day(db, o["owner_id"], day)
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
    logger.info(f"Daily fill-match report scheduled for {hour:02d}:{minute:02d} IST.")
    while True:
        now = datetime.now(fc.IST)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            break
        fire = datetime.now(fc.IST)
        day = fire.date() if fire.hour >= 12 else (fire.date() - timedelta(days=1))
        try:
            res = await send_daily_report(db, day, redis=redis)
            logger.info(f"Daily fill-match report: {res}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Daily fill-match report failed: {e}", exc_info=True)
        # Step off the exact firing minute so the next loop can't compute a
        # zero-length wait and send twice.
        await asyncio.sleep(90)
