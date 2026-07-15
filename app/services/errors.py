from __future__ import annotations

from app.domain.profiles import PROFILES


class UnknownAgentError(Exception):
    """Raised when `agent_id` doesn't resolve to a known profile (M1.T4)."""

    def __init__(self, agent_id: str) -> None:
        valid = ", ".join(PROFILES)
        super().__init__(f"unknown agent_id: {agent_id!r}. Valid: {valid}")
        self.agent_id = agent_id
