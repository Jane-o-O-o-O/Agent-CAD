from typing import Optional, List, Protocol
from app.domain.models.session import Session, SessionStatus
from datetime import datetime

class SessionRepository(Protocol):
    """Repository interface for Session aggregate"""
    
    async def save(self, session: Session) -> None:
        """Save or update a session"""
        ...
    
    async def find_by_id(self, session_id: str) -> Optional[Session]:
        """Find a session by its ID"""
        ...
    
    async def find_by_agent_id(self, agent_id: str) -> List[Session]:
        """Find all sessions for a specific agent"""
        ...
    
    async def find_active_sessions(self, agent_id: str) -> List[Session]:
        """Find all active sessions for a specific agent"""
        ...
    
    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """Update the status of a session"""
        ...
    
    async def update_last_message(self, session_id: str, message: str) -> None:
        """Update the last message and timestamp of a session"""
        ...
    
    async def delete(self, session_id: str) -> None:
        """Delete a session"""
        ... 