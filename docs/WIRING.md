# WiBreathe Hardware Wiring & Pinout Guide

> [!CAUTION]
> **ACADEMIC RESEARCH PROTOTYPE — NOT A MEDICAL DEVICE**  
> WiBreathe is designed solely for contactless RF sensing research and engineering education. It is not certified for medical diagnosis or vital signs monitoring.

---

## 1. Authoritative Hardware Overview

The physical WiBreathe system uses two identical classic **ESP32 DevKit V1** boards (module marked **ESP-32D N4**, 4 MB flash, single USB-C port wired to a CP2102 or CH340 USB-to-UART bridge):
1. **Transmitter Node (`csi_tx`)**: Classic ESP32 DevKit V1 powered via 5V USB (laptop or 5V power bank), continuously transmitting ESP-NOW frames at 50 Hz.
2. **Receiver Node (`csi_rx`)**: Classic ESP32 DevKit V1 connected via USB-C to the host laptop (921,600 baud streaming) and wired via female-female jumpers to a 0.96" 128x64 I2C OLED display (SSD1306 or SH1106).

> [!IMPORTANT]
> **Single USB-C Port & Bridge Behavior**:
> Each board features a single USB-C port connected to a CP2102 or CH340 bridge chip driving UART0. UART0 carries both ESP-IDF system logs and the high-speed 921,600 baud CSI data stream.
> The bridge's DTR and RTS control lines are wired directly to the ESP32's `EN` (Reset) and `GPIO0` (Boot mode) pins. Opening the host serial port asserts DTR/RTS, which may reset the ESP32 chip. Some budget DevKit clones also require holding down the physical **BOOT** button during `idf.py flash` if automatic bootloader synchronization fails.

---

## 2. Receiver Pinout & OLED Wiring Table

The 0.96" 4-pin I2C OLED display operates at address `0x3C` (400 kHz Fast-Mode I2C).

### Primary Target: Classic ESP32 DevKit V1 (30-pin or 36-pin)

| OLED Pin | ESP32 DevKit V1 Pin | Pin Description / Location | Electrical Connection Notes |
| :---: | :---: | :--- | :--- |
| **GND** | **GND** | Ground pin (adjacent to 3V3 or 5V) | Common system ground. |
| **VCC** | **3V3** | **3.3V Output Rail** | **CRITICAL**: Use the **3V3 pin ONLY**. **DO NOT** connect to `VIN` or `5V`, as exceeding 3.6V will permanently damage the display controller! |
| **SDA** | **GPIO 21 (D21)** | Hardware I2C Serial Data (Pin labeled **D21** on the board silk screen) | Connect with female-female jumper wire. Internal pull-up enabled by firmware. |
| **SCL** | **GPIO 22 (D22)** | Hardware I2C Serial Clock (Pin labeled **D22** on the board silk screen) | Connect with female-female jumper wire. Internal pull-up enabled by firmware. |

#### Photo-Free Pin Pinpoint (Classic 30-Pin ESP32 DevKit V1):
- With the ESP32 module oriented pointing **away** from you and the USB-C port at the **bottom** facing you:
  - **Left Row**: The top-most pin is `3V3`, followed by `EN`, `VP (GPIO36)`, `VN (GPIO39)`, `D34`, `D35`, `D32`, `D33`, `D25`, `D26`, `D27`, `D14`, `D12`, `D13`, and `GND` at the bottom.
  - **Right Row**: **D21 (SDA)** is typically the 3rd pin from the bottom on the right side. **D22 (SCL)** is adjacent to **D21** (or 2nd pin from the bottom, depending on whether `D23` or `GND` is at the corner). Always locate the silkscreen markings **D21** and **D22**.

---

### Secondary Guarded Targets (Reference Only)

If building for alternate microcontrollers:
- **ESP32-S3 DevKitC-1**: `SDA = GPIO 8`, `SCL = GPIO 9`, `VCC = 3V3`, `GND = GND`.
- **ESP32-C3 DevKitM-1**: `SDA = GPIO 4`, `SCL = GPIO 5`, `VCC = 3V3`, `GND = GND`.

---

## 3. OLED Controller Types (SSD1306 vs SH1106)

Many budget 0.96" and 1.3" OLED modules on Amazon/AliExpress utilize the **SH1106** controller rather than the standard **SSD1306**.
- **SSD1306**: Standard 128-column internal RAM with horizontal addressing.
- **SH1106**: 132-column internal RAM requiring a 2-column horizontal display offset (columns 2 to 129).

WiBreathe provides a Kconfig choice in `firmware/csi_rx`:
- `WIBREATHE_OLED_SSD1306` (Default)
- `WIBREATHE_OLED_SH1106` (Enables 2-column offset and page addressing)

Configure via `idf.py menuconfig -> WiBreathe Receiver Configuration -> OLED Display Controller`.

---

## 4. Power Decoupling & Cabling Checklist

1. **4-Wire USB Data Cable**: Verify with a multimeter or by verifying device enumeration that your USB-C cable has all four internal wires connected (`VBUS`, `GND`, `D+`, `D-`). Charge-only cables will supply power but cannot stream data.
2. **Current Spikes & Brownouts**: ESP32 Wi-Fi bursts draw up to 450–500 mA transient pulses. Ensure the board is powered from a stable USB 3.0 port or a powered USB hub. If brownout detection triggers, the firmware reports `ESP_RST_BROWNOUT` at boot.
3. **I2C Signal Integrity**: Use female-to-female jumper wires shorter than 15 cm. If jumper wires exceed 15 cm, solder a 0.1 µF ceramic decoupling capacitor across `VCC` and `GND` right at the OLED display header.
