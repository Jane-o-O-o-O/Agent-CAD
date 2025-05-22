from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import signal
import asyncio
import sys

from app.interfaces.api.routes import router
from app.application.services.agent_service import AgentService
from app.infrastructure.config import get_settings
from app.infrastructure.logging import setup_logging
from app.interfaces.api.errors.exception_handlers import register_exception_handlers
from app.infrastructure.mongodb import get_mongodb
from app.infrastructure.external.search import GoogleSearchEngine
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandboxFactory
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowserFactory
from app.infrastructure.repositories.mongo_agent_repository import MongoAgentRepository
from app.interfaces.api.routes import get_agent_service

# Initialize logging system
setup_logging()
logger = logging.getLogger(__name__)

# Load configuration
settings = get_settings()


def create_agent_service() -> AgentService:
    search_engine = None
    # Initialize search engine only if both API key and engine ID are set
    if settings.google_search_api_key and settings.google_search_engine_id:
        logger.info("Initializing Google Search Engine")
        search_engine = GoogleSearchEngine(
            api_key=settings.google_search_api_key, 
            cx=settings.google_search_engine_id
        )
    else:
        logger.warning("Google Search Engine not initialized: missing API key or engine ID")

    return AgentService(
        llm=OpenAILLM(),
        agent_repository=MongoAgentRepository(),
        sandbox_factory=DockerSandboxFactory(),
        browser_factory=PlaywrightBrowserFactory(),
        search_engine=search_engine
    )

agent_service = create_agent_service()

async def shutdown(signal_name=None) -> None:
    """Cleanup function that will be called when the application is shutting down"""
    if signal_name:
        logger.info(f"Received exit signal {signal_name}")

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

# Create lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code executed on startup
    logger.info("Application startup - Manus AI Agent initializing")
    
    # Initialize MongoDB and Beanie
    await get_mongodb().initialize()
    
    try:
        yield
    finally:
        # Code executed on shutdown
        logger.info("Application shutdown - Manus AI Agent terminating")
        # Disconnect from MongoDB
        await get_mongodb().shutdown()
        await shutdown()

app = FastAPI(title="Manus AI Agent", lifespan=lifespan)
app.dependency_overrides[get_agent_service] = lambda: agent_service

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

# Register routes with dependency injection
app.include_router(router, prefix="/api/v1")