import logging
from functools import lru_cache

from app.infrastructure.config import get_settings
from app.infrastructure.storage.mongodb import get_mongodb
from app.infrastructure.storage.redis import get_redis
from app.infrastructure.external.file.gridfsfile import get_file_storage
from app.infrastructure.external.search import get_search_engine

# Import all required services
from app.application.services.agent_service import AgentService
from app.application.services.file_service import FileService

# Import all required dependencies for agent service
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.task.redis_task import RedisStreamTask
from app.infrastructure.utils.llm_json_parser import LLMJsonParser
from app.infrastructure.repositories.mongo_agent_repository import MongoAgentRepository
from app.infrastructure.repositories.mongo_session_repository import MongoSessionRepository
from app.infrastructure.repositories.file_mcp_repository import FileMCPRepository


# Configure logging
logger = logging.getLogger(__name__)

@lru_cache()
def get_agent_service() -> AgentService:
    """
    Get agent service instance with all required dependencies
    
    This function creates and returns an AgentService instance with all
    necessary dependencies. Uses lru_cache for singleton pattern.
    """
    logger.info("Creating AgentService instance")
    
    # Create all dependencies
    llm = OpenAILLM()
    agent_repository = MongoAgentRepository()
    session_repository = MongoSessionRepository()
    sandbox_cls = DockerSandbox
    task_cls = RedisStreamTask
    json_parser = LLMJsonParser()
    file_storage = get_file_storage()
    search_engine = get_search_engine()
    mcp_repository = FileMCPRepository()
    
    # Create AgentService instance
    return AgentService(
        llm=llm,
        agent_repository=agent_repository,
        session_repository=session_repository,
        sandbox_cls=sandbox_cls,
        task_cls=task_cls,
        json_parser=json_parser,
        file_storage=file_storage,
        search_engine=search_engine,
        mcp_repository=mcp_repository,
    )


@lru_cache()
def get_file_service() -> FileService:
    """
    Get file service instance with required dependencies
    
    This function creates and returns a FileService instance with
    the necessary file storage dependency.
    """
    logger.info("Creating FileService instance")
    
    # Get file storage dependency
    file_storage = get_file_storage()
    
    return FileService(
        file_storage=file_storage,
    )
