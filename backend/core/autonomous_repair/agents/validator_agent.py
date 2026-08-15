"""
Autonomous Repair Agents — Validator Agent  (Phase 3D.3)
=========================================================
``ValidatorAgent`` analyses the ``ValidationReport`` from Phase 3D.2 and
the ``StructuredFeedback`` from ``FeedbackEngine`` to produce a clear,
developer-facing explanation of why the current candidate failed and
which root causes should be targeted next.

Responsibilities
----------------
- Analyse validation report and explain failures clearly.
- Identify the root cause of each failure.
- Recommend which elements to preserve vs modify.
- Contribute to the ``AgentDecision`` that feeds the PlannerAgent.
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
    StructuredFeedback,
)
from backend.core.patch_validation.validation_models import ValidationReport

logger = logging.getLogger("backend.autonomous_repair.agents.validator")

_VALIDATOR_SYSTEM = """You are a senior software validation engineer.
Analyse the provided validation failure report and produce a structured JSON response:
{
  "root_cause_summary": "<concise root cause explanation>",
  "preserve_elements": ["<code elements / patterns that are correct and must be preserved>"],
  "modify_elements": ["<code elements / patterns that are causing failures>"],
  "recommended_actions": ["<ordered concrete actions for the next repair attempt>"],
  "confidence": <float 0.0–1.0>
}
Respond with JSON only."""


class ValidatorAgent(BaseRepairAgent):
    """
    Repair agent that analyses validation failures and identifies root causes.
    """

    role = AgentRole.VALIDATOR

    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Parameters in ``context``
        -------------------------
        feedback : StructuredFeedback
        report : ValidationReport (optional)
        iteration : int
        """
        t0 = time.perf_counter()
        feedback: Optional[StructuredFeedback] = context.get("feedback")
        report: Optional[ValidationReport] = context.get("report")
        iteration: int = context.get("iteration", 0)

        if not feedback:
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.REFINE_EXISTING,
                reasoning="No feedback provided — defaulting to refine existing.",
                confidence=0.3,
            )

        prompt = self._build_prompt(feedback, report)

        try:
            raw = await self._provider.generate_completion_async(
                prompt=prompt,
                system_prompt=_VALIDATOR_SYSTEM,
                model=self._cfg.model_name,
                temperature=self._cfg.reasoning_temperature,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("ValidatorAgent: LLM call failed — %s", exc)
            raw = None

        duration_ms = (time.perf_counter() - t0) * 1_000
        return self._parse(raw, iteration, duration_ms)

    def _build_prompt(
        self,
        feedback: StructuredFeedback,
        report: Optional[ValidationReport],
    ) -> str:
        lines = [
            "## Validation Failure Report",
            f"- Compilation failed: {feedback.compilation_failed}",
            f"- Regression failed: {feedback.regression_failed}",
            f"- Bug not removed: {feedback.bug_not_removed}",
            f"- New bugs introduced: {feedback.new_bugs_introduced}",
        ]
        if feedback.failure_reasons:
            lines.append("\n## Failure Reasons")
            for r in feedback.failure_reasons[:6]:
                lines.append(f"- [{r.severity.value}] {r.category}: {r.description}")
                if r.repair_hint:
                    lines.append(f"  Hint: {r.repair_hint}")
        if report and report.diagnostics.errors:
            lines.append("\n## Compiler / Validator Errors")
            for err in report.diagnostics.errors[:5]:
                lines.append(f"- {err}")
        lines.append("\nAnalyse the above and return JSON guidance.")
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

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return AgentDecision(
            agent_role=self.role,
            iteration=iteration,
            strategy=RepairStrategy.REFINE_EXISTING,
            reasoning=str(data.get("root_cause_summary", "Validation analysis complete.")),
            confidence=confidence,
            recommended_actions=list(data.get("recommended_actions", [])),
            preserve_elements=list(data.get("preserve_elements", [])),
            modify_elements=list(data.get("modify_elements", [])),
            duration_ms=round(duration_ms, 2),
        )
