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


def _dedupe(key: str, window: float) -> bool:
    """True if we should SEND (this key hasn't been sent within `window`)."""
    now = time.time()
    ts = _seen.get(key)
    if ts is not None and (now - ts) < window:
        return False
    _seen[key] = now
    return True


def _num(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):g}"
    except Exception:
        return str(v)


async def notify_open(account: str, symbol: str, side: str, lots, price=None) -> None:
    """Follower opened / added to a position."""
    if not telegram_enabled():
        return
    d = str(side).lower()
    icon = "🟢" if d in ("buy", "long") else "🔴"
    direction = "LONG" if d in ("buy", "long") else "SHORT"
    text = (f"{icon} <b>Position Opened</b> · {account}\n"
            f"<code>{symbol}</code>\n"
            f"{direction} · {_num(lots)} lot(s)" + (f" @ {_num(price)}" if price is not None else ""))
    await send_message(text)


async def notify_close(account: str, symbol: str, lots, price=None) -> None:
    """Follower closed / reduced a position."""
    if not telegram_enabled():
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
    if key and not _dedupe(key, window):
        return
    lot_str = f" {_num(lots)} lot(s)" if lots not in (None, "", 0) else ""
    text = (f"⚠️ <b>Mirror Failed</b> · {account}\n"
            f"<code>{symbol}</code>\n"
            f"{str(side).title()}{lot_str} — {reason}")
    await send_message(text)


async def send_alert(alert: dict) -> None:
    """Format an alert row and push it to Telegram."""
    if not telegram_enabled():
        return
    level = (alert.get("level") or "info").lower()
    icon = _ICON.get(level, "•")
    atype = (alert.get("type") or "alert").replace("_", " ").title()
    msg = alert.get("message") or ""
    text = f"{icon} <b>{atype}</b> [{level.upper()}]\n{msg}"

    now = time.time()
    if text == _last["text"] and (now - _last["ts"]) < _DEDUPE_WINDOW:
        return
    _last["text"], _last["ts"] = text, now
    await send_message(text)
