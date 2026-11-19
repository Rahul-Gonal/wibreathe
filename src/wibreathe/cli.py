"""Command-Line Interface (CLI) entry point for WiBreathe."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

from wibreathe import __version__
from wibreathe.config import load_config
from wibreathe.dsp.bandpass import RespirationBandpass, detrend_median
from wibreathe.dsp.breathing import RespirationEstimator
from wibreathe.dsp.hampel import HampelFilter
from wibreathe.dsp.motion import MotionDetector
from wibreathe.dsp.quality import QualityMonitor
from wibreathe.dsp.resample import UniformResampler
from wibreathe.dsp.subcarriers import SubcarrierSelector
from wibreathe.io.display_link import DisplayLink
from wibreathe.io.parser import CSIParser
from wibreathe.io.recorder import CSIRecorder, RecordingMetadata
from wibreathe.io.replay import CSIReplay
from wibreathe.io.serial_reader import SerialReader, list_serial_ports
from wibreathe.sim.synthetic import SyntheticGenerator, SyntheticScenario
from wibreathe.state.calibration import BedCalibration, Calibrator
from wibreathe.state.machine import SystemState, WiBreatheStateMachine
from wibreathe.viz.live_plot import LiveDashboard
from wibreathe.viz.offline_plots import generate_offline_plots

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wibreathe.cli")


def cmd_list_ports(args: argparse.Namespace) -> int:
    """List available serial communication ports."""
    ports = list_serial_ports()
    if not ports:
        print("No serial ports discovered. Verify board connection via USB-UART.")
        return 1
    print("Available serial ports:")
    for p in ports:
        print(f"  - {p}")
    return 0


def cmd_check_link(args: argparse.Namespace) -> int:
    """Listen for incoming CSI frames, report link diagnostics, and assert PASS/FAIL."""
    print(f"Connecting to {args.port} @ {args.baud} baud for link verification...")
    reader = SerialReader(port=args.port, baud_rate=args.baud, timeout_sec=1.0)
    parser = CSIParser(filter_mac=args.mac)

    try:
        reader.start()
    except Exception as e:
        print(f"FAIL: Could not open port {args.port}: {e}")
        return 1

    print("Listening for 5 seconds...")
    start_time = time.time()
    frames_received = 0
    rssi_list: List[int] = []
    len_list: List[int] = []

    while time.time() - start_time < 5.0:
        line = reader.get_line(timeout=0.2)
        if line:
            frame = parser.parse_line(line)
            if frame:
                frames_received += 1
                rssi_list.append(frame.rssi)
                len_list.append(len(frame.raw_bytes))

    reader.stop()

    duration = time.time() - start_time
    rate = frames_received / duration

    print("\n--- Link Check Results ---")
    print(f"Frames Received: {frames_received}")
    print(f"Average Rate   : {rate:.1f} packets/sec")

    if frames_received > 0:
        avg_rssi = sum(rssi_list) / len(rssi_list)
        common_len = max(set(len_list), key=len_list.count)
        print(f"Average RSSI   : {avg_rssi:.1f} dBm")
        print(f"CSI Array Len  : {common_len} bytes ({common_len // 2} subcarriers)")

        if rate >= 10.0:
            print("\nRESULT: [PASS] - Reliable CSI stream active.")
            return 0
        else:
            print("\nRESULT: [WARN] - CSI received but packet rate is low (<10 Hz).")
            return 0

    print("\nRESULT: [FAIL] - No CSI frames received.")
    print("Troubleshooting steps:")
    print("  1. Verify cable is plugged into UART port (not native USB-JTAG).")
    print("  2. Check both TX and RX share the same Wi-Fi channel (default 6).")
    print("  3. Ensure transmitter sends packets at an OFDM rate (MCS0), not 802.11b.")
    print("  4. If TX MAC filter is set, verify it matches transmitter MAC address.")
    return 1


def cmd_record(args: argparse.Namespace) -> int:
    """Record raw CSI stream to timestamped CSV with study metadata sidecar."""
    cfg = load_config(args.config)
    meta = RecordingMetadata(
        trial_id=args.trial,
        volunteer_code=args.volunteer,
        breathing_mode=args.mode,
        posture=args.posture,
        position=args.position,
        consent_obtained=args.consent,
        port=args.port,
        notes=args.notes,
    )

    try:
        meta.validate()
    except ValueError as err:
        print(f"Configuration Error: {err}")
        return 1

    recorder = CSIRecorder(
        output_dir=args.out_dir,
        metadata=meta,
        config=cfg,
        flush_interval_packets=cfg.logging.flush_interval_packets,
        max_file_size_mb=cfg.logging.max_file_size_mb,
    )

    reader = SerialReader(port=args.port, baud_rate=args.baud)
    parser = CSIParser()

    print(f"Starting raw recording for participant {args.volunteer} [{args.mode}]. Press Ctrl+C to stop.")
    reader.start()

    try:
        while True:
            line = reader.get_line(timeout=0.5)
            if line:
                frame = parser.parse_line(line)
                if frame:
                    recorder.record_frame(frame)
    except KeyboardInterrupt:
        print("\nStopping recording session...")
    finally:
        reader.stop()
        recorder.close()
        print(f"Session complete. Output files stored in {recorder.output_dir}")

    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """Generate realistic synthetic CSI data and ground-truth labels."""
    sim = SyntheticGenerator(sample_rate_hz=args.rate, seed=args.seed)
    scenario_enum = SyntheticScenario(args.scenario)

    frames, labels = sim.generate_csv_and_labels(
        csv_output_path=args.out,
        labels_output_path=args.labels,
        duration_sec=args.duration,
        scenario=scenario_enum,
        target_bpm=args.bpm,
    )
    print(f"Generated {frames} frames to {args.out} and {labels} labels to {args.labels}")
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    """Generate static offline report plots from recorded CSV."""
    out = generate_offline_plots(args.csv, args.out)
    if out:
        print(f"Generated plot: {out}")
        return 0
    else:
        print("Note: Plot skipped (matplotlib not installed or CSV has insufficient data).")
        return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay recorded CSI CSV file through the system."""
    replay = CSIReplay(args.file, realtime=not args.fast, speedup=args.speed)
    count = 0
    print(f"Starting replay of {args.file} (realtime={not args.fast}, speedup={args.speed}x)...")
    try:
        for frame in replay.stream():
            count += 1
            if count % 250 == 0:
                print(f"Replayed {count} frames (seq={frame.packet_id}, rssi={frame.rssi} dBm)...")
    except KeyboardInterrupt:
        print("\nReplay paused by user.")
    print(f"Replay complete: {count} frames processed.")
    return 0


