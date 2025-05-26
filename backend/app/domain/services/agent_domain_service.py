from typing import Optional, AsyncGenerator
import logging
from app.domain.models.session import Session
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.events.agent_events import BaseEvent, ErrorEvent, DoneEvent, PlanEvent, StepEvent, ToolEvent, MessageEvent, AgentEventFactory
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.repositories.session_repository import SessionRepository
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.external.task import Task
from typing import Type

# Setup logging
logger = logging.getLogger(__name__)

class AgentDomainService:
    """
    Agent domain service, responsible for coordinating the work of planning agent and execution agent
    """
    
    def __init__(
        self,
        agent_repository: AgentRepository,
        session_repository: SessionRepository,
        llm: LLM,
        sandbox_cls: Type[Sandbox],
        task_cls: Type[Task],
        search_engine: Optional[SearchEngine] = None
    ):
        self._repository = agent_repository
        self._session_repository =session_repository
        self._llm = llm
        self._sandbox_cls = sandbox_cls
        self._search_engine = search_engine
        self._task_cls = task_cls
        logger.info("AgentDomainService initialization completed")
            
    async def shutdown(self) -> None:
        """Clean up all Agent's resources"""
        logger.info(f"Starting to close all Agents")
        await self._task_cls.destroy()
        logger.info("All agents closed successfully")

    async def _create_task(self, session: Session) -> Task:
        """Create a new agent task"""
        sandbox = None
        sandbox_id = session.sandbox_id
        if sandbox_id:
            sandbox = await self._sandbox_cls.get(sandbox_id)
        if not sandbox:
            sandbox = await self._sandbox_cls.create()
            session.sandbox_id = sandbox.id
            await self._session_repository.save(session)
        browser = await sandbox.get_browser(self._llm)
        if not browser:
            logger.error(f"Failed to get browser for Sandbox {sandbox_id}")
            raise RuntimeError(f"Failed to get browser for Sandbox {sandbox_id}")
        
        await self._session_repository.save(session)

        task_runner = AgentTaskRunner(
            session_id=session.id,
            agent_id=session.agent_id,
            llm=self._llm,
            sandbox=sandbox,
            browser=browser,
            search_engine=self._search_engine,
            session_repository=self._session_repository,
            agent_repository=self._repository
        )

        task = self._task_cls.create(task_runner)
        session.task_id = task.id
        await self._session_repository.save(session)

        return task
        
    async def _get_task(self, session: Session) -> Optional[Task]:
        """Get a task for the given session"""

        task_id = session.task_id
        if not task_id:
            return None
        
        return self._task_cls.get(task_id)

    async def chat(
        self,
        session_id: str,
        message: Optional[str] = None,
        timestamp: Optional[int] = None,
        last_event_id: Optional[str] = None
    ) -> AsyncGenerator[BaseEvent, None]:
        """
        Chat with an agent
        """

        try:
            session = await self._session_repository.find_by_id(session_id)
            if not session:
                logger.error(f"Attempted to chat with non-existent Session {session_id}")
                raise RuntimeError("Session not found")
            
            session.last_message_at = timestamp
            session.last_message = message
            await self._session_repository.save(session)

            task = await self._get_task(session)

            if message:
                if task:
                    await task.cancel()
                task = await self._create_task(session)
                if not task:
                    raise RuntimeError("Failed to create task")
                
                message_id = await task.input_stream.put(message)
                message_event = MessageEvent(message=message, role="user", id=message_id)
                await self._session_repository.add_event(session_id, message_event)
                await task.run()
                logger.debug(f"Put message into Session {session_id}'s event queue: {message[:50]}...")
            
            logger.info(f"Session {session_id} started")
            logger.debug(f"Session {session_id} task: {task}")
           
            while task and not task.done:
                last_event_id, event_str = await task.output_stream.get(start_id=last_event_id, block_ms=0)
                if event_str is None:
                    logger.debug(f"No event found in Session {session_id}'s event queue")
                    continue
                event = AgentEventFactory.from_json(event_str)
                logger.debug(f"Got event from Session {session_id}'s event queue: {type(event).__name__}")
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    break
            
            logger.info(f"Session {session_id} completed")

        except Exception as e:
            logger.exception(f"Error in Session {session_id}")  
            yield ErrorEvent(error=str(e))
