from typing import Optional, List
from datetime import datetime, UTC
from app.domain.models.session import Session, SessionStatus
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
        mongo_session.latest_message=session.latest_message
        mongo_session.latest_message_at=session.latest_message_at
        mongo_session.events=session.events
        mongo_session.status=session.status
        mongo_session.unread_message_count=session.unread_message_count
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
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"title": title, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_latest_message(self, session_id: str, message: str, timestamp: datetime) -> None:
        """Update the latest message of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"latest_message": message, "latest_message_at": timestamp, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def add_event(self, session_id: str, event: BaseEvent) -> None:
        """Add an event to a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$push": {"events": event}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def delete(self, session_id: str) -> None:
        """Delete a session"""
        mongo_session = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        )
        if mongo_session:
            await mongo_session.delete()

    async def get_all(self) -> List[Session]:
        """Get all sessions"""
        mongo_sessions = await SessionDocument.find().sort("-latest_message_at").to_list()
        return [self._to_domain_session(mongo_session) for mongo_session in mongo_sessions]
    
    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """Update the status of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"status": status, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def update_unread_message_count(self, session_id: str, count: int) -> None:
        """Update the unread message count of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$set": {"unread_message_count": count, "updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    async def increment_unread_message_count(self, session_id: str) -> None:
        """Atomically increment the unread message count of a session"""
        result = await SessionDocument.find_one(
            SessionDocument.session_id == session_id
        ).update(
            {"$inc": {"unread_message_count": 1}, "$set": {"updated_at": datetime.now(UTC)}}
        )
        if not result:
            raise ValueError(f"Session {session_id} not found")

    def _to_domain_session(self, mongo_session: SessionDocument) -> Session:
        """Convert MongoDB document to domain model"""
        return Session(
            id=mongo_session.session_id,
            sandbox_id=mongo_session.sandbox_id,
            agent_id=mongo_session.agent_id,
            task_id=mongo_session.task_id,
            title=mongo_session.title,
            latest_message=mongo_session.latest_message,
            latest_message_at=mongo_session.latest_message_at,
            created_at=mongo_session.created_at,
            updated_at=mongo_session.updated_at,
            events=mongo_session.events,
            status=mongo_session.status,
            unread_message_count=mongo_session.unread_message_count
        )
    
    def _to_mongo_session(self, session: Session) -> SessionDocument:
        """Convert domain session to MongoDB document"""
        return SessionDocument(
            session_id=session.id,
            sandbox_id=session.sandbox_id,
            agent_id=session.agent_id,
            task_id=session.task_id,
            title=session.title,
            latest_message=session.latest_message,
            latest_message_at=session.latest_message_at,
            created_at=session.created_at,
            updated_at=session.updated_at,
            events=session.events,
            status=session.status,
            unread_message_count=session.unread_message_count
        )
