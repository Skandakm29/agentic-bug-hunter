"""
Autonomous Repair Agents — Patch Generator Agent  (Phase 3D.3)
===============================================================
``PatchGeneratorAgent`` wraps the existing Phase 3D.1 ``PatchGenerationEngine``
and adapts it to the agent interface.

Responsibilities
----------------
- Generate new patch candidates from scratch using the 3D.1 engine.
- Incorporate element-level guidance (preserve/modify lists) from the
  ValidatorAgent and ReasoningEngine into the generation context.
- Return an ``AgentDecision`` describing what was generated.

This agent does NOT re-implement any generation logic; it exclusively
orchestrates the existing ``PatchGenerationEngine``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    RepairStrategy,
)
from backend.core.patch_generation.patch_generator import PatchGenerationEngine
from backend.core.patch_generation.patch_models import (
    PatchGenerationConfig,
    StructuredPatch,
)

logger = logging.getLogger("backend.autonomous_repair.agents.patch_generator")


class PatchGeneratorAgent(BaseRepairAgent):
    """
    Repair agent that generates new patch candidates using the 3D.1 engine.
    """

    role = AgentRole.PATCH_GENERATOR

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        gen_config = PatchGenerationConfig(
            candidate_count=self._cfg.candidates_per_iteration,
            temperature=self._cfg.temperature,
            max_tokens=self._cfg.max_tokens,
        )
        self._engine = PatchGenerationEngine(
            provider=self._provider,
            config=gen_config,
        )
        self._last_patch: Optional[StructuredPatch] = None

    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Parameters in ``context``
        -------------------------
        finding : NormalizedFinding
        code : str
        root_cause : RootCauseChain (optional)
        evidence : EvidenceGraph (optional)
        bug_id : str
        preserve_elements : List[str] — injected from ValidatorAgent
        modify_elements : List[str] — injected from ValidatorAgent
        iteration : int
        """
        t0 = time.perf_counter()
        finding = context.get("finding")
        code: str = context.get("code", "")
        root_cause = context.get("root_cause")
        evidence = context.get("evidence")
        bug_id: str = context.get("bug_id", "UNKNOWN")
        iteration: int = context.get("iteration", 0)
        preserve: List[str] = context.get("preserve_elements", [])
        modify: List[str] = context.get("modify_elements", [])

        if not finding:
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.GENERATE_NEW,
                reasoning="No finding provided — cannot generate patches.",
                confidence=0.0,
            )

        # Inject repair hints into finding metadata if possible
        if preserve or modify:
            extra_hint = ""
            if preserve:
                extra_hint += f" PRESERVE: {', '.join(preserve[:3])}."
            if modify:
                extra_hint += f" MODIFY: {', '.join(modify[:3])}."
            # Append hints to finding description if it supports metadata
            try:
                if hasattr(finding, "metadata"):
                    finding.metadata["repair_hints"] = extra_hint
            except Exception:
                pass

        try:
            patch = await self._engine.generate(
                finding=finding,
                code=code,
                root_cause=root_cause,
                evidence=evidence,
                bug_id=bug_id,
            )
            self._last_patch = patch
            candidate_count = sum(
                len(fp.candidates) for fp in patch.file_patches
            )
            duration_ms = (time.perf_counter() - t0) * 1_000
            logger.info(
                "PatchGeneratorAgent: generated %d candidate(s) for bug=%s in %.1fms",
                candidate_count,
                bug_id,
                duration_ms,
            )
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.GENERATE_NEW,
                reasoning=f"Generated {candidate_count} new candidate(s).",
                confidence=0.7,
                recommended_actions=[f"Validate {candidate_count} generated candidate(s)."],
                metadata={"patch_id": patch.patch_id, "candidate_count": candidate_count},
                duration_ms=round(duration_ms, 2),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1_000
            logger.error("PatchGeneratorAgent: generation failed — %s", exc)
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.GENERATE_NEW,
                reasoning=f"Generation failed: {exc}",
                confidence=0.0,
                duration_ms=round(duration_ms, 2),
            )

    @property
    def last_patch(self) -> Optional[StructuredPatch]:
        """Return the last ``StructuredPatch`` produced by this agent."""
        return self._last_patch
