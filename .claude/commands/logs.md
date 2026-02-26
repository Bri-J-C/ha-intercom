Fetch and display recent diagnostic logs from both ESP32 intercoms and the HA hub.

## Device Reference
- Bedroom Intercom: 10.0.0.15
- INTERCOM2: 10.0.0.14
- HA Server: 10.0.0.8

## Steps

1. Fetch status JSON from both intercoms in parallel:
   ```
   curl -s --max-time 15 http://10.0.0.15/api/status
   curl -s --max-time 15 http://10.0.0.14/api/status
   ```

2. Fetch recent hub logs:
   `ssh root@10.0.0.8 "ha apps logs local_intercom_hub --lines 40"`

3. For each intercom, grep key events from the HTML diagnostics page:
   ```
   curl -s http://10.0.0.15/diagnostics | grep -oE "(FATAL[^<]*|TX start[^<]*|AEC[^<]*|Audio TX task[^<]*|Silence frame[^<]*)"
   curl -s http://10.0.0.14/diagnostics | grep -oE "(FATAL[^<]*|TX start[^<]*|AEC[^<]*|Audio TX task[^<]*|Silence frame[^<]*)"
   ```

4. Summarize: firmware version, uptime, tx_packets_sent/failed, free heap, reset reason, and any errors.

If `$ARGUMENTS` is provided (e.g. "bedroom" or "intercom2"), only fetch from that device.
