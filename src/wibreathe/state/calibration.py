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
    is_calibrated: bool = False

    def save(self, file_path: str | Path) -> None:
        """Save calibration data to file."""
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.calibrated_at_iso = datetime.now(timezone.utc).isoformat()
        data = asdict(self)
        if yaml is not None:
            with open(target, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False)
        else:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        logger.info("Saved bed calibration profile to %s", target)

    @classmethod
    def load(cls, file_path: str | Path) -> BedCalibration:
        """Load calibration profile from file."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Calibration file %s not found. Using default profile.", path)
            return cls()

        try:
            if yaml is not None:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            calib = cls(**data)
            calib.is_calibrated = True
            logger.info("Loaded bed calibration profile from %s", path)
            return calib
        except Exception as e:
            logger.error("Error reading calibration file %s: %s", path, e)
            return cls()


class Calibrator:
    """Computes calibration signatures from recorded baseline data."""

    def __init__(self):
        self.calibration = BedCalibration()

    def process_empty_bed_data(
        self,
        variances: Sequence[float],
        band_powers: Optional[Sequence[float]] = None,
    ) -> None:
        """Process empty-bed baseline data."""
        if not variances:
            return
        med_var, mad_var = _calc_median_and_mad(variances)
        self.calibration.empty_bed_variance = max(1e-4, med_var)
        self.calibration.empty_bed_mad = max(1e-4, mad_var)

        if band_powers:
            med_bp, _ = _calc_median_and_mad(band_powers)
            self.calibration.empty_bed_band_power = max(1e-4, med_bp)

        self.calibration.is_calibrated = True

    def process_occupied_still_data(
        self,
        variances: Sequence[float],
        top_subcarriers: Optional[Sequence[int]] = None,
    ) -> None:
        """Process occupied-still baseline data."""
        if not variances:
            return
        med_var, mad_var = _calc_median_and_mad(variances)
        self.calibration.occupied_still_variance = max(1e-4, med_var)
        self.calibration.occupied_still_mad = max(1e-4, mad_var)

        if top_subcarriers:
            self.calibration.selected_subcarriers = list(top_subcarriers)

        self.calibration.is_calibrated = True
