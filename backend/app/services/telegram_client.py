"""Telegram Bot notifications for alerts. Inert unless TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID are configured."""

import json
import logging
import time
import httpx

from app.config import settings
from app.services.paper_client import is_paper

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
# A STUCK position is re-detected every 10s, so it needs a window — but six
# hours would hide a second episode the same day. Half an hour: loud enough to
# act on, quiet enough to ignore while someone is already dealing with it.
STUCK_ALERT_WINDOW = 1800.0

# ONE message per episode, not one per hour.
#
# These conditions are re-evaluated every 15s and can hold all day, so a 30-60min
# window meant the same unchanged problem re-announcing itself all afternoon.
# What is wanted is edge-triggered: say it when it STARTS, stay quiet while it
# persists, say it again only if it comes back after resolving. clear_alert()
# provides that second half and is called wherever a condition is actually fixed.
#
# Not infinite, deliberately. If a clear_alert() is ever missed the condition
# would go permanently silent, which is a worse failure than one repeat — so this
# is a week: effectively "once" for any real episode, while guaranteeing a
# genuinely stuck problem cannot stay quiet forever.
ONCE_WINDOW = 7 * 24 * 3600.0
# Written as a name because this module is edited by scripts often enough that
# an escaped newline inside a generated literal has broken it before.
NEWLINE = chr(10)

# Alert types NOT forwarded to Telegram, because a better message already covers
# them. They still land in the database and the in-app Alert Feed.
#
#   position_mismatch — fires when a follower's size differs from its target,
#     which is exactly what the reconciler acts on. You would get the mismatch,
#     then a "Reconciler corrected" for the same event saying what was actually
#     done — or, when the reconciler declines, a "Left out of sync" saying so.
#     The mismatch alert is the least informative of the three and arrives first,
#     so it reads as the headline when it is really the footnote.
SILENT_ALERT_TYPES = {"position_mismatch"}


def telegram_enabled() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


# A Telegram group that gets upgraded to a SUPERGROUP changes its chat id, and
# every send to the old one fails with 400 "group chat was upgraded to a
# supergroup chat". It is a silent killer: the engine thinks it alerted, the
# failure is a WARNING nobody reads, and the desk simply stops hearing from it.
#
# Exactly that happened on 2026-11-11 — the two reconciler corrections at
# 03:16:24 and 03:16:25 both bounced, which is why nobody was told the follower
# had been left holding unprotected positions.
#
# Telegram hands back the new id in parameters.migrate_to_chat_id, so follow it
# and remember it for the rest of the process. The .env should still be updated
# (this is lost on restart), and the log says so loudly enough to notice.
_migrated_chat_id = None


def _chat_id():
    return _migrated_chat_id or settings.TELEGRAM_CHAT_ID


# ── Per-holder routing ───────────────────────────────────────────────────
#
# One desk watches everyone's accounts; a client should see their own and
# nothing else — not the other holders' symbols, sizes or balances, which is
# what a shared alert group leaks.
#
# Keyed on owner_id, never on account name. Pratik's accounts are called
# "Dhwani" and "Dhwani1": matching on the name would be unreadable, and a
# rename would quietly route his alerts back to the main group with nobody
# noticing until he asked why he had stopped hearing anything.
_routes_cache: dict | None = None
_owner_of: dict = {}          # account name -> owner_id
_owner_by_id: dict = {}       # account id   -> owner_id
_owner_of_ts: float = 0.0
_OWNER_TTL = 60.0

# PAPER ACCOUNTS DO NOT TELEGRAM. A paper follower is a testing instrument and
# its failures are expected and frequent -- they are the point. Letting them into
# the desk's group would mix invented problems with real ones in the one channel
# that has to stay worth reading. Desk call: paper appears in the daily REPORT,
# where it can be looked at deliberately, and nowhere else.
#
# Filled from the same 60s account read the routing already does, so this costs
# no extra round trip. Both sets start empty, which means nothing is suppressed
# until the first read -- erring towards saying too much rather than silently
# swallowing a real alert.
_paper_names: set = set()
_paper_ids: set = set()


