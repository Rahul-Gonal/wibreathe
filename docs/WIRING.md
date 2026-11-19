# WiBreathe Hardware Wiring & Pinout Guide

## 1. Overview
The WiBreathe system consists of two ESP32 nodes placed on opposite sides of a bed:
1. **Transmitter Node (`csi_tx`)**: Headless ESP32 or ESP32-S3 DevKitC-1 powered via 5V USB-UART.
2. **Receiver Node (`csi_rx`)**: ESP32 or ESP32-S3 DevKitC-1 connected via USB-UART to the host laptop and wired to an SSD1306 0.96" 128x64 I2C OLED display.

> **CRITICAL HARDWARE NOTE**: On ESP32-S3 boards equipped with dual Type-C ports labeled `USB` and `UART`, always plug the cable into the **UART** port (CP2102/CH340 chip). The native USB-JTAG port can suffer from buffer stalls at 921,600 baud streaming rates.

---

## 2. Receiver Pinout & OLED Connection Table

The SSD1306 display communicates using standard I2C at 400 kHz (address `0x3C`).

### ESP32-S3 DevKitC-1 (Default Target)
| SSD1306 OLED Pin | ESP32-S3 GPIO Pin | Function | Notes |
| :--- | :--- | :--- | :--- |
| **GND** | **GND** | Ground | Common system ground |
| **VCC** | **3V3** | 3.3V Power | Do not power with 5V; SSD1306 is 3.3V logic |
| **SDA** | **GPIO 8** | I2C Data | Internal pull-up enabled; 4.7kΩ external recommended |
| **SCL** | **GPIO 9** | I2C Clock | Internal pull-up enabled; 4.7kΩ external recommended |

### Classic ESP32 DevKit v1 (Fallback Target)
| SSD1306 OLED Pin | ESP32 GPIO Pin | Function | Notes |
| :--- | :--- | :--- | :--- |
| **GND** | **GND** | Ground | Common system ground |
| **VCC** | **3V3** | 3.3V Power | Connect to board 3.3V output |
| **SDA** | **GPIO 21** | I2C Data | Default hardware I2C SDA |
| **SCL** | **GPIO 22** | I2C Clock | Default hardware I2C SCL |

### ESP32-C3 DevKitM-1 (Compact Target)
| SSD1306 OLED Pin | ESP32-C3 GPIO Pin | Function | Notes |
| :--- | :--- | :--- | :--- |
| **GND** | **GND** | Ground | Common ground |
| **VCC** | **3V3** | 3.3V Power | 3.3V logic rail |
| **SDA** | **GPIO 4** | I2C Data | Configured via Kconfig |
| **SCL** | **GPIO 5** | I2C Clock | Configured via Kconfig |

---

## 3. Power Supply and Cabling Checklist
- **Cables**: Ensure you use a **4-wire USB data cable**. Many micro-USB and Type-C cables included with chargers are 2-wire charge-only cables and will fail to enumerate a COM / tty port.
- **Power Decoupling**: If using breadboard jumper wires longer than 15 cm for the OLED, place a 0.1 µF ceramic capacitor across `VCC` and `GND` pins directly at the OLED header to prevent I2C bus lockups caused by Wi-Fi RF burst current spikes.
