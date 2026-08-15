import logging
from typing import Dict, List
from backend.core.patch_validation.validation_models import CandidateRanking, ValidationMetrics

logger = logging.getLogger("backend.patch_validation.candidate_ranker")


class CandidateRanker:
    """Ranks and selects the optimal repair candidate based on validation metrics."""

    def rank_candidates(
        self,
        candidate_metrics: Dict[str, ValidationMetrics],
        min_score: float = 0.7
    ) -> List[CandidateRanking]:
        """
        Rank all candidates and return a sorted list of CandidateRanking.

        Parameters
        ----------
        candidate_metrics : Dict of candidate_id -> ValidationMetrics.
        min_score : Minimum overall score to qualify as a winner.
        """
        if not candidate_metrics:
            return []

        # Convert dictionary to sortable list
        candidates_list = []
        for cid, metrics in candidate_metrics.items():
            candidates_list.append((cid, metrics))

        # Sort criteria:
        # 1. Higher validation score first
        # 2. Compilation success first
        # 3. Regression success first
        # 4. Fewer new bugs
        # 5. Fewer warning count changes
        # 6. Shorter line edits (simplicity)
        def sort_key(item):
            cid, m = item
            return (
                m.score,
                1 if m.compilation_success else 0,
                1 if m.regression_success else 0,
                -m.new_bug_count,
                -m.warning_delta,
                -m.lines_changed
            )

        candidates_list.sort(key=sort_key, reverse=True)

        rankings = []
        has_winner = False

        for idx, (cid, m) in enumerate(candidates_list):
            rank = idx + 1
            reasons = []

            # Determine eligibility
            eligible = m.compilation_success and m.syntax_success and m.score >= min_score

            is_winner = False
            if eligible and not has_winner:
                is_winner = True
                has_winner = True
                reasons.append("Highest score candidate passing compilation and regression checks.")
            elif not m.compilation_success:
                reasons.append("Disqualified: compilation failed.")
            elif not m.syntax_success:
                reasons.append("Disqualified: syntax check failed.")
            elif m.score < min_score:
                reasons.append(f"Disqualified: score {m.score} is below minimum threshold of {min_score}.")
            else:
                reasons.append("Valid candidate, but ranked lower than the selected winner.")

            if m.regression_success:
                reasons.append("Passed all regression tests.")
            else:
                reasons.append("Failed one or more regression tests.")

            if m.new_bug_count > 0:
                reasons.append(f"Warning: Introduced {m.new_bug_count} new bug(s).")
            if m.warning_delta > 0:
                reasons.append(f"Warning: Increased compiler warnings count by {m.warning_delta}.")

            rankings.append(CandidateRanking(
                candidate_id=cid,
                score=m.score,
                rank=rank,
                winner=is_winner,
                reasons=reasons
            ))

        logger.info(f"Candidates ranked. Winner identified: {rankings[0].candidate_id if rankings and rankings[0].winner else 'None'}")
        return rankings
