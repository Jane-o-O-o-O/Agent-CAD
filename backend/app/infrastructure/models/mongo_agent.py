from typing import Optional, List, Dict, Any
from datetime import datetime
from beanie import Document, Indexed, Link
from app.infrastructure.models.mongo_memory import MongoMemory


class MongoAgent(Document):
    """MongoDB document for Agent"""
    agent_id: Indexed(str, unique=True)
    model_name: str
    temperature: float
    max_tokens: int
    sandbox_id: Optional[str] = None
    memories: Dict[str, Link[MongoMemory]] = {}
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = "agents"