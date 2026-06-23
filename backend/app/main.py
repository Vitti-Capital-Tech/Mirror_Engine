import asyncio
import json
import logging
import redis.asyncio as aioredis
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import db
from app.websocket.socket_manager import sio, socket_app, socket_manager
from app.core.trade_listener import trade_listener
from app.core.copy_engine import CopyEngine
from app.core.position_monitor import position_monitor
from app.core.connection_manager import connection_manager
from app.api import accounts, trades, positions, alerts, dashboard

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Module-level variable accessed by dashboard router
redis_client = None
copy_engine = None
redis_consumer_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, copy_engine, redis_consumer_task
    logger.info("Starting Copy Trading Backend...")
    
    # 1. Connect to Redis
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("Redis connected successfully.")
    
    # Set the redis client on trade_listener
    trade_listener.redis = redis_client
    
    # 2. Instantiate copy engine
    copy_engine = CopyEngine(db, redis_client, socket_manager, connection_manager)
    
    # 3. Load active accounts from Supabase
    try:
        accounts_res = db.table("accounts").select("*").execute()
        accounts_data = accounts_res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch accounts during startup: {e}")
        accounts_data = []
        
    # Initialize position monitor cache
    await position_monitor.start_monitoring(accounts_data)
    
    # 4. Connect WebSockets for active accounts
    master_accounts = [a for a in accounts_data if a.get("is_master")]
    follower_accounts = [a for a in accounts_data if not a.get("is_master")]
    
    # Connect active followers first
    for follower in follower_accounts:
        if follower.get("status") == "active":
            try:
                await connection_manager.connect_account(
                    follower,
                    on_position=position_monitor.make_position_callback(follower["id"], follower["name"])
                )
                logger.info(f"Connected WebSocket for follower: {follower['name']}")
            except Exception as e:
                logger.error(f"Failed to connect follower WS {follower['name']}: {e}")
                
    # Connect active master and start listening to trades
    if master_accounts:
        master = master_accounts[0]
        if master.get("status") == "active":
            try:
                await trade_listener.start(master)
                logger.info(f"Trade listener started for master: {master['name']}")
            except Exception as e:
                logger.error(f"Failed to start master trade listener: {e}")
                
    # 5. Start Redis Consumer for trade copying
    redis_consumer_task = asyncio.create_task(redis_consumer())
    logger.info("Background Redis consumer started. Copy trading engine is ready.")

    # 6. Trigger automatic sync of live positions on startup asynchronously
    async def startup_sync():
        await asyncio.sleep(2) # Give websockets a couple seconds to connect
        try:
            from app.api.positions import sync_live_positions
            logger.info("Triggering automatic live positions sync on startup...")
            await sync_live_positions()
            logger.info("Automatic startup live positions sync completed successfully.")
        except Exception as sync_err:
            logger.error(f"Failed automatic startup live positions sync: {sync_err}")

    asyncio.create_task(startup_sync())
    
    yield
    
    # 6. Shutdown cleanups
    logger.info("Shutting down backend...")
    if redis_consumer_task:
        redis_consumer_task.cancel()
        try:
            await redis_consumer_task
        except asyncio.CancelledError:
            pass
            
    await trade_listener.stop()
    await connection_manager.disconnect_all()
    await redis_client.close()
    logger.info("Shutdown completed.")

async def redis_consumer():
    """Background loop consuming master fill events from Redis and executing them via the CopyEngine."""
    logger.info("Redis consumer loop started.")
    while True:
        try:
            # Block for up to 1 second waiting for list elements
            data = await redis_client.brpop("trade_events", timeout=1)
            if data:
                _, raw_payload = data
                event_dict = json.loads(raw_payload)
                await copy_engine.process_fill(event_dict)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in Redis consumer loop: {e}")
            await asyncio.sleep(0.1)

app = FastAPI(
    title="Mirror Engine API",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(accounts.router)
app.include_router(trades.router)
app.include_router(positions.router)
app.include_router(alerts.router)
app.include_router(dashboard.router)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "delta-copy-trader"}

# Wrap FastAPI with the Socket.IO ASGI application
import socketio
app = socketio.ASGIApp(sio, other_asgi_app=app)
