# HA Intercom — Test Suite

Integration test suite for the HA Intercom system. 65 tests across 10 categories exercising real audio paths on live hardware.

## Overview

The test suite validates the full system end-to-end: ESP32 firmware, hub add-on, MQTT integration, UDP audio delivery, Web PTT, chime playback, and protocol robustness. Tests run against two physical ESP32 nodes and a live Home Assistant instance.

### Test Files

| File | Purpose |
|------|---------|
| `tests/test_harness.py` | Shared infrastructure: monitors, helpers, HTTP/MQTT clients, `AudioAnalyzer` |
| `tests/test_audio_scenarios.py` | 65 test functions (S01–S65) across 10 categories |
| `tests/qa_audio_sender.py` | `QAudioSender` — generates and sends Opus-encoded sine waves over UDP |
| `tests/.env.example` | Environment variable template — copy to `tests/.env` and fill in |

### Dependencies

**Required:**
```
paho-mqtt      MQTT client
pyserial       Serial log monitoring
opuslib        Opus encode/decode for QAudioSender and AudioAnalyzer
```

**Optional:**
```
websockets     Web PTT tests (S06, S07, S26, S45–S51, S59)
numpy          Audio quality analysis in AudioAnalyzer (S62, S63, S64)
```

Install all:
```bash
pip install paho-mqtt pyserial opuslib websockets numpy
```

## Setup

### 1. Copy the environment template

```bash
cp tests/.env.example tests/.env
```

### 2. Fill in tests/.env

```ini
# Device IPs
BEDROOM_IP=10.0.0.15
INTERCOM2_IP=10.0.0.14
HUB_IP=10.0.0.8
HUB_PORT=8099

# MQTT broker (usually same host as HUB_IP)
MQTT_HOST=10.0.0.8
MQTT_PORT=1883
MQTT_USER=your_mqtt_user
MQTT_PASS=your_mqtt_password

# Device unique IDs (from HA MQTT auto-discovery)
# Find these in HA → Settings → Devices → select the intercom device → Identifiers
BEDROOM_UNIQUE_ID=intercom_xxxxxxxx
INTERCOM2_UNIQUE_ID=intercom_xxxxxxxx
HUB_UNIQUE_ID=intercom_xxxxxxxx

# Device IDs: 8-byte hex from ESP32 MAC, shown in serial logs at boot
# Look for: "Device ID: XXXXXXXXXXXXXXXX"
BEDROOM_DEVICE_ID=xxxxxxxxxxxxxxxx
INTERCOM2_DEVICE_ID=xxxxxxxxxxxxxxxx

# ESP32 HTTP Basic Auth
DEVICE_USER=admin
DEVICE_PASS=your_device_web_password

# Serial ports (optional, used for crash detection)
BEDROOM_SERIAL=/dev/ttyACM0
INTERCOM2_SERIAL=/dev/ttyACM1
```

**Finding your device IDs:**
- `BEDROOM_UNIQUE_ID` / `INTERCOM2_UNIQUE_ID`: Go to HA → Settings → Devices & services → Devices → select the intercom device → look for the identifier (e.g. `intercom_5fe36818`)
- `BEDROOM_DEVICE_ID` / `INTERCOM2_DEVICE_ID`: Connect via serial and look for `Device ID:` in boot logs, or check `GET /api/status` response field `device_id`
- `HUB_UNIQUE_ID`: Same process — hub is also discoverable in HA as a device

### 3. Verify connectivity

Before running the suite, confirm both devices and hub are reachable:

```bash
curl -u admin:your_pass http://10.0.0.15/api/status
curl -u admin:your_pass http://10.0.0.14/api/status
curl http://10.0.0.8:8099/api/audio_stats
```

## Running Tests

From the project root:

```bash
# Run all 65 tests
python3 tests/test_audio_scenarios.py

# Run a single test by ID
python3 tests/test_audio_scenarios.py --test S05

# Run a category (1-10)
python3 tests/test_audio_scenarios.py --category 1

# Skip tests longer than 60 seconds (S33, S34, S42, S43)
python3 tests/test_audio_scenarios.py --skip-long

# List all tests without running them
python3 tests/test_audio_scenarios.py --list
```

### Test Result Codes

| Code | Meaning |
|------|---------|
| `PASS` | Test passed |
| `FAIL` | Test failed — check detail string |
| `SKIP` | Test skipped — optional dependency not installed (e.g. `websockets`) or condition not met |

## Test Categories

### Category 1: Basic Audio Paths (S01–S10)

Validates that audio actually travels end-to-end through the real delivery path.

