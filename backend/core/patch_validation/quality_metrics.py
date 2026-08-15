import logging
from backend.core.patch_validation.validation_models import ValidationMetrics

logger = logging.getLogger("backend.patch_validation.quality_metrics")


class QualityMetricsCalculator:
    """Computes multidimensional validation scores and code quality metrics."""

    # Nominal dimension weights. They intentionally sum to 0.8 rather than
    # 1.0: a perfect patch scores 0.8 against a default acceptance threshold
    # of 0.7, leaving headroom for the penalty deductions below.
    _W_BUG_REMOVAL = 0.4
    _W_REGRESSION  = 0.3
    _W_SIMPLICITY  = 0.1
    _NOMINAL_TOTAL = _W_BUG_REMOVAL + _W_REGRESSION + _W_SIMPLICITY

    def compute_metrics(
        self,
        compilation_success: bool,
        syntax_success: bool,
        bug_removed: bool,
        regression_success: bool,
        new_bug_count: int,
        warning_delta: int,
        lines_changed: int,
        patch_size: int,
        duration_ms: float,
        *,
        static_evaluated: bool = True,
        regression_evaluated: bool = True,
    ) -> ValidationMetrics:
        """
        Evaluate metrics and calculate an overall quality score.

        Parameters
        ----------
        static_evaluated : False when static reanalysis was disabled or did
            not run, so ``bug_removed`` carries no information.
        regression_evaluated : False when regression testing was disabled or
            did not run, so ``regression_success`` carries no information.

        A dimension that was never evaluated is *excluded* from the score and
        the remaining weights are rescaled to the same nominal total. Counting
        an unevaluated dimension as a failure (the previous behaviour) capped
        the score below the default 0.7 acceptance threshold, so no candidate
        could ever be accepted with regression testing turned off.

        Returns
        -------
        ValidationMetrics object.
        """
        # Strict rules: compilation and basic syntax are absolute prerequisites
        if not compilation_success or not syntax_success:
            score = 0.0
        else:
            # Patch simplicity: penalise excessively large patches;
            # the ideal patch touches few lines.
            if lines_changed > 50:
                simplicity_raw = 0.2
            elif lines_changed > 20:
                simplicity_raw = 0.5
            elif lines_changed > 10:
                simplicity_raw = 0.8
            else:
                simplicity_raw = 1.0

            # (weight, achieved_fraction) for every dimension that was
            # actually measured.
            dimensions = [(self._W_SIMPLICITY, simplicity_raw)]
            if static_evaluated:
                dimensions.append((self._W_BUG_REMOVAL, 1.0 if bug_removed else 0.0))
            if regression_evaluated:
                dimensions.append((self._W_REGRESSION, 1.0 if regression_success else 0.0))

            available_weight = sum(w for w, _ in dimensions)
            earned = sum(w * v for w, v in dimensions)
            # Rescale so the achievable maximum is always _NOMINAL_TOTAL,
            # whichever dimensions were measured.
            score = earned * (self._NOMINAL_TOTAL / available_weight)

            # Code regression penalties (deductions).
            # New bugs introduced are heavily penalised.
            if new_bug_count > 0:
                score -= min(0.4, 0.15 * new_bug_count)

            # Warning delta penalty
            if warning_delta > 0:
                score -= min(0.1, 0.01 * warning_delta)

            # Ensure final score range [0.0, 1.0]
            score = max(0.0, min(1.0, round(score, 3)))

        bug_removal_rate = 1.0 if bug_removed else 0.0

        logger.info(f"Computed validation score: {score} (compilation={compilation_success}, bug_removed={bug_removed})")

        return ValidationMetrics(
            compilation_success=compilation_success,
            syntax_success=syntax_success,
            bug_removal_rate=bug_removal_rate,
            regression_success=regression_success,
            new_bug_count=new_bug_count,
            warning_delta=warning_delta,
            lines_changed=lines_changed,
            patch_size=patch_size,
            duration_ms=duration_ms,
            score=score
        )
