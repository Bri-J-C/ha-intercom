---
name: intercom-protocol
description: Intercom system protocol specification. Use when writing, reviewing, or debugging code that handles audio packets, MQTT messages, WebSocket communication, or any inter-component protocol. Contains packet format, codec config, transport details, and sync requirements.
version: 1.0.0
---

# Intercom Protocol Specification

## Audio Codec
- Codec: Opus, 32kbps VBR, complexity 5
- Sample rate: 16kHz mono
- Frame size: 20ms = 320 samples
- PLC (Packet Loss Concealment) and FEC (Forward Error Correction) enabled

## Transport
- **Broadcast**: UDP multicast 224.0.0.100:5005
- **Targeted**: UDP unicast to device IP
- **`IP_MULTICAST_LOOP=0`** must be set on TX socket in BOTH hub and firmware — prevents self-reception

## Packet Format (13-byte header)
```
[0-7]   device_id     8 bytes   sender identifier
[8-11]  sequence      4 bytes   packet sequence number (uint32, big-endian)
[12]    priority      1 byte    0=Normal, 1=High, 2=Emergency
[13+]   opus_data     variable  Opus-encoded audio frame
```

## Priority System
- 0 = Normal
- 1 = High
- 2 = Emergency (can preempt active transmission)
- Do Not Disturb mode blocks Normal and High; Emergency always gets through
- Trail-out silence frames use the active PTT priority

## Control Plane (MQTT)
- Broker: 10.0.0.8 (HA default)
- HA auto-discovery for device tracking
- LWT (Last Will and Testament) for online/offline tracking
- Room targeting and call notifications via MQTT

## WebSocket (Hub ↔ Web Clients)
- Binary messages: raw PCM audio (16-bit, 16kHz mono)
- JSON messages: control (state, target, volume, mute, DND)
- Each web client has a unique `client_id` (device name or generated)
- `publish_state(notify_web=False)` prevents double-notifications
- `notify_targeted_web_client_state(target_device, state)` for per-client updates
- "All Rooms" detection: `target.lower() in ('all', 'all rooms')`

## Collision Avoidance
- First-to-talk wins
- 500ms timeout to release channel
- `is_channel_busy()` checks: `web_ptt_active`, `current_state == "transmitting"/"receiving"`

## Version Sync Requirement
**Hub and firmware must always be in sync on protocol constants.**
- Hub version tracked in `VERSION` in `intercom_hub.py`
- Firmware version tracked in `FIRMWARE_VERSION` in `firmware/main/protocol.h`
- Any protocol change (packet format, port, codec params) requires updating BOTH

## Audio Buffer Architecture (Firmware)
- TX and RX PCM buffers are separate — no shared buffer between tasks
- RX audio queue: 15-deep FreeRTOS queue; `audio_play_task` on PSRAM stack
- DMA pre-fill: 2 descriptors on start (~40ms latency)
- I2S write timeout: 20ms (one frame) — prevents RX stalls
- Idle state broadcast sent before 750ms sleep so web clients see idle immediately
- `nextPlayTime` in web PTT client must reset on AudioContext `suspend()`/`resume()`
