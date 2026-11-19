"""Butterworth Second-Order Sections (SOS) bandpass filter, detrending, and smoothing."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

try:
    import scipy.signal as signal
except ImportError:
    signal = None  # type: ignore


class RespirationBandpass:
    """Bandpass filtering pipeline tailored for human respiration frequencies (0.1 to 0.5 Hz)."""

    def __init__(
        self,
        sample_rate_hz: float = 20.0,
        low_cut_hz: float = 0.1,
        high_cut_hz: float = 0.5,
        order: int = 4,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.low_cut_hz = low_cut_hz
        self.high_cut_hz = high_cut_hz
        self.order = order

        self.sos: Optional[List[List[float]]] = None
        self._zi: Optional[List[List[float]]] = None
        self._init_filter()

    def _init_filter(self) -> None:
        """Initialize filter coefficients in SOS form."""
        if signal is not None:
            nyq = 0.5 * self.sample_rate_hz
            low = self.low_cut_hz / nyq
            high = self.high_cut_hz / nyq
            sos_arr = signal.butter(self.order, [low, high], btype="bandpass", output="sos")
            self.sos = sos_arr.tolist()
            # Initialize zero-state for causal filtering
            n_sections = len(self.sos)
            self._zi = [[0.0, 0.0] for _ in range(n_sections)]
        else:
            # Analytical 4th-order Butterworth bandpass cascade (2 biquad stages)
            # Derived via bilinear transform for fs=20Hz, [0.1, 0.5]Hz passband
            b0 = 0.059191
            b1 = 0.0
            b2 = -0.059191
            a1 = -1.876972
            a2 = 0.881619
            self.sos = [
                [b0, b1, b2, 1.0, a1, a2],
                [b0, b1, b2, 1.0, a1, a2],
            ]
            self._zi = [[0.0, 0.0] for _ in range(len(self.sos))]

    def reset_state(self) -> None:
        """Reset internal causal filter delays."""
        if self._zi is not None:
            for s in range(len(self._zi)):
                self._zi[s][0] = 0.0
                self._zi[s][1] = 0.0

    def filter_causal_step(self, x: float) -> float:
        """Process one sample causally via Direct Form II Transposed biquad sections."""
        if self.sos is None or self._zi is None:
            return x

        curr = x
        for s, sec in enumerate(self.sos):
            b0, b1, b2, a0, a1, a2 = sec
            # Direct Form II Transposed
            y = b0 * curr + self._zi[s][0]
            self._zi[s][0] = b1 * curr - a1 * y + self._zi[s][1]
            self._zi[s][1] = b2 * curr - a2 * y
            curr = y

        return curr

    def filter_batch(self, series: Sequence[float], zero_phase: bool = False) -> List[float]:
        """Filter a batch sequence using either zero-phase (sosfiltfilt) or causal forward pass."""
        if len(series) == 0:
            return []

        if zero_phase and signal is not None and self.sos is not None:
            return signal.sosfiltfilt(self.sos, series).tolist()

        # Causal filtering with fresh state
        self.reset_state()
        out: List[float] = []
        for x in series:
            out.append(self.filter_causal_step(x))
        return out


def detrend_series(series: Sequence[float], method: str = "mean") -> List[float]:
    """Remove DC baseline or linear drift without harmonic distortion."""
    n = len(series)
    if n == 0:
        return []
    if method == "linear" and n > 1:
        x_mean = (n - 1) / 2.0
        y_mean = sum(series) / n
        num = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / (den + 1e-9)
        intercept = y_mean - slope * x_mean
        return [series[i] - (slope * i + intercept) for i in range(n)]

    mean_val = sum(series) / n
    return [x - mean_val for x in series]


def detrend_median(series: Sequence[float], window_samples: int = 201) -> List[float]:
    """Remove slow baseline drift via moving-median subtraction.
    
    Note: Moving median window should be at least 3-4x the breathing period (e.g. >= 300 samples)
    to prevent beat-frequency subharmonic modulation.
    """
    n = len(series)
    if n == 0:
        return []
    half = window_samples // 2
    detrended: List[float] = []

    for i in range(n):
        i0 = max(0, i - half)
        i1 = min(n, i + half + 1)
        sub = sorted(series[i0:i1])
        med = sub[len(sub) // 2]
        detrended.append(series[i] - med)

    return detrended


def smooth_series(
    series: Sequence[float],
    method: str = "savgol",
    window_samples: int = 5,
    poly_order: int = 2,
) -> List[float]:
    """Smooth signal using Savitzky-Golay or moving-average filter."""
    n = len(series)
    if n < window_samples:
        return list(series)

    if method == "savgol" and signal is not None:
        try:
            return signal.savgol_filter(series, window_samples, poly_order).tolist()
        except Exception:
            pass  # Fallback to moving average

    # Moving average fallback
    half = window_samples // 2
    smoothed: List[float] = []
    for i in range(n):
        i0 = max(0, i - half)
        i1 = min(n, i + half + 1)
        smoothed.append(sum(series[i0:i1]) / (i1 - i0))

    return smoothed
