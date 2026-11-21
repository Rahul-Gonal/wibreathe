"""Configuration management and schema validation for WiBreathe."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class SystemConfig:
    device_id: str = "wibreathe-node-01"
    sample_rate_hz: float = 50.0
    uniform_rate_hz: float = 20.0
    baud_rate: int = 921600
    serial_timeout_sec: float = 1.0
    reconnect_delay_sec: float = 2.0
    max_queue_size: int = 2000

    def validate(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.uniform_rate_hz <= 0:
            raise ValueError("uniform_rate_hz must be positive")
        if self.uniform_rate_hz > self.sample_rate_hz:
            raise ValueError("uniform_rate_hz cannot exceed incoming sample_rate_hz")
        if self.baud_rate <= 0:
            raise ValueError("baud_rate must be positive")


@dataclass
class SubcarrierConfig:
    raw_subcarrier_count: int = 64
    layout: str = "lltf_ht20"
    null_indices: List[int] = field(
        default_factory=lambda: [0, 1, 2, 3, 4, 5, 32, 59, 60, 61, 62, 63]
    )
    selection_method: str = "snr"  # variance, snr, pca, mean
    num_selected: int = 8
    top_k: int = 8

    def validate(self) -> None:
        valid_methods = {"variance", "snr", "pca", "mean"}
        if self.selection_method not in valid_methods:
            raise ValueError(f"selection_method must be one of {valid_methods}")
        if self.num_selected < 1 or self.num_selected > self.raw_subcarrier_count:
            raise ValueError(
                f"num_selected must be between 1 and {self.raw_subcarrier_count}"
            )


@dataclass
class FilterConfig:
    hampel_window_samples: int = 7
    hampel_n_sigmas: float = 3.0
    min_deviation_floor: Optional[float] = None
    relative_floor_ratio: float = 0.20
    detrend_method: str = "linear"  # linear, mean, median
    detrend_window_sec: float = 10.0
    bandpass_low_hz: float = 0.1
    bandpass_high_hz: float = 0.5
    bandpass_order: int = 4
    smoothing_method: str = "savgol"  # savgol, moving_average
    smoothing_window_samples: int = 5
    smoothing_poly_order: int = 2

    def validate(self) -> None:
        if self.bandpass_low_hz <= 0 or self.bandpass_high_hz <= self.bandpass_low_hz:
            raise ValueError("bandpass_low_hz must be positive and less than bandpass_high_hz")
        if self.hampel_window_samples % 2 == 0 or self.hampel_window_samples < 3:
            raise ValueError("hampel_window_samples must be an odd integer >= 3")
        if self.smoothing_window_samples % 2 == 0 or self.smoothing_window_samples < 3:
            raise ValueError("smoothing_window_samples must be an odd integer >= 3")
        if self.smoothing_poly_order >= self.smoothing_window_samples:
            raise ValueError("smoothing_poly_order must be less than smoothing_window_samples")


@dataclass
class EstimationConfig:
    window_length_sec: float = 30.0
    window_stride_sec: float = 5.0
    fft_zero_pad_factor: int = 8
    parabolic_interpolation: bool = True
    min_peak_prominence: float = 0.05
    min_peak_distance_sec: float = 1.2
    min_confidence_score: float = 35.0
    min_bpm: float = 6.0
    max_bpm: float = 30.0

    def validate(self) -> None:
        if self.window_length_sec <= 0:
            raise ValueError("window_length_sec must be positive")
        if self.window_stride_sec <= 0 or self.window_stride_sec > self.window_length_sec:
            raise ValueError("window_stride_sec must be positive and <= window_length_sec")
        if self.min_confidence_score < 0 or self.min_confidence_score > 100:
            raise ValueError("min_confidence_score must be between 0 and 100")


@dataclass
class MotionConfig:
    short_window_sec: float = 1.5
    step_sec: float = 0.5
    multiplier_mad_enter: float = 3.5
    multiplier_mad_exit: float = 2.0
    min_duration_sec: float = 1.0
    mad_low_threshold: float = 0.04
    mad_high_threshold: float = 0.12
    window_duration_s: float = 1.0

    def validate(self) -> None:
        if self.short_window_sec <= 0 or self.step_sec <= 0:
            raise ValueError("Motion window and step must be positive")


@dataclass
class StateMachineConfig:
    debounce_time_sec: float = 2.0
    bed_exit_confirmation_sec: float = 6.0
    empty_bed_variance_threshold: float = 0.04
    no_data_timeout_sec: float = 5.0
    empty_bed_threshold: float = 0.015
    no_person_timeout_s: float = 5.0
    bed_exit_motion_decay_s: float = 3.0

    def validate(self) -> None:
        if self.debounce_time_sec < 0 or self.bed_exit_confirmation_sec < 0:
            raise ValueError("State machine delay parameters cannot be negative")


@dataclass
class DisplayLinkConfig:
    enabled: bool = True
    publish_rate_hz: float = 1.0

    def validate(self) -> None:
        if self.publish_rate_hz <= 0:
            raise ValueError("publish_rate_hz must be positive")


@dataclass
class LoggingConfig:
    flush_interval_packets: int = 100
    max_file_size_mb: int = 50

    def validate(self) -> None:
        if self.flush_interval_packets <= 0:
            raise ValueError("flush_interval_packets must be positive")
        if self.max_file_size_mb <= 0:
            raise ValueError("max_file_size_mb must be positive")


@dataclass
class ResampleFacade:
    target_rate_hz: float = 20.0
    max_gap_s: float = 0.5


@dataclass
class HampelFacade:
    window_size: int = 7
    n_sigmas: float = 3.0
    min_deviation_floor: Optional[float] = None
    relative_floor_ratio: float = 0.20


@dataclass
class BandpassFacade:
    lowcut_hz: float = 0.1
    highcut_hz: float = 0.5
    order: int = 4


@dataclass
class BreathingFacade:
    window_duration_s: float = 15.0
    step_duration_s: float = 1.0
    min_bpm: float = 6.0
    max_bpm: float = 30.0
    fft_zero_pad_factor: int = 8
    min_peak_distance_sec: float = 1.2


@dataclass
class AppConfig:
    system: SystemConfig = field(default_factory=SystemConfig)
    subcarriers: SubcarrierConfig = field(default_factory=SubcarrierConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    estimation: EstimationConfig = field(default_factory=EstimationConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    state_machine: StateMachineConfig = field(default_factory=StateMachineConfig)
    display_link: DisplayLinkConfig = field(default_factory=DisplayLinkConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @property
    def resample(self) -> ResampleFacade:
        return ResampleFacade(target_rate_hz=self.system.uniform_rate_hz, max_gap_s=0.5)

    @property
    def hampel(self) -> HampelFacade:
        return HampelFacade(
            window_size=self.filter.hampel_window_samples,
            n_sigmas=self.filter.hampel_n_sigmas,
            min_deviation_floor=self.filter.min_deviation_floor,
            relative_floor_ratio=self.filter.relative_floor_ratio,
        )

    @property
    def bandpass(self) -> BandpassFacade:
        return BandpassFacade(
            lowcut_hz=self.filter.bandpass_low_hz,
            highcut_hz=self.filter.bandpass_high_hz,
            order=self.filter.bandpass_order,
        )

    @property
    def breathing(self) -> BreathingFacade:
        return BreathingFacade(
            window_duration_s=self.estimation.window_length_sec,
            step_duration_s=self.estimation.window_stride_sec,
            min_bpm=self.estimation.min_bpm,
            max_bpm=self.estimation.max_bpm,
            fft_zero_pad_factor=self.estimation.fft_zero_pad_factor,
            min_peak_distance_sec=self.estimation.min_peak_distance_sec,
        )

    def validate(self) -> None:
        self.system.validate()
        self.subcarriers.validate()
        self.filter.validate()
        self.estimation.validate()
        self.motion.validate()
        self.state_machine.validate()
        self.display_link.validate()
        self.logging.validate()


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """Load and validate configuration from YAML file or defaults."""
    if config_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        default_yaml = project_root / "config" / "default.yaml"
        if default_yaml.is_file():
            config_path = default_yaml

    cfg = AppConfig()
    if config_path and Path(config_path).is_file():
        if yaml is not None:
            with open(config_path, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = yaml.safe_load(f) or {}
        else:
            data = {}

        if "system" in data:
            cfg.system = SystemConfig(**data["system"])
        if "subcarriers" in data:
            cfg.subcarriers = SubcarrierConfig(**data["subcarriers"])
        if "filter" in data:
            cfg.filter = FilterConfig(**data["filter"])
        if "estimation" in data:
            cfg.estimation = EstimationConfig(**data["estimation"])
        if "motion" in data:
            cfg.motion = MotionConfig(**data["motion"])
        if "state_machine" in data:
            cfg.state_machine = StateMachineConfig(**data["state_machine"])
        if "display_link" in data:
            cfg.display_link = DisplayLinkConfig(**data["display_link"])
        if "logging" in data:
            cfg.logging = LoggingConfig(**data["logging"])

    cfg.validate()
    return cfg


def save_config(config: AppConfig, output_path: str | Path) -> None:
    """Serialize configuration to a YAML file."""
    config.validate()
    data = {
        "system": vars(config.system),
        "subcarriers": vars(config.subcarriers),
        "filter": vars(config.filter),
        "estimation": vars(config.estimation),
        "motion": vars(config.motion),
        "state_machine": vars(config.state_machine),
        "display_link": vars(config.display_link),
        "logging": vars(config.logging),
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    else:
        import json
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
