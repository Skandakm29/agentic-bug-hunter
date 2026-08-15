import time
import logging
from typing import Optional, Dict, List
from backend.core.patch_generation.patch_models import StructuredPatch
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.patch_validation.validation_models import ValidationReport, ValidationMetrics, CandidateRanking, Diagnostics
from backend.core.patch_validation.workspace_manager import WorkspaceManager
from backend.core.patch_validation.validator import CandidateValidator
from backend.core.patch_validation.candidate_ranker import CandidateRanker

logger = logging.getLogger("backend.patch_validation.validation_engine")


class ValidationEngine:
    """Orchestrates patch validation, rankings, metrics collection, and report generation."""

    def __init__(self, config: Optional[PatchValidationConfig] = None) -> None:
        self.config = config or PatchValidationConfig()
        self.validator = CandidateValidator(self.config)
        self.ranker = CandidateRanker()

    async def validate_patch(
        self,
        patch: StructuredPatch,
        original_code_path: str,
        original_finding: Optional[dict] = None
    ) -> ValidationReport:
        """
        Validate multiple candidates in a StructuredPatch and rank them.

        Parameters
        ----------
        patch : StructuredPatch model containing candidates.
        original_code_path : Path to original repository source.
        original_finding : Optional original finding dict for comparison.
        """
        t_start = time.perf_counter()
        logger.info(f"Starting validation for patch {patch.patch_id} (bug_id={patch.bug_id})")

        all_metrics: Dict[str, ValidationMetrics] = {}
        all_rankings: List[CandidateRanking] = []
        timing_records: Dict[str, float] = {}

        # Merge diagnostics collectors across all candidates
        final_errors: List[str] = []
        final_warnings: List[str] = []
        affected_files: List[str] = []
        suggested_actions: List[str] = []

        # Find first file patch (typically argus validates single-file/single-finding repairs)
        if not patch.file_patches:
            logger.warning("No file patches found to validate.")
            return ValidationReport(
                patch_id=patch.patch_id,
                bug_id=patch.bug_id,
                accepted=False
            )

        file_patch = patch.file_patches[0]
        file_path = file_patch.file_path
        affected_files.append(file_path)

        # Standardize finding dictionary for static reanalysis comparison
        if not original_finding:
            # line_text lets StaticValidator identify the exact flagged
            # statement instead of guessing by line proximity. The first
            # candidate's original_code is the block the patch replaces, so
            # its first non-empty line is the flagged statement.
            flagged_text = ""
            if file_patch.candidates:
                for raw_line in file_patch.candidates[0].original_code.splitlines():
                    if raw_line.strip():
                        flagged_text = raw_line.strip()
                        break

            original_finding = {
                "rule_id": patch.rule_id,
                "file_path": file_path,
                "line_number": file_patch.start_line or 1,
                "line_text": flagged_text,
                "severity": file_patch.severity,
                "static_confidence": file_patch.confidence,
                "description": patch.root_cause or "Repair patch verification",
                "evidence": patch.evidence_summary or "",
                "remediation": ""
            }

        # Run validation sequentially for each candidate
        for idx, candidate in enumerate(file_patch.candidates):
            logger.info(f"Validating candidate {candidate.candidate_id} (index {idx})")
            
            # Isolated execution inside WorkspaceManager context
            try:
                with WorkspaceManager(original_code_path, self.config.workspace_type) as ws:
                    metrics, collector = await self.validator.validate_candidate(
                        workspace=ws,
                        file_path=file_path,
                        candidate=candidate,
                        original_finding=original_finding
                    )
                
                # Store candidate metrics
                all_metrics[candidate.candidate_id] = metrics
                
                # Consolidate diagnostics
                diag = collector.to_diagnostics()
                for err in diag.errors:
                    final_errors.append(f"Candidate {candidate.candidate_id}: {err}")
                for wrn in diag.warnings:
                    final_warnings.append(f"Candidate {candidate.candidate_id}: {wrn}")
                for step, dur in diag.timing.items():
                    timing_records[f"cand_{candidate.candidate_id}_{step}"] = dur
                for action in diag.suggested_actions:
                    if action not in suggested_actions:
                        suggested_actions.append(action)

            except Exception as e:
                logger.error(f"Failed to validate candidate {candidate.candidate_id}: {e}")
                final_errors.append(f"Candidate {candidate.candidate_id} validation execution crash: {e}")

        # Rank all candidates based on compiled metrics
        all_rankings = self.ranker.rank_candidates(all_metrics, self.config.min_acceptance_score)

        winner_id = None
        accepted = False

        # If a valid candidate was selected as winner, update patch index
        for rank in all_rankings:
            if rank.winner:
                winner_id = rank.candidate_id
                accepted = True
                break

        if winner_id:
            # Map candidate_id back to its original list index in the file_patch
            for idx, candidate in enumerate(file_patch.candidates):
                if candidate.candidate_id == winner_id:
                    file_patch.best_candidate_index = idx
                    logger.info(f"Selected winner candidate {winner_id} at index {idx}")
                    break
        else:
            logger.warning("No candidate met the minimum quality scores to be accepted.")

        timing_records["total_validation_duration"] = round((time.perf_counter() - t_start) * 1000, 2)

        diagnostics_report = Diagnostics(
            errors=final_errors,
            warnings=final_warnings,
            timing=timing_records,
            affected_files=affected_files,
            suggested_actions=suggested_actions
        )

        return ValidationReport(
            patch_id=patch.patch_id,
            bug_id=patch.bug_id,
            winner_candidate_id=winner_id,
            rankings=all_rankings,
            diagnostics=diagnostics_report,
            metrics=all_metrics,
            accepted=accepted
        )
