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
from wibreathe.io.display_link import DisplayLink
from wibreathe.io.parser import CSIParser
from wibreathe.io.recorder import CSIRecorder, RecordingMetadata
from wibreathe.io.replay import CSIReplay
from wibreathe.io.serial_reader import SerialReader, list_serial_ports
from wibreathe.pipeline import WiBreathePipeline
from wibreathe.sim.synthetic import SyntheticGenerator, SyntheticScenario
from wibreathe.state.calibration import BedCalibration, Calibrator
from wibreathe.viz.offline_plots import generate_offline_plots

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wibreathe.cli")


def cmd_list_ports(args: argparse.Namespace) -> int:
    """List available serial communication ports."""
    ports = list_serial_ports()
    if not ports:
        print("No serial ports discovered.")
        print("Troubleshooting:")
        print("  1. Verify USB cable is a 4-wire data cable (not a charge-only cable).")
        print("  2. Ensure USB-UART bridge drivers (CP210x or CH340) are installed.")
        print("  3. On Linux, ensure your user belongs to the 'dialout' or 'uucp' group.")
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
        print("\nTroubleshooting:")
        print("  1. Verify USB cable is a 4-wire data cable (not a charge-only cable).")
        print("  2. Ensure driver (CP210x or CH340) is installed and port is not open elsewhere.")
        return 1

    listen_sec = max(1.0, float(getattr(args, "duration", 5.0)))
    print(f"Listening for {listen_sec:.1f} seconds...")
    start_time = time.time()
    frames_received = 0
    rssi_list: List[int] = []
    len_list: List[int] = []
    seq_list: List[int] = []

    while time.time() - start_time < listen_sec:
        line = reader.get_line(timeout=0.2)
        if line:
            frame = parser.parse_line(line)
            if frame:
                frames_received += 1
                rssi_list.append(frame.rssi)
                len_list.append(len(frame.raw_bytes))
                seq_list.append(frame.packet_id)

    reader.stop()

    duration = time.time() - start_time
    rate = frames_received / max(0.001, duration)

    # Compute loss from sequence numbers if possible
    loss_pct = 0.0
    if len(seq_list) > 1:
        seq_diff = seq_list[-1] - seq_list[0] + 1
        if seq_diff > len(seq_list):
            loss_pct = ((seq_diff - len(seq_list)) / seq_diff) * 100.0

    print("\n--- Link Check Results ---")
    print(f"Port           : {args.port}")
    print(f"Duration       : {duration:.2f} s")
    print(f"Frames Received: {frames_received}")
    print(f"Average Rate   : {rate:.1f} packets/sec")

    if frames_received > 0:
        avg_rssi = sum(rssi_list) / len(rssi_list)
        common_len = max(set(len_list), key=len_list.count)
        print(f"Average RSSI   : {avg_rssi:.1f} dBm")
        print(f"CSI Array Len  : {common_len} values ({common_len // 2} subcarriers)")
        print(f"Est. Packet Loss: {loss_pct:.1f}%")

        if rate >= 10.0:
            print("\nRESULT: [PASS] - Reliable CSI stream active.")
            return 0
        else:
            print("\nRESULT: [WARN] - CSI received but packet rate is low (<10 Hz).")
            return 0

    print("\nRESULT: [FAIL] - No CSI frames received.")
    print("Troubleshooting:")
    print("  1. Verify USB cable is a 4-wire data cable (charge-only cables cannot transfer data).")
    print("  2. Check that the transmitter node is powered and active.")
    print("  3. Check both TX and RX share the same Wi-Fi channel (default 6).")
    print("  4. Ensure transmitter sends packets at an OFDM rate (MCS0), not 802.11b.")
    print("  5. If TX MAC filter is set, verify it matches transmitter MAC address.")
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

    start_t = time.time()
    max_duration = float(args.duration) if args.duration else None

    try:
        while True:
            if max_duration and (time.time() - start_t) >= max_duration:
                print(f"\nReached target duration of {max_duration}s.")
                break
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
    """Replay recorded CSI CSV file through the shared DSP pipeline and state machine."""
    cfg = load_config(args.config)
    pipeline = WiBreathePipeline(config=cfg)
    replay = CSIReplay(args.file, realtime=not args.fast, speedup=args.speed)
    count = 0
    print(f"Starting replay of {args.file} (realtime={not args.fast}, speedup={args.speed}x)...")

    try:
        for frame in replay.stream():
            res = pipeline.process_frame(frame)
            count += 1
            if count % 250 == 0:
                bpm_str = f"{res.bpm:.1f}" if res.bpm is not None else "--"
                print(
                    f"Replayed {count:5d} frames | t={res.timestamp_s:6.1f}s | "
                    f"State: {res.state.value:10s} | BPM: {bpm_str:4s} | Mot: {res.motion_metric:.3f}"
                )
    except KeyboardInterrupt:
        print("\nReplay paused by user.")

    print(f"Replay complete: {count} frames processed through pipeline.")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    """Run real-time streaming pipeline from serial reader to state machine and optional display link."""
    cfg = load_config(args.config)
    calib = BedCalibration.load(args.calibration) if args.calibration else BedCalibration()
    pipeline = WiBreathePipeline(config=cfg, calibration=calib)

    reader = SerialReader(port=args.port, baud_rate=args.baud)
    parser = CSIParser(filter_mac=args.mac)

    disp_link = None
    if getattr(args, "display", False):
        disp_link = DisplayLink(reader=reader, publish_rate_hz=1.0)
        disp_link.start()

    print(f"Starting WiBreathe live processing on {args.port} @ {args.baud} baud...")
    print("Press Ctrl+C to stop.")
    reader.start()

    last_print_t = time.time()
    try:
        while True:
            line = reader.get_line(timeout=0.2)
            if line:
                frame = parser.parse_line(line)
                if frame:
                    res = pipeline.process_frame(frame)
                    if disp_link:
                        disp_link.update_values(res.state, res.bpm, res.confidence)

                    now = time.time()
                    if now - last_print_t >= 1.0:
                        bpm_str = f"{res.bpm:.1f}" if res.bpm is not None else "--"
                        print(
                            f"[LIVE] t={res.timestamp_s:6.1f}s | State: {res.state.value:11s} | "
                            f"BPM: {bpm_str:4s} | Qual: {res.confidence:3.0f}% | "
                            f"Mot: {res.motion_metric:.3f} | Pkts: {res.packet_rate_hz:4.1f}Hz"
                        )
                        last_print_t = now
    except KeyboardInterrupt:
        print("\nHalting live pipeline...")
    finally:
        if disp_link:
            disp_link.stop()
        reader.stop()

    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Calibrate empty bed or occupied stillness baseline profile."""
    mode = args.mode.lower()
    out_path = Path(args.out) if args.out else Path(f"config/calibration_{mode}.json")
    calibrator = Calibrator()

    if getattr(args, "file", None):
        print(f"Calibrating from file {args.file} (mode={mode})...")
        replay = CSIReplay(args.file, realtime=False)
        variances = []
        for frame in replay.stream():
            amps = frame.amplitudes
            mean_a = sum(amps) / len(amps)
            v = sum((a - mean_a) ** 2 for a in amps) / len(amps)
            variances.append(v)
    else:
        if not getattr(args, "port", None):
            print("Error: Either --port or --file must be specified for calibration.")
            return 1
        print(f"Recording {args.duration}s calibration baseline from {args.port} (mode={mode})...")
        reader = SerialReader(port=args.port, baud_rate=args.baud)
        parser = CSIParser()
        reader.start()
        start_t = time.time()
        variances = []
        try:
            while time.time() - start_t < float(args.duration):
                line = reader.get_line(timeout=0.2)
                if line:
                    frame = parser.parse_line(line)
                    if frame:
                        amps = frame.amplitudes
                        mean_a = sum(amps) / len(amps)
                        v = sum((a - mean_a) ** 2 for a in amps) / len(amps)
                        variances.append(v)
        finally:
            reader.stop()

    if not variances:
        print("Error: No CSI frames acquired during calibration session.")
        return 1

    if mode == "empty":
        calibrator.process_empty_bed_data(variances)
    else:
        calibrator.process_occupied_still_data(variances)

    calibrator.calibration.save(out_path)
    print(f"Calibration successful! Profile saved to: {out_path}")
    print(f"  Empty bed variance: {calibrator.calibration.empty_bed_variance:.4f}")
    print(f"  Occupied variance : {calibrator.calibration.occupied_still_variance:.4f}")
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
    p_check.add_argument("-p", "--port", required=True, help="Serial device port")
    p_check.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    p_check.add_argument("-d", "--duration", type=float, default=5.0, help="Listening duration in seconds")
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
    p_rec.add_argument("--duration", type=float, help="Optional duration limit in seconds")
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
    p_rep = subparsers.add_parser("replay", help="Replay recorded CSI CSV stream through pipeline")
    p_rep.add_argument("-f", "--file", required=True, help="Path to recorded CSV file")
    p_rep.add_argument("-s", "--speed", type=float, default=1.0, help="Replay speed multiplier")
    p_rep.add_argument("--fast", action="store_true", help="Replay as fast as possible without delays")
    p_rep.add_argument("-c", "--config", help="Configuration file path")

    # live
    p_live = subparsers.add_parser("live", help="Live streaming pipeline and monitoring")
    p_live.add_argument("-p", "--port", required=True, help="Serial device port")
    p_live.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate")
    p_live.add_argument("--mac", help="Filter by transmitter MAC address")
    p_live.add_argument("--calibration", help="Path to calibration profile JSON/YAML")
    p_live.add_argument("--display", action="store_true", help="Enable downstream OLED display link")
    p_live.add_argument("--headless", action="store_true", help="Run without graphical UI")
    p_live.add_argument("-c", "--config", help="Configuration file path")

    # calibrate
    p_cal = subparsers.add_parser("calibrate", help="Calibrate empty bed or occupied stillness baseline")
    p_cal.add_argument("--mode", choices=["empty", "occupied"], required=True, help="Calibration target mode")
    p_cal.add_argument("-p", "--port", help="Serial port to record baseline from")
    p_cal.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate")
    p_cal.add_argument("-d", "--duration", type=float, default=30.0, help="Duration in seconds")
    p_cal.add_argument("-f", "--file", help="Calibrate from existing CSV file instead of live port")
    p_cal.add_argument("-o", "--out", help="Output calibration JSON path")

    # tap
    p_tap = subparsers.add_parser("tap", help="Reference tap logger for ground-truth breath recording")
    p_tap.add_argument("--volunteer", required=True, help="Participant code (e.g. V01)")
    p_tap.add_argument("--mode", default="normal", help="Breathing mode")
    p_tap.add_argument("-o", "--out", help="Custom output reference CSV path")

    # eval & evaluate
    for ev_cmd in ("eval", "evaluate"):
        p_ev = subparsers.add_parser(ev_cmd, help="Compute quantitative benchmark evaluation metrics")
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
    elif args.command == "live":
        return cmd_live(args)
    elif args.command == "calibrate":
        return cmd_calibrate(args)
    elif args.command == "tap":
        return cmd_tap(args)
    elif args.command in ("eval", "evaluate"):
        return cmd_eval(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
