

import json
import re

from static_engine import analyze_code
from validator_agent import validate_issue, llm_fallback_analyze, parse_llm_json


def analyze_logic(context: str, code: str) -> tuple[int | None, str]:
    """
    Main entry point for batch_runner.
    
    Args:
        context: Problem context / description string from samples.csv
        code: The C++ source code to analyze
    
    Returns:
        Tuple of (bug_line, explanation)
        bug_line is None if no bug found
    """
    full_code = f"// Context: {context}\n{code}" if context else code

    # ── Step 1: Static Analysis ───────────────────────────────────────────────
    print("  [Orchestrator] Step 1: Running static analysis...")
    static_findings = analyze_code(full_code)
    print(f"  [Orchestrator] Static findings: {len(static_findings)}")

    # ── Step 2: LLM Fallback if no static findings ────────────────────────────
    if not static_findings:
        print("  [Orchestrator] No static findings — triggering LLM fallback...")
        raw = llm_fallback_analyze(full_code)
        parsed = parse_llm_json(raw)

        if isinstance(parsed, list) and parsed:
            best = max(parsed, key=lambda x: float(x.get("confidence", 0)))
            return (
                best.get("bug_line") or best.get("line_number"),
                best.get("explanation", "LLM detected a structural anomaly")
            )
        elif isinstance(parsed, dict):
            return (
                parsed.get("bug_line") or parsed.get("line_number"),
                parsed.get("explanation", "LLM detected a structural anomaly")
            )

        # Static had nothing, LLM had nothing — semantic pass with generic finding
        static_findings = [{
            "line_number": None,
            "line_text": full_code[:200],
            "rule_tag": "semantic_review",
            "static_confidence": 0.4,
            "explanation": "General semantic review — no specific pattern matched"
        }]

    # ── Step 3: Validator Agent — validate each static finding via LLM ────────
    print(f"  [Orchestrator] Step 3: Validating {len(static_findings)} finding(s) with LLM...")

    best_result = None
    best_confidence = 0.0

    for finding in static_findings:
        raw = validate_issue(full_code, finding)
        validation = parse_llm_json(raw)

        if not validation or not isinstance(validation, dict):
            continue

        # Skip if LLM says it's not a real bug and confidence is low
        if not validation.get("valid_bug", True) and float(validation.get("confidence", 0)) < 0.3:
            continue

        # Combined confidence: 60% LLM weight, 40% static weight
        combined = (
            0.6 * float(validation.get("confidence", 0)) +
            0.4 * float(finding.get("static_confidence", 0))
        )

        if combined > best_confidence:
            best_confidence = combined
            # Prefer LLM-provided line number, fall back to static
            bug_line = (
                validation.get("bug_line")
                or validation.get("line_number")
                or finding.get("line_number")
            )
            best_result = {
                "bug_line": bug_line,
                "explanation": validation.get("explanation", finding.get("explanation", "")),
            }

    # ── Step 4: Return best result ────────────────────────────────────────────
    if best_result:
        print(f"  [Orchestrator] Best result: line={best_result['bug_line']}, confidence={best_confidence:.2f}")
        return best_result["bug_line"], best_result["explanation"]

    # Last resort: return highest-confidence static finding directly
    if static_findings:
        top = max(static_findings, key=lambda x: x.get("static_confidence", 0))
        return top.get("line_number"), top.get("explanation", "Static analysis flagged this line")

    return None, "No bugs detected"


def process_code(code: str) -> dict:
    """
    Standalone entry point (non-batch use).
    Returns dict with explanation and corrected_code.
    """
    bug_line, explanation = analyze_logic("", code)
    return {
        "bug_line": bug_line,
        "explanation": explanation,
    }