def _routes() -> dict:
    global _routes_cache
    if _routes_cache is None:
        raw = (settings.TELEGRAM_CHAT_ROUTES or "").strip()
        try:
            _routes_cache = json.loads(raw) if raw else {}
        except Exception as e:
            logger.error("TELEGRAM_CHAT_ROUTES is not valid JSON (%s) — "
                         "every alert will go to the main chat", e)
            _routes_cache = {}
    return _routes_cache


async def _chat_for(account: str | None = None, account_id: str | None = None) -> str:
    """The chat this account's alerts belong in; the main chat by default.

    Any failure here falls back to the main chat. An alert in the wrong group
    is a privacy problem; an alert in NO group is a trading problem, and the
    second is worse.
    """
    routes = _routes()
    if not routes or not (account or account_id):
        return _chat_id()
    global _owner_of, _owner_by_id, _owner_of_ts
    try:
        if time.time() - _owner_of_ts > _OWNER_TTL:
            from app.database import db
            rows = (db.table("accounts")
                    .select("id,name,owner_id,environment").execute().data) or []
            _owner_of = {r["name"]: str(r.get("owner_id") or "") for r in rows}
            _owner_by_id = {str(r["id"]): str(r.get("owner_id") or "") for r in rows}
            # Same trip, no extra round trip: which of these are paper.
            _paper_names.clear()
            _paper_ids.clear()
            for r in rows:
                if is_paper(r):
                    _paper_names.add(r.get("name"))
                    _paper_ids.add(str(r.get("id")))
            _owner_of_ts = time.time()
        owner = (_owner_by_id.get(str(account_id)) if account_id
                 else _owner_of.get(account, ""))
        return routes.get(owner or "", _chat_id())
    except Exception as e:
        logger.warning("Alert routing lookup failed for %s (%s) — using the main chat",
                       account, e)
        return _chat_id()


def _is_paper_account(account: str | None, account_id: str | None) -> bool:
    """Is this message about a paper account?

    Reads the cache the routing lookup already maintains. Never raises, and
    never refreshes on its own: if the cache has not been filled yet this
    returns False, so an unknown account is treated as real and its alert is
    sent. The two failure modes are not symmetrical -- a paper alert that slips
    into the group is noise, a real alert suppressed by mistake is a position
    nobody hears about.
    """
    try:
        if account_id is not None and str(account_id) in _paper_ids:
            return True
        return account is not None and account in _paper_names
    except Exception:
        return False


async def send_message(text: str, account: str | None = None,
                       account_id: str | None = None) -> bool:
    """Send a plain-text message to the chat this account belongs to."""
    if not telegram_enabled():
        return False
    # THE ONE GATE. Every notify_* in this module ends in send_message, so
    # suppressing paper here covers all of them -- including any added later,
    # which a per-function check would not.
    if _is_paper_account(account, account_id):
        logger.info("paper account %s - alert not sent to Telegram: %s",
                    account or account_id, text.replace(chr(10), " | ")[:160])
        return True
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

    async def _post(chat_id):
        async with httpx.AsyncClient(timeout=10.0) as c:
            return await c.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })

    try:
        r = await _post(await _chat_for(account, account_id))
        if r.status_code >= 400:
            # Follow a supergroup migration once, then retry this same message.
            new_id = None
            try:
                new_id = ((r.json() or {}).get("parameters") or {}).get("migrate_to_chat_id")
            except Exception:
                pass
            if new_id:
                global _migrated_chat_id
                _migrated_chat_id = new_id
                logger.error(
                    "Telegram chat was upgraded to a supergroup. Now sending to %s "
                    "— UPDATE TELEGRAM_CHAT_ID in .env, this is only remembered "
                    "until the next restart.", new_id,
                )
                r = await _post(new_id)
                if r.status_code < 400:
                    return True
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


# In-process fallback for the failure→correction link, mirroring what _dedupe
# does for suppression. Redis is preferred (it survives the reload on every
# deploy), but this must not be the kind of feature that silently stops working
# when Redis hiccups — a correction with no cause attached is a worse message,
# and there is no way to tell from the outside that it was meant to have one.
_FAIL_TTL = 1800.0
_last_fail: dict = {}