| Test | Description |
|------|-------------|
| S01 | Device A (Bedroom) `sustained_tx` → Device B (INTERCOM2) receives via multicast |
| S02 | Device B (INTERCOM2) `sustained_tx` → Device A (Bedroom) receives via multicast |
| S03 | Device A unicast to B (`target=INTERCOM2`) — non-target device is isolated |
| S04 | Hub chime → specific device (unicast call via MQTT) |
| S05 | Hub chime → All Rooms (multicast call via MQTT) |
| S06 | Web PTT → specific device (WebSocket simulation, requires `websockets`) |
| S07 | Web PTT → All Rooms (WebSocket simulation, requires `websockets`) |
| S08 | TTS via MQTT notify → devices receive audio (requires Piper add-on running) |
| S09 | Device `sustained_tx` → hub `/api/audio_stats` confirms receipt and packet count |
| S10 | `QAudioSender` 440 Hz → hub receives → packet count and sequence verified |

**What S01/S02 validate:** Device `rx_packet_count` increments by at least 100 (250 packets expected for 5s). Hub `audio_stats` confirms packets arrived. Sequence continuity checked.

**S03 unicast isolation:** Transmitter sets `target=INTERCOM2` before sending. Non-target (Bedroom) is also the transmitter, so half-duplex blocks its own RX — the test verifies INTERCOM2 receives at least 100 packets via unicast.

**S08 TTS:** Returns `SKIP` if Piper is not running — TTS is optional.

### Category 2: Call System (S11–S16)

Validates the MQTT call flow: hub receives a call message, streams a chime, and the callee device plays it.

| Test | Description |
|------|-------------|
| S11 | Single-device call: MQTT call → chime + audio flows |
| S12 | All Rooms call: both devices get chime via multicast |
| S13 | DND blocks incoming call audio (device does not enter `receiving` state) |
| S14 | Call while device is transmitting — no crash, device survives |
| S15 | Simultaneous calls in both directions — no deadlock |
| S16 | Rapid call switching: call A, wait, call B — each device gets chime |

**MQTT call payload format:**
```json
{"target": "INTERCOM2", "caller": "QA Test"}
```
Both `target` and `caller` fields are required. The firmware JSON parser silently ignores messages missing either field.

**S13 DND:** Sets DND via `intercom/{unique_id}/dnd/set` before the call, then verifies the device never enters `receiving=true`. Note: `rx_packet_count` still increments (UDP packets arrive at the socket before the DND filter). Restores DND=OFF after the test.

### Category 3: MQTT Entity Control (S17–S23)

Verifies that all MQTT-settable parameters apply and are reflected in `/api/status`.

| Test | Description |
|------|-------------|
| S17 | Volume set to 42 via MQTT → confirmed in `/api/status` |
| S18 | Mute ON via MQTT → confirmed |
| S19 | DND ON via MQTT → confirmed (waits up to 3s for NVS save) |
| S20 | Priority set to `High` (value=1) via MQTT → confirmed |
| S21 | Target set to `INTERCOM2` via MQTT → confirmed |
| S22 | AGC toggled via MQTT → confirmed as flipped |
| S23 | LED state verification: device cycles through TX→idle |

All tests restore the original value after verification.

**MQTT topics used:**
- `intercom/{unique_id}/volume/set` — numeric string `"42"`
- `intercom/{unique_id}/mute/set` — `"ON"` or `"OFF"`
- `intercom/{unique_id}/dnd/set` — `"ON"` or `"OFF"`
- `intercom/{unique_id}/priority/set` — `"Normal"`, `"High"`, or `"Emergency"`
- `intercom/{unique_id}/target/set` — room name string
- `intercom/{unique_id}/agc/set` — `"ON"` or `"OFF"`

### Category 4: Audio Collision and Priority (S24–S29)

Validates first-to-talk collision avoidance and priority preemption.

| Test | Description |
|------|-------------|
| S24 | First-to-talk: A transmitting, B tries to send — B's audio does not replace A's |
| S25 | Priority preemption: NORMAL stream, HIGH priority source interrupts |
| S26 | Channel busy: Web PTT active → hub reports `transmitting` state |
| S27 | New source during active RX → first-to-talk holds (original stream continues) |
| S28 | Chime during active audio RX — chime plays (HIGH priority preempts NORMAL) |
| S29 | TX then immediate RX — clean half-duplex transition |

**S25 details:** Sets INTERCOM2 to `High` priority before the test. `QAudioSender` sends `PRIORITY_NORMAL` audio first to establish the channel. INTERCOM2 then transmits at `High` priority to preempt. Verifies both devices survive.

