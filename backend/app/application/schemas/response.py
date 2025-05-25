from typing import Any, Generic, Optional, TypeVar, List
from pydantic import BaseModel
from app.domain.events.agent_events import AgentEvent


T = TypeVar('T')


class APIResponse(BaseModel, Generic[T]):
    code: int = 0
    msg: str = "success"
    data: Optional[T] = None 

    @staticmethod
    def success(data: Optional[T] = None) -> "APIResponse[T]":
        return APIResponse(code=0, msg="success", data=data)

    @staticmethod
    def error(code: int, msg: str) -> "APIResponse[T]":
        return APIResponse(code=code, msg=msg, data=None)


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str = "created"
    message: str = "Session created successfully" 

class GetSessionResponse(BaseModel):
    events: List[AgentEvent]

class ConsoleRecord(BaseModel):
    ps1: str
    command: str
    output: str

class ShellViewResponse(BaseModel):
    output: str
    session_id: str
    console: Optional[List[ConsoleRecord]] = None

class FileViewResponse(BaseModel):
    content: str
    file: str
