from typing import Any, Optional, AsyncGenerator, List
import asyncio
import logging
import os
import debugpy
from pydantic import TypeAdapter
from app.domain.models.message import Message
from app.domain.models.event import (
    BaseEvent,
    ErrorEvent,
    MessageEvent,
    DoneEvent,
    ToolEvent,
    WaitEvent,
    FileToolContent,
    ShellToolContent,
    SearchToolContent,
    BrowserToolContent,
    ToolStatus,
    AgentEvent,
    McpToolContent,
)
from app.domain.services.flows.agentscope_flow import AgentScopeFlow
from app.domain.external.sandbox import Sandbox
from app.domain.external.browser import Browser
from app.domain.external.search import SearchEngine
from app.domain.external.file import FileStorage
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.task import TaskRunner, Task
from app.domain.repositories.session_repository import SessionRepository
from app.domain.repositories.mcp_repository import MCPRepository
from app.domain.models.session import SessionStatus
from app.domain.models.file import FileInfo
from app.domain.services.tools.mcp import MCPToolkit
from app.domain.models.tool_result import ToolResult
from app.domain.models.search import SearchResults
from app.domain.services.skills import SkillRegistry
from app.core.config import get_settings

logger = logging.getLogger(__name__)
MAX_CONVERSATION_HISTORY_MESSAGES = 12
MAX_CONVERSATION_HISTORY_CHARS = 12000

