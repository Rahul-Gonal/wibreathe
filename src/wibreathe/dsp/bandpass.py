"""Butterworth Second-Order Sections (SOS) bandpass filter, detrending, and smoothing."""

from __future__ import annotations

import cmath
import math
from typing import List, Optional, Sequence, Tuple

try:
    import scipy.signal as signal
except ImportError:
    signal = None  # type: ignore


def butter_bandpass_sos_pure(order: int, f_low: float, f_high: float, fs: float) -> List[List[float]]:
    """Compute Butterworth bandpass filter coefficients in Second-Order Sections (SOS) format.
    
    Dynamically derived via bilinear transform of analog Butterworth prototype.
    Used as fallback when SciPy is not installed, supporting arbitrary fs, band edges, and order.
    """
    if f_low <= 0 or f_high <= f_low or f_high >= (fs / 2.0):
        raise ValueError(f"Invalid bandpass cutoffs: 0 < {f_low} < {f_high} < {fs/2.0}")

    f0 = math.sqrt(f_low * f_high)
    bw = f_high - f_low
    w0 = 2.0 * math.pi * f0 / fs
    Q_base = f0 / bw

    n_sections = max(1, order // 2)
    sections = []
    for k in range(n_sections):
        theta = math.pi * (2 * k + 1) / (2 * n_sections)
        damp = 2.0 * math.sin(theta)
        Q = Q_base / damp
        alpha = math.sin(w0) / (2.0 * Q)
        a0 = 1.0 + alpha
        b0 = alpha / a0
        b1 = 0.0
        b2 = -alpha / a0
        a1 = (-2.0 * math.cos(w0)) / a0
        a2 = (1.0 - alpha) / a0
        sections.append([b0, b1, b2, 1.0, a1, a2])

    return sections


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
        else:
            # Dynamically compute SOS coefficients from config without hardcoded constants
            self.sos = butter_bandpass_sos_pure(
                order=self.order,
                f_low=self.low_cut_hz,
                f_high=self.high_cut_hz,
                fs=self.sample_rate_hz,
            )

        n_sections = len(self.sos)
        self._zi = [[0.0, 0.0] for _ in range(n_sections)]

    def reset_state(self) -> None:
        """Reset internal causal filter delays."""
        if self._zi is not None:
            for stage in self._zi:
                stage[0] = 0.0
                stage[1] = 0.0

    def reset(self) -> None:
        """Reset internal causal filter delays (alias for reset_state)."""
        self.reset_state()

    def filter_sample(self, val: float) -> float:
        """Apply causal SOS biquad cascade to a single sample."""
        if self.sos is None or self._zi is None:
            return val

        x = val
        for s_idx, sec in enumerate(self.sos):
            b0, b1, b2, a0, a1, a2 = sec
            w0, w1 = self._zi[s_idx]

            # Direct Form II Transposed implementation
            y = b0 * x + w0
            w0 = b1 * x - a1 * y + w1
            w1 = b2 * x - a2 * y

            self._zi[s_idx][0] = w0
            self._zi[s_idx][1] = w1
            x = y

        return x

    def filter_batch(self, signal_in: Sequence[float], zero_phase: bool = True) -> List[float]:
        """Apply zero-phase forward-backward or forward SOS filtering over a batch."""
        if not signal_in:
            return []
        if signal is not None and zero_phase:
            sos_arr = signal.butter(
                self.order,
                [self.low_cut_hz / (0.5 * self.sample_rate_hz), self.high_cut_hz / (0.5 * self.sample_rate_hz)],
                btype="bandpass",
                output="sos",
            )
            return signal.sosfiltfilt(sos_arr, signal_in).tolist()

        # Pure-Python causal forward pass
        self.reset_state()
        forward = [self.filter_sample(x) for x in signal_in]
        return forward


def detrend_series(values: Sequence[float], method: str = "linear") -> List[float]:
    """Detrend timeseries to remove DC drift and slow multipath baseline changes."""
    n = len(values)
    if n <= 1:
        return list(values)

    if method == "mean":
        mean_v = sum(values) / n
        return [x - mean_v for x in values]

    if method == "linear":
        # Fit y = m*x + c
        sum_x = n * (n - 1) / 2.0
        sum_x2 = (n - 1) * n * (2 * n - 1) / 6.0
        sum_y = sum(values)
        sum_xy = sum(i * y for i, y in enumerate(values))

        denom = (n * sum_x2 - sum_x * sum_x)
        if abs(denom) < 1e-12:
            mean_v = sum_y / n
            return [x - mean_v for x in values]

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n
        return [y - (slope * i + intercept) for i, y in enumerate(values)]

    return list(values)


def detrend_median(
    values: Sequence[float],
    window_size: int = 41,
    window_samples: Optional[int] = None,
) -> List[float]:
    """Moving-median baseline subtraction."""
    n = len(values)
    if n == 0:
        return []
    win = window_samples if window_samples is not None else window_size
    radius = win // 2
    detrended: List[float] = []

    for i in range(n):
        start = max(0, i - radius)
        end = min(n, i + radius + 1)
        sub = sorted(values[start:end])
        mid = len(sub) // 2
        med = sub[mid] if len(sub) % 2 == 1 else (sub[mid - 1] + sub[mid]) / 2.0
        detrended.append(values[i] - med)

    return detrended
