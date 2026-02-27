# Deep Code Review: HA Intercom System

**Date**: 2026-02-26
**Firmware**: v2.9.1 | **Hub**: v2.5.5
**Files reviewed**: 12 (main.c, ha_mqtt.c, network.c, audio_input.c, audio_output.c, codec.c, webserver.c, display.c, button.c, settings.c, protocol.h, intercom_hub.py)
**Commit**: 24817b7 (all pushed to origin/main)

---

## BUG-1: Hub chime streaming has no concurrency protection (HIGH)

**File**: `intercom_hub.py` — `_stream_chime_blocking()` (line ~1485)

The chime streaming function sends UDP packets via `tx_socket` but:
1. Does **not** acquire `tx_lock` — so if `encode_and_broadcast()` (TTS) runs simultaneously, both threads write interleaved packets through the same socket
2. Does **not** set `current_state = "transmitting"` — so `is_channel_busy()` returns `false` during chime playback, allowing a TTS or Web PTT to start concurrently

**Consequence**: If a TTS announcement triggers during a chime (or vice versa), the ESP32 sees interleaved packets from two logical streams sharing the same `DEVICE_ID` and `sequence_num` counter. This corrupts PLC/FEC sequence tracking, causing the ESP32 decoder to generate garbage concealment audio.

**Fix**: Either wrap `_stream_chime_blocking()` with `tx_lock.acquire()/release()`, or make chime streaming go through `send_audio_packet()` with a `state_lock` guard that sets `current_state = "transmitting"` for the duration.

---

## BUG-2: Hub `sequence_num` shared across concurrent senders (MEDIUM)

**File**: `intercom_hub.py` — `send_audio_packet()` (line ~818)

`sequence_num` is a global counter incremented in `send_audio_packet()`. It's called from:
- `encode_and_broadcast()` (in a thread holding `tx_lock`)
- `_stream_chime_blocking()` (in a thread **without** `tx_lock`)
- Web PTT handler (from the async event loop)

Python's GIL prevents data corruption on the increment, but when chime and TTS streams overlap (see BUG-1), the ESP32 receiver sees a single `DEVICE_ID` with sequence numbers like: `100, 101, 102, 5000, 5001, 103, 5002...` — the PLC/FEC logic interprets this as massive packet loss and generates concealment audio.

**Fix**: Give chimes their own sequence counter (e.g., `chime_seq` starting from 0 each time), or better yet, resolve BUG-1 so streams can't overlap.

---

## BUG-3: Auth bypass when no web password is set (LOW/DESIGN)

**File**: `firmware/main/webserver.c` — `check_basic_auth()` (line ~104)

```c
// If no password is set, allow access (first-time setup)
if (strlen(s->web_admin_password) == 0) {
    return true;
}
```

When `web_admin_password` is empty (default after first flash or `restore_defaults`), **all** endpoints are accessible without authentication, including `/api/test` (which can trigger `reboot`, `restore_defaults`), `/update` (OTA firmware upload), and `/reboot`.

This is the root cause of the S58 test failure. It's intentional for first-time setup, but the blast radius is large — anyone on the LAN can OTA-flash or reboot the device.

**Fix**: Consider requiring auth for destructive actions (`reboot`, `restore_defaults`, OTA) even in first-time setup mode. Or add a log warning at boot if no password is set.

---

## BUG-4: Beep/play_task I2S interleaving race (LOW)

**File**: `firmware/main/main.c` — `play_fallback_beep()` (line ~644)

`play_fallback_beep()` runs in the main loop task context. It flushes the RX queue, starts I2S, writes 10 beep frames, stops I2S. But the `audio_play_task` runs in a separate FreeRTOS task and dequeues packets every 50ms.

If a hub chime packet arrives DURING the 200ms beep window (after the queue flush), the play_task dequeues it, sees `audio_playing = false` (beep cleared it), calls `audio_output_start()` (already active — logged as "already active"), then calls `audio_output_write()`. Now both the main loop (beep frames) and play_task (chime frames) alternate writing to I2S via the `output_lock` mutex. The audio is garbled for the remaining beep duration.

**Window**: Hub chime arriving 150-350ms after MQTT call message — rare since hub chime usually arrives within 25ms.

**Fix**: Set a flag like `beep_in_progress` that `process_rx_packet()` checks before writing, or run the beep through the queue like hub chime packets.

---

## BUG-5: Hub stale mobile device MQTT entries (LOW)

**File**: `intercom_hub.py` — `on_mqtt_connect()` (line ~2173)

Mobile device cleanup uses index-based IDs:
```python
for i, device in enumerate(MOBILE_DEVICES):
    device_id = f"{UNIQUE_ID}_mobile_{i}"
```

