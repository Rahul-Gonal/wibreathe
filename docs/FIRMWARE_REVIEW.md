# WiBreathe Firmware Architecture & Static Review

> [!CAUTION]
> **ACADEMIC RESEARCH PROTOTYPE — NOT A MEDICAL DEVICE**  
> The WiBreathe firmware is an academic research prototype. It is not clinical software, has not undergone FDA/CE medical software validation, and must never be used for life-critical patient monitoring.

---

## 1. Firmware Architecture Overview

WiBreathe firmware consists of two independent ESP-IDF v5.x projects targeting the classic **ESP32 DevKit V1** (ESP-32D N4, 4 MB flash) as the primary target, with guarded preprocessor options for ESP32-S3 and ESP32-C3:
1. **`firmware/csi_tx`**: Transmits periodic uncompressed 802.11n HT20 OFDM frames at a strict 50 Hz rate using ESP-NOW with forced MCS0 rate.
2. **`firmware/csi_rx`**: Captures raw CSI from Wi-Fi callbacks, decouples through a FreeRTOS queue, serializes them through a single exclusive UART0 writer task (`csi_print_task`), receives downstream `DISP` commands, and drives an SSD1306/SH1106 OLED display.
3. **`firmware/components/wibreathe_common`**: Shared lightweight on-device motion detection engine (`ondevice_motion.c`) and common header structures.

```
+-----------------------------------------------------------------------------------+
|                            CSI Receiver (csi_rx)                                  |
|                                                                                   |
|  [Wi-Fi Task Context]                                                             |
|      |                                                                            |
|  (CSI Callback) ----[ xQueueSend(queue, &item, 0) ]-----> [ FreeRTOS CSI Queue ]  |
|  (Zero malloc, non-blocking,                               |                      |
|   drop counter on full queue)                              v                      |
|                                                    [ csi_print_task ]             |
|                                                     (Exclusive UART0 Writer)      |
|                                                            |                      |
|                                                            v                      |
|                                                   [ UART0 @ 921600 ]              |
|                                                   (atomic uart_write_bytes)       |
|                                                            |                      |
|                                                            v                      |
|                                                     To Host Python                |
|                                                                                   |
|  [ UART RX Task ] <--- (DISP,<state>,<bpm>,<conf>,<seq>\n) - From Host Python     |
|         |                                                                         |
|         v                                                                         |
|  [ OLED Task ]  -----> [ SSD1306 / SH1106 Display ] (I2C 400kHz, non-blocking)    |
+-----------------------------------------------------------------------------------+
```

---

## 2. Concurrency, Queue Safety & Single UART Writer

### 2.1 CSI Callback Context
- The CSI callback registered with `esp_wifi_set_csi_rx_cb` runs inside the high-priority **Wi-Fi task context**, not a hardware interrupt service routine (ISR).
- As confirmed by ESP-IDF architecture, `xQueueSend(s_csi_queue, &item, 0)` is used with a zero tick timeout.
- **Zero Dynamic Allocation**: The callback operates strictly on static memory pools; no `malloc()`, `free()`, `printf()`, or blocking calls are permitted.
- If the queue is momentarily full, the callback increments `s_dropped_packets` and returns immediately without stalling the Wi-Fi stack.

### 2.2 Single Writer to UART0
- To prevent garbled lines and mid-line interleaving, all serial outputs (`CSI_DATA` frames and `STATUS` diagnostic messages) are marshaled exclusively through `csi_print_task`.
- In `firmware/csi_rx/sdkconfig.defaults`, the default log level is set to `CONFIG_LOG_DEFAULT_LEVEL_WARN=y` to eliminate system log chatter on UART0 during normal streaming.
- All UART transmissions use atomic `uart_write_bytes()` calls; `printf()` and `uart_write_bytes()` are never mixed on the same port.

### 2.3 Non-Blocking OLED Operations
- If the I2C OLED display is absent or unplugged, the I2C driver detects NACK immediately and skips display updates, preventing any delay or crash in the high-speed CSI pipeline.

---

## 3. Serial Bandwidth & Line Length Budget Calculation

The maximum physical byte throughput at 921,600 baud (8-N-1: 10 bits per byte) is:
$$\text{Max Throughput} = \frac{921,600 \text{ baud}}{10 \text{ bits/byte}} = 92,160 \text{ bytes/s} = 92.16 \text{ KB/s}$$

Actual formatted ASCII line lengths depend directly on the configured CSI set (`len`):

| Configuration | `len` (int8) | Subcarriers | Formatted Line Length | 25 Hz Stream | 50 Hz Stream (Default) | 100 Hz Stream | 921.6k Utilization (50 Hz) | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **LLTF Only** (Classic ESP32) | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | **RECOMMENDED** |
| **HT-LTF Only** | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | Valid |
| **LLTF + HT-LTF** | **384** | 64 x 3 | **~1,100 bytes** | 27.5 KB/s | **55.0 KB/s** | **110.0 KB/s** (EXCEEDS LIMIT) | **59.7%** | High load (100 Hz drops) |

The firmware calculates this exact bandwidth budget dynamically at boot and issues a console warning if the estimated data rate exceeds 80% of the baud capacity.

---

## 4. Reset Reason & Brownout Diagnostics

Classic ESP32 DevKit V1 boards frequently encounter power stability issues during Wi-Fi transmission bursts (drawing transient currents up to 450–500 mA).
- At boot, firmware queries `esp_reset_reason()`.
- If the reset was caused by brownout (`ESP_RST_BROWNOUT`), the firmware emits a prominent alert:
  ```
  *****************************************************
  * ALERT: System reset due to BROWNOUT!             *
  * Check USB power supply, cable gauge, and hub!     *
  *****************************************************
  ```
- Brownout detection is kept enabled via `CONFIG_ESP_BROWNOUT_DET=y` with brownout threshold level at 2.43V.

---

## 5. Physical Layer & Verification Status

### 5.1 ESP-NOW Modulation Enforcement
ESP-NOW defaults to 802.11b DSSS (1 Mbps), which does not trigger OFDM CSI demodulation in hardware.
In `firmware/csi_tx/main/main.c`, the peer configuration explicitly enforces:
```c
rate_cfg.phymode = WIFI_PHY_MODE_HT20;
rate_cfg.rate = WIFI_PHY_RATE_MCS0_LGI;
esp_now_set_peer_rate_config(s_peer_mac, &rate_cfg);
```

### 5.2 Classic ESP32 CSI Configuration (`wifi_csi_config_t`)
For target `esp32`:
```c
wifi_csi_config_t csi_config = {
    .lltf_en = true,
    .htltf_en = false,           // Set to true only if WIBREATHE_CSI_LLTF_HTLTF is selected
    .stbc_htltf2_en = false,
    .ltf_merge_en = true,
    .channel_filter_en = true,
    .manu_scale = false,
    .shift = false,
};
```
> [!WARNING]
> **UNVERIFIED Hardware Behavior**:
> - The exact raw byte order of I and Q (`[Q0, I0, Q1, I1, ...]`) and subcarrier index order per layout on classic ESP32 silicon have not been verified against Espressif reference firmware (`csi_send`, `csi_recv`, `csi_data_read_parse.py`) or physical RF test equipment.
> - Forced RX gain (`phy_force_rx_gain` / `esp_wifi_set_rx_gain`): Does not exist as a public stable API on classic ESP32 in ESP-IDF v5.x; compiled out. Amplitude stability relies entirely on host-side robust normalization and baseline calibration.
