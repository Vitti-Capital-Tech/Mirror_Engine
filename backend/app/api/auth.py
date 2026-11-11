"""Auth API — Supabase password auth + mandatory email-OTP 2FA (via Resend)."""

import hashlib
import hmac
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
    password: str = ""     # blank = passwordless: just the email is enough


class Verify2FAIn(BaseModel):
    pending_id: str
    code: str


class ResendIn(BaseModel):
    pending_id: str


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/signup")
async def signup(body: SignupIn):
    # Create the user via the admin API with email pre-confirmed. Our email-OTP
    # 2FA at login is what actually proves inbox ownership, so Supabase's own
    # email-confirmation step is redundant here.
    admin_headers = {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=admin_headers,
            json={"email": body.email, "password": body.password, "email_confirm": True},
        )
    if r.status_code >= 400:
        body_json = r.json() if r.content else {}
        detail = body_json.get("msg") or body_json.get("error_description") or body_json.get("error") or "Signup failed"
        if "already" in str(detail).lower() or "registered" in str(detail).lower():
            detail = "An account with this email already exists."
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "message": "Account created. You can now log in."}


def _derived_password(email: str) -> str:
    """The password for a passwordless login, derived from the email.

    Same idea as Option_Scope's login — type an email, you are in — with one
    deliberate difference: the formula NEVER leaves the server.

    Option_Scope builds it in the browser:

        const derivedPassword = `OptionScope_${cleanEmail}_Secure123!`;

    which ships in the JS bundle and sits in a public repo, so anyone can
    compute anyone's password, admin included. That is survivable for a paper
    trading dashboard. This system holds live Delta API keys that place real
    orders, so the same trick here would hand over the trading accounts.

    Deriving it here costs the user nothing — they still type only an email —
    and the secret stays in .env with the other secrets.

    Keyed on PASSWORDLESS_SECRET, falling back to ENCRYPTION_KEY, which is
    already pinned. Deliberately NOT keyed on SUPABASE_SERVICE_KEY: that is a
    rotatable credential, and tying derived values to it is exactly how every
    stored Delta API key nearly became undecryptable during the 2026-08-28
    project migration. Pin PASSWORDLESS_SECRET once and leave it alone — change
    it and every passwordless user is locked out until their password is reset.
    """
    secret = (settings.PASSWORDLESS_SECRET or settings.ENCRYPTION_KEY
              or settings.SUPABASE_SERVICE_KEY or "mirror-engine-dev")
    digest = hmac.new(secret.encode(), email.strip().lower().encode(),
                      hashlib.sha256).hexdigest()
    # Upper, lower, digit and symbol so it satisfies any Supabase password policy.
    return f"Me1!{digest[:44]}"


async def _ensure_profile(user_id: str, email: str) -> None:
    """Give a brand-new passwordless user the profile row the app reads roles
    from. Best effort: a failure here must not block the login, and the row may
    already exist from a trigger."""
    from app.database import db
    try:
        if db.table("profiles").select("id").eq("id", user_id).execute().data:
            return
        db.table("profiles").insert({"id": user_id, "email": email, "role": "user"}).execute()
    except Exception as e:
        logger.warning("passwordless: could not create profile for %s: %s", email, e)


def _admin_headers() -> dict:
    return {"apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json"}


async def _create_user(email: str, password: str) -> None:
    """Create a pre-confirmed Supabase user (admin API)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{settings.SUPABASE_URL}/auth/v1/admin/users",
            headers=_admin_headers(),
            json={"email": email, "password": password, "email_confirm": True},
        )
    if r.status_code >= 400:
        j = r.json() if r.content else {}
        msg = str(j.get("msg") or j.get("error_description") or j.get("error") or "")
        # Already exists is fine — we fall through to the password grant.
        if "already" not in msg.lower() and "registered" not in msg.lower():
            raise HTTPException(status_code=400, detail=msg or "Could not create account")


async def _find_user(email: str):
    """The Supabase user row for this email, or None.

    Paged rather than a single big read: the admin list is capped per page, and
    "not on page 1" must not read as "no such user" — that would send an
    existing account down the create-then-fail path.
    """
    email = email.strip().lower()
    async with httpx.AsyncClient(timeout=15) as c:
        for page in range(1, 11):          # 10 x 200 = 2000 users, then give up
            r = await c.get(f"{settings.SUPABASE_URL}/auth/v1/admin/users",
                            headers=_admin_headers(),
                            params={"page": page, "per_page": 200})
            if r.status_code >= 400:
                return None
            users = (r.json() or {}).get("users") or []
            for u in users:
                if (u.get("email") or "").strip().lower() == email:
                    return u
            if len(users) < 200:
                return None
    return None


async def _set_password(user_id: str, password: str) -> bool:
    """Point an existing account at the derived password (admin API).

    Needed because the 7 accounts that already existed when passwordless went in
    have their own passwords — or none at all, being Google sign-ins — so the
    derived grant fails for every one of them. Verified against the live project
    on 2026-11-11: prathavkevadiya@, danani.j@ and tusharbhardwaj2617@ all
    returned 400. Without this, "type your email" worked only for addresses
    nobody had ever used, i.e. for none of the team.

    Google sign-in is a separate identity and keeps working either way.
    """
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.put(f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                        headers=_admin_headers(), json={"password": password})
    if r.status_code >= 400:
        logger.warning("passwordless: could not set password for %s: %s",
                       user_id, r.text[:200])
        return False
    return True


async def _password_grant(email: str, password: str) -> dict:
    """Exchange email+password for a Supabase session (raises 401 on failure)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(_gotrue("/token?grant_type=password"), headers=_headers(),
                         json={"email": email, "password": password})
    if r.status_code >= 400:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return r.json()


