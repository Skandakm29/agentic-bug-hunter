"""
Autonomous Repair Agents — Reviewer Agent  (Phase 3D.3)
========================================================
``ReviewerAgent`` performs a peer review of a patch candidate, scoring
it on maintainability, flagging risky patterns, and estimating the
long-term impact on code quality.

Responsibilities
----------------
- Review the patch candidate diff and reasoning.
- Score maintainability on a [0, 1] scale.
- Identify code smells, risky patterns, or unsafe constructs.
- Provide an independent confidence estimate.
- Feed maintainability_score into ``RepairScorer``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    RepairStrategy,
)
from backend.core.patch_generation.patch_models import PatchCandidate

logger = logging.getLogger("backend.autonomous_repair.agents.reviewer")

_REVIEWER_SYSTEM = """You are a principal software engineer performing a code review of an automated patch.
Evaluate the patch candidate and respond with JSON:
{
  "maintainability_score": <float 0.0–1.0>,
  "weaknesses": ["<list of identified weaknesses>"],
  "risky_patterns": ["<list of risky patterns or unsafe constructs>"],
  "review_summary": "<concise review narrative>",
  "confidence": <float 0.0–1.0>
}
Respond with JSON only."""


class ReviewerAgent(BaseRepairAgent):
    """
    Repair agent that reviews patch candidates for quality and maintainability.
    """

    role = AgentRole.REVIEWER

    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Parameters in ``context``
        -------------------------
        candidate : PatchCandidate (optional)
        patch_diff : str — unified diff of the candidate
        file_path : str
        bug_description : str
        iteration : int
        """
        t0 = time.perf_counter()
        candidate: Optional[PatchCandidate] = context.get("candidate")
        patch_diff: str = context.get("patch_diff", "")
        file_path: str = context.get("file_path", "")
        bug_description: str = context.get("bug_description", "")
        iteration: int = context.get("iteration", 0)

        # Use candidate diff if not explicitly provided
        if not patch_diff and candidate and candidate.unified_diff:
            patch_diff = candidate.unified_diff

        if not patch_diff:
            # No diff to review — return neutral assessment
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=None,
                reasoning="No patch diff available for review.",
                confidence=0.5,
                metadata={"maintainability_score": 0.5},
            )

        prompt = self._build_prompt(patch_diff, file_path, bug_description, candidate)

        try:
            raw = await self._provider.generate_completion_async(
                prompt=prompt,
                system_prompt=_REVIEWER_SYSTEM,
                model=self._cfg.model_name,
                temperature=self._cfg.temperature,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("ReviewerAgent: LLM call failed — %s", exc)
            raw = None

        duration_ms = (time.perf_counter() - t0) * 1_000
        return self._parse(raw, iteration, duration_ms)

    def _build_prompt(
        self,
        patch_diff: str,
        file_path: str,
        bug_description: str,
        candidate: Optional[PatchCandidate],
    ) -> str:
        lines = [
            f"## Code Review Request",
            f"File: {file_path}",
            f"Bug: {bug_description}",
        ]
        if candidate:
            lines.append(f"LLM Confidence: {candidate.confidence:.2f}")
            lines.append(f"Estimated Risk: {candidate.estimated_risk:.2f}")
            if candidate.reasoning:
                lines.append(f"LLM Reasoning: {candidate.reasoning[:300]}")
        lines.append(f"\n## Patch Diff\n```diff\n{patch_diff[:1500]}\n```")
        lines.append("\nReview this patch and respond with JSON.")
        return "\n".join(lines)

    def _parse(
        self,
        raw: Optional[str],
        iteration: int,
        duration_ms: float,
    ) -> AgentDecision:
        data: Dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass

        maintainability = float(data.get("maintainability_score", 0.5))
        maintainability = max(0.0, min(1.0, maintainability))
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        weaknesses = list(data.get("weaknesses", []))
        risky = list(data.get("risky_patterns", []))
        all_actions = weaknesses + risky

        return AgentDecision(
            agent_role=self.role,
            iteration=iteration,
            strategy=None,
            reasoning=str(data.get("review_summary", "Review complete.")),
            confidence=confidence,
            recommended_actions=all_actions[:5],
            metadata={"maintainability_score": maintainability},
            duration_ms=round(duration_ms, 2),
        )
