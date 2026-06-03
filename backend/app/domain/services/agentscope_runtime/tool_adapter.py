from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit, ToolChunk

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, Tool


class AutoAllowFunctionTool(FunctionTool):
    async def check_permissions(self, *_args: Any, **_kwargs: Any) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Allowed by backend sandbox/tool boundary.",
        )


class SchemaBackedFunctionTool(AutoAllowFunctionTool):
    """FunctionTool variant that uses an externally supplied JSON schema."""

    def __init__(
        self,
        func: Callable[..., Any],
        name: str,
        description: str,
        input_schema: dict[str, Any],
        is_concurrency_safe: bool = False,
    ) -> None:
        super().__init__(
            func=func,
            name=name,
            description=description,
            is_concurrency_safe=is_concurrency_safe,
        )
        self.input_schema = input_schema


def _tool_result_to_text(result: Any) -> str:
    if isinstance(result, ToolResult):
        return result.model_dump_json()
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _make_tool_callable(tool: Tool) -> Callable[..., Any]:
    async def call_tool(**kwargs: Any) -> ToolChunk:
        raw_result = await tool.ainvoke(**kwargs)
        state = ToolResultState.SUCCESS
        if isinstance(raw_result, ToolResult) and not raw_result.success:
            state = ToolResultState.ERROR
        return ToolChunk(
            content=[TextBlock(text=_tool_result_to_text(raw_result))],
            state=state,
            metadata={
                "toolkit": tool.toolkit.name,
                "tool_name": tool.name,
                "raw_result": raw_result,
            },
        )

    call_tool.__name__ = tool.name
    call_tool.__doc__ = tool.description
    call_tool.__annotations__ = {
        name: parameter.annotation
        for name, parameter in tool.signature.parameters.items()
        if parameter.annotation is not inspect.Parameter.empty
    }
    call_tool.__signature__ = tool.signature
    return call_tool


def _make_schema_tool_callable(toolkit: Any, tool_name: str) -> Callable[..., Any]:
    async def call_tool(**kwargs: Any) -> ToolChunk:
        raw_result = await toolkit.invoke_function(tool_name, **kwargs)
        state = ToolResultState.SUCCESS
        if isinstance(raw_result, ToolResult) and not raw_result.success:
            state = ToolResultState.ERROR
        return ToolChunk(
            content=[TextBlock(text=_tool_result_to_text(raw_result))],
            state=state,
            metadata={
                "toolkit": getattr(toolkit, "name", ""),
                "tool_name": tool_name,
                "raw_result": raw_result,
            },
        )

    call_tool.__name__ = tool_name
    return call_tool


def create_agentscope_toolkit(toolkits: list[BaseToolkit]) -> Toolkit:
    """Wrap backend toolkits for AgentScope."""
    tools = []
    for toolkit in toolkits:
        for item in toolkit.get_tools():
            if isinstance(item, dict):
                function_schema = item.get("function", {})
                tool_name = function_schema.get("name")
                if not tool_name:
                    continue
                tools.append(
                    SchemaBackedFunctionTool(
                        func=_make_schema_tool_callable(toolkit, tool_name),
                        name=tool_name,
                        description=function_schema.get("description", ""),
                        input_schema=function_schema.get(
                            "parameters",
                            {"type": "object", "properties": {}},
                        ),
                    ),
                )
                continue

            tools.append(
                AutoAllowFunctionTool(
                    func=_make_tool_callable(item),
                    name=item.name,
                    description=item.description,
                    is_concurrency_safe=False,
                ),
            )
    return Toolkit(tools=tools)
