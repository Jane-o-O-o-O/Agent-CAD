from typing import Optional, Dict, AsyncGenerator
import asyncio
import logging
from dataclasses import dataclass
from app.domain.events.agent_events import AgentEvent, ErrorEvent, DoneEvent
from app.domain.services.flows.plan_act import PlanActFlow
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.external.llm import LLM
from app.domain.models.agent import Agent
from app.domain.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

@dataclass
class RuntimeContext:
    """Runtime context that cannot be persisted"""
    flow: PlanActFlow
    sandbox: Sandbox
    msg_queue: asyncio.Queue
    event_queue: asyncio.Queue
    task: Optional[asyncio.Task] = None

class AgentRuntime:
    """Runtime class for handling agent execution lifecycle"""
    
    def __init__(self, agent_repository: AgentRepository):
        self._repository = agent_repository
        self._runtime_contexts: Dict[str, RuntimeContext] = {}
    
    def create_and_start_task(self, agent_id: str, llm: LLM, sandbox: Sandbox, browser: Browser, 
                             search_engine: Optional[SearchEngine] = None) -> None:
        """Create runtime context and start agent task"""
        # Create runtime context
        runtime_context = self._create_runtime_context(agent_id, llm, sandbox, browser, search_engine)
        
        # Create and start task
        self.ensure_task(agent_id)
    
    def _create_runtime_context(self, agent_id: str, llm: LLM, sandbox: Sandbox, browser: Browser, 
                             search_engine: Optional[SearchEngine] = None) -> RuntimeContext:
        """Create a new runtime context for an agent"""
        # Create flow
        flow = PlanActFlow(agent_id, self._repository, llm, sandbox, browser, search_engine)
        
        # Create runtime context
        runtime_context = RuntimeContext(
            flow=flow,
            sandbox=sandbox,
            msg_queue=asyncio.Queue(),
            event_queue=asyncio.Queue()
        )
        
        # Set runtime context
        self._runtime_contexts[agent_id] = runtime_context
        
        return runtime_context
    
    def get_runtime_context(self, agent_id: str) -> Optional[RuntimeContext]:
        """Get runtime context for specified agent"""
        return self._runtime_contexts.get(agent_id)
    
    def has_agent(self, agent_id: str) -> bool:
        """Check if specified agent exists"""
        return agent_id in self._runtime_contexts
    
    async def _run_flow(self, agent_id: str, flow: PlanActFlow, message: Optional[str] = None) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent's flow with the given message"""
        if not message:
            logger.warning(f"Agent {agent_id} received empty message")
            yield ErrorEvent(error="No message")
            return
        
        async for event in flow.run(message):
            yield event
    
    def ensure_task(self, agent_id: str) -> None:
        """Ensure specified agent's task is initialized and running normally"""
        runtime_context = self._runtime_contexts.get(agent_id)
        if not runtime_context:
            logger.warning(f"Attempted to ensure task for non-existent Agent {agent_id}")
            return
        
        task_needs_restart = (
            runtime_context.task is None or 
            runtime_context.task.done() or 
            runtime_context.task.cancelled()
        )
        
        if task_needs_restart:
            logger.info(f"Agent {agent_id} task needs restart, creating new task")
            runtime_context.task = asyncio.create_task(
                self._run_flow_task(agent_id)
            )
    
    async def _run_flow_task(self, agent_id: str) -> None:
        """Process specified agent's message queue"""
        try:
            logger.info(f"Agent {agent_id} message processing task started")
            while True:
                runtime_context = self._runtime_contexts.get(agent_id)
                
                if not runtime_context:
                    logger.warning(f"Agent {agent_id} context does not exist, ending task")
                    break
                
                logger.debug(f"Agent {agent_id} waiting for message...")
                message = await runtime_context.msg_queue.get()
                logger.info(f"Agent {agent_id} received new message: {message[:50]}...")
                
                async for event in self._run_flow(agent_id, runtime_context.flow, message):
                    await runtime_context.event_queue.put(event)
                    if not runtime_context.msg_queue.empty():
                        break
                
                runtime_context.msg_queue.task_done()
                logger.debug(f"Agent {agent_id} completed processing one message")
                
        except asyncio.CancelledError:
            logger.info(f"Agent {agent_id} task cancelled")
            pass
        except Exception as e:
            logger.exception(f"Agent {agent_id} task encountered exception: {str(e)}")
            runtime_context = self._runtime_contexts.get(agent_id)
            if runtime_context:
                await runtime_context.event_queue.put(ErrorEvent(error=f"Task error: {str(e)}"))
                await runtime_context.event_queue.put(DoneEvent())
    
    async def _clear_queue(self, queue: asyncio.Queue) -> None:
        """Empty specified queue"""
        cleared_count = 0
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
                cleared_count += 1
            except asyncio.QueueEmpty:
                break
        logger.debug(f"Cleared queue, removed {cleared_count} items")
    
    async def close_agent(self, agent_id: str) -> bool:
        """Clean up specified Agent's resources"""
        logger.info(f"Starting to close Agent {agent_id}")
        runtime_context = self._runtime_contexts.get(agent_id)
        
        if not runtime_context:
            logger.warning(f"Attempted to close non-existent Agent {agent_id}")
            return False

        if runtime_context.task and not runtime_context.task.done():
            logger.debug(f"Cancelling Agent {agent_id}'s task")
            runtime_context.task.cancel()
            try:
                await runtime_context.task
            except asyncio.CancelledError:
                pass
        
        await self._clear_queue(runtime_context.msg_queue)
        await self._clear_queue(runtime_context.event_queue)
        
        # Destroy sandbox environment
        if runtime_context.sandbox:
            logger.debug(f"Destroying Agent {agent_id}'s sandbox environment")
            await runtime_context.sandbox.destroy()
        
        # Remove runtime context
        self._runtime_contexts.pop(agent_id, None)
        logger.debug(f"Removed runtime context for Agent {agent_id}")
        
        return True
    
    async def shutdown(self) -> None:
        """Clean up all Agent's resources"""
        logger.info(f"Starting to close all Agents, currently {len(self._runtime_contexts)} in total")
        for agent_id in list(self._runtime_contexts.keys()):
            await self.close_agent(agent_id)
        logger.info("All Agents have been closed") 