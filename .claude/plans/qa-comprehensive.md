# Comprehensive QA Test Plan — v2.8.3 / Hub v2.5.2

## Test Infrastructure

### Device Network
- **Bedroom Intercom**: 10.0.0.15 (serial /dev/ttyACM0)
- **INTERCOM2**: 10.0.0.14 (serial /dev/ttyACM1)
- **Hub**: 10.0.0.8:8099
- **MQTT**: $MQTT_HOST:$MQTT_PORT (credentials from tests/.env)

### Hub API Endpoints
- **audio_stats**: GET/POST http://10.0.0.8:8099/api/audio_stats
  - GET: returns `{"stats": {<source_ip>: <packet_count>}}`
  - POST: clears all counters
- **WebSocket**: ws://10.0.0.8:8099/ws
- **Chime API**: POST http://10.0.0.8:8099/api/chime (multicast/unicast)

### ESP32 API Endpoints
- **/api/status** (GET): Device state, heap, uptime, MQTT status
- **/api/test** (POST): Actions (beep, test_tone), optional duration
  - Body: `{"action":"beep"}` or `{"action":"test_tone","duration":30}`
  - Returns: `{"result":"ok"}` or error JSON

### Test Tools
- curl for HTTP endpoints
- mosquitto_pub/mosquitto_sub for MQTT
- Python script for complex test sequences (binary frame generation, WebSocket)

---

## Category 1: Feature Verification (Happy Path)

### Audio Pipeline
- **T1**: test_tone on Bedroom → hub audio_stats shows packets from 10.0.0.15
  - Steps: POST /api/test `{"action":"test_tone","duration":3}` → wait 3.5s → GET /api/audio_stats → verify count > 100
  - Expected: Bedroom IP in stats, packet_count > 100 (50 packets/sec × 3.5s)

- **T2**: test_tone on INTERCOM2 → hub audio_stats shows packets from 10.0.0.14
  - Steps: Same as T1, target INTERCOM2
  - Expected: INTERCOM2 IP in stats, packet_count > 100

- **T3**: 30-second test_tone → audio_stats packet count matches expected
  - Steps: Reset audio_stats (POST) → tone 30s → GET stats → verify count ~1500 (50/sec × 30s)
  - Expected: 1400–1600 packets from device

- **T4**: 1-minute test_tone → audio_stats stable throughout
  - Steps: Reset → tone 60s → GET stats every 10s during tone → verify monotonic increase
  - Expected: Packet rate constant ~50/sec, no gaps

- **T5**: 5-minute test_tone → audio_stats stable, heap stable throughout
  - Steps: Reset → tone 300s → check /api/status heap every 30s → tone should complete
  - Expected: Heap free stays > 7MB, no ENOMEM errors, tone completes without interruption

- **T6**: Back-to-back test_tones (10 in sequence) → all complete, no failures
  - Steps: POST tone 3s → wait for 409 (busy) then retry → repeat 10x
  - Expected: All 10 tones succeed (or rejected with 409 when previous tone still running), no crashes

### MQTT Entity Control

- **T7**: Set volume via MQTT (0, 50, 100) → /api/status reflects each value
  - Steps: Publish to `intercom/intercom_<id>/set_volume` with 0 → GET /api/status → verify `"volume": 0` → repeat 50, 100
  - Expected: Volume changes applied immediately

- **T8**: Set mute ON/OFF via MQTT → /api/status reflects state
  - Steps: Publish `{"state":"on"}` to set_mute topic → GET /api/status → verify `"muted": true` → toggle OFF
  - Expected: Mute state changes applied

- **T9**: Set DND ON/OFF via MQTT → /api/status reflects state
  - Steps: Publish to set_dnd topic → verify in /api/status
  - Expected: DND state toggles correctly

- **T10**: Set priority Normal/High/Emergency via MQTT → verify in /api/status
  - Steps: Publish priority value → GET /api/status → verify `"priority": "NORMAL"` etc.
  - Expected: Priority changes reflected

- **T11**: Set AGC ON/OFF via MQTT → verify in /api/status
  - Steps: Toggle AGC via MQTT → GET /api/status
  - Expected: AGC state reflected

