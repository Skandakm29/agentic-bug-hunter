"""
Autonomous Repair Loop — Candidate Pool  (Phase 3D.3)
======================================================
``CandidatePool`` is a thread-safe, append-only store that tracks every
patch candidate produced across all repair iterations in a session.

Design invariants
-----------------
- Entries are NEVER deleted — the pool grows monotonically.
- Every entry records its parent (for refinement lineage tracing).
- Scoring information is updated in-place when validation completes.
- ``export()`` produces a JSON-serializable snapshot suitable for the
  audit trail and engineering report.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Dict, List, Optional

from backend.core.autonomous_repair.repair_models import (
    CandidateEntry,
    CompositeScore,
    RepairStrategy,
    RefinementStrategy,
)

logger = logging.getLogger("backend.autonomous_repair.candidate_pool")


class CandidatePool:
    """
    Thread-safe candidate store maintaining complete provenance across
    all repair iterations.

    All mutating operations are protected by a reentrant lock so the pool
    can be safely accessed from concurrent agent callbacks.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._entries: Dict[str, CandidateEntry] = {}   # entry_id → entry
        self._ordered_ids: List[str] = []               # insertion order

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add(
        self,
        candidate_id: str,
        patch_id: str,
        iteration: int,
        candidate_snapshot: Dict,
        parent_entry_id: Optional[str] = None,
        strategy_used: Optional[RepairStrategy] = None,
        refinement_strategy: Optional[RefinementStrategy] = None,
    ) -> CandidateEntry:
        """
        Record a new candidate in the pool.

        Parameters
        ----------
        candidate_id : PatchCandidate.candidate_id from 3D.1.
        patch_id : StructuredPatch.patch_id.
        iteration : Loop iteration index.
        candidate_snapshot : JSON-serializable dict of the candidate.
        parent_entry_id : entry_id of the parent CandidateEntry (refinement).
        strategy_used : RepairStrategy that produced this candidate.
        refinement_strategy : Specific RefinementStrategy if applicable.

        Returns
        -------
        The newly created CandidateEntry.
        """
        entry = CandidateEntry(
            candidate_id=candidate_id,
            patch_id=patch_id,
            iteration=iteration,
            parent_entry_id=parent_entry_id,
            strategy_used=strategy_used,
            refinement_strategy=refinement_strategy,
            candidate_snapshot=candidate_snapshot,
        )
        with self._lock:
            self._entries[entry.entry_id] = entry
            self._ordered_ids.append(entry.entry_id)
        logger.debug(
            "CandidatePool: added entry %s (candidate=%s, iteration=%d)",
            entry.entry_id,
            candidate_id,
            iteration,
        )
        return entry

    def update_scores(
        self,
        entry_id: str,
        validation_score: float,
        composite_score: Optional[CompositeScore] = None,
    ) -> None:
        """Update validation and composite scores for an existing entry."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                logger.warning("CandidatePool.update_scores: entry_id %s not found.", entry_id)
                return
            entry.validation_score = validation_score
            if composite_score is not None:
                entry.composite_score = composite_score
        logger.debug("CandidatePool: updated scores for entry %s → %.3f", entry_id, validation_score)

    def mark_accepted(self, entry_id: str) -> None:
        """Mark a candidate entry as accepted (winner)."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry:
                entry.accepted = True
                entry.rejected = False
        logger.info("CandidatePool: entry %s marked ACCEPTED.", entry_id)

    def mark_rejected(self, entry_id: str, reasons: List[str]) -> None:
        """Mark a candidate entry as rejected with reasons."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry:
                entry.rejected = True
                entry.rejection_reasons = reasons
        logger.debug("CandidatePool: entry %s marked REJECTED.", entry_id)

    def attach_feedback(self, entry_id: str, feedback_dict: Dict) -> None:
        """Attach a feedback snapshot to a candidate entry."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry:
                entry.feedback_snapshot = feedback_dict

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, entry_id: str) -> Optional[CandidateEntry]:
        """Retrieve a specific entry by ID."""
        with self._lock:
            return self._entries.get(entry_id)

    def get_all(self) -> List[CandidateEntry]:
        """Return all entries in insertion order."""
        with self._lock:
            return [self._entries[eid] for eid in self._ordered_ids]

    def get_by_iteration(self, iteration: int) -> List[CandidateEntry]:
        """Return all entries produced in a specific iteration."""
        with self._lock:
            return [
                self._entries[eid]
                for eid in self._ordered_ids
                if self._entries[eid].iteration == iteration
            ]

    def get_best(self) -> Optional[CandidateEntry]:
        """
        Return the entry with the highest composite score.
        Prefers accepted entries; falls back to highest-score non-rejected entry.
        """
        with self._lock:
            entries = list(self._entries.values())

        if not entries:
            return None

        # Prefer accepted
        accepted = [e for e in entries if e.accepted]
        if accepted:
            return max(accepted, key=lambda e: e.composite_score.total if e.composite_score else e.validation_score)

        # Fall back to best non-rejected
        candidates = [e for e in entries if not e.rejected]
        if not candidates:
            candidates = entries  # all rejected — return global best anyway

        return max(
            candidates,
            key=lambda e: e.composite_score.total if e.composite_score else e.validation_score,
        )

    def get_best_per_iteration(self) -> Dict[int, CandidateEntry]:
        """Return a mapping of iteration → best CandidateEntry for that iteration."""
        result: Dict[int, CandidateEntry] = {}
        with self._lock:
            for entry in self._entries.values():
                it = entry.iteration
                current_best = result.get(it)
                my_score = entry.composite_score.total if entry.composite_score else entry.validation_score
                if current_best is None:
                    result[it] = entry
                else:
                    best_score = (
                        current_best.composite_score.total
                        if current_best.composite_score
                        else current_best.validation_score
                    )
                    if my_score > best_score:
                        result[it] = entry
        return result

    def lineage_of(self, entry_id: str) -> List[CandidateEntry]:
        """
        Return the ancestor chain of a candidate entry in oldest-first order.

        Example: [root_entry, ..., parent_entry, entry]
        """
        chain: List[CandidateEntry] = []
        current_id: Optional[str] = entry_id
        visited: set = set()

        with self._lock:
            while current_id and current_id not in visited:
                visited.add(current_id)
                entry = self._entries.get(current_id)
                if entry is None:
                    break
                chain.append(entry)
                current_id = entry.parent_entry_id

        chain.reverse()
        return chain

    @property
    def size(self) -> int:
        """Total number of entries in the pool."""
        with self._lock:
            return len(self._entries)

    @property
    def accepted_count(self) -> int:
        """Number of accepted entries."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.accepted)

    @property
    def rejected_count(self) -> int:
        """Number of rejected entries."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.rejected)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def export(self) -> str:
        """Export the entire pool as a JSON string snapshot."""
        with self._lock:
            data = {
                "total": len(self._entries),
                "accepted": self.accepted_count,
                "rejected": self.rejected_count,
                "entries": [
                    self._entries[eid].model_dump()
                    for eid in self._ordered_ids
                ],
            }
        return json.dumps(data, default=str)

    def score_progression(self) -> List[float]:
        """
        Return the best validation score achieved per iteration,
        in ascending iteration order.
        """
        per_iter = self.get_best_per_iteration()
        if not per_iter:
            return []
        max_iter = max(per_iter.keys())
        progression: List[float] = []
        for i in range(max_iter + 1):
            entry = per_iter.get(i)
            if entry:
                score = (
                    entry.composite_score.total
                    if entry.composite_score
                    else entry.validation_score
                )
                progression.append(score)
            else:
                # Gap — carry forward previous best
                progression.append(progression[-1] if progression else 0.0)
        return progression
