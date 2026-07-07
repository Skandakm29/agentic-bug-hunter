"""
llm_validator.py — LLM Validation Layer

SOLID Principles Applied:
  S → LLMValidator has ONE job: call Groq and return structured result
  D → depends on groq client injected via constructor
      not hardcoded — easy to swap for a different LLM provider
"""

import re
import json
from typing import Optional
from groq import Groq
from static_engine import Finding


class LLMValidator:
    """
    Sends code and the most suspicious static finding to Groq LLaMA
    for semantic analysis, explanation, and corrected code generation.

    Why inject groq_client?
      Dependency Inversion — LLMValidator doesn't create its own client.
      The client is passed in. This makes it easy to:
        → swap Groq for OpenAI (just pass a different client)
        → mock the client in tests
        → reuse one client instance across the app
    """

    def __init__(self, groq_client: Groq, model: str):
        self.client = groq_client
        self.model  = model

    def validate(self, code: str, finding: Finding) -> Optional[dict]:
        """
        Call the LLM with the code and best static finding.
        Returns structured JSON result or None on any failure.

        Why send only ONE finding (not all)?
          → Focused prompt → better, more specific LLM response
          → Fewer tokens → lower cost
          → LLM doesn't get confused by multiple issues at once
        """
        prompt = self._build_prompt(code, finding)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,   # near-deterministic output
                max_tokens=1024
            )
            raw = response.choices[0].message.content
            return self._extract_json(raw)

        except Exception as e:
            # Catches everything: network errors, auth errors,
            # rate limits, model errors — system degrades gracefully
            print(f"[LLM] error: {e}")
            return None

    def _build_prompt(self, code: str, finding: Finding) -> str:
        """Build the focused prompt for the LLM."""
        return f"""You are an expert embedded systems C++ engineer.
Analyze this code for bugs: integer overflow, missing volatile, null pointers,
stack overflow, incorrect bit manipulation, blocking delays in ISR, etc.

Suspicious area (line {finding.line_number or '?'}):
{finding.line_text}

Full Code:
{code}

Respond ONLY in JSON, no extra text, no markdown:
{{
  "valid_bug": true,
  "explanation": "...",
  "corrected_code": "...",
  "confidence": 0.0
}}"""

    def _extract_json(self, raw: str) -> Optional[dict]:
        """
        Defensively extract JSON from LLM response.
        Even if the model adds extra text around the JSON,
        this regex finds and parses just the {...} block.
        """
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                print(f"[LLM] JSON parse failed: {raw[:100]}")
        return None
