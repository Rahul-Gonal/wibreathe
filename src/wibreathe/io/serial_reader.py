"""Background serial port reader with automatic reconnection and bounded buffering."""

from __future__ import annotations

import glob
import logging
import queue
import sys
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None  # type: ignore


def list_serial_ports() -> List[str]:
    """Discover available serial communication ports cross-platform.
    
    Prefers macOS /dev/cu.* ports over /dev/tty.* to avoid blocking on modem control lines.
    Shows COMx on Windows and /dev/ttyUSB* / /dev/ttyACM* on Linux.
    """
    found: List[str] = []
    if serial is not None:
        try:
            for p in serial.tools.list_ports.comports():
                dev = p.device
                # On macOS, prefer 'cu.*' devices over 'tty.*'
                if sys.platform.startswith("darwin") and dev.startswith("/dev/tty."):
                    cu_dev = dev.replace("/dev/tty.", "/dev/cu.")
                    found.append(cu_dev)
                else:
                    found.append(dev)
            if found:
                return sorted(list(set(found)))
        except Exception as e:
            logger.debug("serial.tools.list_ports error: %s", e)

    # Fallback heuristic port matching
    if sys.platform.startswith("darwin"):
        found.extend(glob.glob("/dev/cu.usbserial*"))
        found.extend(glob.glob("/dev/cu.SLAB_USBtoUART*"))
        found.extend(glob.glob("/dev/cu.wchusbserial*"))
        found.extend(glob.glob("/dev/cu.usbmodem*"))
    elif sys.platform.startswith("linux"):
        found.extend(glob.glob("/dev/ttyUSB*"))
        found.extend(glob.glob("/dev/ttyACM*"))
    elif sys.platform.startswith("win"):
        found.extend([f"COM{i}" for i in range(1, 65)])
    return sorted(list(set(found)))


class SerialReader:
    """Threaded serial port reader with thread-safe output queue and auto-reconnect."""

    def __init__(
        self,
        port: str,
        baud_rate: int = 921600,
        timeout_sec: float = 1.0,
        reconnect_delay_sec: float = 2.0,
        max_queue_size: int = 2000,
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout_sec = timeout_sec
        self.reconnect_delay_sec = reconnect_delay_sec
        self.line_queue: queue.Queue[str] = queue.Queue(maxsize=max_queue_size)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._serial_handle: Optional[serial.Serial] = None
        self._serial_lock = threading.Lock()
        self._synced: bool = False  # True once valid CSI_DATA line is reached after connect

        # Operational statistics
        self.lines_read_count: int = 0
        self.csi_lines_count: int = 0
        self.status_lines_count: int = 0
        self.dropped_queue_count: int = 0
        self.reconnect_count: int = 0
        self.is_connected: bool = False

    def start(self) -> None:
        """Start background polling thread."""
        if self._running:
            return
        if serial is None:
            raise RuntimeError(
                "pyserial package is not installed. Please install with 'pip install pyserial'"
            )
        self._running = True
        self._thread = threading.Thread(
            target=self._reader_loop,
            name=f"SerialReader-{self.port}",
            daemon=True,
        )
        self._thread.start()
        logger.info("SerialReader background worker started on %s @ %d baud", self.port, self.baud_rate)

    def stop(self, timeout: float = 2.0) -> None:
        """Stop background worker and safely close open handle."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        with self._serial_lock:
            if self._serial_handle and self._serial_handle.is_open:
                try:
                    self._serial_handle.close()
                except Exception as e:
                    logger.debug("Error closing serial port: %s", e)
            self._serial_handle = None
            self.is_connected = False
        logger.info("SerialReader stopped")

    def write_line(self, message: str) -> bool:
        """Thread-safe line writer to allow sending commands back to the receiver."""
        with self._serial_lock:
            if not self._serial_handle or not self._serial_handle.is_open:
                return False
            try:
                data = message.strip().encode("ascii", errors="replace") + b"\n"
                self._serial_handle.write(data)
                self._serial_handle.flush()
                return True
            except Exception as e:
                logger.error("Failed to write line to serial: %s", e)
                return False

    def get_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """Fetch next received line from the queue."""
        try:
            return self.line_queue.get(block=timeout is not None, timeout=timeout)
        except queue.Empty:
            return None

    def _reader_loop(self) -> None:
        """Core loop handling connections, streaming reads, and recovery."""
        buffer = bytearray()

        while self._running:
            # Connection management: open port without pulsing DTR/RTS lines
            if not self._serial_handle or not self._serial_handle.is_open:
                try:
                    logger.info("Attempting connection to %s without pulsing DTR/RTS...", self.port)
                    new_handle = serial.Serial()
                    new_handle.port = self.port
                    new_handle.baudrate = self.baud_rate
                    new_handle.bytesize = serial.EIGHTBITS
                    new_handle.parity = serial.PARITY_NONE
                    new_handle.stopbits = serial.STOPBITS_ONE
                    new_handle.timeout = self.timeout_sec
                    # Explicitly suppress DTR/RTS to avoid resetting ESP32 on connect
                    new_handle.dtr = False
                    new_handle.rts = False
                    new_handle.open()

                    with self._serial_lock:
                        self._serial_handle = new_handle
                        self.is_connected = True
                        self._synced = False  # Reset sync until first valid CSI line
                    self.reconnect_count += 1
                    logger.info("Successfully connected to %s", self.port)
                except Exception as e:
                    self.is_connected = False
                    logger.warning("Could not open serial port %s: %s. Retrying in %.1fs...", self.port, e, self.reconnect_delay_sec)
                    time.sleep(self.reconnect_delay_sec)
                    continue

            # Stream reading
            try:
                chunk = self._serial_handle.read(256)
                if not chunk:
                    continue

                buffer.extend(chunk)
                while b"\n" in buffer:
                    newline_idx = buffer.index(b"\n")
                    raw_line = buffer[:newline_idx]
                    del buffer[: newline_idx + 1]

                    line_str = raw_line.decode("utf-8", errors="replace").strip("\r")
                    if not line_str:
                        continue

                    # Discard bootloader ROM text until first valid CSI_DATA line appears
                    if not self._synced:
                        if line_str.startswith("CSI_DATA,"):
                            self._synced = True
                            logger.info("Detected initial CSI_DATA stream sync.")
                        elif line_str.startswith("type,id,mac") or line_str.startswith("STATUS,"):
                            # Header or status is acceptable sync marker as well
                            self._synced = True
                        else:
                            # Discard boot ROM text / chip reset logs
                            continue

                    self.lines_read_count += 1
                    if line_str.startswith("CSI_DATA,"):
                        self.csi_lines_count += 1
                    elif line_str.startswith("STATUS,"):
                        self.status_lines_count += 1

                    try:
                        self.line_queue.put_nowait(line_str)
                    except queue.Full:
                        self.dropped_queue_count += 1
                        if self.dropped_queue_count % 100 == 1:
                            logger.warning("SerialReader queue overflow! Dropped %d lines so far.", self.dropped_queue_count)

            except Exception as e:
                logger.warning("Serial read error on %s: %s. Reconnecting...", self.port, e)
                with self._serial_lock:
                    if self._serial_handle:
                        try:
                            self._serial_handle.close()
                        except Exception:
                            pass
                        self._serial_handle = None
                    self.is_connected = False
                    self._synced = False
                time.sleep(self.reconnect_delay_sec)
