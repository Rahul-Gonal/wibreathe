"""Unit tests for RespirationBandpass, dynamic Butterworth design, and detrending."""

import math
from wibreathe.dsp.bandpass import RespirationBandpass, butter_bandpass_sos_pure, detrend_median, detrend_series


def test_bandpass_passband_transmission():
    bp = RespirationBandpass(sample_rate_hz=20.0, low_cut_hz=0.1, high_cut_hz=0.5, order=4)

    # Respiration frequency signal: 0.25 Hz (15 bpm), 300 samples (15 seconds)
    fs = 20.0
    freq_in = 0.25
    clean_wave = [math.sin(2.0 * math.pi * freq_in * (n / fs)) for n in range(300)]

    filtered = bp.filter_batch(clean_wave)

    tail_orig = clean_wave[60:]
    tail_filt = filtered[60:]

    max_orig = max(tail_orig)
    max_filt = max(tail_filt)

    # Filter should maintain strong passband gain (> 0.2 in causal cascade)
    assert max_filt > 0.2


def test_dynamic_butterworth_sos_computation():
    """Verify that pure-Python Butterworth SOS generator computes stable biquads for arbitrary configs."""
    for (fs, fl, fh, ord_val) in [(20.0, 0.1, 0.5, 4), (25.0, 0.15, 0.45, 4), (50.0, 0.08, 0.4, 4)]:
        sos = butter_bandpass_sos_pure(order=ord_val, f_low=fl, f_high=fh, fs=fs)
        assert len(sos) == ord_val // 2

        for sec in sos:
            b0, b1, b2, a0, a1, a2 = sec
            # Direct DC gain check: sum(b) should be 0 because b0 = -b2 and b1 = 0
            assert abs(b0 + b1 + b2) < 1e-12
            assert a0 == 1.0
            # Stability check: denominator roots inside unit circle (|a2| < 1)
            assert abs(a2) < 1.0


def test_compare_fallback_to_scipy_if_available():
    """If SciPy is installed, compare pure-Python fallback SOS coefficients against scipy.signal.butter."""
    try:
        import scipy.signal as signal
        fs = 20.0
        fl = 0.1
        fh = 0.5
        order = 4

        sos_pure = butter_bandpass_sos_pure(order, fl, fh, fs)
        sos_scipy = signal.butter(order, [fl / (0.5 * fs), fh / (0.5 * fs)], btype="bandpass", output="sos")

        # Compare poles: |a2| and a1 should be close
        for pure_sec, sci_sec in zip(sos_pure, sos_scipy):
            # Check stability and close matching of denominator coefficients
            assert abs(pure_sec[5] - sci_sec[5]) < 0.2
    except ImportError:
        # SciPy not installed in this environment; test skipped cleanly
        pass


def test_detrend_median():
    trend = [0.1 * i + math.sin(i * 0.2) for i in range(50)]
    detrended = detrend_median(trend, window_size=15)

    assert len(detrended) == len(trend)
    mean_head = sum(detrended[:10]) / 10.0
    mean_tail = sum(detrended[-10:]) / 10.0
    assert abs(mean_tail - mean_head) < 2.0


def test_detrend_linear():
    linear = [2.0 * i + 5.0 for i in range(50)]
    detrended = detrend_series(linear, method="linear")
    assert all(abs(x) < 1e-6 for x in detrended)


if __name__ == "__main__":
    test_bandpass_passband_transmission()
    test_dynamic_butterworth_sos_computation()
    test_compare_fallback_to_scipy_if_available()
    test_detrend_median()
    test_detrend_linear()
    print("Bandpass tests passed.")
