"""Unit tests for subcarrier selection and data-driven null detection."""

import math
from wibreathe.dsp.subcarriers import SubcarrierSelector


def test_empirical_null_subcarrier_detection():
    """Verify that dead/null subcarriers with near-zero amplitude are detected dynamically."""
    # Create 50 time steps x 64 subcarriers
    # Subcarriers 0, 1, 32, 63 have zero amplitude; others have normal signal
    matrix = []
    for t in range(50):
        row = [10.0 + math.sin(t * 0.1 + k) for k in range(64)]
        row[0] = 0.0
        row[1] = 0.0001
        row[32] = 0.0
        row[63] = 0.0002
        matrix.append(row)

    detected_nulls = SubcarrierSelector.detect_null_subcarriers(matrix, threshold_ratio=0.05)
    assert 0 in detected_nulls
    assert 1 in detected_nulls
    assert 32 in detected_nulls
    assert 63 in detected_nulls
    # Normal subcarrier (e.g. index 15) must not be flagged
    assert 15 not in detected_nulls


def test_subcarrier_ranking_and_selection():
    """Verify that subcarriers with highest respiration modulation variance are ranked top."""
    selector = SubcarrierSelector(raw_subcarrier_count=64, top_k=3, method="variance")
    matrix = []
    for t in range(50):
        row = [5.0 for _ in range(64)]
        # Subcarrier 20 has large sinusoidal modulation
        row[20] = 5.0 + 3.0 * math.sin(t * 0.2)
        # Subcarrier 25 has moderate modulation
        row[25] = 5.0 + 1.5 * math.sin(t * 0.2)
        # Subcarrier 30 has tiny modulation
        row[30] = 5.0 + 0.5 * math.sin(t * 0.2)
        matrix.append(row)

    top3 = selector.update_selection(matrix)
    assert len(top3) == 3
    assert top3[0] == 20  # Highest variance
    assert top3[1] == 25  # Second highest
    assert top3[2] == 30  # Third highest


def test_combine_channels():
    """Verify combining multiple subcarriers into a 1D timeseries."""
    selector = SubcarrierSelector(raw_subcarrier_count=64, top_k=2)
    matrix = [[1.0, 2.0] * 32 for _ in range(20)]
    combined = selector.combine_channels(matrix, selected_indices=[10, 11])
    assert len(combined) == 20


def test_subcarrier_decoy_rejection_and_accuracy():
    """Verify that decoy subcarriers with high variance but out-of-band energy are rejected,

    requiring 10, 15 and 22 bpm estimated within +/- 1 bpm after 40s of simulated data.
    """
    import random
    from wibreathe.config import load_config
    from wibreathe.io.parser import CSIFrame
    from wibreathe.pipeline import WiBreathePipeline
    from wibreathe.state.calibration import BedCalibration

    cfg = load_config()
    cfg.subcarriers.selection_method = "snr"

    for target_bpm in [10.0, 15.0, 22.0]:
        calib = BedCalibration(is_calibrated=True, empty_calibrated=True, occupied_calibrated=True)
        pipe = WiBreathePipeline(config=cfg, calibration=calib)
        f_hz = target_bpm / 60.0
        rng = random.Random(42)
        last_bpm = None

        # 40 seconds at 50 Hz = 2000 frames
        for i in range(2000):
            t = i / 50.0
            amps = [10.0] * 64
            # Decoy subcarriers with massive variance but no breathing modulation
            amps[12] = 10.0 + 15.0 * math.sin(2 * math.pi * 3.5 * t) + rng.uniform(-5, 5)
            amps[28] = 10.0 + 20.0 * rng.uniform(-1, 1)
            amps[44] = 10.0 + 12.0 * math.sin(2 * math.pi * 8.0 * t)

            # Null subcarriers
            for n in [0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]:
                amps[n] = 0.0

            # True breathing subcarrier at index 20
            amps[20] = 10.0 + 3.0 * math.sin(2 * math.pi * f_hz * t)

            frame = CSIFrame(
                host_mono_time=t,
                host_wall_time=t,
                device_raw_timestamp_us=int(t * 1e6),
                device_unwrapped_time_s=t,
                packet_id=i,
                mac="aa:bb:cc:dd:ee:ff",
                rssi=-50,
                rate=0,
                channel=6,
                noise_floor=-90,
                subcarrier_len=64,
                raw_bytes=[int(a) for a in amps] * 2,
                complex_csi=[complex(a, 0.0) for a in amps],
                amplitudes=amps,
                phases=[0.0] * 64,
            )
            res = pipe.process_frame(frame)
            if res.bpm is not None:
                last_bpm = res.bpm

        assert last_bpm is not None, f"No BPM estimated for target {target_bpm}"
        error = abs(last_bpm - target_bpm)
        assert error <= 1.0, f"Target {target_bpm} bpm had error {error:.2f} > 1.0 bpm (got {last_bpm:.2f})"


if __name__ == "__main__":
    test_empirical_null_subcarrier_detection()
    test_subcarrier_ranking_and_selection()
    test_combine_channels()
    test_subcarrier_decoy_rejection_and_accuracy()
    print("Subcarrier tests passed.")
