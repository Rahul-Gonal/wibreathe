"""Transmission link quality metrics, packet loss tracking, and SNR proxies."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Sequence

from wibreathe.io.parser import CSIFrame


@dataclass
class WindowQualityMetrics:
    """Diagnostic link quality indicators computed over a processing window."""
    packet_rate_achieved_hz: float
    expected_rate_hz: float
    packet_loss_percent: float
    mean_rssi_dbm: float
    mean_noise_floor_dbm: float
    snr_proxy_db: float
    valid_sample_fraction: float


class QualityMonitor:
    """Monitors raw packet reception stats and computes link performance metrics."""

    def __init__(self, expected_rate_hz: float = 50.0, window_duration_s: float = 2.0):
        self.expected_rate_hz = expected_rate_hz
        self.window_duration_s = window_duration_s
        self._history: Deque[tuple[float, int, int, int]] = deque()
        self.packet_rate_hz: float = expected_rate_hz
        self.packet_loss_pct: float = 0.0

    def add_packet(self, frame: CSIFrame) -> None:
        """Add live frame to rolling quality monitor."""
        t_s = frame.device_unwrapped_time_s
        self._history.append((t_s, frame.packet_id, frame.rssi, frame.noise_floor))

        # Evict older samples
        while self._history and (t_s - self._history[0][0]) > self.window_duration_s:
            self._history.popleft()

        if len(self._history) >= 2:
            dt = max(1e-3, self._history[-1][0] - self._history[0][0])
            self.packet_rate_hz = (len(self._history) - 1) / dt
            seq_first = self._history[0][1]
            seq_last = self._history[-1][1]
            seq_diff = seq_last - seq_first + 1
            if seq_diff > len(self._history):
                self.packet_loss_pct = ((seq_diff - len(self._history)) / seq_diff) * 100.0
            else:
                self.packet_loss_pct = 0.0

    def compute_metrics(
        self,
        packet_ids: Sequence[int],
        timestamps_s: Sequence[float],
        rssi_values: Sequence[int],
        noise_floors: Sequence[int],
        valid_mask: Optional[Sequence[bool]] = None,
    ) -> WindowQualityMetrics:
        """Compute window quality indicators."""
        n_packets = len(packet_ids)
        if n_packets < 2:
            return WindowQualityMetrics(
                packet_rate_achieved_hz=0.0,
                expected_rate_hz=self.expected_rate_hz,
                packet_loss_percent=100.0,
                mean_rssi_dbm=0.0,
                mean_noise_floor_dbm=-100.0,
                snr_proxy_db=0.0,
                valid_sample_fraction=0.0,
            )

        duration = max(1e-3, timestamps_s[-1] - timestamps_s[0])
        rate_achieved = (n_packets - 1) / duration

        # Sequence gap loss calculation
        seq_diff = packet_ids[-1] - packet_ids[0] + 1
        loss_pct = 0.0
        if seq_diff > n_packets:
            loss_pct = ((seq_diff - n_packets) / seq_diff) * 100.0

        mean_rssi = sum(rssi_values) / n_packets
        mean_noise = sum(noise_floors) / n_packets
        snr_proxy = mean_rssi - mean_noise

        valid_fraction = 1.0
        if valid_mask is not None and len(valid_mask) > 0:
            valid_fraction = sum(1 for v in valid_mask if v) / len(valid_mask)

        return WindowQualityMetrics(
            packet_rate_achieved_hz=rate_achieved,
            expected_rate_hz=self.expected_rate_hz,
            packet_loss_percent=loss_pct,
            mean_rssi_dbm=mean_rssi,
            mean_noise_floor_dbm=mean_noise,
            snr_proxy_db=snr_proxy,
            valid_sample_fraction=valid_fraction,
        )
