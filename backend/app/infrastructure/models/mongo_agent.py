from typing import Dict
from datetime import datetime
from beanie import Document
from app.domain.models.memory import Memory

class MongoAgent(Document):
    """MongoDB document for Agent"""
    agent_id: str
    model_name: str
    temperature: float
    max_tokens: int
    memories: Dict[str, Memory] = {}
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = "agents"
        indexes = [
            "agent_id",
        ] 