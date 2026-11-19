# WiBreathe Hardware Bringup Checklist

This checklist guides engineering teams through bringing up new ESP32 / ESP32-S3 boards from unboxing to validated CSI packet streaming and live OLED telemetry. Follow these steps in exact sequential order.

---

## Bringup Quick Reference Flowchart

```
[ Step 1: Cable & Driver ] 
          |
[ Step 2: Flash TX Node (csi_tx) ]
          |
[ Step 3: Flash RX Node (csi_rx) ]
          |
[ Step 4: Python check-link (50 Hz, <2% loss) ]
          |
[ Step 5: Raw CSI Format Check (len=128, int8) ]
          |
[ Step 6: OLED Display & Downstream DISP Link ]
          |
[ Step 7: Dry Run End-to-End Recording ]
```

---

## Step 1: Physical Cable & Port Identification

- [ ] **Data vs Charge Cable Verification**:
  - Connect the board to your computer.
  - On macOS: run `ls /dev/cu.usb*` or `ls /dev/tty.usb*`.
  - On Linux: run `ls /dev/ttyUSB*` or `ls /dev/ttyACM*`.
  - On Windows: check Device Manager under "Ports (COM & LPT)".
  - *If no port appears*: Replace the USB cable. Most bundled cables are 2-wire charge-only. A verified 4-wire data cable is required.
- [ ] **ESP32-S3 Port Selection**:
  - Locate the two USB Type-C ports on the ESP32-S3 DevKitC-1:
    - Port 1: Labeled **UART** (connected to on-board CP2102N / CH343 USB-to-UART bridge).
    - Port 2: Labeled **USB** (connected directly to ESP32-S3 native USB-JTAG pins GPIO 19/20).
  - **Connect the cable exclusively to the UART port**. Native USB-JTAG can drop packets during continuous 921,600 baud streaming.
- [ ] **Driver Installation**:
  - If using Silicon Labs CP210x, verify driver is active.
  - If using WCH CH340/CH341, install the vendor VCP driver if not auto-detected by macOS/Windows.

---

## Step 2: Build & Flash Transmitter Node (`firmware/csi_tx`)

- [ ] Activate your ESP-IDF v5.x environment:
  ```bash
  # macOS / Linux
  . $HOME/esp/esp-idf/export.sh
  # Windows
  %userprofile%\esp\esp-idf\export.bat
  ```
- [ ] Navigate to the transmitter project directory:
  ```bash
  cd firmware/csi_tx
  ```
- [ ] Set target to ESP32-S3 (or classic `esp32` / `esp32c3` if using alternative silicon):
  ```bash
  idf.py set-target esp32s3
  ```
- [ ] Build the firmware:
  ```bash
  idf.py build
  ```
- [ ] Flash to TX node and open serial monitor:
  ```bash
  idf.py -p /dev/tty.usbserial-TX_PORT -b 921600 flash monitor
  ```
- [ ] **Verify Transmitter Boot Log**:
  - Look for: `I (XXX) csi_tx: WiBreathe CSI Transmitter starting up...`
  - Look for: `I (XXX) csi_tx: Wi-Fi initialized in Station mode on channel 6`
  - Look for: `I (XXX) csi_tx: ESP-NOW peer added. Forced rate: HT20 MCS0 (OFDM)`
  - Look for: Periodic heartbeat messages: `I (XXX) csi_tx: TX Heartbeat: sent=500 packets (rate ~50.0 Hz)`
- [ ] Exit monitor with `Ctrl + ]`. Disconnect or move TX node to its battery power supply.

---

## Step 3: Build & Flash Receiver Node (`firmware/csi_rx`)

- [ ] Wire the SSD1306 0.96" I2C OLED display to the RX node as detailed in [WIRING.md](file:///Users/rgo75/work/wibreathe/docs/WIRING.md):
  - `GND` -> `GND`
  - `VCC` -> `3V3`
  - `SDA` -> `GPIO 8` (S3) or `GPIO 21` (classic ESP32)
  - `SCL` -> `GPIO 9` (S3) or `GPIO 22` (classic ESP32)
