"""Breathing rate estimation using FFT with parabolic interpolation and peak-interval analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from wibreathe.dsp.bandpass import detrend_series


@dataclass
class BreathingEstimate:
    """Estimated respiration rate and signal quality metrics for a temporal window."""
    bpm: Optional[float]           # Estimated breaths per minute, or None if unreliable/empty
    confidence: float              # Confidence score [0.0 to 100.0]
    peak_freq_hz: float            # Dominant respiration frequency
    spectral_purity: float         # Ratio of dominant peak power to breathing band power
    bpm_spectral: float            # Rate estimated strictly from interpolated FFT peak
    bpm_time_domain: Optional[float] # Rate estimated from peak-to-peak time intervals
    is_valid: bool                 # True if confidence >= threshold and bed is occupied


def _simple_fft_magnitudes(
    signal_in: Sequence[float],
    n_fft: int,
    sample_rate_hz: float = 20.0,
) -> Tuple[List[float], List[float]]:
    """Compute one-sided FFT frequency bins and magnitudes (supports numpy or pure-python fallback)."""
    n_in = len(signal_in)
    padded = list(signal_in) + [0.0] * (n_fft - n_in)

    if np is not None:
        fft_vals = np.fft.rfft(padded, n=n_fft)
        mags = np.abs(fft_vals).tolist()
        freqs = (np.fft.rfftfreq(n_fft, d=1.0 / sample_rate_hz)).tolist()
        return freqs, mags

    # Pure Python DFT fallback for frequency band [0.05, 0.6] Hz
    freqs: List[float] = []
    mags: List[float] = []
    num_bins = n_fft // 2 + 1

    for k in range(num_bins):
        f = k * sample_rate_hz / n_fft
        freqs.append(f)
        if 0.05 <= f <= 0.6:
            real_sum = 0.0
            imag_sum = 0.0
            for n in range(n_in):
                angle = -2.0 * math.pi * k * n / n_fft
                real_sum += padded[n] * math.cos(angle)
                imag_sum += padded[n] * math.sin(angle)
            mags.append(math.sqrt(real_sum**2 + imag_sum**2))
        else:
            mags.append(0.0)

    return freqs, mags


class RespirationEstimator:
    """Extracts respiration rate combining high-resolution spectral analysis and time-domain peaks."""

    def __init__(
        self,
        sample_rate_hz: float = 20.0,
        bandpass_low_hz: float = 0.1,
        bandpass_high_hz: float = 0.5,
        fft_pad_factor: int = 8,
        min_peak_prominence: float = 0.05,
        min_peak_distance_sec: float = 1.2,
        min_confidence_score: float = 35.0,
        window_duration_s: float = 30.0,
        step_duration_s: float = 1.0,
        min_bpm: float = 6.0,
        max_bpm: float = 30.0,
        fft_zero_pad_factor: Optional[int] = None,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.bandpass_low_hz = bandpass_low_hz if bandpass_low_hz > 0 else (min_bpm / 60.0)
        self.bandpass_high_hz = bandpass_high_hz if bandpass_high_hz > 0 else (max_bpm / 60.0)
        self.fft_pad_factor = fft_zero_pad_factor if fft_zero_pad_factor is not None else fft_pad_factor
        self.min_peak_prominence = min_peak_prominence
        # Bound minimum peak distance so rates up to max_bpm are not dropped
        max_period_s = 60.0 / max(1.0, max_bpm)
        eff_peak_dist_s = min(min_peak_distance_sec, 0.65 * max_period_s)
        self.min_peak_distance_samples = max(2, int(round(eff_peak_dist_s * sample_rate_hz)))
        self.min_confidence_score = min_confidence_score
        self.window_duration_s = window_duration_s
        self.step_duration_s = step_duration_s

        # Rolling buffer for streaming add_sample
        self._samples: List[float] = []
        self._last_eval_t: float = 0.0

    def add_sample(self, t: float, val: float) -> Optional[Tuple[float, float]]:
        """Add sample to rolling window and return (bpm, confidence) if step interval elapsed."""
        self._samples.append(val)
        max_len = int(self.window_duration_s * self.sample_rate_hz)
        if len(self._samples) > max_len:
            self._samples.pop(0)

        step_samples = int(self.step_duration_s * self.sample_rate_hz)
        if len(self._samples) >= max_len and (t - self._last_eval_t) >= self.step_duration_s:
            est = self.estimate_rate(self._samples)
            self._last_eval_t = t
            return est.bpm, est.confidence
        return None

    def estimate_rate(
        self,
        signal_window: Sequence[float],
        is_empty_bed: bool = False,
        is_motion: bool = False,
    ) -> BreathingEstimate:
        """Estimate breathing rate from a pre-filtered window (e.g. 30 seconds)."""
        n_samples = len(signal_window)
        if n_samples < int(10.0 * self.sample_rate_hz) or is_empty_bed or is_motion:
            return BreathingEstimate(
                bpm=None,
                confidence=0.0,
                peak_freq_hz=0.0,
                spectral_purity=0.0,
                bpm_spectral=0.0,
                bpm_time_domain=None,
                is_valid=False,
            )

        # 1. Linearly detrend window to prevent DC ramp spectral leakage, then apply Hann window
        detrended = detrend_series(signal_window, method="linear")
        mean_val = sum(detrended) / n_samples
        windowed: List[float] = []
        for i, val in enumerate(detrended):
            w = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (n_samples - 1)))
            windowed.append((val - mean_val) * w)

        # 2. Compute zero-padded FFT
        n_fft = max(2048, 1 << (math.ceil(math.log2(n_samples * self.fft_pad_factor))))
        freqs, mags = _simple_fft_magnitudes(windowed, n_fft, sample_rate_hz=self.sample_rate_hz)

        # 3. Locate dominant peak inside respiration band [0.1, 0.5] Hz
        # Guard against band-edge falling tails: candidate must be a true local maximum
        best_k = -1
        max_mag = -1.0
        band_power = 0.0

        for k, (f, mag) in enumerate(zip(freqs, mags)):
            if self.bandpass_low_hz <= f <= self.bandpass_high_hz:
                band_power += mag**2
                if 0 < k < len(mags) - 1:
                    if mags[k] > mags[k - 1] and mags[k] >= mags[k + 1]:
                        if mag > max_mag:
                            max_mag = mag
                            best_k = k

        if best_k <= 0 or best_k >= len(mags) - 1 or max_mag < 1e-6:
            return BreathingEstimate(
                bpm=None,
                confidence=0.0,
                peak_freq_hz=0.0,
                spectral_purity=0.0,
                bpm_spectral=0.0,
                bpm_time_domain=None,
                is_valid=False,
            )

        # 4. Parabolic peak interpolation for sub-bin frequency accuracy
        y_prev = mags[best_k - 1]
        y_curr = mags[best_k]
        y_next = mags[best_k + 1]

        denom = y_prev - 2.0 * y_curr + y_next
        if abs(denom) > 1e-9:
            delta = 0.5 * (y_prev - y_next) / denom
            delta = max(-0.5, min(0.5, delta))
        else:
            delta = 0.0

        bin_width = self.sample_rate_hz / n_fft
        interp_freq = (best_k + delta) * bin_width
        interp_freq = max(self.bandpass_low_hz, min(self.bandpass_high_hz, interp_freq))
        bpm_spectral = interp_freq * 60.0

        # Spectral purity: ratio of power in peak neighborhood (+/- 0.04 Hz) to total breathing band power
        neighborhood_power = 0.0
        for f, mag in zip(freqs, mags):
            if abs(f - interp_freq) <= 0.04:
                neighborhood_power += mag**2

        spectral_purity = min(1.0, neighborhood_power / max(1e-9, band_power))

        # 5. Time-domain peak counting
        peaks = self._find_time_domain_peaks(signal_window)
        bpm_time: Optional[float] = None
        if len(peaks) >= 2:
            intervals = [
                (peaks[i] - peaks[i - 1]) / self.sample_rate_hz
                for i in range(1, len(peaks))
            ]
            intervals.sort()
            median_interval = intervals[len(intervals) // 2]
            if median_interval > 0.5:
                bpm_time = 60.0 / median_interval

        # 6. Confidence Scoring
        confidence = spectral_purity * 65.0  # Up to 65% from purity
        if bpm_time is not None:
            # Agreement check between spectral and time-domain estimates
            diff_bpm = abs(bpm_spectral - bpm_time)
            if diff_bpm <= 1.5:
                confidence += 35.0
            elif diff_bpm <= 3.0:
                confidence += 20.0
            elif diff_bpm <= 5.0:
                confidence += 10.0

        confidence = max(0.0, min(100.0, confidence))
        is_valid = confidence >= self.min_confidence_score

        final_bpm = bpm_spectral if is_valid else None

        return BreathingEstimate(
            bpm=final_bpm,
            confidence=confidence,
            peak_freq_hz=interp_freq,
            spectral_purity=spectral_purity,
            bpm_spectral=bpm_spectral,
            bpm_time_domain=bpm_time,
            is_valid=is_valid,
        )

    def _find_time_domain_peaks(self, signal_in: Sequence[float]) -> List[int]:
        """Detect local maxima with minimum spacing and prominence."""
        n = len(signal_in)
        if n < 3:
            return []

        candidates: List[int] = []
        for i in range(1, n - 1):
            if signal_in[i] > signal_in[i - 1] and signal_in[i] >= signal_in[i + 1]:
                candidates.append(i)

        if not candidates:
            return []

        # Filter by distance and prominence
        accepted: List[int] = []
        for c in candidates:
            val = signal_in[c]
            # Simple local prominence
            left_min = min(signal_in[max(0, c - self.min_peak_distance_samples) : c])
            right_min = min(signal_in[c + 1 : min(n, c + self.min_peak_distance_samples + 1)])
            prominence = val - max(left_min, right_min)

            if prominence < self.min_peak_prominence:
                continue

            if not accepted:
                accepted.append(c)
            else:
                if (c - accepted[-1]) >= self.min_peak_distance_samples:
                    accepted.append(c)
                else:
                    if val > signal_in[accepted[-1]]:
                        accepted[-1] = c

        return accepted
