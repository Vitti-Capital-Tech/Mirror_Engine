"""Master-vs-follower fill comparison, and the daily report built from it.

Read-only. Every endpoint is owner-scoped; an admin may pass ?owner_id= to look
at another tenant's comparison, which is *viewing* — the one thing an admin
deliberately cannot do anywhere in this API is change who the master is.
"""

import logging
from datetime import date, datetime, timedelta

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.config import settings
from app.core import daily_report as dr
from app.core import fill_compare as fc
from app.core.auth import CurrentUser, get_current_user, require_admin
from app.database import db

logger = logging.getLogger(__name__)

_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

router = APIRouter(prefix="/api/comparison", tags=["comparison"])

# A comparison reads /v2/fills for every account, so it is the most expensive
# read in the API. Cap how far back one request may reach.
MAX_WINDOW_DAYS = 31


def _resolve_owner(user: CurrentUser, owner_id: str | None) -> str:
    """Whose comparison to build. Only an admin may name someone else."""
    if not owner_id or owner_id == user.id:
        return user.id
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="You can only view your own accounts.")
    return owner_id


def _resolve_window(day: str | None, start: str | None, end: str | None):
    """(start, end, day_label) from either ?date= or an explicit ?start=&end=."""
    if start or end:
        if not (start and end):
            raise HTTPException(status_code=400, detail="Pass both start and end, or neither.")
        s, e = fc.parse_ts(start), fc.parse_ts(end)
        if not s or not e:
            raise HTTPException(status_code=400, detail="start/end must be ISO timestamps.")
        if e <= s:
            raise HTTPException(status_code=400, detail="end must be after start.")
        if e - s > timedelta(days=MAX_WINDOW_DAYS):
            raise HTTPException(
                status_code=400,
                detail=f"Window too wide — {MAX_WINDOW_DAYS} days maximum.",
            )
        return s, e, None
    if day:
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.")
    else:
        d = fc.today_ist()
    s, e = fc.ist_day_bounds(d)
    return s, e, d


async def _build(user: CurrentUser, owner_id, day, start, end) -> dict:
    owner = _resolve_owner(user, owner_id)
    s, e, d = _resolve_window(day, start, end)
    try:
        out = await fc.compare_for_owner(db, owner, s, e)
    except Exception as ex:
        logger.error(f"Comparison failed for owner {owner}: {ex}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(ex))
    if d:
        out["window"]["date"] = d.isoformat()
    return out


@router.get("")
async def get_comparison(
    date_: str | None = Query(None, alias="date", description="IST day, YYYY-MM-DD"),
    start: str | None = Query(None, description="ISO timestamp (use with end)"),
    end: str | None = Query(None, description="ISO timestamp (use with start)"),
    owner_id: str | None = Query(None, description="Admins only — view another tenant"),
    user: CurrentUser = Depends(get_current_user),
):
    """Order-by-order comparison of master fills against every follower's fills,
    with per-leg verdicts, delays and the day's error count."""
    return await _build(user, owner_id, date_, start, end)


@router.get("/report.csv")
async def get_report_csv(
    date_: str | None = Query(None, alias="date"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    owner_id: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """The same comparison as a spreadsheet — one row per (master order, follower)."""
    cmp = await _build(user, owner_id, date_, start, end)
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    return PlainTextResponse(
        dr.render_csv(cmp),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fill-match-{day}.csv"'},
    )


@router.get("/report.html", response_class=HTMLResponse)
async def get_report_html(
    date_: str | None = Query(None, alias="date"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    owner_id: str | None = Query(None),
    download: bool = Query(False, description="Force a file download instead of rendering"),
    user: CurrentUser = Depends(get_current_user),
):
    """The shareable doc. Self-contained HTML — opens offline, prints to PDF."""
    cmp = await _build(user, owner_id, date_, start, end)
    day = cmp["window"].get("date") or cmp["window"]["start"][:10]
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="fill-match-{day}.html"'
    return HTMLResponse(dr.render_html(cmp), headers=headers)


@router.get("/report.txt", response_class=PlainTextResponse)
async def get_report_text(
    date_: str | None = Query(None, alias="date"),
    owner_id: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """The Telegram summary as text — handy for pasting into a chat by hand."""
    cmp = await _build(user, owner_id, date_, None, None)
    return dr.render_telegram(cmp)


@router.post("/report/send")
async def send_report_now(
    date_: str | None = Query(None, alias="date"),
    user: CurrentUser = Depends(get_current_user),
):
    """Send today's (or a given day's) report to Telegram right now.

    Deliberately ignores the once-a-day marker: this endpoint exists because
    somebody asked for the report again, and refusing on the grounds that it was
    already sent would be answering a different question.
    """
    day = fc.today_ist()
    if date_:
        try:
            day = date.fromisoformat(date_)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.")
    if user.is_admin:
        # An admin's own accounts are usually empty, so a manual send from the
        # admin console covers every tenant — the same thing the scheduler sends.
        return await dr.send_daily_report(db, day, redis=_redis, force=True)
    cmp = await fc.compare_for_day(db, user.id, day)
    text = dr.render_telegram(cmp)
    from app.services import telegram_client as tg
    ok = await tg.send_message(text)
    return {"sent": ok, "day": day.isoformat(),
            "reason": None if ok else "Telegram is not configured on this deployment."}


@router.get("/report/status")
async def report_status(user: CurrentUser = Depends(require_admin)):
    """Whether the daily report is scheduled, and whether today's went out."""
    from app.services import telegram_client as tg
    day = fc.today_ist()
    sent = None
    try:
        sent = bool(await _redis.get(f"daily_report:sent:{day.isoformat()}"))
    except Exception as e:
        logger.warning(f"report_status: marker read failed: {e}")
    return {
        "enabled": settings.DAILY_REPORT_ENABLED,
        "send_at_ist": f"{settings.DAILY_REPORT_HOUR_IST:02d}:{settings.DAILY_REPORT_MINUTE_IST:02d}",
        "telegram_configured": tg.telegram_enabled(),
        "today_ist": day.isoformat(),
        "sent_marker_today": sent,
        "server_time_ist": datetime.now(fc.IST).isoformat(),
    }
