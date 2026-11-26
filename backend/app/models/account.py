from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class AccountEnvironment(str, Enum):
    demo = "demo"
    live = "live"
    # A FOLLOWER that trades nothing: a name and a balance, no credentials. It
    # mirrors a real master so the copy path can be exercised against live
    # master flow with no money at risk. `environment` is the single source of
    # truth for this -- see app/services/paper_client.is_paper -- because a
    # separate boolean would have been a second source of truth for one fact,
    # which is how the two disagree later.
    paper = "paper"


class AccountStatus(str, Enum):
    active = "active"
    paused = "paused"
    error = "error"
    circuit_break = "circuit_break"


class AllocationMode(str, Enum):
    fixed = "fixed"           # fixed lot size per trade
    multiplier = "multiplier" # master qty * multiplier
    capital_pct = "capital_pct"  # % of follower capital
    auto_ratio = "auto_ratio"   # dynamic ratio (follower balance / master balance)


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # OPTIONAL ONLY FOR PAPER. A paper account has no credentials to give, so
    # these cannot be required outright -- but a REAL account saved without
    # them fails later, at connect time, far from the form that caused it. The
    # validator below enforces the actual rule: keys unless paper, balance if
    # paper, and never a paper master.
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    is_master: bool = Field(default=False)
    environment: AccountEnvironment = Field(default=AccountEnvironment.demo)
    allocation_mode: Optional[AllocationMode] = None
    allocation_value: Optional[float] = Field(default=None, gt=0)
    allocated_balance: Optional[float] = Field(default=None, gt=0)
    # THE PAPER FORM'S ONE REQUIRED NUMBER. For a demo or live account the
    # balance is read from the exchange wallet and anything set here is
    # overwritten within seconds, so it is only meaningful for paper -- where
    # there is no wallet to read and this IS the wallet.
    balance: Optional[float] = Field(default=None, gt=0)
    max_position_size: Optional[float] = Field(default=None, gt=0)
    leverage_limit: int = Field(default=10, ge=1, le=200)
    # MASTER accounts only: how far a follower's SL/TP sits from the master's.
    # Its followers straddle that price — the first at +offset, the second at
    # -offset — so they do not all trigger in the same instant. Left unset, the
    # engine keeps its built-in deterministic +/-(0..20) hash jitter.
    #
    # NOT constrained here, on purpose. This field and master_id were each
    # DECLARED TWICE in this class; pydantic keeps the LATER declaration, so the
    # `gt=0` that sat on this line has never actually applied -- the field has
    # carried no constraints at all, and a 0 or a negative was accepted. Nothing
    # depended on it: _jitter_trigger normalises the value where it is USED
    # (`mag = abs(float(offset))`, then `if mag > 0`), so a negative reads as its
    # magnitude and a zero falls back to the hash jitter -- deliberately, "so a
    # bad value can never stack a follower on the master's exact price".
    # Deduplicated 2026-11-26 keeping the PERMISSIVE declaration: re-imposing
    # the constraint would start rejecting a 0 that callers have been allowed to
    # send for months, in order to guard something already guarded.
    tp_sl_offset: Optional[float] = None
    # FOLLOWERS only: which master this one mirrors. One owner may run several
    # independent master/follower groups, so the group cannot be inferred from
    # owner_id any more — it has to be stated. Required when creating a follower
    # for an owner who has more than one master; inferred when there is only one.
    master_id: Optional[str] = None
    # ADMIN ONLY, and ignored from anyone else. An admin sees every tenant's
    # accounts, so "add an account" is ambiguous: without this the new row was
    # always stamped with the ADMIN's own id, which quietly put it in a group of
    # its own with no master to mirror — a follower that could never copy
    # anything. Omitted means "mine", which is what a normal user always gets.
    owner_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_credentials_and_balance(self):
        """What an account needs depends on what KIND of account it is.

        api_key/api_secret cannot be required outright any more, because a paper
        account has none to give. But a REAL account saved without them does not
        fail here — it fails much later, at connect time, far from the form that
        caused it, as "Failed to connect client". So the rule moves here, where
        it can say which field is missing and why.

        Three rules, in the order they matter:

        1. A PAPER ACCOUNT IS NEVER A MASTER. A fake master would drive real
           followers off invented trades, which is the one thing in this system
           that could lose somebody else's money. Refused here, again in
           PaperClient's constructor, and again in CopyEngine._get_master_client.
        2. A real account needs its credentials.
        3. A paper account needs a balance, because the whole point of it is the
           ratio it produces — and auto_ratio divides by it. A paper follower
           with no balance would size every copy at zero and look like a bug.
        """
        paper = self.environment == AccountEnvironment.paper
        if paper and self.is_master:
            raise ValueError(
                "A paper account cannot be a master — it would drive real "
                "followers off trades that never happened. Make it a follower "
                "of a real master instead."
            )
        if not paper and not (self.api_key and self.api_secret):
            raise ValueError(
                "api_key and api_secret are required for a demo or live "
                "account. Choose the 'paper' environment for an account that "
                "trades nothing."
            )
        # `balance` SPECIFICALLY, not allocated_balance. PaperClient.get_wallet
        # reports `balance` -- it is the wallet -- while allocated_balance is a
        # separate allocation override the sizing code applies on top. Accepting
        # the override as a substitute would create a paper account whose wallet
        # reads zero, which sizes every copy at zero and looks exactly like a bug.
        if paper and not (self.balance or 0):
            raise ValueError(
                "A paper account needs a balance — it is what the copy ratio is "
                "worked out from."
            )
        if paper and (self.api_key or self.api_secret):
            raise ValueError(
                "A paper account must not carry API credentials — it never "
                "reaches an exchange, so keys here would only be a liability."
            )
        return self


