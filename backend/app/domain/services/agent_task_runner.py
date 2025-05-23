from typing import Optional, Dict, AsyncGenerator, Callable, Tuple, Any
import asyncio
import logging
from app.domain.events.agent_events import AgentEvent, ErrorEvent, DoneEvent
from app.domain.services.flows.plan_act import PlanActFlow
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.external.llm import LLM
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.task import TaskRunner, Task

logger = logging.getLogger(__name__)

class AgentTaskRunner(TaskRunner):
    """Agent task that can be cancelled"""
    def __init__(
        self,
        agent_id: str,
        llm: LLM,
        sandbox: Sandbox,
        browser: Browser,
        agent_repository: AgentRepository,
        search_engine: Optional[SearchEngine] = None
    ):
        self._agent_id = agent_id
        self._llm = llm
        self._sandbox = sandbox
        self._browser = browser
        self._search_engine = search_engine
        self._repository = agent_repository
        self._flow = PlanActFlow(
            self._agent_id,
            self._repository,
            self._llm,
            self._sandbox,
            self._browser,
            self._search_engine
        )

    async def run(self, task: Task) -> None:
        """Process agent's message queue and run the agent's flow"""
        try:
            logger.info(f"Agent {self._agent_id} message processing task started")
            message_id, message = await task.input_stream.pop()
            if message is None:
                logger.warning(f"Agent {self._agent_id} received empty message")
                return
                
            logger.info(f"Agent {self._agent_id} received new message: {message[:50]}...")
            
            async for event in self._run_flow(message):
                await task.output_stream.put(event.model_dump_json())
                
        except asyncio.CancelledError:
            logger.info(f"Agent {self._agent_id} task cancelled")
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} task encountered exception: {str(e)}")
            await task.output_stream.put(ErrorEvent(error=f"Task error: {str(e)}"))
    
    async def _run_flow(self, message: str) -> AsyncGenerator[AgentEvent, None]:
        """Process a single message through the agent's flow and yield events"""
        if not message:
            logger.warning(f"Agent {self._agent_id} received empty message")
            yield ErrorEvent(error="No message")
            return

        async for event in self._flow.run(message):
            yield event

        logger.info(f"Agent {self._agent_id} completed processing one message")

    
    async def on_done(self, task: Task) -> None:
        """Called when the task is done"""
        logger.info(f"Agent {self._agent_id} task done")


    async def destroy(self) -> None:
        """Destroy the task and release resources"""
        logger.info(f"Starting to destroy agent task")
        
        # Destroy sandbox environment
        if self._sandbox:
            logger.debug(f"Destroying Agent {self._agent_id}'s sandbox environment")
            await self._sandbox.destroy()
        
        logger.debug(f"Agent {self._agent_id} has been fully closed and resources cleared")
