"""Regression: a live session must survive the hour, and a blip must not end it.

Run:  venv/Scripts/python.exe test_session_refresh.py
No network, no Redis, no Supabase — httpx is stubbed.

THE INCIDENT (2026-10-14): "it logs out after every few seconds and throws me
out". Three separate defects stacked up.

1. Supabase access tokens live exactly one hour — measured against the live
   project, exp - iat = 3600. Nothing ever renewed them. /login and /verify-2fa
   both returned a refresh_token and the frontend dropped it on the floor, so
   every session died on the hour. The backend log shows it cleanly:

       token granted 01:55:27 -> 403 at 02:55:27   (60m 00s)
       token granted 03:02:12 -> 403 at 04:02:12   (60m 00s)

   GoTrue answers /auth/v1/user with 403 for an expired JWT, get_current_user
   turns any non-200 into a 401, and the dashboard wiped the token and bounced
   to /login. Six polled endpoints meant six simultaneous 401s per expiry.

2. get_current_user called GoTrue over the network on EVERY request: 8,510
   round trips in 76 minutes, all re-deciding something that cannot change
   until the token expires. Every one was a chance to time out, and a timeout
   raises 503.

3. AuthContext caught EVERY failure of /api/auth/me — including that 503 — and
   dropped the token. One blip signed the user out for good, minutes after they
   logged in. That is the "few seconds" half of the complaint; (1) is the
   "every hour" half.

Fixes under test here: the /refresh endpoint that spends the refresh token, and
the verified-token cache. The frontend half (renew-and-replay, and not treating
a network error as a sign-out) lives in frontend/src/lib/api.ts.
"""
import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import httpx
from fastapi import HTTPException

import app.core.auth as core_auth

FAILURES = []


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def token_expiring_in(seconds):
    """A JWT-shaped string whose payload carries an `exp`. Only the payload is
    ever read, and only to shorten a cache entry — never to accept a token."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": time.time() + seconds}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.content = b"{}"
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient and counts the calls that go out."""
    calls = []
    get_status = 200
    get_payload = {"id": "user-1", "email": "t@example.com"}
    post_status = 200
    post_payload = {"access_token": "new-access", "refresh_token": "new-refresh",
                    "expires_in": 3600, "user": {"id": "user-1"}}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        FakeClient.calls.append(("GET", url))
        return FakeResponse(FakeClient.get_status, FakeClient.get_payload)

    async def post(self, url, headers=None, json=None):
        FakeClient.calls.append(("POST", url))
        return FakeResponse(FakeClient.post_status, FakeClient.post_payload)


def reset():
    FakeClient.calls = []
    FakeClient.get_status = 200
    FakeClient.post_status = 200
    core_auth._verify_cache.clear()


def install_stubs():
    httpx.AsyncClient = FakeClient
    core_auth.httpx.AsyncClient = FakeClient
    # Role lookup hits Supabase; it is not what these tests are about.
    core_auth._role_for = lambda uid: "admin"


# ---------------------------------------------------------------------------


def test_the_token_really_does_expire():
    print("\n1. the premise: these tokens last an hour, so something must renew")
    tok = token_expiring_in(3600)
    ttl = core_auth._token_expiry(tok) - time.time()
    check("lifetime is ~3600s", 3590 < ttl <= 3600, True)
    check("an expired token reads as past", core_auth._token_expiry(token_expiring_in(-5)) < time.time(), True)


def test_refresh_endpoint_spends_the_refresh_token():
    print("\n2. THE FIX: /refresh exchanges the refresh token for a new session")
    reset()
    import app.api.auth as api_auth
    api_auth.httpx.AsyncClient = FakeClient

    out = asyncio.run(api_auth.refresh(api_auth.RefreshIn(refresh_token="rt-1")))
    check("asked GoTrue for a refresh_token grant",
          any("grant_type=refresh_token" in u for _, u in FakeClient.calls), True)
    check("returns a new access token", out["access_token"], "new-access")
    check("returns the ROTATED refresh token", out["refresh_token"], "new-refresh")


