from typing import Any

from pydantic import BaseModel, Field


class Memory(BaseModel):
    """Persisted agent memory payload.

    AgentScope currently keeps runtime context in-process. This model remains
    for repository/database compatibility and future persisted AgentScope state.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return len(self.messages) == 0
