"""
static_engine.py — Static rule-based analysis engine.
Detects bugs in RDI C++ API code using pattern matching.
Returns structured findings for orchestrator + validator pipeline.
"""

import re


# ── RDI-specific known method prefixes ───────────────────────────────────────
KNOWN_PREFIXES = [
    "get", "set", "read", "write",
    "pin", "label", "burst", "execute",
    "vForce", "iForce", "vMeas", "iMeas",
    "samples", "begin", "end", "wait",
    "connect", "disconnect", "reset", "init",
    "enable", "disable", "configure", "trigger",
]


def detect_unknown_methods(code: str) -> list[dict]:
    """Flag method calls not matching any known RDI prefix."""
    findings = []
    lines = code.split("\n")
    for i, line in enumerate(lines):
        matches = re.findall(r'\.\s*(\w+)\s*\(', line)
        for method in matches:
            if not any(method.startswith(prefix) for prefix in KNOWN_PREFIXES):
                findings.append({
                    "line_number": i + 1,
                    "line_text": line.strip(),
                    "rule_tag": "suspicious_method_name",
                    "static_confidence": 0.8,
                    "explanation": f"Unknown RDI method '{method}' — not in known API prefix list"
                })
    return findings


def detect_unmatched_rdi_blocks(code: str) -> list[dict]:
    """Detect mismatched RDI_BEGIN / RDI_END blocks."""
    findings = []
    begin_count = code.count("RDI_BEGIN")
    end_count = code.count("RDI_END")
    if begin_count != end_count:
        findings.append({
            "line_number": None,
            "line_text": f"RDI_BEGIN={begin_count}, RDI_END={end_count}",
            "rule_tag": "rdi_block_mismatch",
            "static_confidence": 0.95,
            "explanation": f"Mismatched RDI blocks: {begin_count} BEGIN vs {end_count} END — missing terminator"
        })
    return findings


def detect_incomplete_chaining(code: str) -> list[dict]:
    """Flag incomplete method chains (line ends mid-chain without semicolon)."""
    findings = []
    lines = code.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "rdi." in stripped and not stripped.endswith((";", "}", "{", ",")):
            findings.append({
                "line_number": i + 1,
                "line_text": stripped,
                "rule_tag": "incomplete_chain",
                "static_confidence": 0.7,
                "explanation": "Incomplete method chain — line may be missing semicolon or continuation"
            })
    return findings


def detect_null_pointer_risk(code: str) -> list[dict]:
    """Detect malloc/new without null check."""
    findings = []
    lines = code.split("\n")
    for i, line in enumerate(lines):
        if re.search(r'\b(malloc|calloc|new)\b', line):
            # Check if next 2 lines contain a null check
            context = "\n".join(lines[i:min(i+3, len(lines))])
            if not re.search(r'(if\s*\(|nullptr|NULL|!.*ptr)', context):
                findings.append({
                    "line_number": i + 1,
                    "line_text": line.strip(),
                    "rule_tag": "null_pointer_risk",
                    "static_confidence": 0.75,
                    "explanation": "Memory allocation without null pointer check — dereference may crash"
                })
    return findings


def detect_buffer_issues(code: str) -> list[dict]:
    """Detect strcpy, sprintf, gets — unsafe string functions."""
    findings = []
    lines = code.split("\n")
    unsafe = {
        "strcpy": "Use strncpy() or strlcpy() — strcpy has no bounds check",
        "sprintf": "Use snprintf() — sprintf can overflow destination buffer",
        "gets": "gets() is banned — use fgets() with size limit",
        "strcat": "Use strncat() — strcat has no bounds check",
    }
    for i, line in enumerate(lines):
        for fn, msg in unsafe.items():
            if re.search(rf'\b{fn}\s*\(', line):
                findings.append({
                    "line_number": i + 1,
                    "line_text": line.strip(),
                    "rule_tag": f"unsafe_function_{fn}",
                    "static_confidence": 0.9,
                    "explanation": msg
                })
    return findings


def detect_missing_volatile(code: str) -> list[dict]:
    """Flag hardware register pointers missing volatile keyword."""
    findings = []
    lines = code.split("\n")
    for i, line in enumerate(lines):
        # Pointer cast to hardware address (0x4xxxxxxx typical for MCU peripherals)
        if re.search(r'\(uint\d+_t\s*\*\)\s*0x[4-9A-Fa-f][0-9A-Fa-f]{6,}', line):
            if "volatile" not in line:
                findings.append({
                    "line_number": i + 1,
                    "line_text": line.strip(),
                    "rule_tag": "missing_volatile",
                    "static_confidence": 0.85,
                    "explanation": "Hardware register pointer missing 'volatile' — optimizer may eliminate reads/writes"
                })
    return findings


def detect_blocking_in_isr(code: str) -> list[dict]:
    """Detect blocking calls inside ISR/interrupt handlers."""
    findings = []
    lines = code.split("\n")
    in_isr = False
    brace_depth = 0
    isr_start = 0

    blocking_calls = ["vTaskDelay", "delay(", "sleep(", "printf(", "HAL_Delay"]

    for i, line in enumerate(lines):
        if re.search(r'(IRQHandler|IRAM_ATTR|__interrupt|ISR\s*\()', line):
            in_isr = True
            brace_depth = 0
            isr_start = i + 1

        if in_isr:
            brace_depth += line.count("{") - line.count("}")
            for call in blocking_calls:
                if call in line:
                    findings.append({
                        "line_number": i + 1,
                        "line_text": line.strip(),
                        "rule_tag": "blocking_call_in_isr",
                        "static_confidence": 0.95,
                        "explanation": f"Blocking call '{call}' inside ISR — causes system freeze. Use FromISR variants."
                    })
            if brace_depth <= 0 and i > isr_start:
                in_isr = False

    return findings


def analyze_code(code: str) -> list[dict]:
    """
    Run all static analysis rules on the provided code.
    Returns list of finding dicts with line_number, explanation, rule_tag, static_confidence.
    """
    findings = []
    findings.extend(detect_unknown_methods(code))
    findings.extend(detect_unmatched_rdi_blocks(code))
    findings.extend(detect_incomplete_chaining(code))
    findings.extend(detect_null_pointer_risk(code))
    findings.extend(detect_buffer_issues(code))
    findings.extend(detect_missing_volatile(code))
    findings.extend(detect_blocking_in_isr(code))
    return findings