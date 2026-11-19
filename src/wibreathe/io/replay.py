"""Replay engine for reading recorded CSI CSV files and feeding into DSP pipelines."""

from __future__ import annotations

import csv
import json
import logging
import math
import time
from pathlib import Path
from typing import Generator, List, Optional

from wibreathe.io.parser import CSIFrame

logger = logging.getLogger(__name__)


class CSIReplay:
    """Reads recorded CSI CSV files and yields reconstructed CSIFrame objects."""

    def __init__(
        self,
        csv_path: str | Path,
        realtime: bool = False,
        speedup: float = 1.0,
    ):
        self.csv_path = Path(csv_path)
        if not self.csv_path.is_file():
            raise FileNotFoundError(f"Replay CSV file not found: {self.csv_path}")
        self.realtime = realtime
        self.speedup = max(0.01, speedup)
        self.frames_read: int = 0

    def stream(self) -> Generator[CSIFrame, None, None]:
        """Stream frames from file with optional real-time pacing."""
        last_frame_time: Optional[float] = None
        replay_start_wall = time.time()
        replay_start_mono = time.monotonic()

        with open(self.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or "data" not in header:
                raise ValueError(f"Invalid CSI CSV header in {self.csv_path}")

            idx_mono = header.index("host_mono_time") if "host_mono_time" in header else 0
            idx_wall = header.index("host_wall_time") if "host_wall_time" in header else 1
            idx_raw_us = header.index("device_raw_timestamp_us") if "device_raw_timestamp_us" in header else 2
            idx_unwrapped = header.index("device_unwrapped_time_s") if "device_unwrapped_time_s" in header else 3
            idx_id = header.index("packet_id") if "packet_id" in header else 4
            idx_mac = header.index("mac") if "mac" in header else 5
            idx_rssi = header.index("rssi") if "rssi" in header else 6
            idx_rate = header.index("rate") if "rate" in header else 7
            idx_channel = header.index("channel") if "channel" in header else 8
            idx_noise = header.index("noise_floor") if "noise_floor" in header else 9
            idx_len = header.index("len") if "len" in header else 10
            idx_data = header.index("data") if "data" in header else 11

            for row in reader:
                if not row or len(row) <= idx_data:
                    continue

                try:
                    host_mono = float(row[idx_mono])
                    host_wall = float(row[idx_wall])
                    dev_raw_us = int(row[idx_raw_us])
                    dev_unwrapped_s = float(row[idx_unwrapped])
                    packet_id = int(row[idx_id])
                    mac = row[idx_mac]
                    rssi = int(row[idx_rssi])
                    rate = int(row[idx_rate])
                    channel = int(row[idx_channel])
                    noise_floor = int(row[idx_noise])
                    data_str = row[idx_data].strip()

                    if not (data_str.startswith("[") and data_str.endswith("]")):
                        continue

                    raw_bytes = json.loads(data_str)
                    subcarrier_count = len(raw_bytes) // 2

                    complex_csi: List[complex] = []
                    amplitudes: List[float] = []
                    phases: List[float] = []

                    # Ground truth order: [Q0, I0, Q1, I1, ...]
                    for k in range(subcarrier_count):
                        q = float(raw_bytes[k * 2])
                        i_val = float(raw_bytes[k * 2 + 1])
                        c_val = complex(i_val, q)
                        complex_csi.append(c_val)
                        amplitudes.append(abs(c_val))
                        phases.append(math.atan2(q, i_val))

                    # Pace playback if realtime mode is requested
                    if self.realtime and last_frame_time is not None:
                        delta = (dev_unwrapped_s - last_frame_time) / self.speedup
                        if 0 < delta < 2.0:
                            time.sleep(delta)
                    last_frame_time = dev_unwrapped_s

                    frame = CSIFrame(
                        host_mono_time=replay_start_mono + dev_unwrapped_s,
                        host_wall_time=replay_start_wall + dev_unwrapped_s,
                        device_raw_timestamp_us=dev_raw_us,
                        device_unwrapped_time_s=dev_unwrapped_s,
                        packet_id=packet_id,
                        mac=mac,
                        rssi=rssi,
                        rate=rate,
                        channel=channel,
                        noise_floor=noise_floor,
                        subcarrier_len=subcarrier_count,
                        raw_bytes=raw_bytes,
                        complex_csi=complex_csi,
                        amplitudes=amplitudes,
                        phases=phases,
                    )
                    self.frames_read += 1
                    yield frame

                except Exception as e:
                    logger.debug("Skipping unparseable row in replay: %s", e)
                    continue
