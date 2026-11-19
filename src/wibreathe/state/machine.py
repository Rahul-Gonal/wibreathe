"""Hierarchical state machine for respiration and bed-exit event classification.

State Transition Table:
-----------------------------------------------------------------------------------------
Current State | Condition / Event                               | Target State | Notes
-----------------------------------------------------------------------------------------
Any           | Time since last packet > no_data_timeout_sec   | NO_DATA      | Link dropped
NO_DATA       | Packet arrived, uncalibrated                   | CALIBRATING  | Awaiting profile
NO_DATA       | Packet arrived, calibrated                     | NO_PERSON    | Initializing
CALIBRATING   | Calibration routine completed                  | NO_PERSON    | Ready
NO_PERSON     | Occupancy detected (variance > empty_thresh)   | STILL        | Person entered
STILL         | High variance (motion_detected == True)        | MOVING       | In-bed movement
MOVING        | Motion ends, variance matches empty for T_exit | EXIT         | Bed-exit event
MOVING        | Motion ends, variance matches occupied still   | STILL        | Stillness resumed
EXIT          | Empty signature persists > reset_time          | NO_PERSON    | Bed remains empty
EXIT          | New motion/occupancy detected                  | STILL/MOVING | Returned to bed
-----------------------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from wibreathe.config import StateMachineConfig
from wibreathe.state.calibration import BedCalibration

logger = logging.getLogger(__name__)


class SystemState(str, Enum):
    NO_DATA = "NO_DATA"
    CALIBRATING = "CALIBRATING"
    NO_PERSON = "NO_PERSON"
    STILL = "STILL"
    MOVING = "MOVING"
    EXIT = "EXIT"


@dataclass
class StateEvent:
    timestamp_s: float
    previous_state: SystemState
    new_state: SystemState
    reason: str


class WiBreatheStateMachine:
    """Manages system occupancy, respiration availability, and bed-exit sequence tracking."""

    def __init__(
        self,
        config: Optional[StateMachineConfig] = None,
        calibration: Optional[BedCalibration] = None,
    ):
        self.config = config or StateMachineConfig()
        self.calibration = calibration or BedCalibration()

        self.current_state: SystemState = (
            SystemState.NO_DATA if not self.calibration.is_calibrated else SystemState.NO_PERSON
        )
        self.last_state_change_s: float = 0.0
        self.events: List[StateEvent] = []

        # Internal sequence timers
        self._last_packet_time_s: Optional[float] = None
        self._motion_burst_seen: bool = False
        self._empty_post_motion_duration_s: float = 0.0
        self._exit_reset_duration_s: float = 0.0

    def _transition(self, new_state: SystemState, timestamp_s: float, reason: str) -> None:
        if new_state == self.current_state:
            return
        event = StateEvent(
            timestamp_s=timestamp_s,
            previous_state=self.current_state,
            new_state=new_state,
            reason=reason,
        )
        self.events.append(event)
        logger.info(
            "State transition: %s -> %s at %.2fs (Reason: %s)",
            self.current_state.value,
            new_state.value,
            timestamp_s,
            reason,
        )
        self.current_state = new_state
        self.last_state_change_s = timestamp_s

    def update(
        self,
        timestamp_s: float,
        is_packet_received: bool,
        current_variance: float,
        is_motion: bool,
        bpm_confidence: float = 0.0,
    ) -> SystemState:
        """Step state machine given recent physical features."""
        dt = (timestamp_s - self._last_packet_time_s) if self._last_packet_time_s is not None else 0.0

        # 1. Check for data starvation timeout
        if is_packet_received:
            self._last_packet_time_s = timestamp_s
        else:
            if (
                self._last_packet_time_s is not None
                and (timestamp_s - self._last_packet_time_s) > self.config.no_data_timeout_sec
            ):
                self._transition(SystemState.NO_DATA, timestamp_s, "Packet stream timeout")
                return self.current_state

        if self.current_state == SystemState.NO_DATA:
            if is_packet_received:
                if not self.calibration.is_calibrated:
                    self._transition(SystemState.CALIBRATING, timestamp_s, "Awaiting calibration")
                else:
                    self._transition(SystemState.NO_PERSON, timestamp_s, "Packets resumed")
            return self.current_state

        if self.current_state == SystemState.CALIBRATING:
            if self.calibration.is_calibrated:
                self._transition(SystemState.NO_PERSON, timestamp_s, "Calibration finalized")
            return self.current_state

        # Empty bed threshold: empty_bed_variance plus cushion
        empty_thresh = max(
            self.config.empty_bed_variance_threshold,
            self.calibration.empty_bed_variance + 1.5 * self.calibration.empty_bed_mad,
        )
        is_empty_signature = current_variance <= empty_thresh and bpm_confidence < 30.0

        # State evaluations
        if self.current_state == SystemState.NO_PERSON:
            # Check for bed entry: significant variance rise or respiration detected
            if current_variance > empty_thresh * 1.5 or bpm_confidence >= 40.0:
                if is_motion:
                    self._transition(SystemState.MOVING, timestamp_s, "Bed entry movement detected")
                else:
                    self._transition(SystemState.STILL, timestamp_s, "Person detected still in bed")

        elif self.current_state == SystemState.STILL:
            if is_motion:
                self._motion_burst_seen = True
                self._empty_post_motion_duration_s = 0.0
                self._transition(SystemState.MOVING, timestamp_s, "Body movement initiated")
            elif is_empty_signature and (timestamp_s - self.last_state_change_s) > 10.0:
                # Spontaneous drop to empty without motion
                self._transition(SystemState.NO_PERSON, timestamp_s, "Bed empty signature confirmed")

        elif self.current_state == SystemState.MOVING:
            if is_motion:
                self._motion_burst_seen = True
                self._empty_post_motion_duration_s = 0.0
            else:
                # Movement stopped: check whether bed is empty (Bed-Exit) or still occupied
                if is_empty_signature:
                    self._empty_post_motion_duration_s += max(0.0, dt)
                    # When empty signature sustains for bed_exit_confirmation_sec -> EXIT
                    if self._empty_post_motion_duration_s >= self.config.bed_exit_confirmation_sec:
                        self._transition(
                            SystemState.EXIT,
                            timestamp_s,
                            f"Movement followed by sustained empty signature for {self.config.bed_exit_confirmation_sec}s",
                        )
                        self._motion_burst_seen = False
                        self._empty_post_motion_duration_s = 0.0
                        self._exit_reset_duration_s = 0.0
                else:
                    # Settled back down in bed
                    self._motion_burst_seen = False
                    self._empty_post_motion_duration_s = 0.0
                    self._transition(SystemState.STILL, timestamp_s, "Movement ceased; occupant settled")

        elif self.current_state == SystemState.EXIT:
            if is_motion or (current_variance > empty_thresh * 2.0 and bpm_confidence >= 40.0):
                self._transition(SystemState.STILL, timestamp_s, "Occupant returned to bed")
            else:
                self._exit_reset_duration_s += max(0.0, dt)
                if self._exit_reset_duration_s >= 15.0:
                    self._transition(SystemState.NO_PERSON, timestamp_s, "Bed exit acknowledged; reset to NO_PERSON")

        return self.current_state
