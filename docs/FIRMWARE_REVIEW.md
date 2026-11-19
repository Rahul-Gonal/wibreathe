# WiBreathe Firmware Architecture & Static Review

> **DISCLAIMER**: The WiBreathe firmware is an academic research prototype. It is not clinical software, has not undergone FDA/CE medical software validation, and must never be used for life-critical patient monitoring.

---

## 1. Firmware Architecture Overview

WiBreathe firmware consists of two independent ESP-IDF v5.x projects targeting the ESP32 family (primary target: **ESP32-S3 DevKitC-1**):
1. **`firmware/csi_tx`**: Transmits periodic uncompressed 802.11n HT20 OFDM frames at a strict 50 Hz rate using ESP-NOW with forced MCS0 rate.
2. **`firmware/csi_rx`**: Captures raw CSI from Wi-Fi PHY promiscuous callbacks, queues frames via FreeRTOS queue, serializes them to 921,600 baud UART, and updates an SSD1306 OLED display.
3. **`firmware/components/wibreathe_common`**: Shared lightweight on-device motion detection engine (`ondevice_motion.c`) and common header structures.

```
+-----------------------------------------------------------------------------------+
|                            CSI Receiver (csi_rx)                                  |
|                                                                                   |
|  [Wi-Fi PHY]                                                                      |
|      |                                                                            |
|  (CSI Callback) ----[ xQueueSendFromISR ]-----> [ FreeRTOS CSI Queue ]            |
|  (Zero malloc,                                         |                          |
|   ISR-safe)                                            v                          |
|                                                [ CSI Serial Task ]                |
|                                                        |                          |
|                                                        v                          |
|                                               [ UART0 @ 921600 ]                  |
|                                                        |                          |
|                                                        v                          |
|                                                 To Host Python                    |
|                                                                                   |
|  [ UART RX Task ] <--- (DISP telemetry) <------- From Host Python                 |
|         |                                                                         |
|         v                                                                         |
|  [ OLED Task ]  -----> [ SSD1306 Display ] (I2C 400kHz: BPM, State, Motion)      |
+-----------------------------------------------------------------------------------+
```

---

## 2. ESP-IDF v5.x API Compatibility Audit

The firmware strictly uses verified ESP-IDF v5.x APIs (target **ESP-IDF v5.5.6**):

| Subsystem | ESP-IDF v4.x (Deprecated) | ESP-IDF v5.x (WiBreathe Implementation) | Status in v5.5.6 |
| :--- | :--- | :--- | :--- |
| **CSI Callback** | `esp_wifi_set_csi_cb(cb, ctx)` | `esp_wifi_set_csi_rx_cb(csi_rx_callback, NULL)` | Valid |
| **CSI Structure** | `wifi_csi_info_t.buf` (untyped) | `wifi_csi_info_t.buf` (cast to `int8_t *`) | Valid |
| **ESP-NOW Recv CB**| `esp_now_recv_cb_t(mac, data, len)` | `esp_now_recv_cb_t(esp_now_recv_info_t *, data, len)` | Valid |
| **Peer Rate Config**| Unset (defaults to 1 Mbps DSSS 802.11b)| `esp_now_set_peer_rate_config(mac, &rate_cfg)` | Mandatory for OFDM CSI |
| **Timers** | `esp_timer_create` / `esp_timer_start_periodic` | `esp_timer_create` / `esp_timer_start_periodic` | Valid |
| **I2C Master Driver** | `i2c_param_config` & `i2c_driver_install` | Supported legacy driver in v5.x | Valid & portable |
| **UART Driver** | `uart_param_config` & `uart_driver_install` | `uart_driver_install(UART_NUM_0, 4096, 2048, ...)` | Valid |

---

## 3. Serial Bandwidth & UART Headroom Budget Calculation

### 3.1 Downlink (ESP32 RX -> Host PC)
- **Packet Rate ($f_{\text{pkt}}$)**: 50 Hz (1 frame every 20 ms).
- **Line Structure**:
  - Header: `CSI_DATA,<node_id>,<mac>,<timestamp>,<rssi>,<rate>,<sig_mode>,<mcs>,<bandwidth>,<smoothing>,<not_sounding>,<aggregation>,<stbc>,<fec_coding>,<sgi>,<noise_floor>,<ampdu_cnt>,<channel>,<secondary_channel>,<local_timestamp>,<ant>,<sig_len>,<rx_state>,<len>,` (~85 characters)
  - Data: 128 signed 8-bit integers separated by commas, enclosed in brackets: `"[val1,val2,...,val128]\n"` (~520 to ~640 characters depending on negative signs and digit counts).
  - Total line length ($L_{\text{line}}$): **~650 to ~750 bytes** per frame.
  - At 50 Hz, raw byte throughput:
    $$\text{Data Rate} = 750\text{ bytes/frame} \times 50\text{ frames/s} = 37,500\text{ bytes/s}$$
  - UART Serial Framing: 8N1 (1 start bit + 8 data bits + 1 stop bit = 10 bits/byte).
    $$\text{Baud Demand} = 37,500\text{ bytes/s} \times 10\text{ bits/byte} = 375,000\text{ baud}$$
  - Selected UART Baud: **921,600 baud**.
  - **Available Bandwidth Headroom**:
    $$\text{Headroom} = \frac{921,600 - 375,000}{921,600} \approx 59.3\%$$
  - **Verdict**: Fully safe with ~60% headroom against serial buffer backpressure.