async def _remember_failure(account: str, symbol: str, reason: str) -> None:
    """Stash why a leg last failed, so a later correction can name the cause.

    The reconciler knows a leg was missing; it cannot know the exchange said
    "insufficient_margin". Correlating the two by hand across two messages is
    work the reader should not have to do.
    """
    if not reason:
        return
    key = f"lastfail:{account}:{symbol}"
    _last_fail[key] = (str(reason)[:200], time.time())
    r = _r()
    if r is None:
        return
    try:
        await r.set(key, str(reason)[:200], ex=int(_FAIL_TTL))
    except Exception:
        pass


async def _recent_failure(account: str, symbol: str) -> str:
    key = f"lastfail:{account}:{symbol}"
    r = _r()
    if r is not None:
        try:
            hit = await r.get(key)
            if hit:
                return hit
        except Exception:
            pass
    cached = _last_fail.get(key)
    if cached and (time.time() - cached[1]) < _FAIL_TTL:
        return cached[0]
    return ""


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
    await send_message(text, account=account)


async def notify_close(account: str, symbol: str, lots, price=None) -> None:
    """Follower closed / reduced a position. UNUSED — see the note above."""
    if not telegram_enabled():
        return
    if not await _should_send(f"close:{account}:{symbol}:{_num(lots)}", DUP_EVENT_WINDOW):
        return
    text = (f"✅ <b>Position Closed</b> · {account}\n"
            f"<code>{symbol}</code>\n"
            f"{_num(lots)} lot(s)" + (f" @ {_num(price)}" if price is not None else ""))
    await send_message(text, account=account)


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
    #
    # Kept, and load-bearing: this is the ONLY notification for a leg the
    # reconciler has knowingly abandoned (price drifted too far from the master's
    # entry to be worth chasing). No correction fires, because no correction
    # happened — the follower is deliberately left out of sync.
    skipped = str(side).lower() in ("topup", "recover") or "drift" in (reason or "").lower()
    if skipped:
        text = (f"ℹ️ <b>Left out of sync</b> · {account}\n"
                f"<code>{symbol}</code>\n"
                f"{lot_str.strip() or 'leg'} — {reason}\n"
                f"<i>Deliberate: nothing failed, the follower was left as-is.</i>")
    else:
        # Only a real failure is worth remembering as the cause of a later fix.
        await _remember_failure(account, symbol, reason)
        # THREE DIFFERENT PROBLEMS, THREE DIFFERENT HEADINGS. These all read
        # "Mirror Failed" until 2026-11-20, which flattened an order the
        # exchange rejected, the engine crashing on its own correction, and
        # the engine deliberately giving up into one indistinguishable
        # alert. The first is routine; the second means the code is broken;
        # the third means nothing more happens until a person looks. Nobody
        # can triage what all looks the same.
        _low = (reason or "").lower()
        if "itself crashed" in _low:
            _head = "🔥 <b>Engine error</b>"
        elif "not re-placing" in _low or "vanished each time" in _low:
            _head = "🛑 <b>Stopped retrying</b>"
        else:
            _head = "⚠️ <b>Mirror Failed</b>"
        text = (f"{_head} · {account}\n"
                f"<code>{symbol}</code>\n"
                f"{str(side).title()}{lot_str} — {reason}")
    await send_message(text, account=account)


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
        (f"<b>{action}</b> {_num(lots)} lot(s)" if lots is not None
         else f"<b>{action}</b>")
        # A cancelled stop or a restored SL/TP has no lot count. Printing
        # "1 lot(s)" for a stop order, or a bare "— lot(s)" for a missing
        # one, makes the alert read like a position change it is not.
        + (f" — {detail}" if detail else ""),
    ]
    if why:
        lines.append(f"<i>{why}</i>")
    # Cause and cure in one message, rather than leaving the reader to correlate
    # a "Mirror Failed" and a "Reconciler corrected" by symbol and timestamp.
    cause = await _recent_failure(account, symbol)
    if cause:
        lines.append(f"↳ earlier failure: <code>{cause}</code>")
    # `account=` was missing here and nowhere else in this module, so every
    # "Reconciler corrected" went to the MAIN chat rather than the owner's --
    # the one alert that reports the engine having got something WRONG, routed
    # away from the desk it happened on. It would also have slipped past the
    # paper gate in send_message. Fixed 2026-11-26.
    await send_message("\n".join(lines), account=account)