- [ ] Navigate to the receiver project directory:
  ```bash
  cd ../csi_rx
  ```
- [ ] Set target and build:
  ```bash
  idf.py set-target esp32s3
  idf.py build
  ```
- [ ] Flash to RX node and monitor:
  ```bash
  idf.py -p /dev/tty.usbserial-RX_PORT -b 921600 flash monitor
  ```
- [ ] **Verify Receiver Boot Log**:
  - Look for: `I (XXX) csi_rx: WiBreathe CSI Receiver starting up...`
  - Look for: `I (XXX) ssd1306: SSD1306 OLED initialized successfully at address 0x3c`
  - Look for: `I (XXX) csi_rx: CSI promiscuous RX callback registered`
  - Confirm the OLED screen lights up and displays:
    ```
    +-------------------+
    |     WIBREATHE     |
    |  RVCE  Phase 2    |
    |  Init CSI RX...   |
    +-------------------+
    ```
  - When the TX node is running nearby on the same channel (Channel 6), verify raw CSV frames stream on the console:
    ```
    CSI_DATA,1,14:28:01.120,412000,-48,0,0,1,0,0,1,1,0,1,0,0,0,0,0,0,0,0,0,128,...
    ```
- [ ] Exit monitor with `Ctrl + ]`.

---

## Step 4: Python Link Quality Verification (`check-link`)

Run the host CLI link verification tool to assess packet timing stability and channel loss:

```bash
python -m wibreathe check-link --port /dev/tty.usbserial-RX_PORT --baud 921600 --duration 10
```

- [ ] **Evaluate Output Criteria**:
  - `Average Packet Rate`: Must be between **48.0 Hz and 52.0 Hz** (nominal 50 Hz).
  - `Packet Loss Rate`: Must be **< 2.0%** (calculated from sequence number continuity).
  - `Packet Jitter (Std Dev)`: Must be **< 5.0 ms**.
  - `Mean RSSI`: Should be stronger than **-70 dBm** across a typical bedroom span (2–3 m).

*Pass Criteria*: All checks green. If packet loss exceeds 5%, check for Wi-Fi channel congestion on Channel 6.

---

## Step 5: CSI Payload Structure & I/Q Sanity Check

Run a short capture or use the Python parser to inspect frame contents:

- [ ] **Header Columns**:
  - Column 0: `CSI_DATA`
  - Column 23 (`len`): Must be exactly `128` (representing 64 subcarriers $\times$ 2 values per subcarrier: I and Q).
- [ ] **Data Sign & Dynamic Range**:
  - Subcarrier values must range from **-128 to +127** (signed 8-bit integers).
  - If values appear as positive integers between 128 and 255, the parser or firmware is incorrectly treating signed `int8_t` as `uint8_t`.
- [ ] **Null Subcarriers**:
  - Subcarrier indices [-32, -31, -30, -29] and [0] and [28, 29, 30, 31] should have near-zero or low-variance values corresponding to guard bands and DC carrier.
- [ ] **Verified I/Q Order**:
  - Wi-Fi PHY convention on ESP32 is **Imaginary first, then Real**:
    $$\text{CSI}[k] = Q[k] \cdot j + I[k] = \text{raw}[2k] \cdot j + \text{raw}[2k + 1]$$
  - Verify that `wibreathe.io.parser` decodes `csi_complex[k] = complex(real=raw[2*k+1], imag=raw[2*k])`.

---

## Step 6: OLED Display & Downstream DISP Link

- [ ] Confirm OLED screen behavior:
  - **No Host Connected / No Packets**: OLED displays `LINK: NO PACKETS`.
  - **Active CSI Streaming**: OLED displays `LINK: ACTIVE`, RSSI value, and packet count.
