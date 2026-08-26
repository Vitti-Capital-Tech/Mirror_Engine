from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class TradeSide(str, Enum):
    buy = "buy"
    sell = "sell"


class TradeType(str, Enum):
    entry = "entry"
    exit = "exit"
    sl = "sl"
    tp = "tp"
    sl_modify = "sl_modify"
    tp_modify = "tp_modify"
    cancel = "cancel"


class TradeStatus(str, Enum):
    processing = "processing"
    copied = "copied"
    partial = "partial"
    failed = "failed"


class CopyStatus(str, Enum):
    pending = "pending"
    filled = "filled"
    # An order that executed for LESS than the follower's proportional target.
    # Distinct from 'filled' because the follower is left out of proportion with
    # the master, which is a problem to surface, not a success to hide.
    partial = "partial"
    failed = "failed"
    skipped = "skipped"
    retrying = "retrying"


class TradeEvent(BaseModel):
    """Internal event pushed to Redis queue after master fill."""
    master_trade_id: str
    symbol: str
    side: TradeSide
    quantity: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    trade_type: TradeType = TradeType.entry
    raw_payload: Optional[dict] = None

    def dict(self, **kwargs):
        # Ensure enums are serialised as strings for JSON transport
        d = super().model_dump(**kwargs)
        d["side"] = self.side.value
        d["trade_type"] = self.trade_type.value
        return d


class TradeCopyResult(BaseModel):
    """Result of copying a trade to one follower account."""
    account_id: str
    account_name: str
    status: CopyStatus
    execution_price: Optional[float] = None
    slippage_pct: Optional[float] = None
    execution_time_ms: Optional[int] = None
    failure_reason: Optional[str] = None
    # What actually filled vs what the proportional target was. Both are needed
    # to judge a copy: "3 lots" is only meaningful against the 3 (or 34) asked for.
    quantity: Optional[float] = None
    requested_quantity: Optional[float] = None


class TradeResponse(BaseModel):
    """Full trade record returned by the API."""
    id: str
    master_trade_id: str
    symbol: str
    side: TradeSide
    quantity: float
    entry_price: float
    trade_type: TradeType
    status: TradeStatus
    created_at: datetime
    copies: List[TradeCopyResult] = Field(default_factory=list)

    class Config:
        from_attributes = True


class TradeStatsResponse(BaseModel):
    total_trades: int
    successful_copies: int
    partial_copies: int = 0
    failed_copies: int
    success_rate_pct: float
    avg_slippage_pct: float
    max_slippage_pct: float
    avg_execution_time_ms: float