def test_a_dead_refresh_token_is_the_only_thing_that_logs_you_out():
    print("\n3. a refresh token GoTrue rejects -> 401, and only then")
    reset()
    import app.api.auth as api_auth
    api_auth.httpx.AsyncClient = FakeClient
    FakeClient.post_status = 400          # GoTrue's answer for a dead token
    try:
        asyncio.run(api_auth.refresh(api_auth.RefreshIn(refresh_token="dead")))
        check("raises", False, True)
    except HTTPException as e:
        check("status is 401 (not 400/500)", e.status_code, 401)
        check("message tells the user to log in", "log in again" in e.detail, True)


def test_verification_is_cached():
    print("\n4. 8,510 round trips an hour -> the same answer, reused")
    reset()
    install_stubs()
    tok = token_expiring_in(3600)
    hdr = "Bearer " + tok

    u1 = asyncio.run(core_auth.get_current_user(hdr))
    check("first call hits GoTrue", len(FakeClient.calls), 1)
    check("resolves the user", u1.id, "user-1")
    check("and the role", u1.role, "admin")

    for _ in range(50):
        u2 = asyncio.run(core_auth.get_current_user(hdr))
    check("50 further calls hit GoTrue 0 more times", len(FakeClient.calls), 1)
    check("same user returned", u2.id, "user-1")


def test_a_rejection_is_never_cached():
    print("\n5. a 403 must NOT be remembered — re-login has to work at once")
    reset()
    install_stubs()
    tok = token_expiring_in(3600)
    FakeClient.get_status = 403
    for _ in range(3):
        try:
            asyncio.run(core_auth.get_current_user("Bearer " + tok))
        except HTTPException as e:
            last = e.status_code
    check("still 401 to the caller", last, 401)
    check("every rejection was re-checked, none cached", len(FakeClient.calls), 3)
    check("nothing stored", len(core_auth._verify_cache), 0)


def test_a_cache_entry_cannot_outlive_its_token():
    print("\n6. the cache can never keep a dead token alive")
    reset()
    install_stubs()
    # A token with 2s left must not be trusted for the full 60s TTL.
    short = token_expiring_in(2)
    key = core_auth._token_fingerprint(short)
    core_auth._remember_user(key, core_auth.CurrentUser(id="u"), short)
    expires_at, _ = core_auth._verify_cache[key]
    check("entry clamped to the token's own expiry", expires_at - time.time() <= 2.01, True)

    dead = token_expiring_in(-1)
    dkey = core_auth._token_fingerprint(dead)
    core_auth._remember_user(dkey, core_auth.CurrentUser(id="u"), dead)
    check("an already-expired token is not cached at all",
          core_auth._cached_user(dkey), None)


def test_two_users_are_never_confused():
    print("\n7. the cache is keyed per token, not shared across users")
    reset()
    install_stubs()
    a, b = token_expiring_in(3600), token_expiring_in(1800)
    FakeClient.get_payload = {"id": "user-A", "email": "a@x.com"}
    ua = asyncio.run(core_auth.get_current_user("Bearer " + a))
    FakeClient.get_payload = {"id": "user-B", "email": "b@x.com"}
    ub = asyncio.run(core_auth.get_current_user("Bearer " + b))
    check("A is A", ua.id, "user-A")
    check("B is B", ub.id, "user-B")
    check("A still A on a cached re-read",
          asyncio.run(core_auth.get_current_user("Bearer " + a)).id, "user-A")
    check("raw tokens are not used as keys",
          any(a in k for k in core_auth._verify_cache), False)


def test_missing_token_still_rejected():
    print("\n8. none of this weakens the front door")
    reset()
    install_stubs()
    for bad in (None, "", "Basic abc", "Bearer"):
        try:
            asyncio.run(core_auth.get_current_user(bad))
            check("rejected %r" % (bad,), False, True)
        except HTTPException as e:
            check("rejected %r" % (bad,), e.status_code, 401)


def main():
    print("=" * 74)
    print("session refresh - a session must survive the hour, and survive a blip")
    print("=" * 74)
    real = httpx.AsyncClient
    try:
        for fn in (
            test_the_token_really_does_expire,
            test_refresh_endpoint_spends_the_refresh_token,
            test_a_dead_refresh_token_is_the_only_thing_that_logs_you_out,
            test_verification_is_cached,
            test_a_rejection_is_never_cached,
            test_a_cache_entry_cannot_outlive_its_token,
            test_two_users_are_never_confused,
            test_missing_token_still_rejected,
        ):
            fn()
    finally:
        httpx.AsyncClient = real
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
