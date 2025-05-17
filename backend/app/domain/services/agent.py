from typing import Optional, AsyncGenerator, Dict
import asyncio
import logging
from dataclasses import dataclass
from app.domain.models.memory import Memory
from app.domain.models.agent import Agent
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.models.event import (
    AgentEvent,
    ErrorEvent,
    DoneEvent
)
from app.domain.services.flows.plan_act import PlanActFlow

# Setup logging
logger = logging.getLogger(__name__)

# Define resource collection class
@dataclass
class AgentContext:
    agent: Agent
    flow: PlanActFlow
    sandbox: Sandbox
    msg_queue: asyncio.Queue
    event_queue: asyncio.Queue
    task: Optional[asyncio.Task] = None
    last_message: Optional[str] = None
    last_message_time: Optional[int] = None


class AgentDomainService:
    """
    Agent domain service, responsible for coordinating the work of planning agent and execution agent
    """
    
    def __init__(self):
        # Store and manage Agent and related resources, key is agent_id, value is resource collection
        self._contexts: Dict[str, AgentContext] = {}
        logger.info("AgentDomainService initialization completed")
    
    def create_agent(self, model_name: str, llm: LLM, sandbox: Sandbox, browser: Browser, 
                     search_engine: Optional[SearchEngine] = None, 
                     temperature: float = 0.7, 
                     max_tokens: Optional[int] = None) -> Agent:
        """Create and initialize Agent, including related agents and resources"""
        # Create Agent instance, ID will be generated automatically
        agent = Agent(
            planner_memory=Memory(),
            execution_memory=Memory(),
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        agent_id = agent.id
        logger.info(f"Created new Agent, ID: {agent_id}, model: {model_name}")
        
        # Check if resources for this agent_id already exist (although the probability is very small)
        if agent_id in self._contexts:
            logger.error(f"Agent with ID {agent_id} already exists")
            raise ValueError(f"Agent with ID {agent_id} already exists")
        
        flow = PlanActFlow(agent, llm, sandbox, browser, search_engine)
        
        # Create resource collection
        self._contexts[agent_id] = AgentContext(
            agent=agent,
            flow=flow,
            sandbox=sandbox,
            msg_queue=asyncio.Queue(),
            event_queue=asyncio.Queue()
        )
        
        # Create and start task
        self._contexts[agent_id].task = asyncio.create_task(
            self._run_flow_task(agent_id)
        )
        logger.info(f"Agent {agent_id} initialization completed and task started")
        
        return agent
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get specified ID Agent instance"""
        context = self._contexts.get(agent_id)
        if not context:
            logger.warning(f"Attempted to get non-existent Agent: {agent_id}")
        return context.agent if context else None
    
    def has_agent(self, agent_id: str) -> bool:
        """Check if specified ID Agent exists"""
        exists = agent_id in self._contexts
        logger.debug(f"Checking if Agent {agent_id} exists: {exists}")
        return exists

    async def _run_flow(self, agent_id: str, message: Optional[str] = None) -> AsyncGenerator[AgentEvent, None]:
        """
        Complete business process for handling user messages:
        1. Create plan
        2. Execute plan
        """
        # Get related resources
        context = self._contexts.get(agent_id)
        
        if not context:
            logger.error(f"Agent {agent_id} not initialized")
            yield ErrorEvent(error="Agent not initialized")
            return

        if not message:
            logger.warning(f"Agent {agent_id} received empty message")
            yield ErrorEvent(error="No message")
            return
        
        async for event in context.flow.run(message):
            yield event

    def _ensure_task(self, agent_id: str) -> None:
        """Ensure specified agent's task and queue are initialized and running normally"""
        context = self._contexts.get(agent_id)
        if not context:
            logger.warning(f"Attempted to ensure task for non-existent Agent {agent_id}")
            return
        
        # Check if task needs to be restarted (does not exist or completed or cancelled or encountered exception)
        task_needs_restart = (
            context.task is None or 
            context.task.done() or 
            context.task.cancelled()
        )
        
        if task_needs_restart:
            # Create and start new task
            logger.info(f"Agent {agent_id} task needs restart, creating new task")
            context.task = asyncio.create_task(
                self._run_flow_task(agent_id)
            )

    async def chat(self, agent_id: str, message: Optional[str] = None, timestamp: Optional[int] = None) -> AsyncGenerator[AgentEvent, None]:
        """
        Complete business process for handling user messages, using asynchronous tasks and queues:
        1. Create plan
        2. Execute plan
        """
        if agent_id not in self._contexts:
            logger.error(f"Attempted to chat with non-existent Agent {agent_id}")
            yield ErrorEvent(error="Agent not initialized")
            return
        context = self._contexts[agent_id]
        
        
        # Put message into queue
        if message and not (context.last_message == message and context.last_message_time == timestamp):
            logger.debug(f"Putting message into Agent {agent_id}'s message queue: {message[:50]}...")
            await context.msg_queue.put(message)
            context.last_message = message
            context.last_message_time = timestamp
        else:
            if context.flow.is_idle():
                logger.info(f"Agent {agent_id} flow is idle")
                yield DoneEvent()
                return
        
        
        # Ensure task and queue are initialized
        self._ensure_task(agent_id)

        # Get events from event queue and yield to caller
        while True:
            event = await context.event_queue.get()
            logger.debug(f"Got event from Agent {agent_id}'s event queue: {type(event).__name__}")
            yield event
            context.event_queue.task_done()
            
            # If done event is received, end generation
            if isinstance(event, DoneEvent):
                logger.debug(f"Agent {agent_id} received done event, ending generation")
                break
    
    async def _run_flow_task(self, agent_id: str) -> None:
        """Process specified agent's message queue"""
        try:
            logger.info(f"Agent {agent_id} message processing task started")
            while True:
                context = self._contexts.get(agent_id)
                
                if not context:
                    logger.warning(f"Agent {agent_id} context does not exist, ending task")
                    break
                
                logger.debug(f"Agent {agent_id} waiting for message...")
                message = await context.msg_queue.get()
                logger.info(f"Agent {agent_id} received new message: {message[:50]}...")
                
                # Call original chat method to process message, and put event into queue
                async for event in self._run_flow(agent_id, message):
                    await context.event_queue.put(event)
                    if not context.msg_queue.empty():
                        break
                
                context.msg_queue.task_done()
                logger.debug(f"Agent {agent_id} completed processing one message")
        except asyncio.CancelledError:
            # Clean up work when task is cancelled
            logger.info(f"Agent {agent_id} task cancelled")
            pass
        except Exception as e:
            # Put exception information into event queue when exception occurs
            logger.exception(f"Agent {agent_id} task encountered exception: {str(e)}")
            context = self._contexts.get(agent_id)
            if context:
                await context.event_queue.put(ErrorEvent(error=f"Task error: {str(e)}"))
                await context.event_queue.put(DoneEvent())
        
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
        context = self._contexts.get(agent_id)
        
        if not context:
            logger.warning(f"Attempted to close non-existent Agent {agent_id}")
            return False

        # 1. Cancel and clean up task
        if context.task and not context.task.done():
            logger.debug(f"Cancelling Agent {agent_id}'s task")
            context.task.cancel()
            try:
                await context.task
            except asyncio.CancelledError:
                pass
        
        # 2. Clean up queue resources
        logger.debug(f"Clearing Agent {agent_id}'s message queue")
        await self._clear_queue(context.msg_queue)
        logger.debug(f"Clearing Agent {agent_id}'s event queue")
        await self._clear_queue(context.event_queue)
        
        # 3. Destroy sandbox environment
        if context.sandbox:
            logger.debug(f"Destroying Agent {agent_id}'s sandbox environment")
            await context.sandbox.destroy()
        
        # 4. Remove resource collection
        self._contexts.pop(agent_id, None)
        logger.info(f"Agent {agent_id} has been fully closed and resources cleared")
        return True
            
    async def shutdown(self) -> None:
        """Clean up all Agent's resources"""
        logger.info(f"Starting to close all Agents, currently {len(self._contexts)} in total")
        # Close resources of each agent one by one
        for agent_id in list(self._contexts.keys()):
            await self.close_agent(agent_id)
        logger.info("All Agents have been closed")
    
    def get_sandbox(self, agent_id: str) -> Optional[Sandbox]:
        """Get specified agent's sandbox"""
        context = self._contexts.get(agent_id)
        if not context:
            logger.warning(f"Attempted to get sandbox for non-existent Agent {agent_id}")
        return context.sandbox if context else None

agent_domain_service = AgentDomainService()