- **T12**: Set LED ON/OFF via MQTT → verify visual change (or /api/status if LED state tracked)
  - Steps: Publish to set_led_enabled topic → observe
  - Expected: LED state changes

- **T13**: Set target room via MQTT → verify in /api/status
  - Steps: Publish room name to set_target topic → verify returned in /api/status
  - Expected: Target room updated

### MQTT Infrastructure

- **T14**: Verify HA auto-discovery → 9 entities per device (sensor, number, 4 switches, 2 selects + DND switch)
  - Steps: Check Home Assistant Devices page or MQTT topic structure
  - Expected: All device entities present and controllable

- **T15**: Verify online published AFTER subscribes (serial log ordering)
  - Steps: Flash device with debug logging → observe serial output → "subscribed to X topics" before "online: true"
  - Expected: Availability publish comes after subscribe confirmations

- **T16**: Verify device discovery → both devices see each other in room list
  - Steps: GET /api/status on each device → check `"discovered_devices"` or equivalent
  - Expected: Each device lists the other device(s)

- **T17**: Verify LWT (Last Will Testament) → power cycle one device, other sees "offline"
  - Steps: Power off Bedroom → observe INTERCOM2 MQTT messages or Home Assistant status → device shows offline
  - Expected: Offline state published, then online when device reboots

### Call System

- **T18**: Call specific device via MQTT → target device chimes
  - Steps: Publish `{"target":"Bedroom Intercom"}` to `intercom/call` → listen on Bedroom for chime
  - Expected: Bedroom device receives call and chimes

- **T19**: Call "All Rooms" via MQTT → BOTH devices chime
  - Steps: Publish `{"target":"All Rooms"}` to `intercom/call` → both devices should chime
  - Expected: Simultaneous chimes on both devices

- **T20**: Case-insensitive call matching → "bedroom intercom" matches "Bedroom Intercom"
  - Steps: Publish lowercase target → verify device still receives call
  - Expected: Call accepted despite case difference

- **T21**: Self-echo prevention → device sending call does NOT chime itself
  - Steps: Trigger call from Bedroom via MQTT → Bedroom should NOT chime (only INTERCOM2)
  - Expected: Only non-originating device chimes

- **T22**: Chime detection → hub chime audio arrives before fallback beep
  - Steps: Monitor hub chime thread + device beep fallback timer → hub chime should complete before 150ms beep triggers
  - Expected: Audio plays, no fallback beep (or beep only if hub chime fails)

### Hub Audio Flow

- **T23**: Hub chime streaming multicast → both devices receive
  - Steps: POST hub /api/chime with multicast destination → check audio_stats on both devices → both should show packet reception
  - Expected: Both devices receive chime audio packets

- **T24**: Hub chime streaming unicast → only target receives
  - Steps: POST hub /api/chime with target IP (e.g., 10.0.0.15) → check audio_stats → only Bedroom should show packets
  - Expected: Only target device receives packets

- **T25**: TTS broadcast via MQTT notify topic → both devices play
  - Steps: Publish to `intercom/intercom_ZZZZZZZZ/notify` with TTS text → both devices play
  - Expected: TTS audio played on all devices

- **T26**: Hub audio_stats accurate → reset, send tone, verify counts match
  - Steps: POST /api/audio_stats (reset) → test_tone 10s → GET stats → verify count = ~500 (50/sec × 10s)
  - Expected: Packet count within ±5% of expected

### Web PTT (if testable via script)

- **T27**: WebSocket connect → receives "init" message
  - Steps: Open WebSocket to hub → wait for initial message
  - Expected: Hub sends initialization/welcome message

- **T28**: WebSocket identify → hub publishes client to MQTT
  - Steps: Send `{"type":"register","device_name":"qa-test"}` → check MQTT for client registration
  - Expected: Hub publishes web client as discovered device

- **T29**: WebSocket get_state → returns target list
  - Steps: Send state request → receive target room list
  - Expected: Hub returns available targets

### Bug Fix Verification

