from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional


ToolCallable = Callable[..., Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    func: ToolCallable
    toolkit: "BaseToolkit"
    signature: inspect.Signature

    async def ainvoke(self, **kwargs: Any) -> Any:
        return await self.func(**kwargs)


def tool(parse_docstring: bool = True) -> Callable[[ToolCallable], ToolCallable]:
    """Mark an async method as an agent tool.

    The argument is kept for compatibility with previous decorators; docstrings
    are parsed by AgentScope's FunctionTool wrapper.
    """

    def decorator(func: ToolCallable) -> ToolCallable:
        setattr(func, "_is_agent_tool", True)
        return func

    return decorator


class BaseToolkit:
    """Base class for backend toolkits used by AgentScope."""

    name: str = ""

    def __init__(self) -> None:
        self.tools: list[Tool] = []
        for _, method in inspect.getmembers(self, inspect.ismethod):
            if not _is_agent_tool(method):
                continue
            self.tools.append(
                Tool(
                    name=method.__name__,
                    description=inspect.getdoc(method) or "",
                    func=method,
                    toolkit=self,
                    signature=_signature_without_self(method),
                ),
            )

    def get_tools(self) -> list[Tool]:
        return self.tools

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        for item in self.tools:
            if item.name == tool_name:
                return item
        return None


def _signature_without_self(func: ToolCallable) -> inspect.Signature:
    signature = inspect.signature(func)
    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.name not in {"self", "cls"}
    ]
    return signature.replace(parameters=parameters)


def _is_agent_tool(func: ToolCallable) -> bool:
    return bool(
        getattr(func, "_is_agent_tool", False)
        or getattr(getattr(func, "__func__", None), "_is_agent_tool", False)
    )
