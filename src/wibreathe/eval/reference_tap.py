"""Interactive observer reference tap tool for ground-truth breath and event recording."""

from __future__ import annotations

import csv
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

REFERENCE_CSV_HEADER = ["host_mono_time", "host_wall_time", "event_type", "notes"]


class ReferenceTapLogger:
    """Records ground-truth breath taps and event tags using host high-precision timers."""

    def __init__(self, output_csv_path: str | Path, volunteer_code: str = "V01"):
        self.output_csv = Path(output_csv_path)
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        self.volunteer_code = volunteer_code
        self.events: List[Tuple[float, float, str, str]] = []

    def log_event(self, event_type: str, notes: str = "") -> None:
        """Record an event with synchronized host monotonic and epoch timestamps."""
        mono_t = time.monotonic()
        wall_t = time.time()
        self.events.append((mono_t, wall_t, event_type, notes))
        logger.info("Recorded event '%s' at mono=%.3f", event_type, mono_t)

    def save(self) -> None:
        """Write all logged events to CSV file."""
        with open(self.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(REFERENCE_CSV_HEADER)
            for mono_t, wall_t, ev_type, note in self.events:
                writer.writerow([f"{mono_t:.6f}", f"{wall_t:.6f}", ev_type, note])
        print(f"Reference log written to {self.output_csv} ({len(self.events)} events).")

    def run_interactive(self) -> None:
        """Start interactive terminal tap loop."""
        print("=" * 60)
        print("WiBreathe Reference Tap Tool")
        print("Participant Code:", self.volunteer_code)
        print("Controls:")
        print("  [SPACE] / [ENTER] : Tap at peak of each breath (inhalation)")
        print("  [m]               : Mark MOVEMENT onset")
        print("  [e]               : Mark BED-EXIT event")
        print("  [s]               : Mark SESSION START")
        print("  [q] / [x]         : Finish and save reference log")
        print("=" * 60)

        self.log_event("SESSION_START", "Observer began logging")

        # Windows-specific non-blocking console key input
        if sys.platform.startswith("win"):
            import msvcrt
            try:
                while True:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b" ", b"\r", b"\n"):
                            self.log_event("BREATH", "Tap")
                            print(f"\r[{len(self.events):4d}] BREATH logged at {time.strftime('%H:%M:%S')}   ", end="", flush=True)
                        elif ch in (b"m", b"M"):
                            self.log_event("MOVEMENT", "Observer mark")
                            print("\n>>> MOVEMENT MARKED <<<")
                        elif ch in (b"e", b"E"):
                            self.log_event("BED_EXIT", "Observer mark")
                            print("\n>>> BED-EXIT MARKED <<<")
                        elif ch in (b"q", b"Q", b"x", b"X"):
                            break
                    time.sleep(0.01)
            except KeyboardInterrupt:
                pass
        else:
            # macOS / Linux terminal raw mode
            try:
                import termios
                import tty
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    while True:
                        ch = sys.stdin.read(1)
                        if ch in (" ", "\r", "\n"):
                            self.log_event("BREATH", "Tap")
                            sys.stdout.write(f"\r[{len(self.events):4d}] BREATH logged...   ")
                            sys.stdout.flush()
                        elif ch in ("m", "M"):
                            self.log_event("MOVEMENT", "Observer mark")
                            sys.stdout.write("\r\n>>> MOVEMENT MARKED <<<\r\n")
                            sys.stdout.flush()
                        elif ch in ("e", "E"):
                            self.log_event("BED_EXIT", "Observer mark")
                            sys.stdout.write("\r\n>>> BED-EXIT MARKED <<<\r\n")
                            sys.stdout.flush()
                        elif ch in ("q", "Q", "x", "X", "\x03"):  # \x03 is Ctrl+C
                            break
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                # Fallback line-by-line input
                print("Raw keyboard unavailable. Enter: [Enter]=Breath, m=Move, e=Exit, q=Quit:")
                try:
                    while True:
                        line = input().strip().lower()
                        if line == "q":
                            break
                        elif line == "m":
                            self.log_event("MOVEMENT", "Observer mark")
                        elif line == "e":
                            self.log_event("BED_EXIT", "Observer mark")
                        else:
                            self.log_event("BREATH", "Tap")
                except KeyboardInterrupt:
                    pass

        self.log_event("SESSION_END", "Observer ended logging")
        self.save()
