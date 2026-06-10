from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    long = "long"
    short = "short"


class SyncStatus(str, Enum):
    synced = "synced"
    out_of_sync = "out_of_sync"
    unknown = "unknown"


class AlertLevel(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class PositionResponse(BaseModel):
    id: str
    account_id: str
    account_name: str
    symbol: str
    side: PositionSide
    quantity: float
    entry_price: float
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    sync_status: SyncStatus = SyncStatus.unknown
    last_synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlertResponse(BaseModel):
    id: str
    level: AlertLevel
    type: str
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    message: str
    metadata: Optional[dict] = None
    is_resolved: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_accounts: int
    active_accounts: int
    paused_accounts: int
    error_accounts: int
    master_account_name: Optional[str] = None
    total_trades_today: int
    successful_copies: int
    failed_copies: int
    success_rate_pct: float
    avg_slippage_pct: float
    max_slippage_pct: float
    total_pnl: float
    active_alerts_count: int
    websocket_connections: int


class SystemStatus(BaseModel):
    redis_ok: bool
    supabase_ok: bool
    master_ws_connected: bool
    total_ws_connections: int
    environment: str
    uptime_seconds: float
