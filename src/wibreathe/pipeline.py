"""Unified WiBreathe processing pipeline.

Shared by live streaming, file replay, calibration, and integration tests.
Receives raw CSIFrame objects, runs DSP processing, updates the state machine,
and produces structured pipeline telemetry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from wibreathe.config import AppConfig, load_config
from wibreathe.dsp.bandpass import RespirationBandpass, detrend_series
from wibreathe.dsp.breathing import RespirationEstimator
from wibreathe.dsp.hampel import HampelFilter
from wibreathe.dsp.motion import MotionDetector
from wibreathe.dsp.quality import QualityMonitor
from wibreathe.dsp.resample import UniformResampler
from wibreathe.dsp.subcarriers import SubcarrierSelector
from wibreathe.io.parser import CSIFrame
from wibreathe.state.calibration import BedCalibration
from wibreathe.state.machine import SystemState, WiBreatheStateMachine

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Telemetry produced for a single processing epoch or packet."""
    timestamp_s: float
    state: SystemState
    bpm: Optional[float]
    confidence: float
    motion_metric: float
    selected_subcarrier_idx: int
    is_empty_bed: bool
    packet_rate_hz: float
    packet_loss_pct: float
    rssi: int


class WiBreathePipeline:
    """End-to-end processing pipeline for WiBreathe CSI stream."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        calibration: Optional[BedCalibration] = None,
    ):
        self.config = config or load_config()
        self.calibration = calibration or BedCalibration()

        # DSP components
        self.quality = QualityMonitor(window_duration_s=2.0)
        self.subcarrier_sel = SubcarrierSelector(
            null_indices=self.config.subcarriers.null_indices,
            method=self.config.subcarriers.selection_method,
            top_k=self.config.subcarriers.top_k,
            sample_rate_hz=50.0,
            hysteresis_ratio=0.20,
        )
        self.hampel = HampelFilter(
            window_size=self.config.hampel.window_size,
            n_sigmas=self.config.hampel.n_sigmas,
            min_deviation_floor=self.config.hampel.min_deviation_floor,
            relative_floor_ratio=getattr(self.config.hampel, "relative_floor_ratio", 0.05),
        )
        self.resampler = UniformResampler(
            target_rate_hz=self.config.resample.target_rate_hz,
            max_gap_s=self.config.resample.max_gap_s,
        )
        self.bandpass = RespirationBandpass(
            sample_rate_hz=self.config.resample.target_rate_hz,
            low_cut_hz=self.config.bandpass.lowcut_hz,
            high_cut_hz=self.config.bandpass.highcut_hz,
            order=self.config.bandpass.order,
        )
        self.estimator = RespirationEstimator(
            sample_rate_hz=self.config.resample.target_rate_hz,
            window_duration_s=self.config.breathing.window_duration_s,
            step_duration_s=self.config.breathing.step_duration_s,
            min_bpm=self.config.breathing.min_bpm,
            max_bpm=self.config.breathing.max_bpm,
            fft_zero_pad_factor=self.config.breathing.fft_zero_pad_factor,
        )
        self.motion_det = MotionDetector(
            low_threshold=self.config.motion.mad_low_threshold,
            high_threshold=self.config.motion.mad_high_threshold,
            window_duration_s=self.config.motion.window_duration_s,
        )
        if self.calibration.is_calibrated:
            self.motion_det.set_calibration(
                baseline_median=self.calibration.occupied_still_variance,
                baseline_mad=self.calibration.occupied_still_mad,
            )
        self.state_machine = WiBreatheStateMachine(
            calibration=self.calibration,
            config=self.config.state_machine,
        )

        # Buffers for subcarrier batch selection and sliding estimation
        self._raw_timestamps: List[float] = []
        self._raw_subcarrier_matrices: List[List[float]] = []
        self._current_subcarrier_idx: int = 15  # Default sensitive carrier
        self._latest_bpm: Optional[float] = None
        self._latest_conf: float = 0.0
        self._latest_motion: float = 0.0

    def process_frame(self, frame: CSIFrame) -> PipelineResult:
        """Process one incoming raw CSI frame through the full pipeline."""
        t_s = frame.device_unwrapped_time_s
        self.quality.add_packet(frame)

        # 1. Motion detection on multi-subcarrier amplitudes
        self.motion_det.add_frame(frame)
        self._latest_motion = self.motion_det.current_mad

        # 2. Accumulate subcarriers for selection
        self._raw_timestamps.append(t_s)
        self._raw_subcarrier_matrices.append(frame.amplitudes)

        # Periodically select best subcarrier (every 250 frames / 5 seconds)
        if len(self._raw_subcarrier_matrices) >= 250 and len(self._raw_subcarrier_matrices) % 250 == 0:
            top_indices = self.subcarrier_sel.select_top_k(self._raw_subcarrier_matrices[-250:])
            if top_indices:
                new_idx = top_indices[0]
                if new_idx != self._current_subcarrier_idx:
                    logger.info("Switching active subcarrier: %d -> %d", self._current_subcarrier_idx, new_idx)
                    self._current_subcarrier_idx = new_idx
                    # Reset filter states when switching to avoid DC step artifact
                    self.hampel.reset()
                    self.bandpass.reset()

        # Extract scalar amplitude for chosen subcarrier
        raw_amp = frame.amplitudes[self._current_subcarrier_idx]

        # 3. Causal Hampel filter
        clean_amp = self.hampel.filter_sample(raw_amp)

        # 4. Uniform resample & causal bandpass filter
        resampled_samples = self.resampler.add_sample(t_s, clean_amp)
        for t_uni, val_uni in resampled_samples:
            filtered_val = self.bandpass.filter_sample(val_uni)
            # Add to respiration window estimator
            est = self.estimator.add_sample(t_uni, filtered_val)
            if est is not None:
                self._latest_bpm, self._latest_conf = est

        # 5. Check empty bed signature using calibration baseline
        is_empty = False
        if self.calibration.is_calibrated and self.calibration.empty_bed_variance > 0:
            # Low variance across subcarriers indicates empty bed
            recent_amps = frame.amplitudes
            mean_a = sum(recent_amps) / len(recent_amps)
            cur_var = sum((a - mean_a) ** 2 for a in recent_amps) / len(recent_amps)
            if cur_var < self.calibration.empty_bed_variance * 1.5:
                is_empty = True

        # 6. Update state machine
        is_motion_active = self.motion_det.is_moving
        state = self.state_machine.update(
            timestamp_s=t_s,
            is_packet_received=True,
            current_variance=self._latest_motion,
            is_motion=is_motion_active,
            bpm_confidence=self._latest_conf,
        )

        # In STILL, or if confidence >= 25% during streaming replay, report BPM
        report_bpm = (
            self._latest_bpm
            if (state == SystemState.STILL or (state != SystemState.NO_DATA and self._latest_conf >= 25.0))
            else None
        )

        return PipelineResult(
            timestamp_s=t_s,
            state=state,
            bpm=report_bpm,
            confidence=self._latest_conf,
            motion_metric=self._latest_motion,
            selected_subcarrier_idx=self._current_subcarrier_idx,
            is_empty_bed=is_empty,
            packet_rate_hz=self.quality.packet_rate_hz,
            packet_loss_pct=self.quality.packet_loss_pct,
            rssi=frame.rssi,
        )
