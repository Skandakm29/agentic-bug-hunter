import json
import re
from backend.core.ai.prompts.library import SYSTEM_INSTRUCTION, FIXER_PROMPT
from backend.core.ai.schemas import CodeCorrection
from backend.core.ai.agents.base import BaseAgent


class FixerAgent(BaseAgent):
    """Agent responsible for proposing C++ code corrections for confirmed bugs."""

    async def generate_fix(self, code: str, finding: dict, explanation: str) -> CodeCorrection:
        prompt = FIXER_PROMPT.format(
            line_text=finding.get("line_text", ""),
            explanation=explanation,
            code=code
        )

        from backend.core.ai.router import ModelRoutingEngine
        route = ModelRoutingEngine().route_task("complex_fix")

        response = await self.provider.generate_completion_async(
            prompt=prompt,
            system_prompt=SYSTEM_INSTRUCTION,
            response_format={"type": "json_object"},
            model=route.get("model"),
            temperature=route.get("temperature"),
            max_tokens=route.get("max_tokens")
        )

        fallback = CodeCorrection(corrected_code=finding.get("line_text", ""))

        if not response:
            return fallback

        try:
            m = re.search(r'\{.*\}', response, re.DOTALL)
            if m:
                data = json.loads(m.group())
                return CodeCorrection(
                    corrected_code=str(data.get("corrected_code", fallback.corrected_code))
                )
        except Exception as e:
            print(f"[FixerAgent] Parsing Error: {e}")

        return fallback
