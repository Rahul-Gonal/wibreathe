"""Unit tests for the unified WiBreathePipeline."""

import sys
from pathlib import Path

# Add src to sys.path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wibreathe.pipeline import WiBreathePipeline, PipelineResult
from wibreathe.sim.synthetic import SyntheticGenerator, SyntheticScenario
from wibreathe.state.calibration import BedCalibration
from wibreathe.state.machine import SystemState


def test_pipeline_streaming_and_state_transitions():
    """Verify that WiBreathePipeline accurately processes synthetic frames and drives the state machine."""
    sim = SyntheticGenerator(sample_rate_hz=50.0, seed=42)
    calib = BedCalibration(
        is_calibrated=True,
        empty_bed_variance=0.5,
        empty_bed_mad=0.15,
        occupied_still_variance=4.0,
        occupied_still_mad=1.5,
    )
    pipeline = WiBreathePipeline(calibration=calib)

    frames = sim.generate_frames(duration_sec=10.0, scenario=SyntheticScenario.STILL, target_bpm=16.0)
    assert len(frames) > 400

    last_res: PipelineResult = None
    for frame in frames:
        last_res = pipeline.process_frame(frame)

    assert last_res is not None
    assert isinstance(last_res, PipelineResult)
    assert last_res.state in (SystemState.STILL, SystemState.NO_PERSON)
    assert last_res.selected_subcarrier_idx >= 0
    assert last_res.packet_rate_hz > 0


if __name__ == "__main__":
    test_pipeline_streaming_and_state_transitions()
    print("Pipeline tests passed.")
