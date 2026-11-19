# WiBreathe: Contactless Respiration & Bed-Exit Detection Using Wi-Fi CSI on ESP32

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![ESP-IDF v5.x](https://img.shields.io/badge/ESP--IDF-v5.x-red.svg)](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Experiential Learning Prototype**  
**Institution**: RV College of Engineering, Bengaluru  
**Team**: Mugglelabs 




## 1. System Architecture

WiBreathe senses minute chest wall displacements (~1–12 mm) and gross occupant body movement by measuring subcarrier-level Channel State Information (CSI) from 802.11n Wi-Fi OFDM packets transmitted across a bed or cot.


## 2. Hardware Wiring & Bill of Materials

### 2.1 Bill of Materials
- **2x Classic ESP32 DevKit V1** (Module marked **ESP-32D N4**, classic ESP32, 4 MB flash, single USB-C port wired to CP2102 or CH340 USB-UART bridge)
- **1x 0.96" I2C OLED Display** (128x64 pixels, SSD1306 or SH1106 controller, address `0x3C`)
- **2x 4-wire USB data cables** (USB-C to USB-A or USB-C; avoid charge-only cables)
- **1x 5V USB battery pack or AC-DC adapter** (for isolated TX node operation)
- **4x Female-to-Female jumper wires** (< 15 cm)

### 2.2 Receiver Wiring Table
Connect the OLED display to the receiver board:

| OLED Pin | ESP32 DevKit V1 Pin | Pin Description / Location | Electrical Connection Notes |
| :---: | :---: | :--- | :--- |
| **GND** | **GND** | Ground pin | Common system ground. |
| **VCC** | **3V3** | **3.3V Output Rail** | **CRITICAL**: Connect to **3V3 ONLY**. Exceeding 3.6V (VIN/5V) will burn the display controller! |
| **SDA** | **GPIO 21 (D21)** | Hardware I2C Serial Data (Silk screen **D21**) | Jumper wire to OLED SDA. Firmware enables internal pullup. |
| **SCL** | **GPIO 22 (D22)** | Hardware I2C Serial Clock (Silk screen **D22**) | Jumper wire to OLED SCL. Firmware enables internal pullup. |

> [!NOTE]
> **Secondary Guarded Options**:
> - ESP32-S3 DevKitC-1: `SDA = GPIO 8`, `SCL = GPIO 9`, `VCC = 3V3`, `GND = GND`.
> - ESP32-C3 DevKitM-1: `SDA = GPIO 4`, `SCL = GPIO 5`, `VCC = 3V3`, `GND = GND`.

---

## 3. Host Software Setup (macOS, Windows, and Linux)

WiBreathe supports Python **3.10 to 3.14** across macOS, Windows, and Linux equally. Always work inside a virtual environment.

### 3.1 Environment Activation & Installation

#### On macOS and Linux:
```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip and install package in editable mode
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

#### On Windows (PowerShell):
```powershell
# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Upgrade pip and install package in editable mode
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

### 3.2 Running the Test Suite
The official way to run tests is `pytest -q`:
```bash
pytest -q
```
*(If pytest is not installed, run the standalone runner: `python tests/run_all_tests.py`)*

---

## 4. Firmware Build & Flashing (ESP-IDF v5.x)

WiBreathe firmware builds with any modern ESP-IDF v5.x installed via the Espressif Installation Manager.
*(To record your build environment, run `idf.py --version`)*

### 4.1 Activate ESP-IDF Environment
- **macOS / Linux**: `source $HOME/esp/esp-idf/export.sh`
- **Windows (PowerShell)**: `. $HOME\esp\esp-idf\export.ps1`

### 4.2 Transmitter Node (`firmware/csi_tx`)
```bash
cd firmware/csi_tx

# Retarget to classic ESP32
idf.py set-target esp32

# Build and flash to TX node
idf.py build
idf.py -p <TX_PORT> -b 921600 flash monitor
```
*(Some DevKit clones require holding down the physical **BOOT** button during flashing)*

### 4.3 Receiver Node (`firmware/csi_rx`)
```bash
cd ../csi_rx

# Retarget to classic ESP32
idf.py set-target esp32

# (Optional) Select SH1106 controller if using non-SSD1306 OLED:
# idf.py menuconfig -> WiBreathe Receiver Configuration -> OLED Display Controller

# Build and flash to RX node
idf.py build
idf.py -p <RX_PORT> -b 921600 flash monitor
```

---

## 5. Command-Line Interface (CLI) Reference

All commands run via `python -m wibreathe <subcommand>`:

### 5.1 `list-ports`
List connected serial ports cross-platform (`/dev/cu.*` on macOS, `COMx` on Windows, `/dev/ttyUSB*` on Linux):
```bash
python -m wibreathe list-ports
```

### 5.2 `check-link`
Verify physical serial connection, packet ingress rate (~50 Hz), packet loss, and RSSI:
```bash
python -m wibreathe check-link --port <PORT> --baud 921600 --duration 10
```

### 5.3 `live`
Run live streaming pipeline from serial reader through DSP and state machine with optional OLED display link and plotting:
```bash
# Headless terminal dashboard
python -m wibreathe live --port <PORT> --baud 921600 --headless

# With OLED downlink feedback and calibration profile
python -m wibreathe live --port <PORT> --baud 921600 --display --calibration config/calibration_empty.json
```

### 5.4 `calibrate`
Learn baseline variance and stillness threshold for empty bed or occupied stillness:
```bash
python -m wibreathe calibrate --mode empty --file data/raw/csi_V00_empty_bed.csv --out config/calibration_empty.json
python -m wibreathe calibrate --mode occupied --file data/raw/csi_V01_still.csv --out config/calibration_occupied.json
```

### 5.5 `record`
Stream and log raw CSI packets to `data/raw/` with JSON metadata sidecars:
```bash
python -m wibreathe record --port <PORT> --baud 921600 --volunteer V01 --mode normal --duration 120
```

### 5.6 `simulate`
Generate biologically realistic synthetic CSI packets for testing without hardware:
```bash
python -m wibreathe simulate --duration 60 --rate 50.0 --scenario still --output data/raw/sim_still.csv --labels data/reference/labels_still.csv
```

### 5.7 `replay`
Replay recorded or synthetic CSV sessions through the unified pipeline:
```bash
python -m wibreathe replay --file data/raw/sim_still.csv --speed 1.0
```

### 5.8 `plot`
Generate 4-panel diagnostic PNG figures:
```bash
python -m wibreathe plot --file data/raw/sim_still.csv --out data/results/diagnostic_plot.png
```

### 5.9 `tap`
Cross-platform keystroke reference tap tool for synchronizing volunteer ground truth:
```bash
python -m wibreathe tap --volunteer V01 --mode normal --session 1
```

### 5.10 `eval` / `evaluate`
Compute quantitative benchmarks (MAE, RMSE, bias, coverage, confusion matrix, Bland-Altman):
```bash
python -m wibreathe eval --est data/processed/estimates.csv --ref data/reference/reference.csv -o data/results
```

---

## 6. Serial Bandwidth Budget Table

Throughput limit at 921,600 baud: **92.16 KB/s** ($921,600 / 10$ bits/byte).

| Configuration | `len` (int8) | Subcarriers | Avg Line Length | 25 Hz Stream | 50 Hz Stream (Default) | 100 Hz Stream | 921.6k Bus Load (50 Hz) | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **LLTF Only** (Classic ESP32) | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | **RECOMMENDED** |
| **HT-LTF Only** | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | Supported |
| **LLTF + HT-LTF** | **384** | 64 x 3 | **~1,100 bytes** | 27.5 KB/s | **55.0 KB/s** | **110.0 KB/s** (OVERFLOW) | **59.7%** | High load (100 Hz drops) |

---

## 7. Troubleshooting & Pitfalls Matrix

| # | Issue / Pitfall | Likely Cause | Handling & Resolution |
| :---: | :--- | :--- | :--- |
| **1** | **No port enumerates** | Charge-only USB cable | First troubleshooting check: replace with verified 4-wire data cable. |
| **2** | **Zero CSI packets received** | 802.11b rate default on ESP-NOW | ESP-NOW defaults to 802.11b DSSS, which lacks OFDM CSI. Firmware forces `WIFI_PHY_RATE_MCS0_LGI` HT20 rate. |
| **3** | **Garbled serial characters** | Baud rate mismatch | Ensure host reader is configured to `921600` baud. Check ESP32 crystal frequency (40 MHz default). |
| **4** | **OLED display is blank** | I2C pin mismatch or power error | Verify wiring: `SDA=GPIO 21 (D21)`, `SCL=GPIO 22 (D22)`, `VCC=3V3` (never VIN/5V). Add 4.7kΩ pullups if jumpers > 15 cm. |
| **5** | **Left 2 columns distorted on OLED** | Controller is SH1106, not SSD1306 | SH1106 has a 132-column internal RAM. Set `CONFIG_WIBREATHE_OLED_SH1106=y` in Kconfig to apply 2-column offset. |
| **6** | **Breathing peak suppressed or flatline** | Hampel filter clamping natural slope | Set `relative_floor_ratio: 0.15` in `config/default.yaml` to scale floor dynamically with signal amplitude. |
| **7** | **Subharmonic beats in respiration** | Windowed median filter on non-integer periods | Use linear or polynomial detrending; avoid short rolling median detrending on sine waves. |
| **8** | **CSI callback crashes on boot** | Dynamic memory allocation in Wi-Fi task | Never call `malloc()` or `printf()` inside callback. Callback uses non-blocking `xQueueSend(..., 0)`. |
| **9** | **Serial output interleaved mid-line** | Multiple tasks writing to UART0 simultaneously | Handled in firmware: `csi_print_task` is the single exclusive writer to UART0; log level set to `WARN`. |
| **10** | **OLED absent crashes firmware** | Blocking I2C driver on disconnected bus | Driver handles NACK cleanly: if display is disconnected, returns immediately with 0 delay. |
| **11** | **Spurious bed-exit alerts** | Empty bed threshold uncalibrated | Run `python -m wibreathe calibrate --mode empty` to establish real room baseline variance. |
| **12** | **Outlier burst on body turn** | Rapid multipath shift | Causal Hampel filter and dual-threshold MAD motion detector flag `MOVING` state to suppress spurious BPM. |
| **13** | **Linux port permission denied** | User not in dialout group | Run `sudo usermod -a -G dialout $USER`, log out, and log back in. |
| **14** | **macOS terminal blocks on serial open** | Using `/dev/tty.*` instead of `/dev/cu.*` | `list-ports` and `SerialReader` automatically prefer `/dev/cu.*` calling devices. |
| **15** | **DTR/RTS reset + ROM boot text** | Bridge DTR/RTS lines drive EN/GPIO0 | `SerialReader` opens port with `dtr=False, rts=False` before opening; discards boot text until first `CSI_DATA,` line. |
| **16** | **Brownout detector reset** | Wi-Fi current spikes (~450-500 mA) drop VDD3V3 | Firmware logs `esp_reset_reason()` at boot and warns prominently on `ESP_RST_BROWNOUT`; use powered USB hub. |
| **17** | **CSI length vs serial bandwidth overflow** | LLTF+HTLTF (384 values) @ 100 Hz requires ~110 KB/s | Exceeds 921,600 baud max throughput (92.16 KB/s). Default to LLTF-only (128 values) at 50 Hz (~19.5 KB/s). |

---

## 8. Data Organization

```
data/
├── raw/        # Raw CSI CSV files and JSON metadata sidecars (csi_V01_normal_*.csv, *.json)
├── processed/  # Resampled and filtered timeseries datasets
├── reference/  # Synchronized manual tap tool ground-truth CSVs
└── results/    # Evaluation markdown reports, CSV error metrics, and session PNG plots
```

---

## 9. Ethics, Privacy & Limitations

1. **Human Volunteer Ethics**: All human data collection must strictly follow [docs/TRIAL_PROTOCOL.md](file:///Users/rgo75/work/wibreathe/docs/TRIAL_PROTOCOL.md), including informed written consent and freedom to withdraw.
2. **Participant Anonymity**: Never record names or identifiable health details. Use strictly de-identified keys (`V01`, `V02`).
3. **RF Safety**: WiBreathe operates at standard Wi-Fi transmission power levels ($\le 20\text{ dBm}$ / $100\text{ mW}$ EIRP), well below FCC/ICNIRP human RF exposure limits.
4. **Multipath Sensitivity**: Performance is subject to multipath changes from other moving individuals in the room. Controlled, single-occupant experimental rooms are required.
5. **No Medical Claims**: Outputs represent experimental physical channel perturbations, not clinical respiratory parameters.
