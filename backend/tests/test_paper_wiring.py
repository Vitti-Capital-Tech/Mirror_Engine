# Runnable from anywhere: put backend/ (the `app` package) on the path.
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
"""Paper accounts must be invisible to the real path, and real accounts to paper.

Run:  cd backend && python tests/test_paper_wiring.py
No network, no Redis, no Supabase.

THE MISTAKE THIS FILE EXISTS BECAUSE OF. The design rests on there being one
place that decides which client an account gets. I guarded the two construction
sites on the copy path and called it done. There were ELEVEN: the daily report,
the fill report, the dashboard's balance and position reads, the live-view
panel, the close-position endpoint and the "test connection" button each built
their own client from an account row. A paper account would have read as broken
everywhere a human actually looks, and been absent from the one report that was
supposed to grade it. Worse, the first version of this very test asserted "two
construction sites" and passed, because I had taught it my own wrong answer.

So the decision now lives in app/services/client_factory.client_for, every site
holding an account row calls it, and the check below is the real invariant
rather than a restatement of my assumption.

THE ASYMMETRY THAT SHAPES is_paper. The two ways of getting the branch wrong
are not remotely equal:

  * a PAPER account handed a real DeltaClient -> it has no keys, so every call
    fails loudly and nobody is hurt.
  * a REAL account handed a PaperClient -> its orders are never placed, the
    engine believes they were, and NOTHING SAYS SO. A desk would sit flat while
    its dashboard showed it in the market.

So `environment` must equal the exact word, and the first checks here are all
about the second case.

THE PROPERTY WORTH REMEMBERING: with no paper rows in the database, not one
line of behaviour changes. The feature is inert until somebody creates one.
"""
import asyncio
import inspect
import pathlib
import sys

from app.core import connection_manager as cm
from app.services.delta_client import DeltaClient
from app.services.paper_client import PaperClient, is_paper

FAILURES = []

REAL = {"id": "f-real", "name": "Dhwani1", "is_master": False, "status": "active",
        "owner_id": "u1", "master_id": "m-dh", "api_key": "k", "api_secret": "s",
        "environment": "demo", "allocation_mode": "auto_ratio", "balance": 5000.0}
PAPER = {"id": "f-paper", "name": "Paper1", "is_master": False, "status": "active",
         "owner_id": "u1", "master_id": "m-dh", "api_key": None, "api_secret": None,
         "environment": "paper", "allocation_mode": "auto_ratio", "balance": 5000.0}


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


def connect(account):
    mgr = cm.ConnectionManager()
    return asyncio.run(mgr.connect_account(dict(account)))


# ---------------------------------------------------------------------------


def test_every_shape_of_a_real_account_gets_the_real_client():
    """THE DANGEROUS DIRECTION."""
    print("\n1. a real account can never be mistaken for paper")
    for label, extra in [
        ("environment missing", {"environment": None}),
        ("live", {"environment": "live"}),
        ("demo", {"environment": "demo"}),
        ("production", {"environment": "production"}),
        ("an empty string", {"environment": ""}),
        ("boolean true in the column", {"environment": True}),
        ("the number 1", {"environment": 1}),
        ('a word containing it, "paper-trading"', {"environment": "paper-trading"}),
        ("a stray is_paper flag, which means nothing now", {"is_paper": True}),
    ]:
        check("%s -> not paper" % label, is_paper(dict(REAL, **extra)), False)


def test_only_the_exact_word_is_paper():
    print("\n2. and the shapes that ARE paper")
    check("paper", is_paper(dict(REAL, environment="paper")), True)
    # Case and surrounding spaces come from a form, not from a decision.
    check("PAPER", is_paper(dict(REAL, environment="PAPER")), True)
    check("' paper '", is_paper(dict(REAL, environment=" paper ")), True)
    check("no account at all -> not paper", is_paper(None), False)


def test_a_real_follower_still_gets_a_deltaclient():
    print("\n3. connect_account builds the real client for a real follower")
    c = connect(REAL)
    check_true("it is a DeltaClient", isinstance(c, DeltaClient), type(c).__name__)
    check_true("and not a PaperClient", not isinstance(c, PaperClient), type(c).__name__)
    asyncio.run(c.close())


def test_a_paper_follower_gets_a_paperclient():
    print("\n4. and the paper client for a paper follower")
    c = connect(PAPER)
    check_true("it is a PaperClient", isinstance(c, PaperClient), type(c).__name__)
    asyncio.run(c.close())


