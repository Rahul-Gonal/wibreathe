"""Unit tests for RespirationEstimator."""

import math
from wibreathe.dsp.breathing import RespirationEstimator


def test_estimate_known_respiration_rate():
    estimator = RespirationEstimator(sample_rate_hz=20.0, min_confidence_score=20.0)

    # 15 bpm -> 0.25 Hz; 30-second window (600 samples)
    fs = 20.0
    target_bpm = 15.0
    target_freq = target_bpm / 60.0
    duration_s = 30.0
    n_pts = int(duration_s * fs)

    wave = [
        math.sin(2.0 * math.pi * target_freq * (i / fs))
        for i in range(n_pts)
    ]

    est = estimator.estimate_rate(wave, is_empty_bed=False, is_motion=False)

    assert est.is_valid
    assert est.bpm is not None
    # Parabolic interpolation should yield accuracy within 0.5 bpm
    assert abs(est.bpm - target_bpm) < 0.5
    assert est.confidence > 50.0


def test_empty_bed_and_motion_rejection():
    estimator = RespirationEstimator(sample_rate_hz=20.0)
    wave = [0.1 * math.sin(i * 0.2) for i in range(600)]

    # Empty bed must never yield a numerical BPM
    est_empty = estimator.estimate_rate(wave, is_empty_bed=True, is_motion=False)
    assert est_empty.bpm is None
    assert not est_empty.is_valid

    # Active motion must never yield a numerical BPM
    est_motion = estimator.estimate_rate(wave, is_empty_bed=False, is_motion=True)
    assert est_motion.bpm is None
    assert not est_motion.is_valid
