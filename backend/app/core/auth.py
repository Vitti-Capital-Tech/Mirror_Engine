"""Authentication core — Supabase JWT verification + email-OTP 2FA helpers."""

import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, Tuple

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

# ---------------------------------------------------------------------------
# Verified-token cache
#
# This dependency used to call GoTrue over the network on EVERY request, and
# then hit Supabase again for the role. The dashboard polls six endpoints on
# 10-15s timers: that measured 8,510 /auth/v1/user round trips in 76 minutes,
# all re-answering a question that cannot change until the token expires.
#
# Each of those was also a chance to time out, and a timeout raises 503 — which
# the dashboard used to treat as "signed out". Caching the POSITIVE answer for
# a short window removes almost all of that exposure. A rejection is never
# cached, and the entry is clamped to the token's own expiry so it can never
# outlive the token. Cost: a role change can take up to _VERIFY_TTL_SEC to
# take effect.
# ---------------------------------------------------------------------------

_VERIFY_TTL_SEC = 60
_VERIFY_CACHE_MAX = 512
_verify_cache: dict = {}


def _token_fingerprint(token: str) -> str:
    """Key the cache by a digest so raw bearer tokens aren't held in a dict."""
    return hashlib.sha256(token.encode()).hexdigest()


def _token_expiry(token: str) -> Optional[float]:
    """Read `exp` out of the JWT payload WITHOUT verifying it. Only ever used to
    shorten a cache entry, never to decide that a token is valid — GoTrue
    remains the sole authority on that."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


def _cached_user(key: str) -> Optional["CurrentUser"]:
    entry = _verify_cache.get(key)
    if not entry:
        return None
    expires_at, user = entry
    if time.time() >= expires_at:
        _verify_cache.pop(key, None)
        return None
    return user


def _remember_user(key: str, user: "CurrentUser", token: str) -> None:
    ttl = _VERIFY_TTL_SEC
    exp = _token_expiry(token)
    if exp is not None:
        ttl = min(ttl, exp - time.time())
    if ttl <= 0:
        return
    if len(_verify_cache) >= _VERIFY_CACHE_MAX:
        _verify_cache.clear()
    _verify_cache[key] = (time.time() + ttl, user)


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
    cache_key = _token_fingerprint(token)
    cached = _cached_user(cache_key)
    if cached is not None:
        return cached
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
    current = CurrentUser(id=uid, email=u.get("email"), role=_role_for(uid))
    _remember_user(cache_key, current, token)
    return current


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
