import json
import re
from backend.core.ai.prompts.library import SYSTEM_INSTRUCTION, VALIDATOR_PROMPT
from backend.core.ai.schemas import BugValidationResult
from backend.core.ai.agents.base import BaseAgent


class ValidatorAgent(BaseAgent):
    """Agent responsible for checking if a static warning corresponds to a real semantic bug."""

    async def validate_finding(self, code: str, finding: dict) -> BugValidationResult:
        prompt = VALIDATOR_PROMPT.format(
            # NormalizedFinding exposes this as `rule_id`; accept either so the
            # prompt never degrades to "Rule: unknown".
            rule_tag=finding.get("rule_id") or finding.get("rule_tag") or "unknown",
            description=finding.get("description", "No description"),
            line_number=finding.get("line_number", "?"),
            line_text=finding.get("line_text", ""),
            code=code
        )

        from backend.core.ai.router import ModelRoutingEngine
        route = ModelRoutingEngine().route_task("static_validation")

        response = await self.provider.generate_completion_async(
            prompt=prompt,
            system_prompt=SYSTEM_INSTRUCTION,
            response_format={"type": "json_object"},
            model=route.get("model"),
            temperature=route.get("temperature"),
            max_tokens=route.get("max_tokens")
        )

        # Fallback values if API fails or returns invalid format
        fallback = BugValidationResult(
            valid_bug=False,
            explanation="Failed to connect to LLM validator or parse response.",
            confidence=0.0
        )

        if not response:
            return fallback

        try:
            m = re.search(r'\{.*\}', response, re.DOTALL)
            if m:
                data = json.loads(m.group())
                return BugValidationResult(
                    valid_bug=bool(data.get("valid_bug", False)),
                    explanation=str(data.get("explanation", "")),
                    confidence=float(data.get("confidence", 0.0))
                )
        except Exception as e:
            print(f"[ValidatorAgent] Parsing Error: {e}")

        return fallback
