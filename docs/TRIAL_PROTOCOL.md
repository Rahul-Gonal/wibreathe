# WiBreathe Volunteer Trial Protocol

> **IMPORTANT ETHICAL & REGULATORY NOTICE**  
> WiBreathe is an academic research prototype developed for Experiential Learning Phase 2 at RV College of Engineering. **It is NOT a medical device and is NOT intended for medical diagnosis, clinical monitoring, life support, or diagnostic decision-making.** Do not use this system in medical settings or as a replacement for clinical vital-sign monitors.

---

## 1. Objectives & Scope
This protocol defines the standardized procedure for collecting Wi-Fi Channel State Information (CSI) during controlled human respiration and bed-exit experimental sessions. Following this protocol ensures repeatable RF channel geometry, synchronized reference ground truth, and volunteer safety and privacy.

---

## 2. Participant Privacy, Ethics & Anonymisation

1. **Informed Consent**: Every participant must read and sign the Informed Consent Form before participating.
2. **Anonymised Identification**:
   - Never record participant names, national IDs, or personal phone numbers in data filenames, code, or commit history.
   - Use strictly anonymised identifiers following the regex `^V\d{2,3}$` (e.g., `V01`, `V02`, ..., `V99`).
   - The master key linking volunteer names to volunteer IDs must be stored offline on an encrypted drive accessible only by the primary investigator.
3. **Right to Withdraw**: Participants may pause or terminate the recording session at any point without penalty.
4. **Data Storage**: Raw CSI logs and metadata sidecars are saved exclusively in `data/raw/` and are excluded from git version control.

---

## 3. Experimental Setup & Hardware Geometry

```
                    +-----------------------------+
                    |                             |
                    |          BED / COT          |
                    |                             |
   +-------------+  |         (Volunteer)         |  +-------------+
   |  TX Node    |==|=============================|==|  RX Node    |
   | (csi_tx)    |  |                             |  | (csi_rx)    |
   +-------------+  +-----------------------------+  +-------------+
         |                                                  |
   5V USB Power                                        USB to Laptop
   (Battery bank)                                    (921,600 baud)
                                                            |
                                                     [ OLED Display ]
                                                     (State & BPM)
```

### 3.1 Node Placement
1. **Transmitter (`csi_tx`)**:
   - Mount securely on a non-metallic tripod or bed frame mount on the **left side** of the bed.
   - Height: Level with the top of the mattress (approximately 45–60 cm above floor level).
   - Distance from bed edge: 10–20 cm.
   - Power: Dedicated 5V USB power bank or isolated AC-DC adapter to eliminate laptop ground loops.

2. **Receiver (`csi_rx`)**:
   - Mount securely on the **right side** of the bed directly opposite the TX node.
   - Line-of-Sight (LoS): The direct wireless path crosses horizontally across the volunteer's chest / abdominal area.
   - Connect to host computer via high-speed USB-UART data cable (port CP2102/CH340).
   - Ensure the I2C OLED display is facing away from the volunteer if visual feedback would bias natural breathing.

3. **Room Conditions**:
   - Conduct sessions in a quiet room with doors and windows closed.
   - No other individuals should move inside the room during active data recording.
   - Ensure no large moving metal objects (e.g., oscillating fans) operate in the direct multipath vicinity.

---

## 4. Equipment Bringup Checklist

Prior to seating the participant, perform this equipment check:
- [ ] Connect RX node to host laptop.
- [ ] Verify serial port detection:
  ```bash
  python -m wibreathe list-ports
  ```
- [ ] Check link quality and CSI streaming (50 Hz nominal, < 2% packet loss):
  ```bash
  python -m wibreathe check-link --port /dev/tty.usbserial-0001 --baud 921600 --duration 10
  ```
- [ ] Verify OLED displays `WIBREATHE` splash followed by `LINK: OK` and live packet counter.
- [ ] Prepare reference tap tool terminal or physical metronome if conducting paced respiration.

---

## 5. Trial Procedure (Step-by-Step)

### Phase 1: Empty-Bed Calibration Baseline (Mandatory)
Before the volunteer enters the bed, record and calibrate an empty-bed baseline:
1. Ensure the mattress, pillow, and bedsheets are arranged normally.
2. Ensure the room is completely empty of humans and pets.
3. Record 60 seconds of empty bed data:
   ```bash
   python -m wibreathe record \
       --port <RX_PORT> \
       --baud 921600 \
       --volunteer V00 \
       --mode empty_bed \
       --duration 60
   ```
4. Calibrate the empty-bed variance signature:
   ```bash
   python -m wibreathe calibrate --mode empty --file data/raw/csi_V00_empty_bed_*.csv --out config/calibration_empty.json
   ```
5. Verify in the output summary that motion metric $M(t) < \tau_{\text{motion}}$ consistently.

