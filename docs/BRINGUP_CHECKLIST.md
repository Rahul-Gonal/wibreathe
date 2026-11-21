# WiBreathe Hardware Bringup Checklist

> [!CAUTION]
> **ACADEMIC RESEARCH PROTOTYPE — NOT A MEDICAL DEVICE**  
> WiBreathe is designed solely for contactless RF sensing research and engineering education. It is not certified for medical diagnosis or vital signs monitoring.

---

This checklist guides engineering teams through bringing up new classic **ESP32 DevKit V1** boards (ESP-32D N4, 4 MB flash) from unboxing to validated CSI packet streaming and live OLED telemetry. Follow these steps in exact sequential order.

---

## Bringup Quick Reference Flowchart

```
[ Step 1: Cable & Bridge Port Check ] 
                  |
[ Step 2: Flash TX Node (csi_tx) ]
                  |
[ Step 3: Flash RX Node (csi_rx) ]
                  |
[ Step 4: Python check-link (First Software Verification) ]
                  |
[ Step 5: Confirm CSI len (128 vs 384) & Serial Budget ]
                  |
[ Step 6: Validate I/Q Order (UNVERIFIED) & Subcarrier Nulls ]
                  |
[ Step 7: OLED Controller & Downstream DISP Link ]
                  |
[ Step 8: Dry Run End-to-End Recording & State Verification ]
```

---

## Step 1: Physical Cable & Port Identification

- [ ] **Data vs Charge Cable Verification**:
  - Connect the board to your computer using a verified 4-wire USB-C data cable.
  - On macOS: run `python -m wibreathe list-ports` (or `ls /dev/cu.usb*`).
  - On Linux: run `python -m wibreathe list-ports` (or `ls /dev/ttyUSB*`).
  - On Windows: run `python -m wibreathe list-ports` (or Device Manager under "Ports (COM & LPT)").
  - *If no port appears*:
    1. **Primary troubleshooting suspect**: The cable is a 2-wire charge-only cable. Replace with a known 4-wire data cable.
    2. Verify USB-to-UART bridge drivers (Silicon Labs CP210x or WCH CH340) are installed.
    3. On Linux, ensure user is in the `dialout` or `uucp` group (`sudo usermod -a -G dialout $USER`).
- [ ] **Hardware Architecture**:
  - Each classic ESP32 DevKit V1 has **ONE USB-C port** wired to a CP2102 or CH340 bridge chip.
  - That single port carries both ESP-IDF boot/system logs and the 921,600 baud CSI data stream.
  - The bridge's DTR/RTS lines control `EN` and `GPIO0`. Opening the port can pulse EN and reset the board; the host serial reader automatically disables DTR/RTS.
  - *Bootloader Sync*: Some budget DevKit clones fail automatic reset into the ROM bootloader; if `idf.py flash` displays `Connecting........_____.....`, **press and hold the physical BOOT button** on the board until flashing starts.

---

## Step 2: Build & Flash Transmitter Node (`firmware/csi_tx`)

- [ ] Activate your ESP-IDF v5.x environment:
  ```bash
  # macOS / Linux
  . $HOME/esp/esp-idf/export.sh
  # Windows PowerShell
  . $HOME\esp\esp-idf\export.ps1
  ```
  *(Record output of `idf.py --version` for build reproducibility)*
- [ ] Navigate to transmitter directory:
  ```bash
  cd firmware/csi_tx
  ```
- [ ] Set target to classic `esp32`:
  ```bash
  idf.py set-target esp32
  ```
- [ ] Build and flash:
  ```bash
  idf.py build
  idf.py -p <TX_PORT> -b 921600 flash monitor
  ```
- [ ] **Verify Transmitter Boot Output**:
  - `esp_reset_reason()` logged at boot (e.g. `ESP_RST_POWERON`).
  - Clear message if brownout was detected previously (`ESP_RST_BROWNOUT`).
  - Wi-Fi initialized in Station mode on Channel 6.
  - ESP-NOW peer configured with forced rate `WIFI_PHY_RATE_MCS0_LGI` (HT20 MCS0 OFDM).
  - Pacing at 50 Hz using monotonic `esp_timer_get_time()`.
- [ ] Exit monitor with `Ctrl + ]`. Disconnect or connect TX node to standalone 5V power supply.

---

## Step 3: Build & Flash Receiver Node (`firmware/csi_rx`)

- [ ] Wire the 0.96" I2C OLED display to the classic ESP32 RX node ([docs/WIRING.md](WIRING.md)):
  - `GND` -> `GND`
  - `VCC` -> **3V3** (Never connect to VIN/5V!)
  - `SDA` -> **GPIO 21 (D21)**
  - `SCL` -> **GPIO 22 (D22)**
- [ ] Navigate to receiver directory:
  ```bash
  cd ../csi_rx
  ```
- [ ] Configure OLED controller if needed:
  ```bash
  idf.py menuconfig
  # WiBreathe Receiver Configuration -> OLED Display Controller -> SSD1306 (default) or SH1106
  ```
