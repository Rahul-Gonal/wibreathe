"""Subcarrier selection, pruning, and multi-channel combining methods."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Set

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore


class SubcarrierSelector:
    """Handles exclusion of null/guard subcarriers, data-driven null detection, and combining."""

    def __init__(
        self,
        raw_subcarrier_count: int = 64,
        null_indices: Optional[Sequence[int]] = None,
        method: str = "variance",
        num_selected: int = 8,
        top_k: Optional[int] = None,
        layout: str = "lltf_ht20",
    ):
        self.raw_subcarrier_count = raw_subcarrier_count
        self.layout = layout
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

        for idx in valid_candidates:
            if idx >= n_subs:
                continue
            col = [matrix[t][idx] for t in range(n_steps)]
            mean_val = sum(col) / n_steps
            var_val = sum((x - mean_val) ** 2 for x in col) / max(1, n_steps - 1)

            if self.method == "snr":
                score = mean_val / (math.sqrt(var_val) + 1e-6)
            else:
                score = var_val

            scores.append((score, idx))

        scores.sort(key=lambda item: item[0], reverse=True)
        self.active_indices = [idx for _, idx in scores[: self.num_selected]]
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
