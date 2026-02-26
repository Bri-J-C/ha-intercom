Build the ESP32 firmware and flash both intercoms.

## Device Reference
- Bedroom Intercom: 10.0.0.15, serial /dev/ttyACM0
- INTERCOM2: 10.0.0.14, serial /dev/ttyACM1
- PlatformIO: /home/user/.pio-venv/bin/pio
- Firmware dir: /home/user/Projects/assistantlisteners/firmware (or active worktree equivalent)

## Steps

1. Build firmware using absolute path:
   `/home/user/.pio-venv/bin/pio run -d /home/user/Projects/assistantlisteners/firmware`

2. If build succeeds, flash devices. Flash sequentially (parallel flashing with & is unreliable — background process loses its working directory):
   ```
   /home/user/.pio-venv/bin/pio run -d /home/user/Projects/assistantlisteners/firmware -t upload --upload-port /dev/ttyACM0
   /home/user/.pio-venv/bin/pio run -d /home/user/Projects/assistantlisteners/firmware -t upload --upload-port /dev/ttyACM1
   ```

3. Wait ~12 seconds for boot, then verify firmware version on both devices:
   ```
   curl -s --max-time 15 http://10.0.0.15/api/status
   curl -s --max-time 15 http://10.0.0.14/api/status
   ```
   The response is JSON with a `version` field. Confirm version matches what was just flashed.

4. Report firmware version, uptime, free heap, and any errors visible in the status response.

## INTERCOM2 Serial vs OTA
INTERCOM2 can be flashed via serial (/dev/ttyACM1) when physically USB-connected to the dev machine — this is preferred when available. When INTERCOM2 is not physically connected, use the `flash-intercom2` skill to OTA via the HA server (10.0.0.8). Always ping 10.0.0.14 first to check WiFi reachability before deciding.

## Worktree Note
If an active worktree is in use (e.g. /home/user/Projects/assistantlisteners/.claude/worktrees/chime-upload/firmware), substitute that path for /home/user/Projects/assistantlisteners/firmware in all commands above.
