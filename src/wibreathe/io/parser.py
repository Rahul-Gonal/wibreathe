"""CSI data parser for ESP32 raw serial streams.

Converts formatted CSI_DATA ASCII lines into structured CSIFrame objects,
validating length, handling signed int8 byte representations, unwrapping 32-bit
microsecond device timestamps, and transforming interleaved [Q, I] subcarriers
into complex numbers.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from io import StringIO
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

UINT32_MODULO = 1 << 32


@dataclass
class CSIFrame:
    """Represents a single parsed and validated Wi-Fi CSI packet."""
    host_mono_time: float          # Host monotonic clock in seconds
    host_wall_time: float          # Host UTC epoch in seconds
    device_raw_timestamp_us: int   # Raw 32-bit microsecond counter from ESP32
    device_unwrapped_time_s: float # Continuous unwrapped device time in seconds
    packet_id: int                 # Sequential packet sequence ID embedded by TX
    mac: str                       # Transmitter MAC address
    rssi: int                      # Received signal strength indicator (dBm)
    rate: int                      # PHY rate index
    channel: int                   # Primary Wi-Fi channel
    noise_floor: int               # Noise floor in dBm
    subcarrier_len: int            # Number of subcarriers (len // 2)
    raw_bytes: List[int]           # Raw signed int8 values from packet
    complex_csi: List[complex]     # Complex subcarrier values (Real + Imag*j)
    amplitudes: List[float]        # Subcarrier amplitudes abs(complex_csi)
    phases: List[float]            # Subcarrier phases in radians atan2(Q, I)


class CSIParser:
    """Parser for raw ESP32 serial CSI lines."""

    def __init__(self, filter_mac: Optional[str] = None):
        self.filter_mac = filter_mac.lower() if filter_mac else None
        self._last_raw_timestamp_us: Optional[int] = None
        self._accumulated_us: int = 0
        self.parsed_frames_count: int = 0
        self.dropped_frames_count: int = 0
        self.mac_filtered_count: int = 0
        self.error_lines_count: int = 0

    def reset_timestamp_unwrapper(self) -> None:
        """Reset the 32-bit timestamp unwrapping state."""
        self._last_raw_timestamp_us = None
        self._accumulated_us = 0

    def unwrap_timestamp(self, raw_us: int) -> float:
        """Unwrap 32-bit microsecond counter and return continuous seconds.
        
        The ESP32 local_timestamp is a 32-bit unsigned microsecond counter that
        wraps back to 0 every 4294.967296 seconds (~71.58 minutes).
        """
        raw_us = raw_us & 0xFFFFFFFF
        if self._last_raw_timestamp_us is None:
            self._last_raw_timestamp_us = raw_us
            self._accumulated_us = raw_us
        else:
            diff = (raw_us - self._last_raw_timestamp_us) % UINT32_MODULO
            # If diff is excessively large (e.g., negative jump or reboot), keep delta within bound
            if diff > (UINT32_MODULO >> 1):
                # Reverse jump or device reboot: reset anchor
                logger.warning(
                    "Timestamp reverse jump detected (%d -> %d), resetting unwrap state",
                    self._last_raw_timestamp_us,
                    raw_us,
                )
                self._last_raw_timestamp_us = raw_us
            else:
                self._accumulated_us += diff
                self._last_raw_timestamp_us = raw_us

        return self._accumulated_us / 1_000_000.0

    def parse_line(
        self,
        line: str,
        host_mono_time: Optional[float] = None,
        host_wall_time: Optional[float] = None,
    ) -> Optional[CSIFrame]:
        """Parse a single raw serial line.
        
        Returns a CSIFrame if the line is a valid CSI_DATA packet matching filter
        rules, or None if skipped/malformed.
        """
        if host_mono_time is None:
            host_mono_time = time.monotonic()
        if host_wall_time is None:
            host_wall_time = time.time()

        line = line.strip()
        if not line or not line.startswith("CSI_DATA,"):
            return None

        try:
            reader = csv.reader(StringIO(line))
            cols = next(reader)
        except Exception as e:
            self.error_lines_count += 1
            logger.debug("CSV parsing error on line: %s, err: %s", line[:40], e)
            return None

        # Standard ESP32 format is 25 columns; C5/C6 is 15 columns
        if len(cols) < 15:
            self.dropped_frames_count += 1
            return None

        try:
            packet_id = int(cols[1])
            mac = cols[2].strip().lower()

            if self.filter_mac and mac != self.filter_mac:
                self.mac_filtered_count += 1
                return None

            rssi = int(cols[3])
            rate = int(cols[4])

            # Detect format by length
            if len(cols) >= 25:
                noise_floor = int(cols[14])
                channel = int(cols[16])
                raw_timestamp_us = int(cols[18])
                declared_len = int(cols[22])
                raw_data_str = cols[24]
            else:
                noise_floor = int(cols[5])
                channel = int(cols[8])
                raw_timestamp_us = int(cols[9])
                declared_len = int(cols[12])
                raw_data_str = cols[14]

            # Parse array payload: "[v0,v1,v2,...]"
            raw_data_str = raw_data_str.strip()
            if not (raw_data_str.startswith("[") and raw_data_str.endswith("]")):
                self.dropped_frames_count += 1
                return None

            try:
                raw_vals: List[int] = json.loads(raw_data_str)
            except json.JSONDecodeError:
                # Fast fallback regex if JSON parser fails on unconventional quotes
                raw_vals = [
                    int(x.strip()) for x in raw_data_str[1:-1].split(",") if x.strip()
                ]

            if len(raw_vals) != declared_len:
                self.dropped_frames_count += 1
                logger.debug(
                    "Length mismatch: declared %d vs parsed %d",
                    declared_len,
                    len(raw_vals),
                )
                return None

            # Handle signed int8 clamping
            signed_raw: List[int] = []
            for v in raw_vals:
                if v > 127:
                    v = v - 256
                elif v < -128:
                    v = -128
                signed_raw.append(v)

            # Ground truth I/Q ordering:
            # Index 2*k is Q (Imaginary), Index 2*k + 1 is I (Real)
            subcarrier_count = len(signed_raw) // 2
            complex_csi: List[complex] = []
            amplitudes: List[float] = []
            phases: List[float] = []

            for k in range(subcarrier_count):
                q_imag = float(signed_raw[k * 2])
                i_real = float(signed_raw[k * 2 + 1])
                c_val = complex(i_real, q_imag)
                complex_csi.append(c_val)
                amplitudes.append(abs(c_val))
                phases.append(math.atan2(q_imag, i_real))

            unwrapped_time = self.unwrap_timestamp(raw_timestamp_us)
            self.parsed_frames_count += 1

            return CSIFrame(
                host_mono_time=host_mono_time,
                host_wall_time=host_wall_time,
                device_raw_timestamp_us=raw_timestamp_us,
                device_unwrapped_time_s=unwrapped_time,
                packet_id=packet_id,
                mac=mac,
                rssi=rssi,
                rate=rate,
                channel=channel,
                noise_floor=noise_floor,
                subcarrier_len=subcarrier_count,
                raw_bytes=signed_raw,
                complex_csi=complex_csi,
                amplitudes=amplitudes,
                phases=phases,
            )

        except (ValueError, IndexError) as err:
            self.error_lines_count += 1
            logger.debug("Exception decoding CSI frame: %s", err)
            return None
