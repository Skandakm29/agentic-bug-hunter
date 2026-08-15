"""
Autonomous Repair Agents — Base Agent  (Phase 3D.3)
====================================================
``BaseRepairAgent`` is the ABC that every specialized repair agent
must extend.  It defines the standard ``execute()`` contract and
provides shared utilities (timeout enforcement, error wrapping,
metrics recording).
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.exceptions import AgentExecutionError, AgentTimeoutError
from backend.core.autonomous_repair.repair_models import AgentDecision, AgentRole

logger = logging.getLogger("backend.autonomous_repair.agents.base")


class BaseRepairAgent(ABC):
    """
    Abstract base class for all autonomous repair agents.

    Parameters
    ----------
    provider : Any ``BaseLLMProvider`` implementation.
    config : ``RepairConfiguration`` shared across the session.
    """

    role: AgentRole  # Must be overridden by every concrete subclass

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: Optional[RepairConfiguration] = None,
    ) -> None:
        self._provider = provider
        self._cfg = config or RepairConfiguration()
        self._call_count: int = 0
        self._error_count: int = 0
        self._total_duration_ms: float = 0.0

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Core agent logic.  Subclasses implement this method.

        Parameters
        ----------
        context : Arbitrary dict containing all inputs the agent needs.

        Returns
        -------
        ``AgentDecision`` with the agent's output.
        """

    # ------------------------------------------------------------------
    # Public execute wrapper
    # ------------------------------------------------------------------

    async def execute(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Execute the agent with timeout enforcement and error wrapping.

        Wraps ``_run()`` with:
        - Wall-clock timeout (``config.agent_timeout_seconds``)
        - Exception conversion to ``AgentExecutionError``
        - Timing / call-count tracking

        Parameters
        ----------
        context : Input context dict forwarded to ``_run()``.
        """
        t0 = time.perf_counter()
        self._call_count += 1
        role_name = self.role.value

        logger.debug("Agent %s: starting (call #%d).", role_name, self._call_count)

        try:
            decision = await asyncio.wait_for(
                self._run(context),
                timeout=self._cfg.agent_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self._error_count += 1
            raise AgentTimeoutError(
                f"Agent '{role_name}' exceeded timeout of "
                f"{self._cfg.agent_timeout_seconds}s.",
                agent_role=role_name,
                original_error=exc,
                iteration=context.get("iteration", -1),
            ) from exc
        except AgentExecutionError:
            self._error_count += 1
            raise
        except Exception as exc:
            self._error_count += 1
            raise AgentExecutionError(
                f"Agent '{role_name}' raised an unexpected error: {exc}",
                agent_role=role_name,
                original_error=exc,
                iteration=context.get("iteration", -1),
            ) from exc
        finally:
            elapsed = (time.perf_counter() - t0) * 1_000
            self._total_duration_ms += elapsed

        decision.duration_ms = round((time.perf_counter() - t0) * 1_000, 2)
        logger.debug(
            "Agent %s: completed in %.1fms (confidence=%.2f).",
            role_name,
            decision.duration_ms,
            decision.confidence,
        )
        return decision

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, Any]:
        """Return call statistics for this agent instance."""
        avg_ms = (
            self._total_duration_ms / self._call_count
            if self._call_count > 0
            else 0.0
        )
        return {
            "role": self.role.value,
            "call_count": self._call_count,
            "error_count": self._error_count,
            "avg_duration_ms": round(avg_ms, 2),
        }
