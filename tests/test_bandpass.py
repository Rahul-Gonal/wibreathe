"""Unit tests for RespirationBandpass and detrending."""

import math
from wibreathe.dsp.bandpass import RespirationBandpass, detrend_median


def test_bandpass_passband_transmission():
    bp = RespirationBandpass(sample_rate_hz=20.0, low_cut_hz=0.1, high_cut_hz=0.5, order=4)

    # Respiration frequency signal: 0.25 Hz (15 bpm), 200 samples (10 seconds)
    fs = 20.0
    freq_in = 0.25
    clean_wave = [math.sin(2.0 * math.pi * freq_in * (n / fs)) for n in range(300)]

    filtered = bp.filter_batch(clean_wave, zero_phase=False)

    # Discard startup transient (first 40 samples / 2 seconds)
    tail_orig = clean_wave[60:]
    tail_filt = filtered[60:]

    max_orig = max(tail_orig)
    max_filt = max(tail_filt)

    # Filter should maintain reasonable passband gain (> 0.2 in transient/causal)
    assert max_filt > 0.2


def test_detrend_median():
    # Signal with slow linear trend + small sine
    trend = [0.1 * i + math.sin(i * 0.2) for i in range(50)]
    detrended = detrend_median(trend, window_samples=15)

    assert len(detrended) == len(trend)
    # The linear trend should be flattened around zero
    mean_head = sum(detrended[:10]) / 10.0
    mean_tail = sum(detrended[-10:]) / 10.0
    assert abs(mean_tail - mean_head) < 2.0
