from pydantic import BaseModel, Field
from typing import Any, Union, Literal, Dict, Optional, List
import time
from app.domain.models.plan import ExecutionStatus
from app.domain.events.agent_events import ToolStatus
from app.domain.events.agent_events import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    PlanEvent,
    MessageEvent,
    TitleEvent,
    ToolEvent,
    StepEvent,
)


class BaseEventData(BaseModel):
    event_id: Optional[str] = None
    timestamp: int = Field(default_factory=lambda: int(time.time()))

class MessageEventData(BaseEventData):
    content: str

class ToolEventData(BaseEventData):
    name: str
    status: ToolStatus
    function: str
    args: Dict[str, Any]
    result: Optional[Any] = None

class StepEventData(BaseEventData):
    status: ExecutionStatus
    id: str
    description: str


class PlanEventData(BaseEventData):
    steps: List[StepEventData]

class ErrorEventData(BaseEventData):
    error: str

class TitleEventData(BaseEventData):
    title: str

class BaseSSEEvent(BaseModel):
    event: str
    data: Optional[Union[str, BaseEventData]]

class MessageSSEEvent(BaseSSEEvent):
    event: Literal["message"] = "message"
    data: MessageEventData

class ToolSSEEvent(BaseSSEEvent):
    event: Literal["tool"] = "tool"
    data: ToolEventData

class DoneSSEEvent(BaseSSEEvent):
    event: Literal["done"] = "done"
    data: BaseEventData

class ErrorSSEEvent(BaseSSEEvent):
    event: Literal["error"] = "error"
    data: ErrorEventData

class StepSSEEvent(BaseSSEEvent):
    event: Literal["step"] = "step"
    data: StepEventData

class TitleSSEEvent(BaseSSEEvent):
    event: Literal["title"] = "title"
    data: TitleEventData

class PlanSSEEvent(BaseSSEEvent):
    event: Literal["plan"] = "plan"
    data: PlanEventData

AgentSSEEvent = Union[
    BaseSSEEvent,
    PlanSSEEvent,
    MessageSSEEvent,
    TitleSSEEvent,
    ToolSSEEvent,
    StepSSEEvent,
    DoneSSEEvent,
    ErrorSSEEvent
]

class SSEEventFactory:
    @staticmethod
    def from_events(events: List[AgentEvent]) -> List[AgentSSEEvent]:
        return list(filter(lambda x: x is not None, [
            SSEEventFactory.from_event(event) for event in events if event
        ]))
    
    @staticmethod
    def from_event(event: AgentEvent) -> Optional[AgentSSEEvent]:
        if isinstance(event, PlanEvent):
            return PlanSSEEvent(
                data=PlanEventData(
                    event_id=event.id,
                    steps=[StepEventData(
                        status=step.status,
                        id=step.id, 
                        description=step.description
                    ) for step in event.plan.steps]
                )
            )
        elif isinstance(event, MessageEvent):
            return MessageSSEEvent(
                data=MessageEventData(event_id=event.id, content=event.message)
            )
        elif isinstance(event, TitleEvent):
            return TitleSSEEvent(
                data=TitleEventData(event_id=event.id, title=event.title)
            )
        elif isinstance(event, ToolEvent):
            if event.status == ToolStatus.CALLING and event.tool_name in ["browser", "file", "shell", "message"]:
                return ToolSSEEvent(
                    data=ToolEventData(
                        event_id=event.id,
                        name=event.tool_name,
                        status=event.status,
                        function=event.function_name,
                        args=event.function_args
                    )
                )
            elif event.status == ToolStatus.CALLED and event.tool_name in ["search"]:
                return ToolSSEEvent(
                    id=event.id,
                    data=ToolEventData(
                        name=event.tool_name,
                        function=event.function_name,
                        args=event.function_args,
                        status=event.status,
                        result=event.function_result
                    )
                )
        elif isinstance(event, StepEvent):
            return StepSSEEvent(
                id=event.id,
                data=StepEventData(
                    event_id=event.id,
                    status=event.step.status,
                    id=event.step.id, 
                    description=event.step.description
                )
            )
        elif isinstance(event, DoneEvent):
            return DoneSSEEvent(
                data=BaseEventData(event_id=event.id)
            )
        elif isinstance(event, ErrorEvent):
            return ErrorSSEEvent(
                data=ErrorEventData(event_id=event.id, error=event.error)
            )
