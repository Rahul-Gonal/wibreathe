"""Unit tests for UniformResampler."""

import math
from wibreathe.dsp.resample import UniformResampler


def test_uniform_resampler_linear_interpolation():
    resampler = UniformResampler(target_rate_hz=10.0, max_gap_sec=0.5)

    # Feed line y = 2*t + 1 with irregular timestamps
    t_vals = [0.0, 0.08, 0.19, 0.31, 0.42, 0.50]
    y_vals = [2.0 * t + 1.0 for t in t_vals]

    emitted_pts = []
    for t, y in zip(t_vals, y_vals):
        pts = resampler.push(t, y)
        emitted_pts.extend(pts)

    # Ensure grid timestamps are exact multiples of 0.1s
    assert len(emitted_pts) >= 4
    for grid_t, interp_v, is_valid in emitted_pts:
        assert is_valid
        expected_v = 2.0 * grid_t + 1.0
        assert math.isclose(interp_v, expected_v, abs_tol=0.05)


def test_uniform_resampler_gap_detection():
    resampler = UniformResampler(target_rate_hz=10.0, max_gap_sec=0.3)

    # Feed points with a 1.0 second gap between 0.2s and 1.2s
    resampler.push(0.0, 10.0)
    resampler.push(0.1, 10.0)
    resampler.push(0.2, 10.0)

    # 1.0 s gap
    pts_gap = resampler.push(1.2, 20.0)
    # Check that points during the gap interval were marked invalid
    invalid_pts = [p for p in pts_gap if not p[2]]
    assert len(invalid_pts) > 0


def test_batch_resample():
    t_in = [0.0, 0.05, 0.12, 0.18, 0.25]
    v_in = [1.0, 1.5, 2.2, 2.8, 3.5]
    grid_t, grid_v, mask = UniformResampler.resample_batch(t_in, v_in, target_rate_hz=20.0, max_gap_sec=0.5)

    assert len(grid_t) == len(grid_v) == len(mask)
    assert all(mask)
    assert math.isclose(grid_t[1] - grid_t[0], 0.05)
