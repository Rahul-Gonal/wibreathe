"""Evaluation, reference data collection, and clinical/benchmarking metrics."""

from wibreathe.eval.reference_tap import ReferenceTapLogger
from wibreathe.eval.align import align_reference_and_estimates
from wibreathe.eval.metrics import RespirationMetrics, EventMetrics, compute_evaluation_metrics
from wibreathe.eval.report import generate_evaluation_report

__all__ = [
    "ReferenceTapLogger",
    "align_reference_and_estimates",
    "RespirationMetrics",
    "EventMetrics",
    "compute_evaluation_metrics",
    "generate_evaluation_report",
]
