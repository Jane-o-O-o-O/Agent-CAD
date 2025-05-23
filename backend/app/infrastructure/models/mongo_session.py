from datetime import datetime, UTC
from typing import List, Optional
from beanie import Document, Link
from app.domain.models.session import SessionStatus
from app.infrastructure.models.mongo_agent import MongoAgent
from app.domain.events.agent_events import AgentEvent


class MongoSession(Document):
    """MongoDB model for Session"""
    session_id: str
    sandbox_id: Optional[str] = None
    agent_id: str
    task_id: Optional[str] = None
    title: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    created_at: datetime = datetime.now(UTC)
    updated_at: datetime = datetime.now(UTC)
    events: List[AgentEvent]
    status: SessionStatus

    class Settings:
        name = "sessions"
        indexes = [
            "session_id",
        ] 