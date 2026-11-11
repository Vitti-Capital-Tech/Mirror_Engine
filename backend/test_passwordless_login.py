"""Regression: email-only login, and the admin shortcut that must survive it.

Run:  venv/Scripts/python.exe test_passwordless_login.py
No network, no Redis, no Supabase — httpx is stubbed.

WHAT THIS IS. The team wanted Option_Scope's login: type an email, you are in,
no password. Option_Scope derives the password in the BROWSER:

    const derivedPassword = `OptionScope_${cleanEmail}_Secure123!`;

which ships in the JS bundle from a public repo, so anyone can compute anyone's
password. Option_Scope is paper trading; this system holds live Delta API keys
that place real orders, and now carries a SECOND tenant's accounts as well. The
behaviour is the same by request; the derivation is kept server-side so the
formula is not readable from devtools.

TWO THINGS THAT WERE BROKEN AND ARE EASY TO BREAK AGAIN.

1. It did not work for anyone who already had an account. The derived grant was
   tried, and on failure the code called "create user", which answers "already
   registered" and changes nothing — so the retry failed too. Verified against
   the live project on 2026-11-11: prathavkevadiya@, danani.j@ and
   tusharbhardwaj2617@ all returned HTTP 400. All 7 existing users, including
   every Google sign-in and the admin, were locked out of email-only login.
   An existing account is now pointed at the derived password instead.

2. The admin account must never be re-derived. ADMIN_PASSWORD is pinned in .env
   and the `trade` shortcut grants with it; deriving a new password for
   ADMIN_EMAIL would overwrite it and silently kill the shortcut for everyone.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import app.api.auth as A
from app.config import settings

FAILURES = []
CALLS = []


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {}
        self.content = b"{}"
        self.text = str(self._p)

    def json(self):
        return self._p


def session_for(email):
    return {"access_token": "at-" + email, "refresh_token": "rt", "expires_in": 3600,
            "user": {"id": "uid-" + email, "email": email}}


class FakeClient:
    """Stands in for httpx.AsyncClient. `known` maps email -> real password."""
    known = {}
    users = []          # admin-list rows

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if "grant_type=password" in url:
            email = (json or {}).get("email", "").lower()
            pw = (json or {}).get("password")
            CALLS.append(("grant", email))
            if FakeClient.known.get(email) == pw:
                return Resp(200, session_for(email))
            return Resp(400, {"msg": "Invalid login credentials"})
        if "admin/users" in url:
            email = (json or {}).get("email", "").lower()
            CALLS.append(("create", email))
            if email in FakeClient.known:
                return Resp(422, {"msg": "User already registered"})
            FakeClient.known[email] = (json or {}).get("password")
            FakeClient.users.append({"id": "uid-" + email, "email": email})
            return Resp(200, {"id": "uid-" + email, "email": email})
        return Resp(404)

    async def get(self, url, headers=None, params=None):
        if "admin/users" in url:
            page = (params or {}).get("page", 1)
            CALLS.append(("list", page))
            return Resp(200, {"users": FakeClient.users if page == 1 else []})
        return Resp(404)

    async def put(self, url, headers=None, json=None):
        uid = url.rsplit("/", 1)[-1]
        CALLS.append(("setpw", uid))
        for u in FakeClient.users:
            if u["id"] == uid:
                FakeClient.known[u["email"]] = (json or {}).get("password")
                return Resp(200, u)
        return Resp(404, {"msg": "not found"})


def setup(existing=None, passwordless=True, magic="trade"):
    CALLS.clear()
    FakeClient.known = dict(existing or {})
    FakeClient.users = [{"id": "uid-" + e, "email": e} for e in (existing or {})]
    A.httpx.AsyncClient = FakeClient
    settings.PASSWORDLESS_LOGIN = passwordless
    settings.ADMIN_MAGIC_CODE = magic
    settings.ADMIN_EMAIL = "admin@vitti.capital"
    settings.ADMIN_PASSWORD = "the-real-admin-password"
    FakeClient.known["admin@vitti.capital"] = "the-real-admin-password"
    FakeClient.users.append({"id": "uid-admin", "email": "admin@vitti.capital"})
    # _ensure_profile touches Supabase; not what these tests are about.
    A._ensure_profile = lambda *a, **kw: asyncio.sleep(0)


def login(email, password=""):
    return asyncio.run(A.login(A.LoginIn(email=email, password=password)))


# ---------------------------------------------------------------------------


def test_a_brand_new_email_just_works():
    print("\n1. a never-seen email signs in, creating the account")
    setup()
    out = login("newbie@vitti.capital")
    check("signed in", out["access_token"], "at-newbie@vitti.capital")
    check("account was created", ("create", "newbie@vitti.capital") in CALLS, True)
    check("no 2FA step", out.get("twofa_required"), False)


def test_an_existing_account_can_now_use_it():
    print("\n2. THE BUG: an account that already existed was locked out")
    # Exactly the live case — a real password of their own (or a Google sign-in).
    setup(existing={"prathavkevadiya@gmail.com": "their-own-password"})
    out = login("prathavkevadiya@gmail.com")
    check("signed in", out["access_token"], "at-prathavkevadiya@gmail.com")
    check("looked the user up", any(c[0] == "list" for c in CALLS), True)
    check("pointed it at the derived password",
          any(c[0] == "setpw" for c in CALLS), True)
    check("did NOT try to re-create it",
          any(c[0] == "create" for c in CALLS), False)
    # Before the fix: grant 400 -> create says "already registered" -> grant 400.


def test_second_login_needs_no_repair():
    print("\n3. once pointed at it, the derived password just grants")
    setup(existing={"x@vitti.capital": "old"})
    login("x@vitti.capital")
    CALLS.clear()
    login("x@vitti.capital")
    check("one grant, nothing else", [c[0] for c in CALLS], ["grant"])


def test_the_admin_account_is_never_rederived():
    print("\n4. the admin account must keep its pinned password")
    setup()
    out = login("admin@vitti.capital")
    check("signed in", out["access_token"], "at-admin@vitti.capital")
    check("password was NOT reset", any(c[0] == "setpw" for c in CALLS), False)
    check("ADMIN_PASSWORD still valid",
          FakeClient.known["admin@vitti.capital"], "the-real-admin-password")
    # And the shortcut still works afterwards — this is the thing that breaks.
    out2 = login("trade")
    check("`trade` still signs in as admin", out2["access_token"],
          "at-admin@vitti.capital")


def test_trade_in_the_email_box():
    print("\n5. THE ASK: `trade` typed in the EMAIL box logs in as admin")
    setup()
    out = login("trade")
    check("signed in as admin", out["access_token"], "at-admin@vitti.capital")
    check("granted with ADMIN_PASSWORD", ("grant", "admin@vitti.capital") in CALLS, True)
    check("case does not matter", login("TRADE")["access_token"],
          "at-admin@vitti.capital")
    check("whitespace does not matter", login("  trade  ")["access_token"],
          "at-admin@vitti.capital")


def test_trade_still_works_in_the_password_box():
    print("\n6. the old way keeps working (saved autofill, habit)")
    setup()
    out = login("anything@whatever.com", "trade")
    check("still admin", out["access_token"], "at-admin@vitti.capital")


def test_the_magic_code_is_not_treated_as_an_email():
    print("\n7. `trade` must never be created as a user account")
    setup()
    login("trade")
    check("no account creation", any(c[0] == "create" for c in CALLS), False)


def test_a_non_email_is_refused():
    print("\n8. anything else without an @ is a typo, not a login")
    setup()
    for bad in ("prathav", "hello world", ""):
        try:
            login(bad)
            check("refused %r" % bad, False, True)
        except A.HTTPException as e:
            check("refused %r" % bad, e.status_code, 400)


def test_the_flag_gates_it():
    print("\n9. PASSWORDLESS_LOGIN=false turns the whole thing off")
    setup(passwordless=False)
    try:
        login("someone@vitti.capital")
        check("refused", False, True)
    except A.HTTPException as e:
        check("refused with 400", e.status_code, 400)
    # ...but the admin shortcut is independent of the flag.
    check("`trade` unaffected", login("trade")["access_token"],
          "at-admin@vitti.capital")


def test_no_magic_code_configured():
    print("\n10. with ADMIN_MAGIC_CODE empty, 'trade' is just a bad email")
    setup(magic="")
    try:
        login("trade")
        check("refused", False, True)
    except A.HTTPException as e:
        check("refused with 400", e.status_code, 400)


def test_the_formula_never_reaches_the_browser():
    print("\n11. the derivation is server-side and keyed on a secret")
    setup()
    a = A._derived_password("someone@vitti.capital")
    b = A._derived_password("SOMEONE@VITTI.CAPITAL  ")
    check("case/space insensitive — same account", a, b)
    check("differs per email",
          a != A._derived_password("other@vitti.capital"), True)
    check("not the Option_Scope plaintext shape", "Secure123!" in a, False)
    check("email is not recoverable from it", "someone" in a, False)
    settings.PASSWORDLESS_SECRET = "a-different-secret"
    check("changing the secret changes it",
          A._derived_password("someone@vitti.capital") != a, True)
    settings.PASSWORDLESS_SECRET = ""


def main():
    print("=" * 74)
    print("passwordless login - email only, and the admin shortcut survives it")
    print("=" * 74)
    real = A.httpx.AsyncClient
    try:
        for fn in (
            test_a_brand_new_email_just_works,
            test_an_existing_account_can_now_use_it,
            test_second_login_needs_no_repair,
            test_the_admin_account_is_never_rederived,
            test_trade_in_the_email_box,
            test_trade_still_works_in_the_password_box,
            test_the_magic_code_is_not_treated_as_an_email,
            test_a_non_email_is_refused,
            test_the_flag_gates_it,
            test_no_magic_code_configured,
            test_the_formula_never_reaches_the_browser,
        ):
            fn()
    finally:
        A.httpx.AsyncClient = real
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
