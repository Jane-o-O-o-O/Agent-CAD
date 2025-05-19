from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import signal
import asyncio
import sys

from app.interfaces.api.routes import router
from app.application.services.agent_service import agent_service
from app.infrastructure.config import get_settings
from app.infrastructure.logging import setup_logging
from app.interfaces.api.errors.exception_handlers import register_exception_handlers
from app.infrastructure.mongodb import get_mongodb
from app.infrastructure.models.mongo_agent import MongoAgent, MongoAgentMemories

# Initialize logging system
setup_logging()
logger = logging.getLogger(__name__)

# Load configuration
settings = get_settings()

# Global shutdown event
shutdown_event = asyncio.Event()

async def init_mongodb():
    """Initialize MongoDB connection and Beanie ODM"""
    try:
        await get_mongodb().initialize([MongoAgent])
        logger.info("Successfully initialized MongoDB and Beanie")
    except Exception as e:
        logger.error(f"Failed to initialize MongoDB and Beanie: {str(e)}")
        sys.exit(1)

async def shutdown(signal_name=None):
    """Cleanup function that will be called when the application is shutting down"""
    if signal_name:
        logger.info(f"Received exit signal {signal_name}")
    
    # Set shutdown event
    shutdown_event.set()
    
    logger.info("Graceful shutdown...")
    
    try:
        # Create task for agent service shutdown with timeout
        shutdown_task = asyncio.create_task(agent_service.shutdown())
        try:
            await asyncio.wait_for(shutdown_task, timeout=30.0)  # 30 seconds timeout
            logger.info("Successfully completed graceful shutdown")
        except asyncio.TimeoutError:
            logger.warning("Shutdown timed out after 30 seconds")
            
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")

def handle_exit_signals():
    """Set up handlers for exit signals"""
    loop = asyncio.get_event_loop()
    
    # Handle SIGTERM and SIGINT
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(
                shutdown(signal.Signals(s).name)
            )
        )

# Create lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code executed on startup
    logger.info("Application startup - Manus AI Agent initializing")
    
    if "--reload" not in sys.argv:
        # Set up signal handlers
        handle_exit_signals()
    
    # Initialize MongoDB and Beanie
    await init_mongodb()
    
    try:
        yield
    finally:
        # Code executed on shutdown
        logger.info("Application shutdown - Manus AI Agent terminating")
        # Disconnect from MongoDB
        await get_mongodb().shutdown()
        await shutdown()

app = FastAPI(title="Manus AI Agent", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register exception handlers
register_exception_handlers(app)

# Register routes
app.include_router(router, prefix="/api/v1")