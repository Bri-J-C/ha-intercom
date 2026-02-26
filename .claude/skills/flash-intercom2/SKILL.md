---
name: flash-intercom2
description: OTA flash INTERCOM2 (10.0.0.38) via Home Assistant server. MUST be used for INTERCOM2 — its WiFi is too weak for direct OTA. Never attempt to flash INTERCOM2 directly.
allowed-tools: Bash, Read
version: 1.0.0
---

# Flash INTERCOM2 via HA Server

## Why This Is Different
INTERCOM2 (10.0.0.38) has weak WiFi and cannot reliably receive OTA updates directly. All flashing must be staged through the HA server at 10.0.0.8.

## Pre-flight
- Build firmware first using `flash-firmware` skill (steps 1 and 2 only — do not flash directly)
- Locate the compiled firmware binary (typically `firmware/.pio/build/esp32s3/firmware.bin`)
- Confirm INTERCOM2 is online: `ping 10.0.0.38`

## Step 1 — Stage on HA Server
```bash
scp firmware/.pio/build/esp32s3/firmware.bin root@10.0.0.8:/tmp/firmware.bin
```

## Step 2 — Flash via HA Server
```bash
ssh root@10.0.0.8 "curl -F 'firmware=@/tmp/firmware.bin' http://10.0.0.38/update"
```
This may take 60-120 seconds. Wait for the curl command to return a success response.

## Step 3 — Verify
```bash
# Wait ~30 seconds for device to reboot, then check it's back online
ping -c 3 10.0.0.38
```
Then verify version via MQTT or serial if accessible.

## Step 4 — Cleanup
```bash
ssh root@10.0.0.8 "rm /tmp/firmware.bin"
```

## If Update Fails
Device should fall back to previous firmware. Verify it's still responsive at 10.0.0.38 before attempting again. Do not retry immediately — wait for the device to fully boot.
