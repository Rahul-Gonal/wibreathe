"""Uniform time-grid resampling with packet-loss gap detection."""

from __future__ import annotations

import math
from typing import Generator, List, Optional, Sequence, Tuple


class UniformResampler:
    """Resamples non-uniform timestamped measurements onto an equidistant time grid."""

    def __init__(
        self,
        target_rate_hz: float = 20.0,
        max_gap_sec: float = 0.5,
        max_gap_s: Optional[float] = None,
    ):
        if target_rate_hz <= 0:
            raise ValueError("target_rate_hz must be positive")
        self.target_rate_hz = target_rate_hz
        self.dt = 1.0 / target_rate_hz
        self.max_gap_sec = max_gap_s if max_gap_s is not None else max_gap_sec

        self.last_t: Optional[float] = None
        self.last_val: Optional[float] = None
        self.next_grid_t: Optional[float] = None

    def reset(self) -> None:
        """Reset internal resampler state."""
        self.last_t = None
        self.last_val = None
        self.next_grid_t = None

    def add_sample(self, t: float, val: float) -> List[Tuple[float, float]]:
        """Add live sample and return list of (grid_time, interpolated_value) tuples."""
        emitted = self.push(t, val)
        return [(t_g, v_g) for t_g, v_g, valid in emitted if valid]

    def push(self, t: float, val: float) -> List[Tuple[float, float, bool]]:
        """Feed a new point (time_sec, value) and yield zero or more uniform grid points.
        
        Returns a list of (grid_time, interpolated_value, is_valid) tuples.
        """
        emitted: List[Tuple[float, float, bool]] = []

        if self.last_t is None:
            self.last_t = t
            self.last_val = val
            self.next_grid_t = math.ceil(t / self.dt) * self.dt
            return emitted

        if t < self.last_t:
            # Out-of-order packet: ignore
            return emitted

        gap = t - self.last_t
        is_large_gap = gap > self.max_gap_sec

        # Emit all uniform grid points that fall strictly between last_t and t
        while self.next_grid_t is not None and self.next_grid_t <= t:
            if self.next_grid_t >= self.last_t:
                if is_large_gap:
                    emitted.append((self.next_grid_t, float("nan"), False))
                else:
                    if gap > 1e-9 and self.last_val is not None:
                        alpha = (self.next_grid_t - self.last_t) / gap
                        v_interp = self.last_val + alpha * (val - self.last_val)
                    else:
                        v_interp = val
                    emitted.append((self.next_grid_t, v_interp, True))
            self.next_grid_t += self.dt

        self.last_t = t
        self.last_val = val
        return emitted

    @classmethod
    def resample_batch(
        cls,
        times_s: Sequence[float],
        values: Sequence[float],
        target_rate_hz: float = 20.0,
        max_gap_sec: float = 0.5,
    ) -> Tuple[List[float], List[float], List[bool]]:
        """Batch resampling with uniform time vector, interpolated values, and validity mask."""
        if not times_s or len(times_s) != len(values):
            return [], [], []

        resampler = cls(target_rate_hz=target_rate_hz, max_gap_sec=max_gap_sec)
        all_t: List[float] = []
        all_v: List[float] = []
        all_mask: List[bool] = []

        for t, v in zip(times_s, values):
            pts = resampler.push(t, v)
            for t_g, v_g, valid in pts:
                all_t.append(t_g)
                all_v.append(v_g)
                all_mask.append(valid)

        # Forward fill or zero fill any initial/internal NaNs in value vector
        last_good = 0.0
        for i in range(len(all_v)):
            if all_mask[i] and not math.isnan(all_v[i]):
                last_good = all_v[i]
            elif math.isnan(all_v[i]):
                all_v[i] = last_good

        return all_t, all_v, all_mask