- **T30**: BUG-E6 — 300-byte body → JSON 400 (re-verify for confidence)
  - Steps: POST /api/test with 300-byte JSON body → expect HTTP 400, `{"error":"request_body_too_large"}`
  - Expected: Proper error response, no TCP RST

- **T31**: BUG-G2 — error content-type is application/json (re-verify)
  - Steps: POST /api/test with invalid JSON → verify `Content-Type: application/json` in response
  - Expected: JSON content-type on all error responses

- **T32**: BUG-001 — heap_usage_percent in diagnostics is 0-100 (re-verify)
  - Steps: GET /diagnostics or /api/status → check heap_usage_percent field
  - Expected: Value between 0–100, NOT negative (was -2465.6% on PSRAM devices before fix)

---

## Category 2: Stress Testing

### Rapid Actions

- **T33**: Spam 20 beep requests in 2 seconds → no crash, all return JSON
  - Steps: `for i in {1..20}; do curl -s POST /api/test -d '{"action":"beep"}'; done` rapidly
  - Expected: All requests return JSON (200 or 409 if rejected), no crashes, no TCP errors

- **T34**: Spam 10 test_tone requests while one running → 409 rejections, no crash
  - Steps: Start test_tone → immediately spam 10 more requests → expect 409 "busy" responses
  - Expected: Encoder mutex prevents concurrent tones, returns 409, no crash

- **T35**: Spam 50 volume changes (0→100→0) in 5 seconds via MQTT
  - Steps: Rapid mosquitto_pub calls changing volume → verify device handles gracefully
  - Expected: All changes processed, final value correct, no resets

- **T36**: Spam 20 mute ON/OFF toggles in 2 seconds → final state correct
  - Steps: Rapid MQTT publishes toggling mute
  - Expected: Final mute state correct, no unexpected state

- **T37**: Spam 20 call notifications in 5 seconds → device doesn't crash
  - Steps: Rapid chime API calls or MQTT call messages
  - Expected: Device handles gracefully, no lockup, no ENOMEM

- **T38**: Rapid PTT tap-and-talk-release cycles — 10 cycles in 10 seconds
  - Steps: Simulate PTT button presses: press → test_tone 1s → release → wait 100ms → repeat 10x
  - Expected: No stale audio playback, no lag, all cycles complete

### Concurrent Multi-Node

- **T39**: Both devices test_tone simultaneously → hub audio_stats shows BOTH, no crash
  - Steps: Start tone on Bedroom → start tone on INTERCOM2 → GET audio_stats → both IPs present with packet counts
  - Expected: Both devices transmit simultaneously without interference, hub receives both streams

- **T40**: Device A test_tone + call to Device B simultaneously
  - Steps: Bedroom tone starts → immediately call Bedroom from MQTT → INTERCOM2 should chime while Bedroom tones
  - Expected: Both operations proceed without corruption

- **T41**: Call "All Rooms" from MQTT while both devices have test_tone running
  - Steps: Both tones running → call "All Rooms" → both devices should respond (or ignore if chime preemption implemented)
  - Expected: Graceful handling (either preempt tones with chime or ignore call during tone)

- **T42**: Rapid alternating calls between devices — A calls B, then B calls A, repeat 10x
  - Steps: Call Bedroom → wait 2s for chime → call INTERCOM2 → repeat 5x
  - Expected: All calls successful, no drops, no chimes overlap

### Extended Soak

- **T43**: 5-minute continuous test_tone on Bedroom → monitor heap, audio_stats, MQTT
  - Steps: test_tone 300s → check /api/status every 30s → monitor heap for leaks
  - Expected: Tone completes, heap stable (no > 5% decrease), audio_stats monotonic

- **T44**: 5-minute continuous test_tone on INTERCOM2 → same
  - Steps: Same as T43 on INTERCOM2
  - Expected: Same results

- **T45**: 10-minute idle soak → both devices, verify MQTT connected, heap stable
  - Steps: No actions → monitor MQTT keepalive messages → check heap every 1 min
  - Expected: Devices stay online, MQTT stable, heap constant

- **T46**: Back-to-back 30s tones for 5 minutes total (10 tones, 30s each) → no degradation
  - Steps: Loop: tone 30s → wait 1s → repeat 10x
  - Expected: All tones complete successfully, no degradation in audio quality or timing