- [ ] Set target to classic `esp32`, build and flash:
  ```bash
  idf.py set-target esp32
  idf.py build
  idf.py -p <RX_PORT> -b 921600 flash monitor
  ```
- [ ] **Verify Receiver Boot Output**:
  - `esp_reset_reason()` logged at boot.
  - Default log level set to `WARN` in `sdkconfig.defaults` to prevent line interleaving with CSI data stream.
  - Serial bandwidth budget computed and logged:
    - LLTF only (128 values): ~390 bytes/line -> 19.5 KB/s @ 50 Hz (21.2% bus utilization).
    - Status warning if budget exceeds 80% of 921,600 baud capacity.
  - Non-blocking OLED initialization: if OLED is not connected, logs warning and boots cleanly without crashing or stalling CSI stream.
- [ ] Exit monitor with `Ctrl + ]`.

---

## Step 4: Python Link Quality Verification (`check-link`)

Always run `check-link` **first** before recording to verify software-observable link metrics:

```bash
python -m wibreathe check-link --port <RX_PORT> --baud 921600 --duration 10
```

- [ ] **Software-Observable Pass Criteria**:
  - `Port Accessible`: Port opened successfully without DTR/RTS pulse loop.
  - `Average Packet Rate`: **48.0 Hz – 52.0 Hz** (nominal 50 Hz).
  - `Packet Loss Rate`: **< 2.0%** (derived from sequence number continuity).
  - `Mean RSSI`: Stronger than **-70 dBm** across 2–3 m line-of-sight bed span.
  - *Troubleshooting if FAIL*:
    1. Check if USB cable is a charge-only cable.
    2. Check that transmitter node is powered and blinking/active.
    3. Ensure both nodes share Channel 6.

---

## Step 5: Confirm CSI Length (`len`) & Serial Bandwidth

Inspect the first received `CSI_DATA` lines:

- [ ] **Column Count**: Exactly 25 comma-delimited columns.
- [ ] **CSI Length Column (`len`)**:
  - Default classic ESP32 configuration: `len = 128` (LLTF only: 64 subcarriers $\times$ 2 values per subcarrier).
  - If `WIBREATHE_CSI_LLTF_HTLTF` is configured: `len = 384` (64 subcarriers $\times$ 3 LTF symbols $\times$ 2 values).
  - Confirm bus load is < 80% (LLTF at 50 Hz = ~19.5 KB/s, well within 92.16 KB/s limit).

---

## Step 6: Validate I/Q Order & Subcarrier Null Mapping

- [ ] **Dynamic Range**:
  - Raw values must be signed 8-bit integers ranging from **-128 to +127**.
  - Positive-only values (0–255) indicate signedness error.
- [ ] **I/Q Order [UNVERIFIED]**:
  - Specification order: `[Q0, I0, Q1, I1, ...]` (Q first, then I per subcarrier).
  - *Note*: Marked **UNVERIFIED** against Espressif reference firmware files until physical SDR/hardware test is performed.
- [ ] **Subcarrier Null Mapping**:
  - Theoretical null bins for HT20: guard subcarriers [-32..-29, 28..31] and DC [0].
  - *Data-driven detection*: WiBreathe includes empirical null detection (`detect_null_subcarriers()`) as fallback if subcarrier layout deviates on classic ESP32 silicon.

---

## Step 7: OLED Controller & Downstream DISP Link

- [ ] **OLED Display Verification**:
  - If OLED is connected, confirm initial splash screen shows `WIBREATHE` and `Init CSI RX...`.
  - If screen displays fuzzy snow on 2 leftmost columns, select `WIBREATHE_OLED_SH1106` in Kconfig for the 2-column shift.
  - If OLED is completely disconnected, confirm firmware continues streaming `CSI_DATA` without stalling.
- [ ] **Downstream DISP Message Protocol**:
  - Start live streaming pipeline:
    ```bash
    python -m wibreathe live --port <RX_PORT> --baud 921600 --display
    ```
  - Verify host sends canonical DISP messages:
    `DISP,<state>,<bpm or -->,<conf 0-100>,<seq>\n`
  - Confirm OLED displays updated state, breathing rate, and quality.

---

## Step 8: Dry Run Recording & State Verification

- [ ] Perform a 30-second dry run recording:
  ```bash
  python -m wibreathe record \
      --port <RX_PORT> \
      --baud 921600 \
      --volunteer V00 \
      --mode normal \
      --duration 30
  ```
- [ ] Verify generated files in `data/raw/`:
  - `csi_V00_normal_<TIMESTAMP>_part01.csv`
  - `metadata_V00_normal_<TIMESTAMP>.json`
- [ ] Replay through the pipeline:
  ```bash
  python -m wibreathe replay --file data/raw/csi_V00_normal_*.csv --fast
  ```
- [ ] Confirm state machine transitions cleanly (e.g. `NO_PERSON` -> `STILL` -> `MOVING` -> `EXIT`).