### 3.2 Uplink (Host PC -> ESP32 RX)
- **Message Structure**: `DISP:BPM=16.2,STATE=STILL,MOT=0.012\n` (~32 bytes).
- **Transmission Rate**: 1 Hz.
- **Baud Demand**: $32\text{ bytes/s} \times 10\text{ bits/byte} = 320\text{ baud}$.
- **Verdict**: Completely negligible footprint (< 0.04% of 921,600 baud).

---

## 4. Interrupt Context Safety & FreeRTOS Concurrency Audit

1. **Zero Dynamic Allocation in Interrupt Context**:
   - The Wi-Fi promiscuous CSI callback `csi_rx_callback` runs in high-priority Wi-Fi driver task/ISR context.
   - It performs **no `malloc()`, `free()`, or heap operations**.
   - It performs **no blocking operations** (no `printf()`, `vTaskDelay()`, or blocking mutex acquisitions).
2. **Bounded Queue Decoupling**:
   - CSI frames are dispatched to `s_csi_queue` using `xQueueSendFromISR(..., &higher_woken)`.
   - If the queue is momentarily full, frames are dropped gracefully with a dropped counter increment; the system never deadlocks or blocks the Wi-Fi PHY.
   - Queue depth: 32 frames ($32 \times 20\text{ ms} = 640\text{ ms}$ buffer capacity).
3. **Task Priorities**:
   - `csi_uart_task`: Priority 5 (high priority; drains CSI queue immediately).
   - `oled_task`: Priority 2 (low priority; updates display at 2 Hz).
   - `uart_rx_task`: Priority 3 (reads downstream telemetry without blocking).

---

## 5. Physical Layer & RF Channel Verification

1. **OFDM vs DSSS Requirement**:
   - 802.11b DSSS packets (1 Mbps, 2 Mbps) **do not produce Channel State Information** on the ESP32 Wi-Fi hardware demodulator.
   - ESP-NOW defaults to 802.11b rates unless explicitly overridden.
   - In `csi_tx/main/main.c`, the peer configuration enforces:
     ```c
     rate_cfg.phymode = WIFI_PHY_MODE_HT20;
     rate_cfg.rate = WIFI_PHY_RATE_MCS0_LGI;
     esp_now_set_peer_rate_config(s_peer_mac, &rate_cfg);
     ```
   - This guarantees that every transmitted packet produces a full 64-subcarrier OFDM CSI matrix at the receiver.
2. **Channel Stability**:
   - Both TX and RX are locked to channel 6 (`CONFIG_WIBREATHE_WIFI_CHANNEL=6`) with secondary channel set to 0 (HT20 20 MHz bandwidth).

---

## 6. On-Device Motion Detection Verification

- Implemented in `components/wibreathe_common/ondevice_motion.c`.
- Computes mean absolute deviation (MAD) across adjacent CSI frame subcarriers:
  $$M_k = \frac{1}{N_{\text{sc}}} \sum_{i=0}^{N_{\text{sc}}-1} \left| |C_t[i]| - |C_{t-1}[i]| \right|$$
- When $M_k > \tau_{\text{motion}}$, the device flags local movement instantly to drive on-board alert states without waiting for host Python computation.

---

## 7. Multi-Target Portability Checklist

The codebase includes preprocessor guards for cross-platform ESP32 silicon:

```c
#if CONFIG_IDF_TARGET_ESP32S3
#define DEFAULT_I2C_SDA_PIN GPIO_NUM_8
#define DEFAULT_I2C_SCL_PIN GPIO_NUM_9
#elif CONFIG_IDF_TARGET_ESP32C3
#define DEFAULT_I2C_SDA_PIN GPIO_NUM_4
#define DEFAULT_I2C_SCL_PIN GPIO_NUM_5
#else // Classic ESP32
#define DEFAULT_I2C_SDA_PIN GPIO_NUM_21
#define DEFAULT_I2C_SCL_PIN GPIO_NUM_22
#endif
```

All pinouts are additionally overridable via `idf.py menuconfig` (Kconfig `CONFIG_WIBREATHE_I2C_SDA_PIN` and `CONFIG_WIBREATHE_I2C_SCL_PIN`).
