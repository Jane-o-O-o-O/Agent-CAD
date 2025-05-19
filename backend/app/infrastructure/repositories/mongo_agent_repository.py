from typing import Optional, List
from datetime import datetime
from app.domain.models.agent import Agent
from app.domain.models.memory import Memory
from app.domain.repositories.agent_repository import AgentRepository
from app.infrastructure.models.mongo_agent import MongoAgent, MongoAgentMemories
import logging


logger = logging.getLogger(__name__)

class MongoAgentRepository(AgentRepository):
    """MongoDB implementation of AgentRepository"""

    async def save(self, agent: Agent) -> None:
        """Save or update an agent"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent.id
        )
        
        if not mongo_agent:
            mongo_agent = MongoAgent(
                agent_id=agent.id,
                model_name=agent.model_name,
                temperature=agent.temperature,
                max_tokens=agent.max_tokens,
                sandbox_id=agent.sandbox_id,
                memories=agent.memories,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
            )
        
        # Update fields
        mongo_agent.model_name = agent.model_name
        mongo_agent.temperature = agent.temperature
        mongo_agent.max_tokens = agent.max_tokens
        mongo_agent.sandbox_id = agent.sandbox_id
        mongo_agent.memories = agent.memories
        mongo_agent.updated_at = agent.updated_at
        
        await mongo_agent.save()

    async def find_by_id(self, agent_id: str) -> Optional[Agent]:
        """Find an agent by its ID"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        return self._to_domain_model(mongo_agent) if mongo_agent else None

    async def update_memory(self, agent_id: str,
                          agent_name: str,
                          memory: Memory) -> Optional[Agent]:
        """Update the memory of an agent"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        if not mongo_agent:
            return None

        mongo_agent.memories[agent_name] = MongoAgentMemories(messages=memory.messages)
            
        mongo_agent.updated_at = datetime.utcnow()
        await mongo_agent.save()
        
        return self._to_domain_model(mongo_agent)

    async def get_memory(self, agent_id: str, name: str) -> Memory:
        """Get memory by name from agent, create if not exists"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        if not mongo_agent:
            raise ValueError(f"Agent {agent_id} not found")
            
        agent = self._to_domain_model(mongo_agent)
        memory = agent.memories.get(name)
        if not memory:
            memory = Memory()
            agent.memories[name] = memory
            await self.save(agent)
        return memory

    def _to_domain_model(self, mongo_agent: MongoAgent) -> Agent:
        """Convert MongoDB document to domain model"""

        return Agent(
            id=mongo_agent.agent_id,
            model_name=mongo_agent.model_name,
            temperature=mongo_agent.temperature,
            max_tokens=mongo_agent.max_tokens,
            sandbox_id=mongo_agent.sandbox_id,
            created_at=mongo_agent.created_at,
            updated_at=mongo_agent.updated_at,
            memories={
                name: Memory(messages=mongo_memory.messages)
                for name, mongo_memory in mongo_agent.memories.items()
            }
        ) 