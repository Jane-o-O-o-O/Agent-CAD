from typing import Optional, List, Protocol
from app.domain.models.agent import Agent
from app.domain.models.plan import Plan
from app.domain.models.memory import Memory

class AgentRepository(Protocol):
    """Repository interface for Agent aggregate"""
    
    async def save(self, agent: Agent) -> None:
        """Save or update an agent"""
        ...
    
    async def find_by_id(self, agent_id: str) -> Optional[Agent]:
        """Find an agent by its ID"""
        ...
    
    async def update_memory(self, agent_id: str,
                            agent_name: str,
                            memory: Memory) -> Optional[Agent]:
        """Update the memory of an agent"""
        ...

    async def get_memory(self, agent_id: str, name: str) -> Memory:
        """Get memory by name from agent, create if not exists"""
        ... 