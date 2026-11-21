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
from wibreathe.state.calibration import BedCalibration, Calibrator, compute_inter_frame_metrics
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
    csv_file = getattr(args, "file", None) or getattr(args, "csv", None)
    if not csv_file:
        print("Error: Input CSV file is required for plotting.")
        return 1
    out = generate_offline_plots(csv_file, args.out)
    if out:
        print(f"Generated plot: {out}")
        return 0
    else:
        print("Note: Plot skipped (matplotlib not installed or CSV has insufficient data).")
        return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay recorded CSI CSV file through the shared DSP pipeline and state machine."""
    cfg = load_config(args.config)
    if getattr(args, "calibration", None):
        calib = BedCalibration.load(args.calibration)
    else:
        # Default to a baseline calibrated profile so replay yields valid state transitions and BPM
        calib = BedCalibration(is_calibrated=True, empty_calibrated=True, occupied_calibrated=True)

    pipeline = WiBreathePipeline(config=cfg, calibration=calib)
    replay = CSIReplay(args.file, realtime=not args.fast, speedup=args.speed)
    count = 0
    print(f"Starting replay of {args.file} (realtime={not args.fast}, speedup={args.speed}x)...")

    est_file = None
    if getattr(args, "est_out", None):
        est_path = Path(args.est_out)
        est_path.parent.mkdir(parents=True, exist_ok=True)
        est_file = open(est_path, "w", encoding="utf-8")
        est_file.write("t,bpm,confidence,state\n")

    last_est_write_t = -1.0
    last_bpm: Optional[float] = None
    last_conf: float = 0.0
    last_state = None
    last_res_t = 0.0

    try:
        for frame in replay.stream():
            res = pipeline.process_frame(frame)
            count += 1
            last_state = res.state
            last_res_t = res.timestamp_s
            if res.bpm is not None:
                last_bpm = res.bpm
                last_conf = res.confidence

            if est_file is not None and (res.timestamp_s - last_est_write_t >= 1.0 or count == 1):
                bpm_val = f"{res.bpm:.2f}" if res.bpm is not None else ""
                est_file.write(f"{res.timestamp_s:.2f},{bpm_val},{res.confidence:.1f},{res.state.value}\n")
                last_est_write_t = res.timestamp_s

            if count % 250 == 0:
                bpm_str = f"{res.bpm:.1f}" if res.bpm is not None else "--"
                print(
                    f"Replayed {count:5d} frames | t={res.timestamp_s:6.1f}s | "
                    f"State: {res.state.value:10s} | BPM: {bpm_str:4s} | Mot: {res.motion_metric:.3f}"
                )
    except KeyboardInterrupt:
        print("\nReplay paused by user.")
    finally:
        if est_file is not None:
            if last_state is not None:
                bpm_val = f"{last_bpm:.2f}" if last_bpm is not None else ""
                est_file.write(f"{last_res_t:.2f},{bpm_val},{last_conf:.1f},{last_state.value}\n")
            est_file.close()

    print(f"Replay complete: {count} frames processed through pipeline.")
    if last_bpm is not None:
        print(f"Final Estimated Respiration Rate: {last_bpm:.1f} BPM (Confidence: {last_conf:.1f}%, State: {last_state.value if last_state else 'STILL'})")
    elif last_state is not None:
        print(f"Final State: {last_state.value} (No stable respiration rate detected)")

    return 0


def cmd_live(args: argparse.Namespace) -> int:
    """Run real-time streaming pipeline from serial reader to state machine and optional display link."""
    cfg = load_config(args.config)
    calib = BedCalibration.load(args.calibration) if getattr(args, "calibration", None) else BedCalibration()
    pipeline = WiBreathePipeline(config=cfg, calibration=calib)

    reader = SerialReader(port=args.port, baud_rate=args.baud)
    parser = CSIParser(filter_mac=args.mac)

    disp_link = None
    if getattr(args, "display", False):
        disp_link = DisplayLink(reader=reader, publish_rate_hz=1.0)
        disp_link.start()

    record_file = None
    if getattr(args, "record", None):
        rec_path = Path(args.record)
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        record_file = open(rec_path, "w", encoding="utf-8")
        logger.info("Recording raw CSI stream to %s", rec_path)

    est_file = None
    if getattr(args, "est_out", None):
        est_path = Path(args.est_out)
        est_path.parent.mkdir(parents=True, exist_ok=True)
        est_file = open(est_path, "w", encoding="utf-8")
        est_file.write("t,bpm,confidence,state\n")
        logger.info("Writing live estimates to %s", est_path)

    if getattr(args, "headless", False):
        logger.info("Running in headless terminal mode.")

    print(f"Starting WiBreathe live processing on {args.port} @ {args.baud} baud...")
    print("Press Ctrl+C to stop.")
    reader.start()

    last_print_t = time.time()
    last_est_write_t = time.time()
    try:
        while True:
            line = reader.get_line(timeout=0.2)
            if line:
                if record_file is not None:
                    record_file.write(line + "\n")
                frame = parser.parse_line(line)
                if frame:
                    res = pipeline.process_frame(frame)
                    if disp_link:
                        disp_link.update_values(res.state, res.bpm, res.confidence)

                    now = time.time()
                    if est_file is not None and now - last_est_write_t >= 1.0:
                        bpm_val = f"{res.bpm:.2f}" if res.bpm is not None else ""
                        est_file.write(f"{res.timestamp_s:.2f},{bpm_val},{res.confidence:.1f},{res.state.value}\n")
                        est_file.flush()
                        last_est_write_t = now

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
        if record_file is not None:
            record_file.close()
        if est_file is not None:
            est_file.close()
        reader.stop()

    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Calibrate empty bed or occupied stillness baseline profile."""
    mode = getattr(args, "mode", "guided").lower()
    default_ext = ".yaml" if getattr(args, "out", "") and str(args.out).endswith((".yaml", ".yml")) else ".json"
    out_path = Path(args.out) if getattr(args, "out", None) else Path(f"config/calibration_{mode}{default_ext}")
    calibrator = Calibrator()

    def _collect_frames_from_file(file_path: str | Path) -> List[Any]:
        rep = CSIReplay(file_path, realtime=False)
        return list(rep.stream())

    def _collect_frames_from_serial(port: str, baud: int, duration_sec: float) -> List[Any]:
        rdr = SerialReader(port=port, baud_rate=baud)
        psr = CSIParser()
        rdr.start()
        frames = []
        st = time.time()
        try:
            while time.time() - st < duration_sec:
                l = rdr.get_line(timeout=0.2)
                if l:
                    fr = psr.parse_line(l)
                    if fr:
                        frames.append(fr)
        finally:
            rdr.stop()
        return frames

    if mode == "guided":
        empty_frames: List[Any] = []
        occupied_frames: List[Any] = []

        if getattr(args, "empty_file", None) and getattr(args, "occupied_file", None):
            print(f"Guided calibration: loading empty baseline from {args.empty_file}...")
            empty_frames = _collect_frames_from_file(args.empty_file)
            print(f"Guided calibration: loading occupied baseline from {args.occupied_file}...")
            occupied_frames = _collect_frames_from_file(args.occupied_file)
        else:
            if not getattr(args, "port", None):
                print("Error: For guided calibration, provide either (--empty-file AND --occupied-file) or --port.")
                return 1
            dur = float(args.duration)
            print("=== GUIDED CALIBRATION (Step 1/2): EMPTY BED ===")
            print(f"Ensure the cot/bed is completely empty. Starting {dur:.0f}s recording in 1 second...")
            time.sleep(1.0)
            empty_frames = _collect_frames_from_serial(args.port, args.baud, dur)

            print("\n=== GUIDED CALIBRATION (Step 2/2): OCCUPIED STILLNESS ===")
            print("Please lie still on the cot/bed in your normal resting position.")
            print(f"Starting {dur:.0f}s recording in 1 second...")
            time.sleep(1.0)
            occupied_frames = _collect_frames_from_serial(args.port, args.baud, dur)

        if not empty_frames or not occupied_frames:
            print("Error: Guided calibration requires non-empty frames for both stages.")
            return 1

        empty_mads = compute_inter_frame_metrics(empty_frames)
        occupied_mads = compute_inter_frame_metrics(occupied_frames)

        calibrator.process_empty_bed_data(empty_mads)
        calibrator.process_occupied_still_data(occupied_mads)

    elif mode == "empty":
        if getattr(args, "file", None) or getattr(args, "empty_file", None):
            f_path = args.file or getattr(args, "empty_file", None)
            print(f"Calibrating empty bed baseline from file {f_path}...")
            frames = _collect_frames_from_file(f_path)
        else:
            if not getattr(args, "port", None):
                print("Error: Either --port or --file must be specified for calibration.")
                return 1
            print(f"Recording {args.duration}s empty bed baseline from {args.port}...")
            frames = _collect_frames_from_serial(args.port, args.baud, float(args.duration))

        if not frames:
            print("Error: No CSI frames acquired during calibration session.")
            return 1
        mads = compute_inter_frame_metrics(frames)
        calibrator.process_empty_bed_data(mads)

    elif mode == "occupied":
        if getattr(args, "file", None) or getattr(args, "occupied_file", None):
            f_path = args.file or getattr(args, "occupied_file", None)
            print(f"Calibrating occupied stillness baseline from file {f_path}...")
            frames = _collect_frames_from_file(f_path)
        else:
            if not getattr(args, "port", None):
                print("Error: Either --port or --file must be specified for calibration.")
                return 1
            print(f"Recording {args.duration}s occupied stillness baseline from {args.port}...")
            frames = _collect_frames_from_serial(args.port, args.baud, float(args.duration))

        if not frames:
            print("Error: No CSI frames acquired during calibration session.")
            return 1
        mads = compute_inter_frame_metrics(frames)
        calibrator.process_occupied_still_data(mads)

    calibrator.calibration.save(out_path)
    print(f"Profile saved to: {out_path}")
    print(f"  Empty bed baseline MAD   : {calibrator.calibration.empty_bed_variance:.4f} (calibrated={calibrator.calibration.empty_calibrated})")
    print(f"  Occupied baseline MAD    : {calibrator.calibration.occupied_still_variance:.4f} (calibrated={calibrator.calibration.occupied_calibrated})")
    print(f"  Profile Status           : {'CALIBRATED' if calibrator.calibration.is_calibrated else 'PARTIAL / UNCALIBRATED (requires both empty and occupied)'}")
    return 0


