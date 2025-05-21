from typing import AsyncGenerator, Dict, Any, Optional, Generator
import logging

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
    PlanCreatedEvent,
    ToolCallingEvent,
    ToolCalledEvent,
    StepStartedEvent,
    StepFailedEvent,
    StepCompletedEvent,
    PlanCompletedEvent,
    PlanUpdatedEvent,
    ErrorEvent,
    AgentEvent,
    DoneEvent
)
from app.application.schemas.exceptions import NotFoundError
from typing import Type
from app.domain.models.agent import Agent
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.external.llm import LLM
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.sandbox import SandboxFactory
from app.domain.external.browser import BrowserFactory


# Set up logger
logger = logging.getLogger(__name__)

class AgentService:
    def __init__(self,
                 llm: LLM,
                 agent_repository: AgentRepository,
                 sandbox_factory: SandboxFactory,
                 browser_factory: BrowserFactory, 
                 search_engine: Optional[SearchEngine] = None):
        logger.info("Initializing AgentService")
        self._agent_repository = agent_repository
        self._agent_domain_service = AgentDomainService(self._agent_repository)
        self._llm = llm
        self._browser_factory = browser_factory
        self._search_engine = search_engine
        self._sandbox_factory = sandbox_factory

    async def create_agent(self) -> Agent:
        logger.info("Creating new agent")
        # Create a new Docker container as sandbox
        sandbox = await self._sandbox_factory.create()
        logger.info(f"Created sandbox with CDP URL: {sandbox.cdp_url}")
        
        browser = await self._browser_factory.create(self._llm, sandbox.cdp_url)
        logger.info("Initialized Playwright browser")
        
        # Create Agent instance
        agent = Agent(
            model_name=self._llm.model_name,
            temperature=self._llm.temperature,
            max_tokens=self._llm.max_tokens,
            sandbox_id=sandbox.id
        )
        logger.info(f"Created new Agent with ID: {agent.id}")
        
        # Save agent to repository
        await self._agent_repository.save(agent)
        logger.info(f"Saved agent {agent.id} to repository")
        
        # Initialize agent runtime in domain service
        await self._agent_domain_service.create_agent_runtime(
            agent_id=agent.id,
            llm=self._llm, 
            sandbox=sandbox, 
            browser=browser, 
            search_engine=self._search_engine
        )
        
        logger.info(f"Agent created successfully with ID: {agent.id}")
        return agent

    def _to_sse_event(self, event: AgentEvent) -> Generator[SSEEvent, None, None]:
        if isinstance(event, (PlanCreatedEvent, PlanUpdatedEvent, PlanCompletedEvent)):
            if isinstance(event, PlanCreatedEvent):
                if event.plan.title:
                    yield TitleSSEEvent(data=TitleData(title=event.plan.title))
                yield MessageSSEEvent(data=MessageData(content=event.plan.message))
            if len(event.plan.steps) > 0:
                yield PlanSSEEvent(data=PlanData(steps=[StepData(
                    status=step.status,
                    id=step.id, 
                    description=step.description
                ) for step in event.plan.steps]))
        elif isinstance(event, ToolCallingEvent):
            if event.tool_name in ["browser", "file", "shell", "message"]:
                yield ToolSSEEvent(data=ToolData(
                    name=event.tool_name,
                    status="calling",
                    function=event.function_name,
                    args=event.function_args
                ))
        elif isinstance(event, ToolCalledEvent):
            if event.tool_name in ["search"]:
                yield ToolSSEEvent(data=ToolData(
                    name=event.tool_name,
                    function=event.function_name,
                    args=event.function_args,
                    status="called",
                    result=event.function_result
                ))
        elif isinstance(event, (StepStartedEvent, StepCompletedEvent, StepFailedEvent)):
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

    async def chat(self, agent_id: str, message: str, timestamp: int) -> AsyncGenerator[SSEEvent, None]:
        logger.info(f"Starting chat with agent {agent_id}: {message[:50]}...")
        # Directly use the domain service's chat method, which will check if the agent exists
        async for event in self._agent_domain_service.chat(agent_id, message, timestamp):
            logger.debug(f"Received event: {event}")
            for sse_event in self._to_sse_event(event):
                yield sse_event

    async def destroy_agent(self, agent_id: str) -> bool:
        """Destroy the specified Agent and its associated sandbox
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Whether the destruction was successful
        """
        logger.info(f"Attempting to destroy agent: {agent_id}")
        try:
            # Destroy Agent resources through the domain service
            result = await self._agent_domain_service.close_agent(agent_id)
            if result:
                logger.info(f"Agent destroyed successfully: {agent_id}")
            else:
                logger.warning(f"Failed to destroy agent: {agent_id}")
            return result
        except Exception as e:
            logger.error(f"Error destroying agent {agent_id}: {str(e)}")
            return False

    async def shutdown(self):
        logger.info("Closing all agents and cleaning up resources")
        # Clean up all Agents and their associated sandboxes
        await self._agent_domain_service.shutdown()
        logger.info("All agents closed successfully")

    async def _get_sandbox(self, agent_id: str) -> Sandbox:
        """Get sandbox instance for the specified agent
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Sandbox: Sandbox instance
            
        Raises:
            NotFoundError: When Agent or Sandbox does not exist
        """
        agent = await self._agent_repository.find_by_id(agent_id)
        if not agent:
            logger.warning(f"Agent not found: {agent_id}")
            raise NotFoundError(f"Agent not found: {agent_id}")
        
        if not agent.sandbox_id:
            logger.warning(f"Sandbox ID not found for agent: {agent_id}")
            raise NotFoundError(f"Sandbox not found: {agent_id}")
            
        sandbox = await self._sandbox_factory.get(agent.sandbox_id)
        if not sandbox:
            logger.warning(f"Sandbox not found: {agent_id}")
            raise NotFoundError(f"Sandbox not found: {agent_id}")
            
        return sandbox

    async def shell_view(self, agent_id: str, session_id: str) -> ShellViewResponse:
        """View shell session output
        
        Args:
            agent_id: Agent ID
            session_id: Shell session ID
            
        Returns:
            APIResponse: Response entity containing shell output
            
        Raises:
            ResourceNotFoundError: When Agent or Sandbox does not exist
            OperationError: When a server error occurs during execution
        """
        logger.info(f"Viewing shell output for agent {agent_id} in session {session_id}")
        
        sandbox = await self._get_sandbox(agent_id)
        result = await sandbox.view_shell(session_id)
        return ShellViewResponse(**result.data)

    async def get_vnc_url(self, agent_id: str) -> str:
        """Get the VNC URL for the Agent sandbox
        
        Args:
            agent_id: Agent ID
            
        Returns:
            str: Sandbox host address
            
        Raises:
            NotFoundError: When Agent or Sandbox does not exist
        """
        logger.info(f"Getting sandbox host for agent {agent_id}")
        
        sandbox = await self._get_sandbox(agent_id)
        return sandbox.vnc_url

    async def file_view(self, agent_id: str, path: str) -> FileViewResponse:
        """View file content
        
        Args:
            agent_id: Agent ID
            path: File path
            
        Returns:
            APIResponse: Response entity containing file content
            
        Raises:
            ResourceNotFoundError: When Agent or Sandbox does not exist
            OperationError: When a server error occurs during execution
        """
        logger.info(f"Viewing file content for agent {agent_id}, file path: {path}")
        
        sandbox = await self._get_sandbox(agent_id)
        result = await sandbox.file_read(path)
        logger.info(f"File read successfully: {path}")
        return FileViewResponse(**result.data)
