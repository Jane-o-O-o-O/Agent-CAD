from typing import Optional, List, Dict, Any
from datetime import datetime
from beanie import Document
from pydantic import BaseModel


class MongoAgentMemories(BaseModel):
    """MongoDB document for Agent Memories"""
    messages: List[Dict[str, Any]]

class MongoAgent(Document):
    """MongoDB document for Agent"""
    agent_id: str
    model_name: str
    temperature: float
    max_tokens: int
    sandbox_id: Optional[str] = None
    memories: Dict[str, MongoAgentMemories] = {}
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = "agents"