class AgentTaskRunner(TaskRunner):
    """Agent task that can be cancelled"""
    def __init__(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        sandbox: Sandbox,
        browser: Browser,
        agent_repository: AgentRepository,
        session_repository: SessionRepository,
        file_storage: FileStorage,
        mcp_repository: MCPRepository,
        search_engine: Optional[SearchEngine] = None,
        cad_service: Optional[Any] = None,
    ):
        self._session_id = session_id
        self._agent_id = agent_id
        self._user_id = user_id
        self._sandbox = sandbox
        self._browser = browser
        self._search_engine = search_engine
        self._session_repository = session_repository
        self._file_storage = file_storage
        self._mcp_repository = mcp_repository
        self._cad_service = cad_service
        self._mcp_tool = MCPToolkit()
        self._pending_output_files: List[FileInfo] = []
        settings = get_settings()
        skill_registry = None
        if settings.skills_enabled:
            skill_registry = SkillRegistry(
                roots=[path.strip() for path in settings.skills_paths.split(":")],
                include_system=settings.skills_include_system,
                max_body_chars=settings.skills_max_body_chars,
            )
        self._flow = AgentScopeFlow(
            agent_id=self._agent_id,
            session_id=self._session_id,
            sandbox=self._sandbox,
            browser=self._browser,
            mcp_tool=self._mcp_tool,
            search_engine=self._search_engine,
            cad_service=self._cad_service,
            user_id=self._user_id,
            skill_registry=skill_registry,
        )

    async def _put_and_add_event(self, task: Task, event: AgentEvent) -> None:
        event_id = await task.output_stream.put(event.model_dump_json())
        event.id = event_id
        await self._session_repository.add_event(self._session_id, event)
    
    async def _pop_event(self, task: Task) -> AgentEvent:
        event_id, event_str = await task.input_stream.pop()
        if event_str is None:
            logger.warning(f"Agent {self._agent_id} received empty message")
            return
        event = TypeAdapter(AgentEvent).validate_json(event_str)
        event.id = event_id
        return event
    
    async def _get_browser_screenshot(self) -> str:
        screenshot = await self._browser.screenshot()
        result = await self._file_storage.upload_file(screenshot, "screenshot.png", self._user_id)
        return result.file_id

    async def _sync_file_to_storage(self, file_path: str) -> Optional[FileInfo]:
        """Upload or update file and return FileInfo"""
        try:
            file_info = await self._session_repository.get_file_by_path(self._session_id, file_path)
            file_data = await self._sandbox.file_download(file_path)
            if file_info:
                await self._session_repository.remove_file(self._session_id, file_info.file_id)
            file_name = file_path.split("/")[-1]
            file_info = await self._file_storage.upload_file(file_data, file_name, self._user_id)
            file_info.file_path = file_path
            await self._session_repository.add_file(self._session_id, file_info)
            return file_info
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} failed to sync file: {e}")

    async def _sync_cad_output_file(self, file_path: Optional[str]) -> Optional[FileInfo]:
        """Sync generated CAD output into session files and pending message attachments."""
        if not file_path:
            return None
        file_info = await self._sync_file_to_storage(file_path)
        if not file_info:
            return None
        if not any(existing.file_id == file_info.file_id for existing in self._pending_output_files):
            self._pending_output_files.append(file_info)
        return file_info

    async def _sync_cad_result_files(self, event: ToolEvent) -> None:
        """Attach generated CAD files to the session when CAD tools return sandbox paths."""
        if event.status != ToolStatus.CALLED or event.tool_name != "cad" or not event.function_result:
            return

        raw_result = (
            event.function_result.model_dump()
            if hasattr(event.function_result, "model_dump")
            else event.function_result
        )
        if not isinstance(raw_result, dict) or not raw_result.get("success", False):
            return

        data = raw_result.get("data")
        if not isinstance(data, dict):
            return

        candidate_paths = [
            data.get("output_path"),
            data.get("file"),
        ]
        validation = data.get("validation")
        if isinstance(validation, dict):
            candidate_paths.append(validation.get("file"))

        synced_files = []
        for file_path in candidate_paths:
            if isinstance(file_path, str) and file_path.lower().endswith(".dxf"):
                file_info = await self._sync_cad_output_file(file_path)
                if file_info:
                    synced_files.append(file_info.model_dump())

        if synced_files:
            raw_result["data"]["files"] = synced_files
            event.function_result = ToolResult(**raw_result)
    
    async def _sync_file_to_sandbox(self, file_id: str) -> Optional[FileInfo]:
        """Download file from storage to sandbox"""
        try:
            file_data, file_info = await self._file_storage.download_file(file_id, self._user_id)
            file_path = "/home/ubuntu/upload/" + file_info.filename
            result = await self._sandbox.file_upload(file_data, file_path)
            if result.success:
                file_info.file_path = file_path
                return file_info
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} failed to sync file: {e}")

    async def _sync_message_attachments_to_storage(self, event: MessageEvent) -> None:
        """Sync message attachments and update event attachments"""
        attachments: List[FileInfo] = []
        try:
            if event.attachments:
                for attachment in event.attachments:
                    if attachment.file_id:
                        attachments.append(attachment)
                        continue
                    file_info = await self._sync_file_to_storage(attachment.file_path)
                    if file_info:
                        attachments.append(file_info)
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} failed to sync attachments to storage: {e}")
    
    async def _sync_message_attachments_to_sandbox(self, event: MessageEvent) -> None:
        """Sync message attachments and update event attachments"""
        attachments: List[FileInfo] = []
        try:
            if event.attachments:
                for attachment in event.attachments:
                    file_info = await self._sync_file_to_sandbox(attachment.file_id)
                    if file_info:
                        attachments.append(file_info)
                        await self._session_repository.add_file(self._session_id, file_info)
            event.attachments = attachments
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} failed to sync attachments to event: {e}")

    async def _build_conversation_history(self, current_event_id: Optional[str]) -> List[dict[str, str]]:
        """Build a compact same-session text history for a fresh AgentScope runner."""
        try:
            session = await self._session_repository.find_by_id(self._session_id)
        except Exception as e:
            logger.warning("Failed to load session history for %s: %s", self._session_id, e)
            return []

        if not session or not session.events:
            return []

        history: List[dict[str, str]] = []
        total_chars = 0
        for event in reversed(session.events):
            if event.id == current_event_id:
                continue
            if not isinstance(event, MessageEvent):
                continue
            if event.role not in ("user", "assistant"):
                continue
            content = (event.message or "").strip()
            if not content:
                continue
            if event.attachments:
                attachment_names = [
                    attachment.filename or attachment.file_path or attachment.file_id
                    for attachment in event.attachments
                    if attachment.filename or attachment.file_path or attachment.file_id
                ]
                if attachment_names:
                    content = f"{content}\nAttachments: {', '.join(attachment_names)}"
            next_total = total_chars + len(content)
            if history and next_total > MAX_CONVERSATION_HISTORY_CHARS:
                break
            history.append({"role": event.role, "content": content})
            total_chars = next_total
            if len(history) >= MAX_CONVERSATION_HISTORY_MESSAGES:
                break

        history.reverse()
        return history
    

    # TODO: refactor this function
    async def _handle_tool_event(self, event: ToolEvent):
        """Generate tool content"""
        try:
            if event.status == ToolStatus.CALLED:
                if event.tool_name == "browser":
                    event.tool_content = BrowserToolContent(screenshot=await self._get_browser_screenshot())
                elif event.tool_name == "search":
                    search_results: ToolResult[SearchResults] = event.function_result
                    logger.debug(f"Search tool results: {search_results}")
                    search_data = search_results.data
                    if isinstance(search_data, dict):
                        search_data = SearchResults(**search_data)
                    event.tool_content = SearchToolContent(
                        results=search_data.results if search_data else []
                    )
                elif event.tool_name == "shell":
                    if "id" in event.function_args:
                        shell_result = await self._sandbox.view_shell(event.function_args["id"], console=True)
                        event.tool_content = ShellToolContent(console=shell_result.data.get("console", []))
                    else:
                        event.tool_content = ShellToolContent(console="(No Console)")
                elif event.tool_name == "file":
                    if "file" in event.function_args:
                        file_path = event.function_args["file"]
                        file_read_result = await self._sandbox.file_read(file_path)
                        file_content: str = file_read_result.data.get("content", "")
                        event.tool_content = FileToolContent(content=file_content)
                        await self._sync_file_to_storage(file_path)
                    else:
                        event.tool_content = FileToolContent(content="(No Content)")
                elif event.tool_name == "mcp":
                    logger.debug(f"Processing MCP tool event: function_result={event.function_result}")
                    if event.function_result:
                        if hasattr(event.function_result, 'data') and event.function_result.data:
                            logger.debug(f"MCP tool result data: {event.function_result.data}")
                            event.tool_content = McpToolContent(result=event.function_result.data)
                        elif hasattr(event.function_result, 'success') and event.function_result.success:
                            logger.debug(f"MCP tool result (success, no data): {event.function_result}")
                            result_data = event.function_result.model_dump() if hasattr(event.function_result, 'model_dump') else str(event.function_result)
                            event.tool_content = McpToolContent(result=result_data)
                        else:
                            logger.debug(f"MCP tool result (fallback): {event.function_result}")
                            event.tool_content = McpToolContent(result=str(event.function_result))
                    else:
                        logger.warning("MCP tool: No function_result found")
                        event.tool_content = McpToolContent(result="No result available")
                    
                    logger.debug(f"MCP tool_content set to: {event.tool_content}")
                    if event.tool_content:
                        logger.debug(f"MCP tool_content.result: {event.tool_content.result}")
                        logger.debug(f"MCP tool_content dict: {event.tool_content.model_dump()}")
                elif event.tool_name == "cad":
                    await self._sync_cad_result_files(event)
                    if event.function_result:
                        event.tool_content = McpToolContent(
                            result=event.function_result.model_dump()
                            if hasattr(event.function_result, "model_dump")
                            else str(event.function_result)
                        )
                    else:
                        event.tool_content = McpToolContent(result="No CAD result available")
                elif event.tool_name == "data_processor":
                    if event.function_result:
                        event.tool_content = McpToolContent(
                            result=event.function_result.model_dump()
                            if hasattr(event.function_result, "model_dump")
                            else str(event.function_result)
                        )
                    else:
                        event.tool_content = McpToolContent(result="No data processor result available")
                else:
                    logger.warning(f"Agent {self._agent_id} received unknown tool event: {event.tool_name}")
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} failed to generate tool content: {e}")

    async def run(self, task: Task) -> None:
        """Process agent's message queue and run the agent's flow"""
        try:
            logger.info(f"Agent {self._agent_id} message processing task started")
            await self._sandbox.ensure_sandbox()
            await self._mcp_tool.initialized(await self._mcp_repository.get_mcp_config())
            while not await task.input_stream.is_empty():
                event = await self._pop_event(task)
                self._pending_output_files = []
                message = ""
                if isinstance(event, MessageEvent):
                    message = event.message or ""
                    await self._sync_message_attachments_to_sandbox(event)
                    
                logger.info(f"Agent {self._agent_id} received new message: {message[:50]}...")

                message_obj = Message(message=message, attachments=[attachment.file_path for attachment in event.attachments])
                conversation_history = (
                    await self._build_conversation_history(event.id)
                    if isinstance(event, MessageEvent)
                    else []
                )
                
                async for event in self._run_flow(message_obj, conversation_history):
                    await self._put_and_add_event(task, event)
                    if isinstance(event, MessageEvent):
                        await self._session_repository.update_latest_message(self._session_id, event.message, event.timestamp)
                        await self._session_repository.increment_unread_message_count(self._session_id)
                    elif isinstance(event, WaitEvent):
                        await self._session_repository.update_status(self._session_id, SessionStatus.WAITING)
                        return
                    if not await task.input_stream.is_empty():
                        break

            await self._session_repository.update_status(self._session_id, SessionStatus.COMPLETED)
        except asyncio.CancelledError:
            logger.info(f"Agent {self._agent_id} task cancelled")
            await self._put_and_add_event(task, DoneEvent())
            await self._session_repository.update_status(self._session_id, SessionStatus.COMPLETED)
        except Exception as e:
            logger.exception(f"Agent {self._agent_id} task encountered exception: {str(e)}")
            
            # If debugger is attached, trigger breakpoint for debugging
            # You can also manually set ENABLE_DEBUG_BREAK=1 environment variable
            if debugpy.is_client_connected() or os.getenv('ENABLE_DEBUG_BREAK'):
                logger.debug("Debugger detected, triggering breakpoint")
                import traceback
                traceback.print_exc()
                debugpy.breakpoint()  # This will pause execution if a debugger is attached
            
            await self._put_and_add_event(task, ErrorEvent(error=f"Task error: {str(e)}"))
            await self._session_repository.update_status(self._session_id, SessionStatus.COMPLETED)
    
    async def _run_flow(
        self,
        message: Message,
        conversation_history: Optional[List[dict[str, str]]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Process a single message through the agent's flow and yield events"""
        if not message.message:
            logger.warning(f"Agent {self._agent_id} received empty message")
            yield ErrorEvent(error="No message")
            return

        async for event in self._flow.run(message, conversation_history=conversation_history):
            if isinstance(event, ToolEvent):
                # TODO: move to tool function
                await self._handle_tool_event(event)
            elif isinstance(event, MessageEvent):
                if self._pending_output_files and not event.attachments:
                    event.attachments = list(self._pending_output_files)
                    self._pending_output_files = []
                await self._sync_message_attachments_to_storage(event)
            yield event

        logger.info(f"Agent {self._agent_id} completed processing one message")

    
    async def on_done(self, task: Task) -> None:
        """Called when the task is done"""
        logger.info(f"Agent {self._agent_id} task done")


    async def destroy(self) -> None:
        """Destroy the task and release resources"""
        logger.info("Starting to destroy agent task")
        
        # Destroy sandbox environment
        if self._sandbox:
            logger.debug(f"Destroying Agent {self._agent_id}'s sandbox environment")
            await self._sandbox.destroy()
        
        if self._mcp_tool:
            logger.debug(f"Destroying Agent {self._agent_id}'s MCP tool")
            await self._mcp_tool.cleanup()
        
        logger.debug(f"Agent {self._agent_id} has been fully closed and resources cleared")