---

## Category 3: Edge Cases

### State Machine

- **T47**: Set DND ON, send NORMAL call → device does NOT chime
  - Steps: Set DND via MQTT → call device → expect no chime
  - Expected: Call accepted (MQTT received) but device doesn't chime

- **T48**: Set DND ON, send EMERGENCY call → device DOES chime
  - Steps: DND ON → call with priority EMERGENCY → expect chime
  - Expected: Emergency calls bypass DND

- **T49**: Set DND ON, trigger test_tone → should still work
  - Steps: DND ON → POST /api/test (test_tone) → should complete
  - Expected: Local test_tone not affected by DND (DND only blocks incoming calls, not local actions)

- **T50**: Set mute ON, trigger beep → behavior TBD (clarify with user)
  - Steps: Mute ON → POST /api/test (beep) → check if audio plays
  - Expected: Depends on design — beep may be muted or may play regardless

- **T51**: Change volume during active audio playback
  - Steps: test_tone 5s → during playback, change volume → tone should continue at new volume
  - Expected: Volume change applied smoothly, no audio glitches

- **T52**: PTT lockout — send call, then immediately try test_tone (within 2s) → should still work
  - Steps: Call device → within 1s, POST /api/test → expect tone to work (PTT button lockout, not tone lockout)
  - Expected: Tone starts after call completes or in parallel

### Protocol Edge Cases

- **T53**: Send malformed JSON to MQTT command topics → device doesn't crash
  - Steps: Publish invalid JSON (missing quotes, truncated) to set_volume topic
  - Expected: Device ignores or logs error, doesn't crash

- **T54**: Send invalid volume values via MQTT (-1, 999, "abc") → rejected or clamped
  - Steps: Send volume -1, 999, "invalid" to set_volume
  - Expected: Values clamped to 0–100 or rejected gracefully

- **T55**: Send empty MQTT payload to command topics → no crash
  - Steps: Publish empty string to set_mute
  - Expected: Ignored or default applied, no crash

- **T56**: Send oversized POST to /api/test (1KB, 10KB bodies) → clean rejection
  - Steps: POST with 1KB and 10KB JSON bodies → expect HTTP 400
  - Expected: Body size check rejects, no crash

- **T57**: Send invalid action to /api/test → JSON 400 unknown_action
  - Steps: POST `{"action":"invalid"}` → expect 400 with error message
  - Expected: Clean error response

- **T58**: POST /api/test with missing Content-Type → handled gracefully
  - Steps: POST without Content-Type header
  - Expected: Default to JSON or appropriate error, no crash

- **T59**: GET /api/test (wrong method) → 405 or appropriate error
  - Steps: GET /api/test (POST expected)
  - Expected: 405 Method Not Allowed or appropriate rejection

### Resource Monitoring

- **T60**: Track heap before and after 100 test_tones → no leak
  - Steps: Check /api/status heap → run 100 tones (3s each) → check heap again → verify difference < 100KB
  - Expected: No heap leak detected

- **T61**: Track heap before and after 100 MQTT command changes → no leak
  - Steps: Check heap → change volume/mute/DND 100 times → check heap → verify stable
  - Expected: No memory leak from MQTT command processing

- **T62**: Monitor audio_stats during extended test — packet rate consistent, no gaps
  - Steps: 5-min tone → reset audio_stats every 30s → verify rate ~50/sec for each interval
  - Expected: Packet rate constant, no drops or bursts

- **T63**: MQTT reconnect after hub restart — devices come back online
  - Steps: ssh to hub, restart MQTT → observe devices reconnect
  - Expected: Devices show offline briefly, then online after reconnect (< 60s)

---

## Category 4: Known Bug Reproduction

- **T64**: BUG-003 (P0) — 30-min soak → TCP/ARP still working?
  - Steps: 30-min continuous idle with periodic /api/status checks → monitor MQTT connection
  - Expected: Both devices stay online, MQTT responsive throughout (this is the longstanding stability issue)

