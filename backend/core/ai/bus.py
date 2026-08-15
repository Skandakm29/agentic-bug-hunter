import time
import uuid
import logging
import asyncio
from typing import Any, Callable, Dict, List
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.ai.bus")


class MessageEnvelope(BaseModel):
    """Structured immutable envelope carrying inter-agent task allocations."""
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: str
    sender: str
    receiver: str
    timestamp: float = Field(default_factory=time.time)
    message_type: str  # e.g., "TASK_ASSIGNMENT", "EVIDENCE_SUBMISSION", "VALIDATION_REQUEST"
    payload: Dict[str, Any]
    priority: int = 1


class AgentMessageBus:
    """Bus orchestrator managing subscription channels and routing envelopes."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._history: List[MessageEnvelope] = []

    def subscribe(self, agent_name: str, handler: Callable[[MessageEnvelope], Any]):
        if agent_name not in self._subscribers:
            self._subscribers[agent_name] = []
        self._subscribers[agent_name].append(handler)
        logger.info(f"Agent '{agent_name}' subscribed to Message Bus.")

    async def publish(self, envelope: MessageEnvelope):
        self._history.append(envelope)
        logger.debug(f"[Bus Event] {envelope.sender} -> {envelope.receiver} [{envelope.message_type}]")

        handlers = self._subscribers.get(envelope.receiver, [])
        for handler in handlers:
            try:
                # Dispatch handler task asynchronously
                if asyncio.iscoroutinefunction(handler):
                    await handler(envelope)
                else:
                    await asyncio.to_thread(handler, envelope)
            except Exception as e:
                logger.error(f"Failed to deliver message to agent '{envelope.receiver}': {e}")

    def get_history(self) -> List[MessageEnvelope]:
        return list(self._history)
