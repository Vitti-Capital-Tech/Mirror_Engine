"""Regression: the Supabase connection must not die of HTTP/2 stream exhaustion.

Run:  venv/Scripts/python.exe test_db_transport.py
No network — builds a client against a fake URL and inspects the transport.

supabase-py 2.4.6 enables HTTP/2 on the PostgREST session. A long-lived HTTP/2
connection is capped on how many streams it will carry; at the cap the server
sends a graceful GOAWAY and every request already dispatched onto it fails:

    ConnectionTerminated error_code:0, last_stream_id:19999

11 of those on 2026-09-04, each one breaking something real and silent —
"Error handling position update", "Error checking position sync", "Failed to sync
live positions for account Jigar", "Failed to read accounts". error_code 0 is
NO_ERROR: nothing is wrong, the connection is simply used up.
(Prathav, 2026-09-04: "4-5th we should have a failure mechanism".)

HTTP/2's only benefit here is multiplexing concurrent requests, and this client is
SYNCHRONOUS — each call blocks the event loop for its round trip, so requests are
serialised and there is nothing to multiplex. Pinning HTTP/1.1 therefore costs
nothing and removes the cap entirely, which beats retrying around it.

There are 113 `.execute()` call sites in the app, so this had to be fixed in one
place rather than wrapped at each caller.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT

import httpx
from supabase import create_client
from app.database import _force_http11

FAILURES = []


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def fresh():
    return create_client("https://fake.supabase.co", _JWT)


def pool(client):
    return client.postgrest.session._transport._pool


def test_the_default_is_the_problem():
    print("\n1. supabase-py's default really does enable HTTP/2")
    # If this ever flips, the fix is unnecessary and should be reconsidered
    # rather than left in place reaching into private attributes.
    check("http2 on by default", pool(fresh())._http2, True)


def test_pinning_turns_it_off():
    print("\n2. pinning switches the PostgREST session to HTTP/1.1")
    c = fresh()
    _force_http11(c)
    check("http2 off", pool(c)._http2, False)


def test_connect_retries_are_added():
    print("\n3. and adds connect retries for a closed keep-alive socket")
    c = fresh()
    _force_http11(c)
    check("retries", pool(c)._retries, 2)


def test_nothing_else_is_disturbed():
    print("\n4. base_url and auth headers survive the swap")
    c = fresh()
    before_url = str(c.postgrest.session.base_url)
    before_keys = {k.lower() for k in c.postgrest.session.headers}
    _force_http11(c)
    check("base_url unchanged", str(c.postgrest.session.base_url), before_url)
    check("headers unchanged", {k.lower() for k in c.postgrest.session.headers},
          before_keys)
    check("apikey still present",
          "apikey" in {k.lower() for k in c.postgrest.session.headers}, True)
    check("still an httpx transport",
          isinstance(c.postgrest.session._transport, httpx.HTTPTransport), True)


def test_it_cannot_stop_the_app_starting():
    print("\n5. supabase-py internals changing must not refuse to start")
    # It reaches into a private attribute because ClientOptions in 2.4.6 exposes
    # no transport option. If that shape changes, degrade to today's behaviour.
    class Odd:
        pass

    broken = Odd()
    try:
        _force_http11(broken)          # no .postgrest at all
        check("swallowed and logged", True, True)
    except Exception as e:
        check("swallowed and logged", "raised %s" % type(e).__name__, True)


def test_get_db_applies_it():
    print("\n6. get_db() hands back a pinned client, not a raw one")
    import app.database as dbmod
    dbmod._db_client = None            # force a rebuild
    c = dbmod.get_db()
    check("http2 off on the singleton", pool(c)._http2, False)


def main():
    print("=" * 74)
    print("supabase transport - no HTTP/2 stream cap to exhaust")
    print("=" * 74)
    for fn in (
        test_the_default_is_the_problem,
        test_pinning_turns_it_off,
        test_connect_retries_are_added,
        test_nothing_else_is_disturbed,
        test_it_cannot_stop_the_app_starting,
        test_get_db_applies_it,
    ):
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
