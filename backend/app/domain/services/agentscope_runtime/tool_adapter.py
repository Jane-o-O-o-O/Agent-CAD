from __future__ import annotations

import json
import inspect
from typing import Any, Callable

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit, ToolChunk
from langchain_core.tools.base import BaseTool

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit


class AutoAllowFunctionTool(FunctionTool):
    async def check_permissions(self, *_args: Any, **_kwargs: Any) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Allowed by backend sandbox/tool boundary.",
        )


def _tool_result_to_text(result: Any) -> str:
    if isinstance(result, ToolResult):
        return result.model_dump_json()
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _make_tool_callable(tool: BaseTool) -> Callable[..., Any]:
    async def call_tool(**kwargs: Any) -> ToolChunk:
        raw_result = await tool._arun(**kwargs)
        state = ToolResultState.SUCCESS
        if isinstance(raw_result, ToolResult) and not raw_result.success:
            state = ToolResultState.ERROR
        return ToolChunk(
            content=[TextBlock(text=_tool_result_to_text(raw_result))],
            state=state,
            metadata={
                "toolkit": getattr(tool.toolkit, "name", ""),
                "tool_name": tool.name,
                "raw_result": raw_result,
            },
        )

    call_tool.__name__ = tool.name
    call_tool.__doc__ = tool.description
    annotations = {}
    parameters = []
    for name, field in (tool.args_schema.model_fields or {}).items():
        annotations[name] = field.annotation
        default = inspect.Parameter.empty if field.is_required() else field.default
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field.annotation,
            ),
        )
    call_tool.__annotations__ = annotations
    call_tool.__signature__ = inspect.Signature(parameters=parameters)
    return call_tool


def create_agentscope_toolkit(toolkits: list[BaseToolkit]) -> Toolkit:
    """Wrap existing backend toolkits for AgentScope without changing them."""
    tools = []
    for toolkit in toolkits:
        for tool in toolkit.get_tools():
            tools.append(
                AutoAllowFunctionTool(
                    func=_make_tool_callable(tool),
                    name=tool.name,
                    description=tool.description,
                    is_concurrency_safe=False,
                ),
            )
    return Toolkit(tools=tools)