If the mobile device count decreases between restarts (e.g., 3 devices → 2), the retained MQTT messages at index 2 are never cleared. Since mobile devices are no longer published as targets (they're cleaned up), this is cosmetic — stale retained entries sit on the broker but don't appear on OLED.

**Fix**: Track the maximum mobile device index ever used (persist in a file) and clear up to that index on connect.

---

## DESIGN CONCERN 1: ESP32 audio state variables — no mutex, relies on volatile + ordering

**Files**: `firmware/main/main.c` — multiple locations

The compound state `{audio_playing, has_current_sender, current_rx_priority, last_audio_rx_time}` is accessed from 3 tasks:
- **play_task**: reads/writes all four
- **main loop**: reads all four, writes `audio_playing`, `has_current_sender`, `current_rx_priority`
- **button handler**: writes `audio_playing`, `has_current_sender`

Currently safe because:
1. Xtensa single-word writes are atomic
2. `last_audio_rx_time` is written BEFORE `audio_playing` in `process_rx_packet()` (line 312, with comment explaining why)
3. Main loop reads with 100ms granularity, so one stale read is tolerable

But fragile — any future change that adds a second dependent write or reorders operations could introduce a race. A FreeRTOS mutex or critical section would make this bulletproof.

---

## DESIGN CONCERN 2: Hub globals accessed from threads without explicit synchronization

**File**: `intercom_hub.py`

Several globals are written from `receive_thread` (background thread) and read from the async event loop:
- `current_audio_sender` (line ~1036)
- `current_rx_priority` (line ~1033)
- `esp32_targets` (dict, read from async audio forwarding)

Python's GIL prevents data corruption, but the code relies on implicit GIL timing rather than explicit locks. For example, `broadcast_audio_to_web_clients()` reads `current_audio_sender` and then reads `esp32_targets` — both set from `receive_thread`. Between those two reads, the receive_thread could update both, leading to a mismatch (sender A's ID paired with sender B's target).

**Impact**: Rare, causes one frame to be forwarded to the wrong web client. Self-correcting on the next frame.

---

## DESIGN CONCERN 3: Hub receive_thread stale header check

**File**: `intercom_hub.py` — line ~997

```python
if len(data) < 12:  # Minimum: 8 byte ID + 4 byte seq
```

Protocol is 13 bytes since v2.5.0. The code handles 12-byte packets gracefully (falls back to `PRIORITY_NORMAL`), but the comment is stale. Should be `< PACKET_HEADER_SIZE` (13) since all firmware is >= v2.9.1.

---

## DESIGN CONCERN 4: ESP32 lead-in silence is pre-encoded once at TX start

**File**: `firmware/main/main.c` — `audio_tx_task()` (line ~474-487)

The lead-in sends 15 copies of the same pre-encoded silence frame. The Opus standard specifies that encoders maintain internal prediction state, and each call to `opus_encode()` produces different output even for identical input (because the prediction evolves). By reusing one encoded silence frame, the receiver's decoder gets 15 identical frames which the decoder can't meaningfully predict across — this is fine for silence but differs from how `encode_and_broadcast()` (hub) does it (encodes each frame fresh, line ~1680-1682).

**Impact**: None for silence (all zeros → no audible difference). But inconsistent approach between firmware and hub.

---

## OBSERVATIONS (not bugs, just notes)

1. **Well-designed half-duplex guard**: The `transmitting` check in `on_audio_received()` correctly prevents RX during TX without any lock overhead.

2. **Good TOCTOU protection in audio_output.c**: The double-check pattern (quick volatile read → mutex → re-check) is properly implemented.

3. **Solid priority preemption**: The first-to-talk + priority preemption logic in `process_rx_packet()` correctly handles all cases: same sender, higher priority preempt, lower priority discard.

4. **Good encoder mutex**: `codec.c` properly protects the shared Opus encoder between PTT TX and test_tone with a mutex + timeout pattern.

5. **Security headers present**: The webserver sets CSP, X-Content-Type-Options, X-Frame-Options, CSRF tokens on forms, HTML encoding of user inputs. Good security posture for an embedded system.

6. **Emergency override restore is thorough**: Both idle timeout (main loop) and priority preemption (process_rx_packet) restore the emergency volume override. No leak path.

---

## Summary

| ID | Severity | Component | Description |
|---|---|---|---|
| BUG-1 | HIGH | Hub | Chime streaming has no tx_lock or state transition |
| BUG-2 | MEDIUM | Hub | Shared sequence_num across concurrent streams |
| BUG-3 | LOW | Firmware | Auth bypass when web password empty (by design) |
| BUG-4 | LOW | Firmware | Beep/play_task I2S write interleaving race |
| BUG-5 | LOW | Hub | Stale mobile device MQTT retained entries |
| DC-1 | INFO | Firmware | Audio state vars rely on volatile+ordering, no mutex |
| DC-2 | INFO | Hub | Thread globals rely on GIL, no explicit locks |
| DC-3 | INFO | Hub | Stale minimum packet size comment |
| DC-4 | INFO | Firmware | Lead-in silence reuses one encoded frame |

**Overall assessment**: Codebase is well-structured with careful attention to thread safety on the ESP32 side. The hub's chime streaming lacking `tx_lock` (BUG-1 + BUG-2) is the most impactful finding. The auth bypass (BUG-3) is a known design choice. The low-severity races are narrow windows with minimal user impact.
