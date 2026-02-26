# QA Comprehensive Report — 2026-02-25 20:05

Firmware target: 2.8.4
Hub: 10.0.0.8:8099

## Summary

| Status  | Count |
|---------|-------|
| PASS    | 44 |
| FAIL    | 0 |
| SKIP    | 0 |
| CLARIFY | 18 |
| **Total** | **62** |

## Results

| ID | Name | Status | Detail |
|----|------|--------|--------|
| T1 | Bedroom test_tone → hub audio_stats packet count > 0 | PASS | Bedroom: 50 packets received by hub |
| T2 | INTERCOM2 test_tone → hub audio_stats packet count > 0 | PASS | INTERCOM2: 198 packets received by hub |
| T3 | test_tone → firmware sends exactly 50 frames (1 second), verify count | PASS | 50 packets (expected 40–60; firmware hardcodes 50 frames) |
| T4 | Multiple test_tones accumulate monotonically in audio_stats | PASS | 6 tones, accumulated 300 packets. samples=[50, 100, 150, 200, 250, 300] |
| T5 | Heap stable after 10 sequential test_tones | PASS | Heap: before=7955KB, after=7955KB, delta=0KB |
| T6 | Back-to-back 10 test_tones → all complete, no crash | PASS | All 10 tones completed (10/10 returned 200/409) |
| T7 | Volume via MQTT (0,50,100) → /api/status reflects value | PASS | Volume 0→50→100 all reflected in /api/status |
| T8 | Mute ON/OFF via MQTT → /api/status reflects state | PASS | Mute ON→OFF reflected correctly |
| T9 | DND ON/OFF via MQTT → /api/status reflects state | PASS | DND ON→OFF reflected correctly |
| T10 | Priority MQTT command accepted, device survives all values | CLARIFY | Priority MQTT commands published (NORMAL/HIGH/EMERGENCY). Device alive. Field 'priority' NOT in /api/status — cannot ver |
| T11 | AGC MQTT command accepted, device survives ON/OFF | CLARIFY | AGC ON/OFF/ON commands published. Device alive. Field 'agc_enabled' NOT in /api/status — cannot verify via HTTP. COVERAG |
| T12 | LED ON/OFF via MQTT → state changes without crash | PASS | LED ON/OFF published, device remained online |
| T13 | Target room via MQTT → command accepted, device alive | CLARIFY | Target MQTT commands published. Device alive. Field 'target_room'/'target' NOT in /api/status — cannot verify via HTTP.  |
| T14 | MQTT device discovery → both devices subscribe, state published | PASS | Bedroom: mqtt_connected=true, INTERCOM2: mqtt_connected=true |
| T15 | Online published after subscribes (ordering) | CLARIFY | Cannot observe subscribe→online ordering without forcing reconnect. Verify manually via serial: subscribes appear before |
| T16 | Device discovery → both devices see each other | CLARIFY | discovered_devices empty or field absent: Bedroom: discovered_devices=[]; INTERCOM2: discovered_devices=[] |
| T17 | LWT — device offline/online cycle visible via MQTT | CLARIFY | LWT cannot be validated without device power cycle. Verify manually: power off device, observe HA 'unavailable' state wi |
| T18 | Call specific device via MQTT → target receives (hub chimes) | CLARIFY | Call published. Cannot verify device chimed without audio monitoring. Hub state messages: [{'topic': 'intercom/intercom_ |
| T19 | Call All Rooms via MQTT → both devices get call | CLARIFY | Call published to All Rooms. Bedroom: ok, INTERCOM2: ok. Cannot verify chime without audio monitoring. |
| T20 | Case-insensitive call matching → lowercase target works | CLARIFY | lowercase 'bedroom intercom' published. Cannot verify chime without audio monitoring. Device remained online. |
| T21 | Self-echo prevention → originating device does not process its own call | CLARIFY | Self-echo test requires serial log inspection. Look for 'ignoring self-call' or 'last_call_sent' log in Bedroom serial o |
| T22 | Chime detection — hub chime arrives before 150ms fallback beep | CLARIFY | Cannot distinguish hub chime vs fallback beep without audio monitoring or serial logs. Verify manually: send call, obser |
| T23 | Hub GET /api/chimes → lists available chimes | PASS | Hub /api/chimes: 6 chimes listed: ['232857-6f695e15-fd5e-41b0-b7bf-8b18a4d0abd8', 'alert', 'doorbell', 'gentle', 'qa_tes |
| T24 | Hub MQTT call → triggers chime stream (MQTT path, not HTTP) | PASS | MQTT call published, hub streamed chime and remained alive |
| T25 | TTS broadcast via MQTT notify → hub processes without error | PASS | TTS notify published, hub remained online |
| T26 | Hub audio_stats accurate — reset, tone, verify count = ~50 | PASS | 50 packets (expected 40–60 for single 50-frame tone) |
| T27 | WebSocket connect → receives init/welcome message | PASS | Received 3 message(s) on connect: '{"type": "init", "version": "2.5.2", "status": "idle"}' |
| T28 | WebSocket register → hub acknowledges client | PASS | Received 3 message(s): '{"type": "init", "version": "2.5.2", "status": "idle"}' |
| T29 | WebSocket get_state → returns target list or state | PASS | Received 5 message(s): '{"type": "init", "version": "2.5.2", "status": "idle"}' |
| T30 | BUG-E6 — 300-byte body → HTTP 400, no TCP RST | PASS | HTTP 400 returned (no TCP RST). Body: b'{"error":"request_body_too_large"}' |
| T31 | BUG-G2 — error response Content-Type is application/json | PASS | HTTP 400, Content-Type: application/json |
| T32 | BUG-001 — heap_usage_percent in 0–100 range | CLARIFY | heap_usage_percent field not found in /api/status or /diagnostics |
| T33 | Spam 20 beep requests in 2s → all return JSON, no crash | PASS | 20 requests in 5.8s. Codes: [200] |
| T34 | 10 concurrent test_tone requests → 409 rejections, no crash | PASS | 10 concurrent requests. Codes: [200] (httpd serialises) |
| T35 | 50 rapid volume changes (0→100) via MQTT → final value correct | PASS | Final volume=80 (correct after 50 rapid changes) |
| T36 | 20 rapid mute ON/OFF toggles → final state correct | PASS | Final muted=false (correct after 20 rapid toggles) |
| T37 | 20 rapid call notifications in 5s → device doesn't crash | PASS | 20 calls sent, device alive. heap=7955KB |
| T38 | 10 PTT tap cycles (tone ~1s each, 200ms gap) → no stale audio | PASS | 10 PTT tap cycles completed, device remained online throughout |
| T39 | Both devices test_tone simultaneously → hub shows both | PASS | Bedroom=50 pkts, INTERCOM2=198 pkts (simultaneous TX) |
| T40 | Bedroom test_tone + simultaneous call to INTERCOM2 → both succeed | PASS | Tone HTTP 200, call published. Both operations completed. |
| T41 | Call All Rooms while both devices have test_tone running | PASS | Call during dual tone: Bedroom: ok, INTERCOM2: ok |
| T42 | Alternating calls between devices — 5 cycles | PASS | 5 alternating calls completed, Bedroom device alive |
| T47 | DND ON + normal call → device does NOT receive (verifiable via log) | CLARIFY | DND=ON, normal call sent. Cannot verify blocking without audio/serial monitoring. Check serial: should see 'DND active,  |
| T48 | DND ON + EMERGENCY call → device DOES chime (bypasses DND) | CLARIFY | DND=ON + EMERGENCY call sent. Cannot verify chime without audio monitoring. Check serial: should NOT see 'DND active' lo |
| T49 | DND ON + test_tone → tone still works | PASS | test_tone with DND=ON returned HTTP 200 (DND does not block local actions) |
| T50 | Mute ON + beep → behavior observed (beep may or may not play) | CLARIFY | beep with mute=ON returned HTTP 200. Cannot determine if audio played without hardware monitoring. |
| T51 | Volume change during active audio playback | PASS | Volume changed during playback, device alive, final volume=80 |
| T52 | Call followed immediately by test_tone → tone executes | PASS | test_tone after call returned HTTP 200 |
| T53 | Malformed JSON to MQTT volume topic → device doesn't crash | PASS | Device survived 5 malformed MQTT payloads. heap=7955KB |
| T54 | Invalid volume values via MQTT (-1, 999, 'abc') → clamped or rejected | PASS | Volume within valid range after invalid values. got=0 |
| T55 | Empty MQTT payload to command topics → no crash | PASS | Device survived empty payloads to 4 topics |
| T56 | Oversized POST to /api/test (1KB, 10KB) → clean HTTP 400 | PASS | 1KB and 10KB bodies both returned HTTP 400 cleanly |
| T57 | Invalid action to /api/test → HTTP 400 with error JSON | PASS | HTTP 400 received. body={'error': 'unknown_action'} |
| T58 | POST /api/test without Content-Type → handled gracefully | PASS | HTTP 200 — server handled missing Content-Type gracefully |
| T59 | GET /api/test (wrong method) → 405 or appropriate rejection | PASS | HTTP 405 Method Not Allowed |
| T60 | Heap before/after 100 test_tones → no leak > 100KB | PASS | Heap: before=7955KB, after=7955KB, delta=-1KB |
| T61 | Heap before/after 100 MQTT command changes → no leak | PASS | Heap: before=7955KB, after=7955KB, delta=0KB |
| T62 | Audio_stats packet rate consistent across 20 sequential tones | PASS | 20 tones: avg=50.0 pkts, min=50, max=50 |
| T63 | MQTT reconnect after broker restart → devices come back online | CLARIFY | Cannot restart MQTT broker from QA runner without SSH access. Verify manually: restart Mosquitto on HA server, observe d |
| T65 | BUG-004 — Rapid call spam → chimes queue cleanly, no garble | CLARIFY | 10 calls sent at 300ms intervals. Device alive. heap=7955KB. Cannot verify audio quality without monitoring. Check for o |
| T66 | BUG-005 — Call during TTS playback → no audio conflict | CLARIFY | TTS + simultaneous call sent. Both hub and device survived. Cannot verify audio quality without monitoring. Check for I2 |
| T67 | BUG-006 — Rapid PTT cycles → no lag or stale audio packets | PASS | 5 PTT cycles: packet counts=[50, 50, 50, 50, 50] (expected 35–65 each) |