### Category 5: Conversation Simulations (S30–S34)

Validates multi-exchange conversation integrity and heap stability under sustained load.

| Test | Description |
|------|-------------|
| S30 | 3-exchange conversation (A→B→A), each 3s |
| S31 | 10 rapid 2s exchanges (alternating A/B) |
| S32 | 20 rapid 2s exchanges |
| S33 | 30-second sustained call — heap drift must be < 8192 bytes |
| S34 | 60-second sustained call — heap drift must be < 8192 bytes |

S33 and S34 monitor heap every 10 seconds during the call and report the drift from start to end. These are the primary heap leak detection tests.

### Category 6: Stress and Durability (S35–S42)

Hammers the system with high message rates and adversarial inputs.

| Test | Description |
|------|-------------|
| S35 | 50 sequential MQTT calls (0.5s apart) — MQTT stays connected, heap stable |
| S36 | 20 rapid MQTT calls during active TX — device survives |
| S37 | 5 simultaneous MQTT calls to same device — no crash |
| S38 | Two `QAudioSender` instances running concurrently (double UDP rate, 10s) |
| S39 | 200 MQTT messages in 10s (mixed: volume, calls, mute) |
| S40 | 50 malformed UDP packets (random garbage, sizes 1–2000 bytes) — no crash |
| S41 | Malformed MQTT payloads (null, empty, huge, binary) — no crash |
| S42 | Kitchen-sink chaos: sustained TX + calls + malformed UDP + API polling for 30s |

S42 is the longest stress test at ~35 seconds plus device monitoring.

### Category 7: Idle and Recovery (S43–S46)

Validates behavior after extended idle periods and recovery from stuck states.

| Test | Description |
|------|-------------|
| S43 | 5-minute idle soak — post-soak call delivers audio to hub (< 5 min by default) |
| S44 | Rapid API polling during TX — no more than 2 failures in 20 polls |
| S45 | Web PTT timeout recovery — hub auto-resets `transmitting` state after 5s idle |
| S46 | 10 full conversation cycles (call + 5s audio + 2s idle each) |

**S43 is skipped in default runs** with `--skip-long` (300s idle + test time).

**S45 mechanism:** Hub tracks `last_web_ptt_frame_time` (monotonic). `_check_web_ptt_timeout()` auto-resets `current_state` to `idle` after `WEB_PTT_IDLE_TIMEOUT` (5.0 seconds) with no audio frames. This prevents the hub from staying stuck in `transmitting` when a mobile client disconnects without sending `ptt_stop`.

### Category 8: Web PTT (S47–S51)

Validates WebSocket-based PTT from browser clients. Requires `pip install websockets`.

| Test | Description |
|------|-------------|
| S47 | Web PTT to All Rooms — both devices receive (multicast) |
| S48 | Web PTT to specific device — only target receives |
| S49 | Web PTT while device PTT is active — no crash |
| S50 | Web PTT disconnect without `ptt_stop` — hub auto-recovers within 5s |
| S51 | Two concurrent Web PTT clients — second gets busy, hub returns to idle |

All Web PTT tests return `SKIP` if `websockets` is not installed.

**WebSocket protocol:** Client connects to `ws://<hub-ip>:8099/ws`, sends JSON `{"type": "register", "device_name": "client-id"}`, then sends binary 640-byte PCM frames (320 samples × 16-bit little-endian) at 50 fps to transmit. Hub Opus-encodes and forwards via UDP.

### Category 9: Abuse, Misuse, and Edge Cases (S52–S58)

Validates robustness against spoofed data, injection attacks, and boundary conditions.

| Test | Description |
|------|-------------|
| S52 | Spoofed device_id (`FAKEID!!`) — hub tracks it, devices survive |
| S53 | DND toggled ON during active RX — playback stops, device survives |
| S54 | Volume set to 0 during active RX — packets still counted (socket-level), device survives |
| S55 | Rapid PTT toggle: 20 start/stop cycles in 10s — device not stuck |
| S56 | Two calls 500ms apart — at least one chime delivers, device survives |
| S57 | MQTT payload injection: XSS, SQL injection, binary garbage, emoji in `caller`/`target` |
| S58 | API auth enforcement: `/api/status` and `/api/test` must return `401` without auth |

**S58 known behavior:** If `web_admin_password` is empty (default after first flash), the firmware allows unauthenticated access. S58 will FAIL until a password is configured in the web UI. This is by design for initial setup.

### Category 10: Audio Verification (S60–S65)