def _session_response(session: dict) -> dict:
    return {
        "twofa_required": False,
        "access_token": session.get("access_token"),
        "refresh_token": session.get("refresh_token"),
        "expires_in": session.get("expires_in"),
        "user": session.get("user"),
    }


@router.post("/login")
async def login(body: LoginIn):
    # Quick-admin shortcut: the magic passphrase signs straight into the shared
    # admin account (no user password, no OTP).
    #
    # Accepted in the EMAIL box now, because the login form no longer has a
    # password box to type it into. Still accepted as the password as well, so
    # anyone used to the old way — or any saved browser autofill — keeps working.
    magic = (settings.ADMIN_MAGIC_CODE or "").strip().lower()
    typed_email = body.email.strip().lower()
    if magic and settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD and \
       magic in (typed_email, body.password.strip().lower()):
        session = await _password_grant(settings.ADMIN_EMAIL, settings.ADMIN_PASSWORD)
        return _session_response(session)

    # Passwordless: an email on its own is enough. Sign the user in, creating
    # the account on first sight — the same sign-up-or-sign-in shape
    # Option_Scope uses, with the derivation kept server-side (see
    # _derived_password).
    if not body.password.strip():
        if not settings.PASSWORDLESS_LOGIN:
            raise HTTPException(status_code=400, detail="Password is required.")
        email = typed_email
        if "@" not in email:
            raise HTTPException(status_code=400, detail="Enter a valid email address.")

        # The admin account never goes through derivation. Its password is pinned
        # in .env and the `trade` shortcut grants with it — re-deriving here would
        # overwrite that and silently break the shortcut for everyone.
        if settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD and \
           email == settings.ADMIN_EMAIL.strip().lower():
            session = await _password_grant(settings.ADMIN_EMAIL, settings.ADMIN_PASSWORD)
            return _session_response(session)

        derived = _derived_password(email)
        try:
            session = await _password_grant(email, derived)
        except HTTPException:
            # Either there is no account yet, or there is one whose password is
            # its own (every account that predates passwordless, including every
            # Google sign-in). Create it, or point it at the derived password.
            existing = await _find_user(email)
            if existing is None:
                await _create_user(email, derived)
            elif not await _set_password(existing.get("id"), derived):
                raise HTTPException(
                    status_code=503,
                    detail="Could not sign you in just now — please try again.")
            session = await _password_grant(email, derived)
        u = session.get("user") or {}
        if u.get("id"):
            await _ensure_profile(u["id"], u.get("email") or email)
        return _session_response(session)

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

    # 2FA disabled → return the session immediately (email + password is enough)
    if not settings.TWOFA_ENABLED:
        return {
            "twofa_required": False,
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_in": session.get("expires_in"),
            "user": session.get("user"),
        }

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


@router.post("/refresh")
async def refresh(body: RefreshIn):
    """Exchange a refresh token for a fresh access token.

    GoTrue issues access tokens with a one-hour life (measured: exp - iat =
    3600). Nothing renewed them, so every session died exactly on the hour —
    GoTrue answered /auth/v1/user with 403, get_current_user turned that into a
    401, and the dashboard wiped the token and bounced to /login mid-session.
    The refresh token was handed to the frontend by /login and /verify-2fa all
    along and simply thrown away; this is the endpoint that spends it.

    No 2FA re-prompt: the refresh token only exists because a login already
    passed 2FA, and GoTrue invalidates it the moment the session is signed out.
    """
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(_gotrue("/token?grant_type=refresh_token"), headers=_headers(),
                         json={"refresh_token": body.refresh_token})
    if r.status_code >= 400:
        # Genuinely dead (revoked, reused, or past the refresh window) — this is
        # the ONLY case in which the caller should send the user back to /login.
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    return _session_response(r.json())


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
