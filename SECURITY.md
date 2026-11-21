# Security Policy

## Research Prototype Notice
WiBreathe is an academic research prototype for contactless respiration and motion sensing. It is not intended for clinical use, life-critical environments, or production deployments.

## Unencrypted ESP-NOW Transmissions
Communication between the transmitter and receiver uses broadcast 802.11n Wi-Fi frames over ESP-NOW without cryptographic encryption. Raw RF signals and CSI packets are observable by any receiver tuned to the same 2.4 GHz channel within radio range.

## Data Privacy and Version Control
Recordings of human subjects, raw CSI streams, and reference logs must never be committed to source code repositories. Ensure that `.gitignore` prevents unintentional commits of files under `data/raw/` and `data/processed/`.

## Reporting Vulnerabilities
To report security or privacy concerns, please open a private GitHub advisory or reach out to the project maintainers.
