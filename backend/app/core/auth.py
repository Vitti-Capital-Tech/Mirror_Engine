"""Authentication core — Supabase JWT verification + email-OTP 2FA helpers."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException, status

from app.config import settings
from app.database import db

logger = logging.getLogger(__name__)


@dataclass
class CurrentUser:
    id: str
    email: Optional[str] = None
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---------------------------------------------------------------------------
# Token verification (via Supabase GoTrue introspection — no JWT secret needed)
# ---------------------------------------------------------------------------

def _role_for(user_id: str) -> str:
    try:
        res = db.table("profiles").select("role").eq("id", user_id).execute()
        if res.data:
            return res.data[0].get("role") or "user"
    except Exception as e:
        logger.warning("Could not fetch role for %s: %s", user_id, e)
    return "user"


async def get_current_user(authorization: str = Header(None)) -> CurrentUser:
    """FastAPI dependency: resolve the authenticated user from the Bearer token.

    Validates the token by calling Supabase's /auth/v1/user endpoint (works for
    any signing scheme, no local JWT secret required). The token is only issued
    by our /verify-2fa step, so any valid token here already passed 2FA.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.SUPABASE_URL}/auth/v1/user",
                headers={"apikey": settings.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Auth service unreachable: {e}")
    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    u = resp.json()
    uid = u.get("id")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return CurrentUser(id=uid, email=u.get("email"), role=_role_for(uid))


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def scope_owned(query, user: "CurrentUser", column: str = "owner_id"):
    """Apply an owner filter to a Supabase query — unless the user is admin
    (admins see all tenants)."""
    if user.is_admin:
        return query
    return query.eq(column, user.id)


def owned_account_or_404(account_id: str, user: "CurrentUser") -> dict:
    """Fetch an account and ensure the caller owns it (or is admin)."""
    res = db.table("accounts").select("*").eq("id", account_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Account not found.")
    acc = res.data[0]
    if not user.is_admin and acc.get("owner_id") != user.id:
        raise HTTPException(status_code=404, detail="Account not found.")
    return acc


# ---------------------------------------------------------------------------
# Email-OTP 2FA
# ---------------------------------------------------------------------------

def _hash_code(code: str) -> str:
    return hashlib.sha256(f"{settings.SUPABASE_SERVICE_KEY}:{code}".encode()).hexdigest()


def generate_and_store_otp(user_id: str, purpose: str = "login_2fa") -> str:
    """Create a 6-digit OTP, store its hash, return the plaintext code (to email)."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_TTL_SECONDS)
    # Invalidate previous unconsumed OTPs for this user+purpose
    try:
        db.table("auth_otps").delete().eq("user_id", user_id).eq("purpose", purpose).is_("consumed_at", "null").execute()
    except Exception:
        pass
    db.table("auth_otps").insert({
        "user_id": user_id,
        "code_hash": _hash_code(code),
        "purpose": purpose,
        "expires_at": expires.isoformat(),
    }).execute()
    return code


def verify_otp(user_id: str, code: str, purpose: str = "login_2fa") -> bool:
    """Verify an OTP; consume it on success. Returns True/False."""
    try:
        res = (
            db.table("auth_otps")
            .select("*")
            .eq("user_id", user_id)
            .eq("purpose", purpose)
            .is_("consumed_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("OTP lookup failed: %s", e)
        return False
    if not res.data:
        return False
    row = res.data[0]

    # Expiry check
    try:
        exp = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except Exception:
        exp = datetime.now(timezone.utc) - timedelta(seconds=1)
    if datetime.now(timezone.utc) > exp:
        return False

    # Attempt limit
    if row.get("attempts", 0) >= settings.OTP_MAX_ATTEMPTS:
        return False

    if row["code_hash"] != _hash_code(code):
        db.table("auth_otps").update({"attempts": row.get("attempts", 0) + 1}).eq("id", row["id"]).execute()
        return False

    # Success — consume
    db.table("auth_otps").update({"consumed_at": datetime.now(timezone.utc).isoformat()}).eq("id", row["id"]).execute()
    return True
