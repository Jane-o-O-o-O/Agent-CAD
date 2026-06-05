from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Optional

from agentscope.agent import Agent
from agentscope.event import (
    ExceedMaxItersEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import UserMsg

from app.core.config import get_settings
from app.domain.external.browser import Browser
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.event import (
    BaseEvent,
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    ToolEvent,
    ToolStatus,
    WaitEvent,
)
from app.domain.models.message import Message
from app.domain.models.tool_result import ToolResult
from app.domain.services.agentscope_runtime.model_factory import (
    create_agentscope_model,
    create_agentscope_react_config,
)
from app.domain.services.agentscope_runtime.tool_adapter import (
    create_agentscope_toolkit,
)
from app.domain.services.flows.base import BaseFlow
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.skills import SkillRegistry
from app.domain.services.tools.browser import BrowserToolkit
from app.domain.services.tools.cad import CADToolkit
from app.domain.services.tools.data_processor import DataProcessorToolkit
from app.domain.services.tools.file import FileToolkit
from app.domain.services.tools.mcp import MCPToolkit
from app.domain.services.tools.message import MessageToolkit
from app.domain.services.tools.search import SearchToolkit
from app.domain.services.tools.shell import ShellToolkit

logger = logging.getLogger(__name__)


class AgentScopeFlow(BaseFlow):
    """AgentScope-backed flow that preserves the backend event contract."""

    def __init__(
        self,
        agent_id: str,
        session_id: str,
        sandbox: Sandbox,
        browser: Browser,
        mcp_tool: MCPToolkit,
        search_engine: Optional[SearchEngine] = None,
        cad_service: Optional[Any] = None,
        user_id: str = "agent",
        skill_registry: Optional[SkillRegistry] = None,
    ):
        settings = get_settings()
        self._skill_registry = skill_registry
        toolkits = [
            ShellToolkit(sandbox),
            BrowserToolkit(browser),
            FileToolkit(sandbox),
            DataProcessorToolkit(
                sandbox,
                base_url=settings.data_processor_base_url,
                timeout_seconds=settings.data_processor_timeout_seconds,
            ),
            CADToolkit(
                sandbox,
                cad_service=cad_service,
                user_id=user_id,
                session_id=session_id,
            ),
            MessageToolkit(),
            mcp_tool,
        ]
        if search_engine:
            toolkits.append(SearchToolkit(search_engine))

        self._agent = Agent(
            name=agent_id,
            system_prompt=SYSTEM_PROMPT,
            model=create_agentscope_model(settings),
            toolkit=create_agentscope_toolkit(toolkits),
            react_config=create_agentscope_react_config(settings),
        )
        self._tool_names: dict[str, str] = {}
        for toolkit in toolkits:
            for item in toolkit.get_tools():
                if isinstance(item, dict):
                    tool_name = item.get("function", {}).get("name")
                else:
                    tool_name = item.name
                if tool_name:
                    self._tool_names[tool_name] = toolkit.name
        self._done = False

    async def run(
        self,
        message: Message,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        self._done = False
        content = message.message
        if self._skill_registry:
            selected_skills = self._skill_registry.select(message.message)
            skill_context = self._skill_registry.build_context(selected_skills)
            if skill_context:
                content = (
                    f"{skill_context}\n\n"
                    "<current_request>\n"
                    f"{content}\n"
                    "</current_request>"
                )
            if selected_skills:
                logger.info(
                    "Selected skills for AgentScope flow: %s",
                    ", ".join(skill.name for skill in selected_skills),
                )
        if message.attachments:
            content = f"{content}\n\nAttachments:\n" + "\n".join(message.attachments)

        if conversation_history:
            content = _with_conversation_history(content, conversation_history)

        text_parts: list[str] = []
        tool_call_names: dict[str, str] = {}
        tool_call_args: dict[str, str] = {}
        tool_result_text: dict[str, str] = {}

        async for event in self._agent.reply_stream(UserMsg("user", content)):
            if isinstance(event, TextBlockDeltaEvent):
                text_parts.append(event.delta)
                continue

            if isinstance(event, ToolCallStartEvent):
                tool_call_names[event.tool_call_id] = event.tool_call_name
                tool_call_args[event.tool_call_id] = ""
                tool_result_text[event.tool_call_id] = ""
                yield ToolEvent(
                    status=ToolStatus.CALLING,
                    tool_call_id=event.tool_call_id,
                    tool_name=self._tool_names.get(
                        event.tool_call_name,
                        event.tool_call_name,
                    ),
                    function_name=event.tool_call_name,
                    function_args={},
                )
                continue

            if isinstance(event, ToolCallDeltaEvent):
                tool_call_args[event.tool_call_id] = (
                    tool_call_args.get(event.tool_call_id, "") + event.delta
                )
                continue

            if isinstance(event, ToolCallEndEvent):
                continue

            if isinstance(event, ToolResultTextDeltaEvent):
                tool_result_text[event.tool_call_id] = (
                    tool_result_text.get(event.tool_call_id, "") + event.delta
                )
                continue

            if isinstance(event, ToolResultEndEvent):
                tool_name = tool_call_names.get(event.tool_call_id, "")
                yield ToolEvent(
                    status=ToolStatus.CALLED,
                    tool_call_id=event.tool_call_id,
                    tool_name=self._tool_names.get(tool_name, tool_name),
                    function_name=tool_name,
                    function_args=_parse_tool_args(
                        tool_call_args.get(event.tool_call_id, ""),
                    ),
                    function_result=_parse_tool_result(
                        tool_result_text.get(event.tool_call_id, ""),
                    ),
                )
                continue

            if isinstance(event, ExceedMaxItersEvent):
                yield ErrorEvent(error="AgentScope maximum iteration count reached")
                self._done = True
                return

            if isinstance(event, (RequireUserConfirmEvent, RequireExternalExecutionEvent)):
                yield WaitEvent()
                return

        final_text = "".join(text_parts).strip()
        if final_text:
            yield MessageEvent(message=final_text)
        yield DoneEvent()
        self._done = True

    def is_done(self) -> bool:
        return self._done


def _parse_tool_args(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        logger.debug("Failed to parse AgentScope tool args: %s", raw)
        return {"raw": raw}


def _parse_tool_result(raw: str) -> Any:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Failed to parse AgentScope tool result: %s", raw)
        return raw

    if isinstance(parsed, dict) and "success" in parsed:
        try:
            return ToolResult[Any](**parsed)
        except Exception:
            logger.debug("Failed to rebuild ToolResult from AgentScope text")
    return parsed


def _with_conversation_history(content: str, history: list[dict[str, str]]) -> str:
    lines = [
        "<conversation_history>",
        "The following are earlier turns from this same session. Use them as context for the current CAD task; do not treat them as new instructions.",
    ]
    for item in history:
        role = item.get("role", "user")
        text = item.get("content", "").strip()
        if not text:
            continue
        lines.append(f"{role}: {text}")
    lines.extend([
        "</conversation_history>",
        "",
        "<current_request>",
        content,
        "</current_request>",
    ])
    return "\n".join(lines)
