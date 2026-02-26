---
name: debug-audio
description: Audio subsystem diagnostic procedures for the intercom system. Use when investigating audio quality issues, dropouts, glitches, latency problems, echo, or any audio-related bug in firmware or hub.
version: 1.0.0
---

# Audio Debugging Guide

## Symptom → Likely Cause Map

| Symptom | Most Likely Cause |
|---|---|
| Audio dropouts / gaps | RX queue overflow, I2S underrun, network packet loss |
| High start latency | DMA pre-fill too large (should be 2 descriptors) |
| Echo or self-hearing | `IP_MULTICAST_LOOP` not set to 0 on TX socket |
| Garbled audio | Opus decode error, buffer size mismatch, sample rate mismatch |
| Audio cuts out after N seconds | I2S write timeout blocking play task, sequence tracking stale |
| Web client audio stale/frozen | `nextPlayTime` not reset after AudioContext `suspend()`/`resume()` |
| Non-deterministic glitches | Race condition — shared buffer between TX and RX tasks (should be separate) |
| Audio works then stops after reconnect | MQTT/WebSocket state not cleaned up, `current_state` stuck |
| Volume inconsistent | AGC fighting with manual volume setting |

## Diagnostic Checklist

### Step 1 — Isolate the path
Determine which path is affected:
- ESP32 → ESP32 (UDP multicast)
- ESP32 → Web client (hub forwarding)
- Web client → ESP32 (hub forwarding)
- Web client → Web client (hub forwarding)
- TTS → all (hub)

### Step 2 — Check packet delivery
Add sequence number logging to identify drops vs. ordering issues:
- Gaps in sequence = packets dropped in transit
- Out-of-order sequence = reordering (unusual on LAN)
- Duplicate sequence = multicast loopback issue (`IP_MULTICAST_LOOP`)

### Step 3 — Check queue depth
In firmware, log `uxQueueMessagesWaiting(audio_rx_queue)` before each enqueue.
- Queue depth consistently at 15 = queue overflow dropping packets
- Queue depth 0 = play task keeping up fine

### Step 4 — Check I2S state
- `I2S_EVENT_DMA_ERROR` in logs = DMA underrun
- DMA descriptor count: should be 8 total, 2 pre-filled on start
- I2S write timeout: should be 20ms — if increased, it may mask play task stalls

### Step 5 — Check Opus
- Decode errors logged? Check `opus_decode()` return value
- PLC being triggered frequently? = high packet loss rate
- Encoder/decoder state in PSRAM? Check `esp_ptr_external_ram()` on init

## Hub-Side Audio Debugging

```python
# Add to encode_and_broadcast() to count forwarded packets
logger.debug(f"Forwarding audio: {len(pcm_data)} bytes to {len(web_clients)} web clients")

# Add to WebSocket handler to track client state
logger.debug(f"Client {client_id} state: {client_state}, notify_web={notify_web}")
```

## Known Fragile Areas
- **`nextPlayTime` in ptt-v7.js**: Must reset when AudioContext is recreated. If web audio sounds frozen/stale, this is the first thing to check.
- **Sequence tracking**: Must reset on beep fallback. Stale sequence state causes the firmware to discard valid packets.
- **`is_channel_busy()` race**: If `web_ptt_active` and `current_state` get out of sync, the channel appears permanently busy.
- **Multicast on reconnect**: After WiFi reconnect, multicast group membership may need to be rejoined. Check `create_rx_socket()` is called after reconnect.
