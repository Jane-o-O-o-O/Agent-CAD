from typing import Optional, Protocol
from app.domain.models.session import Session
from app.domain.events.agent_events import BaseEvent

class SessionRepository(Protocol):
    """Repository interface for Session aggregate"""
    
    async def save(self, session: Session) -> None:
        """Save or update a session"""
        ...
    
    async def find_by_id(self, session_id: str) -> Optional[Session]:
        """Find a session by its ID"""
        ...
    
    async def update_title(self, session_id: str, title: str) -> None:
        """Update the title of a session"""
        ...

    async def add_event(self, session_id: str, event: BaseEvent) -> None:
        """Add an event to a session"""
        ...
    
    async def delete(self, session_id: str) -> None:
        """Delete a session"""
        ... 