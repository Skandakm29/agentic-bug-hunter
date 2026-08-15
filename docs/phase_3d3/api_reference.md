# Phase 3D.3 — API Reference

## public interface `RepairOrchestrator`

```python
class RepairOrchestrator:
    def __init__(self, provider: BaseLLMProvider, config: Optional[RepairConfiguration] = None):
        """
        Creates the top-level loop coordinator.
        """

    async def run(
        self,
        finding: Any,
        code: str,
        root_cause: Optional[Any] = None,
        evidence: Optional[Any] = None,
        original_code_path: str = "",
        bug_id: Optional[str] = None
    ) -> RepairSession:
        """
        Executes the autonomous repair loop on the target finding.
        Returns a RepairSession containing logs, reports, and accepted candidates.
        """
```

---

## public interface `RepairSession`

```python
class RepairSession(BaseModel):
    session_id: str
    bug_id: str
    rule_id: str
    accepted: bool
    termination_reason: TerminationReason
    best_candidate_entry_id: Optional[str]
    best_composite_score: float
    iterations: List[RepairIteration]
    report: Optional[RepairReport]
    audit_trail_jsonl: Optional[str]
    metrics_snapshot: Dict[str, Any]
    started_at: float
    completed_at: Optional[float]
    total_duration_ms: float
```

---

## public interface `RepairReport`

```python
class RepairReport(BaseModel):
    report_id: str
    session_id: str
    bug_id: str
    rule_id: str
    accepted: bool
    termination_reason: TerminationReason
    total_iterations: int
    best_score: float
    accepted_candidate_id: Optional[str]
    executive_summary: str
    why_candidate_won: str
    iteration_summaries: List[str]
    agent_decision_log: List[str]
    score_progression: List[float]
    strategies_tried: List[str]
    candidate_count: int
    accepted_count: int
    rejected_count: int
    lineage_summary: str
    total_duration_ms: float
```
