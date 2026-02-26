Check TX packet statistics on both intercoms to verify audio transmission is working.

## Device Reference
- Bedroom Intercom: 10.0.0.15
- INTERCOM2: 10.0.0.14

## Steps

1. Fetch status JSON from both devices in parallel:
   ```
   curl -s --max-time 15 http://10.0.0.15/api/status
   curl -s --max-time 15 http://10.0.0.14/api/status
   ```

2. Display `tx_packets_sent`, `tx_packets_failed`, `tx_last_errno` for each device.

3. Also check for TX task startup messages in the HTML diagnostics page:
   ```
   curl -s http://10.0.0.15/diagnostics | grep -oE "(Audio TX task started[^<]*|Silence frame[^<]*|FATAL[^<]*)"
   curl -s http://10.0.0.14/diagnostics | grep -oE "(Audio TX task started[^<]*|Silence frame[^<]*|FATAL[^<]*)"
   ```

## Interpreting Results
- `tx_packets_sent > 0` after PTT press = TX working
- `tx_packets_failed > 0` = sendto errors (check `tx_last_errno`)
- "FATAL: Failed to create" = task creation failed (memory issue)
- "Audio TX task started" present = task is running
