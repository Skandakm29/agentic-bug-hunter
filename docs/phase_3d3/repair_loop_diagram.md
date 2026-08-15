# Phase 3D.3 — Repair Loop Sequence Diagram

The following Mermaid diagram maps the chronological sequence of events inside the `RepairLoop.execute_loop` orchestrator during a single repair cycle.

```mermaid
sequenceDiagram
    participant Orch as RepairOrchestrator
    participant Loop as RepairLoop
    participant Plan as PlanningEngine
    participant AgentMgr as AgentManager
    participant GenAgent as PatchGeneratorAgent / RefinementEngine
    participant ValEng as ValidationEngine (Phase 3D.2)
    participant Pool as CandidatePool
    participant Scorer as RepairScorer
    participant Feed as FeedbackEngine
    participant Reason as ReasoningEngine
    participant Memory as RepairMemory
    participant Audit as AuditTrail

    Orch->>Loop: execute_loop()
    Loop->>Audit: Log SESSION_START
    
    rect rgb(240, 240, 245)
        Note over Loop: Iterate up to Max Iterations
        Loop->>Plan: decide_strategy(memory, pool)
        Plan-->>Loop: Strategy (GENERATE_NEW / REFINE_EXISTING)
        
        alt GENERATE_NEW
            Loop->>AgentMgr: get_agent(PATCH_GENERATOR)
            AgentMgr-->>Loop: Generator Agent
            Loop->>GenAgent: execute(context)
            GenAgent-->>Loop: StructuredPatch (3D.1)
        else REFINE_EXISTING
            Loop->>GenAgent: refine(best_candidate, feedback)
            GenAgent-->>Loop: StructuredPatch (Refined)
        end
        
        Loop->>ValEng: validate_patch(StructuredPatch)
        ValEng-->>Loop: ValidationReport (3D.2)
        
        Loop->>Pool: add(candidates)
        
        Loop->>Scorer: score(metrics)
        Scorer-->>Loop: CompositeScore
        Loop->>Pool: update_scores(CompositeScore)
        
        Loop->>Feed: extract(report, metrics)
        Feed-->>Loop: StructuredFeedback
        
        Loop->>Reason: reason_about_failure(feedback)
        Reason-->>Loop: AgentDecision (Validator)
        
        Loop->>Memory: add_iteration(RepairIteration)
        Loop->>Audit: Log ITERATION_END
    end
    
    Orch->>AgentMgr: get_agent(REPORT)
    AgentMgr-->>Orch: Report Agent
    Orch->>Orch: build_report()
    Orch-->>Orch: RepairSession
```