def cmd_tap(args: argparse.Namespace) -> int:
    """Run interactive terminal reference tap tool for ground-truth breath logging."""
    from wibreathe.eval.reference_tap import ReferenceTapLogger

    out_file = args.out
    if not out_file:
        out_file = f"data/reference/{args.volunteer}_{args.mode}_ref.csv"

    logger_inst = ReferenceTapLogger(out_file, volunteer_code=args.volunteer)
    try:
        logger_inst.run_interactive()
    except KeyboardInterrupt:
        print("\nExiting tap logger.")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Evaluate estimated respiration rates and states against reference ground truth."""
    import csv
    from wibreathe.eval.align import align_reference_and_estimates
    from wibreathe.eval.metrics import compute_evaluation_metrics
    from wibreathe.eval.report import generate_evaluation_report

    est_path = Path(args.est)
    ref_path = Path(args.ref)
    out_dir = Path(args.out)

    if not est_path.is_file():
        print(f"Error: Estimated data file not found: {est_path}")
        return 1
    if not ref_path.is_file():
        print(f"Error: Reference data file not found: {ref_path}")
        return 1

    # Read estimated windows from CSV
    est_windows = []
    with open(est_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) >= 4:
                try:
                    t = float(row[0])
                    bpm = float(row[1]) if row[1] != "" and row[1].lower() != "none" else None
                    conf = float(row[2]) if row[2] != "" else 0.0
                    st = row[3]
                    est_windows.append((t, bpm, conf, st))
                except ValueError:
                    continue

    aligned = align_reference_and_estimates(est_windows, ref_path)
    if not aligned:
        print("Warning: No aligned windows could be matched between reference and estimates.")
        return 1

    resp_m, event_m = compute_evaluation_metrics(aligned)
    report_path = generate_evaluation_report(aligned, resp_m, event_m, out_dir)
    print(f"Evaluation report generated at: {report_path}")
    print(f"  MAE  : {resp_m.mae:.2f} bpm")
    print(f"  RMSE : {resp_m.rmse:.2f} bpm")
    print(f"  Cov  : {resp_m.coverage_pct:.1f}%")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI dispatcher."""
    parser = argparse.ArgumentParser(
        prog="wibreathe",
        description="WiBreathe: Contactless Respiration and Bed-Exit Monitoring using ESP32 Wi-Fi CSI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-ports
    subparsers.add_parser("list-ports", help="List available serial ports")

    # check-link
    p_check = subparsers.add_parser("check-link", help="Verify receiver CSI data link")
    p_check.add_argument("-p", "--port", required=True, help="Serial device port (e.g. /dev/cu.usbserial-10 or COM3)")
    p_check.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    p_check.add_argument("--mac", help="Filter by transmitter MAC address")

    # record
    p_rec = subparsers.add_parser("record", help="Record raw CSI packets to CSV")
    p_rec.add_argument("-p", "--port", required=True, help="Serial device port")
    p_rec.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate")
    p_rec.add_argument("-o", "--out-dir", default="data/raw", help="Output directory")
    p_rec.add_argument("--trial", default="T01", help="Trial identifier")
    p_rec.add_argument("--volunteer", required=True, help="Participant code matching ^V[0-9]{2,3}$ (e.g. V01)")
    p_rec.add_argument("--mode", default="normal", help="Breathing mode (normal, slow, fast, hold, empty, movement, bed_exit)")
    p_rec.add_argument("--posture", default="supine", help="Body posture")
    p_rec.add_argument("--position", default="center", help="Bed position")
    p_rec.add_argument("--consent", action="store_true", required=True, help="Affirm informed participant consent")
    p_rec.add_argument("-c", "--config", help="Configuration file path")
    p_rec.add_argument("--notes", default="", help="Observational notes")

    # simulate
    p_sim = subparsers.add_parser("simulate", help="Generate synthetic CSI data")
    p_sim.add_argument("--scenario", choices=["still", "empty", "movement", "bed_exit"], default="still")
    p_sim.add_argument("--bpm", type=float, default=15.0, help="Target respiration rate (BPM)")
    p_sim.add_argument("-d", "--duration", type=float, default=60.0, help="Duration in seconds")
    p_sim.add_argument("-r", "--rate", type=float, default=50.0, help="Packet sample rate in Hz")
    p_sim.add_argument("--out", default="data/raw/synthetic_csi.csv", help="Output CSV path")
    p_sim.add_argument("--labels", default="data/reference/synthetic_labels.csv", help="Ground truth labels path")
    p_sim.add_argument("--seed", type=int, default=42, help="Random seed")

    # plot
    p_plot = subparsers.add_parser("plot", help="Generate offline analysis plots")
    p_plot.add_argument("csv", help="Path to raw CSI CSV file")
    p_plot.add_argument("-o", "--out", help="Output PNG path")

    # replay
    p_rep = subparsers.add_parser("replay", help="Replay recorded CSI CSV stream")
    p_rep.add_argument("-f", "--file", required=True, help="Path to recorded CSV file")
    p_rep.add_argument("-s", "--speed", type=float, default=1.0, help="Replay speed multiplier")
    p_rep.add_argument("--fast", action="store_true", help="Replay as fast as possible without real-time delays")

    # tap
    p_tap = subparsers.add_parser("tap", help="Reference tap logger for ground-truth breath recording")
    p_tap.add_argument("--volunteer", required=True, help="Participant code (e.g. V01)")
    p_tap.add_argument("--mode", default="normal", help="Breathing mode")
    p_tap.add_argument("-o", "--out", help="Custom output reference CSV path")

    # eval
    p_ev = subparsers.add_parser("eval", help="Compute quantitative benchmark evaluation metrics")
    p_ev.add_argument("--est", required=True, help="Estimated window states/rates CSV")
    p_ev.add_argument("--ref", required=True, help="Reference ground truth CSV")
    p_ev.add_argument("-o", "--out", default="data/results", help="Output directory for reports")

    args = parser.parse_args(argv)

    if args.command == "list-ports":
        return cmd_list_ports(args)
    elif args.command == "check-link":
        return cmd_check_link(args)
    elif args.command == "record":
        return cmd_record(args)
    elif args.command == "simulate":
        return cmd_simulate(args)
    elif args.command == "plot":
        return cmd_plot(args)
    elif args.command == "replay":
        return cmd_replay(args)
    elif args.command == "tap":
        return cmd_tap(args)
    elif args.command == "eval":
        return cmd_eval(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
