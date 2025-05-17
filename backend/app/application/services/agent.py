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
from app.domain.services.agent import agent_domain_service
from app.domain.models.event import (
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
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser
from app.infrastructure.external.search.google_search import GoogleSearchEngine
from app.infrastructure.config import get_settings

# Set up logger
logger = logging.getLogger(__name__)

class AgentService:
    def __init__(self):
        logger.info("Initializing AgentService")
        self.agent_domain_service = agent_domain_service  # Single domain service instance
        self.settings = get_settings()
        self.llm = OpenAILLM()
        self.search_engine: Optional[GoogleSearchEngine] = None
        
        # Initialize search engine only if both API key and engine ID are set
        if self.settings.google_search_api_key and self.settings.google_search_engine_id:
            logger.info("Initializing Google Search Engine")
            self.search_engine = GoogleSearchEngine(
                api_key=self.settings.google_search_api_key, 
                cx=self.settings.google_search_engine_id
            )
        else:
            logger.warning("Google Search Engine not initialized: missing API key or engine ID")

    async def create_agent(self) -> Agent:
        logger.info("Creating new agent")
        # Create a new Docker container as sandbox
        sandbox = await DockerSandbox.create()
        cdp_url = sandbox.get_cdp_url()
        logger.info(f"Created sandbox with CDP URL: {cdp_url}")
        
        self.browser = PlaywrightBrowser(self.llm, cdp_url)
        logger.info("Initialized Playwright browser")
        
        # Create and initialize Agent and its resources
        agent = self.agent_domain_service.create_agent(
            model_name=self.settings.model_name,
            llm=self.llm, 
            sandbox=sandbox, 
            browser=self.browser, 
            search_engine=self.search_engine,
            temperature=self.settings.temperature,  # Get temperature parameter from configuration
            max_tokens=self.settings.max_tokens     # Get max tokens from configuration
        )
        
        logger.info(f"Agent created successfully with ID: {agent.id}")
        return agent

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        logger.info(f"Retrieving agent with ID: {agent_id}")
        # Use domain service method to get the Agent
        agent = self.agent_domain_service.get_agent(agent_id)
        if agent:
            logger.info(f"Agent found: {agent_id}")
        else:
            logger.warning(f"Agent not found: {agent_id}")
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
        async for event in self.agent_domain_service.chat(agent_id, message, timestamp):
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
            result = await self.agent_domain_service.close_agent(agent_id)
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
        await self.agent_domain_service.shutdown()
        logger.info("All agents closed successfully")

    async def agent_exists(self, agent_id: str) -> bool:
        """Check if an Agent exists
        
        Args:
            agent_id: Agent ID
            
        Returns:
            bool: Returns True if the Agent exists, False otherwise
        """
        agent = self.agent_domain_service.get_agent(agent_id)
        return agent is not None

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
        
        if not self.agent_exists(agent_id):
            logger.warning(f"Agent not found: {agent_id}")
            raise NotFoundError(f"Agent not found: {agent_id}")
        
        sandbox = self.agent_domain_service.get_sandbox(agent_id)
        if not sandbox:
            logger.warning(f"Sandbox not found: {agent_id}")
            raise NotFoundError(f"Sandbox not found: {agent_id}")
            
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
        
        if not await self.agent_exists(agent_id):
            logger.warning(f"Agent not found: {agent_id}")
            raise NotFoundError(f"Agent not found: {agent_id}")
        
        sandbox = self.agent_domain_service.get_sandbox(agent_id)
        if not sandbox:
            logger.warning(f"Sandbox not found: {agent_id}")
            raise NotFoundError(f"Sandbox not found: {agent_id}")
        
        return sandbox.get_vnc_url()

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
        
        if not self.agent_exists(agent_id):
            logger.warning(f"Agent not found: {agent_id}")
            raise NotFoundError(f"Agent not found: {agent_id}")
        
        sandbox = self.agent_domain_service.get_sandbox(agent_id)
        if not sandbox:
            logger.warning(f"Sandbox not found: {agent_id}")
            raise NotFoundError(f"Sandbox not found: {agent_id}")
            
        result = await sandbox.file_read(path)
        logger.info(f"File read successfully: {path}")
        return FileViewResponse(**result.data)

agent_service = AgentService()
