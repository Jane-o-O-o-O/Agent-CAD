from app.domain.events.agent_events import AgentEvent
from app.domain.models.agent import Agent
from typing import AsyncGenerator
from abc import ABC, abstractmethod
from app.domain.repositories.agent_repository import AgentRepository

class BaseFlow(ABC):
    def __init__(self, agent_id: str, agent_repository: AgentRepository):
        self._agent_id = agent_id
        self._repository = agent_repository

    @abstractmethod
    def run(self) -> AsyncGenerator[AgentEvent, None]:
        pass

    @abstractmethod
    def is_done(self) -> bool:
        pass
