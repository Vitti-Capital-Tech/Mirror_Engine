-- =====================================================
-- Delta India Copy Trading System — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- =====================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- ACCOUNTS
-- Stores master + follower accounts with API credentials
-- =====================================================
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    api_key TEXT NOT NULL,
    api_secret TEXT NOT NULL,
    is_master BOOLEAN DEFAULT FALSE,
    environment VARCHAR(20) DEFAULT 'demo' CHECK (environment IN ('demo', 'live')),
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error', 'circuit_break')),
    allocation_mode VARCHAR(50) CHECK (allocation_mode IN ('fixed', 'multiplier', 'capital_pct')),
    allocation_value DECIMAL(20, 8),
    max_position_size DECIMAL(20, 8),
    leverage_limit INTEGER DEFAULT 10,
    consecutive_failures INTEGER DEFAULT 0,
    balance DECIMAL(20, 8),
    available_margin DECIMAL(20, 8),
    used_margin DECIMAL(20, 8),
    today_pnl DECIMAL(20, 8) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_accounts_is_master ON accounts(is_master);
CREATE INDEX idx_accounts_status ON accounts(status);

-- =====================================================
-- TRADES
-- Master filled orders — triggers the copy chain
-- =====================================================
CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    master_trade_id VARCHAR(255) UNIQUE NOT NULL,
    symbol VARCHAR(100) NOT NULL,
    side VARCHAR(20) NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    trade_type VARCHAR(50) NOT NULL CHECK (trade_type IN ('entry', 'exit', 'sl', 'tp', 'sl_modify', 'tp_modify', 'cancel')),
    status VARCHAR(50) DEFAULT 'processing' CHECK (status IN ('processing', 'copied', 'partial', 'failed')),
    raw_payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_created ON trades(created_at DESC);
CREATE INDEX idx_trades_symbol ON trades(symbol);

-- =====================================================
-- TRADE COPIES
-- Per-follower execution record for each master trade
-- =====================================================
CREATE TABLE IF NOT EXISTS trade_copies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trade_id UUID REFERENCES trades(id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    follower_order_id VARCHAR(255),
    execution_price DECIMAL(20, 8),
    quantity DECIMAL(20, 8),
    slippage_points DECIMAL(20, 8),
    slippage_pct DECIMAL(10, 6),
    execution_time_ms INTEGER,
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'filled', 'failed', 'skipped', 'retrying')),
    failure_reason TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trade_copies_trade_id ON trade_copies(trade_id);
CREATE INDEX idx_trade_copies_account_id ON trade_copies(account_id);
CREATE INDEX idx_trade_copies_status ON trade_copies(status);
CREATE INDEX idx_trade_copies_slippage ON trade_copies(slippage_pct);

-- =====================================================
-- POSITIONS
-- Live positions per account (updated via WebSocket)
-- =====================================================
CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
    symbol VARCHAR(100) NOT NULL,
    side VARCHAR(20) NOT NULL CHECK (side IN ('long', 'short')),
    quantity DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8),
    current_price DECIMAL(20, 8),
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    sl_price DECIMAL(20, 8),
    tp_price DECIMAL(20, 8),
    sync_status VARCHAR(50) DEFAULT 'synced' CHECK (sync_status IN ('synced', 'out_of_sync', 'unknown')),
    last_synced_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, symbol)
);

CREATE INDEX idx_positions_account_id ON positions(account_id);
CREATE INDEX idx_positions_sync_status ON positions(sync_status);

-- =====================================================
-- ALERTS
-- System alerts: slippage, mismatch, failures, etc.
-- =====================================================
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level VARCHAR(20) NOT NULL CHECK (level IN ('info', 'warning', 'error', 'critical')),
    type VARCHAR(100) NOT NULL,
    account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    trade_copy_id UUID REFERENCES trade_copies(id) ON DELETE SET NULL,
    message TEXT NOT NULL,
    metadata JSONB,
    is_resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alerts_level ON alerts(level);
CREATE INDEX idx_alerts_type ON alerts(type);
CREATE INDEX idx_alerts_is_resolved ON alerts(is_resolved);
CREATE INDEX idx_alerts_created ON alerts(created_at DESC);

-- =====================================================
-- HELPER FUNCTION: update updated_at automatically
-- =====================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_accounts_updated_at BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_trade_copies_updated_at BEFORE UPDATE ON trade_copies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_positions_updated_at BEFORE UPDATE ON positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =====================================================
-- ROW LEVEL SECURITY (disable for internal tool)
-- =====================================================
ALTER TABLE accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE trades DISABLE ROW LEVEL SECURITY;
ALTER TABLE trade_copies DISABLE ROW LEVEL SECURITY;
ALTER TABLE positions DISABLE ROW LEVEL SECURITY;
ALTER TABLE alerts DISABLE ROW LEVEL SECURITY;