Validates actual audio content using the hub's capture buffer and `AudioAnalyzer`.

| Test | Description |
|------|-------------|
| S60 | Hub capture: trigger chime → verify TX frames captured with monotonic sequences |
| S61 | Hub TX packet count matches device `rx_packet_count` (with allowance for WiFi loss) |
| S62 | ESP32 `sustained_tx` → hub captures RX frames → decode → verify non-silent |
| S63 | `QAudioSender` 440 Hz → hub captures → decode + FFT → verify 440 Hz dominates |
| S64 | Chime → hub capture TX → decode → verify non-silent + expected frame count |

**S62/S63/S64 require `opuslib` and `numpy`** for full audio analysis. Without them, tests pass based on frame count alone.

**`AudioAnalyzer` API:**
```python
from test_harness import AudioAnalyzer

# Decode base64 Opus frames to numpy PCM
pcm = AudioAnalyzer.decode_frames(opus_b64_list, sample_rate=16000)

# Find dominant frequency
freq = AudioAnalyzer.dominant_frequency(pcm, sample_rate=16000)

# Full quality report
quality = AudioAnalyzer.check_audio_quality(opus_b64_list, expected_freq=440.0)
# Returns: {"non_silent": True, "rms": 0.12, "dominant_freq": 441.2,
#           "freq_match": True, "snr_db": 18.4, "snr_ok": True,
#           "duration_s": 3.0}
```

**Audio capture API (hub):**
```python
from test_harness import start_audio_capture, stop_audio_capture, fetch_audio_capture

start_audio_capture()         # POST /api/audio_capture {"action": "start"}
# ... trigger audio ...
frames = fetch_audio_capture(direction="rx", device_id="hex_id")
stop_audio_capture()          # POST /api/audio_capture {"action": "stop"}

# Each frame in frames["frames"]:
# {"seq": 42, "opus_b64": "...", "direction": "rx", "device_id": "...", "ts": 1706000000.0}
```

## Test Infrastructure

### `QAudioSender`

Simulates an ESP32 transmitting audio via UDP multicast. Generates a sine wave, Opus-encodes it at 16 kHz / 32 kbps, and sends 20ms frames at 50 fps.

```python
from qa_audio_sender import QAudioSender

# Send 440 Hz sine wave for 3 seconds via UDP multicast
sender = QAudioSender(frequency=440.0, amplitude=0.5)
sender.start(duration_seconds=3)
sender.wait(timeout=10)

# Custom device ID and priority
sender = QAudioSender(
    frequency=880.0,
    amplitude=0.8,
    device_id=b"MY_DEV!",   # 8 bytes
    priority=1,              # HIGH
)

# Unicast to specific IP
sender = QAudioSender(frequency=440.0, unicast_ip="10.0.0.15")
```

Default device ID: `QA_TEST!` (hex `5141415f544553542100000000000000` — well-known, not a real device).

### `HeapTracker`

Periodically polls `/api/status` and records free heap samples.

```python
from qa_audio_sender import HeapTracker

tracker = HeapTracker(device_ip="10.0.0.15", interval_s=5.0)
tracker.start()
# ... run test ...
tracker.stop()
summary = tracker.summary()
# Returns: {"min": 180000, "max": 185000, "drift": 5000, "samples": [...]}
```

### Helper Functions (from `test_harness.py`)

```python
from test_harness import (
    device_status,          # GET /api/status from a device
    get_audio_stats,        # GET /api/audio_stats from hub
    reset_audio_stats,      # POST /api/audio_stats to reset
    get_hub_state,          # Returns current_state from audio_stats
    ensure_hub_idle,        # Wait until hub state == "idle" (up to timeout)
    hub_packet_count,       # Extract packet count for a device_id from stats
    check_sequence_continuity,  # Analyze seq_min/seq_max/packet_count
    trigger_sustained_tx,   # POST /api/test {"action": "sustained_tx", "duration": N}
    wait_for_tx_complete,   # Poll /api/status until sustained_tx_active=false
    start_audio_capture,    # Enable hub audio capture buffer
    stop_audio_capture,     # Disable capture
    fetch_audio_capture,    # GET captured frames
    mqtt_publish,           # Publish a single MQTT message
    reboot_device,          # POST /api/test {"action": "reboot"}
)
```

### `MqttSession`

Context manager for publishing multiple MQTT messages with a single connection:

```python
from test_harness import MqttSession

with MqttSession(client_id="my_test") as session:
    for i in range(50):
        session.publish("intercom/call", '{"target": "Bedroom", "caller": "QA"}')
        time.sleep(0.5)
```

