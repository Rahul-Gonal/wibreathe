"""Raw CSI data recorder with automatic file rotation and study metadata sidecars."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from wibreathe import __version__
from wibreathe.config import AppConfig
from wibreathe.io.parser import CSIFrame

logger = logging.getLogger(__name__)

VOLUNTEER_REGEX = re.compile(r"^V\d{2,3}$")

CSV_HEADER = [
    "host_mono_time",
    "host_wall_time",
    "device_raw_timestamp_us",
    "device_unwrapped_time_s",
    "packet_id",
    "mac",
    "rssi",
    "rate",
    "channel",
    "noise_floor",
    "len",
    "data",
]


@dataclass
class RecordingMetadata:
    """Participant study metadata accompanying recorded raw CSI data."""
    trial_id: str
    volunteer_code: str                     # Must match V01, V02, etc. No names allowed.
    breathing_mode: str = "normal"          # normal, slow, fast, hold, empty, movement, bed_exit
    posture: str = "supine"                 # supine, prone, lateral_left, lateral_right, seated
    position: str = "center"                # center, left, right
    consent_obtained: bool = True           # Must be explicitly verified
    port: str = "unknown"
    notes: str = ""
    start_time_iso: str = ""
    end_time_iso: str = ""
    total_frames_recorded: int = 0
    package_version: str = __version__
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not VOLUNTEER_REGEX.match(self.volunteer_code):
            raise ValueError(
                f"Invalid volunteer_code '{self.volunteer_code}'. Must match regex ^V[0-9]{{2,3}}$ (e.g. V01)"
            )
        if not self.consent_obtained:
            raise ValueError("Data recording cannot proceed without documented consent")


class CSIRecorder:
    """Records raw CSIFrame streams to timestamped CSV files with periodic flush and rotation."""

    def __init__(
        self,
        output_dir: str | Path,
        metadata: RecordingMetadata,
        config: Optional[AppConfig] = None,
        flush_interval_packets: int = 100,
        max_file_size_mb: int = 50,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata = metadata
        self.metadata.validate()
        self.config = config

        self.flush_interval_packets = flush_interval_packets
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024

        self.part_index: int = 1
        self.frames_in_current_file: int = 0
        self.total_recorded_frames: int = 0
        self._csv_file = None
        self._csv_writer = None
        self.current_csv_path: Optional[Path] = None
        self.sidecar_path: Optional[Path] = None

        now_utc = datetime.now(timezone.utc)
        self.timestamp_str = now_utc.strftime("%Y%m%d_%H%M%S")
        self.metadata.start_time_iso = now_utc.isoformat()
        if self.config:
            self.metadata.config_snapshot = {
                "sample_rate_hz": self.config.system.sample_rate_hz,
                "bandpass": [self.config.filter.bandpass_low_hz, self.config.filter.bandpass_high_hz],
                "subcarriers_selected": self.config.subcarriers.num_selected,
            }

        self._open_new_segment()

    def _open_new_segment(self) -> None:
        """Create and initialize a new CSV segment."""
        self._close_segment()
        filename = (
            f"csi_{self.metadata.volunteer_code}_{self.metadata.breathing_mode}_"
            f"{self.timestamp_str}_part{self.part_index:02d}.csv"
        )
        self.current_csv_path = self.output_dir / filename
        self._csv_file = open(self.current_csv_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(CSV_HEADER)
        self.frames_in_current_file = 0
        self.part_index += 1
        logger.info("Recording to new CSV file segment: %s", self.current_csv_path)

        # Write sidecar YAML alongside
        self._write_sidecar()

    def _close_segment(self) -> None:
        if self._csv_file and not self._csv_file.closed:
            try:
                self._csv_file.flush()
                self._csv_file.close()
            except Exception as e:
                logger.error("Error closing CSV segment: %s", e)
            self._csv_file = None
            self._csv_writer = None

    def record_frame(self, frame: CSIFrame) -> None:
        """Append parsed frame to active CSV."""
        if self._csv_writer is None or self._csv_file is None:
            return

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
        self._csv_writer.writerow(row)
        self.frames_in_current_file += 1
        self.total_recorded_frames += 1

        if self.frames_in_current_file % self.flush_interval_packets == 0:
            self._csv_file.flush()
            if self.current_csv_path and self.current_csv_path.stat().st_size >= self.max_file_size_bytes:
                self._open_new_segment()

    def _write_sidecar(self) -> None:
        """Write sidecar metadata to file (YAML if available, else JSON)."""
        sidecar_base = (
            f"csi_{self.metadata.volunteer_code}_{self.metadata.breathing_mode}_{self.timestamp_str}.meta"
        )
        self.metadata.total_frames_recorded = self.total_recorded_frames
        meta_dict = asdict(self.metadata)

        if yaml is not None:
            self.sidecar_path = self.output_dir / f"{sidecar_base}.yaml"
            with open(self.sidecar_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(meta_dict, f, sort_keys=False)
        else:
            self.sidecar_path = self.output_dir / f"{sidecar_base}.json"
            with open(self.sidecar_path, "w", encoding="utf-8") as f:
                json.dump(meta_dict, f, indent=2)

    def close(self) -> None:
        """Finalize and seal the recording session."""
        self.metadata.end_time_iso = datetime.now(timezone.utc).isoformat()
        self._close_segment()
        self._write_sidecar()
        logger.info(
            "Session closed. Total frames recorded: %d across output dir: %s",
            self.total_recorded_frames,
            self.output_dir,
        )