class AccountUpdate(BaseModel):
    tp_sl_offset: Optional[float] = Field(default=None, gt=0)
    master_id: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    is_master: Optional[bool] = None
    environment: Optional[AccountEnvironment] = None
    allocation_mode: Optional[AllocationMode] = None
    allocation_value: Optional[float] = Field(default=None, gt=0)
    allocated_balance: Optional[float] = Field(default=None, gt=0)
    max_position_size: Optional[float] = Field(default=None, gt=0)
    leverage_limit: Optional[int] = Field(default=None, ge=1, le=200)
    status: Optional[AccountStatus] = None


class AccountResponse(BaseModel):
    id: str
    name: str
    api_key: str  # masked — last 4 chars only
    is_master: bool
    environment: AccountEnvironment
    status: AccountStatus
    allocation_mode: Optional[AllocationMode] = None
    allocation_value: Optional[float] = None
    allocated_balance: Optional[float] = None
    max_position_size: Optional[float] = None
    leverage_limit: int
    consecutive_failures: int
    balance: Optional[float] = None
    available_margin: Optional[float] = None
    today_pnl: float
    # today_pnl is realised + unrealised; this is the realised half on its own,
    # so the dashboard can show the two without one hiding inside the other.
    realized_pnl_today: float = 0.0
    created_at: datetime
    updated_at: datetime
    # Who owns this account. Declared here because response_model silently drops
    # anything it does not know about: without it the dashboard could not tell
    # one tenant's accounts from another's and grouped them all together, which
    # looked correct only for as long as there was a single owner.
    tp_sl_offset: Optional[float] = None
    master_id: Optional[str] = None
    owner_id: Optional[str] = None
    # Filled in for ADMIN callers only (api/accounts.py). A uuid is not a label;
    # this is what lets the UI head a group with the person it belongs to.
    owner_email: Optional[str] = None

    class Config:
        from_attributes = True


class AccountTestResult(BaseModel):
    success: bool
    message: str
    balance: Optional[float] = None
