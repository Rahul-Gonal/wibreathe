"""Unit tests for WiBreatheStateMachine and bed-exit logic."""

from wibreathe.config import StateMachineConfig
from wibreathe.state.calibration import BedCalibration
from wibreathe.state.machine import SystemState, WiBreatheStateMachine


def test_state_machine_initialization_and_calib():
    # Uncalibrated starts in NO_DATA / CALIBRATING
    calib_uncal = BedCalibration(is_calibrated=False)
    sm = WiBreatheStateMachine(calibration=calib_uncal)
    assert sm.current_state == SystemState.NO_DATA

    sm.update(timestamp_s=1.0, is_packet_received=True, current_variance=0.02, is_motion=False)
    assert sm.current_state == SystemState.CALIBRATING

    # Once calibrated, moves to NO_PERSON
    sm.calibration.is_calibrated = True
    sm.update(timestamp_s=2.0, is_packet_received=True, current_variance=0.02, is_motion=False)
    assert sm.current_state == SystemState.NO_PERSON


def test_occupancy_and_motion_transitions():
    calib = BedCalibration(empty_bed_variance=0.02, empty_bed_mad=0.01, is_calibrated=True)
    sm = WiBreatheStateMachine(calibration=calib)
    assert sm.current_state == SystemState.NO_PERSON

    # Person enters bed (variance rises and confidence appears)
    sm.update(timestamp_s=1.0, is_packet_received=True, current_variance=0.15, is_motion=False, bpm_confidence=75.0)
    assert sm.current_state == SystemState.STILL

    # Person moves in bed
    sm.update(timestamp_s=2.0, is_packet_received=True, current_variance=1.5, is_motion=True, bpm_confidence=10.0)
    assert sm.current_state == SystemState.MOVING

    # Person settles down again
    sm.update(timestamp_s=3.0, is_packet_received=True, current_variance=0.12, is_motion=False, bpm_confidence=60.0)
    assert sm.current_state == SystemState.STILL


def test_bed_exit_sequence():
    cfg = StateMachineConfig(bed_exit_confirmation_sec=4.0)
    calib = BedCalibration(empty_bed_variance=0.02, empty_bed_mad=0.01, is_calibrated=True)
    sm = WiBreatheStateMachine(config=cfg, calibration=calib)

    # 1. Occupant in bed breathing
    sm.update(timestamp_s=1.0, is_packet_received=True, current_variance=0.15, is_motion=False, bpm_confidence=80.0)
    assert sm.current_state == SystemState.STILL

    # 2. Movement burst (sitting up, swinging legs out of bed)
    sm.update(timestamp_s=2.0, is_packet_received=True, current_variance=2.0, is_motion=True, bpm_confidence=0.0)
    assert sm.current_state == SystemState.MOVING

    # 3. Movement ends, bed is now empty (low variance, no breathing confidence)
    # Sustain empty signature for 3 seconds (< 4.0s confirmation)
    for t_step in range(30):
        t = 3.0 + t_step * 0.1
        sm.update(timestamp_s=t, is_packet_received=True, current_variance=0.02, is_motion=False, bpm_confidence=0.0)
    # Should still be in MOVING while waiting for confirmation duration
    assert sm.current_state == SystemState.MOVING

    # Sustain past 4.0s (up to 7.5s)
    for t_step in range(30, 45):
        t = 3.0 + t_step * 0.1
        sm.update(timestamp_s=t, is_packet_received=True, current_variance=0.02, is_motion=False, bpm_confidence=0.0)

    # Bed-exit confirmed!
    assert sm.current_state == SystemState.EXIT


def test_packet_timeout_to_no_data():
    calib = BedCalibration(is_calibrated=True)
    cfg = StateMachineConfig(no_data_timeout_sec=3.0)
    sm = WiBreatheStateMachine(config=cfg, calibration=calib)

    sm.update(timestamp_s=1.0, is_packet_received=True, current_variance=0.15, is_motion=False)
    assert sm.current_state == SystemState.STILL

    # Stop receiving packets for 4 seconds (> 3.0s timeout)
    sm.update(timestamp_s=5.5, is_packet_received=False, current_variance=0.0, is_motion=False)
    assert sm.current_state == SystemState.NO_DATA
