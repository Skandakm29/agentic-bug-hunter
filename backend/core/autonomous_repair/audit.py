"""
Autonomous Repair Loop — Audit Trail  (Phase 3D.3)
===================================================
``AuditTrail`` is an append-only, timestamped event log that records
every significant action taken during a repair session.

Design invariants
-----------------
- Append-only: events are never modified or deleted.
- Thread-safe via a reentrant lock.
- ``export_jsonl()`` emits newline-delimited JSON for full reproducibility.
- ``replay()`` reconstructs an ordered event list from JSONL bytes, enabling
  session post-mortems without re-executing.
- Event payloads are serialized using ``str`` fallback to handle arbitrary
  Pydantic models and datetimes.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from backend.core.autonomous_repair.repair_models import AuditEvent, AuditEventType

logger = logging.getLogger("backend.autonomous_repair.audit")


class AuditTrail:
    """
    Append-only event log for full session reproducibility.

    Every component in the repair loop calls ``log()`` at key decision
    points; the resulting log sequence can be replayed to reconstruct the
    exact sequence of events without re-running any computation.
    """

    def __init__(self, session_id: str = "", enabled: bool = True) -> None:
        self._session_id = session_id
        self._enabled = enabled
        self._lock = threading.RLock()
        self._events: List[AuditEvent] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: AuditEventType,
        payload: Optional[Dict[str, Any]] = None,
        iteration: int = -1,
        agent_role: Optional[str] = None,
    ) -> Optional[AuditEvent]:
        """
        Append a new event to the audit trail.

        Parameters
        ----------
        event_type : Type of event being recorded.
        payload : Optional dict of structured event data.
        iteration : Repair loop iteration index (−1 = outside loop).
        agent_role : Role name if the event is agent-specific.

        Returns
        -------
        The created ``AuditEvent``, or None if the trail is disabled.
        """
        if not self._enabled:
            return None

        event = AuditEvent(
            event_type=event_type,
            session_id=self._session_id,
            iteration=iteration,
            agent_role=agent_role,
            payload=payload or {},
            timestamp=time.time(),
        )
        with self._lock:
            self._events.append(event)

        logger.debug(
            "AuditTrail[%s]: %s (iteration=%d, agent=%s)",
            self._session_id,
            event_type.value,
            iteration,
            agent_role or "—",
        )
        return event

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_events(self) -> List[AuditEvent]:
        """Return all events in chronological order."""
        with self._lock:
            return list(self._events)

    def get_events_by_type(self, event_type: AuditEventType) -> List[AuditEvent]:
        """Filter events by type."""
        with self._lock:
            return [e for e in self._events if e.event_type == event_type]

    def get_events_by_iteration(self, iteration: int) -> List[AuditEvent]:
        """Return all events from a specific loop iteration."""
        with self._lock:
            return [e for e in self._events if e.iteration == iteration]

    @property
    def event_count(self) -> int:
        """Total number of recorded events."""
        with self._lock:
            return len(self._events)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def export_jsonl(self) -> str:
        """
        Serialize the audit trail to newline-delimited JSON (JSONL).

        Each line is one ``AuditEvent`` serialized with ``default=str``
        so all timestamps, UUIDs, and Enum values round-trip cleanly.
        """
        with self._lock:
            events = list(self._events)

        lines: List[str] = []
        for event in events:
            lines.append(json.dumps(event.model_dump(), default=str))
        return "\n".join(lines)

    @staticmethod
    def replay(jsonl: str) -> List[AuditEvent]:
        """
        Reconstruct an ordered ``AuditEvent`` list from a JSONL string.

        This enables post-mortem analysis and session reproducibility
        without re-executing the repair loop.

        Parameters
        ----------
        jsonl : Newline-delimited JSON string (output of ``export_jsonl()``).

        Returns
        -------
        List of ``AuditEvent`` objects in original order.
        """
        events: List[AuditEvent] = []
        for line in jsonl.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(AuditEvent.model_validate(data))
            except Exception as exc:
                logger.warning("AuditTrail.replay: could not parse line: %s — %s", line[:80], exc)
        return events

    def set_session_id(self, session_id: str) -> None:
        """Update the session_id on all future events."""
        with self._lock:
            self._session_id = session_id
