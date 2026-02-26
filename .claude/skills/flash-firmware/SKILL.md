---
name: flash-firmware
description: Build and flash ESP32 firmware to a directly-connected device via USB. Use when flashing Bedroom Intercom (10.0.0.36) or Office Intercom (10.0.0.41) via serial. Do NOT use for INTERCOM2 — use flash-intercom2 instead.
allowed-tools: Bash, Read
version: 1.0.0
---

# Build and Flash ESP32 Firmware

## Pre-flight
- Confirm `FIRMWARE_VERSION` in `firmware/main/protocol.h` has been updated
- Confirm code has passed code-review and tests
- If sdkconfig was changed: run fullclean first (see step 1a)
- Device connected via USB to /dev/ttyACM0

## Step 1 — Build
```bash
cd firmware
/home/user/.pio-venv/bin/pio run
```

### Step 1a — If sdkconfig.esp32s3 was changed (REQUIRED)
```bash
cd firmware
/home/user/.pio-venv/bin/pio run -t fullclean
/home/user/.pio-venv/bin/pio run
```
Never skip fullclean after sdkconfig changes — PSRAM config will not apply correctly.

## Step 2 — Check Build Output
Verify: no errors, binary size looks reasonable (not suspiciously small), PSRAM allocation messages present if PSRAM is enabled.

## Step 3 — Flash
```bash
/home/user/.pio-venv/bin/pio run -t upload --upload-port /dev/ttyACM0
```

## Step 4 — Verify
Monitor serial output to confirm:
- Device boots cleanly
- WiFi connects
- MQTT connects
- Version string matches expected `FIRMWARE_VERSION`

## Rollback
Flash previous firmware binary if available, or revert code changes and rebuild.
