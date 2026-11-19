"""Unit tests for CSIRecorder, metadata validation, and CSIReplay."""

import tempfile
from pathlib import Path

try:
    import pytest
except ImportError:
    class _PytestMock:
        class raises:
            def __init__(self, exc_type):
                self.exc_type = exc_type
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    raise AssertionError(f"Expected {self.exc_type} was not raised")
                return issubclass(exc_type, self.exc_type)
    pytest = _PytestMock()  # type: ignore

from wibreathe.io.recorder import CSIRecorder, RecordingMetadata
from wibreathe.io.replay import CSIReplay
from wibreathe.sim.synthetic import SyntheticGenerator, SyntheticScenario


def test_volunteer_code_validation():
    # Valid participant codes
    m_valid = RecordingMetadata(trial_id="T01", volunteer_code="V01", consent_obtained=True)
    m_valid.validate()

    m_valid3 = RecordingMetadata(trial_id="T02", volunteer_code="V105", consent_obtained=True)
    m_valid3.validate()

    # Invalid names or formats
    with pytest.raises(ValueError):
        RecordingMetadata(trial_id="T03", volunteer_code="JohnDoe", consent_obtained=True).validate()

    with pytest.raises(ValueError):
        RecordingMetadata(trial_id="T04", volunteer_code="123", consent_obtained=True).validate()

    # Missing consent must raise error
    with pytest.raises(ValueError):
        RecordingMetadata(trial_id="T05", volunteer_code="V05", consent_obtained=False).validate()


def test_record_and_replay_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        meta = RecordingMetadata(
            trial_id="T_TEST_01",
            volunteer_code="V01",
            breathing_mode="normal",
            consent_obtained=True,
        )
        recorder = CSIRecorder(output_dir=tmpdir, metadata=meta, flush_interval_packets=10)

        sim = SyntheticGenerator(sample_rate_hz=50.0, seed=123)
        generated_frames = []
        for frame, _ in sim.generate_stream(duration_sec=1.0, scenario=SyntheticScenario.STILL):
            recorder.record_frame(frame)
            generated_frames.append(frame)

        recorder.close()
        assert recorder.current_csv_path is not None
        assert recorder.current_csv_path.is_file()

        # Replay the written CSV
        replay = CSIReplay(csv_path=recorder.current_csv_path, realtime=False)
        replayed_frames = list(replay.stream())

        assert len(replayed_frames) == len(generated_frames)

        # Check exact field matches
        for orig, rep in zip(generated_frames, replayed_frames):
            assert orig.packet_id == rep.packet_id
            assert orig.mac == rep.mac
            assert orig.subcarrier_len == rep.subcarrier_len
            assert orig.raw_bytes == rep.raw_bytes
            assert orig.complex_csi == rep.complex_csi
