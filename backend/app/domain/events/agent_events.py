from pydantic import BaseModel, Field
from typing import Dict, Any, Literal, Optional
import time
import uuid
from enum import Enum
from app.domain.models.plan import Plan, Step


class PlanStatus(str, Enum):
    """Plan status enum"""
    CREATED = "created"
    UPDATED = "updated"
    COMPLETED = "completed"


class StepStatus(str, Enum):
    """Step status enum"""
    STARTED = "started"
    FAILED = "failed"
    COMPLETED = "completed"


class ToolStatus(str, Enum):
    """Tool status enum"""
    CALLING = "calling"
    CALLED = "called"


class AgentEvent(BaseModel):
    """Base class for agent events"""
    type: str
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: int = Field(default_factory=lambda: int(time.time()))

class ErrorEvent(AgentEvent):
    """Error event"""
    type: Literal["error"] = "error"
    error: str

class PlanEvent(AgentEvent):
    """Plan related events"""
    type: Literal["plan"] = "plan"
    plan: Plan
    status: PlanStatus
    step: Optional[Step] = None

class ToolEvent(AgentEvent):
    """Tool related events"""
    type: Literal["tool"] = "tool"
    tool_name: str
    function_name: str
    function_args: Dict[str, Any]
    status: ToolStatus
    function_result: Optional[Any] = None

class StepEvent(AgentEvent):
    """Step related events"""
    type: Literal["step"] = "step"
    step: Step
    status: StepStatus

class MessageEvent(AgentEvent):
    """Message event"""
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"
    message: str

class DoneEvent(AgentEvent):
    """Done event"""
    type: Literal["done"] = "done"
