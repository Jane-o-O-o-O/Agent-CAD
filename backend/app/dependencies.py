from functools import lru_cache
from app.application.services.agent_service import AgentService
from app.application.services.file_service import FileService


@lru_cache()
def get_agent_service() -> AgentService:
    # Placeholder for dependency injection
    return None

@lru_cache()
def get_file_service() -> FileService:
    # Placeholder for dependency injection
    return None