- **T65**: BUG-004 — Spam call button → chimes overlap/garble?
  - Steps: Rapid calls (back-to-back, < 1s interval) → listen for audio quality
  - Expected: Chimes either queue or new chime preempts previous (no overlap/garbling)

- **T66**: BUG-005 — Call during TTS → audio conflict?
  - Steps: Start TTS (notify topic) → during playback, send call → observe audio
  - Expected: Clean audio (either TTS continues, or chime preempts TTS cleanly)

- **T67**: BUG-006 — Rapid PTT cycles → lag/garbled audio/stale packets?
  - Steps: PTT 1s tone → wait 500ms → PTT 1s tone → repeat 5x → monitor audio quality
  - Expected: No lag, no garbled audio, no stale packets playing on next tone

- **T68**: BUG-002 — INTERCOM2 extended use (30+ min) → ENOMEM?
  - Steps: 30-min continuous operation on INTERCOM2 (periodic tones, calls, MQTT) → monitor for ENOMEM errors
  - Expected: No ENOMEM, sustained operation

---

## Execution Notes

### Prerequisites
- Both devices flashed with v2.8.3, online and MQTT connected
- Hub deployed with v2.5.2, audio_stats endpoint functional
- MQTT credentials verified ($MQTT_USER / $MQTT_PASS)
- Network stable (devices and hub on 10.0.0.x subnet, multicast 239.255.0.100:5005 routing verified)

### Execution Order
1. **Category 1 (Feature Verification)** — Basic functionality, should all pass if v2.8.3 is stable
2. **Category 2 (Stress Testing)** — Push system limits, identify performance bottlenecks
3. **Category 3 (Edge Cases)** — Boundary conditions, error handling, state management
4. **Category 4 (Known Bugs)** — Regression tests for previously identified issues

### Important Coordination Rules
- **NEVER run QA and devops flash simultaneously** — Race condition causes false failures (QA tests MQTT timing; flash disrupts connection)
- **Reset audio_stats (POST /api/audio_stats) before each audio test** — Prevents cross-test contamination
- **Allow 2–3 seconds between tests that trigger audio** — Beep/tone completion + device state settling
- **Monitor serial logs during tests** — Errors on /dev/ttyACM0 and /dev/ttyACM1 may reveal silent failures
- **Stop QA before any devops operations** — Restart QA after online confirmation

### Test Tools
```bash
# HTTP tests
curl -s http://10.0.0.15/api/status | jq
curl -s -X POST http://10.0.0.15/api/test -d '{"action":"beep"}' | jq

# MQTT tests
mosquitto_pub -h 10.0.0.8 -u $MQTT_USER -P $MQTT_PASS -t intercom/call -m '{"target":"All Rooms"}'
mosquitto_sub -h 10.0.0.8 -u $MQTT_USER -P $MQTT_PASS -t 'intercom/#'

# WebSocket (Python)
import websocket
ws = websocket.create_connection("ws://10.0.0.8:8099/ws")
ws.send('{"type":"register","device_name":"qa-test"}')
```

### Reporting
- **Pass**: Test completed as expected, no errors
- **Fail**: Test did not meet expectations, specific error or observation recorded
- **Skip**: Test conditions not met (e.g., feature not implemented)
- After all 68 tests: Generate QA_REPORT_v2.8.3.md with summary (pass/fail/skip counts, key findings, bugs discovered)

---

## Glossary

| Term | Definition |
|---|---|
| MQTT | Message Queuing Telemetry Transport — device-to-hub communication protocol |
| LWT | Last Will Testament — MQTT feature for publishing offline state |
| DND | Do Not Disturb — incoming call blocking mode |
| AGC | Automatic Gain Control — RX audio level normalization |
| Opus | Audio codec for compression (32kbps VBR, 16kHz mono) |
| Multicast | UDP broadcast to group (239.255.0.100:5005 in this system) |
| Unicast | UDP to specific device IP |
| TTS | Text-to-Speech synthesis via Wyoming/Piper |
| PTT | Push-to-Talk — transmission initiated by button/action |
| Beep | Simple tone (fallback notification if hub unreachable) |
| Chime | Hub-generated audio notification on call |
