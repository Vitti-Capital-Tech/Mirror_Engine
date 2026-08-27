"""Telegram Bot notifications for alerts. Inert unless TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID are configured."""

import logging
import time
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_ICON = {"critical": "🚨", "error": "❗", "warning": "⚠️", "info": "ℹ️"}

# Suppress duplicate identical messages within a short window (e.g. the same
# mismatch raised on both master and follower).
_last = {"text": None, "ts": 0.0}
_DEDUPE_WINDOW = 10.0

# Window for collapsing IDENTICAL trade events (same account/symbol/side/lots).
# Short: two real trades of the same size within this window are rare, retries
# reporting the same fill twice are not.
DUP_EVENT_WINDOW = 45.0

# Default suppression for a PERSISTENT condition (a leg that can't be recovered,
# an order that keeps failing). Long, because these are re-evaluated every 15s and
# the state can hold for hours — you want one message, not one per pass. Paired
# with clear_alert() so the next genuine occurrence still gets through.
STATE_ALERT_WINDOW = 6 * 3600.0


def telegram_enabled() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


async def send_message(text: str) -> bool:
    """Send a plain-text message to the configured Telegram chat. Best-effort."""
    if not telegram_enabled():
        return False
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, json={
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        if r.status_code >= 400:
            logger.warning("Telegram send failed %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("Telegram send error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Trade notifications (open / close / mirror-failure)
# ---------------------------------------------------------------------------

# Keyed dedupe so a persistent failure retried every reconcile only alerts once
# per window (not every 30s). key -> last-sent ts.
_seen: dict = {}

# Backed by Redis so the suppression SURVIVES A RESTART. This dict alone lives in
# the worker process, and the backend reloads on every deploy — so a persistent
# condition (e.g. a leg that can't be recovered) re-alerted after every single
# reload, which is what made the drift notifications feel like spam.
_redis = None


def _r():
    global _redis
    if _redis is None:
        try:
            import redis.asyncio as aioredis
            _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning("Telegram dedupe: Redis unavailable, in-memory only: %s", e)
            _redis = False
    return _redis or None


def _dedupe(key: str, window: float) -> bool:
    """True if we should SEND (this key hasn't been sent within `window`)."""
    now = time.time()
    ts = _seen.get(key)
    if ts is not None and (now - ts) < window:
        return False
    _seen[key] = now
    return True


async def _should_send(key: str, window: float) -> bool:
    """Redis-backed `_dedupe`. SET NX EX is atomic, so concurrent reconcile passes
    can't both slip an alert through, and the window outlives a reload."""
    r = _r()
    if r is not None:
        try:
            return bool(await r.set(f"tgalert:{key}", "1", nx=True, ex=int(max(1, window))))
        except Exception:
            pass
    return _dedupe(key, window)


async def clear_alert(key: str) -> None:
    """Forget a suppressed condition so its NEXT occurrence alerts again.

    Call this when the underlying problem resolves (leg recovered, order finally
    mirrored). Without it an alert is merely rate-limited; with it, alerts become
    edge-triggered — one message when a problem starts, and a fresh one if it
    ever comes back — which is what you actually want to read."""
    _seen.pop(key, None)
    r = _r()
    if r is not None:
        try:
            await r.delete(f"tgalert:{key}")
        except Exception:
            pass


def _num(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):g}"
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# notify_open / notify_close are NO LONGER CALLED.
#
# They fired on every mirrored fill, which turned this channel into a running
# trade log. A channel that is mostly routine is one nobody reads closely enough
# to catch the messages that matter, and the messages that matter are failures
# and reconciler corrections. Kept — not deleted — because the reconciler paths
# were rewired onto notify_correction and turning plain trade notifications back
# on should stay a one-line change rather than an archaeology exercise.
# ---------------------------------------------------------------------------

async def notify_open(account: str, symbol: str, side: str, lots, price=None) -> None:
    """Follower opened / added to a position. UNUSED — see the note above."""
    if not telegram_enabled():
        return
    # Collapse identical repeats within a short window. A genuine second open of
    # the same size seconds later is almost always a retry or churn reporting the
    # same fill twice, not two real trades.
    if not await _should_send(f"open:{account}:{symbol}:{side}:{_num(lots)}", DUP_EVENT_WINDOW):
        return
    d = str(side).lower()
    icon = "🟢" if d in ("buy", "long") else "🔴"
    direction = "LONG" if d in ("buy", "long") else "SHORT"
    text = (f"{icon} <b>Position Opened</b> · {account}\n"
            f"<code>{symbol}</code>\n"
            f"{direction} · {_num(lots)} lot(s)" + (f" @ {_num(price)}" if price is not None else ""))
    await send_message(text)


async def notify_close(account: str, symbol: str, lots, price=None) -> None:
    """Follower closed / reduced a position. UNUSED — see the note above."""
    if not telegram_enabled():
        return
    if not await _should_send(f"close:{account}:{symbol}:{_num(lots)}", DUP_EVENT_WINDOW):
        return
    text = (f"✅ <b>Position Closed</b> · {account}\n"
            f"<code>{symbol}</code>\n"
            f"{_num(lots)} lot(s)" + (f" @ {_num(price)}" if price is not None else ""))
    await send_message(text)


async def notify_fail(account: str, symbol: str, side: str, lots, reason: str,
                      key: str = None, window: float = 3600.0) -> None:
    """Follower could NOT mirror an order. Deduped so a repeatedly-retried
    failure only alerts once per `window`."""
    if not telegram_enabled():
        return
    if key and not await _should_send(key, window):
        return
    lot_str = f" {_num(lots)} lot(s)" if lots not in (None, "", 0) else ""
    # A deliberate decision not to act is NOT a failure. Labelling a price-guard
    # skip "Mirror Failed" reads like something broke and trains you to ignore the
    # alert that matters — an order the exchange actually rejected.
    skipped = str(side).lower() in ("topup", "recover") or "drift" in (reason or "").lower()
    if skipped:
        text = (f"ℹ️ <b>Copy Skipped</b> · {account}\n"
                f"<code>{symbol}</code>\n"
                f"{lot_str.strip() or 'leg'} — {reason}\n"
                f"<i>Deliberate: nothing failed, the follower was left as-is.</i>")
    else:
        text = (f"⚠️ <b>Mirror Failed</b> · {account}\n"
                f"<code>{symbol}</code>\n"
                f"{str(side).title()}{lot_str} — {reason}")
    await send_message(text)


async def notify_correction(
    account: str,
    symbol: str,
    action: str,
    lots,
    *,
    held=None,
    target=None,
    master=None,
    why: str = "",
) -> None:
    """The RECONCILER had to correct a follower.

    This is the notification that matters most, and it did not exist. Routine
    open/close messages report the engine working; a reconciler correction reports
    the engine having got it WRONG and the safety net cleaning up — which is the
    only outward sign of a whole class of bug.

    Concretely: on 2026-08-27 the engine punched 62 lots against a target of 31
    and the reconciler trimmed 31 back a minute later. Positions ended correct, so
    nothing alerted and nothing looked wrong, while every affected order paid a
    round trip in fees. Had this message existed it would have read:

        Reconciler corrected Mini Prathav — TRIMMED 31 lots
        P-BTC-74500-280826 · held 62 -> 31 (master 2750)

    ...which names the bug on the first occurrence rather than the hundredth.

    held/target/master are the whole point: "trimmed 31" alone says the reconciler
    did something, "62 -> 31" says what the engine got wrong.
    """
    if not telegram_enabled():
        return
    # A correction that repeats identically is the same episode being re-detected
    # (the sweep runs every 15s), not a new one.
    key = f"fix:{account}:{symbol}:{action}:{_num(lots)}:{_num(held)}"
    if not await _should_send(key, DUP_EVENT_WINDOW):
        return

    detail = ""
    if held is not None and target is not None:
        detail = f"held {_num(held)} → {_num(target)}"
        if master is not None:
            detail += f" (master {_num(master)})"
    elif master is not None:
        detail = f"master {_num(master)}"

    lines = [
        f"🔧 <b>Reconciler corrected</b> · {account}",
        f"<code>{symbol}</code>",
        f"<b>{action}</b> {_num(lots)} lot(s)" + (f" — {detail}" if detail else ""),
    ]
    if why:
        lines.append(f"<i>{why}</i>")
    await send_message("\n".join(lines))


async def send_alert(alert: dict) -> None:
    """Format an alert row and push it to Telegram.

    Deduped on (type, account, message) so a condition re-detected on every sweep
    — a position mismatch, restored protection — reads as one notification rather
    than a stream of identical ones."""
    if not telegram_enabled():
        return
    level = (alert.get("level") or "info").lower()
    icon = _ICON.get(level, "•")
    atype = (alert.get("type") or "alert").replace("_", " ").title()
    msg = alert.get("message") or ""
    akey = f"alert:{alert.get('type')}:{alert.get('account_id') or ''}:{msg}"
    if not await _should_send(akey, alert.get("window") or STATE_ALERT_WINDOW):
        return
    text = f"{icon} <b>{atype}</b> [{level.upper()}]\n{msg}"

    now = time.time()
    if text == _last["text"] and (now - _last["ts"]) < _DEDUPE_WINDOW:
        return
    _last["text"], _last["ts"] = text, now
    await send_message(text)
