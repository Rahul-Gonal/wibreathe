"""Hampel identifier filter for outlier detection and spike suppression."""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Sequence


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
    """Causal and batch Hampel filter for outlier replacement."""

    def __init__(
        self,
        window_size: int = 7,
        n_sigmas: float = 3.0,
        min_deviation_floor: float = 3.0,
    ):
        if window_size < 3:
            raise ValueError("window_size must be >= 3")
        self.window_size = window_size
        self.n_sigmas = n_sigmas
        self.min_deviation_floor = min_deviation_floor
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
        threshold = max(self.min_deviation_floor, self.n_sigmas * 1.4826 * mad)

        if abs(val - med) > threshold:
            self.outliers_detected += 1
            sanitized = med
        else:
            sanitized = val

        self._history.append(sanitized)
        return sanitized

    def filter_series(self, series: Sequence[float], causal: bool = True) -> List[float]:
        """Apply Hampel filter to an entire batch sequence."""
        n = len(series)
        if n < 3:
            return list(series)

        if causal:
            hf = HampelFilter(self.window_size, self.n_sigmas, self.min_deviation_floor)
            return [hf.filter_sample(x) for x in series]

        # Two-sided offline filter
        half_w = self.window_size // 2
        filtered: List[float] = []

        for i in range(n):
            i_start = max(0, i - half_w)
            i_end = min(n, i + half_w + 1)
            window = series[i_start:i_end]

            med, mad = _median_and_mad(window)
            thresh = max(self.min_deviation_floor, self.n_sigmas * 1.4826 * mad)
            x = series[i]

            if abs(x - med) > thresh:
                filtered.append(med)
            else:
                filtered.append(x)

        return filtered
