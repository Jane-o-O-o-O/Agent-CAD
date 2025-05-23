from pydantic import BaseModel

class ChatRequest(BaseModel):
    timestamp: int
    message: str

class FileViewRequest(BaseModel):
    file: str

class ShellViewRequest(BaseModel):
    session_id: str