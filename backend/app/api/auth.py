"""Auth API — Supabase password auth + mandatory email-OTP 2FA (via Resend)."""

import json
import logging
import secrets

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core.auth import generate_and_store_otp, verify_otp, get_current_user, CurrentUser
from app.services.resend_client import send_otp_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _gotrue(path: str) -> str:
    return f"{settings.SUPABASE_URL}/auth/v1{path}"


def _headers() -> dict:
    return {"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"}


class SignupIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class Verify2FAIn(BaseModel):
    pending_id: str
    code: str


class ResendIn(BaseModel):
    pending_id: str


@router.post("/signup")
async def signup(body: SignupIn):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(_gotrue("/signup"), headers=_headers(),
                         json={"email": body.email, "password": body.password})
    if r.status_code >= 400:
        detail = (r.json().get("msg") or r.json().get("error_description") or "Signup failed") if r.content else "Signup failed"
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "message": "Account created. You can now log in."}


@router.post("/login")
async def login(body: LoginIn):
    # 1. Validate email + password via Supabase
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(_gotrue("/token?grant_type=password"), headers=_headers(),
                         json={"email": body.email, "password": body.password})
    if r.status_code >= 400:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    session = r.json()
    user = session.get("user", {})
    uid, email = user.get("id"), user.get("email")
    if not uid:
        raise HTTPException(status_code=401, detail="Login failed")

    # 2. Generate + email the 2FA code
    code = generate_and_store_otp(uid)
    if not await send_otp_email(email, code):
        raise HTTPException(status_code=502, detail="Could not send verification email")

    # 3. Hold the validated session until 2FA completes
    pending_id = secrets.token_urlsafe(24)
    await _redis.setex(f"pending2fa:{pending_id}", settings.OTP_TTL_SECONDS,
                       json.dumps({"user_id": uid, "email": email, "session": session}))
    return {"twofa_required": True, "pending_id": pending_id, "email": email}


@router.post("/verify-2fa")
async def verify_2fa(body: Verify2FAIn):
    raw = await _redis.get(f"pending2fa:{body.pending_id}")
    if not raw:
        raise HTTPException(status_code=400, detail="Login session expired — please log in again")
    data = json.loads(raw)
    if not verify_otp(data["user_id"], body.code):
        raise HTTPException(status_code=401, detail="Invalid or expired code")
    await _redis.delete(f"pending2fa:{body.pending_id}")
    session = data["session"]
    return {
        "access_token": session.get("access_token"),
        "refresh_token": session.get("refresh_token"),
        "expires_in": session.get("expires_in"),
        "user": session.get("user"),
    }


@router.post("/resend-2fa")
async def resend_2fa(body: ResendIn):
    raw = await _redis.get(f"pending2fa:{body.pending_id}")
    if not raw:
        raise HTTPException(status_code=400, detail="Login session expired — please log in again")
    data = json.loads(raw)
    code = generate_and_store_otp(data["user_id"])
    if not await send_otp_email(data["email"], code):
        raise HTTPException(status_code=502, detail="Could not resend verification email")
    return {"success": True, "message": "A new code has been sent."}


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role}
