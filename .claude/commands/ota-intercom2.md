Flash INTERCOM2 (10.0.0.14) via OTA through the HA server. Use this when INTERCOM2 is not physically USB-connected to the dev machine — direct OTA from the dev machine times out due to weak WiFi.

When INTERCOM2 IS physically connected via USB (/dev/ttyACM1), use the `flash-firmware` skill for direct serial flash instead.

## Steps

1. Check WiFi reachability first:
   `ping -c 2 10.0.0.14`
   If unreachable over WiFi and device is not USB-connected, do not proceed — escalate to user.

2. Build firmware if not already built:
   `/home/user/.pio-venv/bin/pio run -d /home/user/Projects/assistantlisteners/firmware`

3. Copy firmware binary to HA server:
   `scp /home/user/Projects/assistantlisteners/firmware/.pio/build/esp32s3/firmware.bin root@10.0.0.8:/tmp/firmware.bin`

4. Flash via curl from HA server (proxies through the LAN, avoids WiFi distance issue):
   `ssh root@10.0.0.8 "curl -s -o /dev/null -w '%{http_code}' -F 'firmware=@/tmp/firmware.bin' http://10.0.0.14/update"`
   Expected response: `200`

5. Wait 15 seconds for reboot, then verify:
   `curl -s --max-time 15 http://10.0.0.14/api/status`
   Confirm `version` field matches what was just flashed.

6. Report firmware version and reset reason from status response.

## Notes
- OTA endpoint is POST /update. No auth required.
- If scp path needs adjusting for an active worktree, substitute the worktree firmware path.
