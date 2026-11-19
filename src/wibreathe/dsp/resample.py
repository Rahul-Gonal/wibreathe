"""Uniform time-grid resampling with packet-loss gap detection."""

from __future__ import annotations

import math
from typing import Generator, List, Optional, Sequence, Tuple


class UniformResampler:
    """Resamples non-uniform timestamped measurements onto an equidistant time grid."""

    def __init__(self, target_rate_hz: float = 20.0, max_gap_sec: float = 0.5):
        if target_rate_hz <= 0:
            raise ValueError("target_rate_hz must be positive")
        self.target_rate_hz = target_rate_hz
        self.dt = 1.0 / target_rate_hz
        self.max_gap_sec = max_gap_sec

        self.last_t: Optional[float] = None
        self.last_val: Optional[float] = None
        self.next_grid_t: Optional[float] = None

    def reset(self) -> None:
        """Reset internal resampler state."""
        self.last_t = None
        self.last_val = None
        self.next_grid_t = None

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

        # Check for gap anomaly
        delta_t = t - self.last_t
        if delta_t < 0:
            # Out-of-order timestamp: discard or ignore
            return emitted

        is_gap = delta_t > self.max_gap_sec

        # Emit all uniform steps lying in the interval [last_t, t]
        while self.next_grid_t is not None and self.next_grid_t <= t:
            if is_gap:
                # Flag as invalid; hold last known value
                emitted.append((self.next_grid_t, val, False))
            else:
                # Linear interpolation
                if delta_t > 1e-9 and self.last_val is not None:
                    alpha = (self.next_grid_t - self.last_t) / delta_t
                    interp_v = self.last_val + alpha * (val - self.last_val)
                else:
                    interp_v = val
                emitted.append((self.next_grid_t, interp_v, True))

            self.next_grid_t += self.dt

        self.last_t = t
        self.last_val = val
        return emitted

    @staticmethod
    def resample_batch(
        timestamps: Sequence[float],
        values: Sequence[float],
        target_rate_hz: float = 20.0,
        max_gap_sec: float = 0.5,
    ) -> Tuple[List[float], List[float], List[bool]]:
        """Batch resample non-uniform time series to a uniform grid."""
        if len(timestamps) < 2 or len(timestamps) != len(values):
            return [], [], []

        dt = 1.0 / target_rate_hz
        t_start = math.ceil(timestamps[0] / dt) * dt
        t_end = timestamps[-1]

        grid_times: List[float] = []
        grid_vals: List[float] = []
        valid_mask: List[bool] = []

        curr_grid = t_start
        idx = 0
        n = len(timestamps)

        while curr_grid <= t_end and idx < n - 1:
            while idx < n - 1 and timestamps[idx + 1] < curr_grid:
                idx += 1

            if idx >= n - 1:
                break

            t0, t1 = timestamps[idx], timestamps[idx + 1]
            v0, v1 = values[idx], values[idx + 1]
            gap = t1 - t0

            if gap > max_gap_sec:
                grid_times.append(curr_grid)
                grid_vals.append(v0)
                valid_mask.append(False)
            else:
                alpha = (curr_grid - t0) / (gap + 1e-9)
                grid_times.append(curr_grid)
                grid_vals.append(v0 + alpha * (v1 - v0))
                valid_mask.append(True)

            curr_grid += dt

        return grid_times, grid_vals, valid_mask
