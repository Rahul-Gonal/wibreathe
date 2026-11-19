"""Temporal alignment of estimator sliding windows with observer ground truth."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AlignedWindow:
    center_time_s: float
    bpm_estimated: Optional[float]
    bpm_reference: Optional[float]
    state_estimated: str
    state_reference: str
    confidence: float
    is_evaluable: bool  # True if both reference and estimate are expected to have readings


def align_reference_and_estimates(
    estimator_windows: Sequence[Tuple[float, Optional[float], float, str]],
    reference_csv_path: str | Path,
    window_length_sec: float = 30.0,
) -> List[AlignedWindow]:
    """Align sliding-window respiration estimates with reference breath events."""
    ref_path = Path(reference_csv_path)
    if not ref_path.is_file():
        raise FileNotFoundError(f"Reference file not found: {ref_path}")

    # Read reference events or labels
    breath_times: List[float] = []
    event_intervals: List[Tuple[float, float, str, Optional[float]]] = []

    with open(ref_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return []

        # Check format: synthetic label vs tap log
        if "target_bpm" in header:
            # Synthetic labels CSV: start_time_s, end_time_s, state, target_bpm
            for row in reader:
                if len(row) < 4:
                    continue
                t0 = float(row[0])
                t1 = float(row[1])
                st = row[2]
                bpm_val = float(row[3]) if row[3] != "--" else None
                event_intervals.append((t0, t1, st, bpm_val))
        else:
            # Tap log: host_mono_time, host_wall_time, event_type, notes
            for row in reader:
                if len(row) < 3:
                    continue
                mono_t = float(row[0])
                ev_type = row[2]
                if ev_type == "BREATH":
                    breath_times.append(mono_t)

    aligned: List[AlignedWindow] = []
    half_win = window_length_sec / 2.0

    for center_t, est_bpm, conf, est_state in estimator_windows:
        w_start = center_t - half_win
        w_end = center_t + half_win

        ref_bpm: Optional[float] = None
        ref_state = "STILL"

        if event_intervals:
            # Synthetic label matching
            overlapping = [
                (st, bpm)
                for (t0, t1, st, bpm) in event_intervals
                if max(t0, w_start) < min(t1, w_end)
            ]
            if overlapping:
                # Majority state
                states = [item[0] for item in overlapping]
                ref_state = max(set(states), key=states.count)
                bpms = [item[1] for item in overlapping if item[1] is not None]
                if bpms:
                    ref_bpm = sum(bpms) / len(bpms)
        elif breath_times:
            # Breath taps inside window
            taps = [t for t in breath_times if w_start <= t <= w_end]
            if len(taps) >= 2:
                intervals = [taps[i] - taps[i - 1] for i in range(1, len(taps))]
                intervals.sort()
                med_interval = intervals[len(intervals) // 2]
                if med_interval > 0.5:
                    ref_bpm = 60.0 / med_interval

        is_evaluable = (ref_bpm is not None and ref_state == "STILL")

        aligned.append(
            AlignedWindow(
                center_time_s=center_t,
                bpm_estimated=est_bpm,
                bpm_reference=ref_bpm,
                state_estimated=est_state,
                state_reference=ref_state,
                confidence=conf,
                is_evaluable=is_evaluable,
            )
        )

    return aligned
