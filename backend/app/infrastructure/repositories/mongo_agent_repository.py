from typing import Optional, List
from datetime import datetime, UTC
from app.domain.models.agent import Agent
from app.domain.models.memory import Memory
from app.domain.repositories.agent_repository import AgentRepository
from app.infrastructure.models.documents import AgentDocument
import logging


logger = logging.getLogger(__name__)

class MongoAgentRepository(AgentRepository):
    """MongoDB implementation of AgentRepository"""

    async def save(self, agent: Agent) -> None:
        """Save or update an agent"""
        mongo_agent = await AgentDocument.find_one(
            AgentDocument.agent_id == agent.id
        )
        
        if not mongo_agent:
            mongo_agent = self._to_mongo_agent(agent)
            await mongo_agent.save()
            return
        
        # Use generic update method from base class
        mongo_agent.model_name=agent.model_name
        mongo_agent.temperature=agent.temperature
        mongo_agent.max_tokens=agent.max_tokens
        mongo_agent.memories=agent.memories
        mongo_agent.updated_at=datetime.now(UTC)
        await mongo_agent.save()

    async def find_by_id(self, agent_id: str) -> Optional[Agent]:
        """Find an agent by its ID"""
        mongo_agent = await AgentDocument.find_one(
            AgentDocument.agent_id == agent_id
        )
        return self._to_domain_agent(mongo_agent) if mongo_agent else None

    async def add_memory(self, agent_id: str,
                          name: str,
                          memory: Memory) -> None:
        """Add or update a memory for an agent"""
        result = await AgentDocument.find_one(
            AgentDocument.agent_id == agent_id
        ).update(
            {"$set": {f"memories.{name}": memory, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Agent {agent_id} not found")

    async def get_memory(self, agent_id: str, name: str) -> Memory:
        """Get memory by name from agent, create if not exists"""
        mongo_agent = await AgentDocument.find_one(
            AgentDocument.agent_id == agent_id
        )
        if not mongo_agent:
            raise ValueError(f"Agent {agent_id} not found")
        return mongo_agent.memories.get(name, Memory(messages=[]))
    
    async def save_memory(self, agent_id: str, name: str, memory: Memory) -> None:
        """Update the messages of a memory"""
        result = await AgentDocument.find_one(
            AgentDocument.agent_id == agent_id
        ).update(
            {"$set": {f"memories.{name}": memory, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Agent {agent_id} not found")

    def _to_domain_agent(self, mongo_agent: AgentDocument) -> Agent:
        """Convert MongoDB document to domain model"""

        return Agent(
            id=mongo_agent.agent_id,
            model_name=mongo_agent.model_name,
            temperature=mongo_agent.temperature,
            max_tokens=mongo_agent.max_tokens,
            created_at=mongo_agent.created_at,
            updated_at=mongo_agent.updated_at,
            memories=mongo_agent.memories
        )
    

    def _to_mongo_agent(self, agent: Agent) -> AgentDocument:
        """Create a new MongoDB agent from domain agent"""
        return AgentDocument(
            agent_id=agent.id,
            model_name=agent.model_name,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            memories=agent.memories
        )