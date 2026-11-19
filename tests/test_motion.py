"""Unit tests for MotionDetector and hysteresis."""

import math
from wibreathe.dsp.motion import MotionDetector


def test_motion_detector_burst_and_recovery():
    md = MotionDetector(
        sample_rate_hz=20.0,
        short_window_sec=1.0,
        min_duration_sec=0.5,
    )
    md.set_calibration(baseline_median=0.05, baseline_mad=0.02)

    # 1. Feed quiet baseline for 2 seconds (40 samples)
    for _ in range(40):
        state = md.push_sample(10.0 + 0.05)
    assert not state.is_moving

    # 2. Feed high-amplitude motion burst for 1.5 seconds (30 samples)
    for i in range(30):
        val = 10.0 + (15.0 if i % 2 == 0 else -15.0)
        state = md.push_sample(val)
    assert state.is_moving

    # 3. Return to quiet baseline for 2 seconds (40 samples)
    for _ in range(40):
        state = md.push_sample(10.0 + 0.05)
    assert not state.is_moving
