# WiBreathe Communication Protocol Specification

> [!CAUTION]
> **ACADEMIC RESEARCH PROTOTYPE — NOT A MEDICAL DEVICE**  
> This specification describes communication protocols for an engineering research prototype. It is not approved or certified for medical diagnosis or clinical vital signs monitoring.

---

## 1. Serial Transport Architecture

- **Interface**: USB-UART bridge (CP2102 or CH340) connected to ESP32 Hardware UART0.
- **Baud Rate**: `921,600` baud, 8 data bits, no parity, 1 stop bit (8-N-1).
- **Physical Line**: Bidirectional single USB-C cable carrying both upstream telemetry and downstream display commands.
- **DTR/RTS Reset Prevention**: On ESP32 DevKit V1, bridge DTR/RTS lines drive EN and GPIO0. The host serial driver must open the port with `dtr=False` and `rts=False` before opening to avoid resetting the board. Any initial ROM bootloader output (emitted at 115,200 baud) is discarded until the first valid `CSI_DATA,` line synchronizer.

---

## 2. Upstream Serial Protocol: Receiver Node (`csi_rx`) $\to$ Host Computer

All upstream lines are ASCII strings terminated by LF (`\n`, optionally CRLF `\r\n`). All serial transmission is handled exclusively by a dedicated FreeRTOS print task (`csi_print_task`) to guarantee atomic line output without mid-line interleaving from system logging.

### 2.1 CSI Data Packets (`CSI_DATA`)

Format: 25 comma-delimited columns matching Espressif CSI format standards:

```text
CSI_DATA,<type>,<seq>,<mac>,<rssi>,<rate>,<sig_mode>,<mcs>,<bandwidth>,<smoothing>,<not_sounding>,<aggregation>,<stbc>,<fec_coding>,<sgi>,<noise_floor>,<ampdu_cnt>,<channel>,<secondary_channel>,<local_timestamp>,<ant>,<sig_len>,<rx_state>,<len>,<data>
```

#### Field Definitions:
1. `type`: Constant string `"CSI_DATA"`
2. `seq`: Upstream packet sequence counter (incrementing integer)
3. `mac`: Transmitter MAC address (e.g. `"1a:00:00:00:00:00"`)
4. `rssi`: Received Signal Strength Indicator in dBm (e.g. `-42`)
5. `rate`: Physical layer transmission rate code
6. `sig_mode`: Signal mode (e.g. `1` for HT20 / OFDM)
7. `mcs`: Modulation and Coding Scheme index (`0` for MCS0 BPSK 1/2)
8. `bandwidth`: Channel bandwidth (`0` for 20 MHz)
9. `smoothing`: Smoothing recommendation flag (`0` or `1`)
10. `not_sounding`: Not sounding flag (`0` or `1`)
11. `aggregation`: Aggregation flag (`0` or `1`)
12. `stbc`: Space-Time Block Coding flag (`0` or `1`)
13. `fec_coding`: Forward Error Correction type (`0`=BCC, `1`=LDPC)
14. `sgi`: Short Guard Interval flag (`0`=Long GI 800ns, `1`=Short GI 400ns)
15. `noise_floor`: Background RF noise floor in dBm (e.g. `-96`)
16. `ampdu_cnt`: A-MPDU subframe count
17. `channel`: Primary RF channel (`6`)
18. `secondary_channel`: Secondary channel offset (`0`=none)
19. `local_timestamp`: Receiver internal microsecond timestamp (`esp_timer_get_time()`)
20. `ant`: Antenna index (`0`)
21. `sig_len`: Frame payload length
22. `rx_state`: Receiver state / status code (`0`=OK)
23. `len`: Number of signed 8-bit integer CSI values in the data payload (e.g. `128` or `384`)
24. `data`: Bracketed space-separated signed int8 array: `"[q0 i0 q1 i1 ... qN iN]"`

