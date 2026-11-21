"""Calibration system for baseline variance and empty-bed signature learning."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)


def _calc_median_and_mad(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    s = sorted(values)
    n = len(s)
    med = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    devs = sorted(abs(x - med) for x in s)
    mad = devs[n // 2] if n % 2 == 1 else (devs[n // 2 - 1] + devs[n // 2]) / 2.0
    return med, mad


@dataclass
class BedCalibration:
    """Calibrated statistical profile of empty bed and occupied stillness."""
    empty_bed_variance: float = 0.03
    empty_bed_mad: float = 0.015
    empty_bed_band_power: float = 1.0
    occupied_still_variance: float = 0.12
    occupied_still_mad: float = 0.04
    selected_subcarriers: List[int] = field(default_factory=lambda: [10, 15, 20, 25, 30, 35, 40, 45])
    calibrated_at_iso: str = ""
    empty_calibrated: bool = False
    occupied_calibrated: bool = False
    is_calibrated: bool = False

    def save(self, file_path: str | Path) -> None:
        """Save calibration data to file following the extension format (.json -> JSON, .yaml/.yml -> YAML)."""
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.calibrated_at_iso = datetime.now(timezone.utc).isoformat()
        # Refuse to mark calibrated if either half is default/uncalibrated
        self.is_calibrated = bool(self.empty_calibrated and self.occupied_calibrated)
        data = asdict(self)

        if target.suffix in (".yaml", ".yml"):
            if yaml is not None:
                with open(target, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=False)
            else:
                # Clean pure-Python YAML writer fallback
                with open(target, "w", encoding="utf-8") as f:
                    for k, v in data.items():
                        if isinstance(v, bool):
                            f.write(f"{k}: {'true' if v else 'false'}\n")
                        elif isinstance(v, list):
                            f.write(f"{k}: [{', '.join(map(str, v))}]\n")
                        elif isinstance(v, str):
                            f.write(f'{k}: "{v}"\n')
                        else:
                            f.write(f"{k}: {v}\n")
        else:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        logger.info("Saved bed calibration profile to %s", target)

    @classmethod
    def load(cls, file_path: str | Path) -> BedCalibration:
        """Load calibration profile from file based on extension (.yaml/.yml or .json)."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Calibration file %s not found. Using default profile.", path)
            return cls()

        try:
            if path.suffix in (".yaml", ".yml"):
                if yaml is not None:
                    with open(path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                else:
                    # Clean pure-Python YAML parser fallback
                    data = {}
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if ":" in line:
                                k, v = line.split(":", 1)
                                k = k.strip()
                                v = v.strip()
                                if v.startswith("[") and v.endswith("]"):
                                    inner = v[1:-1].strip()
                                    data[k] = [int(x.strip()) for x in inner.split(",") if x.strip()]
                                elif v.lower() == "true":
                                    data[k] = True
                                elif v.lower() == "false":
                                    data[k] = False
                                elif v.startswith('"') and v.endswith('"'):
                                    data[k] = v[1:-1]
                                elif "." in v:
                                    try:
                                        data[k] = float(v)
                                    except ValueError:
                                        data[k] = v
                                else:
                                    try:
                                        data[k] = int(v)
                                    except ValueError:
                                        data[k] = v
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            calib = cls(**data)
            # Refuse to mark calibrated if either half is default/uncalibrated
            calib.is_calibrated = bool(calib.empty_calibrated and calib.occupied_calibrated)
            logger.info("Loaded bed calibration profile from %s (is_calibrated=%s)", path, calib.is_calibrated)
            return calib
        except Exception as e:
            logger.error("Error reading calibration file %s: %s", path, e)
            return cls()


def compute_inter_frame_metrics(frames: Sequence[Any]) -> List[float]:
    """Compute inter-frame Mean Absolute Deviation across all subcarriers (matching MotionDetector scale)."""
    prev_amps: Optional[List[float]] = None
    mads: List[float] = []
    for frame in frames:
        amps = getattr(frame, "amplitudes", None)
        if not amps:
            continue
        if prev_amps is not None and len(prev_amps) == len(amps):
            diffs = [abs(a - b) for a, b in zip(amps, prev_amps)]
            mads.append(sum(diffs) / len(diffs))
        prev_amps = amps
    return mads


class Calibrator:
    """Computes calibration signatures from recorded baseline data."""

    def __init__(self, existing: Optional[BedCalibration] = None):
        self.calibration = existing or BedCalibration()

    def process_empty_bed_data(
        self,
        metrics: Sequence[float],
        band_powers: Optional[Sequence[float]] = None,
    ) -> None:
        """Process empty-bed baseline data."""
        if not metrics:
            return
        med_var, mad_var = _calc_median_and_mad(metrics)
        self.calibration.empty_bed_variance = max(1e-4, med_var)
        self.calibration.empty_bed_mad = max(1e-4, mad_var)

        if band_powers:
            med_bp, _ = _calc_median_and_mad(band_powers)
            self.calibration.empty_bed_band_power = max(1e-4, med_bp)

        self.calibration.empty_calibrated = True
        self.calibration.is_calibrated = bool(
            self.calibration.empty_calibrated and self.calibration.occupied_calibrated
        )

    def process_occupied_still_data(
        self,
        metrics: Sequence[float],
        top_subcarriers: Optional[Sequence[int]] = None,
    ) -> None:
        """Process occupied-still baseline data."""
        if not metrics:
            return
        med_var, mad_var = _calc_median_and_mad(metrics)
        self.calibration.occupied_still_variance = max(1e-4, med_var)
        self.calibration.occupied_still_mad = max(1e-4, mad_var)

        if top_subcarriers:
            self.calibration.selected_subcarriers = list(top_subcarriers)

        self.calibration.occupied_calibrated = True
        self.calibration.is_calibrated = bool(
            self.calibration.empty_calibrated and self.calibration.occupied_calibrated
        )
