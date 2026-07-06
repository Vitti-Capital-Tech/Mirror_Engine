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