> [!IMPORTANT]
> **I/Q Order**: The data array format is `[q0 i0 q1 i1 ...]` (Q first, then I per subcarrier). This order is labelled **UNVERIFIED** against physical hardware silicon until confirmed with oscilloscope/SDR or Espressif reference validation.

---

### 2.2 Periodic Status Telemetry (`STATUS`)

Emitted at 1 Hz by `csi_rx` for link diagnostics:

```text
STATUS,uptime_ms=<uint32>,rx_packets=<uint32>,dropped_packets=<uint32>,rate_hz=<uint32>,rssi=<int32>,brownout=<0|1>
```

Example:
```text
STATUS,uptime_ms=30000,rx_packets=1500,dropped_packets=0,rate_hz=50,rssi=-45,brownout=0
```

---

## 3. Serial Bandwidth Budget & Packet Formatting

Serial throughput limit at 921,600 baud (8-N-1 = 10 bits per byte):  
$\text{Max Raw Bandwidth} = \frac{921,600}{10} = 92,160 \text{ bytes/sec} \approx 92.16 \text{ KB/s}$.

The table below details actual formatted line lengths and required bandwidth across configurations:

| CSI Configuration | `len` (int8 values) | Subcarriers | Avg Line Length | 25 Hz Stream | 50 Hz Stream (Default) | 100 Hz Stream | 921.6k Bus Load (50 Hz) | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **LLTF Only** (Classic ESP32) | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | **RECOMMENDED** |
| **HT-LTF Only** | **128** | 64 | **~390 bytes** | 9.75 KB/s | **19.5 KB/s** | 39.0 KB/s | **21.2%** | Supported |
| **LLTF + HT-LTF** | **384** | 64 x 3 | **~1,100 bytes** | 27.5 KB/s | **55.0 KB/s** | **110.0 KB/s** (OVERFLOW) | **59.7%** | High load (100 Hz drops) |

> [!WARNING]
> At 100 Hz with LLTF+HT-LTF (384 values), required bandwidth is 110.0 KB/s, exceeding the 92.16 KB/s physical limit by 19.4%, which causes UART FIFO saturation and packet drops. Default to LLTF-only (128 values) at 50 Hz.

---

## 4. Downstream Serial Protocol: Host Computer $\to$ Receiver Node (`csi_rx`)

The host computer transmits downstream status packets to update the receiver's attached SSD1306/SH1106 OLED display.

### 4.1 Canonical Downstream Message Format

```text
DISP,<state>,<bpm or -->,<conf 0-100>,<seq>\n
```

#### Fields:
1. `DISP`: Fixed command header.
2. `<state>`: System state string:
   - `NO_DATA`
   - `CALIBRATING`
   - `NO_PERSON`
   - `STILL`
   - `MOVING`
   - `EXIT`
3. `<bpm or -->`: Estimated respiration rate as a float formatted to 1 decimal place (e.g. `16.2`), or `--` if breathing is not currently estimable (e.g. during motion or empty bed).
4. `<conf 0-100>`: Signal quality and spectral peak confidence score integer (`0` to `100`).
5. `<seq>`: Monotonically increasing host sequence counter integer (`0` to `999999`).

#### Examples:
- Normal breathing: `DISP,STILL,15.5,85,104\n`
- Movement in bed: `DISP,MOVING,--,15,105\n`
- Empty bed: `DISP,NO_PERSON,--,0,106\n`
- Bed exit alarm: `DISP,EXIT,--,0,107\n`
- Initializing / no signal: `DISP,NO_DATA,--,0,108\n`

#### Receiver Parser Behavior:
- Bounded 64-byte circular buffer on UART0 RX.
- Any line exceeding 64 bytes is discarded immediately.
- Corrupted or unparseable lines are quietly rejected without blocking or delaying `csi_print_task`.
- If no OLED display is physically connected, the I2C driver detects NACK and returns immediately without stalling the CSI processing or print pipelines.
