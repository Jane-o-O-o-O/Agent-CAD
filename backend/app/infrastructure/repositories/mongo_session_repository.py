from typing import Optional
from datetime import datetime, UTC
from app.domain.models.session import Session
from app.domain.repositories.session_repository import SessionRepository
from app.domain.events.agent_events import BaseEvent
from app.infrastructure.models.documents import SessionDocument
import logging

logger = logging.getLogger(__name__)

class MongoSessionRepository(SessionRepository):
    """MongoDB implementation of SessionRepository"""
    
    async def save(self, session: Session) -> None:
        """Save or update a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session.id
        )
        
        if not mongo_session:
            mongo_session = self._to_mongo_session(session)
            await mongo_session.save()
            return
        
        # Use generic update method from base class
        mongo_session.sandbox_id=session.sandbox_id
        mongo_session.agent_id=session.agent_id
        mongo_session.task_id=session.task_id
        mongo_session.title=session.title
        mongo_session.last_message=session.last_message
        mongo_session.last_message_at=session.last_message_at
        mongo_session.events=session.events
        mongo_session.status=session.status
        mongo_session.updated_at=datetime.now(UTC)
        await mongo_session.save()


    async def find_by_id(self, session_id: str) -> Optional[Session]:
        """Find a session by its ID"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        return self._to_domain_session(mongo_session) if mongo_session else None
    
    async def update_title(self, session_id: str, title: str) -> None:
        """Update the title of a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if not mongo_session:
            raise ValueError(f"Session {session_id} not found")
        
        mongo_session.title = title
        mongo_session.updated_at = datetime.now(UTC)
        await mongo_session.save()

    async def add_event(self, session_id: str, event: BaseEvent) -> None:
        """Add an event to a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if not mongo_session:
            raise ValueError(f"Session {session_id} not found")
        
        mongo_session.events.append(event)
        mongo_session.updated_at = datetime.now(UTC)
        await mongo_session.save()

    async def delete(self, session_id: str) -> None:
        """Delete a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if mongo_session:
            await mongo_session.delete()

    def _to_domain_session(self, mongo_session: SessionDocument) -> Session:
        """Convert MongoDB document to domain model"""
        return Session(
            id=mongo_session.session_id,
            sandbox_id=mongo_session.sandbox_id,
            agent_id=mongo_session.agent_id,
            task_id=mongo_session.task_id,
            title=mongo_session.title,
            last_message=mongo_session.last_message,
            last_message_at=mongo_session.last_message_at,
            created_at=mongo_session.created_at,
            updated_at=mongo_session.updated_at,
            events=mongo_session.events,
            status=mongo_session.status
        )
    
    def _to_mongo_session(self, session: Session) -> SessionDocument:
        """Convert domain session to MongoDB document"""
        return SessionDocument(
            session_id=session.id,
            sandbox_id=session.sandbox_id,
            agent_id=session.agent_id,
            task_id=session.task_id,
            title=session.title,
            last_message=session.last_message,
            last_message_at=session.last_message_at,
            created_at=session.created_at,
            updated_at=session.updated_at,
            events=session.events,
            status=session.status
        )
