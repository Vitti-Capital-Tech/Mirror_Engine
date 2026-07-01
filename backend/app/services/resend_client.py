"""Minimal Resend email client — used to deliver 2FA one-time codes."""

import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


async def send_otp_email(to_email: str, code: str) -> bool:
    """Send a 2FA one-time code to the user. Returns True on success."""
    if not settings.RESEND_API_KEY:
        logger.error("RESEND_API_KEY not configured — cannot send OTP email.")
        return False

    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:420px;margin:auto;padding:24px;background:#11141d;border:1px solid #212838;border-radius:12px;color:#e8ecf5">
      <h2 style="margin:0 0 8px;font-size:18px;color:#fff">Mirror Engine — Verification code</h2>
      <p style="color:#97a1b6;font-size:13px;margin:0 0 20px">Use this code to finish signing in. It expires in {settings.OTP_TTL_SECONDS // 60} minutes.</p>
      <div style="font-size:32px;font-weight:800;letter-spacing:8px;color:#3b82f6;text-align:center;padding:14px;background:#0c0f17;border-radius:10px">{code}</div>
      <p style="color:#5b667d;font-size:11px;margin:20px 0 0">If you didn't request this, you can ignore this email.</p>
    </div>
    """
    payload = {
        "from": settings.RESEND_FROM,
        "to": [to_email],
        "subject": f"Your Mirror Engine code: {code}",
        "html": html,
    }
    headers = {"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(RESEND_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.error("Resend send failed (%s): %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as exc:
        logger.error("Resend send error: %s", exc)
        return False
