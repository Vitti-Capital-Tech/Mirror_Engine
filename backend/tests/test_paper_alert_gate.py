# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""A paper account's alerts never reach Telegram — including the FIRST one.

Run:  cd backend && python tests/test_paper_alert_gate.py
No network, no Redis, no Supabase — the db handle and the HTTP client are stubs.

THE GATE. A paper follower is a testing instrument; its failures are expected
and frequent, and that is the point. Letting them into the desk's group mixes
invented problems with real ones in the one channel that has to stay worth
reading. So send_message refuses anything about a paper account — one gate,
because every notify_* in that module ends there, including any added later.

THE TWO HOLES THIS CLOSES. The gate reads a set of paper account names and
ids. That set was filled only as a side effect of the ROUTING lookup, and:

  1. routing runs AFTER the gate. On a cold cache the gate checked an empty
     set, found nothing, and sent. One paper alert into the group per backend
     restart — and per newly created or renamed paper account, since the name
     is the key. The engine restarts on every deploy.
  2. the routing lookup returns early when TELEGRAM_CHAT_ROUTES is unset,
     BEFORE the fill. With no routes configured — the default — the paper set
     would never have been filled at all and every paper alert would have gone
     to the group forever. Routes are configured today, so this was latent;
     it still meant the paper gate silently depended on an unrelated setting.

WHAT MUST NOT HAPPEN. Suppressing a REAL alert. The two failure modes are not
symmetrical: a paper alert in the group is noise, a real alert swallowed is a
position nobody hears about. So every uncertain case here — unknown account,
unreadable database, no account named at all — must still send.
"""
import asyncio
import os
import sys

_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiJ9.notreal"
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_KEY"] = _JWT
os.environ["SUPABASE_SERVICE_KEY"] = _JWT
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import app.services.telegram_client as tg

FAILURES = []

MAIN = "-1001111111111"
OWNER = "48cb7aae-2eb3-42b1-a049-5ca757caba33"

ROWS = [
    {"id": "acc-algo4", "name": "JD algo4", "owner_id": OWNER, "environment": "live"},
    {"id": "acc-mini1", "name": "Mini 1", "owner_id": OWNER, "environment": "paper"},
    {"id": "acc-mini2", "name": "Mini 2", "owner_id": OWNER, "environment": "paper"},
]


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def check_true(name, ok, got=None):
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r" % (got,)))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------- stubs
class Q:
    def __init__(self, rows, boom):
        self.rows, self.boom = rows, boom

    def select(self, *_a, **_k):
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("supabase unreachable")
        return type("R", (), {"data": [dict(r) for r in self.rows]})()


class DB:
    def __init__(self, rows, boom=False):
        self.rows, self.boom, self.reads = rows, boom, 0

    def table(self, _n):
        self.reads += 1
        return Q(self.rows, self.boom)


SENT = []


class FakeResp:
    status_code = 200

    def json(self):
        return {}


class FakeClient:
    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, json=None, **_k):
        SENT.append(json or {})
        return FakeResp()


def configure(rows=ROWS, routes="", boom=False):
    """Every cache reset, exactly as a fresh backend would start."""
    SENT.clear()
    tg.settings.TELEGRAM_BOT_TOKEN = "token"
    tg.settings.TELEGRAM_CHAT_ID = MAIN
    tg.settings.TELEGRAM_CHAT_ROUTES = routes
    tg._routes_cache = None
    tg._owner_of, tg._owner_by_id, tg._owner_of_ts = {}, {}, 0.0
    tg._paper_names.clear()
    tg._paper_ids.clear()
    tg._migrated_chat_id = None
    tg.httpx.AsyncClient = FakeClient
    import app.database
    app.database.db = DB(rows, boom)
    return app.database.db


def send(**kw):
    return asyncio.run(tg.send_message("hello", **kw))


# ---------------------------------------------------------------------------
def test_the_very_first_alert_after_a_restart_is_suppressed():
    """Hole 1. Everything below used to pass; this is the one that did not."""
    print("\n1. a paper alert on a COLD cache never reaches the group")
    configure(routes='{"%s": "-100222"}' % OWNER)
    check_true("cache really is cold", not tg._paper_names, tg._paper_names)
    send(account="Mini 1")
    check("nothing sent", SENT, [])


def test_it_works_with_no_routing_configured():
    """Hole 2. The paper gate must not depend on TELEGRAM_CHAT_ROUTES."""
    print("\n2. paper is suppressed even with no routes configured")
    configure(routes="")
    send(account="Mini 2")
    check("nothing sent", SENT, [])
    send(account_id="acc-mini1")
    check("and by id too", SENT, [])


def test_a_real_account_still_sends():
    print("\n3. a real account is unaffected")
    configure(routes="")
    send(account="JD algo4")
    check("one message sent", len(SENT), 1)
    check("to the main chat", SENT[0].get("chat_id"), MAIN)


def test_an_unknown_account_still_sends():
    """Fail OPEN. An account we cannot classify is treated as real."""
    print("\n4. an unknown account is treated as real, not silenced")
    configure(routes="")
    send(account="Some Desk Nobody Registered")
    check("still sent", len(SENT), 1)


def test_an_unreadable_database_does_not_silence_anything():
    print("\n5. a database that will not answer never swallows an alert")
    configure(routes="", boom=True)
    send(account="JD algo4")
    check("real account still sent", len(SENT), 1)
    # A paper account is unclassifiable here too, so it is sent. Noise beats
    # silence; this records the deliberate direction of the failure.
    send(account="Mini 1")
    check("and paper is sent rather than guessed at", len(SENT), 2)


def test_a_message_with_no_account_is_not_gated():
    """The daily report sends with no account at all, so the gate cannot see
    it. Recorded here as current behaviour, not as an endorsement."""
    print("\n6. a message naming no account still goes out")
    configure(routes="")
    send()
    check("sent", len(SENT), 1)


def test_a_paper_account_created_after_startup_is_picked_up():
    print("\n7. a paper account added later is caught on the next refresh")
    db = configure(routes="")
    send(account="JD algo4")
    check("real account sent", len(SENT), 1)

    db.rows = ROWS + [{"id": "acc-mini3", "name": "Mini anon 2",
                       "owner_id": OWNER, "environment": "paper"}]
    tg._owner_of_ts = 0.0          # the 60s TTL lapses
    send(account="Mini anon 2")
    check("the new paper account is suppressed", len(SENT), 1)


def test_the_account_read_is_not_made_per_alert():
    """The gate now refreshes before deciding. That must stay one read per
    TTL, not one per message — the reconciler can emit a burst of them."""
    print("\n8. the refresh is cached, not paid per alert")
    db = configure(routes="")
    for _ in range(6):
        send(account="JD algo4")
    check("six alerts sent", len(SENT), 6)
    check_true("but only one account read", db.reads <= 1, db.reads)


if __name__ == "__main__":
    for t in (test_the_very_first_alert_after_a_restart_is_suppressed,
              test_it_works_with_no_routing_configured,
              test_a_real_account_still_sends,
              test_an_unknown_account_still_sends,
              test_an_unreadable_database_does_not_silence_anything,
              test_a_message_with_no_account_is_not_gated,
              test_a_paper_account_created_after_startup_is_picked_up,
              test_the_account_read_is_not_made_per_alert):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