- [ ] Test downstream UART telemetry from host:
  - When the Python host pipeline is running (`record` or `replay`), it sends periodic downstream commands to the ESP32:
    `DISP:BPM=16.2,STATE=STILL,MOT=0.012\n`
  - Confirm the OLED switches to the telemetry dashboard:
    - Line 1: `STATE: STILL`
    - Line 2: `RATE:  16.2 BPM`
    - Line 3: `MOT:   0.012 (LOW)`
    - Line 4: `RSSI:  -48 dBm  50Hz`

---

## Step 7: Dry Run Recording & Signal Verification

- [ ] Execute a 30-second dry run recording:
  ```bash
  python -m wibreathe record \
      --port /dev/tty.usbserial-RX_PORT \
      --baud 921600 \
      --volunteer V00 \
      --mode bringup_test \
      --duration 30
  ```
- [ ] Inspect the generated files in `data/raw/`:
  - `V00_bringup_test_<TIMESTAMP>.csv` (approx. 1,500 rows, ~2.5 MB).
  - `V00_bringup_test_<TIMESTAMP>.json` (sidecar metadata with hardware details and drop counts).
- [ ] Generate offline verification plots:
  ```bash
  python -m wibreathe plot --file data/raw/V00_bringup_test_*.csv
  ```
- [ ] Open the generated PNG in `data/results/`:
  - Panel 1 (Raw Subcarriers): Distinct subcarrier power trajectories.
  - Panel 2 (Selected Subcarrier & Hampel): No clamped outliers or runaway baseline steps.
  - Panel 3 (Resampled & Bandpass Filtered): Clean baseline oscillation centered at 0.0.
  - Panel 4 (FFT Respiration Spectrum): Clear spectral peak in the physiological respiration band (0.1–0.5 Hz / 6–30 bpm).

---

## Step 8: Bringup Troubleshooting Matrix

| Symptom | Root Cause | Solution |
| :--- | :--- | :--- |
| Serial output is gibberish / scrambled characters | Baud rate mismatch | Ensure host terminal and serial monitor are set to `921600` baud. Check ESP32 crystal frequency in `sdkconfig` (40 MHz default). |
| Port does not enumerate when plugged in | Charge-only cable or wrong USB port | Replace cable with 4-wire USB data cable. On ESP32-S3, plug into the port labeled **UART**. |
| `check-link` reports 0 packets received | TX node not running or channel mismatch | Ensure TX is powered and set to Channel 6 (`CONFIG_WIBREATHE_WIFI_CHANNEL=6`). Ensure both nodes share the same Wi-Fi channel. |
| Packets received but `len != 128` (e.g. 384) | 40 MHz channel bandwidth (HT40) | Set channel bandwidth to HT20 (`WIFI_BANDWIDTH_HT20`). HT20 yields 64 subcarriers $\times 2 = 128$ bytes. |
| Packets received but CSI callback never fires | Packet transmitted as 802.11b DSSS | Wi-Fi PHY only generates CSI for OFDM frames. Verify TX uses `esp_now_set_peer_rate_config` with `WIFI_PHY_MODE_HT20` and `WIFI_PHY_RATE_MCS0_LGI`. |
| Packet loss > 15% | High 2.4 GHz interference | Change channel in both TX and RX `Kconfig` (e.g., channel 1 or 11 instead of 6). Rebuild and flash both nodes. |
| OLED displays nothing (blank screen) | Incorrect I2C GPIO pins or missing pullups | Check SDA/SCL wiring. On ESP32-S3, SDA=GPIO 8, SCL=GPIO 9. Add 4.7 kΩ pullup resistors to 3.3V if jumper wires exceed 15 cm. |
| Host parser reports dropped frames or serial buffer overflow | OS UART buffer saturation | Increase host serial buffer size or ensure no heavy CPU starvation is blocking the Python worker thread. |
