"""
static_engine.py — Static Analysis Engine

SOLID Principles Applied:
  S → StaticRule handles ONE rule
      StaticEngine handles running all rules
  O → Add new rules by creating new StaticRule instances
      No modification to StaticEngine needed
  D → StaticEngine depends on StaticRule abstraction
      not concrete implementations
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Finding:
    """Represents one bug found by a static rule."""
    line_number:       Optional[int]
    line_text:         str
    rule_tag:          str
    description:       str
    static_confidence: float
    source:            str = "static"

    def to_dict(self) -> dict:
        return {
            "line_number": self.line_number,
            "line_text":   self.line_text,
            "rule_tag":    self.rule_tag,
            "description": self.description,
            "confidence":  self.static_confidence,
            "source":      self.source
        }


class StaticRule:
    """
    Base class for a single static analysis rule.
    Open/Closed: extend this to add new rules.
    Each subclass has ONE responsibility — detect one bug type.
    """
    tag:        str = ""
    confidence: float = 0.0

    def detect(self, code: str) -> list[Finding]:
        raise NotImplementedError


class MissingVolatileRule(StaticRule):
    """
    Detects ISR-shared variables declared without volatile.
    Without volatile, compiler may cache value in register,
    making ISR updates invisible to the main loop.
    """
    tag        = "missing_volatile"
    confidence = 0.85
    KEYWORDS   = ["flag", "ready", "done", "busy", "irq", "data_ready", "isr"]

    def detect(self, code: str) -> list[Finding]:
        findings = []
        for i, line in enumerate(code.split("\n")):
            if (re.search(r'\b(bool|uint8_t|int)\b', line)
                    and "=" in line
                    and "volatile" not in line
                    and any(k in line for k in self.KEYWORDS)):
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag=self.tag,
                    description="ISR-shared variable missing 'volatile'",
                    static_confidence=self.confidence
                ))
        return findings


class NullPointerRule(StaticRule):
    """
    Tracks pointer declarations and allocations across lines.
    Flags any pointer dereferenced without prior allocation.
    Uses two sets for state tracking — declared vs allocated.
    """
    tag        = "null_pointer"
    confidence = 0.90

    def detect(self, code: str) -> list[Finding]:
        findings  = []
        declared  = set()
        allocated = set()

        for i, line in enumerate(code.split("\n")):
            for p in re.findall(r'\b\w+\s*\*\s*(\w+)\s*;', line):
                declared.add(p)

            if any(k in line for k in ["malloc", "new ", "calloc"]):
                allocated.update(declared)

            for p in re.findall(r'\*(\w+)\s*=', line):
                if p in declared and p not in allocated:
                    findings.append(Finding(
                        line_number=i + 1,
                        line_text=line.strip(),
                        rule_tag=self.tag,
                        description=f"Pointer '{p}' dereferenced without allocation",
                        static_confidence=self.confidence
                    ))
        return findings


class OverflowRiskRule(StaticRule):
    """
    Detects uint8_t accumulators that will silently overflow at 255.
    Also detects signed int used for always-positive sensor values.
    One class — two related checks on the same theme (type safety).
    """
    tag        = "overflow_risk"
    confidence = 0.75
    ACCUMULATORS = ["total", "sum", "avg", "count", "acc"]

    def detect(self, code: str) -> list[Finding]:
        findings = []
        for i, line in enumerate(code.split("\n")):
            if "uint8_t" in line and any(k in line for k in self.ACCUMULATORS):
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag="overflow_risk",
                    description="uint8_t accumulator will overflow for values > 255",
                    static_confidence=self.confidence
                ))
            if re.search(r'\bint\b', line) and "sensor" in line.lower():
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag="type_mismatch",
                    description="Signed int for sensor value — consider uint16_t/uint32_t",
                    static_confidence=0.60
                ))
        return findings


class BlockingInISRRule(StaticRule):
    """
    Detects blocking calls inside ISR handlers.
    ISRs must execute and return as fast as possible.
    Tracks ISR context with a flag across lines.
    """
    tag        = "blocking_in_isr"
    confidence = 0.85
    ISR_MARKERS   = r'\bISR\b|\bIRQ\b|interrupt|IRAM_ATTR|__irq'
    BLOCK_MARKERS = r'delay|HAL_Delay|sleep|busy_wait|while\s*\('

    def detect(self, code: str) -> list[Finding]:
        findings = []
        in_isr   = False
        for i, line in enumerate(code.split("\n")):
            if re.search(self.ISR_MARKERS, line, re.IGNORECASE):
                in_isr = True
            if in_isr and re.search(self.BLOCK_MARKERS, line):
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag=self.tag,
                    description="Blocking call inside interrupt context",
                    static_confidence=self.confidence
                ))
            if "}" in line:
                in_isr = False
        return findings


class BitManipulationRule(StaticRule):
    """
    Detects '& mask' where '& ~mask' is needed to clear a bit.
    Using & mask keeps only that bit — opposite of intended effect.
    """
    tag        = "bit_clear_error"
    confidence = 0.80
    CLEAR_WORDS = ["clear", "disable", "off"]

    def detect(self, code: str) -> list[Finding]:
        findings = []
        for i, line in enumerate(code.split("\n")):
            if (re.search(r'&\s*\d+\b', line)
                    and "~" not in line
                    and any(k in line.lower() for k in self.CLEAR_WORDS)):
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag=self.tag,
                    description="Use '& ~mask' not '& mask' to clear a bit",
                    static_confidence=self.confidence
                ))
        return findings


class RDIBlockMismatchRule(StaticRule):
    """
    Counts RDI_BEGIN and RDI_END occurrences.
    Every RDI_BEGIN must have exactly one matching RDI_END.
    """
    tag        = "rdi_block_mismatch"
    confidence = 0.90

    def detect(self, code: str) -> list[Finding]:
        begins = code.count("RDI_BEGIN")
        ends   = code.count("RDI_END")
        if begins != ends:
            return [Finding(
                line_number=None,
                line_text="RDI_BEGIN/RDI_END mismatch",
                rule_tag=self.tag,
                description=f"Found {begins} RDI_BEGIN but {ends} RDI_END",
                static_confidence=self.confidence
            )]
        return []


class IncompleteChainRule(StaticRule):
    """
    Detects rdi. method chains not terminated with a semicolon.
    """
    tag        = "incomplete_chain"
    confidence = 0.70

    def detect(self, code: str) -> list[Finding]:
        findings = []
        for i, line in enumerate(code.split("\n")):
            if "rdi." in line and not line.strip().endswith((";", "}", "{")):
                findings.append(Finding(
                    line_number=i + 1,
                    line_text=line.strip(),
                    rule_tag=self.tag,
                    description="Incomplete method chain — missing terminator",
                    static_confidence=self.confidence
                ))
        return findings


class SuspiciousMethodRule(StaticRule):
    """
    Detects method calls not in the known RDI API prefix list.
    """
    tag        = "suspicious_method_name"
    confidence = 0.80
    KNOWN_PREFIXES = [
        "get", "set", "read", "write", "pin", "label", "burst",
        "execute", "vForce", "iForce", "vMeas", "iMeas",
        "samples", "begin", "end", "wait"
    ]

    def detect(self, code: str) -> list[Finding]:
        findings = []
        for i, line in enumerate(code.split("\n")):
            for method in re.findall(r'\.\s*(\w+)\s*\(', line):
                if not any(method.startswith(p) for p in self.KNOWN_PREFIXES):
                    findings.append(Finding(
                        line_number=i + 1,
                        line_text=line.strip(),
                        rule_tag=self.tag,
                        description=f"Unknown RDI method: '{method}'",
                        static_confidence=self.confidence
                    ))
        return findings


class StaticEngine:
    """
    Runs all static rules and aggregates findings.

    SOLID:
      S → only responsibility is running rules and collecting results
      O → add new rules by passing them in — no code change here
      D → depends on StaticRule abstraction, not concrete classes
    """

    def __init__(self):
        # All rules registered here
        # To add a new rule: create a StaticRule subclass,
        # add one line here. Nothing else changes.
        self.rules: list[StaticRule] = [
            MissingVolatileRule(),
            NullPointerRule(),
            OverflowRiskRule(),
            BlockingInISRRule(),
            BitManipulationRule(),
            RDIBlockMismatchRule(),
            IncompleteChainRule(),
            SuspiciousMethodRule(),
        ]

    def analyze(self, code: str) -> list[Finding]:
        """Run all rules and return combined findings."""
        findings = []
        for rule in self.rules:
            findings.extend(rule.detect(code))
        return findings

    def get_rules_metadata(self) -> list[dict]:
        """Return metadata about all registered rules."""
        return [
            {
                "rule":       rule.tag,
                "confidence": rule.confidence,
                "description": rule.__doc__.strip().split("\n")[0]
                               if rule.__doc__ else ""
            }
            for rule in self.rules
        ]
