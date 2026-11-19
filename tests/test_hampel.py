"""Unit tests for HampelFilter outlier suppression."""

import math
from wibreathe.dsp.hampel import HampelFilter


def test_hampel_spike_removal():
    hf = HampelFilter(window_size=7, n_sigmas=3.0)

    # Clean signal: baseline 20.0 with tiny noise, and an extreme outlier at index 10
    signal = [20.0 + 0.1 * math.sin(i * 0.5) for i in range(20)]
    signal[10] = 85.0  # Massive spike

    filtered = [hf.filter_sample(x) for x in signal]

    assert hf.outliers_detected >= 1
    # Check that spike at index 10 was clamped close to baseline
    assert abs(filtered[10] - 20.0) < 2.0


def test_hampel_batch_filtering():
    hf = HampelFilter(window_size=7, n_sigmas=3.0)
    data = [10.0] * 15
    data[7] = 100.0  # Spike in center

    out_causal = hf.filter_series(data, causal=True)
    assert abs(out_causal[7] - 10.0) < 1.0

    out_twoside = hf.filter_series(data, causal=False)
    assert abs(out_twoside[7] - 10.0) < 1.0
