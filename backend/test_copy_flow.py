import asyncio
import json
import os
import sys
import redis

# Add backend dir to path to load settings
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

def push_mock_trade():
    print(f"Connecting to Redis at: {settings.REDIS_URL}")
    client = redis.from_url(settings.REDIS_URL)
    
    # 1. Verify connection
    try:
        client.ping()
        print("Connected to Redis successfully.")
    except Exception as e:
        print(f"Failed to connect to Redis: {e}")
        return

    # 2. Mock trade payload
    # This represents a filled BUY trade on the master account (BTCUSD contract size: 10)
    mock_payload = {
        "master_trade_id": f"mock-trade-{int(asyncio.time.time() if hasattr(asyncio, 'time') else 1718000000)}",
        "symbol": "BTCUSD",
        "side": "buy",
        "quantity": 10.0,
        "entry_price": 67000.0,
        "trade_type": "entry",
        "raw_payload": {"info": "Mock trade simulation"}
    }
    
    print("\nPushing mock master trade event:")
    print(json.dumps(mock_payload, indent=2))
    
    # Push to 'trade_events' queue
    client.lpush("trade_events", json.dumps(mock_payload))
    print("\nEvent pushed to Redis queue 'trade_events'.")
    print("If the backend server is running, you should see the Copy Engine pick it up, validate margins, execute orders (limit/market), and update the live logs console instantly!")

if __name__ == "__main__":
    push_mock_trade()
