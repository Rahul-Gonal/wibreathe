"""Subcarrier selection, pruning, and multi-channel combining methods."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from wibreathe.dsp.bandpass import RespirationBandpass


def compute_breathing_band_snr(
    col: Sequence[float],
    sample_rate_hz: float = 50.0,
    low_cut_hz: float = 0.10,
    high_cut_hz: float = 0.50,
) -> float:
    """Compute Signal-to-Noise Ratio (SNR) in the human breathing frequency band after linear detrending.
    
    Subcarriers with energy concentrated in the 0.10-0.50 Hz band achieve high scores.
    Decoy subcarriers with high variance outside this band (jitter, drift, 2Hz noise)
    produce low scores because out-of-band energy is penalized.
    """
    n = len(col)
    if n < 20:
        return 0.0

    # 1. Linear detrend
    mean_y = sum(col) / n
    mean_x = (n - 1) / 2.0
    s_xy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(col))
    s_xx = n * (n * n - 1) / 12.0
    slope = s_xy / s_xx if s_xx > 0 else 0.0
    detrended = [y - (mean_y + slope * (i - mean_x)) for i, y in enumerate(col)]

    total_var = sum(v * v for v in detrended) / n
    if total_var < 1e-6:
        return 0.0

    # 2. Respiration bandpass filter (pure Python SOS biquad)
    bp = RespirationBandpass(
        sample_rate_hz=sample_rate_hz,
        low_cut_hz=low_cut_hz,
        high_cut_hz=high_cut_hz,
        order=4,
    )
    filtered = [bp.filter_sample(v) for v in detrended]

    # Skip initial filter warmup settling (first 10-15%)
    settle = min(20, n // 5)
    settled_filt = filtered[settle:]
    settled_detrend = detrended[settle:]

    if not settled_filt:
        return 0.0

    in_band_power = sum(v * v for v in settled_filt) / len(settled_filt)
    out_band_power = sum((d - f) ** 2 for d, f in zip(settled_detrend, settled_filt)) / len(settled_detrend)

    if out_band_power < 1e-6:
        return in_band_power / 1e-6
    return in_band_power / out_band_power


class SubcarrierSelector:
    """Handles exclusion of null/guard subcarriers, breathing SNR ranking, hysteresis, and PCA combining."""

    def __init__(
        self,
        raw_subcarrier_count: int = 64,
        null_indices: Optional[Sequence[int]] = None,
        method: str = "snr",
        num_selected: int = 8,
        top_k: Optional[int] = None,
        layout: str = "lltf_ht20",
        sample_rate_hz: float = 50.0,
        hysteresis_ratio: float = 0.20,
    ):
        self.raw_subcarrier_count = raw_subcarrier_count
        self.layout = layout
        self.sample_rate_hz = sample_rate_hz
        self.hysteresis_ratio = hysteresis_ratio
        # UNVERIFIED on hardware: nominal subcarrier null/guard bins
        self.null_indices: Set[int] = set(
            null_indices if null_indices is not None else [0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]
        )
        self.method = method.lower()
        k_val = top_k if top_k is not None else num_selected
        self.num_selected = max(1, min(k_val, raw_subcarrier_count - len(self.null_indices)))

        self.valid_indices: List[int] = [
            i for i in range(raw_subcarrier_count) if i not in self.null_indices
        ]
        self.active_indices: List[int] = self.valid_indices[: self.num_selected]
        self.current_selected_idx: Optional[int] = None
        self.current_selected_score: float = 0.0

    @staticmethod
    def detect_null_subcarriers(
        matrix: Sequence[Sequence[float]],
        threshold_ratio: float = 0.05,
    ) -> Set[int]:
        """Detect null/guard/dead subcarriers empirically from raw amplitude data.
        
        Subcarriers with near-zero amplitude (< threshold_ratio * median) or negligible
        variance (< 1e-4) are identified as dead/null bins.
        """
        if not matrix or not matrix[0]:
            return set()
        n_steps = len(matrix)
        n_subs = len(matrix[0])

        means = [sum(matrix[t][i] for t in range(n_steps)) / n_steps for i in range(n_subs)]
        vars_ = [
            sum((matrix[t][i] - means[i]) ** 2 for t in range(n_steps)) / max(1, n_steps - 1)
            for i in range(n_subs)
        ]

        sorted_means = sorted(means)
        med_mean = sorted_means[len(sorted_means) // 2]
        cutoff = max(1e-4, med_mean * threshold_ratio)

        nulls: Set[int] = set()
        for i in range(n_subs):
            if means[i] < cutoff or vars_[i] < 1e-4:
                nulls.add(i)
        return nulls

    def select_top_k(self, matrix: Sequence[Sequence[float]]) -> List[int]:
        """Alias for update_selection to retrieve top K sensitive subcarriers."""
        return self.update_selection(matrix)

    def update_selection(self, matrix: Sequence[Sequence[float]]) -> List[int]:
        """Update top subcarrier indices based on recent temporal matrix [time_steps x subcarriers]."""
        if not matrix or not matrix[0]:
            return self.active_indices

        # Augment static nominal nulls with empirical data-driven null detection
        empirical_nulls = self.detect_null_subcarriers(matrix)
        effective_nulls = self.null_indices | empirical_nulls
        valid_candidates = [i for i in range(len(matrix[0])) if i not in effective_nulls]

        if not valid_candidates:
            valid_candidates = [i for i in range(len(matrix[0])) if i not in self.null_indices]
        if not valid_candidates:
            valid_candidates = list(range(len(matrix[0])))

        n_steps = len(matrix)
        n_subs = len(matrix[0])
        scores: List[tuple[float, int]] = []
        scores_map: Dict[int, float] = {}

        for idx in valid_candidates:
            if idx >= n_subs:
                continue
            col = [matrix[t][idx] for t in range(n_steps)]
            if self.method in ("snr", "breathing_snr"):
                score = compute_breathing_band_snr(col, sample_rate_hz=self.sample_rate_hz)
            else:
                mean_val = sum(col) / n_steps
                score = sum((x - mean_val) ** 2 for x in col) / max(1, n_steps - 1)

            scores.append((score, idx))
            scores_map[idx] = score

        scores.sort(key=lambda item: item[0], reverse=True)
        if not scores:
            return self.active_indices

        best_score, best_candidate_idx = scores[0]

        # Apply hysteresis: only switch away from current_selected_idx if best_candidate beats it by margin
        if self.current_selected_idx is not None and self.current_selected_idx in scores_map:
            current_score = scores_map[self.current_selected_idx]
            if best_score < current_score * (1.0 + self.hysteresis_ratio):
                # Retain current selected index
                chosen_top = self.current_selected_idx
            else:
                chosen_top = best_candidate_idx
                self.current_selected_idx = chosen_top
                self.current_selected_score = best_score
        else:
            chosen_top = best_candidate_idx
            self.current_selected_idx = chosen_top
            self.current_selected_score = best_score

        # Order active indices with chosen_top at index 0, followed by remaining top scores
        top_list = [chosen_top]
        for sc, idx in scores:
            if idx != chosen_top and len(top_list) < self.num_selected:
                top_list.append(idx)

        self.active_indices = top_list
        return self.active_indices

    def combine_channels(
        self,
        matrix: Sequence[Sequence[float]],
        selected_indices: Optional[Sequence[int]] = None,
    ) -> List[float]:
        """Combine multi-subcarrier amplitude matrix [time x subcarriers] into a single 1D signal."""
        if not matrix:
            return []

        indices = list(selected_indices) if selected_indices is not None else self.active_indices
        n_steps = len(matrix)

        if self.method == "pca" and np is not None:
            sub_mat = np.array([[row[idx] for idx in indices] for row in matrix], dtype=float)
            sub_mat -= np.mean(sub_mat, axis=0)
            try:
                _, _, vt = np.linalg.svd(sub_mat, full_matrices=False)
                pc1 = np.dot(sub_mat, vt[0])
                return pc1.tolist()
            except Exception:
                pass  # Fallback to normalized mean

        # Standard normalized mean combining
        combined: List[float] = [0.0] * n_steps
        for idx in indices:
            col = [matrix[t][idx] for t in range(n_steps)]
            mean_v = sum(col) / n_steps
            std_v = math.sqrt(sum((x - mean_v) ** 2 for x in col) / max(1, n_steps - 1)) + 1e-6
            for t in range(n_steps):
                combined[t] += (col[t] - mean_v) / std_v

        k = len(indices)
        return [c / max(1, k) for c in combined]
