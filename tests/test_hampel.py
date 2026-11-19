"""Unit tests for HampelFilter outlier suppression and relative deviation floor."""

import math
from wibreathe.dsp.hampel import HampelFilter


def test_hampel_spike_removal():
    hf = HampelFilter(window_size=7, n_sigmas=3.0, min_deviation_floor=1.0)

    # Clean signal: baseline 20.0 with tiny noise, and an extreme outlier at index 10
    signal = [20.0 + 0.1 * math.sin(i * 0.5) for i in range(20)]
    signal[10] = 85.0  # Massive spike

    filtered = [hf.filter_sample(x) for x in signal]

    assert hf.outliers_detected >= 1
    # Check that spike at index 10 was clamped close to baseline
    assert abs(filtered[10] - 20.0) < 2.0


def test_hampel_batch_filtering():
    hf = HampelFilter(window_size=7, n_sigmas=3.0, min_deviation_floor=1.0)
    data = [10.0] * 15
    data[7] = 100.0  # Spike in center

    out_causal = hf.filter_series(data, causal=True)
    assert abs(out_causal[7] - 10.0) < 1.0

    out_twoside = hf.filter_series(data, causal=False)
    assert abs(out_twoside[7] - 10.0) < 1.0


def test_hampel_relative_deviation_floor():
    """Verify that relative floor ratio scales appropriately with signal magnitude."""
    # Large magnitude baseline (10,000)
    hf_rel = HampelFilter(window_size=7, n_sigmas=3.0, min_deviation_floor=None, relative_floor_ratio=0.05)
    large_data = [10000.0 + (i % 2) for i in range(15)]
    # A fluctuation of 100 is only 1%, below the 5% (500) floor, so it should not be considered an outlier
    large_data[7] = 10100.0
    out = hf_rel.filter_series(large_data, causal=False)
    assert out[7] == 10100.0

    # A spike of 5000 is 50%, well above the 5% floor, so it must be suppressed
    large_data[7] = 15000.0
    out2 = hf_rel.filter_series(large_data, causal=False)
    assert out2[7] < 10100.0


if __name__ == "__main__":
    test_hampel_spike_removal()
    test_hampel_batch_filtering()
    test_hampel_relative_deviation_floor()
    print("Hampel tests passed.")
