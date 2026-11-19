"""Static offline session analysis and report plot generator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from wibreathe.dsp.bandpass import RespirationBandpass, detrend_median
from wibreathe.dsp.breathing import RespirationEstimator
from wibreathe.dsp.hampel import HampelFilter
from wibreathe.dsp.resample import UniformResampler
from wibreathe.dsp.subcarriers import SubcarrierSelector
from wibreathe.io.replay import CSIReplay

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend for file rendering
    import matplotlib.pyplot as plt
except ImportError:
    plt = None  # type: ignore


def generate_offline_plots(
    csv_path: str | Path,
    output_png: Optional[str | Path] = None,
) -> Optional[Path]:
    """Parse recorded CSV and render full session analysis figure to PNG."""
    csv_file = Path(csv_path)
    if not csv_file.is_file():
        raise FileNotFoundError(f"Recorded CSV not found: {csv_file}")

    if output_png is None:
        output_png = csv_file.with_suffix(".png")
    out_path = Path(output_png)

    # 1. Read all frames from CSV
    replay = CSIReplay(csv_file, realtime=False)
    frames = list(replay.stream())
    if len(frames) < 10:
        logger.warning("Insufficient frames in %s to generate offline plot", csv_file)
        return None

    times_raw = [f.device_unwrapped_time_s for f in frames]
    selector = SubcarrierSelector(raw_subcarrier_count=frames[0].subcarrier_len)

    # Select representative subcarrier
    subcarrier_amp = [f.amplitudes[18] if len(f.amplitudes) > 18 else f.amplitudes[0] for f in frames]

    # 2. Resample to 20 Hz
    fs = 20.0
    t_uniform, v_uniform, mask = UniformResampler.resample_batch(
        times_raw, subcarrier_amp, target_rate_hz=fs
    )

    # 3. Filter
    hf = HampelFilter(window_size=7, n_sigmas=3.0)
    v_clean = hf.filter_series(v_uniform, causal=False)
    v_detrend = detrend_median(v_clean, window_samples=201)

    bp = RespirationBandpass(sample_rate_hz=fs, low_cut_hz=0.1, high_cut_hz=0.5, order=4)
    v_filt = bp.filter_batch(v_detrend, zero_phase=True)

    # 4. Sliding rate estimation
    estimator = RespirationEstimator(sample_rate_hz=fs)
    win_samples = int(30.0 * fs)
    stride_samples = int(5.0 * fs)

    eval_times: List[float] = []
    bpm_values: List[float] = []

    for start_idx in range(0, len(v_filt) - win_samples + 1, stride_samples):
        segment = v_filt[start_idx : start_idx + win_samples]
        center_t = t_uniform[start_idx + win_samples // 2]
        est = estimator.estimate_rate(segment)
        eval_times.append(center_t)
        bpm_values.append(est.bpm if est.bpm is not None else 0.0)

    if plt is None:
        logger.warning("matplotlib is not installed; skipping PNG generation.")
        return None

    # 5. Render figure
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.suptitle(f"WiBreathe Session Analysis: {csv_file.name}", fontsize=14)

    # Plot 1: Raw CSI Amplitude
    axes[0].plot(t_uniform, v_uniform, color="tab:blue", lw=1.0)
    axes[0].set_ylabel("Amplitude")
    axes[0].set_title("Raw CSI Amplitude (Resampled 20 Hz)")
    axes[0].grid(True, alpha=0.3)

    # Plot 2: Filtered Respiration
    axes[1].plot(t_uniform, v_filt, color="tab:green", lw=1.2)
    axes[1].set_ylabel("Breathing Signal")
    axes[1].set_title("Zero-Phase Bandpass (0.1 - 0.5 Hz)")
    axes[1].grid(True, alpha=0.3)

    # Plot 3: Estimated BPM
    if eval_times:
        axes[2].step(eval_times, bpm_values, color="tab:red", lw=1.8, where="mid", label="Estimated BPM")
    axes[2].set_ylabel("BPM")
    axes[2].set_xlabel("Device Time (seconds)")
    axes[2].set_title("Estimated Respiration Rate (Sliding 30s Window)")
    axes[2].set_ylim(0, 35)
    axes[2].grid(True, alpha=0.3)
    axes[2].legend(loc="upper right")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    logger.info("Saved offline analysis plot to %s", out_path)
    return out_path
