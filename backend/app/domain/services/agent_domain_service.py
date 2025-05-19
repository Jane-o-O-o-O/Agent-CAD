from typing import Optional, AsyncGenerator
import asyncio
import logging
from app.domain.models.agent import Agent
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.events.agent_events import AgentEvent, ErrorEvent, DoneEvent
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.services.agent_runtime import AgentRuntime

# Setup logging
logger = logging.getLogger(__name__)

class AgentDomainService:
    """
    Agent domain service, responsible for coordinating the work of planning agent and execution agent
    """
    
    def __init__(self, agent_repository: AgentRepository):
        self._runtime = AgentRuntime(agent_repository)
        self._repository = agent_repository
        logger.info("AgentDomainService initialization completed")
    
    async def create_agent_runtime(self, agent_id: str, llm: LLM, sandbox: Sandbox, browser: Browser, 
                     search_engine: Optional[SearchEngine] = None) -> None:
        """Initialize Agent runtime context and start its task"""
        
        # Create runtime context and start task
        self._runtime.create_and_start_task(agent_id, llm, sandbox, browser, search_engine)
        logger.info(f"Agent {agent_id} initialization completed and task started")

    async def close_agent(self, agent_id: str) -> bool:
        """Clean up specified Agent's resources"""
        logger.info(f"Starting to close Agent {agent_id}")
        
        # Close runtime resources
        await self._runtime.close_agent(agent_id)
        
        logger.info(f"Agent {agent_id} has been fully closed and resources cleared")
        return True
            
    async def shutdown(self) -> None:
        """Clean up all Agent's resources"""
        await self._runtime.shutdown()

    async def chat(self, agent_id: str, message: Optional[str] = None, timestamp: Optional[int] = None) -> AsyncGenerator[AgentEvent, None]:
        """
        Complete business process for handling user messages:
        1. Create plan
        2. Execute plan
        """
        if not self._runtime.has_agent(agent_id):
            logger.error(f"Attempted to chat with non-existent Agent {agent_id}")
            yield ErrorEvent(error="Agent not initialized")
            return
            
        runtime_context = self._runtime.get_runtime_context(agent_id)
        
        # Get agent
        agent = await self._repository.find_by_id(agent_id)
        
        # Put message into queue if it's new
        if message:
            logger.debug(f"Putting message into Agent {agent_id}'s message queue: {message[:50]}...")
            await runtime_context.msg_queue.put(message)
        else:
            if runtime_context.flow.is_done():
                logger.info(f"Agent {agent_id} flow is done")
                yield DoneEvent()
                return
        
        # Ensure task and queue are initialized
        self._runtime.ensure_task(agent_id)

        # Get events from event queue and yield to caller
        while True:
            event = await runtime_context.event_queue.get()
            logger.debug(f"Got event from Agent {agent_id}'s event queue: {type(event).__name__}")
            yield event
            runtime_context.event_queue.task_done()
            
            # If done event is received, end generation
            if isinstance(event, DoneEvent):
                logger.debug(f"Agent {agent_id} received done event, ending generation")
                break