### Phase 2: Volunteer Settling
1. Invite the volunteer to lie supine (face up) on the bed with their head on the pillow.
2. The wireless LoS path should cross the center of the chest.
3. Allow the volunteer to rest quietly for 2 minutes to stabilize their basal resting heart rate and respiratory pattern.

### Phase 3: Synchronized Reference Tap (Parallel Terminal)
To establish ground truth breathing rate without clinical spirometry:
1. Open a second terminal window on the host computer.
2. The investigator visually watches the volunteer's chest expansion.
3. At the exact peak of each inhalation, press the `SPACEBAR` in the tap tool:
   ```bash
   python -m wibreathe tap --volunteer V01 --mode normal --session 1
   ```
4. The tap tool records millisecond timestamps and outputs instantaneous reference BPM.

### Phase 4: Experimental Breathing Protocols
Execute the following protocols in order, resting 60 seconds between each test:

| Protocol ID | Description | Volunteer Instruction | Duration | Repetitions |
| :--- | :--- | :--- | :--- | :--- |
| **P1: Normal Tidal** | Spontaneous eupneic breathing | "Breathe naturally and relax. Avoid speaking or moving limbs." | 120 s | 3 trials |
| **P2: Deep Breathing** | Slow, deep diaphragmatic | "Inhale deeply for 3 seconds, exhale slowly for 3 seconds (~10 bpm)." | 90 s | 2 trials |
| **P3: Rapid Breathing** | Tachypnea simulation | "Breathe shallowly and quickly at approximately 24–30 bpm." | 60 s | 2 trials |
| **P4: Breath-Hold (Apnoea)** | Central apnoea simulation | "Inhale normally, then hold your breath comfortably for 20–30 s." | 60 s | 2 trials |
| **P5: In-Bed Movement** | Posture changes & limb motion | "Turn from back to right side, adjust blanket, or cough gently." | 60 s | 2 trials |
| **P6: Bed Exit & Return** | Complete bed vacation | "Sit up, swing legs over edge, stand up, step away for 30 s, return." | 90 s | 3 trials |

#### Example Command for Each Protocol:
```bash
# Protocol P1: Normal Tidal Breathing Trial 1
python -m wibreathe record \
    --port /dev/tty.usbserial-0001 \
    --baud 921600 \
    --volunteer V01 \
    --mode normal \
    --duration 120

# Protocol P4: Breath-Hold (Apnoea) Trial 1
python -m wibreathe record \
    --port /dev/tty.usbserial-0001 \
    --baud 921600 \
    --volunteer V01 \
    --mode apnoea \
    --duration 60

# Protocol P6: Bed Exit Trial 1
python -m wibreathe record \
    --port /dev/tty.usbserial-0001 \
    --baud 921600 \
    --volunteer V01 \
    --mode bed_exit \
    --duration 90
```

---

## 6. Data Integrity & Verification Steps

After completing each recording run:
1. Confirm that both `.csv` and `.json` sidecar files exist in `data/raw/`:
   ```bash
   ls -la data/raw/
   ```
2. Check the JSON metadata sidecar:
   - Verify `packet_count` matches expected count ($50\text{ Hz} \times \text{duration}$).
   - Verify `dropped_packets < 2\%`.
   - Verify `subcarrier_count == 64`.
3. Quick visual sanity check of breathing signal:
   ```bash
   python -m wibreathe plot --file data/raw/V01_normal_*.csv
   ```
   Check that:
   - High-variance subcarriers clearly show sinusoidal breathing undulations.
   - Filtered respiratory bandpass waveform (0.1–0.5 Hz) has stable periodicity.

---

## 7. Volunteer Consent & Session Checklist

Copy and print this checklist for each participant session:

```
[ ] Volunteer ID assigned (format V01-V99): ___________
[ ] Information sheet provided and explained to volunteer
[ ] Signed informed consent form received and safely archived
[ ] Volunteer confirmed healthy, comfortable, and willing to participate
[ ] Room temperature comfortable (22-25 °C)
[ ] RX and TX antennas secured at mattress level
[ ] Empty bed 60s baseline recorded (V00_empty_bed)
[ ] Protocol P1 (Normal Tidal) completed (3 runs)
[ ] Protocol P2 (Deep Breathing) completed (2 runs)
[ ] Protocol P3 (Rapid Breathing) completed (2 runs)
[ ] Protocol P4 (Apnoea / Breath-Hold) completed (2 runs)
[ ] Protocol P5 (In-Bed Movement) completed (2 runs)
[ ] Protocol P6 (Bed Exit & Return) completed (3 runs)
[ ] Ground truth tap logs saved in data/reference/
[ ] Debrief volunteer and offer rest/refreshments
[ ] Master de-identification log updated on encrypted offline volume
```
