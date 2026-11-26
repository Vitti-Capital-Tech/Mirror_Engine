"""client_for(account) — the one place that decides which client an account gets.

WHY THIS EXISTS. Eleven places in this codebase constructed a DeltaClient from
an account row. Guarding the two on the copy path and calling the job done was
wrong, and nearly shipped: the daily report, the fill report, the dashboard's
balance and position reads, the live-view panel, the close-position endpoint and
the "test connection" button each build their own, so a paper account would have
read as broken everywhere a human actually looks at it — and been absent from
the one report that was supposed to grade it.

Scattered construction is also a standing invitation to add a twelfth site that
forgets. So the decision lives here, once, and every site that has an account
row calls this instead of naming a class.

TWO SITES DELIBERATELY DO NOT USE IT, and both are about credentials rather than
accounts:

  * accounts.py `/verify` — checks a key and secret somebody just typed into the
    form. There is no account row yet, and a paper account has no credentials to
    verify, so the endpoint is never reached for one.
  * trade_listener.py — the MASTER's websocket feed. A paper account can never
    be a master (refused in the API, in PaperClient's constructor and in
    CopyEngine._get_master_client), so a paper row cannot arrive here.

THE ASYMMETRY THAT SHAPES is_paper. A paper account handed a real client has no
keys, so every call fails loudly and nobody is hurt. A REAL account handed a
PaperClient has its orders quietly not placed while the engine believes they
were — a desk sitting flat with a dashboard showing it in the market. That is
why is_paper demands the exact word and not a truthy value.
"""
from __future__ import annotations

import logging

from app.services.delta_client import DeltaClient
from app.services.paper_client import PaperClient, is_paper

logger = logging.getLogger(__name__)

# What every caller actually holds: either kind. Annotating these as DeltaClient
# said something untrue the moment a paper account could be connected, and a
# wrong type hint is worse than none -- it is the next reader's evidence.
ExchangeClient = DeltaClient | PaperClient


def client_for(account: dict, db=None):
    """The exchange client for this account row: real, or paper.

    `db` is only read for a paper account (its book lives in Supabase); real
    clients ignore it. It defaults to the process-wide handle so the nine call
    sites do not each have to thread one through.
    """
    if is_paper(account):
        if db is None:
            from app.database import db as _db
            db = _db
        return PaperClient(account, db)
    return DeltaClient(
        api_key=account["api_key"],
        api_secret=account["api_secret"],
        environment=account.get("environment", "demo"),
    )