async def notify_stuck(account: str, symbol: str, kind: str, held, target,
                       master=None, seconds: float = 0, why: str | None = None) -> None:
    """A discrepancy the reconciler has SEEN but not resolved.

    Every alert before this one announced something the engine DID. Nothing
    announced what it failed to do, which is how four separate faults in one
    day were found by people staring at the exchange instead of by us.

    The worst of them, 2026-11-19: a follower was doubled to 5539 against a
    target of 2771 and the engine knew eight seconds later —

        04:10:52  over-exposed — holds 5539, target 2789 (excess 2750)

    — then logged that same line thirteen times across two minutes and told
    nobody. The information existed; it never left the box. The client found it
    himself.

    Fired only once a discrepancy OUTLIVES the reconciler's normal correction
    time, so a book caught mid-flight stays quiet. By then it means the
    reconciler has tried and not succeeded, which is exactly when a human
    should look.
    """
    if not telegram_enabled():
        return
    # One per (account, symbol, kind) per window — a stuck position re-detects
    # every 10s and must not become a stream nobody reads.
    if not await _should_send(f"stuck:{account}:{symbol}:{kind}", STUCK_ALERT_WINDOW):
        return

    _k = {
        "trim": "HOLDS TOO MUCH",
        "topup": "HOLDS TOO LITTLE",
        "open": "LEG MISSING",
        "close": "LEG THE MASTER HAS EXITED",
    }.get(kind, kind.upper())

    lines = [
        f"⏳ <b>Not corrected</b> · {account}",
        f"<code>{symbol}</code>",
        f"<b>{_k}</b> — holds {_num(held)}, should hold {_num(target)}"
        + (f" (master {_num(master)})" if master is not None else ""),
        f"<i>unresolved for {int(seconds)}s — the reconciler has tried and not "
        f"settled it</i>",
    ]
    if why:
        lines.append(f"↳ {why}")
    await send_message("\n".join(lines), account=account)


async def notify_mismatch(account: str, symbol: str, kind: str,
                          follower=None, master=None, target=None,
                          detail: str | None = None, acting: bool = True) -> None:
    """Something does not match between master and follower, said at the moment
    the engine is sure of it.

    WHY THIS EXISTS SEPARATELY FROM EVERY OTHER ALERT. The engine's rule was
    "only speak when a human must act", so anything it fixed itself stayed
    silent and anything it had merely SEEN waited 60s. That is defensible for
    noise and indefensible for trust: on 2026-11-20 a client watched four legs
    sit off-ratio with nothing said about two of them, and had no way to tell a
    deliberate hold from a broken copier. Desk call: if it does not match, say
    so, and say so again when it is fixed.

    TIMING. Not on the first sighting. A book read mid-flight -- an order half
    filled, a position still settling -- looks wrong for one pass and then is
    not, and an alert on that is a false alarm that teaches people to ignore
    the channel. Fired on the SECOND consecutive sighting, which is the same
    moment the engine itself becomes willing to act on it: ~10-20s, not 60.

    ONE PER EPISODE. The sweep re-detects every 10s. The throttle is cleared
    when the discrepancy settles, so the next occurrence speaks again.
    """
    if not telegram_enabled():
        return
    if not await _should_send(f"mismatch:{account}:{symbol}:{kind}", ONCE_WINDOW):
        return

    _k = {
        "size": "POSITION SIZE",
        "missing": "LEG MISSING ON THE FOLLOWER",
        "extra": "LEG THE MASTER DOES NOT HOLD",
        "stop": "STOP ORDER THE MASTER NO LONGER HAS",
        "protection": "TP/SL MISSING ON THE FOLLOWER",
        "order": "RESTING ORDER MISSING ON THE FOLLOWER",
    }.get(kind, kind.upper())

    lines = [
        f"⚠️ <b>Mismatch</b> · {account}",
        f"<code>{symbol}</code>",
        f"<b>{_k}</b>",
    ]
    if follower is not None or target is not None:
        _f = f"follower has {_num(follower)}" if follower is not None else ""
        _t = f"should have {_num(target)}" if target is not None else ""
        _m = f"master has {_num(master)}" if master is not None else ""
        lines.append(" · ".join(x for x in (_f, _t, _m) if x))
    elif master is not None:
        lines.append(f"master has {_num(master)}")
    if detail:
        lines.append(f"<i>{detail}</i>")
    # NO PROMISE ABOUT WHAT HAPPENS NEXT. This used to say "correcting it now",
    # which the engine cannot know: on 2026-11-20 the correction itself crashed
    # on four legs and nothing was corrected at all. An alert that guarantees a
    # fix which does not arrive is worse than one that guarantees nothing --
    # the reader stops checking. State the fact; the correction alert, or the
    # 60s "still not corrected", says what actually happened.
    lines.append("<i>not corrected yet</i>" if acting
                 else "<i>not being corrected automatically — see above</i>")
    await send_message(NEWLINE.join(lines), account=account)


