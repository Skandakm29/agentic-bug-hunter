"""
validator_agent.py — LLM-based validator using Ollama llama3.
Takes a static finding and validates/explains it using the full code context.
Also used as LLM fallback when static analysis finds nothing.
"""

import re
import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3"  # Fixed: was phi3


def validate_issue(code_context: str, finding: dict) -> str:
    """
    Call llama3 to validate a static finding and return raw JSON string.
    
    Args:
        code_context: Full source code
        finding: Dict with line_number, line_text, rule_tag, static_confidence
    
    Returns:
        Raw JSON string from LLM with keys: valid_bug, explanation, corrected_code, confidence, bug_line
    """
    line_text = finding.get("line_text", "")
    line_number = finding.get("line_number", "unknown")
    rule_tag = finding.get("rule_tag", "unknown")
    static_hint = finding.get("explanation", "")

    prompt = f"""You are an expert in embedded systems and RDI C++ API code analysis.

A static analysis tool flagged the following issue:
- Rule: {rule_tag}
- Line {line_number}: {line_text}
- Static hint: {static_hint}

Full Code:
{code_context}

Your task:
1. Determine if this is a real bug or a false positive
2. Explain clearly what the bug is (if real)
3. Provide the corrected line or corrected full code

Respond STRICTLY in this JSON format only — no extra text, no markdown:
{{
  "valid_bug": true,
  "bug_line": {line_number if line_number else 1},
  "explanation": "Clear explanation of the bug and why it is dangerous",
  "corrected_code": "The fixed version of the buggy line or function",
  "confidence": 0.85
}}"""

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        return response.json().get("response", "")
    except requests.exceptions.ConnectionError:
        return json.dumps({
            "valid_bug": True,
            "bug_line": line_number or 1,
            "explanation": f"[Ollama offline] Static finding: {static_hint}",
            "corrected_code": line_text,
            "confidence": finding.get("static_confidence", 0.5)
        })
    except Exception as e:
        return json.dumps({
            "valid_bug": False,
            "bug_line": line_number or 1,
            "explanation": f"LLM error: {str(e)}",
            "corrected_code": "",
            "confidence": 0.0
        })


def llm_fallback_analyze(code_context: str) -> str:
    """
    When static analysis finds nothing, ask LLM to do full semantic analysis.
    Returns raw JSON string — list of bugs found.
    """
    prompt = f"""You are an expert embedded systems debugger specializing in RDI C++ API code.

Analyze the following code for ALL bugs, issues, and bad practices including:
- RDI API misuse (wrong method names, wrong parameters, missing calls)
- Memory issues (buffer overflow, null pointer, leaks)
- Timing and concurrency issues
- Hardware/register access issues
- Logic errors

Code:
{code_context}

Respond STRICTLY as a JSON array only — no text outside the array:
[
  {{
    "bug_line": 5,
    "explanation": "Description of the bug",
    "corrected_code": "Fixed version of the line",
    "confidence": 0.9,
    "rule_tag": "category_of_bug"
  }}
]

If no bugs found, return: []"""

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        return response.json().get("response", "[]")
    except requests.exceptions.ConnectionError:
        return "[]"
    except Exception as e:
        print(f"[ValidatorAgent] LLM fallback error: {e}")
        return "[]"


def parse_llm_json(raw: str) -> dict | list | None:
    """Safely parse LLM JSON output, stripping markdown fences."""
    cleaned = re.sub(r'```(?:json)?', '', raw).strip().rstrip('`')
    # Try object first
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # Try array
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None