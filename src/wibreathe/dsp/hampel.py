"""Hampel identifier filter for outlier detection and spike suppression."""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Optional, Sequence


def _median_and_mad(values: Sequence[float]) -> tuple[float, float]:
    """Calculate median and Median Absolute Deviation (MAD)."""
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 0:
        return 0.0, 0.0
    mid = n // 2
    if n % 2 == 1:
        med = sorted_v[mid]
    else:
        med = (sorted_v[mid - 1] + sorted_v[mid]) / 2.0

    devs = sorted(abs(x - med) for x in sorted_v)
    if n % 2 == 1:
        mad = devs[mid]
    else:
        mad = (devs[mid - 1] + devs[mid]) / 2.0

    return med, mad


class HampelFilter:
    """Causal and batch Hampel filter for outlier replacement with relative deviation floor."""

    def __init__(
        self,
        window_size: int = 7,
        n_sigmas: float = 3.0,
        min_deviation_floor: Optional[float] = None,
        relative_floor_ratio: float = 0.15,
    ):
        if window_size < 3:
            raise ValueError("window_size must be >= 3")
        self.window_size = window_size
        self.n_sigmas = n_sigmas
        self.min_deviation_floor = min_deviation_floor
        self.relative_floor_ratio = relative_floor_ratio
        self._history: Deque[float] = deque(maxlen=window_size)
        self.outliers_detected: int = 0

    def reset(self) -> None:
        """Clear streaming history."""
        self._history.clear()
        self.outliers_detected = 0

    def filter_sample(self, val: float) -> float:
        """Apply causal Hampel filter to an incoming live sample."""
        if len(self._history) < self.window_size:
            self._history.append(val)
            return val

        med, mad = _median_and_mad(list(self._history))
        if self.min_deviation_floor is not None:
            floor = self.min_deviation_floor
        else:
            floor = max(1.0, abs(med) * self.relative_floor_ratio)

        threshold = max(floor, self.n_sigmas * 1.4826 * mad)

        if abs(val - med) > threshold:
            self.outliers_detected += 1
            sanitized = med
        else:
            sanitized = val

        self._history.append(sanitized)
        return sanitized

    def filter_series(self, data: Sequence[float], causal: bool = False) -> List[float]:
        """Filter an entire sequence with either causal or two-sided batch window."""
        if causal:
            self.reset()
            return [self.filter_sample(x) for x in data]
        return self.filter_batch(data)

    def filter_batch(self, signal_in: Sequence[float]) -> List[float]:
        """Apply centered two-sided Hampel filter across an entire offline batch."""
        n = len(signal_in)
        if n == 0:
            return []
        if n < self.window_size:
            return list(signal_in)

        radius = self.window_size // 2
        sanitized = list(signal_in)

        for i in range(n):
            start = max(0, i - radius)
            end = min(n, i + radius + 1)
            window = signal_in[start:end]

            med, mad = _median_and_mad(window)
            if self.min_deviation_floor is not None:
                floor = self.min_deviation_floor
            else:
                floor = max(1.0, abs(med) * self.relative_floor_ratio)

            threshold = max(floor, self.n_sigmas * 1.4826 * mad)

            if abs(signal_in[i] - med) > threshold:
                sanitized[i] = med

        return sanitized
