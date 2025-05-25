from typing import AsyncGenerator, Dict, Any, Optional, Generator
import logging
from app.domain.models.session import Session
from app.domain.repositories.session_repository import SessionRepository

from app.application.schemas.event import (
    SSEEvent, DoneSSEEvent,
    MessageData, MessageSSEEvent,
    ToolData, ToolSSEEvent,
    StepSSEEvent, ErrorSSEEvent,
    TitleData, TitleSSEEvent,
    BaseData,
    StepData, ErrorData,
    PlanData, PlanSSEEvent
)
from app.application.schemas.response import ShellViewResponse, FileViewResponse
from app.domain.models.agent import Agent
from app.domain.services.agent_domain_service import AgentDomainService
from app.domain.events.agent_events import (
    BaseEvent,
    PlanEvent,
    PlanStatus,
    ToolEvent,
    ToolStatus,
    StepEvent,
    StepStatus,
    ErrorEvent,
    MessageEvent,
    DoneEvent,
    TitleEvent,
)
from app.application.schemas.exceptions import NotFoundError
from typing import Type
from app.domain.models.agent import Agent
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.external.llm import LLM
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.task import Task

# Set up logger
logger = logging.getLogger(__name__)

class AgentService:
    def __init__(
        self,
        llm: LLM,
        agent_repository: AgentRepository,
        session_repository: SessionRepository,
        sandbox_cls: Type[Sandbox],
        task_cls: Type[Task],
        search_engine: Optional[SearchEngine] = None
    ):
        logger.info("Initializing AgentService")
        self._agent_repository = agent_repository
        self._session_repository = session_repository
        self._agent_domain_service = AgentDomainService(
            self._agent_repository,
            self._session_repository,
            llm,
            sandbox_cls,
            task_cls,
            search_engine
        )
        self._llm = llm
        self._search_engine = search_engine
        self._sandbox_cls = sandbox_cls
    
    async def create_session(self) -> Session:
        logger.info("Creating new session")
        agent = await self._create_agent()
        session = Session(agent_id=agent.id)
        logger.info(f"Created new Session with ID: {session.id}")
        await self._session_repository.save(session)
        return session

    async def get_session(self, session_id: str) -> Session:
        logger.info(f"Getting session {session_id}")
        session = await self._session_repository.find_by_id(session_id)
        if not session:
            logger.warning(f"Session not found: {session_id}")
            raise NotFoundError(f"Session not found: {session_id}")
        return session

    async def _create_agent(self) -> Agent:
        logger.info("Creating new agent")
        
        # Create Agent instance
        agent = Agent(
            model_name=self._llm.model_name,
            temperature=self._llm.temperature,
            max_tokens=self._llm.max_tokens,
        )
        logger.info(f"Created new Agent with ID: {agent.id}")
        
        # Save agent to repository
        await self._agent_repository.save(agent)
        logger.info(f"Saved agent {agent.id} to repository")
        
        logger.info(f"Agent created successfully with ID: {agent.id}")
        return agent

    def _to_sse_event(self, event: BaseEvent) -> Generator[SSEEvent, None, None]:
        if isinstance(event, PlanEvent):
            yield PlanSSEEvent(data=PlanData(steps=[StepData(
                status=step.status,
                id=step.id, 
                description=step.description
            ) for step in event.plan.steps]))
        elif isinstance(event, MessageEvent):
            yield MessageSSEEvent(data=MessageData(content=event.message))
        elif isinstance(event, TitleEvent):
            yield TitleSSEEvent(data=TitleData(title=event.title))
        elif isinstance(event, ToolEvent):
            if event.status == ToolStatus.CALLING and event.tool_name in ["browser", "file", "shell", "message"]:
                yield ToolSSEEvent(data=ToolData(
                    name=event.tool_name,
                    status=event.status,
                    function=event.function_name,
                    args=event.function_args
                ))
            elif event.status == ToolStatus.CALLED and event.tool_name in ["search"]:
                yield ToolSSEEvent(data=ToolData(
                    name=event.tool_name,
                    function=event.function_name,
                    args=event.function_args,
                    status=event.status,
                    result=event.function_result
                ))
        elif isinstance(event, StepEvent):
            yield StepSSEEvent(data=StepData(
                status=event.step.status,
                id=event.step.id,
                description=event.step.description
            ))
            if event.step.error:
                yield ErrorSSEEvent(data=ErrorData(error=event.step.error))
            if event.step.result:
                yield MessageSSEEvent(data=MessageData(content=event.step.result))
        elif isinstance(event, DoneEvent):
            yield DoneSSEEvent(data=BaseData())
        elif isinstance(event, ErrorEvent):
            yield ErrorSSEEvent(data=ErrorData(error=event.error))

    async def chat(
        self,
        session_id: str,
        message: str,
        timestamp: int
    ) -> AsyncGenerator[SSEEvent, None]:
        logger.info(f"Starting chat with session {session_id}: {message[:50]}...")
        # Directly use the domain service's chat method, which will check if the session exists
        async for event in self._agent_domain_service.chat(session_id, message, timestamp):
            logger.debug(f"Received event: {event}")
            for sse_event in self._to_sse_event(event):
                yield sse_event
        logger.info(f"Chat with session {session_id} completed")

    async def shutdown(self):
        logger.info("Closing all agents and cleaning up resources")
        # Clean up all Agents and their associated sandboxes
        await self._agent_domain_service.shutdown()
        logger.info("All agents closed successfully")

    async def _get_sandbox(self, session_id: str) -> Sandbox:
        """Get sandbox instance for the specified agent
        
        Args:
            session_id: Session ID
            
        Returns:
            Sandbox: Sandbox instance
            
        Raises:
            NotFoundError: When Agent or Sandbox does not exist
        """
        session = await self._session_repository.find_by_id(session_id)
        if not session:
            logger.warning(f"Session not found: {session_id}")
            raise NotFoundError(f"Session not found: {session_id}")
        
        if not session.sandbox_id:
            logger.warning(f"Sandbox ID not found for session: {session_id}")
            raise NotFoundError(f"Sandbox not found: {session_id}")
            
        sandbox = await self._sandbox_cls.get(session.sandbox_id)
        if not sandbox:
            logger.warning(f"Sandbox not found: {session_id}")
            raise NotFoundError(f"Sandbox not found: {session_id}")
            
        return sandbox

    async def shell_view(self, session_id: str, shell_session_id: str) -> ShellViewResponse:
        """View shell session output
        
        Args:
            session_id: Session ID
            shell_session_id: Shell session ID
            
        Returns:
            APIResponse: Response entity containing shell output
            
        Raises:
            ResourceNotFoundError: When Agent or Sandbox does not exist
            OperationError: When a server error occurs during execution
        """
        logger.info(f"Viewing shell output for session {session_id}")
        
        sandbox = await self._get_sandbox(session_id)
        result = await sandbox.view_shell(shell_session_id)
        return ShellViewResponse(**result.data)

    async def get_vnc_url(self, session_id: str) -> str:
        """Get the VNC URL for the Agent sandbox
        
        Args:
            session_id: Session ID
            
        Returns:
            str: Sandbox host address
            
        Raises:
            NotFoundError: When Agent or Sandbox does not exist
        """
        logger.info(f"Getting sandbox host for session {session_id}")
        
        sandbox = await self._get_sandbox(session_id)
        return sandbox.vnc_url

    async def file_view(self, session_id: str, path: str) -> FileViewResponse:
        """View file content
        
        Args:
            session_id: Session ID
            path: File path
            
        Returns:
            APIResponse: Response entity containing file content
            
        Raises:
            ResourceNotFoundError: When Agent or Sandbox does not exist
            OperationError: When a server error occurs during execution
        """
        logger.info(f"Viewing file content for session {session_id}, file path: {path}")
        
        sandbox = await self._get_sandbox(session_id)
        result = await sandbox.file_read(path)
        logger.info(f"File read successfully: {path}")
        return FileViewResponse(**result.data)
