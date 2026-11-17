"""Regression: each holder's alerts go to their own Telegram chat.

Run:  venv/Scripts/python.exe test_alert_routing.py
No network, no Redis, no Supabase — the db handle is a stub.

THE CHANGE. One desk watches every account, but a client should see their own
and nothing else. A shared alert group leaks the other holders' symbols, sizes
and balances to everyone in it.

So: TELEGRAM_CHAT_ROUTES maps owner_id -> chat_id. A holder listed there gets
their accounts' alerts in their chat and NOWHERE else; everyone unlisted keeps
going to TELEGRAM_CHAT_ID exactly as before.

WHY IT IS KEYED ON owner_id. Pratik's accounts are named "Dhwani" and
"Dhwani1". Matching on the name would be unreadable to anyone maintaining the
config, and a rename would silently route his alerts back into the main group
with nobody noticing until he asked why he had gone quiet.

WHAT MUST NEVER HAPPEN. An alert in the wrong group is a privacy problem; an
alert in NO group is a trading problem, and the second is worse. Every failure
path below — bad JSON, an unknown account, a database that will not answer —
has to fall back to the main chat rather than drop the message.
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

import app.services.telegram_client as tg

FAILURES = []

MAIN = "-1001111111111"          # the desk's group
PRATIK_CHAT = "-1003734430389"   # the client's own group
PRATIK = "48cb7aae-2eb3-42b1-a049-5ca757caba33"
KAJAL = "867a7192-0000-0000-0000-000000000000"

ROWS = [
    {"id": "acc-dhwani",  "name": "Dhwani",   "owner_id": PRATIK},
    {"id": "acc-dhwani1", "name": "Dhwani1",  "owner_id": PRATIK},
    {"id": "acc-algo4",   "name": "JD algo4", "owner_id": KAJAL},
    {"id": "acc-algo3",   "name": "JD algo3", "owner_id": KAJAL},
]


def check(name, got, want):
    ok = got == want
    print("  " + ("PASS" if ok else "FAIL") + "  " + name
          + ("" if ok else "  got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


class Q:
    def __init__(self, rows, boom=False):
        self.rows, self.boom = rows, boom

    def select(self, *a, **kw):
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("supabase unreachable")
        return type("R", (), {"data": list(self.rows)})()


class DB:
    def __init__(self, rows, boom=False):
        self.rows, self.boom = rows, boom

    def table(self, _n):
        return Q(self.rows, self.boom)


def configure(routes_json, rows=ROWS, boom=False):
    """Reset every cache so each case starts clean."""
    tg.settings.TELEGRAM_CHAT_ID = MAIN
    tg.settings.TELEGRAM_CHAT_ROUTES = routes_json
    tg._routes_cache = None
    tg._owner_of, tg._owner_by_id, tg._owner_of_ts = {}, {}, 0.0
    tg._migrated_chat_id = None
    import app.database
    app.database.db = DB(rows, boom)


ROUTES = '{"%s": "%s"}' % (PRATIK, PRATIK_CHAT)


def chat_for(**kw):
    return asyncio.run(tg._chat_for(**kw))


# ---------------------------------------------------------------------------


def test_each_holder_gets_their_own_chat():
    print("\n1. THE WHOLE POINT: the two desks do not see each other")
    configure(ROUTES)
    check("Dhwani -> Pratik's group", chat_for(account="Dhwani"), PRATIK_CHAT)
    check("Dhwani1 -> Pratik's group", chat_for(account="Dhwani1"), PRATIK_CHAT)
    check("JD algo4 -> main group", chat_for(account="JD algo4"), MAIN)
    check("JD algo3 -> main group", chat_for(account="JD algo3"), MAIN)


def test_alerts_that_only_know_the_account_id():
    print("\n2. send_alert carries an id, not a name")
    configure(ROUTES)
    check("by id, Pratik's", chat_for(account_id="acc-dhwani"), PRATIK_CHAT)
    check("by id, Kajal's", chat_for(account_id="acc-algo3"), MAIN)


def test_an_unlisted_holder_is_unaffected():
    print("\n3. nothing configured = nothing changes")
    configure("")
    check("Pratik's still goes to main", chat_for(account="Dhwani"), MAIN)
    check("Kajal's still goes to main", chat_for(account="JD algo4"), MAIN)


def test_nothing_is_ever_dropped():
    print("\n4. every failure falls back to the main chat, never to silence")
    configure("{not json at all")
    check("broken config", chat_for(account="Dhwani"), MAIN)

    configure(ROUTES, boom=True)
    check("database unreachable", chat_for(account="Dhwani"), MAIN)

    configure(ROUTES)
    check("account nobody has heard of", chat_for(account="Mystery"), MAIN)
    check("no account on the alert at all", chat_for(), MAIN)


def test_a_renamed_account_still_reaches_its_owner():
    print("\n5. the reason this is keyed on owner_id, not name")
    renamed = [dict(r, name="Pratik main") if r["name"] == "Dhwani" else r for r in ROWS]
    configure(ROUTES, rows=renamed)
    check("renamed, still his", chat_for(account="Pratik main"), PRATIK_CHAT)
    check("old name now unknown -> main", chat_for(account="Dhwani"), MAIN)


def test_a_supergroup_migration_still_applies():
    print("\n6. a migrated main chat does not leak into a routed one")
    configure(ROUTES)
    tg._migrated_chat_id = "-1009999999999"
    check("main follows the migration", chat_for(account="JD algo4"), "-1009999999999")
    check("Pratik's is untouched by it", chat_for(account="Dhwani"), PRATIK_CHAT)


if __name__ == "__main__":
    for t in (test_each_holder_gets_their_own_chat,
              test_alerts_that_only_know_the_account_id,
              test_an_unlisted_holder_is_unaffected,
              test_nothing_is_ever_dropped,
              test_a_renamed_account_still_reaches_its_owner,
              test_a_supergroup_migration_still_applies):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
