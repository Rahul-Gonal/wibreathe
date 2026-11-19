"""Downstream serial transmitter sending live DISP packets to the ESP32 OLED receiver.

Protocol defined in docs/PROTOCOL.md:
  DISP,<state>,<bpm or -->,<confidence 0-100>,<seq>\n
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

from wibreathe.io.serial_reader import SerialReader
from wibreathe.state.machine import SystemState

logger = logging.getLogger(__name__)


def format_disp_message(
    state: SystemState | str,
    bpm: Optional[float],
    confidence: float,
    seq: int,
) -> str:
    """Format canonical downstream OLED display line: DISP,<state>,<bpm or -->,<conf>,<seq>"""
    state_str = state.value if isinstance(state, SystemState) else str(state)
    bpm_str = f"{bpm:.1f}" if (bpm is not None and bpm > 0) else "--"
    conf_int = max(0, min(100, int(round(confidence))))
    return f"DISP,{state_str},{bpm_str},{conf_int},{seq}"


def parse_disp_message(line: str) -> Optional[Tuple[str, Optional[float], int, int]]:
    """Parse canonical downstream OLED display line.
    
    Returns (state, bpm, confidence, sequence) or None if invalid.
    """
    clean = line.strip()
    if not clean.startswith("DISP,"):
        return None
    parts = clean.split(",")
    if len(parts) != 5:
        return None
    
    state_str = parts[1]
    bpm_val = None if parts[2] == "--" else float(parts[2])
    conf_val = int(parts[3])
    seq_val = int(parts[4])
    return state_str, bpm_val, conf_val, seq_val


class DisplayLink:
    """Publishes formatted DISP status lines to the ESP32 receiver display over serial."""

    def __init__(
        self,
        reader: SerialReader,
        publish_rate_hz: float = 1.0,
    ):
        self.reader = reader
        self.publish_interval_s = 1.0 / max(0.1, publish_rate_hz)

        self.latest_state: SystemState = SystemState.NO_DATA
        self.latest_bpm: Optional[float] = None
        self.latest_confidence: float = 0.0
        self.seq: int = 1

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def update_values(
        self,
        state: SystemState,
        bpm: Optional[float],
        confidence: float,
    ) -> None:
        """Update active telemetry values to be transmitted in the next cycle."""
        with self._lock:
            self.latest_state = state
            self.latest_bpm = bpm
            self.latest_confidence = max(0.0, min(100.0, confidence))

    def format_line(self) -> str:
        """Format canonical protocol ASCII line."""
        with self._lock:
            line = format_disp_message(
                self.latest_state,
                self.latest_bpm,
                self.latest_confidence,
                self.seq,
            )
            self.seq += 1
            return line

    def send_now(self) -> bool:
        """Immediately format and transmit a single DISP line."""
        line = self.format_line()
        return self.reader.write_line(line)

    def start(self) -> None:
        """Launch background transmission thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._publish_loop,
            name="DisplayLinkWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info("DisplayLink background worker started at %.1f Hz", 1.0 / self.publish_interval_s)

    def stop(self, timeout: float = 2.0) -> None:
        """Halt background transmitter thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("DisplayLink stopped")

    def _publish_loop(self) -> None:
        """Periodic publisher loop."""
        while self._running:
            start_t = time.monotonic()
            self.send_now()
            elapsed = time.monotonic() - start_t
            remain = self.publish_interval_s - elapsed
            if remain > 0:
                time.sleep(remain)