def test_a_paper_account_is_refused_as_a_master():
    """Three guards, because a fake master driving real followers is the one
    thing here that could lose somebody else's money: the API refuses to create
    one, PaperClient refuses to be built for one, and the engine refuses to hand
    one a client."""
    print("\n5. the master path refuses paper outright")
    from app.core.copy_engine import CopyEngine
    eng = CopyEngine.__new__(CopyEngine)
    eng._master_clients = {}
    master = dict(REAL, id="m-paper", name="PaperMaster", is_master=True,
                  environment="paper")
    check("no client for a paper master", eng._get_master_client(master), None)
    got = eng._get_master_client(dict(REAL, id="m-real", name="Dhwani", is_master=True))
    check_true("a real master still gets one", isinstance(got, DeltaClient),
               type(got).__name__)


def _construction_sites():
    """Every line in app/ that constructs a DeltaClient."""
    out = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        if f.as_posix() == "app/services/delta_client.py":
            continue                      # its own definition
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "DeltaClient(" in line and "import" not in line and "class " not in line:
                out.append((f.as_posix(), i))
    return out


def test_nothing_builds_a_client_from_an_account_row_except_the_factory():
    """THE INVARIANT THE DESIGN RESTS ON — and the one the first version of this
    file got wrong.

    Only four files may name DeltaClient, each for a reason that cannot involve
    a paper row:

      client_factory   — it IS the decision.
      accounts /verify — credentials typed into the form; no account row exists
                         yet, and a paper account has none to verify.
      trade_listener   — the MASTER's websocket feed, and paper is never a master.
      copy_engine      — the master client, reached only after is_paper refuses.

    A twelfth site now fails this instead of silently half-working.
    """
    print("\n6. only the factory builds a client from an account row")
    allowed = {"app/services/client_factory.py", "app/api/accounts.py",
               "app/core/trade_listener.py", "app/core/copy_engine.py"}
    sites = _construction_sites()
    check("no construction outside the allowed files",
          [s for s in sites if s[0] not in allowed], [])
    check("and no new allowed file has crept in",
          sorted({s[0] for s in sites}), sorted(allowed))
    from app.core.copy_engine import CopyEngine
    src = inspect.getsource(CopyEngine._get_master_client)
    check_true("the master site refuses paper BEFORE constructing",
               src.index("is_paper(master_row)") < src.index("DeltaClient("), True)


def test_the_factory_is_used_where_an_account_row_is_in_hand():
    """The other half of the invariant: the nine sites really do call it. These
    are exactly the places a paper account was going to read as broken."""
    print("\n7. the account-derived sites go through the factory")
    for path, want in {
        "app/api/positions.py": 4,            # positions, balance sync, live view, close
        "app/core/order_compare.py": 1,       # the daily order report
        "app/core/fill_compare.py": 1,        # the fill report
        "app/api/accounts.py": 1,             # the "test connection" button
        "app/core/connection_manager.py": 1,  # the live follower client
    }.items():
        got = sum(1 for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
                  if "client_for(" in l and "import" not in l and "def " not in l)
        check("%s calls client_for %d time(s)" % (path, want), got, want)


def test_the_guards_read_the_way_they_should():
    print("\n8. and the guards say what they mean")
    from app.services import client_factory, paper_client
    check_true("the factory asks is_paper",
               "is_paper(account)" in inspect.getsource(client_factory.client_for), True)
    cm_src = inspect.getsource(cm.ConnectionManager.connect_account)
    check_true("connect_account warns nothing will be traded",
               "no orders will reach" in cm_src, True)
    src = inspect.getsource(paper_client.is_paper)
    check_true("is_paper is an exact match, not truthiness",
               '== PAPER_ENV' in src, True)
    check_true("and PaperClient refuses to be a master",
               "cannot be a master" in inspect.getsource(PaperClient.__init__), True)


if __name__ == "__main__":
    for t in (test_every_shape_of_a_real_account_gets_the_real_client,
              test_only_the_exact_word_is_paper,
              test_a_real_follower_still_gets_a_deltaclient,
              test_a_paper_follower_gets_a_paperclient,
              test_a_paper_account_is_refused_as_a_master,
              test_nothing_builds_a_client_from_an_account_row_except_the_factory,
              test_the_factory_is_used_where_an_account_row_is_in_hand,
              test_the_guards_read_the_way_they_should):
        t()
    print("\n" + ("ALL PASS" if not FAILURES
                  else "FAILURES: %d  %s" % (len(FAILURES), FAILURES)))
    sys.exit(1 if FAILURES else 0)
