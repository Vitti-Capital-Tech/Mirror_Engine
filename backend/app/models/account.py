from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AccountEnvironment(str, Enum):
    demo = "demo"
    live = "live"


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
    api_key: str = Field(..., min_length=1)
    api_secret: str = Field(..., min_length=1)
    is_master: bool = Field(default=False)
    environment: AccountEnvironment = Field(default=AccountEnvironment.demo)
    allocation_mode: Optional[AllocationMode] = None
    allocation_value: Optional[float] = Field(default=None, gt=0)
    allocated_balance: Optional[float] = Field(default=None, gt=0)
    max_position_size: Optional[float] = Field(default=None, gt=0)
    leverage_limit: int = Field(default=10, ge=1, le=200)


class AccountUpdate(BaseModel):
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
    created_at: datetime
    updated_at: datetime
    # Who owns this account. Declared here because response_model silently drops
    # anything it does not know about: without it the dashboard could not tell
    # one tenant's accounts from another's and grouped them all together, which
    # looked correct only for as long as there was a single owner.
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
