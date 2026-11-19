"""Realistic synthetic CSI generator with ground-truth labels for hardware-free testing."""

from __future__ import annotations

import csv
import json
import logging
import math
import random
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from wibreathe.io.parser import CSIFrame
from wibreathe.io.recorder import CSV_HEADER

logger = logging.getLogger(__name__)


class SyntheticScenario(str, Enum):
    STILL = "still"
    EMPTY = "empty"
    MOVEMENT = "movement"
    BED_EXIT = "bed_exit"


@dataclass
class GroundTruthInterval:
    start_time_s: float
    end_time_s: float
    state: str
    target_bpm: Optional[float]


class SyntheticGenerator:
    """Generates realistic synthetic CSI streams and CSV files with known ground truth."""

    def __init__(
        self,
        sample_rate_hz: float = 50.0,
        subcarrier_count: int = 64,
        seed: int = 42,
    ):
        self.sample_rate_hz = sample_rate_hz
        self.subcarrier_count = subcarrier_count
        self.dt = 1.0 / sample_rate_hz
        self.rng = random.Random(seed)

        # Setup null & guard bins: 0..5, 32, 59..63
        self.null_bins = {0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63}

        # Assign diverse baseline strengths and breathing couplings
        self.baseline_i = [0.0] * subcarrier_count
        self.baseline_q = [0.0] * subcarrier_count
        self.coupling_factors = [0.0] * subcarrier_count

        for k in range(subcarrier_count):
            if k in self.null_bins:
                self.baseline_i[k] = self.rng.uniform(-2.0, 2.0)
                self.baseline_q[k] = self.rng.uniform(-2.0, 2.0)
                self.coupling_factors[k] = 0.0
            else:
                amp = self.rng.uniform(15.0, 45.0)
                theta = self.rng.uniform(-math.pi, math.pi)
                self.baseline_i[k] = amp * math.cos(theta)
                self.baseline_q[k] = amp * math.sin(theta)
                # Some subcarriers have high sensitivity, some low, some inverted
                if k % 5 == 0:
                    self.coupling_factors[k] = self.rng.uniform(3.0, 6.0)
                elif k % 3 == 0:
                    self.coupling_factors[k] = self.rng.uniform(-4.0, -1.5)
                elif k % 2 == 0:
                    self.coupling_factors[k] = self.rng.uniform(0.5, 2.0)
                else:
                    self.coupling_factors[k] = 0.0

    def generate_stream(
        self,
        duration_sec: float = 60.0,
        scenario: SyntheticScenario = SyntheticScenario.STILL,
        target_bpm: float = 15.0,
        packet_loss_prob: float = 0.01,
        outlier_prob: float = 0.005,
    ) -> Generator[Tuple[CSIFrame, GroundTruthInterval], None, None]:
        """Generate a sequential stream of CSIFrame objects and matching ground truth."""
        total_steps = int(duration_sec * self.sample_rate_hz)
        breathing_freq_hz = target_bpm / 60.0

        current_time_s = 0.0
        packet_id = 1
        mac_addr = "1a:00:00:00:00:00"

        # Host time reference
        mono_start = time.monotonic()
        wall_start = time.time()

        for step in range(total_steps):
            # Optional packet loss drop
            if self.rng.random() < packet_loss_prob:
                current_time_s += self.dt
                packet_id += 1
                continue

            # Small timestamp jitter (+/- 2 ms)
            jitter = self.rng.uniform(-0.002, 0.002)
            frame_time = max(0.0, current_time_s + jitter)
            dev_raw_us = int((frame_time * 1_000_000)) % (1 << 32)

            # Determine instantaneous state & motion based on scenario
            is_motion = False
            is_breathing = False
            state_label = "STILL"
            current_bpm: Optional[float] = target_bpm

            if scenario == SyntheticScenario.STILL:
                is_breathing = True
                state_label = "STILL"
            elif scenario == SyntheticScenario.EMPTY:
                is_breathing = False
                state_label = "NO_PERSON"
                current_bpm = None
            elif scenario == SyntheticScenario.MOVEMENT:
                # Bursts of motion between 15s-20s and 40s-45s
                if 15.0 <= frame_time <= 20.0 or 40.0 <= frame_time <= 45.0:
                    is_motion = True
                    is_breathing = False
                    state_label = "MOVING"
                else:
                    is_breathing = True
                    state_label = "STILL"
            elif scenario == SyntheticScenario.BED_EXIT:
                # Still 0-25s, Motion 25-30s, Empty 30s-end
                if frame_time < 25.0:
                    is_breathing = True
                    state_label = "STILL"
                elif 25.0 <= frame_time <= 30.0:
                    is_motion = True
                    is_breathing = False
                    state_label = "MOVING"
                else:
                    is_breathing = False
                    state_label = "EXIT"
                    current_bpm = None

            gt_interval = GroundTruthInterval(
                start_time_s=frame_time,
                end_time_s=frame_time + self.dt,
                state=state_label,
                target_bpm=current_bpm,
            )

            # Compute breathing modulation: sinusoidal with subtle 2nd harmonic
            if is_breathing:
                breath_phase = 2.0 * math.pi * breathing_freq_hz * frame_time
                breath_signal = math.sin(breath_phase) + 0.15 * math.sin(2.0 * breath_phase)
            else:
                breath_signal = 0.0

            # Compute motion perturbation
            motion_offset = self.rng.uniform(-40.0, 40.0) if is_motion else 0.0

            # Generate subcarriers
            raw_bytes: List[int] = []
            complex_csi: List[complex] = []
            amplitudes: List[float] = []
            phases: List[float] = []

            is_outlier_frame = self.rng.random() < outlier_prob

            for k in range(self.subcarrier_count):
                # Baseline + slow drift + breathing + noise
                slow_drift = 1.5 * math.sin(2.0 * math.pi * 0.01 * frame_time + k * 0.1)
                noise_i = self.rng.gauss(0.0, 0.6)
                noise_q = self.rng.gauss(0.0, 0.6)

                i_val = self.baseline_i[k] + slow_drift + noise_i
                q_val = self.baseline_q[k] + slow_drift + noise_q

                if is_breathing and k not in self.null_bins:
                    i_val += self.coupling_factors[k] * breath_signal
                    q_val += self.coupling_factors[k] * breath_signal * 0.5

                if is_motion and k not in self.null_bins:
                    i_val += motion_offset + self.rng.gauss(0.0, 15.0)
                    q_val += motion_offset + self.rng.gauss(0.0, 15.0)

                if is_outlier_frame and k == 18:
                    i_val += 60.0
                    q_val += 60.0

                # Clamp to signed int8 (-128 to 127)
                i_clamped = max(-128, min(127, int(round(i_val))))
                q_clamped = max(-128, min(127, int(round(q_val))))

                # Ground truth order: [Q0, I0, Q1, I1, ...]
                raw_bytes.append(q_clamped)
                raw_bytes.append(i_clamped)

                c_val = complex(float(i_clamped), float(q_clamped))
                complex_csi.append(c_val)
                amplitudes.append(abs(c_val))
                phases.append(math.atan2(float(q_clamped), float(i_clamped)))

            frame = CSIFrame(
                host_mono_time=mono_start + frame_time,
                host_wall_time=wall_start + frame_time,
                device_raw_timestamp_us=dev_raw_us,
                device_unwrapped_time_s=frame_time,
                packet_id=packet_id,
                mac=mac_addr,
                rssi=-45 if not is_motion else -52,
                rate=12,
                channel=6,
                noise_floor=-92,
                subcarrier_len=self.subcarrier_count,
                raw_bytes=raw_bytes,
                complex_csi=complex_csi,
                amplitudes=amplitudes,
                phases=phases,
            )

            yield frame, gt_interval
            current_time_s += self.dt
            packet_id += 1

    def generate_csv_and_labels(
        self,
        csv_output_path: str | Path,
        labels_output_path: str | Path,
        duration_sec: float = 60.0,
        scenario: SyntheticScenario = SyntheticScenario.STILL,
        target_bpm: float = 15.0,
    ) -> Tuple[int, int]:
        """Generate recorded CSV file and separate ground-truth labels CSV."""
        csv_path = Path(csv_output_path)
        labels_path = Path(labels_output_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        labels_path.parent.mkdir(parents=True, exist_ok=True)

        frames_written = 0
        labels_written = 0

        with open(csv_path, "w", newline="", encoding="utf-8") as f_csv, open(
            labels_path, "w", newline="", encoding="utf-8"
        ) as f_lbl:
            writer = csv.writer(f_csv)
            writer.writerow(CSV_HEADER)

            lbl_writer = csv.writer(f_lbl)
            lbl_writer.writerow(["start_time_s", "end_time_s", "state", "target_bpm"])

            for frame, gt in self.generate_stream(
                duration_sec=duration_sec,
                scenario=scenario,
                target_bpm=target_bpm,
            ):
                raw_str = f"[{','.join(str(v) for v in frame.raw_bytes)}]"
                row = [
                    f"{frame.host_mono_time:.6f}",
                    f"{frame.host_wall_time:.6f}",
                    frame.device_raw_timestamp_us,
                    f"{frame.device_unwrapped_time_s:.6f}",
                    frame.packet_id,
                    frame.mac,
                    frame.rssi,
                    frame.rate,
                    frame.channel,
                    frame.noise_floor,
                    len(frame.raw_bytes),
                    raw_str,
                ]
                writer.writerow(row)
                frames_written += 1

                lbl_writer.writerow([
                    f"{gt.start_time_s:.3f}",
                    f"{gt.end_time_s:.3f}",
                    gt.state,
                    f"{gt.target_bpm:.1f}" if gt.target_bpm else "--",
                ])
                labels_written += 1

        logger.info(
            "Wrote %d synthetic frames to %s and %d label rows to %s",
            frames_written,
            csv_path,
            labels_written,
            labels_path,
        )
        return frames_written, labels_written
