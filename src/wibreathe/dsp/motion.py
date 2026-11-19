"""Adaptive motion detection using rolling short-term variance and hysteresis."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Sequence


@dataclass
class MotionState:
    """Current motion detection metrics and classification."""
    is_moving: bool
    current_variance: float
    baseline_median: float
    threshold_enter: float
    threshold_exit: float
    duration_active_s: float


class MotionDetector:
    """Detects physical body movements from broadband CSI amplitude fluctuations."""

    def __init__(
        self,
        sample_rate_hz: float = 20.0,
        short_window_sec: float = 1.5,
        step_sec: float = 0.5,
        multiplier_enter: float = 3.5,
        multiplier_exit: float = 2.0,
        min_duration_sec: float = 1.0,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.window_samples = int(round(short_window_sec * sample_rate_hz))
        self.step_samples = int(round(step_sec * sample_rate_hz))
        self.multiplier_enter = multiplier_enter
        self.multiplier_exit = multiplier_exit
        self.min_duration_sec = min_duration_sec

        self._buffer: Deque[float] = deque(maxlen=self.window_samples)
        self._samples_since_eval: int = 0

        # Calibrated baseline references
        self.baseline_median: float = 0.05
        self.baseline_mad: float = 0.02
        self._update_thresholds()

        self.is_moving: bool = False
        self._motion_time_accum: float = 0.0
        self._still_time_accum: float = 0.0

    def _update_thresholds(self) -> None:
        sigma = 1.4826 * max(1e-5, self.baseline_mad)
        self.threshold_enter = self.baseline_median + self.multiplier_enter * sigma
        self.threshold_exit = self.baseline_median + self.multiplier_exit * sigma

    def set_calibration(self, baseline_median: float, baseline_mad: float) -> None:
        """Configure baseline parameters from a formal calibration session."""
        self.baseline_median = max(1e-5, baseline_median)
        self.baseline_mad = max(1e-5, baseline_mad)
        self._update_thresholds()

    def reset(self) -> None:
        """Clear motion tracking history."""
        self._buffer.clear()
        self._samples_since_eval = 0
        self.is_moving = False
        self._motion_time_accum = 0.0
        self._still_time_accum = 0.0

    def push_sample(self, val: float) -> MotionState:
        """Add a single broadband amplitude sample and return current state."""
        self._buffer.append(val)
        self._samples_since_eval += 1

        dt = 1.0 / self.sample_rate_hz
        curr_var = self._compute_variance()

        if curr_var >= self.threshold_enter:
            self._motion_time_accum += dt
            self._still_time_accum = 0.0
            if self._motion_time_accum >= self.min_duration_sec:
                self.is_moving = True
        elif curr_var <= self.threshold_exit:
            self._still_time_accum += dt
            self._motion_time_accum = 0.0
            if self._still_time_accum >= self.min_duration_sec:
                self.is_moving = False

        return MotionState(
            is_moving=self.is_moving,
            current_variance=curr_var,
            baseline_median=self.baseline_median,
            threshold_enter=self.threshold_enter,
            threshold_exit=self.threshold_exit,
            duration_active_s=self._motion_time_accum if self.is_moving else self._still_time_accum,
        )

    def _compute_variance(self) -> float:
        n = len(self._buffer)
        if n < 4:
            return 0.0
        mean_v = sum(self._buffer) / n
        return sum((x - mean_v) ** 2 for x in self._buffer) / (n - 1)