### `WebPTTClient`

Simulates a Web PTT browser client over WebSocket:

```python
from test_harness import WebPTTClient

client = WebPTTClient("QA_Client")

# Synchronous: connect, transmit, disconnect (blocks until done)
ok = client.transmit(target="All Rooms", duration=3.0)

# Asynchronous: returns immediately, call join() to wait
thread = client.transmit_async(target="INTERCOM2", duration=5.0)
thread.join(timeout=10)

# Disconnect without ptt_stop (tests hub auto-recovery)
client.transmit(target="All Rooms", duration=2.0, disconnect_without_stop=True)
```

### `ensure_hub_idle`

Polls `GET /api/audio_stats` until `current_state == "idle"`. Use at the start of every test to prevent state contamination from prior tests:

```python
ok, detail = ensure_hub_idle(timeout=15.0, label="S05")
if not ok:
    return FAIL, detail
```

### `check_sequence_continuity`

Analyzes sequence numbers in hub audio_stats to detect packet loss:

```python
stats = get_audio_stats()
sc = check_sequence_continuity(stats, device_id_hex)
# Returns: {"ok": True, "expected": 250, "received": 248,
#           "lost": 2, "loss_pct": 0.8, "seq_min": 1, "seq_max": 250}
```

## Tools

### `test_node.py` — Manual Test Node

A command-line tool for manually exercising the intercom system. Reads `HUB_IP`, `BEDROOM_IP`, `INTERCOM2_IP`, `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS` from environment variables.

```bash
# Send a call notification (hub streams chime to target)
python3 tools/test_node.py call "Bedroom Intercom"

# Stream a test tone directly to a device (bypasses hub, UDP only)
python3 tools/test_node.py chime 10.0.0.15 5

# Stream silence
python3 tools/test_node.py silence 10.0.0.15 3

# Show current device status (uptime, heap, MQTT state, etc.)
python3 tools/test_node.py status

# Poll diagnostics logs from both devices once
python3 tools/test_node.py logs

# Continuously poll logs until Ctrl+C
python3 tools/test_node.py watch

# Stress test: send N calls in rapid succession
python3 tools/test_node.py stress "All Rooms" 20

# Race condition test: simultaneous calls
python3 tools/test_node.py race "Bedroom Intercom"
```

### `notify.py` — Mobile Notification

Sends a push notification via MQTT to a mobile device via Home Assistant:

```bash
python3 tools/notify.py "Build complete"
python3 tools/notify.py "Tests passed: 65/65"
```

Reads `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` from environment. Publishes to topic `claude/notify` with payload `{"title": "Claude Code", "message": "..."}`.

## Interpreting Results

### Expected pass rates

On a stable system with both devices online and Piper running:

- **S01–S10**: All PASS (except S08 returns SKIP if Piper not running)
- **S06, S07, S47–S51**: SKIP if `websockets` not installed; PASS otherwise
- **S47**: May show `IGMP flaky:` warning if one device misses a multicast group join — counted as PASS
- **S58**: FAIL until a web admin password is configured on the devices

### Common failure causes

| Failure | Likely cause |
|---------|-------------|
| `Hub unreachable` | Hub add-on stopped or restarted since last rebuild |
| `Hub stuck in 'transmitting'` | Web PTT client disconnected without `ptt_stop`; wait 5s or restart hub |
| `rx_delta=0` (multicast tests) | IGMP group membership not joined — occurs after device reboot or network change; usually resolves after 60s |
| `Device unreachable` | Device rebooted or lost WiFi; check serial logs |
| `MQTT disconnected` | MQTT cycling — check hub logs and device serial for errors |
| `Heap drift > 8192` | Memory leak — investigate with extended serial logging |
| Auth test FAIL (S58) | No web admin password set — configure via `http://<device-ip>/` |

### Sequence continuity warnings

`seq=LOSS(N/M, X%)` in test output means the hub saw fewer packets than expected from the sequence range. Up to ~2% loss is normal on WiFi. Higher loss indicates network congestion or dropped frames.

## QA Reports

Previous QA runs are stored in `tests/`:

| File | Description |
|------|-------------|
| `tests/QA_REPORT_v2.8.4.md` | QA run for firmware v2.8.4 / hub v2.5.2 |
| `tests/QA_REPORT_v2.8.5.md` | QA run for firmware v2.8.5 / hub v2.5.3 |
| `tests/qa_report.md` | Latest QA run results |
| `tests/audio_scenario_report.md` | Audio scenario test report |