def cmd_tap(args: argparse.Namespace) -> int:
    """Run interactive terminal reference tap tool for ground-truth breath logging."""
    from wibreathe.eval.reference_tap import ReferenceTapLogger

    out_file = args.out
    if not out_file:
        out_file = f"data/reference/{args.volunteer}_{args.mode}_ref.csv"

    session = getattr(args, "session", "")
    logger_inst = ReferenceTapLogger(out_file, volunteer_code=args.volunteer, session=session)
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
    p_rec.add_argument("--consent", action="store_true", default=True, help="Affirm informed participant consent")
    p_rec.add_argument("-c", "--config", help="Configuration file path")
    p_rec.add_argument("--notes", default="", help="Observational notes")

    # simulate
    p_sim = subparsers.add_parser("simulate", help="Generate synthetic CSI data")
    p_sim.add_argument("--scenario", choices=["still", "empty", "movement", "bed_exit"], default="still")
    p_sim.add_argument("--bpm", type=float, default=15.0, help="Target respiration rate (BPM)")
    p_sim.add_argument("-d", "--duration", type=float, default=60.0, help="Duration in seconds")
    p_sim.add_argument("-r", "--rate", type=float, default=50.0, help="Packet sample rate in Hz")
    p_sim.add_argument("-o", "--out", "--output", dest="out", default="data/raw/synthetic_csi.csv", help="Output CSV path")
    p_sim.add_argument("--labels", default="data/reference/synthetic_labels.csv", help="Ground truth labels path")
    p_sim.add_argument("--seed", type=int, default=42, help="Random seed")

    # plot
    p_plot = subparsers.add_parser("plot", help="Generate offline analysis plots")
    p_plot.add_argument("csv", nargs="?", default=None, help="Path to raw CSI CSV file")
    p_plot.add_argument("-f", "--file", dest="file", help="Path to raw CSI CSV file")
    p_plot.add_argument("-o", "--out", "--output", dest="out", help="Output PNG path")

    # replay
    p_rep = subparsers.add_parser("replay", help="Replay recorded CSI CSV stream through pipeline")
    p_rep.add_argument("-f", "--file", required=True, help="Path to recorded CSV file")
    p_rep.add_argument("-s", "--speed", type=float, default=1.0, help="Replay speed multiplier")
    p_rep.add_argument("--fast", action="store_true", help="Replay as fast as possible without delays")
    p_rep.add_argument("--calibration", help="Path to calibration profile JSON/YAML")
    p_rep.add_argument("--est-out", help="Output CSV path for estimated t,bpm,confidence,state")
    p_rep.add_argument("-c", "--config", help="Configuration file path")

    # live
    p_live = subparsers.add_parser("live", help="Live streaming pipeline and monitoring")
    p_live.add_argument("-p", "--port", required=True, help="Serial device port")
    p_live.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate")
    p_live.add_argument("--mac", help="Filter by transmitter MAC address")
    p_live.add_argument("--calibration", help="Path to calibration profile JSON/YAML")
    p_live.add_argument("--est-out", help="Output CSV path for estimated t,bpm,confidence,state")
    p_live.add_argument("--record", help="Output CSV path for raw recorded CSI stream")
    p_live.add_argument("--display", action="store_true", help="Enable downstream OLED display link")
    p_live.add_argument("--headless", action="store_true", help="Run without graphical UI")
    p_live.add_argument("-c", "--config", help="Configuration file path")

    # calibrate
    p_cal = subparsers.add_parser("calibrate", help="Calibrate empty bed or occupied stillness baseline")
    p_cal.add_argument("--mode", choices=["empty", "occupied", "guided"], default="guided", help="Calibration target mode")
    p_cal.add_argument("-p", "--port", help="Serial port to record baseline from")
    p_cal.add_argument("-b", "--baud", type=int, default=921600, help="Baud rate")
    p_cal.add_argument("-d", "--duration", type=float, default=30.0, help="Duration in seconds")
    p_cal.add_argument("-f", "--file", help="Calibrate from existing CSV file instead of live port")
    p_cal.add_argument("--empty-file", help="Path to empty bed CSV recording")
    p_cal.add_argument("--occupied-file", help="Path to occupied stillness CSV recording")
    p_cal.add_argument("-o", "--out", "--output", dest="out", help="Output calibration file path (.json or .yaml)")

    # tap
    p_tap = subparsers.add_parser("tap", help="Reference tap logger for ground-truth breath recording")
    p_tap.add_argument("--volunteer", required=True, help="Participant code (e.g. V01)")
    p_tap.add_argument("--mode", default="normal", help="Breathing mode")
    p_tap.add_argument("-s", "--session", default="", help="Session identifier")
    p_tap.add_argument("-o", "--out", "--output", dest="out", help="Custom output reference CSV path")

    # eval & evaluate
    for ev_cmd in ("eval", "evaluate"):
        p_ev = subparsers.add_parser(ev_cmd, help="Compute quantitative benchmark evaluation metrics")
        p_ev.add_argument("--est", required=True, help="Estimated window states/rates CSV")
        p_ev.add_argument("--ref", required=True, help="Reference ground truth CSV")
        p_ev.add_argument("-o", "--out", "--output", dest="out", default="data/results", help="Output directory for reports")

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
