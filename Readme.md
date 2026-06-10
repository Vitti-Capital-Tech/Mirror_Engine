# Mirror Engine

An institutional-grade, low-latency copy trading platform designed for Delta Exchange India. 

The platform monitors trade executions on a primary Master account and replicates them concurrently across multiple Follower accounts in real-time. It handles order scaling, margin validation, position tracking, and slippage controls to ensure institutional reliability.

For deep technical insights, review the High-Level Design at [docs/hld.md](file:///d:/Work/Projects/trades_copy/docs/hld.md) and Low-Level Design specifications at [docs/lld.md](file:///d:/Work/Projects/trades_copy/docs/lld.md).

---

## Business Concept (How It Works)

### The Problem
When managing capital across multiple accounts, executing trades manually on each individual account is slow, prone to errors, and results in different entry prices (slippage). If market liquidity is low, later accounts get worse prices.

### The Solution
Mirror Engine acts as an automated bridge:
1. **Master Execution**: The principal trader executes a trade manually on the Master Account.
2. **Instant Copying**: The system intercepts the order fill details in less than 15 milliseconds and automatically calculates the proportional order size for each follower account based on their custom allocation multiplier.
3. **Concurrent Order Routing**: The system submits the scaled market orders to all Follower Accounts at the exact same time, minimizing price differences.

---

## Core System Architecture

```text
 Master Account (Delta Exchange)
            │
            ▼ (WebSocket Fill Notification)
  [Trade Listener Service]
            │
            ▼ (Pushed to Broker)
    [Redis Event Queue]
            │
            ▼ (Consumed by Worker)
    [Parallel Copy Engine] ───► [Risk Engine] (Validates Margin)
            │
            ▼
    [Order Executor] ───► Concurrently executes orders on Follower Accounts
```

---

## Codebase Map & File Links

Below is the directory map linking to the core files that make up the copy trading engine:

### Infrastructure & Configuration
* [LICENSE](file:///d:/Work/Projects/trades_copy/LICENSE): Software copyright and proprietary terms.
* [.gitignore](file:///d:/Work/Projects/trades_copy/.gitignore): Repository file ignore rules.
* [docker-compose.yml](file:///d:/Work/Projects/trades_copy/docker-compose.yml): Multi-container services configuration.

### Backend Core Services
* [database/supabase_schema.sql](file:///d:/Work/Projects/trades_copy/backend/database/supabase_schema.sql): PostgreSQL tables schema for accounts, copies, and audits.
* [core/trade_listener.py](file:///d:/Work/Projects/trades_copy/backend/app/core/trade_listener.py): Real-time handler listening to the master account WebSocket.
* [core/copy_engine.py](file:///d:/Work/Projects/trades_copy/backend/app/core/copy_engine.py): Broker loop processing trade events off Redis queue.
* [core/risk_engine.py](file:///d:/Work/Projects/trades_copy/backend/app/core/risk_engine.py): Account check limits and size calculations.
* [core/order_executor.py](file:///d:/Work/Projects/trades_copy/backend/app/core/order_executor.py): Asynchronous multi-account execution engine and circuit breaker.
* [core/position_monitor.py](file:///d:/Work/Projects/trades_copy/backend/app/core/position_monitor.py): Periodic auditor checking size drift.
* [core/slippage_tracker.py](file:///d:/Work/Projects/trades_copy/backend/app/core/slippage_tracker.py): Accuracy calculator flagging price differences.
* [services/delta_client.py](file:///d:/Work/Projects/trades_copy/backend/app/services/delta_client.py): Delta Exchange REST API client with signed HMAC authentication.

### Frontend Dashboard Components
* [src/app/layout.tsx](file:///d:/Work/Projects/trades_copy/frontend/src/app/layout.tsx): Root layout with custom theme styles.
* [src/app/page.tsx](file:///d:/Work/Projects/trades_copy/frontend/src/app/page.tsx): Main dashboard control panel and chart grids.
* [src/app/accounts/page.tsx](file:///d:/Work/Projects/trades_copy/frontend/src/app/accounts/page.tsx): Account addition and monitoring table page.
* [src/components/layout/TopBar.tsx](file:///d:/Work/Projects/trades_copy/frontend/src/components/layout/TopBar.tsx): Top header bar showing live WebSocket status and theme toggle controls.

---

## Key Features

### Slippage Protection
The system compares the execution price of the Follower against the Master. The target slippage threshold is set to 0.03%. If price difference exceeds this warning threshold, the system immediately logs a warning alert on the dashboard feed.

### Multi-Account Allocation
Administrators can scale order sizes per account:
* **Fixed Quantity**: Follower receives the exact quantity executed by the master.
* **Multiplier Mode**: Follower copies a scaled quantity (e.g. 2x master size).

### Failure Protection (Circuit Breaker)
If a follower account experiences 5 consecutive order execution errors, the system automatically marks the account status as `blocked` and suspends copy trading operations on it to protect capital.

---

## Operations Guide

### Starting the Backend
1. Install Python dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
2. Configure `.env` using `.env.example` as a template.
3. Start the FastAPI development server:
   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

### Starting the Frontend
1. Install node dependencies:
   ```bash
   cd frontend
   npm install
   ```
2. Launch the client dev server:
   ```bash
   npm run dev
   ```
3. Open `http://localhost:3000` (or `http://localhost:3001` if port 3000 is occupied) to access the console.