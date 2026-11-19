"""Transmission link quality metrics, packet loss tracking, and SNR proxies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


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

    def __init__(self, expected_rate_hz: float = 50.0):
        self.expected_rate_hz = expected_rate_hz

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
        id_span = max(1, packet_ids[-1] - packet_ids[0] + 1)
        lost_packets = max(0, id_span - n_packets)
        loss_pct = (lost_packets / id_span) * 100.0

        mean_rssi = sum(rssi_values) / n_packets
        mean_noise = sum(noise_floors) / n_packets
        snr_proxy = mean_rssi - mean_noise

        if valid_mask is not None and len(valid_mask) > 0:
            valid_fraction = sum(1 for v in valid_mask if v) / len(valid_mask)
        else:
            valid_fraction = 1.0

        return WindowQualityMetrics(
            packet_rate_achieved_hz=rate_achieved,
            expected_rate_hz=self.expected_rate_hz,
            packet_loss_percent=loss_pct,
            mean_rssi_dbm=mean_rssi,
            mean_noise_floor_dbm=mean_noise,
            snr_proxy_db=snr_proxy,
            valid_sample_fraction=valid_fraction,
        )
