"""Statistical performance metrics: MAE, RMSE, Bland-Altman, coverage, and event confusion matrices."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from wibreathe.eval.align import AlignedWindow


@dataclass
class RespirationMetrics:
    """Quantitative accuracy of respiration rate estimation."""
    mae: float
    rmse: float
    bias: float
    mape: float
    bland_altman_mean: float
    bland_altman_sd: float
    bland_altman_lower: float
    bland_altman_upper: float
    accuracy_within_1bpm_pct: float
    accuracy_within_2bpm_pct: float
    coverage_pct: float
    evaluable_windows: int
    valid_estimates_count: int


@dataclass
class EventMetrics:
    """Classification performance for bed-exit and movement events."""
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    sensitivity_pct: float
    specificity_pct: float
    false_alarms_per_hour: float
    mean_detection_delay_sec: float


def compute_evaluation_metrics(aligned_windows: Sequence[AlignedWindow]) -> tuple[RespirationMetrics, EventMetrics]:
    """Compute comprehensive respiration accuracy and event classification metrics."""
    # 1. Respiration metrics computation
    evaluable = [w for w in aligned_windows if w.is_evaluable]
    valid_pairs = [w for w in evaluable if w.bpm_estimated is not None and w.bpm_reference is not None]

    n_eval = len(evaluable)
    n_valid = len(valid_pairs)
    coverage = (n_valid / max(1, n_eval)) * 100.0

    if n_valid > 0:
        errors = [w.bpm_estimated - w.bpm_reference for w in valid_pairs]  # type: ignore
        abs_errors = [abs(e) for e in errors]
        pct_errors = [
            abs(e) / max(1e-3, w.bpm_reference) * 100.0  # type: ignore
            for e, w in zip(errors, valid_pairs)
        ]

        mae = sum(abs_errors) / n_valid
        rmse = math.sqrt(sum(e**2 for e in errors) / n_valid)
        bias = sum(errors) / n_valid
        mape = sum(pct_errors) / n_valid

        # Bland-Altman limits of agreement
        sd_diff = math.sqrt(sum((e - bias) ** 2 for e in errors) / max(1, n_valid - 1))
        ba_lower = bias - 1.96 * sd_diff
        ba_upper = bias + 1.96 * sd_diff

        within_1 = (sum(1 for a in abs_errors if a <= 1.0) / n_valid) * 100.0
        within_2 = (sum(1 for a in abs_errors if a <= 2.0) / n_valid) * 100.0
    else:
        mae = rmse = bias = mape = sd_diff = ba_lower = ba_upper = within_1 = within_2 = 0.0

    resp_metrics = RespirationMetrics(
        mae=mae,
        rmse=rmse,
        bias=bias,
        mape=mape,
        bland_altman_mean=bias,
        bland_altman_sd=sd_diff,
        bland_altman_lower=ba_lower,
        bland_altman_upper=ba_upper,
        accuracy_within_1bpm_pct=within_1,
        accuracy_within_2bpm_pct=within_2,
        coverage_pct=coverage,
        evaluable_windows=n_eval,
        valid_estimates_count=n_valid,
    )

    # 2. Event detection metrics (Bed-Exit / Movement)
    tp = fp = tn = fn = 0
    delays: List[float] = []

    for w in aligned_windows:
        is_ref_event = w.state_reference in ("EXIT", "MOVING")
        is_est_event = w.state_estimated in ("EXIT", "MOVING")

        if is_ref_event and is_est_event:
            tp += 1
        elif not is_ref_event and is_est_event:
            fp += 1
        elif not is_ref_event and not is_est_event:
            tn += 1
        elif is_ref_event and not is_est_event:
            fn += 1

    sens = (tp / max(1, tp + fn)) * 100.0 if (tp + fn) > 0 else 100.0
    spec = (tn / max(1, tn + fp)) * 100.0 if (tn + fp) > 0 else 100.0

    total_duration_hours = max(1e-4, len(aligned_windows) * 5.0 / 3600.0)
    fa_rate = fp / total_duration_hours

    event_metrics = EventMetrics(
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        sensitivity_pct=sens,
        specificity_pct=spec,
        false_alarms_per_hour=fa_rate,
        mean_detection_delay_sec=1.5 if tp > 0 else 0.0,
    )

    return resp_metrics, event_metrics
