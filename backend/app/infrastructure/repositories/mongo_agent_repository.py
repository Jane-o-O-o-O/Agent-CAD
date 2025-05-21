from typing import Optional, List
from datetime import datetime, UTC
from app.domain.models.agent import Agent
from app.domain.models.memory import Memory
from app.domain.repositories.agent_repository import AgentRepository
from app.infrastructure.models.mongo_agent import MongoAgent
from app.infrastructure.models.mongo_memory import MongoMemory
from bson import ObjectId
import logging


logger = logging.getLogger(__name__)

class MongoAgentRepository(AgentRepository):
    """MongoDB implementation of AgentRepository"""

    def _create_mongo_agent(self, agent: Agent) -> MongoAgent:
        """Create a new MongoDB agent from domain agent"""
        return MongoAgent(
            agent_id=agent.id,
            model_name=agent.model_name,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            sandbox_id=agent.sandbox_id,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            memories={
                name: MongoMemory(messages=memory.messages)
                for name, memory in agent.memories.items()
            }
        )

    async def save(self, agent: Agent) -> None:
        """Save or update an agent"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent.id
        )
        
        if not mongo_agent:
            mongo_agent = self._create_mongo_agent(agent)
            await mongo_agent.save()
            return
        
        # Update fields
        mongo_agent.model_name = agent.model_name
        mongo_agent.temperature = agent.temperature
        mongo_agent.max_tokens = agent.max_tokens
        mongo_agent.sandbox_id = agent.sandbox_id
        mongo_agent.updated_at = datetime.now(UTC)
        
        await mongo_agent.save()

    async def find_by_id(self, agent_id: str) -> Optional[Agent]:
        """Find an agent by its ID"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        return self._to_domain_agent(mongo_agent) if mongo_agent else None

    async def add_memory(self, agent_id: str,
                          name: str,
                          memory: Memory) -> Optional[Agent]:
        """Add or update a memory for an agent"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        if not mongo_agent:
            return None

        # Create and save MongoMemory object
        mongo_memory = MongoMemory(messages=memory.messages)
        await mongo_memory.save()
        
        # Update agent's memories
        mongo_agent.memories[name] = mongo_memory
        mongo_agent.updated_at = datetime.now(UTC)
        await mongo_agent.save()
        
        return self._to_domain_agent(mongo_agent)
    

    async def get_memory(self, agent_id: str, name: str) -> Memory:
        """Get memory by name from agent, create if not exists"""
        mongo_agent = await MongoAgent.find_one(
            MongoAgent.agent_id == agent_id
        )
        if not mongo_agent:
            raise ValueError(f"Agent {agent_id} not found")
        mongo_memory = mongo_agent.memories.get(name)
        if not mongo_memory:
            # Create a new memory
            mongo_memory = MongoMemory(messages=[])
            await mongo_memory.save()
            # Update agent's memories
            mongo_agent.memories[name] = mongo_memory
            mongo_agent.updated_at = datetime.now(UTC)
            await mongo_agent.save()
        else:
            mongo_memory = await mongo_memory.fetch()
        
        return self._to_domain_memory(mongo_memory)
    
    async def save_memory(self, memory: Memory) -> None:
        """Update the messages of a memory"""
        mongo_memory = await MongoMemory.find_one(
            MongoMemory.id == ObjectId(memory.id)
        )
        if not mongo_memory:
            raise ValueError(f"Memory {memory.id} not found")
        
        mongo_memory.messages = memory.messages
        await mongo_memory.save()


    def _to_domain_memory(self, mongo_memory: MongoMemory) -> Memory:
        """Convert MongoDB document to domain model"""
        return Memory(id=str(mongo_memory.id), messages=mongo_memory.messages)

    def _to_domain_agent(self, mongo_agent: MongoAgent) -> Agent:
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
                name: self._to_domain_memory(mongo_memory)
                for name, mongo_memory in mongo_agent.memories.items()
            }
        ) 