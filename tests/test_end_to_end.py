"""End-to-End integration test: Simulate -> Record -> Replay -> DSP Pipeline -> Evaluate."""

import tempfile
from pathlib import Path

from wibreathe.dsp.bandpass import RespirationBandpass, detrend_series
from wibreathe.dsp.breathing import RespirationEstimator
from wibreathe.dsp.hampel import HampelFilter
from wibreathe.dsp.motion import MotionDetector
from wibreathe.dsp.resample import UniformResampler
from wibreathe.eval.align import align_reference_and_estimates
from wibreathe.eval.metrics import compute_evaluation_metrics
from wibreathe.eval.report import generate_evaluation_report
from wibreathe.io.recorder import CSIRecorder, RecordingMetadata
from wibreathe.io.replay import CSIReplay
from wibreathe.sim.synthetic import SyntheticGenerator, SyntheticScenario
from wibreathe.state.calibration import BedCalibration
from wibreathe.state.machine import SystemState, WiBreatheStateMachine


def test_end_to_end_still_breathing_pipeline():
    """Full pipeline execution on steady respiration ground truth."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        target_bpm = 16.0
        duration_s = 60.0

        # 1. Simulate and record steady breathing
        sim = SyntheticGenerator(sample_rate_hz=50.0, seed=42)
        csv_file = tmp_path / "raw_sim.csv"
        labels_file = tmp_path / "labels.csv"

        sim.generate_csv_and_labels(
            csv_output_path=csv_file,
            labels_output_path=labels_file,
            duration_sec=duration_s,
            scenario=SyntheticScenario.STILL,
            target_bpm=target_bpm,
        )

        assert csv_file.is_file()
        assert labels_file.is_file()

        # 2. Replay CSV through DSP pipeline
        replay = CSIReplay(csv_file, realtime=False)
        frames = list(replay.stream())
        assert len(frames) > 2000

        # Extract timestamps and representative subcarrier amplitude
        raw_times = [f.device_unwrapped_time_s for f in frames]
        # Choose sensitive subcarrier (index 15)
        raw_amps = [f.amplitudes[15] for f in frames]

        # 3. Resample to 20 Hz
        fs = 20.0
        t_uniform, v_uniform, mask = UniformResampler.resample_batch(
            raw_times, raw_amps, target_rate_hz=fs
        )
        assert len(t_uniform) > 1000

        # 4. Filter: Hampel, detrend, bandpass
        hf = HampelFilter(window_size=7, n_sigmas=3.0)
        v_clean = hf.filter_series(v_uniform, causal=True)
        v_detrend = detrend_series(v_clean, method="linear")

        bp = RespirationBandpass(sample_rate_hz=fs, low_cut_hz=0.1, high_cut_hz=0.5, order=4)
        v_filt = bp.filter_batch(v_detrend, zero_phase=False)

        # 5. Sliding window rate estimation and state machine
        # Realistic calibration values
        calib = BedCalibration(
            empty_bed_variance=0.50,
            empty_bed_mad=0.15,
            occupied_still_variance=4.0,
            occupied_still_mad=1.5,
            is_calibrated=True,
        )
        sm = WiBreatheStateMachine(calibration=calib)
        md = MotionDetector(sample_rate_hz=fs)
        md.set_calibration(baseline_median=4.0, baseline_mad=1.5)

        estimator = RespirationEstimator(sample_rate_hz=fs, min_confidence_score=25.0)

        win_samples = int(30.0 * fs)
        stride_samples = int(5.0 * fs)

        estimator_windows = []

        for start_i in range(0, len(v_filt) - win_samples + 1, stride_samples):
            sub_sig = v_filt[start_i : start_i + win_samples]
            center_t = t_uniform[start_i + win_samples // 2]

            # Step motion detector on window center
            m_state = md.push_sample(sub_sig[-1])

            # Rate estimation
            est = estimator.estimate_rate(
                sub_sig,
                is_empty_bed=False,
                is_motion=m_state.is_moving,
            )

            # Step state machine
            curr_state = sm.update(
                timestamp_s=center_t,
                is_packet_received=True,
                current_variance=m_state.current_variance,
                is_motion=m_state.is_moving,
                bpm_confidence=est.confidence,
            )

            estimator_windows.append((center_t, est.bpm, est.confidence, curr_state.value))

        # 6. Align with ground truth labels
        aligned = align_reference_and_estimates(
            estimator_windows=estimator_windows,
            reference_csv_path=labels_file,
            window_length_sec=30.0,
        )
        assert len(aligned) > 0

        # 7. Compute quantitative evaluation metrics
        resp_m, event_m = compute_evaluation_metrics(aligned)

        # 8. Assert explicit pass criteria
        # MAE must be < 0.5 bpm on clean synthetic respiration
        assert resp_m.mae < 0.5, f"Expected MAE < 0.5 bpm, got {resp_m.mae:.2f}"
        # Measurement coverage must be > 80%
        assert resp_m.coverage_pct >= 80.0, f"Expected coverage >= 80%, got {resp_m.coverage_pct:.1f}%"
        # Zero false exit alarms on steady breathing
        assert event_m.false_positives == 0, f"Expected 0 false alarms, got {event_m.false_positives}"

        # 9. Generate report files
        report_md = generate_evaluation_report(
            aligned, resp_m, event_m, output_dir=tmp_path / "report"
        )
        assert report_md.is_file()


def test_end_to_end_bed_exit_detection():
    """Verify bed-exit sequence triggers correctly from still -> motion -> empty."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        sim = SyntheticGenerator(sample_rate_hz=50.0, seed=99)
        csv_file = tmp_path / "bed_exit_raw.csv"
        labels_file = tmp_path / "bed_exit_labels.csv"

        # 60s scenario: 0-25s still, 25-30s motion, 30-60s empty
        sim.generate_csv_and_labels(
            csv_output_path=csv_file,
            labels_output_path=labels_file,
            duration_sec=60.0,
            scenario=SyntheticScenario.BED_EXIT,
            target_bpm=15.0,
        )

        replay = CSIReplay(csv_file, realtime=False)
        frames = list(replay.stream())

        raw_times = [f.device_unwrapped_time_s for f in frames]
        raw_amps = [f.amplitudes[15] for f in frames]

        fs = 20.0
        t_uniform, v_uniform, _ = UniformResampler.resample_batch(
            raw_times, raw_amps, target_rate_hz=fs
        )

        # Realistic calibration values
        calib = BedCalibration(
            empty_bed_variance=0.50,
            empty_bed_mad=0.15,
            occupied_still_variance=4.0,
            occupied_still_mad=1.5,
            is_calibrated=True,
        )
        sm = WiBreatheStateMachine(calibration=calib)
        md = MotionDetector(sample_rate_hz=fs)
        md.set_calibration(baseline_median=4.0, baseline_mad=1.5)

        states_seen = set()

        for t, val in zip(t_uniform, v_uniform):
            m_state = md.push_sample(val)
            s = sm.update(
                timestamp_s=t,
                is_packet_received=True,
                current_variance=m_state.current_variance,
                is_motion=m_state.is_moving,
                bpm_confidence=70.0 if t < 25.0 else 0.0,
            )
            states_seen.add(s)

        # Confirm EXIT state was successfully triggered
        assert SystemState.STILL in states_seen
        assert SystemState.MOVING in states_seen
        assert SystemState.EXIT in states_seen
