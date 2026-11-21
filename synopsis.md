# WiBreathe: Contactless Respiration & Bed-Exit Monitoring Using Wi-Fi CSI
## Comprehensive Project Synopsis, Architecture, Codebase Reference & Setup Manual

> [!CAUTION]
> **ACADEMIC RESEARCH PROTOTYPE — NOT A MEDICAL DEVICE**  
> WiBreathe is designed solely for engineering education and academic contactless RF sensing research (Experiential Learning Phase 2, RV College of Engineering). It has **NOT** undergone medical device certification (e.g., FDA 510(k), CE-MDR, CDSCO), is **NOT** clinically validated, and **MUST NEVER** be used for clinical diagnosis, patient monitoring, apnoea alarms, infant crib monitoring, or critical care life support.

---

## Table of Contents
1. [Executive Summary & Sensing Physics](#1-executive-summary--sensing-physics)
2. [Hardware Specification & Pinout](#2-hardware-specification--pinout)
3. [Firmware Architecture (`csi_tx` & `csi_rx`)](#3-firmware-architecture-csi_tx--csi_rx)
4. [Host Software & DSP Pipeline Architecture](#4-host-software--dsp-pipeline-architecture)
5. [Communication Protocols](#5-communication-protocols)
6. [Complete From-Scratch Setup Guide](#6-complete-from-scratch-setup-guide)
   - [6.1 Host Python Environment (macOS & Windows)](#61-host-python-environment-macos--windows)
   - [6.2 ESP-IDF v5.x Toolchain Setup](#62-esp-idf-v5x-toolchain-setup)
   - [6.3 Building & Flashing Firmware](#63-building--flashing-firmware)
   - [6.4 Verification & First Run](#64-verification--first-run)
7. [CLI Reference & Workflows](#7-cli-reference--workflows)
8. [Comprehensive Pitfalls & Troubleshooting Matrix (1–17)](#8-comprehensive-pitfalls--troubleshooting-matrix-117)
9. [Ethics, Privacy & Regulatory Boundaries](#9-ethics-privacy--regulatory-boundaries)

---

## 1. Executive Summary & Sensing Physics

### 1.1 Objective
WiBreathe is a non-invasive, contactless physiological sensing system capable of:
1. Continuously estimating human **respiration rate** (in breaths per minute, BPM) for a person resting in bed.
2. Detecting **gross body movement** (turning, adjusting blankets, sitting up).
3. Detecting **bed-exit events** within 6 seconds when a person leaves the cot or mattress.

### 1.2 Sensing Principle
Wi-Fi Channel State Information (CSI) measures how a wireless signal propagates from a transmitter to a receiver across multiple subcarrier frequencies.
- **Physical Channel**: In standard 802.11n 20 MHz (HT20) Wi-Fi at 2.4 GHz (Channel 6, 2.437 GHz), the carrier wavelength is $\lambda \approx 12.3\text{ cm}$.
- **Chest Wall Perturbation**: Normal eupneic human breathing moves the chest wall by approximately 1 to 12 mm. As the chest expands and contracts within the Fresnel reflection zone between the two antennas, it modulates the length of the reflected multipath RF paths.
- **Complex Baseband Representation**:
  For each OFDM subcarrier $k \in [-32 \dots 31]$, the received CSI value is a complex number:
  $$H_k = I_k + j \cdot Q_k = |H_k| e^{j \angle H_k}$$
  where $|H_k| = \sqrt{I_k^2 + Q_k^2}$ represents the subcarrier amplitude attenuation and $\angle H_k = \operatorname{atan2}(Q_k, I_k)$ represents the subcarrier phase.
- **Respiration Frequency**: Human breathing occurs in the frequency range $0.10\text{ Hz}$ to $0.50\text{ Hz}$ (6 to 30 breaths per minute). By tracking subcarrier amplitude fluctuations over time and applying digital bandpass filtering, the subtle quasi-periodic respiratory oscillations are extracted from background thermal noise and multipath clutter.

```
                  +-----------------------------------+
                  |                                   |
                  |             BED / COT             |
                  |                                   |
 +-------------+  |          (Human Occupant)         |  +-------------+
 |  TX Node    |==|===================================|==|  RX Node    |
 | (csi_tx)    |  |        Chest Wall Motion          |  | (csi_rx)    |
 +-------------+  |         (~1 - 12 mm)              |  +-------------+
       |          +-----------------------------------+        |
 5V USB Power                                            USB-C to Laptop
 (Power bank)                                            (921,600 baud)
                                                               |
                                                        [ OLED Display ]
                                                        (State & BPM)
```

---

## 2. Hardware Specification & Pinout

### 2.1 Bill of Materials
- **2x ESP32 DevKit V1 Boards**: Module marked **ESP-32D N4** (classic ESP32, dual Tensilica Xtensa LX6 cores @ 240 MHz, 4 MB SPI Flash, Wi-Fi 802.11 b/g/n, on-board CP2102 or CH340 USB-UART bridge with a single USB-C connector).
- **1x 0.96" I2C OLED Display**: 128x64 resolution, default I2C address `0x3C`. Supports SSD1306 controller (default) or SH1106 controller (with 2-column offset).
- **2x 4-Wire USB Data Cables**: USB-C to USB-A/C (must have all 4 wires connected: VBUS, GND, D+, D-).
- **4x Female-to-Female Jumper Wires**: Under 15 cm for low I2C bus capacitance.
- **1x Isolated 5V Power Supply**: Standard USB power bank or phone charger for the transmitter node.

### 2.2 Receiver Board Wiring Table

| OLED Display Pin | Classic ESP32 DevKit V1 Pin | Board Location / Silk Label | Electrical Specification |
| :---: | :---: | :--- | :--- |
| **GND** | **GND** | Any GND pin | Common circuit ground |
| **VCC** | **3V3** | **3.3V Output Rail** | **CRITICAL: Connect to 3V3 ONLY. Do NOT connect to VIN or 5V (over-voltage destroys OLED)** |
| **SDA** | **GPIO 21** | Pin labeled **D21** | I2C Serial Data line. Internal 45kΩ pull-up enabled by firmware |
| **SCL** | **GPIO 22** | Pin labeled **D22** | I2C Serial Clock line (400 kHz Fast-Mode). Internal pull-up enabled |

> [!NOTE]
> **Pin Identification (Classic 30-pin ESP32 DevKit V1)**:
> With the board held flat, the ESP-32D metal RF shield facing up and pointing away from you, and the USB-C port facing toward you:
> - **D21 (SDA)** is the 3rd pin from the bottom on the right-hand header row.
> - **D22 (SCL)** is the 6th pin from the top (or 2nd pin from the bottom depending on board variant) on the right-hand header row, labeled **D22**.

---

## 3. Firmware Architecture (`csi_tx` & `csi_rx`)

The firmware resides in `firmware/` and is developed for **ESP-IDF v5.x** targeting `esp32`.

```
firmware/
├── components/
│   └── wibreathe_common/
│       ├── include/wibreathe_common.h
│       └── ondevice_motion.c    # Lightweight inter-frame MAD calculation
├── csi_tx/
│   ├── sdkconfig.defaults       # target="esp32", 4MB flash, UART0 @ 921600
│   └── main/main.c              # 50 Hz monotonic pacing, ESP-NOW HT20 MCS0
└── csi_rx/
    ├── sdkconfig.defaults       # target="esp32", log level WARN, brownout enabled
    └── main/
        ├── Kconfig.projbuild    # OLED selection (SSD1306/SH1106), CSI set
        ├── ssd1306.c / .h       # Non-blocking I2C OLED driver
        └── main.c               # Wi-Fi task CSI CB, single UART0 writer task
```

### 3.1 Transmitter Node (`csi_tx`)
- Configured in **Station Mode** locked to Wi-Fi **Channel 6**.
- Transmits raw uncompressed ESP-NOW packets to broadcast or peer MAC at exactly **50 Hz** (20 ms interval) paced by `esp_timer_get_time()`.
- **Forced Modulation**: ESP-NOW defaults to 802.11b DSSS (1 Mbps), which produces zero CSI on hardware. `csi_tx` explicitly enforces OFDM using:
  ```c
  esp_now_rate_config_t rate_cfg = {
      .phymode = WIFI_PHY_MODE_HT20,
      .rate = WIFI_PHY_RATE_MCS0_LGI,
      .ersu = false,
  };
  esp_now_set_peer_rate_config(s_peer_mac, &rate_cfg);
  ```

### 3.2 Receiver Node (`csi_rx`)
- **Wi-Fi Task Context Decoupling**: The CSI callback registered with `esp_wifi_set_csi_rx_cb()` runs within the high-priority Wi-Fi task. It performs zero `malloc()`, zero `printf()`, and sends frames into a FreeRTOS queue using `xQueueSend(s_csi_queue, &item, 0)` with a 0-tick timeout. If full, it increments a drop counter without stalling Wi-Fi.
- **Single Exclusive Writer to UART0**: To prevent interleaved serial output, `csi_print_task` is the sole task permitted to write to UART0 via atomic `uart_write_bytes()`. System log level is set to `WARN` in `sdkconfig.defaults`.
- **Downstream Display Parser**: UART0 RX task monitors for downstream `DISP,<state>,<bpm>,<conf>,<seq>\n` commands in a bounded 64-byte circular buffer and updates the OLED.
- **Fault-Tolerant OLED**: If the OLED is disconnected or absent, the I2C driver detects NACK immediately and returns with 0 delay, ensuring the CSI stream is never blocked.
- **Dynamic Serial Bandwidth Budgeting**: Firmware calculates actual line length and data rate at boot, logging a warning if bandwidth exceeds 80% of 921,600 baud.

---

## 4. Host Software & DSP Pipeline Architecture

The host software is a modular Python package located in `src/wibreathe/`.

```
src/wibreathe/
├── config.py              # YAML/dataclass application configuration
├── pipeline.py            # Unified WiBreathePipeline driving DSP & state machine
├── cli.py                 # Command-line interface entrypoints
├── io/
│   ├── serial_reader.py   # Threaded serial reader, DTR/RTS safe, auto-reconnect
│   ├── parser.py          # 25-column CSV parser, signed int8 I/Q conversion
│   ├── display_link.py    # Downstream DISP packet formatter and publisher
│   ├── recorder.py        # Timestamped raw CSV recorder with JSON metadata
│   └── replay.py          # Frame replay engine with real-time or fast playback
├── dsp/
│   ├── quality.py         # Ingress packet rate, jitter, and loss tracking
│   ├── subcarriers.py     # Variance/SNR/PCA selection + empirical null detection
│   ├── hampel.py          # Causal Hampel filter with relative deviation floor
│   ├── resample.py        # Uniform grid linear interpolation (20 Hz)
│   ├── bandpass.py        # 4th-order dynamic Butterworth SOS biquad cascade
│   ├── breathing.py       # Sliding-window 8x zero-padded FFT respiration estimator
│   └── motion.py          # Broadband multi-carrier inter-frame MAD with hysteresis
├── state/
│   ├── machine.py         # Hierarchical finite state machine (6 states)
│   └── calibration.py     # Empty-bed and occupied-stillness variance baseline
├── sim/
│   └── synthetic.py       # Biologically realistic CSI synthesizer with ground truth
├── eval/
│   ├── reference_tap.py   # Raw single-key keystroke tap logger (cross-platform)
│   ├── align.py           # Temporal alignment of estimates and reference
│   ├── metrics.py         # MAE, RMSE, coverage, bias, confusion matrix
│   └── report.py          # Automated Markdown and CSV evaluation report generator
└── viz/
    ├── live_plot.py       # 3-panel live Matplotlib dashboard with headless fallback
    └── offline_plots.py   # 4-panel diagnostic analysis PNG generator
```

### 4.1 DSP Processing Flow
1. **Raw Frame Parsing**: Validates 25-column CSV line, unpacks bracketed int8 string into complex numbers:
   $$\text{CSI}[k] = \text{complex}(\text{real}=I_k, \text{imag}=Q_k)$$
   Unwraps microsecond hardware timer over 32-bit roll-overs.
2. **Subcarrier Selection & Null Filtering**:
   - Drops null/guard subcarriers (indices -32..-29, 0, 28..31) via static table or empirical zero-variance detection.
   - Evaluates subcarriers over a rolling 5-second window using variance, selecting the most respiration-sensitive subcarrier.
3. **Causal Hampel Filter (Outlier Rejection)**:
   - Rolling window of $W = 7$ samples ($350\text{ ms}$).
   - Computes median $\tilde{x}$ and $\text{MAD} = \operatorname{median}(|x_i - \tilde{x}|)$.
   - Dynamic threshold: $\tau = 3.0 \times 1.4826 \times \max(\text{MAD}, \text{floor}_{\text{rel}})$, where $\text{floor}_{\text{rel}} = 0.15 \times \text{median\_amplitude}$. This prevents natural breathing slopes from being clamped while removing high-amplitude impulse spikes.
4. **Uniform Resampling**:
   - WiFi packet arrival possesses small jitter ($\sim 1\text{ ms}$). The resampler interpolates onto an exact $20.0\text{ Hz}$ grid ($\Delta t = 50\text{ ms}$).
5. **Dynamic Butterworth Bandpass Filter (SOS Biquad)**:
   - Configurable band: $0.10\text{ Hz}$ (6 BPM) to $0.50\text{ Hz}$ (30 BPM), 4th-order.
   - Formatted as a cascade of Second-Order Sections (SOS) to guarantee numerical stability on low-frequency cutoff ratios without pole drift.
   - Computes coefficients dynamically via `scipy.signal.butter(..., output='sos')` with a pure-Python biquad calculation fallback when SciPy is absent.
6. **Respiration Estimator**:
   - Operates on a 15–30 second sliding window.
   - Applies Hanning window and 8x zero-padding FFT for high spectral resolution ($\Delta f < 0.005\text{ Hz} \approx 0.3\text{ BPM}$).
   - Employs 3-point parabolic peak interpolation and assigns a confidence score ($0–100\%$) based on peak-to-noise ratio.
7. **Motion Detector**:
   - Calculates Mean Absolute Deviation (MAD) across all 64 subcarriers between adjacent frames:
     $$M_t = \frac{1}{64} \sum_{k=0}^{63} \left| |H_t[k]| - |H_{t-1}[k]| \right|$$
   - Applies dual-threshold hysteresis ($\tau_{\text{enter}} = 3.5\sigma$, $\tau_{\text{exit}} = 2.0\sigma$) with 1.0 s minimum duration.
8. **State Machine (`WiBreatheStateMachine`)**:
   Tracks occupant state across 6 distinct modes:
   - `NO_DATA`: No serial packets received for $> 5.0\text{ s}$.
   - `CALIBRATING`: Packets arriving, awaiting baseline calibration profile.
   - `NO_PERSON`: Bed is empty (variance below empty-bed threshold, confidence $< 30\%$).
   - `STILL`: Person in bed, quiet respiration detected.
   - `MOVING`: Physical movement active in bed.
   - `EXIT`: Bed-exit confirmed (sustained empty signature for $> 6.0\text{ s}$ following motion).

---

## 5. Communication Protocols

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for full specifications.

### 5.1 Upstream CSI Packet Format (`csi_rx` $\to$ Host)
A 25-column comma-separated ASCII string terminated with `\n`:
```text
CSI_DATA,<type>,<seq>,<mac>,<rssi>,<rate>,<sig_mode>,<mcs>,<bandwidth>,<smoothing>,<not_sounding>,<aggregation>,<stbc>,<fec_coding>,<sgi>,<noise_floor>,<ampdu_cnt>,<channel>,<secondary_channel>,<local_timestamp>,<ant>,<sig_len>,<rx_state>,<len>,<data>
```
- `<len>`: Number of signed 8-bit integers (128 for LLTF, 384 for LLTF+HT-LTF).
- `<data>`: Space-separated bracketed int8 array: `"[q0 i0 q1 i1 ... q63 i63]"`

### 5.2 Downstream OLED Packet Format (Host $\to$ `csi_rx`)
A single ASCII line sent at 1 Hz from the laptop to the receiver board:
```text
DISP,<state>,<bpm or -->,<conf 0-100>,<seq>\n
```
*Examples*:
- `DISP,STILL,16.2,85,101\n`
- `DISP,MOVING,--,15,102\n`
- `DISP,EXIT,--,0,103\n`

### 5.3 Serial Bandwidth Utilization Table

| CSI Configuration | Values (`len`) | Subcarriers | Avg Line Length | 25 Hz | 50 Hz (Default) | 100 Hz | 921.6k Bus Load @ 50 Hz |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **LLTF Only** (Classic ESP32) | **128** | 64 | **~540 bytes** | 13.5 KB/s | **27.0 KB/s** | 54.0 KB/s | **29.3% (SAFE)** |
| **LLTF + HT-LTF** | **384** | 64 x 3 | **~1,450 bytes** | 36.2 KB/s | **72.5 KB/s** | **145.0 KB/s (OVERFLOW)** | **78.7%** |

---

## 6. Complete From-Scratch Setup Guide

### 6.1 Host Python Environment (macOS & Windows)

WiBreathe supports Python 3.10 to 3.14 (Python 3.12 or 3.13 recommended). Always use an isolated virtual environment.

#### On macOS (Terminal):
```bash
# 1. Clone repository
git clone https://github.com/Rahul-Gonal/wibreathe.git
cd wibreathe

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies and editable package
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 4. Verify test suite (all tests should pass)
pytest -q
```

#### On Windows (PowerShell):
```powershell
# 1. Clone repository
git clone https://github.com/Rahul-Gonal/wibreathe.git
cd wibreathe

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Install dependencies and editable package
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 4. Verify test suite
pytest -q
```

---

### 6.2 ESP-IDF v5.x Toolchain Setup

#### On macOS:
1. Locate your ESP-IDF directory (installed via the Espressif Installation Manager or manual git clone):
   ```bash
   cd ~/.espressif/v5.5.6/esp-idf
   # Or: cd ~/esp/esp-idf
   ```
2. Initialize tools and Python environment for target `esp32`:
   ```bash
   # If running with macOS built-in Python:
   ESP_PYTHON=/usr/bin/python3 ./install.sh esp32

   # Or if Homebrew Python 3.12 is installed:
   ./install.sh esp32
   ```
3. Activate ESP-IDF in your current shell:
   ```bash
   . ./export.sh
   idf.py --version
   ```

#### On Windows (PowerShell):
1. In an ESP-IDF PowerShell prompt (opened from Start Menu or via script):
   ```powershell
   # If script execution is restricted:
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

   . $HOME\esp\esp-idf\export.ps1
   idf.py --version
   ```

---

### 6.3 Building & Flashing Firmware

#### 1. Receiver Node (`firmware/csi_rx`)
Plug the board with the OLED display attached into your computer via USB-C:
```bash
cd firmware/csi_rx

# Target classic ESP32
idf.py set-target esp32

# Build binaries
idf.py build

# Flash to board (replace with your port)
idf.py -p /dev/cu.usbserial-0001 -b 921600 flash monitor
```
*(Press `Ctrl + ]` to exit monitor)*

> [!TIP]
> **BOOT Button on Clones**: If flashing hangs on `Connecting........_____.....`, **press and hold the physical BOOT button** on the ESP32 board for 2 seconds until flashing begins.

#### 2. Transmitter Node (`firmware/csi_tx`)
Unplug the receiver, plug in the second ESP32 board:
```bash
cd ../csi_tx

# Target classic ESP32
idf.py set-target esp32

# Build binaries
idf.py build

# Flash to board
idf.py -p /dev/cu.usbserial-0001 -b 921600 flash
```
Disconnect the transmitter and plug it into an external 5V USB power bank or wall charger on the opposite side of the bed.

---

### 6.4 Verification & First Run

1. **Check Port Detection**:
   ```bash
   python -m wibreathe list-ports
   ```
2. **Verify Live Link**:
   ```bash
   python -m wibreathe check-link --port <PORT> --baud 921600 --duration 10
   ```
   *Expected*: ~50.0 Hz packet rate, < 2% loss, RSSI > -70 dBm.
3. **Calibrate Empty Bed**:
   With no one in bed, record a 60-second baseline:
   ```bash
   python -m wibreathe record --port <PORT> --volunteer V00 --mode empty --duration 60 --consent
   python -m wibreathe calibrate --mode empty --file data/raw/csi_V00_empty_*.csv --out config/calibration_empty.json
   ```
4. **Start Live Monitoring Dashboard**:
   ```bash
   python -m wibreathe live --port <PORT> --display --calibration config/calibration_empty.json
   ```

---

## 7. CLI Reference & Workflows

All commands are invoked via `python -m wibreathe <subcommand>`:

| Command | Key Arguments | Description |
| :--- | :--- | :--- |
| `list-ports` | *None* | Discovers all connected serial devices (`/dev/cu.*`, `COMx`, `/dev/ttyUSB*`). |
| `check-link` | `-p`, `-b 921600`, `-d 10` | Measures packet ingress rate, sequence loss, and mean RSSI. |
| `simulate` | `-d 60`, `-r 50`, `--scenario still\|bed_exit`, `-o sim.csv`, `--labels ref.csv` | Generates realistic synthetic CSI frames and ground-truth labels. |
| `replay` | `-f sim.csv`, `--fast`, `-s 1.0` | Feeds recorded/synthetic CSV files through the pipeline and state machine. |
| `live` | `-p PORT`, `--display`, `--headless`, `--calibration cal.json` | Real-time streaming pipeline driving OLED and console/GUI dashboard. |
| `calibrate`| `--mode empty\|occupied`, `-f data.csv`, `-o cal.json` | Computes baseline variance, MAD, and subcarrier rankings. |
| `record` | `-p PORT`, `--volunteer V01`, `--mode normal`, `-d 120`, `--consent` | Logs raw CSI to CSV with structured JSON metadata sidecars. |
| `plot` | `data.csv`, `-o report.png` | Generates 4-panel diagnostic figure (Subcarriers, Hampel, Filtered, FFT). |
| `tap` | `--volunteer V01`, `--mode normal`, `-o ref.csv` | Single-key interactive ground-truth breath and motion logger. |
| `eval` / `evaluate` | `--est estimates.csv`, `--ref ground_truth.csv`, `-o results/` | Computes benchmark MAE, RMSE, coverage, and Bland-Altman statistics. |

---

## 8. Comprehensive Pitfalls & Troubleshooting Matrix (1–17)

| # | Pitfall / Issue | Root Cause | Handling & Resolution in WiBreathe |
| :---: | :--- | :--- | :--- |
| **1** | **No serial port appears** | 2-wire charge-only USB cable | First check: swap with a known 4-wire USB data cable. |
| **2** | **Zero CSI packets received** | ESP-NOW 802.11b default rate | ESP32 Wi-Fi hardware only produces CSI on OFDM frames. Firmware forces `WIFI_PHY_RATE_MCS0_LGI` HT20 rate. |
| **3** | **Garbled serial characters** | Baud rate mismatch | Ensure host reader is set to `921600` baud. Check 40 MHz crystal setting in `sdkconfig`. |
| **4** | **OLED display is blank** | I2C pin mismatch or power error | Verify wiring: `SDA=GPIO 21 (D21)`, `SCL=GPIO 22 (D22)`, `VCC=3V3` (never VIN/5V). Add 4.7kΩ pullups if jumpers > 15 cm. |
| **5** | **Left 2 columns distorted on OLED** | Controller is SH1106, not SSD1306 | SH1106 has a 132-column internal RAM. Set `CONFIG_WIBREATHE_OLED_SH1106=y` in Kconfig for the 2-column shift. |
| **6** | **Breathing peak suppressed or flat** | Hampel filter clamping natural slope | Set `relative_floor_ratio: 0.15` in `config/default.yaml` to dynamically scale floor with amplitude. |
| **7** | **Subharmonic beats in respiration** | Rolling median detrending on sine waves | Use linear or polynomial detrending; avoid short rolling median filters on periodic signals. |
| **8** | **CSI callback crashes on boot** | Dynamic allocation in Wi-Fi task | Never call `malloc()` or `printf()` inside callback. Callback uses non-blocking `xQueueSend(..., 0)`. |
| **9** | **Serial output interleaved mid-line** | Multiple tasks writing to UART0 simultaneously | `csi_print_task` is the single exclusive writer to UART0; default log level set to `WARN`. |
| **10** | **OLED absent crashes firmware** | Blocking I2C driver on disconnected bus | Driver handles NACK cleanly: if display is disconnected, returns immediately with 0 delay. |
| **11** | **Spurious bed-exit alerts** | Empty bed threshold uncalibrated | Run `python -m wibreathe calibrate --mode empty` to establish real room baseline variance. |
| **12** | **Outlier burst on body turn** | Rapid multipath phase shift | Causal Hampel filter and dual-threshold MAD motion detector flag `MOVING` state to suppress spurious BPM. |
| **13** | **Linux port permission denied** | User not in dialout group | Run `sudo usermod -a -G dialout $USER`, log out, and log back in. |
| **14** | **macOS terminal blocks on serial open** | Using `/dev/tty.*` instead of `/dev/cu.*` | `list-ports` and `SerialReader` automatically prefer non-blocking `/dev/cu.*` calling devices. |
| **15** | **DTR/RTS reset + ROM boot text** | Bridge DTR/RTS lines drive EN/GPIO0 | `SerialReader` opens port with `dtr=False, rts=False` before opening; discards boot text until first `CSI_DATA,` line. |
| **16** | **Brownout detector reset** | Wi-Fi current spikes (~450-500 mA) drop VDD3V3 | Firmware logs `esp_reset_reason()` at boot and warns prominently on `ESP_RST_BROWNOUT`; use powered USB hub. |
| **17** | **CSI length vs serial bandwidth overflow** | LLTF+HTLTF (384 values) @ 100 Hz requires ~145 KB/s | Exceeds 921,600 baud max throughput (92.16 KB/s). Default to LLTF-only (128 values) at 50 Hz (~27.0 KB/s). |

---

## 9. Ethics, Privacy & Regulatory Boundaries

1. **Human Volunteer Ethics**: All human data collection must strictly follow [docs/TRIAL_PROTOCOL.md](docs/TRIAL_PROTOCOL.md), including informed written consent and full freedom to withdraw.
2. **Participant Anonymity**: Never record names or identifiable health details. Use strictly de-identified keys (`V01`, `V02`).
3. **RF Emissions**: WiBreathe operates within standard consumer 2.4 GHz Wi-Fi transmission power levels ($\le 20\text{ dBm}$ / $100\text{ mW}$ EIRP). While consumer Wi-Fi transmitters comply with FCC and ICNIRP general-public RF exposure standards, bedside antennas should maintain standard separation ($\ge 20\text{ cm}$) from human occupants during continuous monitoring.
4. **Multipath Sensitivity**: Performance is subject to multipath changes from other moving individuals in the room. Controlled, single-occupant experimental rooms are required.
5. **Academic Scope**: Outputs represent experimental physical channel perturbations, not clinical respiratory parameters.
