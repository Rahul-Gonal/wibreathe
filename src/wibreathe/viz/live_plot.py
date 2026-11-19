"""Live real-time visualization and headless terminal dashboard."""

from __future__ import annotations

import collections
import logging
import sys
import time
from typing import Deque, List, Optional, Sequence

logger = logging.getLogger(__name__)

try:
    import matplotlib
    import matplotlib.pyplot as plt
except ImportError:
    plt = None  # type: ignore


class LiveDashboard:
    """Manages graphical matplotlib window or headless terminal telemetry reporting."""

    def __init__(
        self,
        headless: bool = False,
        buffer_length_sec: float = 30.0,
        sample_rate_hz: float = 20.0,
    ):
        self.headless = headless or (plt is None)
        self.buffer_size = int(buffer_length_sec * sample_rate_hz)

        # Ring buffers for telemetry
        self.time_buffer: Deque[float] = collections.deque(maxlen=self.buffer_size)
        self.raw_buffer: Deque[float] = collections.deque(maxlen=self.buffer_size)
        self.filtered_buffer: Deque[float] = collections.deque(maxlen=self.buffer_size)

        self.last_update_time: float = 0.0
        self._fig = None
        self._axes = None

        if not self.headless:
            self._init_gui()

    def _init_gui(self) -> None:
        """Initialize Matplotlib subplots."""
        try:
            plt.ion()
            self._fig, self._axes = plt.subplots(3, 1, figsize=(10, 8))
            self._fig.suptitle("WiBreathe: Wi-Fi CSI Respiration & Motion Monitor")
            self._fig.canvas.draw()
            plt.pause(0.01)
        except Exception as e:
            logger.warning("Could not initialize GUI display: %s. Switching to headless mode.", e)
            self.headless = True

    def update(
        self,
        t: float,
        raw_val: float,
        filt_val: float,
        state: str,
        bpm: Optional[float],
        confidence: float,
        packet_rate_hz: float,
        loss_pct: float,
    ) -> None:
        """Push latest sample and periodically render updates."""
        self.time_buffer.append(t)
        self.raw_buffer.append(raw_val)
        self.filtered_buffer.append(filt_val)

        now = time.time()
        # Limit redraw rate to 5 Hz to avoid GUI stalls
        if now - self.last_update_time < 0.2:
            return
        self.last_update_time = now

        bpm_display = f"{bpm:.1f} bpm" if bpm is not None else "--"
        status_line = (
            f"\r[WiBreathe] State: {state:<11} | Rate: {bpm_display:<8} | "
            f"Conf: {confidence:3.0f}% | Ingress: {packet_rate_hz:4.1f} Hz | Loss: {loss_pct:4.1f}%"
        )
        sys.stdout.write(status_line)
        sys.stdout.flush()

        if not self.headless and self._axes is not None:
            try:
                times = list(self.time_buffer)
                t0 = times[0] if times else 0.0
                rel_times = [x - t0 for x in times]

                # Panel 0: Raw amplitude
                self._axes[0].cla()
                self._axes[0].plot(rel_times, list(self.raw_buffer), color="tab:blue", lw=1.2)
                self._axes[0].set_title(f"Raw CSI Amplitude (State: {state})")
                self._axes[0].grid(True, alpha=0.3)

                # Panel 1: Filtered breathing waveform
                self._axes[1].cla()
                self._axes[1].plot(rel_times, list(self.filtered_buffer), color="tab:green", lw=1.5)
                self._axes[1].set_title(f"Filtered Respiration Waveform (BPM: {bpm_display}, Conf: {confidence:.0f}%)")
                self._axes[1].grid(True, alpha=0.3)

                # Panel 2: Link health diagnostics
                self._axes[2].cla()
                self._axes[2].text(
                    0.05, 0.6,
                    f"Packet Ingress: {packet_rate_hz:.1f} Hz  |  Loss: {loss_pct:.1f}%",
                    fontsize=12,
                )
                self._axes[2].text(
                    0.05, 0.25,
                    f"Device State: {state}  |  Confidence: {confidence:.0f}%",
                    fontsize=12,
                    weight="bold",
                )
                self._axes[2].axis("off")

                self._fig.tight_layout()
                self._fig.canvas.draw()
                self._fig.canvas.flush_events()
            except Exception as err:
                logger.debug("Plot refresh error: %s", err)

    def close(self) -> None:
        """Close dashboard and cleanup resources."""
        sys.stdout.write("\n")
        sys.stdout.flush()
        if not self.headless and plt is not None:
            try:
                plt.close("all")
            except Exception:
                pass
