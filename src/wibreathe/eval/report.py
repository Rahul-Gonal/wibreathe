"""Automated markdown summary and diagnostic report generator."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional, Sequence

from wibreathe.eval.align import AlignedWindow
from wibreathe.eval.metrics import EventMetrics, RespirationMetrics

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None  # type: ignore


def generate_evaluation_report(
    aligned_windows: Sequence[AlignedWindow],
    resp_metrics: RespirationMetrics,
    event_metrics: EventMetrics,
    output_dir: str | Path,
    title: str = "WiBreathe Trial Evaluation Report",
) -> Path:
    """Generate markdown summary table, metrics CSV, and correlation plots."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write Markdown Summary Report
    md_path = out_dir / "summary_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write("> **Notice**: WiBreathe is an engineering research prototype and NOT a medical device. ")
        f.write("No medical diagnoses or clinical guarantees are made.\n\n")

        f.write("## 1. Respiration Rate Accuracy\n\n")
        f.write("| Metric | Value | Units |\n")
        f.write("| :--- | :--- | :--- |\n")
        f.write(f"| **Mean Absolute Error (MAE)** | **{resp_metrics.mae:.2f}** | bpm |\n")
        f.write(f"| Root Mean Square Error (RMSE) | {resp_metrics.rmse:.2f} | bpm |\n")
        f.write(f"| Systematic Bias (Mean Signed Error) | {resp_metrics.bias:.2f} | bpm |\n")
        f.write(f"| Mean Absolute Percentage Error (MAPE) | {resp_metrics.mape:.1f} | % |\n")
        f.write(f"| Within $\\pm 1.0$ bpm | {resp_metrics.accuracy_within_1bpm_pct:.1f} | % |\n")
        f.write(f"| Within $\\pm 2.0$ bpm | {resp_metrics.accuracy_within_2bpm_pct:.1f} | % |\n")
        f.write(f"| Measurement Coverage | {resp_metrics.coverage_pct:.1f} | % |\n")
        f.write(f"| Bland-Altman Limits (95%) | [{resp_metrics.bland_altman_lower:.2f}, {resp_metrics.bland_altman_upper:.2f}] | bpm |\n")
        f.write(f"| Evaluable Windows | {resp_metrics.evaluable_windows} | count |\n")
        f.write(f"| Valid Rate Windows | {resp_metrics.valid_estimates_count} | count |\n\n")

        f.write("## 2. Event Detection & Bed-Exit Performance\n\n")
        f.write("| Metric | Value |\n")
        f.write("| :--- | :--- |\n")
        f.write(f"| **Sensitivity (Recall)** | **{event_metrics.sensitivity_pct:.1f}%** |\n")
        f.write(f"| Specificity | {event_metrics.specificity_pct:.1f}% |\n")
        f.write(f"| False Alarms per Hour | {event_metrics.false_alarms_per_hour:.2f} / hr |\n")
        f.write(f"| Mean Detection Latency | {event_metrics.mean_detection_delay_sec:.1f} s |\n\n")

        f.write("### Confusion Matrix\n\n")
        f.write("| | Ref Event (Exit/Motion) | Ref Normal (Still/Empty) |\n")
        f.write("| :--- | :--- | :--- |\n")
        f.write(f"| **Detected Event** | {event_metrics.true_positives} (TP) | {event_metrics.false_positives} (FP) |\n")
        f.write(f"| **Detected Normal** | {event_metrics.false_negatives} (FN) | {event_metrics.true_negatives} (TN) |\n\n")

    # 2. Write CSV Metrics Table
    csv_path = out_dir / "metrics.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value", "unit"])
        writer.writerow(["mae_bpm", f"{resp_metrics.mae:.3f}", "bpm"])
        writer.writerow(["rmse_bpm", f"{resp_metrics.rmse:.3f}", "bpm"])
        writer.writerow(["bias_bpm", f"{resp_metrics.bias:.3f}", "bpm"])
        writer.writerow(["mape_percent", f"{resp_metrics.mape:.2f}", "%"])
        writer.writerow(["coverage_percent", f"{resp_metrics.coverage_pct:.2f}", "%"])
        writer.writerow(["within_1bpm_percent", f"{resp_metrics.accuracy_within_1bpm_pct:.2f}", "%"])
        writer.writerow(["within_2bpm_percent", f"{resp_metrics.accuracy_within_2bpm_pct:.2f}", "%"])
        writer.writerow(["event_sensitivity", f"{event_metrics.sensitivity_pct:.2f}", "%"])
        writer.writerow(["event_specificity", f"{event_metrics.specificity_pct:.2f}", "%"])
        writer.writerow(["false_alarms_per_hour", f"{event_metrics.false_alarms_per_hour:.2f}", "events/hr"])

    # 3. Render Correlation Plots if Matplotlib available
    if plt is not None:
        try:
            fig, ax = plt.subplots(figsize=(7, 6))
            refs = [w.bpm_reference for w in aligned_windows if w.bpm_reference and w.bpm_estimated]
            ests = [w.bpm_estimated for w in aligned_windows if w.bpm_reference and w.bpm_estimated]
            if refs and ests:
                ax.scatter(refs, ests, color="tab:blue", alpha=0.7, edgecolors="none")
                min_v = min(min(refs), min(ests)) - 2.0
                max_v = max(max(refs), max(ests)) + 2.0
                ax.plot([min_v, max_v], [min_v, max_v], "r--", label="Ideal Agreement (y=x)")
                ax.set_xlabel("Reference Respiration Rate (BPM)")
                ax.set_ylabel("WiBreathe Estimated Rate (BPM)")
                ax.set_title(f"Correlation Plot (MAE = {resp_metrics.mae:.2f} bpm)")
                ax.grid(True, alpha=0.3)
                ax.legend()
                fig.tight_layout()
                fig.savefig(out_dir / "correlation.png", dpi=150)
                plt.close(fig)
        except Exception as e:
            logger.debug("Plot generation error: %s", e)

    logger.info("Evaluation report generated at %s", out_dir)
    return md_path
