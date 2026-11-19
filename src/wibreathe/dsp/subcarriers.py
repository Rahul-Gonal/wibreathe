"""Subcarrier selection, pruning, and multi-channel combining methods."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Set

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore


class SubcarrierSelector:
    """Handles exclusion of null/guard subcarriers and dimensionality reduction."""

    def __init__(
        self,
        raw_subcarrier_count: int = 64,
        null_indices: Optional[Sequence[int]] = None,
        method: str = "variance",
        num_selected: int = 8,
    ):
        self.raw_subcarrier_count = raw_subcarrier_count
        self.null_indices: Set[int] = set(
            null_indices if null_indices is not None else [0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]
        )
        self.method = method.lower()
        self.num_selected = max(1, min(num_selected, raw_subcarrier_count - len(self.null_indices)))

        self.valid_indices: List[int] = [
            i for i in range(raw_subcarrier_count) if i not in self.null_indices
        ]
        self.active_indices: List[int] = self.valid_indices[: self.num_selected]

    def update_selection(self, matrix: Sequence[Sequence[float]]) -> List[int]:
        """Update top subcarrier indices based on recent temporal matrix [time_steps x subcarriers]."""
        if not matrix or not matrix[0]:
            return self.active_indices

        n_steps = len(matrix)
        n_subs = len(matrix[0])
        scores: List[tuple[float, int]] = []

        for idx in self.valid_indices:
            if idx >= n_subs:
                continue
            col = [matrix[t][idx] for t in range(n_steps)]
            mean_val = sum(col) / n_steps
            var_val = sum((x - mean_val) ** 2 for x in col) / max(1, n_steps - 1)

            if self.method == "snr":
                # High mean signal with controlled variance
                score = mean_val / (math.sqrt(var_val) + 1e-6)
            else:
                # Default variance metric: higher variance reflects greater respiration modulation
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
            # Extract subcarrier matrix
            sub_mat = np.array([[row[idx] for idx in indices] for row in matrix], dtype=float)
            # Center data
            sub_mat -= np.mean(sub_mat, axis=0)
            # SVD to extract first principal component
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
