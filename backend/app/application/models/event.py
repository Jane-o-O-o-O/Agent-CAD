from pydantic import BaseModel, Field
from typing import Any, Union, Literal, Dict, Optional, List
import time
from app.domain.models.plan import ExecutionStatus
from app.domain.events.agent_events import ToolStatus


class BaseData(BaseModel):
    timestamp: int = Field(default_factory=lambda: int(time.time()))

class MessageData(BaseData):
    content: str

class ToolData(BaseData):
    name: str
    status: ToolStatus
    function: str
    args: Dict[str, Any]
    result: Optional[Any] = None

class StepData(BaseData):
    status: ExecutionStatus
    id: str
    description: str


class PlanData(BaseData):
    steps: List[StepData]

class ErrorData(BaseData):
    error: str

class TitleData(BaseData):
    title: str

class SSEEvent(BaseModel):
    event: str
    data: Optional[Union[str, BaseData]]

class MessageSSEEvent(SSEEvent):
    event: Literal["message"] = "message"
    data: MessageData

class ToolSSEEvent(SSEEvent):
    event: Literal["tool"] = "tool"
    data: ToolData

class DoneSSEEvent(SSEEvent):
    event: Literal["done"] = "done"
    data: BaseData

class ErrorSSEEvent(SSEEvent):
    event: Literal["error"] = "error"
    data: ErrorData

class StepSSEEvent(SSEEvent):
    event: Literal["step"] = "step"
    data: StepData

class TitleSSEEvent(SSEEvent):
    event: Literal["title"] = "title"
    data: TitleData

class PlanSSEEvent(SSEEvent):
    event: Literal["plan"] = "plan"
    data: PlanData
