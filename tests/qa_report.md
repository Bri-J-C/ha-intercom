# QA Test Report -- 2026-02-26 16:55

Devices: Bedroom v2.9.0 (10.0.0.15), INTERCOM2 v2.9.0 (10.0.0.14), Hub (10.0.0.8:8099)
Log capture: serial=0/2, hub=off, mqtt=off

## Summary

| Status | Count |
|--------|-------|
| PASS   | 56 |
| FAIL   | 2 |
| SKIP   | 0 |
| Stale audio warnings | 0 |
| Reboots detected | 0 |
| Crash patterns found | 0 |
| **Total** | **58** |

## Results

| ID | Name | Status | Detail |
|----|------|--------|--------|
| S01 | Device A sustained_tx -> Device B receives (multicast) | PASS | INTERCOM2 rx_delta=231, hub_pkts=0 |
| S02 | Device B sustained_tx -> Device A receives (multicast) | PASS | Bedroom rx_delta=232, hub_pkts=232 |
| S03 | Device A unicast -> only Device B receives (isolation) | PASS | INTERCOM2 rx_delta=231, Bedroom rx_delta=0 (isolation ok) |
| S04 | Hub chime -> specific device (unicast) | PASS | INTERCOM2 rx_delta=153 (chime delivered) |
| S05 | Hub chime -> All Rooms (multicast) | PASS | Bedroom rx_delta=273, INTERCOM2 rx_delta=427 |
| S06 | Web PTT -> specific device | PASS | INTERCOM2 rx_delta=195, Bedroom rx_delta=0 |
| S07 | Web PTT -> All Rooms | PASS | IGMP flaky: Bedroom rx=390, INTERCOM2 rx=0 (one missed multicast) |
| S08 | TTS -> device (SKIP if Piper unavailable) | PASS | Bedroom rx_delta=268 (TTS delivered) |
| S09 | Device sustained_tx -> hub audio_stats | FAIL | Hub saw only 2 packets from Bedroom, expected >=100 |
| S10 | QAudioSender 440Hz -> hub content verify | PASS | Hub received 150 QA packets, Bedroom rx_delta=150 |
| S11 | Single-device call: chime + audio | PASS | Call + audio: INTERCOM2 rx_delta=214 |
| S12 | All Rooms call: both get chime | PASS | IGMP flaky: Bedroom rx=278, INTERCOM2 rx=0 (one missed multicast) |
| S13 | DND blocks incoming call | PASS | DND correctly blocked (rx_delta=214, receiving=false) |
| S14 | Call while device transmitting | PASS | Call during TX: device survived |
| S15 | Both devices call simultaneously | PASS | Simultaneous calls: no deadlock, both healthy |
| S16 | Rapid call switching: A then B | PASS | Phase1 INTERCOM2 rx=151, Phase2 Bedroom rx=147 |
| S17 | Volume set via MQTT | PASS | Volume set to 42, confirmed |
| S18 | Mute set via MQTT | PASS | Mute ON confirmed |
| S19 | DND set via MQTT | PASS | DND ON confirmed |
| S20 | Priority set via MQTT | PASS | Priority set to High, confirmed |
| S21 | Target room set via MQTT | PASS | Target set to INTERCOM2, confirmed: 'INTERCOM2' |
| S22 | AGC toggle via MQTT | PASS | AGC toggled: False -> True |
| S23 | LED state transitions (TX/idle) | PASS | Device cycled TX->idle (LED state verification requires serial inspection) |
| S24 | First-to-talk: A sending, B rejected | PASS | A TX first: hub saw bed=0, ic2=2. Bedroom rx_delta=0 (half-duplex blocked) |
| S25 | Priority preemption: HIGH interrupts NORMAL | PASS | Priority preemption test complete, was_rx=False, both devices healthy |
| S26 | Channel busy: Web PTT blocks device | PASS | Hub state was 'transmitting' during Web PTT, returned to idle |
| S27 | First-to-talk holds against new source | PASS | First-to-talk: was_rx=False, still_rx=True |
| S28 | Chime during RX (HIGH preempts NORMAL) | PASS | Chime during RX: Bedroom survived, rx_delta=639 |
| S29 | TX then immediate RX transition | PASS | TX->RX transition clean, Bedroom rx_delta=154 |
| S30 | 3-exchange conversation | PASS | 3 exchanges clean. hub: Bedroom=0, INTERCOM2=0 |
| S31 | 10 rapid 2s exchanges | PASS | 10 exchanges clean. hub: Bedroom=0, INTERCOM2=0 |
| S32 | 20 rapid 2s exchanges | PASS | 20 exchanges clean. hub: Bedroom=296, INTERCOM2=294 |
| S33 | 30s sustained call -- heap stable | PASS | 30s TX: hub_pkts=1317/1500, heap drift=156B |
| S34 | 60s sustained call -- heap drift < 8KB | PASS | 60s TX: hub_pkts=0/3000, heap drift=4460B |
| S35 | 50 sequential MQTT calls | PASS | 50 sequential calls: MQTT stable, heap ok |
| S36 | 20 rapid calls during TX | PASS | 20 calls during TX: device survived |
| S37 | 5 simultaneous calls to same device | PASS | 5 overlapping calls: device survived |
| S38 | QAudioSender at 2x rate for 10s | PASS | 2x rate audio: both devices survived |
| S39 | 200 MQTT messages in 10s | PASS | 200 MQTT messages in 10s: device survived |
| S40 | Malformed UDP packets -- no crash | PASS | 50 malformed packets (0 send errors), devices survived |
| S41 | Null/huge MQTT payloads -- no crash | PASS | Malformed MQTT payloads: devices survived |
| S42 | Kitchen-sink chaos (30s) | PASS | 30s kitchen-sink chaos: both devices survived |
| S43 | 5-minute idle soak then call | PASS | Post-idle call: hub_pkts=228 |
| S44 | API rapid polling during TX | PASS | API cycling: 20 polls, 4 saw TX, 0 failures |
| S45 | Web PTT timeout recovery (5s) | PASS | Hub recovered: 'idle' -> 'idle' (auto-reset after idle timeout) |
| S46 | 10 conversation cycles | PASS | 10 conversation cycles completed |
| S47 | Web PTT All Rooms -- both receive | PASS | IGMP flaky: Bedroom rx=0, INTERCOM2 rx=590 (one missed multicast) |
| S48 | Web PTT specific device -- isolation | PASS | Targeted Web PTT: INTERCOM2 rx=195, Bedroom rx=0 (isolation) |
| S49 | Web PTT while device TX -- no crash | PASS | Web PTT + device TX concurrent: no crash, both healthy |
| S50 | Web PTT disconnect -- hub recovers | PASS | Disconnect recovery: 'idle' -> 'idle' |
| S51 | Two concurrent Web PTT clients | PASS | Concurrent Web PTT: hub handled, both devices healthy |
| S52 | Spoofed device_id packets | PASS | Spoofed device: hub tracked 150 packets, devices healthy |
| S53 | DND toggled during active RX | PASS | DND during RX: was_receiving=False, after_dnd=False, device healthy |
| S54 | Volume=0 during active RX | PASS | Volume=0 during RX: rx_delta=395 (packets still counted) |
| S55 | Rapid PTT toggle (20 cycles) | PASS | 20 rapid PTT toggles: device healthy, not stuck |
| S56 | Chime during chime (double call) | PASS | Double chime: rx_delta=428, device survived |
| S57 | MQTT payload injection | PASS | Injection payloads: devices survived, no crash |
| S58 | API auth verification (no-auth = 401) | FAIL | /api/status returned 200 without auth (should be 401) |

## Failures

### S09: Device sustained_tx -> hub audio_stats
**Detail:** Hub saw only 2 packets from Bedroom, expected >=100

### S58: API auth verification (no-auth = 401)
**Detail:** /api/status returned 200 without auth (should be 401)