async def notify_mismatch_cleared(account: str, symbol: str, kind: str) -> None:
    """Let the next occurrence of the same discrepancy speak again.

    Without this a leg that goes wrong, is fixed, and goes wrong again an hour
    later would be silently swallowed by the throttle for a week.
    """
    await clear_alert(f"mismatch:{account}:{symbol}:{kind}")


async def notify_holding(account: str, symbol: str, held, target,
                         master=None, seconds: float = 0) -> None:
    """A leg the reconciler is deliberately leaving alone.

    When the master is working an order that reduces his own position, the
    reconciler does NOT trim the follower back to target. His order is filling
    in pieces; his size is dropping and the follower's has not moved yet, so
    the gap is a copy mid-flight. Trimming into it would fill the follower at a
    different price from the master's, twice over, for a gap that settles
    itself in one step when his order terminates. Held with no size cap and no
    time limit — desk call, Prathav 2026-09-25.

    That rule was right and completely invisible. On 2026-11-20 four legs sat
    over target under it; the client saw the two WITHOUT a resting master order
    get trimmed to the ratio and the two WITH one not, and concluded the ratio
    had stopped working on shorts. It had not. Nothing anywhere said the engine
    was waiting on purpose.

    So: same threshold as every other unsettled discrepancy, opposite framing.
    This is not a failure and must not read like one — "Not corrected" on a leg
    we chose not to correct sends someone hunting a bug that does not exist.
    """
    if not telegram_enabled():
        return
    # One per (account, symbol) per window. The hold is re-detected every 10s
    # and can last minutes — on 2026-09-21 roughly eleven of them.
    if not await _should_send(f"holding:{account}:{symbol}", STUCK_ALERT_WINDOW):
        return

    lines = [
        f"⏸ <b>Holding on purpose</b> · {account}",
        f"<code>{symbol}</code>",
        f"Holds {_num(held)}, target is {_num(target)}"
        + (f" (master {_num(master)})" if master is not None else ""),
        "<i>the master is closing this leg himself. Trimming now would fill at a "
        "different price than his — so we wait for his order to fill or cancel, "
        "and it settles in one step.</i>",
        f"<i>waiting {int(seconds)}s — no action needed</i>",
    ]
    await send_message(NEWLINE.join(lines), account=account)


async def send_alert(alert: dict) -> None:
    """Format an alert row and push it to Telegram.

    Deduped on (type, account, message) so a condition re-detected on every sweep
    — a position mismatch, restored protection — reads as one notification rather
    than a stream of identical ones."""
    if not telegram_enabled():
        return
    if (alert.get("type") or "").lower() in SILENT_ALERT_TYPES:
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
    await send_message(text, account_id=alert.get("account_id"))
